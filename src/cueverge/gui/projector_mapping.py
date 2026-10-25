import json
import sqlite3
from dataclasses import dataclass, replace
from typing import Literal, cast

from cueverge.gui.contracts import Role
from cueverge.gui.installation_geometry import InstallationGeometryService
from cueverge.gui.repository import GuiRepository
from cueverge.gui.stage_geometry import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_UNITS_PER_CANVAS,
    bounds,
    canvas_to_stage,
    homography_footprint,
    stage_homography,
)

StagePlacement = Literal["surface", "manual"]
StagePlacementBasis = Literal["stage_view", "stage_points", "default_scale"]
DEFAULT_STAGE_EXTENT = (0.0, 0.0, 4.0, 2.25)


class ProjectorMappingError(RuntimeError):
    pass


@dataclass(frozen=True)
class DerivedStagePlacement:
    basis: StagePlacementBasis | None
    extent: tuple[float, float, float, float] | None
    homography: tuple[float, ...] | None
    mesh_vertices: tuple[tuple[float, float, float, float], ...] | None
    message: str | None


class ProjectorMappingConflictError(ProjectorMappingError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("projector mapping revision conflict")
        self.current_revision = current_revision


@dataclass(frozen=True)
class StoredProjectorMapping:
    geometry_id: str
    resource: str
    native_width: int
    native_height: int
    stage_x: float
    stage_y: float
    stage_width: float
    stage_height: float
    crop_x: float
    crop_y: float
    crop_width: float
    crop_height: float
    content_asset_id: str | None
    surface_model: Literal["planar", "subdivided"]
    fit_mode: Literal["preserve", "stretch"]
    overscan: bool
    brightness_limit: float
    media_start_delay_ms: int
    media_release_delay_ms: int
    orientation_degrees: Literal[0, 90, 180, 270]
    mirror_x: bool
    mirror_y: bool
    mask_semantic_ids: tuple[str, ...]
    calibration_source: Literal["measured_grid", "easy_corner_pin", "unverified_estimate"]
    calibration_revision: int
    mesh_vertices: tuple[tuple[float, float, float, float], ...]
    mesh_triangles: tuple[tuple[int, int, int], ...]
    photo_views: tuple["ProjectorPhotoView", ...]
    homography: tuple[float, ...] | None
    diagnostic: str | None
    fit_diagnostics: tuple[str, ...]
    revision: int
    created_at: str
    updated_at: str
    stage_homography: tuple[float, ...] | None = None
    stage_placement: StagePlacement = "surface"
    stage_placement_basis: StagePlacementBasis | None = None
    stage_placement_message: str | None = None


@dataclass(frozen=True)
class ProjectorPhotoView:
    geometry_id: str
    photo_id: str | None
    points: tuple[tuple[float, float], ...]
    homography: tuple[float, ...] | None


class ProjectorMappingService:
    def __init__(
        self,
        repository: GuiRepository,
        geometry: InstallationGeometryService,
    ) -> None:
        self.repository = repository
        self.geometry = geometry

    def get(self, geometry_id: str) -> StoredProjectorMapping:
        geometry = self.geometry.get(geometry_id)
        if geometry.kind != "projector_surface":
            raise ProjectorMappingError("projector mapping requires a projector surface")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projector_surface_mappings WHERE geometry_id = ?",
                (geometry_id,),
            ).fetchone()
            if row is None:
                raise ProjectorMappingError("projector mapping does not exist")
            placement = _row_placement(row)
            derived = (
                derive_stage_placement(
                    connection,
                    installation_id=geometry.installation_id,
                    semantic_id=geometry.semantic_id,
                    points=geometry.points,
                    native_width=int(row["native_width"]),
                    native_height=int(row["native_height"]),
                    orientation_degrees=cast(
                        Literal[0, 90, 180, 270], int(row["orientation_degrees"])
                    ),
                    mirror_x=bool(row["mirror_x"]),
                    mirror_y=bool(row["mirror_y"]),
                    surface_model=cast(Literal["planar", "subdivided"], str(row["surface_model"])),
                    mesh_vertices=_mesh_vertices(row),
                )
                if placement == "surface"
                else None
            )
        stored = _stored_mapping(
            row,
            geometry.points,
            self._photo_views(
                geometry.installation_id,
                geometry.semantic_id,
                int(row["native_width"]),
                int(row["native_height"]),
                cast(Literal[0, 90, 180, 270], int(row["orientation_degrees"])),
                bool(row["mirror_x"]),
                bool(row["mirror_y"]),
                str(row["surface_model"]) == "planar",
            ),
        )
        return replace(
            stored,
            stage_placement_basis=derived.basis if derived is not None else None,
            stage_placement_message=placement_message(
                placement,
                derived,
                (stored.stage_x, stored.stage_y, stored.stage_width, stored.stage_height),
            ),
        )

    def refresh_stage_placement(
        self,
        *,
        installation_id: str,
        semantic_id: str,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> int:
        """Re-derive surface-placed projector mappings after their drawn surface changes."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = refresh_surface_placements(
                connection,
                installation_id=installation_id,
                semantic_id=semantic_id,
                occurred_at=occurred_at,
            )
            for geometry_id in updated:
                connection.execute(
                    """
                    INSERT INTO audit_events(
                        actor, action, target, result, occurred_at,
                        authorization_context_json
                    ) VALUES (
                        ?, 'installation.projector_mapping.stage_placement', ?,
                        'success', ?, ?
                    )
                    """,
                    (
                        actor,
                        geometry_id,
                        occurred_at,
                        json.dumps(
                            {"role": role.value, "stage_placement": "surface"},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
            connection.commit()
        return len(updated)

    def upsert(
        self,
        *,
        geometry_id: str,
        expected_revision: int | None,
        resource: str,
        native_width: int,
        native_height: int,
        stage_x: float,
        stage_y: float,
        stage_width: float,
        stage_height: float,
        crop_x: float,
        crop_y: float,
        crop_width: float,
        crop_height: float,
        content_asset_id: str | None,
        surface_model: Literal["planar", "subdivided"],
        fit_mode: Literal["preserve", "stretch"],
        overscan: bool,
        brightness_limit: float,
        media_start_delay_ms: int,
        media_release_delay_ms: int,
        orientation_degrees: Literal[0, 90, 180, 270],
        mirror_x: bool,
        mirror_y: bool,
        mask_semantic_ids: tuple[str, ...],
        calibration_source: Literal["measured_grid", "easy_corner_pin", "unverified_estimate"],
        calibration_revision: int,
        mesh_vertices: tuple[tuple[float, float, float, float], ...],
        mesh_triangles: tuple[tuple[int, int, int], ...],
        actor: str,
        role: Role,
        occurred_at: str,
        stage_placement: StagePlacement = "surface",
    ) -> StoredProjectorMapping:
        geometry = self.geometry.get(geometry_id)
        if geometry.kind != "projector_surface":
            raise ProjectorMappingError("projector mapping requires a projector surface")
        if surface_model == "planar" and len(geometry.points) != 4:
            raise ProjectorMappingError("planar projector mapping requires exactly four corners")
        if surface_model == "subdivided" and len(geometry.points) < 5:
            raise ProjectorMappingError("subdivided mapping requires additional control points")
        homography = (
            _homography(
                native_width,
                native_height,
                geometry.points,
                orientation_degrees,
                mirror_x,
                mirror_y,
            )
            if surface_model == "planar"
            else None
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if content_asset_id is not None:
                asset = connection.execute(
                    """
                    SELECT a.status, a.trashed_at, r.media_type
                    FROM assets AS a
                    JOIN asset_revisions AS r
                      ON r.asset_id = a.id AND r.revision = a.current_revision
                    WHERE a.id = ?
                    """,
                    (content_asset_id,),
                ).fetchone()
                if (
                    asset is None
                    or str(asset["status"]) != "ready"
                    or not str(asset["media_type"]).startswith("image/")
                    or asset["trashed_at"] is not None
                ):
                    raise ProjectorMappingError(
                        "mapping content asset must be a ready managed image "
                        "that is not in the trash"
                    )
            if mask_semantic_ids:
                masks = connection.execute(
                    """
                    SELECT DISTINCT semantic_id
                    FROM installation_geometry
                    WHERE installation_id = ? AND kind = 'mask'
                      AND semantic_id IN (
                        SELECT value FROM json_each(?)
                      )
                    """,
                    (
                        geometry.installation_id,
                        json.dumps(mask_semantic_ids, separators=(",", ":")),
                    ),
                ).fetchall()
                if {str(row["semantic_id"]) for row in masks} != set(mask_semantic_ids):
                    raise ProjectorMappingError(
                        "projector masks must reference mask geometry in the installation"
                    )
            row = connection.execute(
                """
                SELECT revision, created_at
                FROM projector_surface_mappings WHERE geometry_id = ?
                """,
                (geometry_id,),
            ).fetchone()
            derived = (
                derive_stage_placement(
                    connection,
                    installation_id=geometry.installation_id,
                    semantic_id=geometry.semantic_id,
                    points=geometry.points,
                    native_width=native_width,
                    native_height=native_height,
                    orientation_degrees=orientation_degrees,
                    mirror_x=mirror_x,
                    mirror_y=mirror_y,
                    surface_model=surface_model,
                    mesh_vertices=mesh_vertices,
                )
                if stage_placement == "surface"
                else None
            )
            derived_stage_homography: tuple[float, ...] | None = None
            if derived is not None and derived.extent is not None:
                stage_x, stage_y, stage_width, stage_height = derived.extent
                derived_stage_homography = derived.homography
                if derived.mesh_vertices is not None:
                    mesh_vertices = derived.mesh_vertices
            stage_homography_json = (
                json.dumps(derived_stage_homography, separators=(",", ":"))
                if derived_stage_homography is not None
                else None
            )
            if row is None:
                if expected_revision is not None:
                    raise ProjectorMappingConflictError(0)
                revision = 1
                created_at = occurred_at
                connection.execute(
                    """
                    INSERT INTO projector_surface_mappings(
                        geometry_id, resource, native_width, native_height,
                        stage_x, stage_y, stage_width, stage_height,
                        crop_x, crop_y, crop_width, crop_height,
                        content_asset_id, surface_model, fit_mode, overscan,
                        brightness_limit, media_start_delay_ms,
                        media_release_delay_ms,
                        orientation_degrees, mirror_x, mirror_y,
                        mask_semantic_ids_json, calibration_source,
                        calibration_revision, mesh_vertices_json, mesh_triangles_json,
                        stage_homography_json, stage_placement,
                        revision, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?
                    )
                    """,
                    (
                        geometry_id,
                        resource,
                        native_width,
                        native_height,
                        stage_x,
                        stage_y,
                        stage_width,
                        stage_height,
                        crop_x,
                        crop_y,
                        crop_width,
                        crop_height,
                        content_asset_id,
                        surface_model,
                        fit_mode,
                        int(overscan),
                        brightness_limit,
                        media_start_delay_ms,
                        media_release_delay_ms,
                        orientation_degrees,
                        int(mirror_x),
                        int(mirror_y),
                        json.dumps(mask_semantic_ids, separators=(",", ":")),
                        calibration_source,
                        calibration_revision,
                        json.dumps(mesh_vertices, separators=(",", ":")),
                        json.dumps(mesh_triangles, separators=(",", ":")),
                        stage_homography_json,
                        stage_placement,
                        occurred_at,
                        occurred_at,
                    ),
                )
            else:
                current = int(row["revision"])
                if expected_revision != current:
                    raise ProjectorMappingConflictError(current)
                revision = current + 1
                created_at = str(row["created_at"])
                connection.execute(
                    """
                    UPDATE projector_surface_mappings
                    SET resource = ?, native_width = ?, native_height = ?,
                        stage_x = ?, stage_y = ?, stage_width = ?, stage_height = ?,
                        crop_x = ?, crop_y = ?, crop_width = ?, crop_height = ?,
                        content_asset_id = ?, surface_model = ?, fit_mode = ?,
                        overscan = ?, brightness_limit = ?, media_start_delay_ms = ?,
                        media_release_delay_ms = ?,
                        orientation_degrees = ?, mirror_x = ?, mirror_y = ?,
                        mask_semantic_ids_json = ?, calibration_source = ?,
                        calibration_revision = ?, mesh_vertices_json = ?,
                        mesh_triangles_json = ?, stage_homography_json = ?,
                        stage_placement = ?, revision = ?, updated_at = ?
                    WHERE geometry_id = ?
                    """,
                    (
                        resource,
                        native_width,
                        native_height,
                        stage_x,
                        stage_y,
                        stage_width,
                        stage_height,
                        crop_x,
                        crop_y,
                        crop_width,
                        crop_height,
                        content_asset_id,
                        surface_model,
                        fit_mode,
                        int(overscan),
                        brightness_limit,
                        media_start_delay_ms,
                        media_release_delay_ms,
                        orientation_degrees,
                        int(mirror_x),
                        int(mirror_y),
                        json.dumps(mask_semantic_ids, separators=(",", ":")),
                        calibration_source,
                        calibration_revision,
                        json.dumps(mesh_vertices, separators=(",", ":")),
                        json.dumps(mesh_triangles, separators=(",", ":")),
                        stage_homography_json,
                        stage_placement,
                        revision,
                        occurred_at,
                        geometry_id,
                    ),
                )
            connection.execute(
                """
                INSERT INTO audit_events(
                    actor, action, target, result, occurred_at, authorization_context_json
                ) VALUES (?, 'installation.projector_mapping.upsert', ?, 'success', ?, ?)
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
        return StoredProjectorMapping(
            geometry_id=geometry_id,
            resource=resource,
            native_width=native_width,
            native_height=native_height,
            stage_x=stage_x,
            stage_y=stage_y,
            stage_width=stage_width,
            stage_height=stage_height,
            crop_x=crop_x,
            crop_y=crop_y,
            crop_width=crop_width,
            crop_height=crop_height,
            content_asset_id=content_asset_id,
            surface_model=surface_model,
            fit_mode=fit_mode,
            overscan=overscan,
            brightness_limit=brightness_limit,
            media_start_delay_ms=media_start_delay_ms,
            media_release_delay_ms=media_release_delay_ms,
            orientation_degrees=orientation_degrees,
            mirror_x=mirror_x,
            mirror_y=mirror_y,
            mask_semantic_ids=mask_semantic_ids,
            calibration_source=calibration_source,
            calibration_revision=calibration_revision,
            mesh_vertices=mesh_vertices,
            mesh_triangles=mesh_triangles,
            photo_views=self._photo_views(
                geometry.installation_id,
                geometry.semantic_id,
                native_width,
                native_height,
                orientation_degrees,
                mirror_x,
                mirror_y,
                surface_model == "planar",
            ),
            homography=homography,
            diagnostic=(
                "A single homography cannot represent this subdivided surface; "
                "an indexed UV mesh is required before simulation or publication."
                if surface_model == "subdivided" and not mesh_triangles
                else None
            ),
            fit_diagnostics=_fit_diagnostics(
                native_width,
                native_height,
                stage_width,
                stage_height,
                crop_width,
                crop_height,
                fit_mode,
                overscan,
            ),
            revision=revision,
            created_at=created_at,
            updated_at=occurred_at,
            stage_homography=derived_stage_homography,
            stage_placement=stage_placement,
            stage_placement_basis=derived.basis if derived is not None else None,
            stage_placement_message=placement_message(
                stage_placement,
                derived,
                (stage_x, stage_y, stage_width, stage_height),
            ),
        )

    def _photo_views(
        self,
        installation_id: str,
        semantic_id: str,
        width: int,
        height: int,
        orientation_degrees: Literal[0, 90, 180, 270],
        mirror_x: bool,
        mirror_y: bool,
        planar: bool,
    ) -> tuple[ProjectorPhotoView, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, photo_id, points_json
                FROM installation_geometry
                WHERE installation_id = ? AND semantic_id = ?
                  AND kind = 'projector_surface'
                ORDER BY photo_id, id
                """,
                (installation_id, semantic_id),
            ).fetchall()
        views: list[ProjectorPhotoView] = []
        for row in rows:
            points = tuple(
                (float(point[0]), float(point[1])) for point in json.loads(str(row["points_json"]))
            )
            views.append(
                ProjectorPhotoView(
                    geometry_id=str(row["id"]),
                    photo_id=str(row["photo_id"]) if row["photo_id"] is not None else None,
                    points=points,
                    homography=(
                        _homography(
                            width,
                            height,
                            points,
                            orientation_degrees,
                            mirror_x,
                            mirror_y,
                        )
                        if planar and len(points) == 4
                        else None
                    ),
                )
            )
        return tuple(views)

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


def _stored_mapping(
    row: sqlite3.Row,
    points: tuple[tuple[float, float], ...],
    photo_views: tuple[ProjectorPhotoView, ...],
) -> StoredProjectorMapping:
    width = int(row["native_width"])
    height = int(row["native_height"])
    model_value = str(row["surface_model"])
    if model_value not in {"planar", "subdivided"}:
        raise ProjectorMappingError("projector surface model is invalid")
    surface_model = cast(Literal["planar", "subdivided"], model_value)
    fit_value = str(row["fit_mode"])
    if fit_value not in {"preserve", "stretch"}:
        raise ProjectorMappingError("projector fit mode is invalid")
    fit_mode = cast(Literal["preserve", "stretch"], fit_value)
    orientation_value = int(row["orientation_degrees"])
    if orientation_value not in {0, 90, 180, 270}:
        raise ProjectorMappingError("projector orientation is invalid")
    orientation = cast(Literal[0, 90, 180, 270], orientation_value)
    return StoredProjectorMapping(
        geometry_id=str(row["geometry_id"]),
        resource=str(row["resource"]),
        native_width=width,
        native_height=height,
        stage_x=float(row["stage_x"]),
        stage_y=float(row["stage_y"]),
        stage_width=float(row["stage_width"]),
        stage_height=float(row["stage_height"]),
        crop_x=float(row["crop_x"]),
        crop_y=float(row["crop_y"]),
        crop_width=float(row["crop_width"]),
        crop_height=float(row["crop_height"]),
        content_asset_id=(
            str(row["content_asset_id"]) if row["content_asset_id"] is not None else None
        ),
        surface_model=surface_model,
        fit_mode=fit_mode,
        overscan=bool(row["overscan"]),
        brightness_limit=float(row["brightness_limit"]),
        media_start_delay_ms=int(row["media_start_delay_ms"]),
        media_release_delay_ms=int(row["media_release_delay_ms"]),
        orientation_degrees=orientation,
        mirror_x=bool(row["mirror_x"]),
        mirror_y=bool(row["mirror_y"]),
        mask_semantic_ids=tuple(json.loads(str(row["mask_semantic_ids_json"]))),
        calibration_source=cast(
            Literal["measured_grid", "easy_corner_pin", "unverified_estimate"],
            str(row["calibration_source"]),
        ),
        calibration_revision=int(row["calibration_revision"]),
        mesh_vertices=tuple(
            (
                float(vertex[0]),
                float(vertex[1]),
                float(vertex[2]),
                float(vertex[3]),
            )
            for vertex in json.loads(str(row["mesh_vertices_json"]))
        ),
        mesh_triangles=tuple(
            (int(triangle[0]), int(triangle[1]), int(triangle[2]))
            for triangle in json.loads(str(row["mesh_triangles_json"]))
        ),
        photo_views=photo_views,
        homography=(
            _homography(
                width,
                height,
                points,
                orientation,
                bool(row["mirror_x"]),
                bool(row["mirror_y"]),
            )
            if surface_model == "planar"
            else None
        ),
        diagnostic=(
            "A single homography cannot represent this subdivided surface; "
            "an indexed UV mesh is required before simulation or publication."
            if surface_model == "subdivided" and not json.loads(str(row["mesh_triangles_json"]))
            else None
        ),
        fit_diagnostics=_fit_diagnostics(
            width,
            height,
            float(row["stage_width"]),
            float(row["stage_height"]),
            float(row["crop_width"]),
            float(row["crop_height"]),
            fit_mode,
            bool(row["overscan"]),
        ),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        stage_homography=(
            tuple(float(value) for value in json.loads(str(row["stage_homography_json"])))
            if row["stage_homography_json"] is not None
            else None
        ),
        stage_placement=_row_placement(row),
    )


def derive_stage_placement(
    connection: sqlite3.Connection,
    *,
    installation_id: str,
    semantic_id: str,
    points: tuple[tuple[float, float], ...],
    native_width: int,
    native_height: int,
    orientation_degrees: Literal[0, 90, 180, 270],
    mirror_x: bool,
    mirror_y: bool,
    surface_model: Literal["planar", "subdivided"],
    mesh_vertices: tuple[tuple[float, float, float, float], ...],
) -> DerivedStagePlacement:
    """Derive a projector's shared-stage footprint from its drawn surface.

    Preference order: the linked surface drawn on the stage reference view, the
    surface's saved stage points, then (only when no stage reference exists) the
    mapped drawing itself at the default stage scale.
    """
    reference = connection.execute(
        """
        SELECT photo_id, units_per_canvas FROM installation_stage_references
        WHERE installation_id = ?
        """,
        (installation_id,),
    ).fetchone()
    members = connection.execute(
        """
        SELECT photo_id, points_json, stage_points_json FROM installation_geometry
        WHERE installation_id = ? AND kind = 'projector_surface' AND semantic_id = ?
        ORDER BY id
        """,
        (installation_id, semantic_id),
    ).fetchall()
    reference_member = (
        next((row for row in members if row["photo_id"] == reference["photo_id"]), None)
        if reference is not None and reference["photo_id"] is not None
        else None
    )
    stored_stage = next(
        (row["stage_points_json"] for row in members if row["stage_points_json"] is not None),
        None,
    )
    basis: StagePlacementBasis
    if reference_member is not None:
        basis = "stage_view"
        layout = _json_points(reference_member["points_json"])
        units = float(reference["units_per_canvas"])
    elif stored_stage is not None:
        basis = "stage_points"
        # Express stage points as layout points at one stage unit per layout pixel.
        layout = tuple((x / CANVAS_WIDTH, y / CANVAS_HEIGHT) for x, y in _json_points(stored_stage))
        units = 1.0
    elif reference is None and not _has_stage_points(connection, installation_id):
        basis = "default_scale"
        layout = points
        units = DEFAULT_UNITS_PER_CANVAS
    elif reference is None:
        return _unresolved(
            "Other objects already have hand-entered stage positions. Choose a stage "
            "view in Stage map so this surface is placed at the same scale."
        )
    else:
        return DerivedStagePlacement(
            basis=None,
            extent=None,
            homography=None,
            mesh_vertices=None,
            message=(
                "Draw this surface on the stage view photo (or give it stage points) "
                "so its stage placement can be derived."
            ),
        )
    if surface_model == "planar":
        if len(layout) != 4:
            return _unresolved("A planar projector surface needs exactly four corners.")
        try:
            homography = stage_homography(
                _homography(
                    native_width, native_height, layout, orientation_degrees, mirror_x, mirror_y
                ),
                native_width,
                native_height,
                units,
            )
            footprint = homography_footprint(homography)
        except (ProjectorMappingError, ValueError) as error:
            return _unresolved(f"Projector corners cannot be placed: {error}")
        return DerivedStagePlacement(
            basis=basis,
            extent=_extent(footprint),
            homography=homography,
            mesh_vertices=None,
            message=None,
        )
    stage_points = tuple(canvas_to_stage(point, units) for point in layout)
    if not mesh_vertices or len(mesh_vertices) != len(stage_points):
        return _unresolved(
            "Subdivided projector mesh does not match its drawn points; "
            "set its stage mesh manually."
        )
    return DerivedStagePlacement(
        basis=basis,
        extent=_extent(stage_points),
        homography=None,
        mesh_vertices=tuple(
            (vertex[0], vertex[1], point[0], point[1])
            for vertex, point in zip(mesh_vertices, stage_points, strict=True)
        ),
        message=None,
    )


def refresh_surface_placements(
    connection: sqlite3.Connection,
    *,
    installation_id: str,
    semantic_id: str | None,
    occurred_at: str,
) -> tuple[str, ...]:
    """Rewrite stale surface-placed mappings; returns the updated geometry ids.

    The caller owns the transaction and auditing.
    """
    query = """
        SELECT m.*, g.installation_id, g.semantic_id, g.points_json
        FROM projector_surface_mappings AS m
        JOIN installation_geometry AS g ON g.id = m.geometry_id
        WHERE g.installation_id = ? AND m.stage_placement = 'surface'
    """
    values: tuple[object, ...] = (installation_id,)
    if semantic_id is not None:
        query += " AND g.semantic_id = ?"
        values += (semantic_id,)
    updated: list[str] = []
    for row in connection.execute(query + " ORDER BY m.geometry_id", values).fetchall():
        try:
            orientation = cast(Literal[0, 90, 180, 270], int(row["orientation_degrees"]))
            model = cast(Literal["planar", "subdivided"], str(row["surface_model"]))
            mesh = _mesh_vertices(row)
            derived = derive_stage_placement(
                connection,
                installation_id=installation_id,
                semantic_id=str(row["semantic_id"]),
                points=_json_points(row["points_json"]),
                native_width=int(row["native_width"]),
                native_height=int(row["native_height"]),
                orientation_degrees=orientation,
                mirror_x=bool(row["mirror_x"]),
                mirror_y=bool(row["mirror_y"]),
                surface_model=model,
                mesh_vertices=mesh,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if derived.extent is None:
            continue
        current_extent = (
            float(row["stage_x"]),
            float(row["stage_y"]),
            float(row["stage_width"]),
            float(row["stage_height"]),
        )
        current_homography = (
            tuple(float(value) for value in json.loads(str(row["stage_homography_json"])))
            if row["stage_homography_json"] is not None
            else None
        )
        next_mesh = derived.mesh_vertices if derived.mesh_vertices is not None else mesh
        if (
            _close(current_extent, derived.extent)
            and _close(current_homography, derived.homography)
            and _close(
                tuple(value for vertex in mesh for value in vertex),
                tuple(value for vertex in next_mesh for value in vertex),
            )
        ):
            continue
        x, y, width, height = derived.extent
        connection.execute(
            """
            UPDATE projector_surface_mappings
            SET stage_x = ?, stage_y = ?, stage_width = ?, stage_height = ?,
                stage_homography_json = ?, mesh_vertices_json = ?,
                revision = revision + 1, updated_at = ?
            WHERE geometry_id = ?
            """,
            (
                x,
                y,
                width,
                height,
                (
                    json.dumps(derived.homography, separators=(",", ":"))
                    if derived.homography is not None
                    else None
                ),
                json.dumps(next_mesh, separators=(",", ":")),
                occurred_at,
                str(row["geometry_id"]),
            ),
        )
        updated.append(str(row["geometry_id"]))
    return tuple(updated)


def placement_message(
    placement: StagePlacement,
    derived: DerivedStagePlacement | None,
    extent: tuple[float, float, float, float],
) -> str | None:
    if placement == "manual":
        if _close(extent, DEFAULT_STAGE_EXTENT):
            return (
                "Manual stage placement still uses the default 4 x 2.25 rectangle at the "
                "stage origin; every projector left on this default overlaps the others."
            )
        return "Stage placement is set manually; redrawing the surface does not move it."
    if derived is None:
        return None
    if derived.basis == "stage_view":
        return "Placed from the surface drawn on the stage view."
    if derived.basis == "stage_points":
        return "Placed from this surface's saved stage points."
    if derived.basis == "default_scale":
        return (
            "Placed from the drawn surface at the default stage scale. Choose a stage "
            "view in Stage map to set real-world size."
        )
    return f"{derived.message} The entered stage extent is used until then."


def _has_stage_points(connection: sqlite3.Connection, installation_id: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1 FROM installation_geometry
            WHERE installation_id = ? AND stage_points_json IS NOT NULL
            LIMIT 1
            """,
            (installation_id,),
        ).fetchone()
        is not None
    )


