import json
import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from cueverge.gui.contracts import Role
from cueverge.gui.repository import GuiRepository


class ProjectorOverlapError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredProjectorOverlap:
    id: str
    installation_id: str
    first_geometry_id: str
    second_geometry_id: str
    stage_points: tuple[tuple[float, float], ...]
    ramp_width: float
    overlap_gamma: float
    first_brightness: float
    second_brightness: float
    first_white: tuple[float, float, float]
    second_white: tuple[float, float, float]
    first_color_matrix: tuple[float, ...]
    second_color_matrix: tuple[float, ...]
    first_black_level: float
    second_black_level: float
    diagnostics: tuple[str, ...]
    revision: int
    created_at: str
    updated_at: str


class ProjectorOverlapService:
    def __init__(self, repository: GuiRepository) -> None:
        self.repository = repository

    def list(self, installation_id: str) -> tuple[StoredProjectorOverlap, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM projector_overlap_calibrations
                WHERE installation_id = ?
                ORDER BY created_at, id
                """,
                (installation_id,),
            ).fetchall()
        return tuple(_stored_overlap(row) for row in rows)

    def upsert(
        self,
        *,
        overlap_id: str | None,
        expected_revision: int | None,
        installation_id: str,
        first_geometry_id: str,
        second_geometry_id: str,
        stage_points: tuple[tuple[float, float], ...],
        ramp_width: float,
        overlap_gamma: float,
        first_brightness: float,
        second_brightness: float,
        first_white: tuple[float, float, float],
        second_white: tuple[float, float, float],
        first_color_matrix: tuple[float, ...],
        second_color_matrix: tuple[float, ...],
        first_black_level: float,
        second_black_level: float,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredProjectorOverlap:
        if first_geometry_id == second_geometry_id:
            raise ProjectorOverlapError("overlap requires two projector surfaces")
        if len(stage_points) < 3:
            raise ProjectorOverlapError("overlap requires a stage-space polygon")
        if len(first_color_matrix) != 9 or len(second_color_matrix) != 9:
            raise ProjectorOverlapError("color correction matrices require nine values")
        overlap_id = overlap_id or str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            surfaces = connection.execute(
                """
                SELECT g.id
                FROM installation_geometry AS g
                JOIN projector_surface_mappings AS m ON m.geometry_id = g.id
                WHERE g.installation_id = ? AND g.id IN (?, ?)
                """,
                (installation_id, first_geometry_id, second_geometry_id),
            ).fetchall()
            if len(surfaces) != 2:
                raise ProjectorOverlapError(
                    "both overlap surfaces must be mapped projectors in the installation"
                )
            row = connection.execute(
                "SELECT revision FROM projector_overlap_calibrations WHERE id = ?",
                (overlap_id,),
            ).fetchone()
            values = (
                installation_id,
                first_geometry_id,
                second_geometry_id,
                json.dumps(stage_points, separators=(",", ":")),
                ramp_width,
                overlap_gamma,
                first_brightness,
                second_brightness,
                json.dumps(first_white, separators=(",", ":")),
                json.dumps(second_white, separators=(",", ":")),
                json.dumps(first_color_matrix, separators=(",", ":")),
                json.dumps(second_color_matrix, separators=(",", ":")),
                first_black_level,
                second_black_level,
            )
            if row is None:
                if expected_revision is not None:
                    raise ProjectorOverlapError("overlap revision conflict")
                revision = 1
                connection.execute(
                    """
                    INSERT INTO projector_overlap_calibrations(
                        id, installation_id, first_geometry_id, second_geometry_id,
                        stage_points_json, ramp_width, overlap_gamma,
                        first_brightness, second_brightness,
                        first_white_json, second_white_json,
                        first_color_matrix_json, second_color_matrix_json,
                        first_black_level, second_black_level,
                        revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (overlap_id, *values, occurred_at, occurred_at),
                )
            else:
                current = int(row["revision"])
                if expected_revision != current:
                    raise ProjectorOverlapError("overlap revision conflict")
                revision = current + 1
                connection.execute(
                    """
                    UPDATE projector_overlap_calibrations
                    SET installation_id = ?, first_geometry_id = ?,
                        second_geometry_id = ?, stage_points_json = ?,
                        ramp_width = ?, overlap_gamma = ?,
                        first_brightness = ?, second_brightness = ?,
                        first_white_json = ?, second_white_json = ?,
                        first_color_matrix_json = ?, second_color_matrix_json = ?,
                        first_black_level = ?, second_black_level = ?,
                        revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (*values, revision, occurred_at, overlap_id),
                )
            connection.execute(
                """
                INSERT INTO audit_events(
                    actor, action, target, result, occurred_at, authorization_context_json
                ) VALUES (?, 'installation.projector_overlap.upsert', ?, 'success', ?, ?)
                """,
                (
                    actor,
                    overlap_id,
                    occurred_at,
                    json.dumps(
                        {"role": role.value, "revision": revision},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.commit()
        return next(item for item in self.list(installation_id) if item.id == overlap_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.repository.database_path,
            isolation_level=None,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection


def _tuple(row: sqlite3.Row, key: str) -> tuple[float, ...]:
    value = json.loads(str(row[key]))
    if not isinstance(value, list):
        raise ProjectorOverlapError(f"{key} is invalid")
    return tuple(float(item) for item in value)


def _triple(row: sqlite3.Row, key: str) -> tuple[float, float, float]:
    value = _tuple(row, key)
    if len(value) != 3:
        raise ProjectorOverlapError(f"{key} requires three values")
    return value


def _stored_overlap(row: sqlite3.Row) -> StoredProjectorOverlap:
    points_value = json.loads(str(row["stage_points_json"]))
    return StoredProjectorOverlap(
        id=str(row["id"]),
        installation_id=str(row["installation_id"]),
        first_geometry_id=str(row["first_geometry_id"]),
        second_geometry_id=str(row["second_geometry_id"]),
        stage_points=tuple((float(point[0]), float(point[1])) for point in points_value),
        ramp_width=float(row["ramp_width"]),
        overlap_gamma=float(row["overlap_gamma"]),
        first_brightness=float(row["first_brightness"]),
        second_brightness=float(row["second_brightness"]),
        first_white=_triple(row, "first_white_json"),
        second_white=_triple(row, "second_white_json"),
        first_color_matrix=_tuple(row, "first_color_matrix_json"),
        second_color_matrix=_tuple(row, "second_color_matrix_json"),
        first_black_level=float(row["first_black_level"]),
        second_black_level=float(row["second_black_level"]),
        diagnostics=_diagnostics(
            ramp_width=float(row["ramp_width"]),
            overlap_gamma=float(row["overlap_gamma"]),
            first_brightness=float(row["first_brightness"]),
            second_brightness=float(row["second_brightness"]),
            first_white=_triple(row, "first_white_json"),
            second_white=_triple(row, "second_white_json"),
            first_color_matrix=_tuple(row, "first_color_matrix_json"),
            second_color_matrix=_tuple(row, "second_color_matrix_json"),
            first_black_level=float(row["first_black_level"]),
            second_black_level=float(row["second_black_level"]),
        ),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _diagnostics(
    *,
    ramp_width: float,
    overlap_gamma: float,
    first_brightness: float,
    second_brightness: float,
    first_white: tuple[float, float, float],
    second_white: tuple[float, float, float],
    first_color_matrix: tuple[float, ...],
    second_color_matrix: tuple[float, ...],
    first_black_level: float,
    second_black_level: float,
) -> tuple[str, ...]:
    diagnostics: list[str] = []
    if ramp_width < 0.05:
        diagnostics.append("Blend ramp is narrow; a visible hard seam is likely.")
    if first_brightness + second_brightness > 1.1:
        diagnostics.append("Combined overlap brightness may appear double-bright.")
    if abs(overlap_gamma - 1) > 0.15:
        diagnostics.append("Overlap gamma introduces non-linear blend falloff.")
    if abs(first_black_level - second_black_level) > 0.01:
        diagnostics.append("Projector black levels do not match.")
    if max(abs(left - right) for left, right in zip(first_white, second_white, strict=True)) > 0.03:
        diagnostics.append("Projector white balance differs visibly.")
    if (
        max(
            abs(left - right)
            for left, right in zip(first_color_matrix, second_color_matrix, strict=True)
        )
        > 0.05
    ):
        diagnostics.append("Projector color-correction matrices differ visibly.")
    return tuple(diagnostics)
