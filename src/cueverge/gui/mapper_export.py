import json
import sqlite3
from dataclasses import dataclass
from itertools import pairwise

from cueverge.gui.lighting_surface import LightingSurfaceService
from cueverge.gui.repository import GuiRepository


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
                    "mask_semantic_ids": json.loads(str(row["mask_semantic_ids_json"])),
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
                SELECT s.*, g.semantic_id
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
                        "brightness_limit": float(row["brightness_limit"]),
                        "timing_offset_ms": int(row["timing_offset_ms"]),
                        "release_offset_ms": int(row["release_offset_ms"]),
                        "pixels": [
                            {
                                "output_index": pixel.output_index,
                                "stage_x": pixel.stage_x,
                                "stage_y": pixel.stage_y,
                            }
                            for pixel in mapped.pixels
                        ],
                    }
                )
            fixtures, paths, zones = self._stage_geometry(connection, installation_id)
        return {
            "schema_version": 1,
            "installation_id": installation_id,
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
                SELECT semantic_id, photo_id, GROUP_CONCAT(id) AS ids
                FROM installation_geometry
                WHERE installation_id = ?
                GROUP BY semantic_id, photo_id
                HAVING COUNT(*) > 1
                """,
                (installation_id,),
            ).fetchall()
            diagnostics.extend(
                MapperDiagnostic(
                    code="ambiguous_semantic_geometry",
                    message=(
                        f"Semantic object {row['semantic_id']} has multiple instances "
                        f"in photo {row['photo_id']}."
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
            bounds = connection.execute(
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
                                message=f"{row['name']} extends outside calibrated stage coverage.",
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
