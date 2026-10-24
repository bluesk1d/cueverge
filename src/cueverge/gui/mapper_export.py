import json
import sqlite3
from dataclasses import dataclass
from itertools import pairwise

from cueverge.gui.lighting_surface import LightingSurfaceService
from cueverge.gui.repository import GuiRepository
from cueverge.gui.stage_geometry import homography_mesh, stage_size
from cueverge.gui.stage_reference import stored_stage_homography


class MapperExportError(RuntimeError):
    def __init__(self, diagnostics: tuple["MapperDiagnostic", ...]) -> None:
        super().__init__("mapper validation failed")
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class MapperDiagnostic:
    code: str
    message: str
    object_ids: tuple[str, ...]


class MapperExportService:
    def __init__(
        self,
        repository: GuiRepository,
        lighting_surface: LightingSurfaceService,
    ) -> None:
        self.repository = repository
        self.lighting_surface = lighting_surface

    def export(self, installation_id: str) -> dict[str, object]:
        diagnostics = self.validate(installation_id)
        if diagnostics:
            raise MapperExportError(diagnostics)
        with self._connect() as connection:
            projectors = [
                {
                    "geometry_id": str(row["geometry_id"]),
                    "resource": str(row["resource"]),
                    "native_width": int(row["native_width"]),
                    "native_height": int(row["native_height"]),
                    "orientation_degrees": int(row["orientation_degrees"]),
                    "mirror_x": bool(row["mirror_x"]),
                    "mirror_y": bool(row["mirror_y"]),
                    "crop": [
                        float(row["crop_x"]),
                        float(row["crop_y"]),
                        float(row["crop_width"]),
                        float(row["crop_height"]),
                    ],
                    "stage_extent": [
                        float(row["stage_x"]),
                        float(row["stage_y"]),
                        float(row["stage_width"]),
                        float(row["stage_height"]),
                    ],
                    "stage_placement": str(row["stage_placement"]),
                    "mask_semantic_ids": json.loads(str(row["mask_semantic_ids_json"])),
                    "calibration_source": str(row["calibration_source"]),
                    "calibration_revision": int(row["calibration_revision"]),
                    "surface_model": str(row["surface_model"]),
                    **_projector_mesh(row),
                    "brightness_limit": float(row["brightness_limit"]),
                    "media_start_delay_ms": int(row["media_start_delay_ms"]),
                    "media_release_delay_ms": int(row["media_release_delay_ms"]),
                }
                for row in connection.execute(
                    """
                    SELECT m.*
                    FROM projector_surface_mappings AS m
                    JOIN installation_geometry AS g ON g.id = m.geometry_id
                    WHERE g.installation_id = ?
                    ORDER BY m.resource, m.geometry_id
                    """,
                    (installation_id,),
                ).fetchall()
            ]
            surfaces = []
            for row in connection.execute(
                """
                SELECT s.*, g.semantic_id, g.name
                FROM lighting_surfaces AS s
                JOIN installation_geometry AS g ON g.id = s.geometry_id
                WHERE g.installation_id = ?
                ORDER BY s.resource, s.geometry_id
                """,
                (installation_id,),
            ).fetchall():
                mapped = self.lighting_surface.get(str(row["geometry_id"]))
                surfaces.append(
                    {
                        "semantic_id": str(row["semantic_id"]),
                        "name": str(row["name"]),
                        "resource": str(row["resource"]),
                        "local_width": float(row["local_width"]),
                        "local_height": float(row["local_height"]),
                        "local_points": json.loads(str(row["local_points_json"])),
                        "stage_matrix": json.loads(str(row["stage_matrix_json"])),
                        "fixture_id": row["fixture_id"],
                        "group_id": row["group_id"],
                        "path_id": row["path_id"],
                        "strand_id": row["strand_id"],
                        "calibration_id": row["calibration_id"],
                        "mapping_revision": int(row["revision"]),
                        "pixel_overrides": [
                            {
                                "output_index": override.output_index,
                                "local_x": override.local_x,
                                "local_y": override.local_y,
                            }
                            for override in mapped.pixel_overrides
                        ],
                        "brightness_limit": float(row["brightness_limit"]),
                        "timing_offset_ms": int(row["timing_offset_ms"]),
                        "release_offset_ms": int(row["release_offset_ms"]),
                        "pixels": [
                            {
                                "output_index": pixel.output_index,
                                "local_x": pixel.local_x,
                                "local_y": pixel.local_y,
                                "footprint_width": pixel.footprint_width,
                                "footprint_height": pixel.footprint_height,
                                "stage_x": pixel.stage_x,
                                "stage_y": pixel.stage_y,
                            }
                            for pixel in mapped.pixels
                        ],
                    }
                )
            fixtures, paths, zones = self._stage_geometry(connection, installation_id)
            stage = _stage_frame(connection, installation_id)
        return {
            "schema_version": 1,
            "installation_id": installation_id,
            "stage": stage,
            "projectors": projectors,
            "lighting_surfaces": surfaces,
            "fixtures": fixtures,
            "paths": paths,
            "zones": zones,
        }

    def validate(self, installation_id: str) -> tuple[MapperDiagnostic, ...]:
        diagnostics: list[MapperDiagnostic] = []
        with self._connect() as connection:
            ambiguous = connection.execute(
                """
                SELECT semantic_id, coordinate_space_id, GROUP_CONCAT(id) AS ids
                FROM installation_geometry
                WHERE installation_id = ?
                GROUP BY semantic_id, coordinate_space_id
                HAVING COUNT(*) > 1
                """,
                (installation_id,),
            ).fetchall()
            diagnostics.extend(
                MapperDiagnostic(
                    code="ambiguous_semantic_geometry",
                    message=(
                        f"Semantic object {row['semantic_id']} has multiple instances "
                        f"in layout coordinate space {row['coordinate_space_id']}."
                    ),
                    object_ids=tuple(str(row["ids"]).split(",")),
                )
                for row in ambiguous
            )
            duplicate_resources = connection.execute(
                """
                SELECT m.resource, GROUP_CONCAT(m.geometry_id) AS ids
                FROM projector_surface_mappings AS m
                JOIN installation_geometry AS g ON g.id = m.geometry_id
                WHERE g.installation_id = ?
                GROUP BY m.resource
                HAVING COUNT(*) > 1
                """,
                (installation_id,),
            ).fetchall()
            diagnostics.extend(
                MapperDiagnostic(
                    code="multiply_assigned_projector_resource",
                    message=f"Projector resource {row['resource']} has multiple mappings.",
                    object_ids=tuple(str(row["ids"]).split(",")),
                )
                for row in duplicate_resources
            )
            missing_meshes = connection.execute(
                """
                SELECT m.geometry_id
                FROM projector_surface_mappings AS m
                JOIN installation_geometry AS g ON g.id = m.geometry_id
                WHERE g.installation_id = ?
                  AND m.surface_model = 'subdivided'
                  AND json_array_length(m.mesh_triangles_json) = 0
                """,
                (installation_id,),
            ).fetchall()
            diagnostics.extend(
                MapperDiagnostic(
                    code="projector_mesh_missing",
                    message=(
                        f"Subdivided projector surface {row['geometry_id']} "
                        "requires an indexed UV mesh."
                    ),
                    object_ids=(str(row["geometry_id"]),),
                )
                for row in missing_meshes
            )
            for row in connection.execute(
                """
                SELECT id, kind, name, stage_points_json
                FROM installation_geometry
                WHERE installation_id = ?
                  AND kind IN ('fixture', 'path', 'audience_zone', 'safety_zone')
                """,
                (installation_id,),
            ).fetchall():
                points = (
                    json.loads(str(row["stage_points_json"]))
                    if row["stage_points_json"] is not None
                    else []
                )
                if (
                    not points
                    or (row["kind"] == "path" and len(points) < 2)
                    or (row["kind"] in {"audience_zone", "safety_zone"} and len(points) < 3)
                ):
                    diagnostics.append(
                        MapperDiagnostic(
                            code="disconnected_geometry",
                            message=(
                                f"{row['kind']} {row['name']} is disconnected "
                                "from shared stage geometry."
                            ),
                            object_ids=(str(row["id"]),),
                        )
                    )
            segments = connection.execute(
                """
                SELECT s.resource, g.id, g.start_index, g.pixel_count
                FROM lighting_surfaces AS s
                JOIN installation_geometry AS g ON g.id = s.geometry_id
                WHERE g.installation_id = ?
                ORDER BY s.resource, g.start_index, g.id
                """,
                (installation_id,),
            ).fetchall()
            for left, right in pairwise(segments):
                if left["resource"] != right["resource"]:
                    continue
                if int(right["start_index"]) < int(left["start_index"]) + int(left["pixel_count"]):
                    diagnostics.append(
                        MapperDiagnostic(
                            code="overlapping_pixel_indices",
                            message=(
                                f"Pixel output {left['resource']} has overlapping "
                                "physical index ranges."
                            ),
                            object_ids=(str(left["id"]), str(right["id"])),
                        )
                    )
            stage = _stage_frame(connection, installation_id)
            bounds = (
                (0.0, 0.0, *_stage_dimensions(stage))
                if stage is not None
                else connection.execute(
                    """
                    SELECT MIN(m.stage_x), MIN(m.stage_y),
                           MAX(m.stage_x + m.stage_width),
                           MAX(m.stage_y + m.stage_height)
                    FROM projector_surface_mappings AS m
                    JOIN installation_geometry AS g ON g.id = m.geometry_id
                    WHERE g.installation_id = ?
                    """,
                    (installation_id,),
                ).fetchone()
            )
            if bounds is not None and bounds[0] is not None:
                for row in connection.execute(
                    """
                    SELECT id, name, stage_points_json
                    FROM installation_geometry
                    WHERE installation_id = ? AND stage_points_json IS NOT NULL
                    """,
                    (installation_id,),
                ).fetchall():
                    points = json.loads(str(row["stage_points_json"]))
                    if any(
                        float(point[0]) < float(bounds[0])
                        or float(point[0]) > float(bounds[2])
                        or float(point[1]) < float(bounds[1])
                        or float(point[1]) > float(bounds[3])
                        for point in points
                    ):
                        diagnostics.append(
                            MapperDiagnostic(
                                code="stage_geometry_out_of_range",
                                message=(
                                    f"{row['name']} extends outside the stage reference view."
                                    if stage is not None
                                    else f"{row['name']} extends outside calibrated stage coverage."
                                ),
                                object_ids=(str(row["id"]),),
                            )
                        )
        return tuple(diagnostics)

    def _stage_geometry(
        self,
        connection: sqlite3.Connection,
        installation_id: str,
    ) -> tuple[
        list[dict[str, object]],
        list[dict[str, object]],
        list[dict[str, object]],
    ]:
        results: dict[str, list[dict[str, object]]] = {
            "fixture": [],
            "path": [],
            "audience_zone": [],
            "safety_zone": [],
        }
        seen: set[tuple[str, str]] = set()
        rows = connection.execute(
            """
            SELECT kind, semantic_id, name, stage_points_json
            FROM installation_geometry
            WHERE installation_id = ?
              AND kind IN ('fixture', 'path', 'audience_zone', 'safety_zone')
            ORDER BY kind, semantic_id, id
            """,
            (installation_id,),
        ).fetchall()
        for row in rows:
            key = (str(row["kind"]), str(row["semantic_id"]))
            if key in seen:
                continue
            seen.add(key)
            if row["stage_points_json"] is None:
                raise MapperExportError(
                    (
                        MapperDiagnostic(
                            code="missing_stage_geometry",
                            message=(f"{row['kind']} {row['name']} has no shared-stage points."),
                            object_ids=(str(row["semantic_id"]),),
                        ),
                    )
                )
            results[key[0]].append(
                {
                    "semantic_id": key[1],
                    "name": str(row["name"]),
                    "stage_points": json.loads(str(row["stage_points_json"])),
                }
            )
        return (
            results["fixture"],
            results["path"],
            [
                {**zone, "kind": kind}
                for kind in ("audience_zone", "safety_zone")
                for zone in results[kind]
            ],
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.repository.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection


def _projector_mesh(row: sqlite3.Row) -> dict[str, object]:
    derived = stored_stage_homography(row)
    if derived is not None and str(row["surface_model"]) == "planar":
        vertices, triangles = homography_mesh(derived)
        return {
            "mesh_vertices": [list(vertex) for vertex in vertices],
            "mesh_triangles": [list(triangle) for triangle in triangles],
            "stage_homography": list(derived),
        }
    return {
        "mesh_vertices": json.loads(str(row["mesh_vertices_json"])),
        "mesh_triangles": json.loads(str(row["mesh_triangles_json"])),
        "stage_homography": None,
    }


def _stage_frame(connection: sqlite3.Connection, installation_id: str) -> dict[str, object] | None:
    row = connection.execute(
        """
        SELECT photo_id, units_per_canvas, unit_label, revision, applied_at
        FROM installation_stage_references WHERE installation_id = ?
        """,
        (installation_id,),
    ).fetchone()
    if row is None or row["applied_at"] is None:
        return None
    width, height = stage_size(float(row["units_per_canvas"]))
    return {
        "width": width,
        "height": height,
        "unit": str(row["unit_label"]),
        "reference_photo_id": str(row["photo_id"]) if row["photo_id"] is not None else None,
        "reference_revision": int(row["revision"]),
        "canvas_units_per_stage_unit": 1 / float(row["units_per_canvas"]),
    }


def _stage_dimensions(stage: dict[str, object]) -> tuple[float, float]:
    width, height = stage["width"], stage["height"]
    assert isinstance(width, float) and isinstance(height, float)
    return width, height
