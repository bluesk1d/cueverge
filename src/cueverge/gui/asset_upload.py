import hashlib
import json
import mimetypes
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
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
REVIEW_ARTIFACT_TYPES = {
    "audio": ("audio/mpeg", ".mp3"),
    "image": ("image/jpeg", ".jpg"),
    "video": ("video/mp4", ".mp4"),
}
THUMBNAIL_ARTIFACT_TYPE = ("image/jpeg", ".jpg")
WAVEFORM_ARTIFACT_TYPE = ("image/png", ".png")
DERIVATIVE_PROFILE = "review-v1"
MEDIA_TOOL_MODULE = "cueverge.gui.media_tools"


class AssetUploadError(RuntimeError):
    pass


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


@dataclass(frozen=True)
class AssetSceneReference:
    scene_id: str
    scene_name: str
    output_id: str | None


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
        _validate_filename(original_filename)
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise AssetUploadError(f"media type is not allowed: {media_type}")
        guessed_type, _ = mimetypes.guess_type(original_filename)
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
        asset_id = str(uuid4())
        upload_id = str(uuid4())
        temp_name = f"{upload_id}.part"
        temp_path = self.upload_root / temp_name
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
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
                connection.execute(
                    """
                    INSERT INTO assets(
                        id, project_id, name, status, current_revision,
                        error, created_at, updated_at
                    ) VALUES (?, ?, ?, 'uploading', NULL, NULL, ?, ?)
                    """,
                    (asset_id, project_id, name, occurred_at, occurred_at),
                )
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
                """
                SELECT a.id, a.project_id, a.name, a.status, a.current_revision,
                       a.metadata_revision, a.owner, a.license, a.provenance,
                       a.review_status, a.expires_on, a.folder, a.tags_json,
                       a.favorite,
                       a.error, a.created_at, a.updated_at,
                       r.digest_sha256, r.size_bytes, r.media_type,
                       r.original_filename, r.scan_status, r.metadata_json
                FROM assets AS a
                LEFT JOIN asset_revisions AS r
                  ON r.asset_id = a.id AND r.revision = a.current_revision
                WHERE a.id = ?
                """,
                (asset_id,),
            ).fetchone()
        if row is None:
            raise AssetUploadError("asset does not exist")
        return self._with_diagnostics(_stored_asset(row))

    def list_assets(self, project_id: str) -> tuple[StoredAsset, ...]:
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
                """
                SELECT a.id, a.project_id, a.name, a.status, a.current_revision,
                       a.metadata_revision, a.owner, a.license, a.provenance,
                       a.review_status, a.expires_on, a.folder, a.tags_json,
                       a.favorite,
                       a.error, a.created_at, a.updated_at,
                       r.digest_sha256, r.size_bytes, r.media_type,
                       r.original_filename, r.scan_status, r.metadata_json
                FROM assets AS a
                LEFT JOIN asset_revisions AS r
                  ON r.asset_id = a.id AND r.revision = a.current_revision
                WHERE a.project_id = ?
                ORDER BY a.updated_at DESC, a.id
                """,
                (project_id,),
            ).fetchall()
        draft = self.repository.get_revision(project_id).draft
        return tuple(self._with_diagnostics(_stored_asset(row), draft=draft) for row in rows)

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
                SELECT a.metadata_revision, p.archived_at
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
        if expected is not None and str(expected) != digest:
            error = "uploaded content SHA-256 does not match expected digest"
            connection.execute(
                "UPDATE asset_uploads SET state = 'failed', error = ? WHERE id = ?",
                (error, upload_id),
            )
            connection.execute(
                "UPDATE assets SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (error, occurred_at, str(row["asset_id"])),
            )
            temp_path.unlink(missing_ok=True)
            return
        object_path = self.object_root / digest[:2] / digest
        object_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if object_path.exists():
            if object_path.stat().st_size != int(row["total_bytes"]):
                raise AssetUploadError("existing content-addressed object size is inconsistent")
            temp_path.unlink()
        else:
            os.replace(temp_path, object_path)
            object_path.chmod(0o600)
        metadata = {
            "status": "unavailable",
            "reason": "media processing has not completed",
        }
        connection.execute(
            """
            INSERT INTO asset_revisions(
                asset_id, revision, digest_sha256, size_bytes, media_type,
                original_filename, scan_status, metadata_json, created_at
            ) VALUES (?, 1, ?, ?, ?, ?, 'unavailable', ?, ?)
            """,
            (
                str(row["asset_id"]),
                digest,
                int(row["total_bytes"]),
                str(row["media_type"]),
                str(row["original_filename"]),
                json.dumps(metadata, separators=(",", ":")),
                occurred_at,
            ),
        )
        connection.execute(
            """
            UPDATE assets
            SET status = 'uploading', current_revision = 1, error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (occurred_at, str(row["asset_id"])),
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
        asset = self.get_asset(asset_id)
        if asset.digest_sha256 is None:
            raise AssetUploadError("asset has no finalized content")
        object_path = self.object_root / asset.digest_sha256[:2] / asset.digest_sha256
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
                digest=asset.digest_sha256,
                media_type=asset.media_type,
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
                status="failed",
                scan_status=scan_status,
                metadata={"status": "failed"},
                error=f"media metadata extraction failed: {error}",
                occurred_at=occurred_at,
            )
            return
        self._finish_processing(
            asset_id=asset_id,
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
                SELECT id
                FROM assets
                WHERE status = 'uploading' AND current_revision IS NOT NULL
                ORDER BY created_at, id
                """
            ).fetchall()
        asset_ids = tuple(str(row["id"]) for row in rows)
        for asset_id in asset_ids:
            self.process_asset(asset_id, occurred_at=_now_iso())
        return asset_ids

    def _finish_processing(
        self,
        *,
        asset_id: str,
        status: str,
        scan_status: str,
        metadata: dict[str, object],
        error: str | None,
        occurred_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE asset_revisions
                SET scan_status = ?, metadata_json = ?
                WHERE asset_id = ?
                  AND revision = (
                      SELECT current_revision FROM assets WHERE id = ?
                  )
                """,
                (
                    scan_status,
                    json.dumps(metadata, separators=(",", ":")),
                    asset_id,
                    asset_id,
                ),
            )
            if cursor.rowcount == 0:
                raise AssetUploadError("asset revision does not exist")
            connection.execute(
                """
                UPDATE assets
                SET status = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error, occurred_at, asset_id),
            )
            connection.commit()
        self.repository.append_audit(
            actor="system:asset_processor",
            action="asset.process",
            target=asset_id,
            result=status,
            occurred_at=occurred_at,
            authorization_context={
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
        return replace(
            asset,
            original_file_status=original_file_status,
            artifact_faults=tuple(artifact_faults),
            references=references,
            unused=not references,
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
    )
