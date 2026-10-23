import base64
from collections import Counter
from dataclasses import replace
from io import BytesIO
from typing import cast

import numpy as np
from PIL import Image

from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject
from cueverge.gui.frame_compositor import scale_layer_geometry
from cueverge.gui.lighting_renderer import (
    LightingPixel,
    SamplingMode,
    assess_lighting_performance_budget,
    estimate_device_delivered_colors,
    evaluate_composed_lighting_frame,
)
from cueverge.gui.projector_renderer import PreparedMedia, prepare_media_layers


class LightingPreviewError(RuntimeError):
    pass


def evaluate_lighting_preview(
    *,
    project_id: str,
    draft: DraftProject,
    composition_id: str,
    timestamp_ms: int,
    assets: AssetUploadService,
    mapper_export: dict[str, object],
    device_reported_scale: float | None = None,
    prepared_media: PreparedMedia | None = None,
    include_canvas: bool = False,
) -> dict[str, object]:
    metadata = cast(dict[str, object], draft.editor_metadata)
    composition = _mapping(_mapping(metadata.get("compositions")).get(composition_id))
    if composition.get("space_kind") != "lighting_surface":
        raise LightingPreviewError("lighting preview requires lighting-surface composition space")
    duration_ms = _integer(composition.get("duration_ms"))
    if timestamp_ms < 0 or timestamp_ms >= duration_ms:
        raise LightingPreviewError("lighting preview timestamp is outside duration")
    settings = _mapping(composition.get("lighting_settings"))
    surface = resolve_lighting_preview_surface(
        draft=draft,
        composition_id=composition_id,
        mapper_export=mapper_export,
    )
    local_width = _number(surface.get("local_width"))
    local_height = _number(surface.get("local_height"))
    canvas_width = _integer(settings.get("canvas_width"))
    canvas_height = _integer(settings.get("canvas_height"))
    raw_raster = _mapping(metadata.get("timeline_transform_layers")).get(composition_id, [])
    if not isinstance(raw_raster, list):
        raise LightingPreviewError("lighting raster layers are invalid")
    prepared = prepared_media or prepare_media_layers(
        project_id=project_id,
        raw_layers=raw_raster,
        assets=assets,
    )
    raster_layers = tuple(
        scale_layer_geometry(
            replace(
                layer,
                keyframes=_scale_keyframes(
                    layer.keyframes,
                    canvas_width / local_width,
                    canvas_height / local_height,
                ),
            ),
            canvas_width / local_width,
            canvas_height / local_height,
        )
        for layer in prepared.visual_layers
    )
    author_layers = _mapping(metadata.get("lighting_author_layers")).get(composition_id, [])
    if not isinstance(author_layers, list):
        raise LightingPreviewError("lighting author layers are invalid")
    stage_matrix = _matrix(surface.get("stage_matrix"))
    paths = {
        _string(item.get("semantic_id")): stage_points_to_surface(
            tuple(_pair(point) for point in _list(item.get("stage_points"))),
            stage_matrix=stage_matrix,
            local_width=local_width,
            local_height=local_height,
        )
        for item in (_mapping(value) for value in _list(mapper_export.get("paths")))
    }
    fixture_points = {
        _string(item.get("semantic_id")): stage_points_to_surface(
            tuple(_pair(point) for point in _list(item.get("stage_points"))),
            stage_matrix=stage_matrix,
            local_width=local_width,
            local_height=local_height,
        )
        for item in (_mapping(value) for value in _list(mapper_export.get("fixtures")))
    }
    pixels = tuple(
        LightingPixel(
            output_index=_integer(pixel.get("output_index")),
            local_x=_number(pixel.get("local_x")),
            local_y=_number(pixel.get("local_y")),
            footprint_width=_number(pixel.get("footprint_width")),
            footprint_height=_number(pixel.get("footprint_height")),
        )
        for pixel in (_mapping(value) for value in _list(surface.get("pixels")))
    )
    output_indices = [pixel.output_index for pixel in pixels]
    output_index_counts = Counter(output_indices)
    duplicate_output_indices = sorted(
        output_index for output_index, count in output_index_counts.items() if count > 1
    )
    unmapped_pixels = sum(
        pixel.local_x < 0
        or pixel.local_x > local_width
        or pixel.local_y < 0
        or pixel.local_y > local_height
        for pixel in pixels
    )
    try:
        sampling = _sampling(settings.get("sampling"))
        frame = evaluate_composed_lighting_frame(
            raster_layers=raster_layers,
            author_layers=tuple(_mapping(item) for item in author_layers),
            timestamp_ms=timestamp_ms,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            local_width=local_width,
            local_height=local_height,
            pixels=pixels,
            paths=paths,
            fixtures=fixture_points,
            stage_matrix=stage_matrix,
            sampling=sampling,
            gamma=_number(settings.get("gamma")),
            brightness=_number(settings.get("brightness"))
            * _number(surface.get("brightness_limit")),
        )
        delivered_colors = estimate_device_delivered_colors(
            frame.colors,
            device_reported_scale,
        )
    except ValueError as error:
        raise LightingPreviewError(str(error)) from error
    budget = assess_lighting_performance_budget(
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        output_pixel_count=len(pixels),
        author_layer_count=len(author_layers) + len(raster_layers),
        sampling=sampling,
    )
    result: dict[str, object] = {
        "composition_id": composition_id,
        "timestamp_ms": timestamp_ms,
        "surface_semantic_id": surface.get("semantic_id"),
        "surface_resource": surface.get("resource"),
        "sampling": settings.get("sampling"),
        "normalized_channel_load": frame.normalized_channel_load,
        "clipped_pixels": frame.clipped_pixels,
        "diagnostics": {
            "mapping_revision": surface.get("mapping_revision"),
            "calibration_id": surface.get("calibration_id"),
            "mapping_stale": surface.get("calibration_id") is None,
            "unmapped_pixels": unmapped_pixels,
            "duplicate_output_indices": duplicate_output_indices,
            "device_reported_scale": device_reported_scale,
        },
        "output_boundary": surface.get("local_points", []),
        "performance_budget": {
            "profile": budget.profile,
            "target_fps": budget.target_fps,
            "maximum_canvas_pixels": budget.maximum_canvas_pixels,
            "maximum_output_pixels": budget.maximum_output_pixels,
            "maximum_author_layers": budget.maximum_author_layers,
            "qualified": budget.qualified,
            "exceeded_dimensions": list(budget.exceeded_dimensions),
        },
        "samples": [
            {
                "output_index": output_index,
                "requested_color": color.tolist(),
                "color": delivered_color.tolist(),
            }
            for output_index, color, delivered_color in zip(
                frame.output_indices,
                frame.colors,
                delivered_colors,
                strict=True,
            )
        ],
        "hardware_command_sent": False,
    }
    if include_canvas:
        if frame.canvas is None:
            raise LightingPreviewError("lighting canvas is unavailable")
        output = BytesIO()
        Image.fromarray(np.rint(np.clip(frame.canvas, 0, 1) * 255).astype(np.uint8)).save(
            output,
            format="PNG",
        )
        result["canvas_png_base64"] = base64.b64encode(output.getvalue()).decode("ascii")
    return result


