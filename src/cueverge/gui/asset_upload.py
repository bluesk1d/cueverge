import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import Literal, cast
from uuid import uuid4

from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.repository import GuiRepository

ALLOWED_MEDIA_TYPES = {
    "audio/flac",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "image/jpeg",
    "image/gif",
    "image/png",
    "image/webp",
    "video/mp4",
    "video/quicktime",
    "video/webm",
}
# Browsers and Python's mimetypes disagree on names for the same formats (Python maps .wav to
# audio/x-wav, Chrome declares audio/wav), so both sides are compared in canonical form.
MEDIA_TYPE_ALIASES = {
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/x-pn-wav": "audio/wav",
    "audio/x-flac": "audio/flac",
    "audio/mp3": "audio/mpeg",
    "audio/x-mp3": "audio/mpeg",
    "audio/mpeg3": "audio/mpeg",
    "audio/x-mpeg": "audio/mpeg",
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
}
UNDECLARED_MEDIA_TYPES = {"", "application/octet-stream"}
REVIEW_ARTIFACT_TYPES = {
    "audio": ("audio/mpeg", ".mp3"),
    "image": ("image/jpeg", ".jpg"),
    "video": ("video/mp4", ".mp4"),
}
THUMBNAIL_ARTIFACT_TYPE = ("image/jpeg", ".jpg")
WAVEFORM_ARTIFACT_TYPE = ("image/png", ".png")
DERIVATIVE_PROFILE = "review-v1"
MEDIA_TOOL_MODULE = "cueverge.gui.media_tools"
ASSET_TRASH_RETENTION = timedelta(days=30)
OBJECT_CLEANUP_GRACE_SECONDS = 600.0
TRASH_SWEEP_ACTOR = "system:asset_trash"
_ASSET_COLUMNS = """
    a.id, a.project_id, a.name, a.status, a.current_revision,
    a.metadata_revision, a.owner, a.license, a.provenance,
    a.review_status, a.expires_on, a.folder, a.tags_json,
    a.favorite, a.trashed_at, a.trashed_by, a.purge_after,
    a.error, a.created_at, a.updated_at,
    a.pending_revision, a.replacement_error,
    r.digest_sha256, r.size_bytes, r.media_type,
    r.original_filename, r.scan_status, r.metadata_json
"""
_EDITOR_USAGE_LABELS = {
    "timeline_transform_layers": "timeline media layers",
    "lighting_author_layers": "lighting layers",
    "compositions": "compositions",
    "composition_instances": "composition instances",
    "scene_presets": "scene presets",
}


class AssetUploadError(RuntimeError):
    pass


class AssetNotFoundError(AssetUploadError):
    pass


class AssetReplacementStateError(AssetUploadError):
    """The asset cannot take a replacement or revert in its current state."""


class AssetUploadConflictError(AssetUploadError):
    def __init__(self, received_bytes: int) -> None:
        super().__init__("upload offset conflict")
        self.received_bytes = received_bytes


