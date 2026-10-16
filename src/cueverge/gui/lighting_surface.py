import json
import math
import sqlite3
from dataclasses import dataclass
from itertools import pairwise
from typing import cast

from cueverge.gui.contracts import Role
from cueverge.gui.installation_geometry import (
    InstallationGeometryService,
    StoredInstallationGeometry,
)
from cueverge.gui.repository import GuiRepository


class LightingSurfaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class MappedPixel:
    output_index: int
    local_x: float
    local_y: float
    stage_x: float
    stage_y: float
    footprint_width: float
    footprint_height: float


@dataclass(frozen=True)
class PixelPositionOverride:
    output_index: int
    local_x: float
    local_y: float


@dataclass(frozen=True)
class StoredLightingSurface:
    geometry_id: str
    resource: str
    local_width: float
    local_height: float
    local_points: tuple[tuple[float, float], ...]
    stage_matrix: tuple[float, float, float, float, float, float]
    fixture_id: str | None
    group_id: str | None
    path_id: str | None
    strand_id: str | None
    calibration_id: str | None
    pixel_footprint_width: float
    pixel_footprint_height: float
    brightness_limit: float
    timing_offset_ms: int
    release_offset_ms: int
    pixel_overrides: tuple[PixelPositionOverride, ...]
    pixels: tuple[MappedPixel, ...]
    revision: int
    created_at: str
    updated_at: str