def resolve_lighting_preview_surface(
    *,
    draft: DraftProject,
    composition_id: str,
    mapper_export: dict[str, object],
) -> dict[str, object]:
    metadata = cast(dict[str, object], draft.editor_metadata)
    composition = _mapping(_mapping(metadata.get("compositions")).get(composition_id))
    target_id = _string(composition.get("target_id"))
    surface = next(
        (
            item
            for item in (_mapping(value) for value in _list(mapper_export.get("lighting_surfaces")))
            if item.get("semantic_id") == target_id or item.get("resource") == target_id
        ),
        None,
    )
    if surface is None:
        raise LightingPreviewError("lighting preview surface is not mapped")
    return surface


def _scale_keyframes(
    keyframes: tuple[dict[str, object], ...],
    scale_x: float,
    scale_y: float,
) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for keyframe in keyframes:
        value = dict(keyframe)
        position = _pair(value.get("position"))
        value["position"] = [position[0] * scale_x, position[1] * scale_y]
        result.append(value)
    return tuple(result)


def _stage_to_local(
    point: tuple[float, float],
    matrix: tuple[float, float, float, float, float, float],
    local_width: float,
    local_height: float,
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    determinant = a * d - b * c
    if abs(determinant) < 1e-12:
        raise LightingPreviewError("lighting stage transform is singular")
    translated_x = point[0] - e
    translated_y = point[1] - f
    local_x = (d * translated_x - c * translated_y) / determinant
    local_y = (-b * translated_x + a * translated_y) / determinant
    return local_x / local_width, local_y / local_height


def stage_points_to_surface(
    points: tuple[tuple[float, float], ...],
    *,
    stage_matrix: tuple[float, float, float, float, float, float],
    local_width: float,
    local_height: float,
) -> tuple[tuple[float, float], ...]:
    return tuple(
        _stage_to_local(point, stage_matrix, local_width, local_height) for point in points
    )


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise LightingPreviewError("lighting preview string is invalid")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise LightingPreviewError("lighting preview integer is invalid")
    return value


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise LightingPreviewError("lighting preview number is invalid")
    return float(value)


def _pair(value: object) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise LightingPreviewError("lighting preview point is invalid")
    return _number(value[0]), _number(value[1])


def _matrix(
    value: object,
) -> tuple[float, float, float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise LightingPreviewError("lighting preview matrix is invalid")
    return cast(
        tuple[float, float, float, float, float, float],
        tuple(_number(item) for item in value),
    )


def _sampling(value: object) -> SamplingMode:
    if value not in {"nearest", "bilinear", "area"}:
        raise LightingPreviewError("lighting sampling mode is invalid")
    return cast(SamplingMode, value)