class AssetMetadataConflictError(AssetUploadError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("asset metadata revision conflict")
        self.current_revision = current_revision


class AssetMetadataLockedError(AssetUploadError):
    pass


class AssetTrashStateError(AssetUploadError):
    pass


class AssetPurgeConfirmationError(AssetUploadError):
    pass


class AssetRemovalBlockedError(AssetUploadError):
    def __init__(self, blockers: tuple[str, ...]) -> None:
        super().__init__("asset is still in use: " + " ".join(blockers))
        self.blockers = blockers


@dataclass(frozen=True)
class AssetSceneReference:
    scene_id: str
    scene_name: str
    output_id: str | None


@dataclass(frozen=True)
class AssetRevisionSummary:
    revision: int
    digest_sha256: str
    size_bytes: int
    media_type: str
    original_filename: str
    status: Literal["ready", "processing", "failed", "quarantined"]
    created_at: str


@dataclass(frozen=True)
class StoredAsset:
    id: str
    project_id: str
    name: str
    status: str
    current_revision: int | None
    metadata_revision: int
    owner: str | None
    license: str | None
    provenance: str | None
    review_status: Literal["unreviewed", "approved", "rejected"]
    expires_on: str | None
    folder: str | None
    tags: tuple[str, ...]
    favorite: bool
    error: str | None
    digest_sha256: str | None
    size_bytes: int | None
    media_type: str | None
    original_filename: str | None
    scan_status: str | None
    metadata: dict[str, object] | None
    created_at: str
    updated_at: str
    original_file_status: Literal["pending", "available", "missing", "size_mismatch"] = "pending"
    artifact_faults: tuple[str, ...] = ()
    references: tuple[AssetSceneReference, ...] = ()
    unused: bool = True
    trashed_at: str | None = None
    trashed_by: str | None = None
    purge_after: str | None = None
    draft_usage: tuple[str, ...] = ()
    removal_blockers: tuple[str, ...] = ()
    purge_blockers: tuple[str, ...] = ()
    pending_revision: int | None = None
    replacement_error: str | None = None
    revisions: tuple[AssetRevisionSummary, ...] = ()

    @property
    def replacement_status(self) -> Literal["uploading", "processing"] | None:
        if self.pending_revision is None:
            return None
        if any(item.revision == self.pending_revision for item in self.revisions):
            return "processing"
        return "uploading"


@dataclass(frozen=True)
class AssetPurgeResult:
    asset_id: str
    name: str
    released_objects: int
    released_bytes: int


@dataclass(frozen=True)
class AssetTrashSweepResult:
    purged_asset_ids: tuple[str, ...]
    retained: dict[str, tuple[str, ...]] = field(default_factory=dict)
    released_objects: int = 0
    released_bytes: int = 0


@dataclass(frozen=True)
class _AssetUsage:
    """Live references that keep an asset out of the trash or block permanent deletion."""

    drafts: dict[str, tuple[str, str]]
    projector_content: dict[str, tuple[str, ...]]
    calibration_assets: frozenset[str]
    packages: tuple[tuple[str, int, str, str], ...]
    _parsed: dict[str, object] = field(default_factory=dict)

    def draft_usage(self, project_id: str, asset_id: str) -> tuple[str, ...]:
        entry = self.drafts.get(project_id)
        if entry is None or f'"{asset_id}"' not in entry[1]:
            return ()
        if project_id not in self._parsed:
            self._parsed[project_id] = json.loads(entry[1])
        return _draft_usage(self._parsed[project_id], asset_id)

    def removal_blockers(self, asset_id: str, project_id: str) -> tuple[str, ...]:
        blockers: list[str] = []
        own_usage = self.draft_usage(project_id, asset_id)
        if own_usage:
            blockers.append(
                f"Used in this show's current draft: {', '.join(own_usage)}. Remove it there first."
            )
        for other_id, (name, _) in sorted(self.drafts.items(), key=lambda item: item[1][0]):
            if other_id == project_id:
                continue
            usage = self.draft_usage(other_id, asset_id)
            if usage:
                blockers.append(f'Used in the current draft of show "{name}": {", ".join(usage)}.')
        for surface in self.projector_content.get(asset_id, ()):
            blockers.append(f'Used as projector review content for surface "{surface}".')
        if asset_id in self.calibration_assets:
            blockers.append("Used as the test asset of an accepted timing calibration.")
        for package_project, revision, state, manifest in self.packages:
            if state in {"staged", "active"} and f'"{asset_id}"' in manifest:
                blockers.append(
                    f"Part of the {state} release of show "
                    f'"{self._project_name(package_project)}" (revision {revision}).'
                )
        return tuple(blockers)

    def purge_blockers(self, asset_id: str, project_id: str) -> tuple[str, ...]:
        blockers = list(self.removal_blockers(asset_id, project_id))
        for package_project, revision, state, manifest in self.packages:
            if state not in {"staged", "active"} and f'"{asset_id}"' in manifest:
                blockers.append(
                    "Kept for rollback: published release of show "
                    f'"{self._project_name(package_project)}" (revision {revision}).'
                )
        return tuple(dict.fromkeys(blockers))

    def _project_name(self, project_id: str) -> str:
        entry = self.drafts.get(project_id)
        return entry[0] if entry is not None else project_id


@dataclass(frozen=True)
class StoredAssetUpload:
    id: str
    asset_id: str
    total_bytes: int
    received_bytes: int
    state: str
    error: str | None


class AssetUploadService:
    def __init__(self, config: GuiConfig, repository: GuiRepository) -> None:
        self.config = config
        self.repository = repository
        self.root = config.data_root / "assets"
        self.upload_root = self.root / "uploads"
        self.object_root = self.root / "objects"
        self.artifact_root = self.root / "derived"
        self._lock = threading.Lock()
        self._processing_lock = threading.Lock()
        self.object_cleanup_grace_seconds = OBJECT_CLEANUP_GRACE_SECONDS
        for path in (self.root, self.upload_root, self.object_root, self.artifact_root):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def begin(
        self,
        *,
        project_id: str,
        name: str,
        original_filename: str,
        media_type: str,
        total_bytes: int,
        expected_sha256: str | None,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> tuple[StoredAsset, StoredAssetUpload]:
        media_type = canonical_media_type(media_type, original_filename)
        self._validate_upload(original_filename, media_type, total_bytes, expected_sha256)
        asset_id = str(uuid4())
        upload_id = str(uuid4())
        temp_path = self._reserve_temp(upload_id)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                project = connection.execute(
                    "SELECT archived_at FROM projects WHERE id = ?",
                    (project_id,),
                ).fetchone()
                if project is None:
                    raise AssetUploadError("project does not exist")
                if project["archived_at"] is not None:
                    raise AssetUploadError("archived projects cannot accept assets")
                self._check_quota(connection, total_bytes)
                connection.execute(
                    """
                    INSERT INTO assets(
                        id, project_id, name, status, current_revision,
                        error, created_at, updated_at
                    ) VALUES (?, ?, ?, 'uploading', NULL, NULL, ?, ?)
                    """,
                    (asset_id, project_id, name, occurred_at, occurred_at),
                )
                _insert_upload(
                    connection,
                    upload_id=upload_id,
                    asset_id=asset_id,
                    original_filename=original_filename,
                    media_type=media_type,
                    total_bytes=total_bytes,
                    expected_sha256=expected_sha256,
                    temp_name=temp_path.name,
                    occurred_at=occurred_at,
                )
            connection.commit()
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        self.repository.append_audit(
            actor=actor,
            action="asset.upload.begin",
            target=asset_id,
            result="success",
            occurred_at=occurred_at,
            authorization_context={
                "role": role.value,
                "project_id": project_id,
                "total_bytes": total_bytes,
                "media_type": media_type,
            },
        )
        return self.get_asset(asset_id), self.get_upload(upload_id)

    def begin_replacement(
        self,
        *,
        project_id: str,
        asset_id: str,
        expected_revision: int,
        original_filename: str,
        media_type: str,
        total_bytes: int,
        expected_sha256: str | None,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> tuple[StoredAsset, StoredAssetUpload]:
        """Start uploading a new file revision for an existing asset.

        The asset keeps its ID, so every scene placement, timeline layer and
        composition that references it picks up the new file once processing
        succeeds. Until then the current revision stays in use; a failed or
        quarantined replacement never becomes current.
        """
        media_type = canonical_media_type(media_type, original_filename)
        self._validate_upload(original_filename, media_type, total_bytes, expected_sha256)
        upload_id = str(uuid4())
        temp_path = self._reserve_temp(upload_id)
        superseded: list[str] = []
        try:
            with self._lock, self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = _lifecycle_row(connection, asset_id, project_id)
                current_metadata_revision = _check_editable(row, expected_revision)
                if str(row["status"]) == "uploading":
                    raise AssetReplacementStateError(
                        "wait for the asset to finish processing before replacing it"
                    )
                _require_same_kind(connection, row, media_type)
                pending = row["pending_revision"]
                if pending is not None:
                    if _revision_exists(connection, asset_id, int(pending)):
                        raise AssetReplacementStateError(
                            "a replacement for this asset is still processing"
                        )
                    superseded = [
                        str(item["temp_name"])
                        for item in connection.execute(
                            "SELECT temp_name FROM asset_uploads "
                            "WHERE asset_id = ? AND state = 'active'",
                            (asset_id,),
                        ).fetchall()
                    ]
                    connection.execute(
                        """
                        UPDATE asset_uploads SET state = 'failed', error = ?, updated_at = ?
                        WHERE asset_id = ? AND state = 'active'
                        """,
                        ("superseded by a newer replacement upload", occurred_at, asset_id),
                    )
                self._check_quota(connection, total_bytes)
                next_revision = _next_revision(connection, asset_id)
                connection.execute(
                    """
                    UPDATE assets
                    SET pending_revision = ?, replacement_error = NULL,
                        metadata_revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (next_revision, current_metadata_revision + 1, occurred_at, asset_id),
                )
                _insert_upload(
                    connection,
                    upload_id=upload_id,
                    asset_id=asset_id,
                    original_filename=original_filename,
                    media_type=media_type,
                    total_bytes=total_bytes,
                    expected_sha256=expected_sha256,
                    temp_name=temp_path.name,
                    occurred_at=occurred_at,
                )
                _insert_audit(
                    connection,
                    actor=actor,
                    action="asset.replace.begin",
                    target=asset_id,
                    occurred_at=occurred_at,
                    context={
                        "role": role.value,
                        "project_id": project_id,
                        "upload_id": upload_id,
                        "pending_revision": next_revision,
                        "current_revision": row["current_revision"],
                        "previous_metadata_revision": current_metadata_revision,
                        "total_bytes": total_bytes,
                        "media_type": media_type,
                    },
                )
                connection.commit()
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        for name in superseded:
            (self.upload_root / name).unlink(missing_ok=True)
        return self.get_asset(asset_id), self.get_upload(upload_id)

    def replace_from_asset(
        self,
        *,
        project_id: str,
        asset_id: str,
        source_asset_id: str,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredAsset:
        """Make another ready asset's current file the next revision of this asset.

        Content is deduplicated by digest, so no bytes are copied and the
        processed metadata and review artifacts are reused. The source asset is
        left unchanged; trash it afterwards if it is no longer needed.
        """
        if source_asset_id == asset_id:
            raise AssetReplacementStateError("choose a different asset as the replacement source")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _lifecycle_row(connection, asset_id, project_id)
            current_metadata_revision = _check_editable(row, expected_revision)
            if str(row["status"]) == "uploading" or row["pending_revision"] is not None:
                raise AssetReplacementStateError(
                    "wait for the current upload or replacement to finish first"
                )
            try:
                source_row = _lifecycle_row(connection, source_asset_id, project_id)
            except AssetNotFoundError as error:
                raise AssetNotFoundError("replacement source asset does not exist") from error
            if source_row["trashed_at"] is not None:
                raise AssetReplacementStateError("the replacement source asset is in the trash")
            if str(source_row["status"]) != "ready" or source_row["current_revision"] is None:
                raise AssetReplacementStateError(
                    "the replacement source asset has not finished processing"
                )
            source = connection.execute(
                """
                SELECT digest_sha256, size_bytes, media_type, original_filename,
                       scan_status, metadata_json
                FROM asset_revisions
                WHERE asset_id = ? AND revision = ?
                """,
                (source_asset_id, int(source_row["current_revision"])),
            ).fetchone()
            if source is None:
                raise AssetReplacementStateError("the replacement source asset has no file")
            source_digest = str(source["digest_sha256"])
            original = self.object_root / source_digest[:2] / source_digest
            if not original.is_file():
                raise AssetReplacementStateError(
                    "the replacement source asset's original file is missing"
                )
            _require_same_kind(connection, row, str(source["media_type"]))
            next_revision = _next_revision(connection, asset_id)
            connection.execute(
                """
                INSERT INTO asset_revisions(
                    asset_id, revision, digest_sha256, size_bytes, media_type,
                    original_filename, scan_status, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    next_revision,
                    str(source["digest_sha256"]),
                    int(source["size_bytes"]),
                    str(source["media_type"]),
                    str(source["original_filename"]),
                    str(source["scan_status"]),
                    str(source["metadata_json"]),
                    occurred_at,
                ),
            )
            connection.execute(
                """
                UPDATE assets
                SET current_revision = ?, status = 'ready', error = NULL,
                    replacement_error = NULL, metadata_revision = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_revision, current_metadata_revision + 1, occurred_at, asset_id),
            )
            _insert_audit(
                connection,
                actor=actor,
                action="asset.replace",
                target=asset_id,
                occurred_at=occurred_at,
                context={
                    "role": role.value,
                    "project_id": project_id,
                    "source_asset_id": source_asset_id,
                    "previous_revision": row["current_revision"],
                    "created_revision": next_revision,
                    "digest_sha256": str(source["digest_sha256"]),
                    "previous_metadata_revision": current_metadata_revision,
                },
            )
            connection.commit()
        return self.get_asset(asset_id)

    def revert_revision(
        self,
        *,
        project_id: str,
        asset_id: str,
        revision: int,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredAsset:
        """Make an earlier, successfully processed file revision current again."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _lifecycle_row(connection, asset_id, project_id)
            current_metadata_revision = _check_editable(row, expected_revision)
            if row["pending_revision"] is not None:
                raise AssetReplacementStateError(
                    "wait for the replacement in progress to finish first"
                )
            if row["current_revision"] is not None and int(row["current_revision"]) == revision:
                raise AssetReplacementStateError(f"revision {revision} is already current")
            target = connection.execute(
                """
                SELECT digest_sha256, size_bytes, scan_status,
                       json_extract(metadata_json, '$.status') AS processing_status
                FROM asset_revisions
                WHERE asset_id = ? AND revision = ?
                """,
                (asset_id, revision),
            ).fetchone()
            if target is None:
                raise AssetReplacementStateError(f"revision {revision} does not exist")
            if target["processing_status"] != "ready" or target["scan_status"] == "quarantined":
                raise AssetReplacementStateError(
                    f"revision {revision} was not processed successfully and cannot be restored"
                )
            digest = str(target["digest_sha256"])
            original = self.object_root / digest[:2] / digest
            if not original.is_file() or original.stat().st_size != int(target["size_bytes"]):
                raise AssetReplacementStateError(
                    f"the original file of revision {revision} is missing"
                )
            connection.execute(
                """
                UPDATE assets
                SET current_revision = ?, status = 'ready', error = NULL,
                    replacement_error = NULL, metadata_revision = ?, updated_at = ?
                WHERE id = ?
                """,
                (revision, current_metadata_revision + 1, occurred_at, asset_id),
            )
            _insert_audit(
                connection,
                actor=actor,
                action="asset.revert",
                target=asset_id,
                occurred_at=occurred_at,
                context={
                    "role": role.value,
                    "project_id": project_id,
                    "previous_revision": row["current_revision"],
                    "restored_revision": revision,
                    "previous_metadata_revision": current_metadata_revision,
                },
            )
            connection.commit()
        return self.get_asset(asset_id)

    def _validate_upload(
        self,
        original_filename: str,
        media_type: str,
        total_bytes: int,
        expected_sha256: str | None,
    ) -> None:
        _validate_filename(original_filename)
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise AssetUploadError(f"media type is not allowed: {media_type}")
        guessed_type = _guessed_media_type(original_filename)
        if guessed_type is not None and guessed_type != media_type:
            raise AssetUploadError(
                f"declared media type {media_type} does not match filename type {guessed_type}"
            )
        if total_bytes <= 0 or total_bytes > self.config.asset_max_bytes:
            raise AssetUploadError(
                f"asset size must be between 1 and {self.config.asset_max_bytes} bytes"
            )
        if expected_sha256 is not None and (
            len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise AssetUploadError("expected SHA-256 must be 64 lowercase hexadecimal characters")

    def _reserve_temp(self, upload_id: str) -> Path:
        temp_path = self.upload_root / f"{upload_id}.part"
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        return temp_path

    def _check_quota(self, connection: sqlite3.Connection, total_bytes: int) -> None:
        used_bytes = int(
            connection.execute(
                """
                SELECT COALESCE(SUM(size_bytes), 0)
                FROM (
                    SELECT digest_sha256, MAX(size_bytes) AS size_bytes
                    FROM asset_revisions
                    GROUP BY digest_sha256
                )
                """
            ).fetchone()[0]
        )
        reserved_bytes = int(
            connection.execute(
                """
                SELECT COALESCE(SUM(total_bytes), 0)
                FROM asset_uploads
                WHERE state = 'active'
                """
            ).fetchone()[0]
        )
        if (
            used_bytes + self._derived_bytes() + reserved_bytes + total_bytes
            > self.config.asset_store_quota_bytes
        ):
            raise AssetUploadError("managed asset-store quota would be exceeded")

    def append(
        self,
        *,
        upload_id: str,
        offset: int,
        chunk: bytes,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> tuple[StoredAsset, StoredAssetUpload]:
        if not chunk:
            raise AssetUploadError("upload chunk must not be empty")
        if len(chunk) > self.config.asset_chunk_max_bytes:
            raise AssetUploadError(
                f"upload chunk exceeds {self.config.asset_chunk_max_bytes} bytes"
            )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT asset_id, original_filename, media_type, total_bytes,
                       expected_sha256, received_bytes, temp_name, state
                FROM asset_uploads
                WHERE id = ?
                """,
                (upload_id,),
            ).fetchone()
            if row is None:
                raise AssetUploadError("upload does not exist")
            if str(row["state"]) != "active":
                raise AssetUploadError("upload is not active")
            received_bytes = int(row["received_bytes"])
            if offset != received_bytes:
                raise AssetUploadConflictError(received_bytes)
            total_bytes = int(row["total_bytes"])
            if offset + len(chunk) > total_bytes:
                raise AssetUploadError("chunk exceeds declared asset size")
            temp_path = self.upload_root / str(row["temp_name"])
            with temp_path.open("r+b") as target:
                target.seek(offset)
                target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            next_offset = offset + len(chunk)
            connection.execute(
                """
                UPDATE asset_uploads
                SET received_bytes = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_offset, occurred_at, upload_id),
            )
            if next_offset == total_bytes:
                self._finalize(connection, row, upload_id, temp_path, occurred_at)
            connection.commit()
            asset_id = str(row["asset_id"])
        asset = self.get_asset(asset_id)
        upload = self.get_upload(upload_id)
        self.repository.append_audit(
            actor=actor,
            action="asset.upload.chunk",
            target=asset_id,
            result=upload.state if next_offset == total_bytes else "progress",
            occurred_at=occurred_at,
            authorization_context={
                "role": role.value,
                "upload_id": upload_id,
                "received_bytes": next_offset,
                "total_bytes": total_bytes,
            },
        )
        return asset, upload

    def get_asset(self, asset_id: str) -> StoredAsset:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT {_ASSET_COLUMNS}
                FROM assets AS a
                LEFT JOIN asset_revisions AS r
                  ON r.asset_id = a.id AND r.revision = a.current_revision
                WHERE a.id = ?
                """,
                (asset_id,),
            ).fetchone()
            if row is None:
                raise AssetNotFoundError("asset does not exist")
            usage = _load_usage(connection)
            revisions = _revision_summaries(connection, "r.asset_id = ?", (asset_id,))
        return self._with_diagnostics(
            replace(_stored_asset(row), revisions=revisions.get(asset_id, ())),
            usage=usage,
        )

    def list_assets(
        self,
        project_id: str,
        *,
        trashed: bool | None = False,
    ) -> tuple[StoredAsset, ...]:
        """List project assets; trashed=False (default) active, True trash only, None all."""
        trash_filter = {
            False: "AND a.trashed_at IS NULL",
            True: "AND a.trashed_at IS NOT NULL",
            None: "",
        }[trashed]
        with self._connect() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM projects WHERE id = ?",
                    (project_id,),
                ).fetchone()
                is None
            ):
                raise AssetUploadError("project does not exist")
            rows = connection.execute(
                f"""
                SELECT {_ASSET_COLUMNS}
                FROM assets AS a
                LEFT JOIN asset_revisions AS r
                  ON r.asset_id = a.id AND r.revision = a.current_revision
                WHERE a.project_id = ? {trash_filter}
                ORDER BY a.updated_at DESC, a.id
                """,
                (project_id,),
            ).fetchall()
            usage = _load_usage(connection)
            revisions = _revision_summaries(connection, "a.project_id = ?", (project_id,))
        draft = self.repository.get_revision(project_id).draft
        return tuple(
            self._with_diagnostics(
                replace(_stored_asset(row), revisions=revisions.get(str(row["id"]), ())),
                draft=draft,
                usage=usage,
            )
            for row in rows
        )

    def update_metadata(
        self,
        *,
        asset_id: str,
        expected_revision: int,
        owner: str | None,
        license_name: str | None,
        provenance: str | None,
        review_status: Literal["unreviewed", "approved", "rejected"],
        expires_on: str | None,
        folder: str | None,
        tags: tuple[str, ...],
        favorite: bool,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredAsset:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT a.metadata_revision, a.trashed_at, p.archived_at
                FROM assets AS a
                JOIN projects AS p ON p.id = a.project_id
                WHERE a.id = ?
                """,
                (asset_id,),
            ).fetchone()
            if row is None:
                raise AssetUploadError("asset does not exist")
            if row["archived_at"] is not None:
                raise AssetMetadataLockedError("archived project assets cannot be edited")
            if row["trashed_at"] is not None:
                raise AssetMetadataLockedError("restore the asset from the trash before editing it")
            current_revision = int(row["metadata_revision"])
            if current_revision != expected_revision:
                raise AssetMetadataConflictError(current_revision)
            next_revision = current_revision + 1
            connection.execute(
                """
                UPDATE assets
                SET metadata_revision = ?, owner = ?, license = ?, provenance = ?,
                    review_status = ?, expires_on = ?, folder = ?, tags_json = ?,
                    favorite = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_revision,
                    owner,
                    license_name,
                    provenance,
                    review_status,
                    expires_on,
                    folder,
                    json.dumps(tags, separators=(",", ":")),
                    int(favorite),
                    occurred_at,
                    asset_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_events(
                    actor, action, target, result, occurred_at,
                    authorization_context_json
                ) VALUES (?, 'asset.metadata.update', ?, 'success', ?, ?)
                """,
                (
                    actor,
                    asset_id,
                    occurred_at,
                    json.dumps(
                        {
                            "role": role.value,
                            "previous_metadata_revision": current_revision,
                            "created_metadata_revision": next_revision,
                            "review_status": review_status,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.commit()
        return self.get_asset(asset_id)

    def trash(
        self,
        *,
        project_id: str,
        asset_id: str,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredAsset:
        """Soft-delete an asset unless live work still uses it; files are kept for restore."""
        purge_after = _format_timestamp(_parse_timestamp(occurred_at) + ASSET_TRASH_RETENTION)
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = _lifecycle_row(connection, asset_id, project_id)
                if row["archived_at"] is not None:
                    raise AssetMetadataLockedError("archived project assets cannot be edited")
                if row["trashed_at"] is not None:
                    raise AssetTrashStateError("asset is already in the trash")
                current_revision = int(row["metadata_revision"])
                if current_revision != expected_revision:
                    raise AssetMetadataConflictError(current_revision)
                blockers = _load_usage(connection).removal_blockers(asset_id, project_id)
                if blockers:
                    raise AssetRemovalBlockedError(blockers)
                canceled_uploads = connection.execute(
                    "SELECT id, temp_name FROM asset_uploads "
                    "WHERE asset_id = ? AND state = 'active'",
                    (asset_id,),
                ).fetchall()
                cancel_message = "upload canceled because the asset was moved to the trash"
                if canceled_uploads:
                    connection.execute(
                        """
                        UPDATE asset_uploads SET state = 'failed', error = ?, updated_at = ?
                        WHERE asset_id = ? AND state = 'active'
                        """,
                        (cancel_message, occurred_at, asset_id),
                    )
                    if row["current_revision"] is None:
                        connection.execute(
                            "UPDATE assets SET status = 'failed', error = ? WHERE id = ?",
                            (cancel_message, asset_id),
                        )
                if row["pending_revision"] is not None and not _revision_exists(
                    connection, asset_id, int(row["pending_revision"])
                ):
                    connection.execute(
                        "UPDATE assets SET pending_revision = NULL, replacement_error = ? "
                        "WHERE id = ?",
                        (cancel_message, asset_id),
                    )
                connection.execute(
                    """
                    UPDATE assets
                    SET trashed_at = ?, trashed_by = ?, purge_after = ?,
                        metadata_revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (occurred_at, actor, purge_after, current_revision + 1, occurred_at, asset_id),
                )
                _insert_audit(
                    connection,
                    actor=actor,
                    action="asset.trash",
                    target=asset_id,
                    occurred_at=occurred_at,
                    context={
                        "role": role.value,
                        "project_id": project_id,
                        "previous_metadata_revision": current_revision,
                        "purge_after": purge_after,
                        "canceled_uploads": [str(item["id"]) for item in canceled_uploads],
                    },
                )
                connection.commit()
            for item in canceled_uploads:
                (self.upload_root / str(item["temp_name"])).unlink(missing_ok=True)
        return self.get_asset(asset_id)

    def restore(
        self,
        *,
        project_id: str,
        asset_id: str,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredAsset:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _lifecycle_row(connection, asset_id, project_id)
            if row["archived_at"] is not None:
                raise AssetMetadataLockedError("archived project assets cannot be edited")
            if row["trashed_at"] is None:
                raise AssetTrashStateError("asset is not in the trash")
            current_revision = int(row["metadata_revision"])
            if current_revision != expected_revision:
                raise AssetMetadataConflictError(current_revision)
            connection.execute(
                """
                UPDATE assets
                SET trashed_at = NULL, trashed_by = NULL, purge_after = NULL,
                    metadata_revision = ?, updated_at = ?
                WHERE id = ?
                """,
                (current_revision + 1, occurred_at, asset_id),
            )
            _insert_audit(
                connection,
                actor=actor,
                action="asset.restore",
                target=asset_id,
                occurred_at=occurred_at,
                context={
                    "role": role.value,
                    "project_id": project_id,
                    "previous_metadata_revision": current_revision,
                },
            )
            connection.commit()
        return self.get_asset(asset_id)

    def purge(
        self,
        *,
        project_id: str,
        asset_id: str,
        expected_revision: int,
        confirmation_name: str,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> AssetPurgeResult:
        """Permanently delete a trashed asset now; shared file content stays while referenced."""
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = _lifecycle_row(connection, asset_id, project_id)
                if row["trashed_at"] is None:
                    raise AssetTrashStateError(
                        "move the asset to the trash before deleting it permanently"
                    )
                if confirmation_name != str(row["name"]):
                    raise AssetPurgeConfirmationError(
                        "type the asset name exactly to confirm permanent deletion"
                    )
                current_revision = int(row["metadata_revision"])
                if current_revision != expected_revision:
                    raise AssetMetadataConflictError(current_revision)
                digests, temp_names = self._delete_trashed(
                    connection,
                    row,
                    actor=actor,
                    occurred_at=occurred_at,
                    context={"role": role.value, "trigger": "administrator"},
                )
                connection.commit()
            released_objects, released_bytes = self._release_storage(digests, temp_names)
        return AssetPurgeResult(
            asset_id=asset_id,
            name=str(row["name"]),
            released_objects=released_objects,
            released_bytes=released_bytes,
        )

    def sweep_trash(self, *, occurred_at: str | None = None) -> AssetTrashSweepResult:
        """Purge trash past its retention window, then reclaim unreferenced stored files."""
        now_text = occurred_at or _now_iso()
        now = _parse_timestamp(now_text)
        purged: list[str] = []
        retained: dict[str, tuple[str, ...]] = {}
        with self._lock:
            with self._connect() as connection:
                candidates = [
                    str(row["id"])
                    for row in connection.execute(
                        "SELECT id, purge_after FROM assets WHERE trashed_at IS NOT NULL"
                    ).fetchall()
                    if row["purge_after"] is not None
                    and _parse_timestamp(str(row["purge_after"])) <= now
                ]
            for asset_id in candidates:
                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        row = _lifecycle_row(connection, asset_id, None)
                    except AssetUploadError:
                        connection.execute("ROLLBACK")
                        continue
                    if (
                        row["trashed_at"] is None
                        or row["purge_after"] is None
                        or _parse_timestamp(str(row["purge_after"])) > now
                    ):
                        connection.execute("ROLLBACK")
                        continue
                    try:
                        _, temp_names = self._delete_trashed(
                            connection,
                            row,
                            actor=TRASH_SWEEP_ACTOR,
                            occurred_at=now_text,
                            context={"trigger": "trash_retention_expired"},
                        )
                    except AssetRemovalBlockedError as error:
                        connection.execute("ROLLBACK")
                        retained[asset_id] = error.blockers
                        continue
                    connection.commit()
                for name in temp_names:
                    (self.upload_root / name).unlink(missing_ok=True)
                purged.append(asset_id)
            released_objects, released_bytes = self._collect_garbage(None)
        return AssetTrashSweepResult(
            purged_asset_ids=tuple(purged),
            retained=retained,
            released_objects=released_objects,
            released_bytes=released_bytes,
        )

    def _delete_trashed(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        actor: str,
        occurred_at: str,
        context: dict[str, object],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        asset_id = str(row["id"])
        blockers = _load_usage(connection).purge_blockers(asset_id, str(row["project_id"]))
        if blockers:
            raise AssetRemovalBlockedError(blockers)
        revisions = connection.execute(
            "SELECT digest_sha256, size_bytes FROM asset_revisions WHERE asset_id = ?",
            (asset_id,),
        ).fetchall()
        temp_names = tuple(
            str(item["temp_name"])
            for item in connection.execute(
                "SELECT temp_name FROM asset_uploads WHERE asset_id = ?",
                (asset_id,),
            ).fetchall()
        )
        connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        digests = tuple(sorted({str(item["digest_sha256"]) for item in revisions}))
        _insert_audit(
            connection,
            actor=actor,
            action="asset.purge",
            target=asset_id,
            occurred_at=occurred_at,
            context={
                **context,
                "project_id": str(row["project_id"]),
                "name": str(row["name"]),
                "trashed_at": str(row["trashed_at"]),
                "digests": list(digests),
                "revision_bytes": sum(int(item["size_bytes"]) for item in revisions),
            },
        )
        return digests, temp_names

    def _release_storage(
        self,
        digests: tuple[str, ...],
        temp_names: tuple[str, ...],
    ) -> tuple[int, int]:
        for name in temp_names:
            (self.upload_root / name).unlink(missing_ok=True)
        return self._collect_garbage(digests)

    def _collect_garbage(self, candidates: tuple[str, ...] | None) -> tuple[int, int]:
        """Delete stored originals/derivatives no asset revision or photo references.

        Callers hold self._lock, which serializes upload finalization. Files modified
        within the grace period are kept so concurrent photo writes are never removed.
        """
        with self._connect() as connection:
            referenced = {
                str(row[0])
                for row in connection.execute(
                    "SELECT digest_sha256 FROM asset_revisions "
                    "UNION SELECT digest_sha256 FROM installation_photos"
                ).fetchall()
            }
        if candidates is None:
            found: set[str] = set()
            for root in (self.object_root, self.artifact_root):
                for prefix in root.iterdir():
                    if not prefix.is_dir() or len(prefix.name) != 2:
                        continue
                    found.update(
                        entry.name
                        for entry in prefix.iterdir()
                        if _is_digest(entry.name) and entry.name.startswith(prefix.name)
                    )
            candidates = tuple(sorted(found))
        cutoff = time.time() - self.object_cleanup_grace_seconds
        released_objects = 0
        released_bytes = 0
        for digest in candidates:
            if digest in referenced or not _is_digest(digest):
                continue
            object_path = self.object_root / digest[:2] / digest
            derived_path = self.artifact_root / digest[:2] / digest
            if object_path.is_file():
                stat = object_path.stat()
                if stat.st_mtime > cutoff:
                    continue
                object_path.unlink(missing_ok=True)
                released_objects += 1
                released_bytes += stat.st_size
            if derived_path.is_dir():
                released_bytes += sum(
                    item.stat().st_size for item in derived_path.rglob("*") if item.is_file()
                )
                shutil.rmtree(derived_path, ignore_errors=True)
        return released_objects, released_bytes

    def get_upload(self, upload_id: str) -> StoredAssetUpload:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, asset_id, total_bytes, received_bytes, state, error
                FROM asset_uploads
                WHERE id = ?
                """,
                (upload_id,),
            ).fetchone()
        if row is None:
            raise AssetUploadError("upload does not exist")
        return StoredAssetUpload(
            id=str(row["id"]),
            asset_id=str(row["asset_id"]),
            total_bytes=int(row["total_bytes"]),
            received_bytes=int(row["received_bytes"]),
            state=str(row["state"]),
            error=str(row["error"]) if row["error"] is not None else None,
        )

    def _finalize(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        upload_id: str,
        temp_path: Path,
        occurred_at: str,
    ) -> None:
        digest = _sha256_file(temp_path)
        expected = row["expected_sha256"]
        asset_id = str(row["asset_id"])
        pending = connection.execute(
            "SELECT pending_revision FROM assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
        pending_revision = (
            int(pending["pending_revision"])
            if pending is not None and pending["pending_revision"] is not None
            else None
        )
        if expected is not None and str(expected) != digest:
            error = "uploaded content SHA-256 does not match expected digest"
            connection.execute(
                "UPDATE asset_uploads SET state = 'failed', error = ? WHERE id = ?",
                (error, upload_id),
            )
            if pending_revision is not None:
                connection.execute(
                    """
                    UPDATE assets
                    SET pending_revision = NULL, replacement_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (error, occurred_at, asset_id),
                )
            else:
                connection.execute(
                    "UPDATE assets SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                    (error, occurred_at, asset_id),
                )
            temp_path.unlink(missing_ok=True)
            return
        object_path = self.object_root / digest[:2] / digest
        object_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if object_path.exists():
            if object_path.stat().st_size != int(row["total_bytes"]):
                raise AssetUploadError("existing content-addressed object size is inconsistent")
            temp_path.unlink()
            os.utime(object_path)
        else:
            os.replace(temp_path, object_path)
            object_path.chmod(0o600)
        metadata = {
            "status": "unavailable",
            "reason": "media processing has not completed",
        }
        revision = pending_revision if pending_revision is not None else 1
        connection.execute(
            """
            INSERT INTO asset_revisions(
                asset_id, revision, digest_sha256, size_bytes, media_type,
                original_filename, scan_status, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'unavailable', ?, ?)
            """,
            (
                asset_id,
                revision,
                digest,
                int(row["total_bytes"]),
                str(row["media_type"]),
                str(row["original_filename"]),
                json.dumps(metadata, separators=(",", ":")),
                occurred_at,
            ),
        )
        if pending_revision is None:
            connection.execute(
                """
                UPDATE assets
                SET status = 'uploading', current_revision = 1, error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (occurred_at, asset_id),
            )
        else:
            connection.execute(
                "UPDATE assets SET updated_at = ? WHERE id = ?",
                (occurred_at, asset_id),
            )
        connection.execute(
            """
            UPDATE asset_uploads
            SET state = 'completed', error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (occurred_at, upload_id),
        )

    def process_asset(self, asset_id: str, *, occurred_at: str) -> None:
        with self._processing_lock:
            self._process_asset(asset_id, occurred_at=occurred_at)

    def _process_asset(self, asset_id: str, *, occurred_at: str) -> None:
        revision, digest, media_type, replacement = self._processing_target(asset_id)
        object_path = self.object_root / digest[:2] / digest
        scan_status = "unavailable"
        clamscan = shutil.which("clamscan")
        if clamscan is not None:
            try:
                scan = subprocess.run(
                    [clamscan, "--no-summary", str(object_path)],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=300,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                self._finish_processing(
                    asset_id=asset_id,
                    revision=revision,
                    replacement=replacement,
                    status="failed",
                    scan_status="unavailable",
                    metadata={"status": "failed"},
                    error=f"malware scanner failed: {error}",
                    occurred_at=occurred_at,
                )
                return
            if scan.returncode == 0:
                scan_status = "clean"
            elif scan.returncode == 1:
                self._finish_processing(
                    asset_id=asset_id,
                    revision=revision,
                    replacement=replacement,
                    status="quarantined",
                    scan_status="quarantined",
                    metadata={"status": "quarantined"},
                    error="malware scanner quarantined the uploaded asset",
                    occurred_at=occurred_at,
                )
                return
            else:
                diagnostic = _last_diagnostic(scan.stderr or scan.stdout)
                self._finish_processing(
                    asset_id=asset_id,
                    revision=revision,
                    replacement=replacement,
                    status="failed",
                    scan_status="unavailable",
                    metadata={"status": "failed"},
                    error=f"malware scanner failed: {diagnostic}",
                    occurred_at=occurred_at,
                )
                return
        try:
            metadata = _extract_probe_metadata(
                json.loads(_run_media_tool(["probe", str(object_path)], timeout=120))
            )
            if metadata["audio_codecs"]:
                metadata["loudness_lufs"] = _measure_loudness(object_path)
                metadata["loudness_status"] = (
                    "ready" if metadata["loudness_lufs"] is not None else "unavailable"
                )
            else:
                metadata["loudness_lufs"] = None
                metadata["loudness_status"] = "not_applicable"
            metadata["artifacts"] = self._generate_artifacts(
                source=object_path,
                digest=digest,
                media_type=media_type,
                has_audio=bool(metadata["audio_codecs"]),
            )
            metadata["status"] = "ready"
        except (
            json.JSONDecodeError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
        ) as error:
            self._finish_processing(
                asset_id=asset_id,
                revision=revision,
                replacement=replacement,
                status="failed",
                scan_status=scan_status,
                metadata={"status": "failed"},
                error=f"media metadata extraction failed: {error}",
                occurred_at=occurred_at,
            )
            return
        self._finish_processing(
            asset_id=asset_id,
            revision=revision,
            replacement=replacement,
            status="ready",
            scan_status=scan_status,
            metadata=metadata,
            error=None,
            occurred_at=occurred_at,
        )

    def get_artifact(self, asset_id: str, kind: str) -> tuple[Path, str]:
        if kind not in {"review", "thumbnail", "waveform"}:
            raise AssetUploadError("asset artifact kind is not supported")
        asset = self.get_asset(asset_id)
        artifacts = asset.metadata.get("artifacts") if asset.metadata is not None else None
        if not isinstance(artifacts, dict):
            raise AssetUploadError("asset artifacts are not available")
        artifact = artifacts.get(kind)
        if not isinstance(artifact, dict):
            raise AssetUploadError(f"asset {kind} artifact is not available")
        media_type = artifact.get("media_type")
        if not isinstance(media_type, str):
            raise AssetUploadError("asset artifact metadata is invalid")
        if asset.digest_sha256 is None:
            raise AssetUploadError("asset has no finalized content")
        path = self._artifact_path(asset.digest_sha256, kind, media_type)
        if not path.is_file():
            raise AssetUploadError("asset artifact file is missing")
        expected_size = artifact.get("size_bytes")
        if isinstance(expected_size, int) and path.stat().st_size != expected_size:
            raise AssetUploadError("asset artifact file size is inconsistent")
        return path, media_type

    def current_revision(self, asset_id: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT current_revision FROM assets WHERE id = ?",
                (asset_id,),
            ).fetchone()
        if row is None:
            raise AssetNotFoundError("asset does not exist")
        return int(row["current_revision"]) if row["current_revision"] is not None else None

    def get_original(self, asset_id: str) -> tuple[StoredAsset, Path]:
        asset = self.get_asset(asset_id)
        if (
            asset.status != "ready"
            or asset.current_revision is None
            or asset.digest_sha256 is None
            or asset.media_type is None
        ):
            raise AssetUploadError("asset original is not ready for rendering")
        path = self.object_root / asset.digest_sha256[:2] / asset.digest_sha256
        if not path.is_file():
            raise AssetUploadError("asset original file is missing")
        if asset.size_bytes is not None and path.stat().st_size != asset.size_bytes:
            raise AssetUploadError("asset original file size does not match metadata")
        return asset, path

    def recover_pending_assets(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.id
                FROM assets AS a
                WHERE (a.status = 'uploading' AND a.current_revision IS NOT NULL)
                   OR EXISTS (
                       SELECT 1 FROM asset_revisions AS r
                       WHERE r.asset_id = a.id AND r.revision = a.pending_revision
                   )
                ORDER BY a.created_at, a.id
                """
            ).fetchall()
        asset_ids = tuple(str(row["id"]) for row in rows)
        for asset_id in asset_ids:
            self.process_asset(asset_id, occurred_at=_now_iso())
        return asset_ids

    def _processing_target(self, asset_id: str) -> tuple[int, str, str, bool]:
        """Return the revision to process: an uploaded replacement first, else the current one."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT current_revision, pending_revision FROM assets WHERE id = ?",
                (asset_id,),
            ).fetchone()
            if row is None:
                raise AssetNotFoundError("asset does not exist")
            candidates: list[tuple[int, bool]] = []
            if row["pending_revision"] is not None:
                candidates.append((int(row["pending_revision"]), True))
            if row["current_revision"] is not None:
                candidates.append((int(row["current_revision"]), False))
            for revision, replacement in candidates:
                target = connection.execute(
                    "SELECT digest_sha256, media_type FROM asset_revisions "
                    "WHERE asset_id = ? AND revision = ?",
                    (asset_id, revision),
                ).fetchone()
                if target is not None:
                    return (
                        revision,
                        str(target["digest_sha256"]),
                        str(target["media_type"]),
                        replacement,
                    )
        raise AssetUploadError("asset has no finalized content")

    def _finish_processing(
        self,
        *,
        asset_id: str,
        revision: int,
        replacement: bool,
        status: str,
        scan_status: str,
        metadata: dict[str, object],
        error: str | None,
        occurred_at: str,
    ) -> None:
        promoted = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if replacement:
                pending = connection.execute(
                    "SELECT pending_revision, metadata_revision, current_revision "
                    "FROM assets WHERE id = ?",
                    (asset_id,),
                ).fetchone()
                if pending is None or pending["pending_revision"] != revision:
                    connection.execute("ROLLBACK")
                    return
            cursor = connection.execute(
                """
                UPDATE asset_revisions
                SET scan_status = ?, metadata_json = ?
                WHERE asset_id = ? AND revision = ?
                """,
                (
                    scan_status,
                    json.dumps(metadata, separators=(",", ":")),
                    asset_id,
                    revision,
                ),
            )
            if cursor.rowcount == 0:
                raise AssetUploadError("asset revision does not exist")
            if not replacement:
                connection.execute(
                    """
                    UPDATE assets
                    SET status = ?, error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, error, occurred_at, asset_id),
                )
            elif status == "ready":
                promoted = True
                connection.execute(
                    """
                    UPDATE assets
                    SET current_revision = ?, pending_revision = NULL, status = 'ready',
                        error = NULL, replacement_error = NULL,
                        metadata_revision = metadata_revision + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (revision, occurred_at, asset_id),
                )
            else:
                # The rejected file never becomes current; drop its revision so it
                # cannot be restored, and let garbage collection reclaim the bytes.
                connection.execute(
                    "DELETE FROM asset_revisions WHERE asset_id = ? AND revision = ?",
                    (asset_id, revision),
                )
                connection.execute(
                    """
                    UPDATE assets
                    SET pending_revision = NULL, replacement_error = ?,
                        metadata_revision = metadata_revision + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        f"Replacement {status}: {error}" if error else f"Replacement {status}",
                        occurred_at,
                        asset_id,
                    ),
                )
            connection.commit()
        self.repository.append_audit(
            actor="system:asset_processor",
            action="asset.replace.process" if replacement else "asset.process",
            target=asset_id,
            result=status,
            occurred_at=occurred_at,
            authorization_context={
                "revision": revision,
                "promoted": promoted,
                "scan_status": scan_status,
                "metadata_status": str(metadata.get("status", "unknown")),
            },
        )

    def _generate_artifacts(
        self,
        *,
        source: Path,
        digest: str,
        media_type: str | None,
        has_audio: bool,
    ) -> dict[str, object]:
        if media_type is None:
            raise ValueError("asset media type is unavailable")
        family = media_type.split("/", 1)[0]
        if family not in {"audio", "image", "video"}:
            raise ValueError(f"unsupported asset media family: {family}")
        directory = self.artifact_root / digest[:2] / digest / DERIVATIVE_PROFILE
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        artifacts: dict[str, object] = {}
        if family in {"image", "video"}:
            thumbnail_type, thumbnail_suffix = THUMBNAIL_ARTIFACT_TYPE
            thumbnail = directory / f"thumbnail{thumbnail_suffix}"
            self._run_media_artifact(
                ["thumbnail", *(["--representative"] if family == "video" else [])],
                source,
                thumbnail,
            )
            artifacts["thumbnail"] = self._artifact_metadata(
                thumbnail,
                thumbnail_type,
                digest,
            )
        review_type, review_suffix = REVIEW_ARTIFACT_TYPES[family]
        if media_type == "image/gif":
            review = directory / "review.gif"
            shutil.copyfile(source, review)
            artifacts["review"] = self._artifact_metadata(review, media_type, digest)
            return artifacts
        review = directory / f"review{review_suffix}"
        self._run_media_artifact([f"{family}-review"], source, review)
        artifacts["review"] = self._artifact_metadata(review, review_type, digest)
        if has_audio:
            waveform_type, waveform_suffix = WAVEFORM_ARTIFACT_TYPE
            waveform = directory / f"waveform{waveform_suffix}"
            self._run_media_artifact(["waveform"], source, waveform)
            artifacts["waveform"] = self._artifact_metadata(waveform, waveform_type, digest)
        return artifacts

    def _run_media_artifact(self, operation: list[str], source: Path, target: Path) -> None:
        if target.is_file():
            return
        temporary = target.with_name(f".{target.stem}-{uuid4().hex}.tmp{target.suffix}")
        try:
            _run_media_tool([*operation, str(source), str(temporary)], timeout=900)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise ValueError("media processing did not produce the expected artifact")
            os.replace(temporary, target)
            target.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _artifact_metadata(self, path: Path, media_type: str, digest: str) -> dict[str, object]:
        return {
            "media_type": media_type,
            "size_bytes": path.stat().st_size,
            "source_digest_sha256": digest,
            "profile": DERIVATIVE_PROFILE,
        }

    def _with_diagnostics(
        self,
        asset: StoredAsset,
        *,
        draft: DraftProject | None = None,
        usage: _AssetUsage | None = None,
    ) -> StoredAsset:
        original_file_status: Literal["pending", "available", "missing", "size_mismatch"]
        if asset.digest_sha256 is None:
            original_file_status = "pending"
        else:
            original = self.object_root / asset.digest_sha256[:2] / asset.digest_sha256
            if not original.is_file():
                original_file_status = "missing"
            elif asset.size_bytes is not None and original.stat().st_size != asset.size_bytes:
                original_file_status = "size_mismatch"
            else:
                original_file_status = "available"
        artifact_faults: list[str] = []
        artifacts = asset.metadata.get("artifacts") if asset.metadata is not None else None
        if isinstance(artifacts, dict) and asset.digest_sha256 is not None:
            for kind, descriptor in sorted(artifacts.items()):
                if not isinstance(kind, str) or not isinstance(descriptor, dict):
                    artifact_faults.append("invalid artifact metadata")
                    continue
                media_type = descriptor.get("media_type")
                if not isinstance(media_type, str):
                    artifact_faults.append(f"{kind}: invalid metadata")
                    continue
                try:
                    path = self._artifact_path(asset.digest_sha256, kind, media_type)
                except AssetUploadError:
                    artifact_faults.append(f"{kind}: invalid metadata")
                    continue
                if not path.is_file():
                    artifact_faults.append(f"{kind}: missing")
                    continue
                expected_size = descriptor.get("size_bytes")
                if isinstance(expected_size, int) and path.stat().st_size != expected_size:
                    artifact_faults.append(f"{kind}: size mismatch")
        current_draft = draft or self.repository.get_revision(asset.project_id).draft
        references = _asset_references(current_draft, asset.id)
        if usage is None:
            with self._connect() as connection:
                usage = _load_usage(connection)
        draft_usage = usage.draft_usage(asset.project_id, asset.id)
        trashed = asset.trashed_at is not None
        return replace(
            asset,
            original_file_status=original_file_status,
            artifact_faults=tuple(artifact_faults),
            references=references,
            unused=not references and not draft_usage,
            draft_usage=draft_usage,
            removal_blockers=() if trashed else usage.removal_blockers(asset.id, asset.project_id),
            purge_blockers=usage.purge_blockers(asset.id, asset.project_id) if trashed else (),
        )

    def _artifact_path(self, digest: str, kind: str, media_type: str) -> Path:
        suffix = _artifact_suffix(kind, media_type)
        return self.artifact_root / digest[:2] / digest / DERIVATIVE_PROFILE / f"{kind}{suffix}"

    def _derived_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.artifact_root.rglob("*") if path.is_file())

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


def _validate_filename(value: str) -> None:
    if (
        not value
        or PurePath(value).name != value
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise AssetUploadError("original filename must be a plain filename")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_probe_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("media probe response must be an object")
    streams = value.get("streams", [])
    format_value = value.get("format", {})
    if not isinstance(streams, list) or not isinstance(format_value, dict):
        raise ValueError("media probe streams and format have invalid shapes")
    typed_streams = [stream for stream in streams if isinstance(stream, dict)]
    video_streams = [stream for stream in typed_streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in typed_streams if stream.get("codec_type") == "audio"]
    duration = _optional_float(format_value.get("duration"))
    if duration is None:
        durations = [
            candidate
            for candidate in (_optional_float(stream.get("duration")) for stream in typed_streams)
            if candidate is not None
        ]
        duration = max(durations, default=None)
    frame_rate = None
    if video_streams:
        rate_value = video_streams[0].get("avg_frame_rate")
        if isinstance(rate_value, str) and "/" in rate_value:
            numerator, denominator = rate_value.split("/", 1)
            if float(denominator) != 0:
                frame_rate = float(numerator) / float(denominator)
    return {
        "duration_ms": round(duration * 1000) if duration is not None else None,
        "width": max(
            (
                int(stream["width"])
                for stream in video_streams
                if isinstance(stream.get("width"), int)
            ),
            default=None,
        ),
        "height": max(
            (
                int(stream["height"])
                for stream in video_streams
                if isinstance(stream.get("height"), int)
            ),
            default=None,
        ),
        "frame_rate": round(frame_rate, 3) if frame_rate is not None else None,
        "video_codecs": sorted(
            {str(stream["codec_name"]) for stream in video_streams if stream.get("codec_name")}
        ),
        "audio_codecs": sorted(
            {str(stream["codec_name"]) for stream in audio_streams if stream.get("codec_name")}
        ),
        "channels": max(
            (
                int(stream["channels"])
                for stream in audio_streams
                if isinstance(stream.get("channels"), int)
            ),
            default=None,
        ),
        "sample_rate": max(
            (
                int(stream["sample_rate"])
                for stream in audio_streams
                if isinstance(stream.get("sample_rate"), str)
                and str(stream["sample_rate"]).isdigit()
            ),
            default=None,
        ),
        "format_name": (
            str(format_value["format_name"]) if format_value.get("format_name") else None
        ),
    }


def _measure_loudness(path: Path) -> float | None:
    measured = json.loads(_run_media_tool(["loudness", str(path)], timeout=300))
    value = measured.get("integrated_lufs") if isinstance(measured, dict) else None
    return float(value) if isinstance(value, int | float) else None


def _run_media_tool(arguments: list[str], *, timeout: int) -> str:
    """Run PyAV media work in a child interpreter to isolate untrusted decoding."""
    completed = subprocess.run(
        [sys.executable, "-m", MEDIA_TOOL_MODULE, *arguments],
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise ValueError(_last_diagnostic(completed.stderr))
    return completed.stdout


def _optional_float(value: object) -> float | None:
    if not isinstance(value, str | int | float):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_diagnostic(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return lines[-1][:500] if lines else "no diagnostic was reported"


def _artifact_suffix(kind: str, media_type: str) -> str:
    if kind == "review" and media_type == "image/gif":
        return ".gif"
    if kind == "thumbnail" and media_type == THUMBNAIL_ARTIFACT_TYPE[0]:
        return THUMBNAIL_ARTIFACT_TYPE[1]
    if kind == "waveform" and media_type == WAVEFORM_ARTIFACT_TYPE[0]:
        return WAVEFORM_ARTIFACT_TYPE[1]
    if kind == "review":
        for expected_media_type, suffix in REVIEW_ARTIFACT_TYPES.values():
            if media_type == expected_media_type:
                return suffix
    raise AssetUploadError("asset artifact metadata is invalid")


def _asset_references(draft: DraftProject, asset_id: str) -> tuple[AssetSceneReference, ...]:
    references: list[AssetSceneReference] = []
    for scene in draft.scenes:
        if scene.schema_version == 1:
            if scene.media.asset == asset_id:
                references.append(
                    AssetSceneReference(
                        scene_id=scene.id,
                        scene_name=scene.name,
                        output_id=None,
                    )
                )
            continue
        references.extend(
            AssetSceneReference(
                scene_id=scene.id,
                scene_name=scene.name,
                output_id=output.id,
            )
            for output in scene.media
            if output.asset == asset_id
        )
    return tuple(references)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stored_asset(row: sqlite3.Row) -> StoredAsset:
    metadata = json.loads(str(row["metadata_json"])) if row["metadata_json"] is not None else None
    tags_value = json.loads(str(row["tags_json"]))
    if not isinstance(tags_value, list) or not all(isinstance(tag, str) for tag in tags_value):
        raise AssetUploadError("asset tags are invalid")
    review_status_value = str(row["review_status"])
    if review_status_value not in {"unreviewed", "approved", "rejected"}:
        raise AssetUploadError("asset review status is invalid")
    review_status = cast(
        Literal["unreviewed", "approved", "rejected"],
        review_status_value,
    )
    return StoredAsset(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        name=str(row["name"]),
        status=str(row["status"]),
        current_revision=(
            int(row["current_revision"]) if row["current_revision"] is not None else None
        ),
        metadata_revision=int(row["metadata_revision"]),
        owner=str(row["owner"]) if row["owner"] is not None else None,
        license=str(row["license"]) if row["license"] is not None else None,
        provenance=str(row["provenance"]) if row["provenance"] is not None else None,
        review_status=review_status,
        expires_on=str(row["expires_on"]) if row["expires_on"] is not None else None,
        folder=str(row["folder"]) if row["folder"] is not None else None,
        tags=tuple(tags_value),
        favorite=bool(row["favorite"]),
        error=str(row["error"]) if row["error"] is not None else None,
        digest_sha256=(str(row["digest_sha256"]) if row["digest_sha256"] is not None else None),
        size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
        media_type=str(row["media_type"]) if row["media_type"] is not None else None,
        original_filename=(
            str(row["original_filename"]) if row["original_filename"] is not None else None
        ),
        scan_status=(str(row["scan_status"]) if row["scan_status"] is not None else None),
        metadata=metadata,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        trashed_at=str(row["trashed_at"]) if row["trashed_at"] is not None else None,
        trashed_by=str(row["trashed_by"]) if row["trashed_by"] is not None else None,
        purge_after=str(row["purge_after"]) if row["purge_after"] is not None else None,
        pending_revision=(
            int(row["pending_revision"]) if row["pending_revision"] is not None else None
        ),
        replacement_error=(
            str(row["replacement_error"]) if row["replacement_error"] is not None else None
        ),
    )


def _lifecycle_row(
    connection: sqlite3.Connection,
    asset_id: str,
    project_id: str | None,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT a.id, a.project_id, a.name, a.status, a.current_revision,
               a.metadata_revision, a.trashed_at, a.purge_after, a.pending_revision,
               p.archived_at
        FROM assets AS a
        JOIN projects AS p ON p.id = a.project_id
        WHERE a.id = ?
        """,
        (asset_id,),
    ).fetchone()
    if row is None or (project_id is not None and str(row["project_id"]) != project_id):
        raise AssetNotFoundError("asset does not exist")
    return cast(sqlite3.Row, row)


def _check_editable(row: sqlite3.Row, expected_revision: int) -> int:
    """Validate that an asset's file can change; return its current metadata revision."""
    if row["archived_at"] is not None:
        raise AssetMetadataLockedError("archived project assets cannot be edited")
    if row["trashed_at"] is not None:
        raise AssetTrashStateError("restore the asset from the trash before replacing its file")
    current = int(row["metadata_revision"])
    if current != expected_revision:
        raise AssetMetadataConflictError(current)
    return current


def canonical_media_type(media_type: str, original_filename: str) -> str:
    """Canonical name of a declared type; uses the filename when the browser declared none."""
    declared = media_type.split(";", 1)[0].strip().lower()
    declared = MEDIA_TYPE_ALIASES.get(declared, declared)
    if declared in UNDECLARED_MEDIA_TYPES:
        return _guessed_media_type(original_filename) or declared or "application/octet-stream"
    return declared


def _guessed_media_type(original_filename: str) -> str | None:
    guessed, _ = mimetypes.guess_type(original_filename)
    if guessed is None:
        return None
    guessed = guessed.lower()
    return MEDIA_TYPE_ALIASES.get(guessed, guessed)


def _media_kind(media_type: str) -> str:
    return media_type.split("/", 1)[0]


def _require_same_kind(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    media_type: str,
) -> None:
    """Scenes treat images, video and audio differently, so a replacement keeps its kind."""
    if row["current_revision"] is None:
        return
    current = connection.execute(
        "SELECT media_type FROM asset_revisions WHERE asset_id = ? AND revision = ?",
        (str(row["id"]), int(row["current_revision"])),
    ).fetchone()
    if current is None:
        return
    current_kind = _media_kind(str(current["media_type"]))
    if _media_kind(media_type) != current_kind:
        raise AssetUploadError(
            f"replacement must be {current_kind} media like the current file; "
            f"{media_type} is a different kind"
        )


def _revision_exists(connection: sqlite3.Connection, asset_id: str, revision: int) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM asset_revisions WHERE asset_id = ? AND revision = ?",
            (asset_id, revision),
        ).fetchone()
        is not None
    )


def _next_revision(connection: sqlite3.Connection, asset_id: str) -> int:
    return (
        int(
            connection.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM asset_revisions WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0]
        )
        + 1
    )


def _insert_upload(
    connection: sqlite3.Connection,
    *,
    upload_id: str,
    asset_id: str,
    original_filename: str,
    media_type: str,
    total_bytes: int,
    expected_sha256: str | None,
    temp_name: str,
    occurred_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO asset_uploads(
            id, asset_id, original_filename, media_type, total_bytes,
            expected_sha256, received_bytes, temp_name, state,
            error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'active', NULL, ?, ?)
        """,
        (
            upload_id,
            asset_id,
            original_filename,
            media_type,
            total_bytes,
            expected_sha256,
            temp_name,
            occurred_at,
            occurred_at,
        ),
    )


def _revision_summaries(
    connection: sqlite3.Connection,
    where: str,
    parameters: tuple[str, ...],
) -> dict[str, tuple[AssetRevisionSummary, ...]]:
    rows = connection.execute(
        f"""
        SELECT r.asset_id, r.revision, r.digest_sha256, r.size_bytes, r.media_type,
               r.original_filename, r.scan_status, r.created_at,
               json_extract(r.metadata_json, '$.status') AS processing_status
        FROM asset_revisions AS r
        JOIN assets AS a ON a.id = r.asset_id
        WHERE {where}
        ORDER BY r.asset_id, r.revision DESC
        """,
        parameters,
    ).fetchall()
    grouped: dict[str, list[AssetRevisionSummary]] = {}
    for row in rows:
        status: Literal["ready", "processing", "failed", "quarantined"]
        if row["scan_status"] == "quarantined" or row["processing_status"] == "quarantined":
            status = "quarantined"
        elif row["processing_status"] == "ready":
            status = "ready"
        elif row["processing_status"] == "failed":
            status = "failed"
        else:
            status = "processing"
        grouped.setdefault(str(row["asset_id"]), []).append(
            AssetRevisionSummary(
                revision=int(row["revision"]),
                digest_sha256=str(row["digest_sha256"]),
                size_bytes=int(row["size_bytes"]),
                media_type=str(row["media_type"]),
                original_filename=str(row["original_filename"]),
                status=status,
                created_at=str(row["created_at"]),
            )
        )
    return {key: tuple(value) for key, value in grouped.items()}


def _load_usage(connection: sqlite3.Connection) -> _AssetUsage:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    drafts = {
        str(row["id"]): (str(row["name"]), str(row["content_json"]))
        for row in connection.execute(
            """
            SELECT p.id, p.name, r.content_json
            FROM projects AS p
            JOIN revisions AS r ON r.project_id = p.id AND r.revision = p.head_revision
            """
        ).fetchall()
    }
    projector_content: dict[str, list[str]] = {}
    for row in connection.execute(
        """
        SELECT s.content_asset_id, g.name
        FROM projector_surface_mappings AS s
        JOIN installation_geometry AS g ON g.id = s.geometry_id
        WHERE s.content_asset_id IS NOT NULL
        ORDER BY g.name
        """
    ).fetchall():
        projector_content.setdefault(str(row["content_asset_id"]), []).append(str(row["name"]))
    calibration_assets = frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT test_asset_id FROM accepted_calibrations WHERE test_asset_id IS NOT NULL"
        ).fetchall()
    )
    packages: tuple[tuple[str, int, str, str], ...] = ()
    if "published_packages" in tables:
        packages = tuple(
            (str(row[0]), int(row[1]), str(row[2]), str(row[3]))
            for row in connection.execute(
                "SELECT project_id, revision, state, manifest_json FROM published_packages "
                "ORDER BY created_at, id"
            ).fetchall()
        )
    return _AssetUsage(
        drafts=drafts,
        projector_content={key: tuple(value) for key, value in projector_content.items()},
        calibration_assets=calibration_assets,
        packages=packages,
    )


def _draft_usage(document: object, asset_id: str) -> tuple[str, ...]:
    """Describe where a draft uses an asset; stale editor asset_bindings are not uses."""
    if not isinstance(document, dict):
        return ()
    locations: list[str] = []
    scenes = document.get("scenes")
    if isinstance(scenes, list):
        for scene in scenes:
            if _mentions(scene, asset_id):
                label = scene.get("name") or scene.get("id") if isinstance(scene, dict) else None
                locations.append(f'scene "{label}"' if label else "a scene")
    for key, value in document.items():
        if key == "scenes":
            continue
        if key == "editor_metadata" and isinstance(value, dict):
            for metadata_key, metadata_value in sorted(value.items()):
                if metadata_key != "asset_bindings" and _mentions(metadata_value, asset_id):
                    locations.append(
                        _EDITOR_USAGE_LABELS.get(metadata_key, metadata_key.replace("_", " "))
                    )
        elif _mentions(value, asset_id):
            locations.append(str(key).replace("_", " "))
    return tuple(dict.fromkeys(locations))


def _mentions(value: object, asset_id: str) -> bool:
    if isinstance(value, str):
        return value == asset_id
    if isinstance(value, dict):
        return any(key == asset_id or _mentions(item, asset_id) for key, item in value.items())
    if isinstance(value, list):
        return any(_mentions(item, asset_id) for item in value)
    return False


def _insert_audit(
    connection: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    target: str,
    occurred_at: str,
    context: dict[str, object],
) -> None:
    connection.execute(
        """
        INSERT INTO audit_events(
            actor, action, target, result, occurred_at, authorization_context_json
        ) VALUES (?, ?, ?, 'success', ?, ?)
        """,
        (
            actor,
            action,
            target,
            occurred_at,
            json.dumps(context, sort_keys=True, separators=(",", ":")),
        ),
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
