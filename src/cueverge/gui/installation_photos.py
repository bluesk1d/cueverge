import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Literal, cast
from uuid import uuid4

from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import Role
from cueverge.gui.repository import GuiRepository, InstallationNotFoundError

ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PHOTO_BYTES = 25_000_000


class InstallationPhotoError(RuntimeError):
    pass


class InstallationPhotoConflictError(InstallationPhotoError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("installation photo revision conflict")
        self.current_revision = current_revision


@dataclass(frozen=True)
class StoredInstallationPhoto:
    id: str
    installation_id: str
    name: str
    viewpoint: str
    original_filename: str
    media_type: str
    digest_sha256: str
    size_bytes: int
    orientation_degrees: Literal[0, 90, 180, 270]
    crop_x: float | None
    crop_y: float | None
    crop_width: float | None
    crop_height: float | None
    scale_hint: float | None
    revision: int
    created_at: str
    updated_at: str


class InstallationPhotoService:
    def __init__(self, config: GuiConfig, repository: GuiRepository) -> None:
        self.repository = repository
        self.object_root = config.data_root / "assets" / "objects"
        self.object_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def create(
        self,
        *,
        installation_id: str,
        name: str,
        viewpoint: str,
        original_filename: str,
        media_type: str,
        content: bytes,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredInstallationPhoto:
        _validate_plain_filename(original_filename)
        if media_type not in ALLOWED_PHOTO_TYPES:
            raise InstallationPhotoError("installation photo media type is not allowed")
        if not content or len(content) > MAX_PHOTO_BYTES:
            raise InstallationPhotoError(
                f"installation photo must be between 1 and {MAX_PHOTO_BYTES} bytes"
            )
        try:
            self.repository.get_installation(installation_id)
        except InstallationNotFoundError as error:
            raise InstallationPhotoError("installation does not exist") from error
        digest = hashlib.sha256(content).hexdigest()
        object_path = self.object_root / digest[:2] / digest
        object_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = object_path.with_name(f".{uuid4().hex}.photo")
        if not object_path.exists():
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as target:
                    target.write(content)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, object_path)
            finally:
                temporary.unlink(missing_ok=True)
        photo_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO installation_photos(
                    id, installation_id, name, viewpoint, original_filename,
                    media_type, digest_sha256, size_bytes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    photo_id,
                    installation_id,
                    name,
                    viewpoint,
                    original_filename,
                    media_type,
                    digest,
                    len(content),
                    occurred_at,
                    occurred_at,
                ),
            )
            _insert_audit(
                connection,
                actor=actor,
                action="installation.photo.create",
                target=photo_id,
                role=role,
                occurred_at=occurred_at,
                context={"installation_id": installation_id, "digest_sha256": digest},
            )
            connection.commit()
        return self.get(photo_id)

    def list(self, installation_id: str) -> tuple[StoredInstallationPhoto, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM installation_photos
                WHERE installation_id = ?
                ORDER BY viewpoint, name, id
                """,
                (installation_id,),
            ).fetchall()
        return tuple(_stored_photo(row) for row in rows)

    def get(self, photo_id: str) -> StoredInstallationPhoto:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM installation_photos WHERE id = ?",
                (photo_id,),
            ).fetchone()
        if row is None:
            raise InstallationPhotoError("installation photo does not exist")
        return _stored_photo(row)

    def content(self, photo_id: str) -> tuple[Path, str]:
        photo = self.get(photo_id)
        path = self.object_root / photo.digest_sha256[:2] / photo.digest_sha256
        if not path.is_file() or path.stat().st_size != photo.size_bytes:
            raise InstallationPhotoError("installation photo content is missing or inconsistent")
        return path, photo.media_type

    def update(
        self,
        *,
        photo_id: str,
        expected_revision: int,
        name: str,
        viewpoint: str,
        orientation_degrees: int,
        crop_x: float | None,
        crop_y: float | None,
        crop_width: float | None,
        crop_height: float | None,
        scale_hint: float | None,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredInstallationPhoto:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM installation_photos WHERE id = ?",
                (photo_id,),
            ).fetchone()
            if row is None:
                raise InstallationPhotoError("installation photo does not exist")
            current_revision = int(row["revision"])
            if current_revision != expected_revision:
                raise InstallationPhotoConflictError(current_revision)
            next_revision = current_revision + 1
            connection.execute(
                """
                UPDATE installation_photos
                SET name = ?, viewpoint = ?, orientation_degrees = ?,
                    crop_x = ?, crop_y = ?, crop_width = ?, crop_height = ?,
                    scale_hint = ?, revision = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    viewpoint,
                    orientation_degrees,
                    crop_x,
                    crop_y,
                    crop_width,
                    crop_height,
                    scale_hint,
                    next_revision,
                    occurred_at,
                    photo_id,
                ),
            )
            _insert_audit(
                connection,
                actor=actor,
                action="installation.photo.update",
                target=photo_id,
                role=role,
                occurred_at=occurred_at,
                context={
                    "previous_revision": current_revision,
                    "created_revision": next_revision,
                },
            )
            connection.commit()
        return self.get(photo_id)

    def describe(
        self,
        *,
        photo_id: str,
        expected_revision: int,
        description: str,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredInstallationPhoto:
        photo = self.get(photo_id)
        return self.update(
            photo_id=photo_id,
            expected_revision=expected_revision,
            name=description,
            viewpoint=photo.viewpoint,
            orientation_degrees=photo.orientation_degrees,
            crop_x=photo.crop_x,
            crop_y=photo.crop_y,
            crop_width=photo.crop_width,
            crop_height=photo.crop_height,
            scale_hint=photo.scale_hint,
            actor=actor,
            role=role,
            occurred_at=occurred_at,
        )

    def delete(
        self,
        *,
        photo_id: str,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM installation_photos WHERE id = ?",
                (photo_id,),
            ).fetchone()
            if row is None:
                raise InstallationPhotoError("installation photo does not exist")
            current_revision = int(row["revision"])
            if current_revision != expected_revision:
                raise InstallationPhotoConflictError(current_revision)
            connection.execute("DELETE FROM installation_photos WHERE id = ?", (photo_id,))
            _insert_audit(
                connection,
                actor=actor,
                action="installation.photo.delete",
                target=photo_id,
                role=role,
                occurred_at=occurred_at,
                context={"deleted_revision": current_revision},
            )
            connection.commit()

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


def _validate_plain_filename(value: str) -> None:
    if (
        not value
        or PurePath(value).name != value
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise InstallationPhotoError("installation photo filename must be plain")


def _insert_audit(
    connection: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    target: str,
    role: Role,
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
            json.dumps({"role": role.value, **context}, sort_keys=True, separators=(",", ":")),
        ),
    )


def _stored_photo(row: sqlite3.Row) -> StoredInstallationPhoto:
    orientation_value = int(row["orientation_degrees"])
    if orientation_value not in {0, 90, 180, 270}:
        raise InstallationPhotoError("installation photo orientation is invalid")
    return StoredInstallationPhoto(
        id=str(row["id"]),
        installation_id=str(row["installation_id"]),
        name=str(row["name"]),
        viewpoint=str(row["viewpoint"]),
        original_filename=str(row["original_filename"]),
        media_type=str(row["media_type"]),
        digest_sha256=str(row["digest_sha256"]),
        size_bytes=int(row["size_bytes"]),
        orientation_degrees=cast(Literal[0, 90, 180, 270], orientation_value),
        crop_x=float(row["crop_x"]) if row["crop_x"] is not None else None,
        crop_y=float(row["crop_y"]) if row["crop_y"] is not None else None,
        crop_width=float(row["crop_width"]) if row["crop_width"] is not None else None,
        crop_height=float(row["crop_height"]) if row["crop_height"] is not None else None,
        scale_hint=float(row["scale_hint"]) if row["scale_hint"] is not None else None,
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
