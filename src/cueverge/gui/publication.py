import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import JsonValue

from cueverge.gui.artifact_deployment import ArtifactDeploymentService
from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.render_jobs import RenderJobService
from cueverge.gui.repository import GuiRepository
from cueverge.protocol.device_v2 import ArtifactCacheCapability, MediaPlaybackCapability

ApprovalState = Literal["draft", "in_review", "approved", "rejected"]
PublicationState = Literal["published", "staged", "active", "inactive", "failed"]


class PublicationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicationDiagnostic:
    code: str
    severity: Literal["error", "warning", "info"]
    message: str


@dataclass(frozen=True)
class RevisionRelease:
    project_id: str
    revision: int
    name: str | None
    summary: str | None
    approval_state: ApprovalState
    approved_by: str | None
    approved_at: str | None


@dataclass(frozen=True)
class PublishedPackage:
    id: str
    project_id: str
    revision: int
    digest_sha256: str
    state: PublicationState
    manifest: dict[str, JsonValue]
    created_by: str
    created_at: str
    staged_at: str | None
    activated_at: str | None
    deactivated_at: str | None


class PublicationService:
    def __init__(
        self,
        *,
        repository: GuiRepository,
        compiler: DraftCompiler,
        assets: AssetUploadService,
        render_jobs: RenderJobService,
        deployment: ArtifactDeploymentService,
        output_root: Path,
    ) -> None:
        self.repository = repository
        self.compiler = compiler
        self.assets = assets
        self.render_jobs = render_jobs
        self.deployment = deployment
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._migrate()

    def release(self, project_id: str, revision: int) -> RevisionRelease:
        self.repository.get_revision(project_id, revision)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT name, summary, approval_state, approved_by, approved_at
                FROM revision_release_metadata
                WHERE project_id = ? AND revision = ?
                """,
                (project_id, revision),
            ).fetchone()
        if row is None:
            return RevisionRelease(project_id, revision, None, None, "draft", None, None)
        return RevisionRelease(
            project_id=project_id,
            revision=revision,
            name=str(row["name"]) if row["name"] is not None else None,
            summary=str(row["summary"]) if row["summary"] is not None else None,
            approval_state=cast(ApprovalState, str(row["approval_state"])),
            approved_by=str(row["approved_by"]) if row["approved_by"] is not None else None,
            approved_at=str(row["approved_at"]) if row["approved_at"] is not None else None,
        )

    def update_release(
        self,
        *,
        project_id: str,
        revision: int,
        name: str | None,
        summary: str | None,
        approval_state: ApprovalState,
        actor: str,
        occurred_at: str,
    ) -> RevisionRelease:
        self.repository.get_revision(project_id, revision)
        approved_by = actor if approval_state == "approved" else None
        approved_at = occurred_at if approval_state == "approved" else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO revision_release_metadata(
                    project_id, revision, name, summary, approval_state,
                    approved_by, approved_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, revision) DO UPDATE SET
                    name = excluded.name,
                    summary = excluded.summary,
                    approval_state = excluded.approval_state,
                    approved_by = excluded.approved_by,
                    approved_at = excluded.approved_at,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    revision,
                    name.strip() if name and name.strip() else None,
                    summary.strip() if summary and summary.strip() else None,
                    approval_state,
                    approved_by,
                    approved_at,
                    occurred_at,
                ),
            )
        return self.release(project_id, revision)

    def diff(self, project_id: str, base_revision: int, candidate_revision: int) -> tuple[str, ...]:
        base = self.repository.get_revision(project_id, base_revision).draft.model_dump(mode="json")
        candidate = self.repository.get_revision(project_id, candidate_revision).draft.model_dump(
            mode="json"
        )
        return tuple(_changed_paths(base, candidate))

    def validate(self, project_id: str, revision: int) -> tuple[PublicationDiagnostic, ...]:
        stored = self.repository.get_revision(project_id, revision)
        draft = stored.draft
        diagnostics: list[PublicationDiagnostic] = []

        def add(
            code: str,
            message: str,
            severity: Literal["error", "warning", "info"] = "error",
        ) -> None:
            diagnostics.append(PublicationDiagnostic(code, severity, message))

        installation_resources = {binding.resource for binding in draft.installation.resources}
        for scene in draft.scenes:
            missing = set(scene.resources).difference(installation_resources)
            if missing:
                add(
                    "unknown_scene_resource",
                    f"Scene {scene.id} references unavailable resources: {sorted(missing)}.",
                )

        referenced_assets = {
            reference
            for scene in draft.scenes
            for reference in _scene_asset_ids(scene.model_dump(mode="json"))
        }
        assets = {asset.id: asset for asset in self.assets.list_assets(project_id)}
        for asset_id in sorted(referenced_assets):
            asset = assets.get(asset_id)
            if asset is None:
                add(
                    "asset_missing",
                    f"Referenced asset {asset_id} is not in the project. "
                    "Relink the reference or import the asset before publishing.",
                )
            elif asset.status == "quarantined":
                add(
                    "asset_quarantined",
                    f"Asset {asset.name} was quarantined by malware scanning. "
                    "Replace it with a clean upload before publishing.",
                )
            elif asset.status == "failed":
                detail = f" Processing failed: {asset.error}" if asset.error else ""
                add(
                    "asset_processing_failed",
                    f"Asset {asset.name} could not be processed.{detail} "
                    "Retry processing or replace the asset before publishing.",
                )
            elif asset.status == "uploading":
                add(
                    "asset_processing_incomplete",
                    f"Asset {asset.name} is still uploading or processing. "
                    "Wait for processing to finish before publishing.",
                )
            elif asset.original_file_status == "missing":
                add(
                    "asset_original_missing",
                    f"Asset {asset.name} no longer has its original file. "
                    "Relink or restore the original before publishing.",
                )
            elif asset.original_file_status == "size_mismatch":
                add(
                    "asset_original_size_mismatch",
                    f"Asset {asset.name} does not match its recorded original size. "
                    "Restore the verified original or replace the asset before publishing.",
                )
            elif asset.original_file_status == "pending":
                add(
                    "asset_original_unverified",
                    f"Asset {asset.name} has not completed original-file verification. "
                    "Wait for verification before publishing.",
                )
            elif asset.digest_sha256 is None:
                add(
                    "asset_checksum_missing",
                    f"Asset {asset.name} has no verified SHA-256 checksum. "
                    "Reprocess or replace the asset before publishing.",
                )

        compositions = _object_map(draft.editor_metadata.get("compositions"))
        jobs = self.render_jobs.list(project_id)
        artifact_ids: set[str] = set()
        for composition_id in sorted(compositions):
            job = next(
                (
                    item
                    for item in jobs
                    if item.composition_id == composition_id
                    and item.source_revision == revision
                    and item.state == "completed"
                    and item.artifact_id is not None
                ),
                None,
            )
            if job is None:
                add(
                    "current_bake_missing",
                    f"Composition {composition_id} has no successful bake for revision {revision}.",
                )
            else:
                artifact_ids.add(cast(str, job.artifact_id))

        assignments = {
            assignment.resource: assignment
            for assignment in self.repository.list_resource_assignments()
        }
        devices = {device.id: device for device in self.repository.list_devices()}
        for binding in draft.installation.resources:
            if binding.kind.value != "media_agent":
                continue
            assignment = assignments.get(binding.resource)
            if assignment is None:
                add(
                    "resource_unassigned",
                    f"Media resource {binding.resource} has no enrolled client assignment.",
                )
                continue
            device = devices.get(assignment.device_id)
            if device is None:
                add(
                    "assigned_device_missing",
                    f"Assigned device {assignment.device_id} is missing.",
                )
                continue
            media = next(
                (
                    capability
                    for capability in device.capabilities.capabilities
                    if isinstance(capability, MediaPlaybackCapability)
                ),
                None,
            )
            cache = next(
                (
                    capability
                    for capability in device.capabilities.capabilities
                    if isinstance(capability, ArtifactCacheCapability)
                ),
                None,
            )
            if media is None or cache is None:
                add(
                    "client_capability_missing",
                    (
                        f"Client {device.display_name} requires media playback "
                        "and artifact cache capabilities."
                    ),
                )
            elif artifact_ids and ("h264" not in media.codecs or "mp4" not in media.containers):
                add(
                    "decoder_profile_incompatible",
                    f"Client {device.display_name} cannot decode the baked H.264/MP4 profile.",
                )
            if cache is not None and (
                not cache.supports_pinning or not cache.supports_resumable_transfer
            ):
                add(
                    "cache_safety_unsupported",
                    f"Client {device.display_name} lacks pinning or resumable transfer support.",
                )

        statuses = {
            status.device_id: status for status in self.repository.list_device_artifact_statuses()
        }
        required_by_device: dict[str, dict[str, int]] = {}
        for artifact_id in artifact_ids:
            manifest = self.repository.get_render_artifact_manifest(artifact_id)
            members = manifest.get("members")
            if not isinstance(members, list):
                add("render_manifest_invalid", f"Artifact {artifact_id} has invalid members.")
                continue
            for member in members:
                if not isinstance(member, dict):
                    continue
                resource = member.get("resource")
                digest = member.get("digest_sha256")
                size_bytes = member.get("size_bytes")
                assignment = assignments.get(resource) if isinstance(resource, str) else None
                if (
                    assignment is not None
                    and isinstance(digest, str)
                    and isinstance(size_bytes, int)
                ):
                    required_by_device.setdefault(assignment.device_id, {})[digest] = size_bytes
        for assignment in assignments.values():
            if assignment.resource not in installation_resources:
                continue
            status = statuses.get(assignment.device_id)
            if status is None:
                add(
                    "inventory_unavailable",
                    f"Client inventory is unavailable for {assignment.device_id}.",
                    "warning",
                )
            elif status.faults:
                add(
                    "inventory_fault",
                    f"Client {assignment.device_id} reports: {', '.join(status.faults)}.",
                )
        for device_id, required in required_by_device.items():
            status = statuses.get(device_id)
            if status is None:
                continue
            inventory = {
                artifact.digest_sha256: artifact
                for artifact in self.repository.list_device_artifacts(device_id)
            }
            missing_bytes = sum(
                size
                for digest, size in required.items()
                if digest not in inventory or inventory[digest].state != "verified"
            )
            evictable_bytes = sum(
                artifact.size_bytes
                for artifact in inventory.values()
                if not artifact.pinned and not artifact.in_use
            )
            if missing_bytes > status.free_bytes + evictable_bytes:
                add(
                    "insufficient_safe_cache_space",
                    (
                        f"Client {device_id} needs {missing_bytes} bytes but only "
                        f"{status.free_bytes + evictable_bytes} safe bytes are available."
                    ),
                )
            else:
                add(
                    "transfer_estimate",
                    (
                        f"Client {device_id} will transfer at most {missing_bytes} bytes "
                        "after checksum deduplication."
                    ),
                    "info",
                )

        add(
            "validation_complete",
            "Structural, semantic, asset, capability, bake, and cache checks completed.",
            "info",
        )
        return tuple(diagnostics)

    def publish(
        self,
        *,
        project_id: str,
        revision: int,
        actor: str,
        occurred_at: str,
    ) -> PublishedPackage:
        release = self.release(project_id, revision)
        if release.approval_state != "approved":
            raise PublicationError("revision must be approved before publication")
        diagnostics = self.validate(project_id, revision)
        errors = [item for item in diagnostics if item.severity == "error"]
        if errors:
            raise PublicationError(
                "publication validation failed: " + "; ".join(item.message for item in errors)
            )
        stored = self.repository.get_revision(project_id, revision)
        compiled = self.compiler.compile(
            project_id=project_id,
            revision=revision,
            draft=stored.draft,
        )
        jobs = self.render_jobs.list(project_id)
        artifact_ids = tuple(
            sorted(
                {
                    job.artifact_id
                    for job in jobs
                    if job.source_revision == revision
                    and job.state == "completed"
                    and job.artifact_id is not None
                }
            )
        )
        asset_manifest = [
            {
                "id": asset.id,
                "digest_sha256": asset.digest_sha256,
                "size_bytes": asset.size_bytes,
            }
            for asset in self.assets.list_assets(project_id)
            if not asset.unused and asset.digest_sha256 is not None and asset.size_bytes is not None
        ]
        file_manifest = [
            {
                "path": relative,
                "size_bytes": (compiled.package_root / relative).stat().st_size,
                "digest_sha256": _sha256_file(compiled.package_root / relative),
                "schema_version": _json_schema_version(compiled.package_root / relative),
            }
            for relative in compiled.files
        ]
        package_id = str(uuid4())
        manifest: dict[str, JsonValue] = {
            "schema_version": 1,
            "package_id": package_id,
            "project_id": project_id,
            "source_revision": revision,
            "revision_name": release.name,
            "change_summary": release.summary,
            "creator": actor,
            "created_at": occurred_at,
            "compiled_digest_sha256": compiled.digest,
            "files": cast(list[JsonValue], file_manifest),
            "assets": cast(list[JsonValue], asset_manifest),
            "render_artifact_ids": list(artifact_ids),
            "required_capabilities": ["artifact_cache", "media_playback"],
            "validation": [cast(JsonValue, item.__dict__) for item in diagnostics],
        }
        digest = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        destination = self.output_root / project_id / digest
        temporary = self.output_root / project_id / f".{package_id}.tmp"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        if temporary.exists():
            shutil.rmtree(temporary)
        shutil.copytree(compiled.package_root, temporary)
        (temporary / "publication-manifest.json").write_text(
            json.dumps({**manifest, "package_digest_sha256": digest}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        if destination.exists():
            shutil.rmtree(temporary)
        else:
            os.replace(temporary, destination)
        manifest["package_digest_sha256"] = digest
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO published_packages(
                    id, project_id, revision, digest_sha256, state,
                    manifest_json, created_by, created_at
                ) VALUES (?, ?, ?, ?, 'published', ?, ?, ?)
                """,
                (
                    package_id,
                    project_id,
                    revision,
                    digest,
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                    actor,
                    occurred_at,
                ),
            )
        return self.get(package_id)

    def list(self, project_id: str) -> tuple[PublishedPackage, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM published_packages
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return tuple(_published(row) for row in rows)

    def get(self, package_id: str) -> PublishedPackage:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM published_packages WHERE id = ?",
                (package_id,),
            ).fetchone()
        if row is None:
            raise PublicationError("published package does not exist")
        return _published(row)

    async def stage(self, package_id: str, occurred_at: str) -> PublishedPackage:
        package = self.get(package_id)
        retained = [
            item
            for item in self.list(package.project_id)
            if item.id == package_id or item.state in {"active", "inactive"}
        ]
        protected_digests = tuple(
            sorted(
                {
                    digest
                    for retained_package in retained
                    for digest in self._package_artifact_digests(retained_package)
                }
            )
        )
        for artifact_id in _manifest_artifact_ids(package.manifest):
            await self.deployment.stage(
                artifact_id,
                protected_digests=protected_digests,
            )
        self._set_state(package_id, "staged", "staged_at", occurred_at)
        return self.get(package_id)

    async def activate(
        self,
        package_id: str,
        *,
        occurred_at: str,
        simulation_mode: bool,
    ) -> PublishedPackage:
        package = self.get(package_id)
        if simulation_mode:
            raise PublicationError("live activation is disabled in simulation mode")
        if package.state != "staged":
            raise PublicationError("package must be staged before activation")
        for artifact_id in _manifest_artifact_ids(package.manifest):
            await self.deployment.activate(artifact_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE published_packages
                SET state = 'inactive', deactivated_at = ?
                WHERE project_id = ? AND state = 'active' AND id != ?
                """,
                (occurred_at, package.project_id, package_id),
            )
            connection.execute(
                """
                UPDATE published_packages
                SET state = 'active', activated_at = ?, deactivated_at = NULL
                WHERE id = ?
                """,
                (occurred_at, package_id),
            )
            connection.commit()
        return self.get(package_id)

    def deactivate(self, package_id: str, occurred_at: str) -> PublishedPackage:
        package = self.get(package_id)
        if package.state != "active":
            raise PublicationError("only the active package can be deactivated")
        self._set_state(package_id, "inactive", "deactivated_at", occurred_at)
        return self.get(package_id)

    async def rollback(
        self,
        package_id: str,
        *,
        occurred_at: str,
        simulation_mode: bool,
    ) -> PublishedPackage:
        package = self.get(package_id)
        if package.state not in {"inactive", "staged"}:
            raise PublicationError("rollback target must be a retained inactive or staged package")
        if package.state == "inactive":
            await self.stage(package_id, occurred_at)
        return await self.activate(
            package_id,
            occurred_at=occurred_at,
            simulation_mode=simulation_mode,
        )

    def _package_artifact_digests(
        self,
        package: PublishedPackage,
    ) -> tuple[str, ...]:
        digests: list[str] = []
        for artifact_id in _manifest_artifact_ids(package.manifest):
            manifest = self.repository.get_render_artifact_manifest(artifact_id)
            members = manifest.get("members")
            if not isinstance(members, list):
                raise PublicationError("render artifact members are invalid")
            for member in members:
                if not isinstance(member, dict):
                    raise PublicationError("render artifact member is invalid")
                digest = member.get("digest_sha256")
                if not isinstance(digest, str):
                    raise PublicationError("render artifact digest is invalid")
                digests.append(digest)
        return tuple(digests)

    def _set_state(
        self,
        package_id: str,
        state: PublicationState,
        timestamp_column: str,
        occurred_at: str,
    ) -> None:
        if timestamp_column not in {"staged_at", "activated_at", "deactivated_at"}:
            raise ValueError("invalid publication timestamp column")
        with self._connect() as connection:
            connection.execute(
                f"UPDATE published_packages SET state = ?, {timestamp_column} = ? WHERE id = ?",
                (state, occurred_at, package_id),
            )

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS revision_release_metadata (
                    project_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    name TEXT,
                    summary TEXT,
                    approval_state TEXT NOT NULL
                        CHECK (approval_state IN ('draft', 'in_review', 'approved', 'rejected')),
                    approved_by TEXT,
                    approved_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, revision),
                    FOREIGN KEY (project_id, revision)
                        REFERENCES revisions(project_id, revision) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS published_packages (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
                    revision INTEGER NOT NULL,
                    digest_sha256 TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL
                        CHECK (state IN ('published', 'staged', 'active', 'inactive', 'failed')),
                    manifest_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    staged_at TEXT,
                    activated_at TEXT,
                    deactivated_at TEXT,
                    FOREIGN KEY (project_id, revision)
                        REFERENCES revisions(project_id, revision) ON DELETE RESTRICT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_package_per_project
                ON published_packages(project_id) WHERE state = 'active';
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.repository.database_path,
            isolation_level=None,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def _published(row: sqlite3.Row) -> PublishedPackage:
    manifest = json.loads(str(row["manifest_json"]))
    if not isinstance(manifest, dict):
        raise PublicationError("stored publication manifest is invalid")
    return PublishedPackage(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        revision=int(row["revision"]),
        digest_sha256=str(row["digest_sha256"]),
        state=cast(PublicationState, str(row["state"])),
        manifest=cast(dict[str, JsonValue], manifest),
        created_by=str(row["created_by"]),
        created_at=str(row["created_at"]),
        staged_at=str(row["staged_at"]) if row["staged_at"] is not None else None,
        activated_at=str(row["activated_at"]) if row["activated_at"] is not None else None,
        deactivated_at=(str(row["deactivated_at"]) if row["deactivated_at"] is not None else None),
    )


def _changed_paths(left: object, right: object, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "/"]
    if isinstance(left, dict) and isinstance(right, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}/{key}"
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_changed_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list) and isinstance(right, list):
        paths = []
        for index in range(max(len(left), len(right))):
            child = f"{path}/{index}"
            if index >= len(left) or index >= len(right):
                paths.append(child)
            else:
                paths.extend(_changed_paths(left[index], right[index], child))
        return paths
    return [] if left == right else [path or "/"]


def _scene_asset_ids(scene: dict[str, object]) -> tuple[str, ...]:
    media = scene.get("media")
    values = media if isinstance(media, list) else [media]
    return tuple(
        value["asset"]
        for value in values
        if isinstance(value, dict) and isinstance(value.get("asset"), str)
    )


def _object_map(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _json_schema_version(path: Path) -> int | None:
    if path.suffix != ".json":
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        schema_version = value.get("schema_version")
        return schema_version if isinstance(schema_version, int) else None
    return None


def _manifest_artifact_ids(manifest: dict[str, JsonValue]) -> tuple[str, ...]:
    value = manifest.get("render_artifact_ids")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PublicationError("publication render artifact manifest is invalid")
    return tuple(item for item in value if isinstance(item, str))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