class LightingSurfaceService:
    def __init__(
        self,
        repository: GuiRepository,
        geometry: InstallationGeometryService,
    ) -> None:
        self.repository = repository
        self.geometry = geometry

    def get(self, geometry_id: str) -> StoredLightingSurface:
        geometry = self.geometry.get(geometry_id)
        if geometry.kind != "pixel_segment":
            raise LightingSurfaceError("lighting surface requires pixel-segment geometry")
        with self._connect() as connection:
            row = _find_surface_row(connection, geometry)
        if row is None:
            raise LightingSurfaceError("lighting surface does not exist")
        return _stored_surface(row, geometry, geometry_id)

    def upsert(
        self,
        *,
        geometry_id: str,
        expected_revision: int | None,
        resource: str,
        local_width: float,
        local_height: float,
        local_points: tuple[tuple[float, float], ...],
        stage_matrix: tuple[float, float, float, float, float, float],
        fixture_id: str | None,
        group_id: str | None,
        path_id: str | None,
        strand_id: str | None,
        calibration_id: str | None,
        pixel_footprint_width: float,
        pixel_footprint_height: float,
        brightness_limit: float,
        timing_offset_ms: int,
        release_offset_ms: int,
        actor: str,
        role: Role,
        occurred_at: str,
        pixel_overrides: tuple[PixelPositionOverride, ...] = (),
    ) -> StoredLightingSurface:
        geometry = self.geometry.get(geometry_id)
        if (
            geometry.kind != "pixel_segment"
            or geometry.start_index is None
            or geometry.pixel_count is None
        ):
            raise LightingSurfaceError("lighting surface requires indexed pixel-segment geometry")
        if abs(stage_matrix[0] * stage_matrix[3] - stage_matrix[1] * stage_matrix[2]) < 1e-12:
            raise LightingSurfaceError("lighting surface stage transform is singular")
        if len(local_points) < 1:
            raise LightingSurfaceError("lighting surface requires local points")
        if len(local_points) == 1 and geometry.pixel_count != 1:
            raise LightingSurfaceError("one local point is valid only for a one-pixel surface")
        if any(x < 0 or x > local_width or y < 0 or y > local_height for x, y in local_points):
            raise LightingSurfaceError("lighting local points exceed surface bounds")
        valid_indices = set(
            range(geometry.start_index, geometry.start_index + geometry.pixel_count)
        )
        override_indices = [override.output_index for override in pixel_overrides]
        if len(override_indices) != len(set(override_indices)):
            raise LightingSurfaceError("pixel correction output indices must be unique")
        if any(override.output_index not in valid_indices for override in pixel_overrides):
            raise LightingSurfaceError("pixel correction index is outside this segment")
        if any(
            override.local_x < 0
            or override.local_x > local_width
            or override.local_y < 0
            or override.local_y > local_height
            for override in pixel_overrides
        ):
            raise LightingSurfaceError("pixel correction exceeds surface bounds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _find_surface_row(connection, geometry)
            storage_geometry_id = str(row["geometry_id"]) if row is not None else geometry_id
            values = (
                resource,
                local_width,
                local_height,
                json.dumps(local_points, separators=(",", ":")),
                json.dumps(stage_matrix, separators=(",", ":")),
                fixture_id,
                group_id,
                path_id,
                strand_id,
                calibration_id,
                pixel_footprint_width,
                pixel_footprint_height,
                brightness_limit,
                timing_offset_ms,
                release_offset_ms,
                json.dumps(
                    [
                        {
                            "output_index": override.output_index,
                            "local_x": override.local_x,
                            "local_y": override.local_y,
                        }
                        for override in pixel_overrides
                    ],
                    separators=(",", ":"),
                ),
            )
            if row is None:
                if expected_revision is not None:
                    raise LightingSurfaceError("lighting surface revision conflict")
                revision = 1
                connection.execute(
                    """
                    INSERT INTO lighting_surfaces(
                        geometry_id, resource, local_width, local_height,
                        local_points_json, stage_matrix_json,
                        fixture_id, group_id, path_id, strand_id,
                        calibration_id, pixel_footprint_width, pixel_footprint_height,
                        brightness_limit, timing_offset_ms,
                        release_offset_ms, pixel_overrides_json,
                        revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (geometry_id, *values, occurred_at, occurred_at),
                )
            else:
                current = int(row["revision"])
                if expected_revision != current:
                    raise LightingSurfaceError("lighting surface revision conflict")
                revision = current + 1
                connection.execute(
                    """
                    UPDATE lighting_surfaces
                    SET resource = ?, local_width = ?, local_height = ?,
                        local_points_json = ?, stage_matrix_json = ?,
                        fixture_id = ?, group_id = ?,
                        path_id = ?, strand_id = ?, calibration_id = ?,
                        pixel_footprint_width = ?, pixel_footprint_height = ?,
                        brightness_limit = ?, timing_offset_ms = ?,
                        release_offset_ms = ?, pixel_overrides_json = ?,
                        revision = ?, updated_at = ?
                    WHERE geometry_id = ?
                    """,
                    (*values, revision, occurred_at, storage_geometry_id),
                )
            connection.execute(
                """
                INSERT INTO audit_events(
                    actor, action, target, result, occurred_at, authorization_context_json
                ) VALUES (?, 'installation.lighting_surface.upsert', ?, 'success', ?, ?)
                """,
                (
                    actor,
                    geometry_id,
                    occurred_at,
                    json.dumps(
                        {"role": role.value, "revision": revision},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.commit()
        return self.get(geometry_id)

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


def _stored_surface(
    row: sqlite3.Row,
    geometry: StoredInstallationGeometry,
    requested_geometry_id: str,
) -> StoredLightingSurface:
    matrix_value = json.loads(str(row["stage_matrix_json"]))
    matrix = tuple(float(value) for value in matrix_value)
    if len(matrix) != 6:
        raise LightingSurfaceError("lighting surface transform is invalid")
    local_width = float(row["local_width"])
    local_height = float(row["local_height"])
    raw_local_points = json.loads(str(row["local_points_json"]))
    if not isinstance(raw_local_points, list) or len(raw_local_points) < 1:
        raise LightingSurfaceError("lighting surface local points are invalid")
    local_points = tuple((float(point[0]), float(point[1])) for point in raw_local_points)
    raw_overrides = json.loads(str(row["pixel_overrides_json"]))
    if not isinstance(raw_overrides, list):
        raise LightingSurfaceError("pixel corrections are invalid")
    pixel_overrides = tuple(
        PixelPositionOverride(
            output_index=int(override["output_index"]),
            local_x=float(override["local_x"]),
            local_y=float(override["local_y"]),
        )
        for override in raw_overrides
        if isinstance(override, dict)
    )
    if len(pixel_overrides) != len(raw_overrides):
        raise LightingSurfaceError("pixel corrections are invalid")
    if geometry.start_index is None or geometry.pixel_count is None:
        raise LightingSurfaceError("lighting surface geometry has no pixel index range")
    pixels = _map_pixels(
        local_points,
        geometry.start_index,
        geometry.pixel_count,
        geometry.direction,
        matrix,
        float(row["pixel_footprint_width"]),
        float(row["pixel_footprint_height"]),
        pixel_overrides,
    )
    return StoredLightingSurface(
        geometry_id=requested_geometry_id,
        resource=str(row["resource"]),
        local_width=local_width,
        local_height=local_height,
        local_points=local_points,
        stage_matrix=matrix,
        fixture_id=str(row["fixture_id"]) if row["fixture_id"] is not None else None,
        group_id=str(row["group_id"]) if row["group_id"] is not None else None,
        path_id=str(row["path_id"]) if row["path_id"] is not None else None,
        strand_id=str(row["strand_id"]) if row["strand_id"] is not None else None,
        calibration_id=(str(row["calibration_id"]) if row["calibration_id"] is not None else None),
        pixel_footprint_width=float(row["pixel_footprint_width"]),
        pixel_footprint_height=float(row["pixel_footprint_height"]),
        brightness_limit=float(row["brightness_limit"]),
        timing_offset_ms=int(row["timing_offset_ms"]),
        release_offset_ms=int(row["release_offset_ms"]),
        pixel_overrides=pixel_overrides,
        pixels=pixels,
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _find_surface_row(
    connection: sqlite3.Connection,
    geometry: StoredInstallationGeometry,
) -> sqlite3.Row | None:
    row = connection.execute(
        "SELECT * FROM lighting_surfaces WHERE geometry_id = ?",
        (geometry.id,),
    ).fetchone()
    if row is not None:
        return cast(sqlite3.Row, row)
    linked = connection.execute(
        """
        SELECT s.*
        FROM lighting_surfaces AS s
        JOIN installation_geometry AS owner ON owner.id = s.geometry_id
        WHERE owner.installation_id = ? AND owner.semantic_id = ?
          AND owner.kind = 'pixel_segment'
        ORDER BY s.created_at, s.geometry_id
        LIMIT 1
        """,
        (geometry.installation_id, geometry.semantic_id),
    ).fetchone()
    return cast(sqlite3.Row | None, linked)


def _map_pixels(
    points: tuple[tuple[float, float], ...],
    start_index: int,
    pixel_count: int,
    direction: str,
    matrix: tuple[float, ...],
    footprint_width: float,
    footprint_height: float,
    overrides: tuple[PixelPositionOverride, ...],
) -> tuple[MappedPixel, ...]:
    ordered = points if direction == "forward" else tuple(reversed(points))
    if len(ordered) == 1 and pixel_count == 1:
        local_x, local_y = ordered[0]
        a, b, c, d, tx, ty = matrix
        single_pixel = (
            MappedPixel(
                output_index=start_index,
                local_x=local_x,
                local_y=local_y,
                stage_x=a * local_x + c * local_y + tx,
                stage_y=b * local_x + d * local_y + ty,
                footprint_width=footprint_width,
                footprint_height=footprint_height,
            ),
        )
        return _apply_pixel_overrides(single_pixel, overrides, matrix)
    lengths = [
        math.hypot(right[0] - left[0], right[1] - left[1]) for left, right in pairwise(ordered)
    ]
    total = sum(lengths)
    if total <= 0:
        raise LightingSurfaceError("pixel segment has zero length")
    mapped: list[MappedPixel] = []
    closed = ordered[0] == ordered[-1]
    for offset in range(pixel_count):
        denominator = pixel_count if closed else max(pixel_count - 1, 1)
        distance = total * (offset / denominator)
        traversed = 0.0
        for segment_index, length in enumerate(lengths):
            if traversed + length >= distance or segment_index == len(lengths) - 1:
                ratio = (distance - traversed) / length if length else 0
                left = ordered[segment_index]
                right = ordered[segment_index + 1]
                local_x = left[0] + (right[0] - left[0]) * ratio
                local_y = left[1] + (right[1] - left[1]) * ratio
                a, b, c, d, tx, ty = matrix
                mapped.append(
                    MappedPixel(
                        output_index=start_index + offset,
                        local_x=local_x,
                        local_y=local_y,
                        stage_x=a * local_x + c * local_y + tx,
                        stage_y=b * local_x + d * local_y + ty,
                        footprint_width=footprint_width,
                        footprint_height=footprint_height,
                    )
                )
                break
            traversed += length
    return _apply_pixel_overrides(tuple(mapped), overrides, matrix)


def _apply_pixel_overrides(
    pixels: tuple[MappedPixel, ...],
    overrides: tuple[PixelPositionOverride, ...],
    matrix: tuple[float, ...],
) -> tuple[MappedPixel, ...]:
    by_index = {override.output_index: override for override in overrides}
    a, b, c, d, tx, ty = matrix
    return tuple(
        MappedPixel(
            output_index=pixel.output_index,
            local_x=override.local_x,
            local_y=override.local_y,
            stage_x=a * override.local_x + c * override.local_y + tx,
            stage_y=b * override.local_x + d * override.local_y + ty,
            footprint_width=pixel.footprint_width,
            footprint_height=pixel.footprint_height,
        )
        if (override := by_index.get(pixel.output_index)) is not None
        else pixel
        for pixel in pixels
    )