def _unresolved(message: str) -> DerivedStagePlacement:
    return DerivedStagePlacement(
        basis=None, extent=None, homography=None, mesh_vertices=None, message=message
    )


def _row_placement(row: sqlite3.Row) -> StagePlacement:
    value = str(row["stage_placement"])
    if value not in {"surface", "manual"}:
        raise ProjectorMappingError("projector stage placement is invalid")
    return cast(StagePlacement, value)


def _mesh_vertices(row: sqlite3.Row) -> tuple[tuple[float, float, float, float], ...]:
    return tuple(
        (float(vertex[0]), float(vertex[1]), float(vertex[2]), float(vertex[3]))
        for vertex in json.loads(str(row["mesh_vertices_json"]))
    )


def _json_points(value: object) -> tuple[tuple[float, float], ...]:
    return tuple((float(point[0]), float(point[1])) for point in json.loads(str(value)))


def _extent(points: tuple[tuple[float, float], ...]) -> tuple[float, float, float, float]:
    minimum_x, minimum_y, maximum_x, maximum_y = bounds(points)
    return (
        minimum_x,
        minimum_y,
        max(maximum_x - minimum_x, 1e-6),
        max(maximum_y - minimum_y, 1e-6),
    )


def _close(current: tuple[float, ...] | None, derived: tuple[float, ...] | None) -> bool:
    if current is None or derived is None:
        return current is None and derived is None
    if len(current) != len(derived):
        return False
    return all(
        abs(left - right) <= 1e-6 * max(1.0, abs(left), abs(right))
        for left, right in zip(current, derived, strict=True)
    )


