"""Installation stage reference: derive shared-stage placement from one photo view.

The reference view's normalized layout coordinates are scaled into stage units.
Derived values are written as ordinary stage data (geometry stage points,
lighting-surface stage transforms and projector stage homographies), so they
survive photo removal and remain explicit, revisioned installation state.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Literal, cast

from cueverge.gui.contracts import (
    Role,
    StageMapItemResponse,
    StageMapResponse,
    StageProjectorOverlapResponse,
    StageReferenceResponse,
)
from cueverge.gui.projector_mapping import ProjectorMappingError, _homography
from cueverge.gui.repository import GuiRepository
from cueverge.gui.stage_geometry import (
    Affine,
    Matrix3,
    Point,
    Polygon,
    bbox_affine,
    bounds,
    canvas_to_stage,
    fit_affine,
    homography_footprint,
    overlap_fraction,
    stage_homography,
    stage_size,
    units_from_measurement,
)

StageMapStatus = Literal["placed", "needs_update", "manual", "unplaced"]
UnitLabel = Literal["ft", "m", "in", "cm", "units"]
DEFAULT_UNITS_PER_CANVAS = 0.01


class StageReferenceError(RuntimeError):
    pass


class StageReferenceConflictError(StageReferenceError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("stage reference revision conflict")
        self.current_revision = current_revision


@dataclass(frozen=True)
class StoredStageReference:
    installation_id: str
    photo_id: str | None
    units_per_canvas: float
    unit_label: UnitLabel
    scale_points: tuple[Point, Point] | None
    scale_distance: float | None
    revision: int
    applied_at: str | None
    created_at: str
    updated_at: str

    @property
    def stage_width(self) -> float:
        return stage_size(self.units_per_canvas)[0]

    @property
    def stage_height(self) -> float:
        return stage_size(self.units_per_canvas)[1]


@dataclass(frozen=True)
class StageMapItem:
    semantic_id: str
    name: str
    kind: str
    status: StageMapStatus
    reference_geometry_id: str | None
    resource: str | None
    stage_points: tuple[Point, ...] | None
    message: str | None = None


@dataclass(frozen=True)
class ProjectorOverlapSummary:
    resources: tuple[str, str]
    fraction: float


@dataclass(frozen=True)
class StageMap:
    installation_id: str
    reference: StoredStageReference | None
    items: tuple[StageMapItem, ...]
    projector_overlaps: tuple[ProjectorOverlapSummary, ...]

    @property
    def counts(self) -> dict[str, int]:
        result = {"placed": 0, "needs_update": 0, "manual": 0, "unplaced": 0}
        for item in self.items:
            result[item.status] += 1
        return result


@dataclass
class _Plan:
    item: StageMapItem
    geometry_ids: tuple[str, ...]
    stage_points: tuple[Point, ...] | None = None
    surface_geometry_id: str | None = None
    surface_matrix: Affine | None = None
    mapping_geometry_id: str | None = None
    mapping_homography: Matrix3 | None = None
    mapping_mesh: list[list[float]] | None = None
    mapping_extent: tuple[float, float, float, float] | None = None
    changes: list[str] = field(default_factory=list)


class StageReferenceService:
    def __init__(self, repository: GuiRepository) -> None:
        self.repository = repository

    def get(self, installation_id: str) -> StoredStageReference | None:
        with self._connect() as connection:
            return _reference(connection, installation_id)

    def update(
        self,
        *,
        installation_id: str,
        expected_revision: int | None,
        photo_id: str,
        unit_label: UnitLabel,
        stage_width: float | None,
        scale_points: tuple[Point, Point] | None,
        scale_distance: float | None,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredStageReference:
        if stage_width is not None and (scale_points is not None or scale_distance is not None):
            raise StageReferenceError("choose a view width or a measured distance, not both")
        try:
            if stage_width is not None:
                if stage_width <= 0:
                    raise ValueError("stage width must be positive")
                units = stage_width / stage_size(1.0)[0]
            elif scale_points is not None and scale_distance is not None:
                units = units_from_measurement(scale_points[0], scale_points[1], scale_distance)
            elif scale_points is None and scale_distance is None:
                units = DEFAULT_UNITS_PER_CANVAS
            else:
                raise ValueError("a measured scale needs two points and a distance")
        except ValueError as error:
            raise StageReferenceError(str(error)) from error
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute(
                    "SELECT 1 FROM installations WHERE id = ?", (installation_id,)
                ).fetchone()
                is None
            ):
                raise StageReferenceError("installation does not exist")
            if (
                connection.execute(
                    "SELECT 1 FROM installation_photos WHERE id = ? AND installation_id = ?",
                    (photo_id, installation_id),
                ).fetchone()
                is None
            ):
                raise StageReferenceError("stage reference photo is not in this installation")
            current = _reference(connection, installation_id)
            if current is None:
                if expected_revision is not None:
                    raise StageReferenceConflictError(0)
                connection.execute(
                    """
                    INSERT INTO installation_stage_references(
                        installation_id, photo_id, units_per_canvas, unit_label,
                        scale_points_json, scale_distance, revision, applied_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
                    """,
                    (
                        installation_id,
                        photo_id,
                        units,
                        unit_label,
                        _json(scale_points) if scale_points is not None else None,
                        scale_distance,
                        occurred_at,
                        occurred_at,
                    ),
                )
                revision = 1
            else:
                if expected_revision != current.revision:
                    raise StageReferenceConflictError(current.revision)
                revision = current.revision + 1
                connection.execute(
                    """
                    UPDATE installation_stage_references
                    SET photo_id = ?, units_per_canvas = ?, unit_label = ?,
                        scale_points_json = ?, scale_distance = ?, revision = ?,
                        updated_at = ?
                    WHERE installation_id = ?
                    """,
                    (
                        photo_id,
                        units,
                        unit_label,
                        _json(scale_points) if scale_points is not None else None,
                        scale_distance,
                        revision,
                        occurred_at,
                        installation_id,
                    ),
                )
            _audit(
                connection,
                actor,
                "installation.stage_reference.update",
                installation_id,
                role,
                occurred_at,
                {"revision": revision, "photo_id": photo_id},
            )
            connection.commit()
            stored = _reference(connection, installation_id)
        assert stored is not None
        return stored

    def status(self, installation_id: str) -> StageMap:
        with self._connect() as connection:
            reference = _reference(connection, installation_id)
            plans = _plan(connection, installation_id, reference)
            overlaps = _projector_overlaps(connection, installation_id)
        return StageMap(
            installation_id=installation_id,
            reference=reference,
            items=tuple(plan.item for plan in plans),
            projector_overlaps=overlaps,
        )

    def apply(
        self,
        *,
        installation_id: str,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StageMap:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reference = _reference(connection, installation_id)
            if reference is None or reference.photo_id is None:
                raise StageReferenceError("choose a stage reference photo before updating")
            if reference.revision != expected_revision:
                raise StageReferenceConflictError(reference.revision)
            updated = 0
            for plan in _plan(connection, installation_id, reference):
                if plan.item.status != "needs_update":
                    continue
                updated += 1
                if "stage_points" in plan.changes:
                    for geometry_id in plan.geometry_ids:
                        connection.execute(
                            """
                            UPDATE installation_geometry
                            SET stage_points_json = ?, revision = revision + 1,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (_json(plan.stage_points), occurred_at, geometry_id),
                        )
                if "surface" in plan.changes:
                    connection.execute(
                        """
                        UPDATE lighting_surfaces
                        SET stage_matrix_json = ?, revision = revision + 1, updated_at = ?
                        WHERE geometry_id = ?
                        """,
                        (_json(plan.surface_matrix), occurred_at, plan.surface_geometry_id),
                    )
                if "projector" in plan.changes:
                    assert plan.mapping_extent is not None
                    x, y, width, height = plan.mapping_extent
                    if plan.mapping_mesh is not None:
                        connection.execute(
                            """
                            UPDATE projector_surface_mappings
                            SET mesh_vertices_json = ?, stage_x = ?, stage_y = ?,
                                stage_width = ?, stage_height = ?,
                                revision = revision + 1, updated_at = ?
                            WHERE geometry_id = ?
                            """,
                            (
                                _json(plan.mapping_mesh),
                                x,
                                y,
                                width,
                                height,
                                occurred_at,
                                plan.mapping_geometry_id,
                            ),
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE projector_surface_mappings
                            SET stage_homography_json = ?, stage_x = ?, stage_y = ?,
                                stage_width = ?, stage_height = ?,
                                revision = revision + 1, updated_at = ?
                            WHERE geometry_id = ?
                            """,
                            (
                                _json(plan.mapping_homography),
                                x,
                                y,
                                width,
                                height,
                                occurred_at,
                                plan.mapping_geometry_id,
                            ),
                        )
            connection.execute(
                "UPDATE installation_stage_references SET applied_at = ? WHERE installation_id = ?",
                (occurred_at, installation_id),
            )
            _audit(
                connection,
                actor,
                "installation.stage_reference.apply",
                installation_id,
                role,
                occurred_at,
                {"revision": reference.revision, "updated_objects": updated},
            )
            connection.commit()
        return self.status(installation_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.repository.database_path,
            isolation_level=None,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def stage_reference_response(reference: StoredStageReference) -> StageReferenceResponse:
    return StageReferenceResponse(
        installation_id=reference.installation_id,
        photo_id=reference.photo_id,
        unit_label=reference.unit_label,
        units_per_canvas=reference.units_per_canvas,
        stage_width=reference.stage_width,
        stage_height=reference.stage_height,
        scale_points=reference.scale_points,
        scale_distance=reference.scale_distance,
        revision=reference.revision,
        applied_at=reference.applied_at,
        created_at=reference.created_at,
        updated_at=reference.updated_at,
    )


def stage_map_response(stage_map: StageMap) -> StageMapResponse:
    counts = stage_map.counts
    return StageMapResponse(
        installation_id=stage_map.installation_id,
        reference=(
            stage_reference_response(stage_map.reference)
            if stage_map.reference is not None
            else None
        ),
        counts=counts,
        needs_update=counts["needs_update"] > 0,
        items=tuple(
            StageMapItemResponse(
                semantic_id=item.semantic_id,
                name=item.name,
                kind=item.kind,
                status=item.status,
                reference_geometry_id=item.reference_geometry_id,
                resource=item.resource,
                stage_points=item.stage_points,
                message=item.message,
            )
            for item in stage_map.items
        ),
        projector_overlaps=tuple(
            StageProjectorOverlapResponse(resources=overlap.resources, fraction=overlap.fraction)
            for overlap in stage_map.projector_overlaps
        ),
    )


def stored_stage_homography(row: sqlite3.Row) -> Matrix3 | None:
    keys = row.keys()
    if "stage_homography_json" not in keys or row["stage_homography_json"] is None:
        return None
    values = json.loads(str(row["stage_homography_json"]))
    if not isinstance(values, list) or len(values) != 9:
        return None
    return cast(Matrix3, tuple(float(value) for value in values))


def projector_footprint(row: sqlite3.Row) -> Polygon:
    matrix = stored_stage_homography(row)
    if matrix is not None and str(row["surface_model"]) == "planar":
        return homography_footprint(matrix)
    x, y = float(row["stage_x"]), float(row["stage_y"])
    width, height = float(row["stage_width"]), float(row["stage_height"])
    return ((x, y), (x + width, y), (x + width, y + height), (x, y + height))


def _reference(connection: sqlite3.Connection, installation_id: str) -> StoredStageReference | None:
    row = connection.execute(
        "SELECT * FROM installation_stage_references WHERE installation_id = ?",
        (installation_id,),
    ).fetchone()
    if row is None:
        return None
    scale_value = (
        json.loads(str(row["scale_points_json"])) if row["scale_points_json"] is not None else None
    )
    return StoredStageReference(
        installation_id=str(row["installation_id"]),
        photo_id=str(row["photo_id"]) if row["photo_id"] is not None else None,
        units_per_canvas=float(row["units_per_canvas"]),
        unit_label=cast(UnitLabel, str(row["unit_label"])),
        scale_points=(
            (
                (float(scale_value[0][0]), float(scale_value[0][1])),
                (float(scale_value[1][0]), float(scale_value[1][1])),
            )
            if scale_value is not None
            else None
        ),
        scale_distance=(
            float(row["scale_distance"]) if row["scale_distance"] is not None else None
        ),
        revision=int(row["revision"]),
        applied_at=str(row["applied_at"]) if row["applied_at"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _plan(
    connection: sqlite3.Connection,
    installation_id: str,
    reference: StoredStageReference | None,
) -> list[_Plan]:
    rows = connection.execute(
        """
        SELECT * FROM installation_geometry
        WHERE installation_id = ?
        ORDER BY kind, name, semantic_id, id
        """,
        (installation_id,),
    ).fetchall()
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault((str(row["kind"]), str(row["semantic_id"])), []).append(row)
    surfaces = {
        str(row["geometry_id"]): row
        for row in connection.execute(
            """
            SELECT s.* FROM lighting_surfaces AS s
            JOIN installation_geometry AS g ON g.id = s.geometry_id
            WHERE g.installation_id = ?
            """,
            (installation_id,),
        ).fetchall()
    }
    mappings = {
        str(row["geometry_id"]): row
        for row in connection.execute(
            """
            SELECT m.* FROM projector_surface_mappings AS m
            JOIN installation_geometry AS g ON g.id = m.geometry_id
            WHERE g.installation_id = ?
            """,
            (installation_id,),
        ).fetchall()
    }
    plans: list[_Plan] = []
    for (kind, semantic_id), members in groups.items():
        geometry_ids = tuple(str(row["id"]) for row in members)
        surface_row = next((surfaces[gid] for gid in geometry_ids if gid in surfaces), None)
        mapping_row = next((mappings[gid] for gid in geometry_ids if gid in mappings), None)
        resource = (
            str(surface_row["resource"])
            if surface_row is not None
            else str(mapping_row["resource"])
            if mapping_row is not None
            else None
        )
        current_points = _points(members[0]["stage_points_json"])
        reference_row = (
            next(
                (row for row in members if row["photo_id"] == reference.photo_id),
                None,
            )
            if reference is not None and reference.photo_id is not None
            else None
        )
        name = str(members[0]["name"])
        if reference is None or reference_row is None:
            has_stage = current_points is not None or mapping_row is not None
            plans.append(
                _Plan(
                    item=StageMapItem(
                        semantic_id=semantic_id,
                        name=name,
                        kind=kind,
                        status="manual" if has_stage else "unplaced",
                        reference_geometry_id=None,
                        resource=resource,
                        stage_points=current_points,
                        message=(
                            "Not drawn on the stage reference view."
                            if reference is not None and reference.photo_id is not None
                            else "No stage reference view is selected."
                        ),
                    ),
                    geometry_ids=geometry_ids,
                )
            )
            continue
        units = reference.units_per_canvas
        layout_points = tuple(
            (float(point[0]), float(point[1]))
            for point in json.loads(str(reference_row["points_json"]))
        )
        derived = tuple(canvas_to_stage(point, units) for point in layout_points)
        plan = _Plan(
            item=StageMapItem(
                semantic_id=semantic_id,
                name=name,
                kind=kind,
                status="placed",
                reference_geometry_id=str(reference_row["id"]),
                resource=resource,
                stage_points=derived,
            ),
            geometry_ids=geometry_ids,
            stage_points=derived,
        )
        messages: list[str] = []
        if not _same_points(current_points, derived):
            plan.changes.append("stage_points")
        if surface_row is not None:
            local = tuple(
                (float(point[0]), float(point[1]))
                for point in json.loads(str(surface_row["local_points_json"]))
            )
            matrix = (
                fit_affine(local, derived)
                if len(local) == len(derived)
                else bbox_affine(local, derived)
            )
            if len(local) != len(derived):
                messages.append("LED layout points differ; placed by bounding box.")
            plan.surface_geometry_id = str(surface_row["geometry_id"])
            plan.surface_matrix = matrix
            stored = tuple(
                float(value) for value in json.loads(str(surface_row["stage_matrix_json"]))
            )
            if not _same_values(stored, matrix):
                plan.changes.append("surface")
        if kind == "projector_surface" and mapping_row is None:
            messages.append("No projector is assigned to this surface yet.")
        if mapping_row is not None:
            plan.mapping_geometry_id = str(mapping_row["geometry_id"])
            if str(mapping_row["surface_model"]) == "planar":
                if len(layout_points) == 4:
                    try:
                        photo = _homography(
                            int(mapping_row["native_width"]),
                            int(mapping_row["native_height"]),
                            layout_points,
                            cast(Literal[0, 90, 180, 270], int(mapping_row["orientation_degrees"])),
                            bool(mapping_row["mirror_x"]),
                            bool(mapping_row["mirror_y"]),
                        )
                        homography = stage_homography(
                            photo,
                            int(mapping_row["native_width"]),
                            int(mapping_row["native_height"]),
                            units,
                        )
                    except (ProjectorMappingError, ValueError) as error:
                        messages.append(f"Projector corners cannot be placed: {error}")
                    else:
                        plan.mapping_homography = homography
                        plan.mapping_extent = _extent(homography_footprint(homography))
                        if not _same_values(
                            stored_stage_homography(mapping_row), homography
                        ) or not _same_values(_row_extent(mapping_row), plan.mapping_extent):
                            plan.changes.append("projector")
            else:
                mesh = json.loads(str(mapping_row["mesh_vertices_json"]))
                if isinstance(mesh, list) and len(mesh) == len(derived):
                    updated_mesh = [
                        [float(vertex[0]), float(vertex[1]), point[0], point[1]]
                        for vertex, point in zip(mesh, derived, strict=True)
                    ]
                    plan.mapping_mesh = updated_mesh
                    plan.mapping_extent = _extent(derived)
                    if not _same_values(
                        tuple(value for vertex in mesh for value in vertex),
                        tuple(value for vertex in updated_mesh for value in vertex),
                    ) or not _same_values(_row_extent(mapping_row), plan.mapping_extent):
                        plan.changes.append("projector")
                else:
                    messages.append(
                        "Subdivided projector mesh does not match its layout points; "
                        "set its stage mesh manually."
                    )
        plan.item = StageMapItem(
            semantic_id=semantic_id,
            name=name,
            kind=kind,
            status="needs_update" if plan.changes else "placed",
            reference_geometry_id=str(reference_row["id"]),
            resource=resource,
            stage_points=derived,
            message=" ".join(messages) or None,
        )
        plans.append(plan)
    return plans


def _projector_overlaps(
    connection: sqlite3.Connection, installation_id: str
) -> tuple[ProjectorOverlapSummary, ...]:
    rows = connection.execute(
        """
        SELECT m.* FROM projector_surface_mappings AS m
        JOIN installation_geometry AS g ON g.id = m.geometry_id
        WHERE g.installation_id = ?
        ORDER BY m.resource, m.geometry_id
        """,
        (installation_id,),
    ).fetchall()
    footprints = [(str(row["resource"]), projector_footprint(row)) for row in rows]
    result: list[ProjectorOverlapSummary] = []
    for index, (first_resource, first) in enumerate(footprints):
        for second_resource, second in footprints[index + 1 :]:
            fraction = overlap_fraction(first, second)
            if fraction > 1e-6:
                result.append(
                    ProjectorOverlapSummary(
                        resources=(first_resource, second_resource),
                        fraction=round(fraction, 4),
                    )
                )
    return tuple(result)


def _points(value: object) -> tuple[Point, ...] | None:
    if value is None:
        return None
    parsed = json.loads(str(value))
    return tuple((float(point[0]), float(point[1])) for point in parsed)


def _same_points(current: tuple[Point, ...] | None, derived: tuple[Point, ...]) -> bool:
    if current is None or len(current) != len(derived):
        return False
    return _same_values(
        tuple(value for point in current for value in point),
        tuple(value for point in derived for value in point),
    )


def _same_values(
    current: tuple[float, ...] | None,
    derived: tuple[float, ...] | None,
) -> bool:
    if current is None or derived is None or len(current) != len(derived):
        return False
    return all(
        abs(left - right) <= 1e-6 * max(1.0, abs(left), abs(right))
        for left, right in zip(current, derived, strict=True)
    )


def _extent(points: tuple[Point, ...]) -> tuple[float, float, float, float]:
    minimum_x, minimum_y, maximum_x, maximum_y = bounds(points)
    return (
        minimum_x,
        minimum_y,
        max(maximum_x - minimum_x, 1e-6),
        max(maximum_y - minimum_y, 1e-6),
    )


def _row_extent(row: sqlite3.Row) -> tuple[float, float, float, float]:
    return (
        float(row["stage_x"]),
        float(row["stage_y"]),
        float(row["stage_width"]),
        float(row["stage_height"]),
    )


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


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
