import json
import sqlite3
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal, cast

from cueverge.gui.projector_mapping import _homography
from cueverge.gui.repository import GuiRepository

RegionKind = Literal["covered", "overlap", "occluded", "uncovered"]


@dataclass(frozen=True)
class StageRegion:
    kind: RegionKind
    points: tuple[tuple[float, float], ...]
    resources: tuple[str, ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class StageCoverage:
    installation_id: str
    minimum_x: float
    minimum_y: float
    maximum_x: float
    maximum_y: float
    regions: tuple[StageRegion, ...]


class StageCoverageService:
    def __init__(self, repository: GuiRepository) -> None:
        self.repository = repository

    def analyze(self, installation_id: str) -> StageCoverage:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT m.*, g.photo_id, g.points_json, g.semantic_id, g.installation_id,
                       g.coordinate_space_id
                FROM projector_surface_mappings AS m
                JOIN installation_geometry AS g ON g.id = m.geometry_id
                WHERE g.installation_id = ?
                ORDER BY m.geometry_id
                """,
                (installation_id,),
            ).fetchall()
            if not rows:
                return StageCoverage(installation_id, 0, 0, 1, 1, ())
            xs = sorted(
                {
                    value
                    for row in rows
                    for value in (
                        float(row["stage_x"]),
                        float(row["stage_x"]) + float(row["stage_width"]),
                    )
                }
            )
            ys = sorted(
                {
                    value
                    for row in rows
                    for value in (
                        float(row["stage_y"]),
                        float(row["stage_y"]) + float(row["stage_height"]),
                    )
                }
            )
            regions: list[StageRegion] = []
            for left, right in pairwise(xs):
                for top, bottom in pairwise(ys):
                    center_x = (left + right) / 2
                    center_y = (top + bottom) / 2
                    owners = [
                        row
                        for row in rows
                        if float(row["stage_x"])
                        <= center_x
                        <= float(row["stage_x"]) + float(row["stage_width"])
                        and float(row["stage_y"])
                        <= center_y
                        <= float(row["stage_y"]) + float(row["stage_height"])
                    ]
                    regions.append(
                        StageRegion(
                            kind=(
                                "uncovered"
                                if not owners
                                else "covered"
                                if len(owners) == 1
                                else "overlap"
                            ),
                            points=_rectangle(left, top, right, bottom),
                            resources=tuple(str(row["resource"]) for row in owners),
                            source_ids=tuple(str(row["geometry_id"]) for row in owners),
                        )
                    )
            regions.extend(self._occluded_regions(connection, rows))
        return StageCoverage(
            installation_id=installation_id,
            minimum_x=xs[0],
            minimum_y=ys[0],
            maximum_x=xs[-1],
            maximum_y=ys[-1],
            regions=tuple(regions),
        )

    def _occluded_regions(
        self,
        connection: sqlite3.Connection,
        mappings: list[sqlite3.Row],
    ) -> list[StageRegion]:
        regions: list[StageRegion] = []
        for mapping in mappings:
            mask_ids = json.loads(str(mapping["mask_semantic_ids_json"]))
            if not mask_ids:
                continue
            masks = connection.execute(
                """
                SELECT semantic_id, points_json
                FROM installation_geometry
                WHERE installation_id = ? AND coordinate_space_id = ? AND kind = 'mask'
                  AND semantic_id IN (SELECT value FROM json_each(?))
                """,
                (
                    mapping["installation_id"],
                    mapping["coordinate_space_id"],
                    json.dumps(mask_ids, separators=(",", ":")),
                ),
            ).fetchall()
            surface_points = tuple(
                (float(point[0]), float(point[1]))
                for point in json.loads(str(mapping["points_json"]))
            )
            if len(surface_points) != 4:
                continue
            homography = _homography(
                int(mapping["native_width"]),
                int(mapping["native_height"]),
                surface_points,
                cast(Literal[0, 90, 180, 270], int(mapping["orientation_degrees"])),
                bool(mapping["mirror_x"]),
                bool(mapping["mirror_y"]),
            )
            inverse = _inverse(homography)
            for mask in masks:
                stage_points = tuple(
                    _photo_to_stage(
                        (float(point[0]), float(point[1])),
                        inverse,
                        mapping,
                    )
                    for point in json.loads(str(mask["points_json"]))
                )
                regions.append(
                    StageRegion(
                        kind="occluded",
                        points=stage_points,
                        resources=(str(mapping["resource"]),),
                        source_ids=(str(mask["semantic_id"]),),
                    )
                )
        return regions

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.repository.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection


def _rectangle(
    left: float, top: float, right: float, bottom: float
) -> tuple[tuple[float, float], ...]:
    return ((left, top), (right, top), (right, bottom), (left, bottom))


def _inverse(values: tuple[float, ...]) -> tuple[float, ...]:
    a, b, c, d, e, f, g, h, i = values
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(determinant) < 1e-12:
        raise ValueError("homography is singular")
    return (
        (e * i - f * h) / determinant,
        (c * h - b * i) / determinant,
        (b * f - c * e) / determinant,
        (f * g - d * i) / determinant,
        (a * i - c * g) / determinant,
        (c * d - a * f) / determinant,
        (d * h - e * g) / determinant,
        (b * g - a * h) / determinant,
        (a * e - b * d) / determinant,
    )


def _photo_to_stage(
    point: tuple[float, float],
    inverse: tuple[float, ...],
    mapping: sqlite3.Row,
) -> tuple[float, float]:
    x, y = point
    denominator = inverse[6] * x + inverse[7] * y + inverse[8]
    native_x = (inverse[0] * x + inverse[1] * y + inverse[2]) / denominator
    native_y = (inverse[3] * x + inverse[4] * y + inverse[5]) / denominator
    return (
        float(mapping["stage_x"])
        + native_x / int(mapping["native_width"]) * float(mapping["stage_width"]),
        float(mapping["stage_y"])
        + native_y / int(mapping["native_height"]) * float(mapping["stage_height"]),
    )