def _homography(
    width: int,
    height: int,
    points: tuple[tuple[float, float], ...],
    orientation_degrees: Literal[0, 90, 180, 270],
    mirror_x: bool,
    mirror_y: bool,
) -> tuple[float, ...]:
    if len(points) != 4:
        raise ProjectorMappingError("homography requires exactly four corners")
    sources = tuple(
        _oriented_source(x, y, width, height, orientation_degrees, mirror_x, mirror_y)
        for x, y in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    )
    matrix: list[list[float]] = []
    for (x, y), (u, v) in zip(sources, points, strict=True):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y, u])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y, v])
    for pivot in range(8):
        row = max(range(pivot, 8), key=lambda index: abs(matrix[index][pivot]))
        if abs(matrix[row][pivot]) < 1e-12:
            raise ProjectorMappingError("projector corners do not define a valid plane")
        matrix[pivot], matrix[row] = matrix[row], matrix[pivot]
        divisor = matrix[pivot][pivot]
        matrix[pivot] = [value / divisor for value in matrix[pivot]]
        for index in range(8):
            if index == pivot:
                continue
            factor = matrix[index][pivot]
            matrix[index] = [
                value - factor * pivot_value
                for value, pivot_value in zip(matrix[index], matrix[pivot], strict=True)
            ]
    values = tuple(matrix[index][8] for index in range(8))
    return (*values, 1.0)


