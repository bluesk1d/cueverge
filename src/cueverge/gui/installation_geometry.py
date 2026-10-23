import json
import sqlite3
from dataclasses import dataclass
from typing import Literal, cast
from uuid import uuid4

from cueverge.gui.contracts import Role
from cueverge.gui.repository import GuiRepository

GeometryKind = Literal[
    "output",
    "pixel_segment",
    "fixture",
    "path",
    "projector_surface",
    "mask",
    "audience_zone",
    "safety_zone",
]


class InstallationGeometryError(RuntimeError):
    pass


class InstallationGeometryConflictError(InstallationGeometryError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("installation geometry revision conflict")
        self.current_revision = current_revision


@dataclass(frozen=True)
class StoredInstallationGeometry:
    id: str
    installation_id: str
    photo_id: str | None
    kind: GeometryKind
    name: str
    semantic_id: str
    landmark_ids: tuple[str, ...]
    color: str
    label_visible: bool
    locked: bool
    hidden: bool
    z_index: int
    direction: Literal["forward", "reverse"]
    start_index: int | None
    pixel_count: int | None
    stage_points: tuple[tuple[float, float], ...] | None
    points: tuple[tuple[float, float], ...]
    closed: bool
    revision: int
    created_at: str
    updated_at: str


class InstallationGeometryService:
    def __init__(self, repository: GuiRepository) -> None:
        self.repository = repository

    def create(
        self,
        *,
        installation_id: str,
        photo_id: str | None,
        kind: GeometryKind,
        name: str,
        points: tuple[tuple[float, float], ...],
        closed: bool,
        semantic_id: str | None,
        color: str,
        label_visible: bool,
        locked: bool,
        hidden: bool,
        z_index: int,
        direction: Literal["forward", "reverse"],
        start_index: int | None,
        pixel_count: int | None,
        stage_points: tuple[tuple[float, float], ...] | None,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredInstallationGeometry:
        geometry_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            photo = connection.execute(
                """
                SELECT 1 FROM installation_photos
                WHERE id = ? AND installation_id = ?
                """,
                (photo_id, installation_id),
            ).fetchone()
            installation = connection.execute(
                "SELECT 1 FROM installations WHERE id=?",
                (installation_id,),
            ).fetchone()
            if installation is None:
                raise InstallationGeometryError("installation does not exist")
            if photo_id is not None and photo is None:
                raise InstallationGeometryError("installation photo does not exist")
            if semantic_id is None:
                semantic_id = str(uuid4())
                landmark_ids = tuple(str(uuid4()) for _ in points)
            else:
                linked = connection.execute(
                    """
                    SELECT landmark_ids_json
                    FROM installation_geometry
                    WHERE installation_id = ? AND kind = ? AND semantic_id = ?
                    LIMIT 1
                    """,
                    (installation_id, kind, semantic_id),
                ).fetchone()
                if linked is None:
                    raise InstallationGeometryError(
                        "linked semantic geometry does not exist for this installation and kind"
                    )
                landmark_ids = tuple(json.loads(str(linked["landmark_ids_json"])))
                if len(landmark_ids) != len(points):
                    raise InstallationGeometryError(
                        "linked geometry instances must use the same landmark count"
                    )
            connection.execute(
                """
                INSERT INTO installation_geometry(
                    id, installation_id, photo_id, kind, name, semantic_id,
                    landmark_ids_json, color, label_visible, locked, hidden, z_index,
                    direction, start_index, pixel_count, points_json, closed,
                    stage_points_json, created_at, updated_at, coordinate_space_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    geometry_id,
                    installation_id,
                    photo_id,
                    kind,
                    name,
                    semantic_id,
                    json.dumps(landmark_ids, separators=(",", ":")),
                    color,
                    int(label_visible),
                    int(locked),
                    int(hidden),
                    z_index,
                    direction,
                    start_index,
                    pixel_count,
                    _points_json(points),
                    int(closed),
                    (
                        json.dumps(stage_points, separators=(",", ":"))
                        if stage_points is not None
                        else None
                    ),
                    occurred_at,
                    occurred_at,
                    photo_id if photo_id is not None else installation_id,
                ),
            )
            _audit(
                connection,
                actor,
                "installation.geometry.create",
                geometry_id,
                role,
                occurred_at,
                {"installation_id": installation_id, "kind": kind},
            )
            connection.commit()
        return self.get(geometry_id)

    def list(
        self, installation_id: str, photo_id: str | None = None
    ) -> tuple[StoredInstallationGeometry, ...]:
        query = "SELECT * FROM installation_geometry WHERE installation_id = ?"
        values: tuple[object, ...] = (installation_id,)
        if photo_id is not None:
            query += " AND photo_id = ?"
            values += (photo_id,)
        query += " ORDER BY kind, name, id"
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return tuple(_stored_geometry(row) for row in rows)

    def get(self, geometry_id: str) -> StoredInstallationGeometry:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM installation_geometry WHERE id = ?",
                (geometry_id,),
            ).fetchone()
        if row is None:
            raise InstallationGeometryError("installation geometry does not exist")
        return _stored_geometry(row)

    def update(
        self,
        *,
        geometry_id: str,
        expected_revision: int,
        photo_id: str | None,
        kind: GeometryKind,
        name: str,
        points: tuple[tuple[float, float], ...],
        closed: bool,
        color: str,
        label_visible: bool,
        locked: bool,
        hidden: bool,
        z_index: int,
        direction: Literal["forward", "reverse"],
        start_index: int | None,
        pixel_count: int | None,
        stage_points: tuple[tuple[float, float], ...] | None,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredInstallationGeometry:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT revision, installation_id, semantic_id, landmark_ids_json
                FROM installation_geometry WHERE id = ?
                """,
                (geometry_id,),
            ).fetchone()
            if row is None:
                raise InstallationGeometryError("installation geometry does not exist")
            current_revision = int(row["revision"])
            if current_revision != expected_revision:
                raise InstallationGeometryConflictError(current_revision)
            photo = connection.execute(
                """
                SELECT 1 FROM installation_photos
                WHERE id = ? AND installation_id = ?
                """,
                (photo_id, str(row["installation_id"])),
            ).fetchone()
            if photo_id is not None and photo is None:
                raise InstallationGeometryError(
                    "target photo does not belong to the geometry installation"
                )
            next_revision = current_revision + 1
            landmark_ids = list(json.loads(str(row["landmark_ids_json"])))
            landmark_ids = landmark_ids[: len(points)]
            landmark_ids.extend(str(uuid4()) for _ in range(len(points) - len(landmark_ids)))
            connection.execute(
                """
                UPDATE installation_geometry
                SET photo_id = ?, kind = ?, name = ?, landmark_ids_json = ?,
                    color = ?, label_visible = ?, locked = ?, hidden = ?, z_index = ?,
                    direction = ?, start_index = ?, pixel_count = ?,
                    points_json = ?, closed = ?, stage_points_json = ?,
                    coordinate_space_id = CASE WHEN ? IS NULL THEN coordinate_space_id ELSE ? END,
                    revision = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    photo_id,
                    kind,
                    name,
                    json.dumps(landmark_ids, separators=(",", ":")),
                    color,
                    int(label_visible),
                    int(locked),
                    int(hidden),
                    z_index,
                    direction,
                    start_index,
                    pixel_count,
                    _points_json(points),
                    int(closed),
                    (
                        json.dumps(stage_points, separators=(",", ":"))
                        if stage_points is not None
                        else None
                    ),
                    photo_id,
                    photo_id,
                    next_revision,
                    occurred_at,
                    geometry_id,
                ),
            )
            connection.execute(
                """
                UPDATE installation_geometry
                SET name = ?, color = ?, label_visible = ?, locked = ?,
                    hidden = ?, z_index = ?, direction = ?, start_index = ?,
                    pixel_count = ?, stage_points_json = ?,
                    revision = revision + 1, updated_at = ?
                WHERE installation_id = ? AND semantic_id = ? AND id != ?
                """,
                (
                    name,
                    color,
                    int(label_visible),
                    int(locked),
                    int(hidden),
                    z_index,
                    direction,
                    start_index,
                    pixel_count,
                    (
                        json.dumps(stage_points, separators=(",", ":"))
                        if stage_points is not None
                        else None
                    ),
                    occurred_at,
                    str(row["installation_id"]),
                    str(row["semantic_id"]),
                    geometry_id,
                ),
            )
            _audit(
                connection,
                actor,
                "installation.geometry.update",
                geometry_id,
                role,
                occurred_at,
                {"previous_revision": current_revision, "created_revision": next_revision},
            )
            connection.commit()
        return self.get(geometry_id)

    def delete(
        self,
        *,
        geometry_id: str,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM installation_geometry WHERE id = ?",
                (geometry_id,),
            ).fetchone()
            if row is None:
                raise InstallationGeometryError("installation geometry does not exist")
            current_revision = int(row["revision"])
            if current_revision != expected_revision:
                raise InstallationGeometryConflictError(current_revision)
            connection.execute("DELETE FROM installation_geometry WHERE id = ?", (geometry_id,))
            _audit(
                connection,
                actor,
                "installation.geometry.delete",
                geometry_id,
                role,
                occurred_at,
                {"deleted_revision": current_revision},
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


def _points_json(points: tuple[tuple[float, float], ...]) -> str:
    return json.dumps(points, separators=(",", ":"))


def _audit(
    connection: sqlite3.Connection,
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


def _stored_geometry(row: sqlite3.Row) -> StoredInstallationGeometry:
    kind_value = str(row["kind"])
    kinds = {
        "output",
        "pixel_segment",
        "fixture",
        "path",
        "projector_surface",
        "mask",
        "audience_zone",
        "safety_zone",
    }
    if kind_value not in kinds:
        raise InstallationGeometryError("installation geometry kind is invalid")
    raw_points = json.loads(str(row["points_json"]))
    if not isinstance(raw_points, list):
        raise InstallationGeometryError("installation geometry points are invalid")
    points = tuple((float(point[0]), float(point[1])) for point in raw_points)
    raw_landmarks = json.loads(str(row["landmark_ids_json"]))
    if not isinstance(raw_landmarks, list) or not all(
        isinstance(landmark, str) for landmark in raw_landmarks
    ):
        raise InstallationGeometryError("installation geometry landmarks are invalid")
    direction_value = str(row["direction"])
    if direction_value not in {"forward", "reverse"}:
        raise InstallationGeometryError("installation geometry direction is invalid")
    stage_value = (
        json.loads(str(row["stage_points_json"])) if row["stage_points_json"] is not None else None
    )
    return StoredInstallationGeometry(
        id=str(row["id"]),
        installation_id=str(row["installation_id"]),
        photo_id=str(row["photo_id"]) if row["photo_id"] is not None else None,
        kind=cast(GeometryKind, kind_value),
        name=str(row["name"]),
        semantic_id=str(row["semantic_id"]),
        landmark_ids=tuple(raw_landmarks),
        color=str(row["color"]),
        label_visible=bool(row["label_visible"]),
        locked=bool(row["locked"]),
        hidden=bool(row["hidden"]),
        z_index=int(row["z_index"]),
        direction=cast(Literal["forward", "reverse"], direction_value),
        start_index=(int(row["start_index"]) if row["start_index"] is not None else None),
        pixel_count=(int(row["pixel_count"]) if row["pixel_count"] is not None else None),
        stage_points=(
            tuple((float(point[0]), float(point[1])) for point in stage_value)
            if stage_value is not None
            else None
        ),
        points=points,
        closed=bool(row["closed"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
