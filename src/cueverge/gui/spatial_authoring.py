import hashlib
import json
from io import BytesIO
from typing import Literal, cast
from uuid import uuid4

from PIL import Image, ImageDraw
from pydantic import JsonValue

from cueverge.gui.contracts import DraftProject
from cueverge.gui.mcp_authoring import McpAuthoringError
from cueverge.gui.spatial_effects import SpatialRipple


def layout_digest(mapper: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(mapper, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def spatial_context(
    draft: DraftProject,
    mapper: dict[str, object],
    *,
    pixel_offset: int = 0,
    pixel_limit: int = 0,
) -> dict[str, JsonValue]:
    if pixel_offset < 0 or pixel_limit < 0 or pixel_limit > 500:
        raise McpAuthoringError("pixel offset must be nonnegative and limit must be 0..500")
    surfaces = cast(list[dict[str, object]], mapper.get("lighting_surfaces", []))
    projectors = cast(list[dict[str, object]], mapper.get("projectors", []))
    if len(surfaces) + len(projectors) > 100:
        raise McpAuthoringError("spatial context exceeds 100 surfaces; narrow the installation")
    points: list[tuple[float, float]] = []
    for projector in projectors:
        x, y, width, height = cast(list[float], projector["stage_extent"])
        points.extend([(x, y), (x + width, y + height)])
    for section in ("fixtures", "paths", "zones"):
        for record in cast(list[dict[str, object]], mapper.get(section, [])):
            points.extend(
                (float(point[0]), float(point[1]))
                for point in cast(list[list[float]], record.get("stage_points", []))
            )
    summaries: list[dict[str, object]] = []
    detailed: list[dict[str, object]] = []
    total = 0
    for surface in surfaces:
        pixels = cast(list[dict[str, object]], surface["pixels"])
        positions = [
            (float(cast(float, p["stage_x"])), float(cast(float, p["stage_y"]))) for p in pixels
        ]
        points.extend(positions)
        summary = {
            key: surface.get(key)
            for key in (
                "semantic_id",
                "name",
                "resource",
                "local_width",
                "local_height",
                "stage_matrix",
                "local_points",
                "mapping_revision",
                "calibration_id",
                "brightness_limit",
                "fixture_id",
                "group_id",
                "path_id",
            )
        }
        summary["pixel_count"] = len(pixels)
        summary["stage_bounds"] = _bounds(positions)
        summaries.append(summary)
        for pixel in pixels:
            if pixel_offset <= total < pixel_offset + pixel_limit:
                detailed.append(
                    {
                        "surface_id": surface["semantic_id"],
                        **{
                            key: pixel[key]
                            for key in (
                                "local_x",
                                "local_y",
                                "stage_x",
                                "stage_y",
                                "footprint_width",
                                "footprint_height",
                            )
                        },
                    }
                )
            total += 1
    result = {
        "schema_version": 1,
        "layout_digest": layout_digest(mapper),
        "coordinate_space": "stage",
        "units": "installation units; do not assume metres or photo pixels",
        "stage_bounds": _bounds(points),
        "surfaces": summaries,
        "projectors": projectors,
        "zones": mapper.get("zones", []),
        "landmarks": mapper.get("fixtures", []),
        "paths": mapper.get("paths", []),
        "compositions": draft.editor_metadata.get("compositions", {}),
        "existing_layers": draft.editor_metadata.get("lighting_author_layers", {}),
        "existing_media_layers": draft.editor_metadata.get("timeline_transform_layers", {}),
        "pixel_page": {
            "offset": pixel_offset,
            "limit": pixel_limit,
            "total": total,
            "next_offset": (
                pixel_offset + pixel_limit
                if pixel_limit and pixel_offset + pixel_limit < total
                else None
            ),
            "pixels": detailed,
        },
        "hardware_command_sent": False,
        "preview_budget": {
            "nearest_bilinear": {
                "canvas_width": 512,
                "canvas_height": 512,
                "output_pixels": 4096,
                "layers": 128,
                "target_fps": 30,
            },
            "note": "Targets, not a guarantee; detailed preview reports qualification.",
        },
    }
    if len(json.dumps(result).encode()) > 200 * 1024:
        raise McpAuthoringError("spatial context exceeds the 200 KiB response budget")
    return cast(dict[str, JsonValue], result)


def propose_effect_draft(
    draft: DraftProject,
    mapper: dict[str, object],
    *,
    expected_layout_digest: str,
    composition_ids: list[str],
    effect: SpatialRipple,
    start_ms: int,
    duration_ms: int,
    blend_mode: Literal["replace", "add", "max", "multiply"],
    opacity: float,
) -> DraftProject:
    if expected_layout_digest != layout_digest(mapper):
        raise McpAuthoringError("installation layout changed; fetch spatial context again")
    if (
        not composition_ids
        or len(composition_ids) > 16
        or len(set(composition_ids)) != len(composition_ids)
    ):
        raise McpAuthoringError("select 1..16 distinct lighting compositions")
    document = draft.model_dump(mode="json")
    metadata = document["editor_metadata"]
    compositions = metadata.get("compositions", {})
    layers = metadata.setdefault("lighting_author_layers", {})
    surface_ids = {
        value
        for surface in cast(list[dict[str, object]], mapper.get("lighting_surfaces", []))
        for value in (surface["semantic_id"], surface["resource"])
    }
    for composition_id in composition_ids:
        composition = compositions.get(composition_id, {})
        if (
            composition.get("space_kind") != "lighting_surface"
            or composition.get("target_id") not in surface_ids
        ):
            raise McpAuthoringError("spatial effects require mapped lighting-surface compositions")
        existing = layers.setdefault(composition_id, [])
        if len(existing) >= 128:
            raise McpAuthoringError("lighting composition exceeds the 128 author-layer budget")
        precedence = max((layer["precedence"] for layer in existing), default=-1) + 1
        if precedence > 1000:
            raise McpAuthoringError("make room below precedence 1000 before adding an effect")
        existing.append(
            {
                "id": str(uuid4()),
                "name": "Spatial ripple",
                "mode": "spatial",
                "start_ms": start_ms,
                "duration_ms": duration_ms,
                "persistent": False,
                "precedence": precedence,
                "opacity": opacity,
                "blend_mode": blend_mode,
                "spatial": effect.model_dump(mode="json"),
            }
        )
    metadata["spatial_layout_digest"] = expected_layout_digest
    return DraftProject.model_validate(document)


def lighting_preview_png(
    preview: dict[str, object],
    surface: dict[str, object],
) -> bytes:
    image = Image.new("RGB", (800, 400), "#101018")
    draw = ImageDraw.Draw(image)
    colors = {
        sample["output_index"]: tuple(cast(list[int], sample["color"]))
        for sample in cast(list[dict[str, object]], preview["samples"])
    }
    width, height = (
        float(cast(float, surface["local_width"])),
        float(cast(float, surface["local_height"])),
    )
    for pixel in cast(list[dict[str, object]], surface["pixels"]):
        x = float(cast(float, pixel["local_x"])) / width * 799
        y = float(cast(float, pixel["local_y"])) / height * 399
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=colors[pixel["output_index"]])
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _bounds(points: list[tuple[float, float]]) -> list[float] | None:
    if not points:
        return None
    return [
        min(x for x, _ in points),
        min(y for _, y in points),
        max(x for x, _ in points),
        max(y for _, y in points),
    ]