def _oriented_source(
    x: float,
    y: float,
    width: int,
    height: int,
    orientation_degrees: Literal[0, 90, 180, 270],
    mirror_x: bool,
    mirror_y: bool,
) -> tuple[float, float]:
    if mirror_x:
        x = 1 - x
    if mirror_y:
        y = 1 - y
    if orientation_degrees == 90:
        x, y = 1 - y, x
    elif orientation_degrees == 180:
        x, y = 1 - x, 1 - y
    elif orientation_degrees == 270:
        x, y = y, 1 - x
    return x * width, y * height


def _fit_diagnostics(
    native_width: int,
    native_height: int,
    stage_width: float,
    stage_height: float,
    crop_width: float,
    crop_height: float,
    fit_mode: Literal["preserve", "stretch"],
    overscan: bool,
) -> tuple[str, ...]:
    native_aspect = (native_width * crop_width) / (native_height * crop_height)
    stage_aspect = stage_width / stage_height
    mismatch = abs(native_aspect - stage_aspect) / native_aspect
    diagnostics: list[str] = []
    if mismatch > 0.01:
        diagnostics.append(
            "Stretch distortion will be visible."
            if fit_mode == "stretch"
            else "Aspect ratio differs; preserved content will letterbox or pillarbox."
        )
    if crop_width < 1 or crop_height < 1:
        diagnostics.append("Native content is intentionally cropped.")
    if overscan:
        diagnostics.append("Overscan is enabled and may place content outside the surface.")
    return tuple(diagnostics)
