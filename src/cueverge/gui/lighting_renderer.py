from dataclasses import dataclass
from itertools import pairwise
from typing import Literal, cast

import numpy as np
from numpy.typing import NDArray

from cueverge.gui.frame_compositor import PreparedVisualLayer, compose_visual_frame
from cueverge.lighting.sink import FrameSink, RenderedFrame

SamplingMode = Literal["nearest", "bilinear", "area"]
FloatCanvas = NDArray[np.float32]


@dataclass(frozen=True)
class LightingPixel:
    output_index: int
    local_x: float
    local_y: float
    footprint_width: float
    footprint_height: float
    color_matrix: tuple[float, ...] = (1, 0, 0, 0, 1, 0, 0, 0, 1)
    gain: tuple[float, float, float] = (1, 1, 1)


@dataclass(frozen=True)
class LightingFrame:
    colors: NDArray[np.uint8]
    output_indices: tuple[int, ...]
    normalized_channel_load: float
    clipped_pixels: int


@dataclass(frozen=True)
class LightingPerformanceBudget:
    profile: Literal["interactive_30fps", "area_preview_10fps"]
    target_fps: int
    maximum_canvas_pixels: int
    maximum_output_pixels: int
    maximum_author_layers: int
    qualified: bool
    exceeded_dimensions: tuple[str, ...]


def assess_lighting_performance_budget(
    *,
    canvas_width: int,
    canvas_height: int,
    output_pixel_count: int,
    author_layer_count: int,
    sampling: SamplingMode,
) -> LightingPerformanceBudget:
    if canvas_width <= 0 or canvas_height <= 0 or output_pixel_count < 0 or author_layer_count < 0:
        raise ValueError("lighting performance dimensions are invalid")
    if sampling == "area":
        profile: Literal["interactive_30fps", "area_preview_10fps"] = "area_preview_10fps"
        target_fps = 10
        maximum_canvas_pixels = 256 * 256
        maximum_output_pixels = 2048
        maximum_author_layers = 64
    else:
        profile = "interactive_30fps"
        target_fps = 30
        maximum_canvas_pixels = 512 * 512
        maximum_output_pixels = 4096
        maximum_author_layers = 128
    exceeded: list[str] = []
    if canvas_width * canvas_height > maximum_canvas_pixels:
        exceeded.append("canvas_pixels")
    if output_pixel_count > maximum_output_pixels:
        exceeded.append("output_pixels")
    if author_layer_count > maximum_author_layers:
        exceeded.append("author_layers")
    return LightingPerformanceBudget(
        profile=profile,
        target_fps=target_fps,
        maximum_canvas_pixels=maximum_canvas_pixels,
        maximum_output_pixels=maximum_output_pixels,
        maximum_author_layers=maximum_author_layers,
        qualified=not exceeded,
        exceeded_dimensions=tuple(exceeded),
    )


def estimate_device_delivered_colors(
    colors: NDArray[np.uint8],
    device_reported_scale: float | None,
) -> NDArray[np.uint8]:
    if colors.ndim != 2 or colors.shape[1] != 3:
        raise ValueError("lighting output colors are invalid")
    if device_reported_scale is None:
        return colors.copy()
    if not 0 <= device_reported_scale <= 1:
        raise ValueError("device-reported output scale is invalid")
    return np.rint(colors.astype(np.float32) * device_reported_scale).astype(np.uint8)


def dispatch_lighting_frame(
    frame: LightingFrame,
    *,
    sink: FrameSink,
    resource: str,
    scene_instance_id: str,
    timestamp_ms: int,
) -> None:
    if not resource or not scene_instance_id or timestamp_ms < 0:
        raise ValueError("lighting dispatch identity is invalid")
    if len(frame.output_indices) != len(frame.colors):
        raise ValueError("lighting frame output order is invalid")
    if tuple(sorted(frame.output_indices)) != frame.output_indices:
        raise ValueError("lighting frame must be in physical output order")
    sink.send(
        RenderedFrame(
            scene_instance_id=scene_instance_id,
            position_ms=timestamp_ms,
            pixels=tuple((int(color[0]), int(color[1]), int(color[2])) for color in frame.colors),
            resource=resource,
        )
    )


def evaluate_composed_lighting_frame(
    *,
    raster_layers: tuple[PreparedVisualLayer, ...],
    author_layers: tuple[dict[str, object], ...],
    timestamp_ms: int,
    canvas_width: int,
    canvas_height: int,
    local_width: float,
    local_height: float,
    pixels: tuple[LightingPixel, ...],
    paths: dict[str, tuple[tuple[float, float], ...]],
    fixtures: dict[str, tuple[tuple[float, float], ...]],
    stage_canvas: FloatCanvas | None = None,
    stage_bounds: tuple[float, float, float, float] | None = None,
    stage_matrix: tuple[float, float, float, float, float, float] | None = None,
    sampling: SamplingMode,
    gamma: float,
    brightness: float,
) -> LightingFrame:
    base = compose_visual_frame(
        raster_layers,
        timestamp_ms=timestamp_ms,
        output_width=canvas_width,
        output_height=canvas_height,
        background_rgb=(0, 0, 0),
    )[0][..., :3]
    if stage_canvas is not None:
        if stage_bounds is None or stage_matrix is None:
            raise ValueError("stage lighting content requires calibrated transform")
        stage_local = map_stage_canvas_to_surface(
            stage_canvas,
            stage_bounds=stage_bounds,
            stage_matrix=stage_matrix,
            local_width=local_width,
            local_height=local_height,
            output_width=canvas_width,
            output_height=canvas_height,
        )
        base = np.maximum(base, stage_local)
    canvas = render_lighting_layers(
        layers=author_layers,
        timestamp_ms=timestamp_ms,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        paths=paths,
        fixtures=fixtures,
        base_canvas=base,
    )
    return evaluate_lighting_canvas(
        canvas,
        local_width=local_width,
        local_height=local_height,
        pixels=pixels,
        sampling=sampling,
        gamma=gamma,
        brightness=brightness,
    )


def map_stage_canvas_to_surface(
    stage_canvas: FloatCanvas,
    *,
    stage_bounds: tuple[float, float, float, float],
    stage_matrix: tuple[float, float, float, float, float, float],
    local_width: float,
    local_height: float,
    output_width: int,
    output_height: int,
) -> FloatCanvas:
    if (
        stage_canvas.ndim != 3
        or stage_canvas.shape[2] != 3
        or local_width <= 0
        or local_height <= 0
        or output_width <= 0
        or output_height <= 0
    ):
        raise ValueError("stage-to-surface mapping settings are invalid")
    minimum_x, minimum_y, maximum_x, maximum_y = stage_bounds
    if maximum_x <= minimum_x or maximum_y <= minimum_y:
        raise ValueError("stage bounds are invalid")
    a, b, c, d, e, f = stage_matrix
    determinant = a * d - b * c
    if abs(determinant) < 1e-12:
        raise ValueError("lighting surface stage transform is singular")
    local_x, local_y = np.meshgrid(
        (np.arange(output_width, dtype=np.float32) + 0.5) / output_width * local_width,
        (np.arange(output_height, dtype=np.float32) + 0.5) / output_height * local_height,
    )
    stage_x = a * local_x + c * local_y + e
    stage_y = b * local_x + d * local_y + f
    normalized_x = (stage_x - minimum_x) / (maximum_x - minimum_x)
    normalized_y = (stage_y - minimum_y) / (maximum_y - minimum_y)
    output = np.zeros((output_height, output_width, 3), dtype=np.float32)
    inside = (normalized_x >= 0) & (normalized_x <= 1) & (normalized_y >= 0) & (normalized_y <= 1)
    if not np.any(inside):
        return output
    source_height, source_width, _ = stage_canvas.shape
    source_x = normalized_x[inside] * (source_width - 1)
    source_y = normalized_y[inside] * (source_height - 1)
    x0 = np.floor(source_x).astype(np.intp)
    y0 = np.floor(source_y).astype(np.intp)
    x1 = np.minimum(x0 + 1, source_width - 1)
    y1 = np.minimum(y0 + 1, source_height - 1)
    weight_x = (source_x - x0)[..., None]
    weight_y = (source_y - y0)[..., None]
    top = stage_canvas[y0, x0] * (1 - weight_x) + stage_canvas[y0, x1] * weight_x
    bottom = stage_canvas[y1, x0] * (1 - weight_x) + stage_canvas[y1, x1] * weight_x
    output[inside] = top * (1 - weight_y) + bottom * weight_y
    return output


def render_lighting_layers(
    *,
    layers: tuple[dict[str, object], ...],
    timestamp_ms: int,
    canvas_width: int,
    canvas_height: int,
    paths: dict[str, tuple[tuple[float, float], ...]] | None = None,
    fixtures: dict[str, tuple[tuple[float, float], ...]] | None = None,
    base_canvas: FloatCanvas | None = None,
) -> FloatCanvas:
    if canvas_width <= 0 or canvas_height <= 0:
        raise ValueError("lighting canvas dimensions must be positive")
    if base_canvas is not None and base_canvas.shape != (
        canvas_height,
        canvas_width,
        3,
    ):
        raise ValueError("base lighting canvas dimensions are invalid")
    canvas = (
        base_canvas.astype(np.float32, copy=True)
        if base_canvas is not None
        else np.zeros((canvas_height, canvas_width, 3), dtype=np.float32)
    )
    for layer in sorted(
        layers,
        key=lambda item: (_integer(item.get("precedence")), _string(item.get("id"))),
    ):
        start = _integer(layer.get("start_ms"))
        duration = _integer(layer.get("duration_ms"))
        persistent = layer.get("persistent", False)
        if not isinstance(persistent, bool):
            raise ValueError("lighting persistent-look flag is invalid")
        if not persistent and (timestamp_ms < start or timestamp_ms >= start + duration):
            continue
        progress = (timestamp_ms - start) / duration
        mode = _string(layer.get("mode"))
        if mode == "spatial":
            source = _render_spatial(
                _mapping(layer.get("spatial")),
                progress,
                canvas_width,
                canvas_height,
            )
        elif mode == "path":
            source = _render_path(
                _mapping(layer.get("path")),
                progress,
                canvas_width,
                canvas_height,
                paths or {},
            )
        elif mode == "fixture":
            source = _render_fixture(
                _mapping(layer.get("fixture")),
                progress,
                canvas_width,
                canvas_height,
                fixtures or {},
            )
        else:
            raise ValueError("lighting author mode is unsupported")
        opacity = np.float32(_number(layer.get("opacity")))
        canvas = _blend(
            canvas,
            source * opacity,
            _string(layer.get("blend_mode")),
            opacity,
        )
    return np.clip(canvas, 0, 1).astype(np.float32, copy=False)


def evaluate_lighting_canvas(
    canvas: FloatCanvas,
    *,
    local_width: float,
    local_height: float,
    pixels: tuple[LightingPixel, ...],
    sampling: SamplingMode,
    gamma: float,
    brightness: float,
) -> LightingFrame:
    if (
        canvas.ndim != 3
        or canvas.shape[2] != 3
        or local_width <= 0
        or local_height <= 0
        or gamma <= 0
        or brightness < 0
        or brightness > 1
    ):
        raise ValueError("lighting evaluation settings are invalid")
    if len({pixel.output_index for pixel in pixels}) != len(pixels):
        raise ValueError("lighting output indices must be unique")
    logical: list[tuple[int, NDArray[np.float32]]] = []
    clipped_pixels = 0
    for pixel in pixels:
        sampled = _sample(
            canvas,
            x=pixel.local_x / local_width,
            y=pixel.local_y / local_height,
            footprint_width=pixel.footprint_width / local_width,
            footprint_height=pixel.footprint_height / local_height,
            mode=sampling,
        )
        if len(pixel.color_matrix) != 9:
            raise ValueError("pixel color matrix must contain nine values")
        matrix = np.asarray(pixel.color_matrix, dtype=np.float32).reshape(3, 3)
        corrected = matrix @ sampled
        corrected *= np.asarray(pixel.gain, dtype=np.float32)
        if np.any(corrected > 1) or np.any(corrected < 0):
            clipped_pixels += 1
        corrected = np.power(np.clip(corrected, 0, 1), np.float32(1 / gamma))
        corrected *= np.float32(brightness)
        logical.append(
            (
                pixel.output_index,
                np.clip(corrected, 0, 1).astype(np.float32, copy=False),
            )
        )
    ordered = sorted(logical, key=lambda item: item[0])
    linear = (
        np.stack([item[1] for item in ordered]) if ordered else np.zeros((0, 3), dtype=np.float32)
    )
    return LightingFrame(
        colors=np.rint(linear * 255).astype(np.uint8),
        output_indices=tuple(item[0] for item in ordered),
        normalized_channel_load=float(np.sum(linear)),
        clipped_pixels=clipped_pixels,
    )


def _sample(
    canvas: FloatCanvas,
    *,
    x: float,
    y: float,
    footprint_width: float,
    footprint_height: float,
    mode: SamplingMode,
) -> NDArray[np.float32]:
    height, width, _ = canvas.shape
    normalized_x = float(np.clip(x, 0, 1))
    normalized_y = float(np.clip(y, 0, 1))
    if mode == "nearest":
        column = round(normalized_x * (width - 1))
        row = round(normalized_y * (height - 1))
        return cast(
            NDArray[np.float32],
            canvas[row, column].astype(np.float32, copy=False),
        )
    if mode == "bilinear":
        return _bilinear(canvas, normalized_x, normalized_y)
    if mode != "area":
        raise ValueError("lighting sampling mode is unsupported")
    half_width = max(footprint_width, 1 / width) / 2
    half_height = max(footprint_height, 1 / height) / 2
    columns = np.arange(width, dtype=np.float32)
    rows = np.arange(height, dtype=np.float32)
    sample_x = (columns + np.float32(0.5)) / width
    sample_y = (rows + np.float32(0.5)) / height
    selected_columns = np.abs(sample_x - normalized_x) <= half_width
    selected_rows = np.abs(sample_y - normalized_y) <= half_height
    region = canvas[np.ix_(selected_rows, selected_columns)]
    if not region.size:
        return _bilinear(canvas, normalized_x, normalized_y)
    return np.mean(region, axis=(0, 1), dtype=np.float32)


def _bilinear(
    canvas: FloatCanvas,
    x: float,
    y: float,
) -> NDArray[np.float32]:
    height, width, _ = canvas.shape
    source_x = np.float32(x * (width - 1))
    source_y = np.float32(y * (height - 1))
    x0 = int(np.floor(source_x))
    y0 = int(np.floor(source_y))
    x1 = min(x0 + 1, width - 1)
    y1 = min(y0 + 1, height - 1)
    weight_x = source_x - x0
    weight_y = source_y - y0
    top = canvas[y0, x0] * (1 - weight_x) + canvas[y0, x1] * weight_x
    bottom = canvas[y1, x0] * (1 - weight_x) + canvas[y1, x1] * weight_x
    return cast(
        NDArray[np.float32],
        (top * (1 - weight_y) + bottom * weight_y).astype(
            np.float32,
            copy=False,
        ),
    )


def _render_spatial(
    payload: dict[str, object],
    progress: float,
    width: int,
    height: int,
) -> FloatCanvas:
    primitive = _string(payload.get("primitive"))
    first = np.asarray(_color(payload.get("color")), dtype=np.float32)
    second = np.asarray(
        _color(payload.get("end_color", payload.get("color"))),
        dtype=np.float32,
    )
    raw_x, raw_y = np.meshgrid(
        (np.arange(width, dtype=np.float32) + 0.5) / width,
        (np.arange(height, dtype=np.float32) + 0.5) / height,
    )
    x = raw_x.astype(np.float32, copy=False)
    y = raw_y.astype(np.float32, copy=False)
    if primitive == "solid":
        return np.broadcast_to(first, (height, width, 3)).copy()
    if primitive == "linear_gradient":
        amount = np.clip(x + np.float32(progress), 0, 1)[..., None]
        return cast(
            FloatCanvas,
            (first * (np.float32(1) - amount) + second * amount).astype(
                np.float32,
                copy=False,
            ),
        )
    if primitive == "radial_gradient":
        center = _pair(payload.get("position", [0.5, 0.5]))
        radius = max(_number(payload.get("radius", 0.5)), 1e-6)
        amount = np.clip(
            np.hypot(x - center[0], y - center[1]) / radius,
            0,
            1,
        )[..., None]
        return cast(
            FloatCanvas,
            (first * (np.float32(1) - amount) + second * amount).astype(
                np.float32,
                copy=False,
            ),
        )
    if primitive == "shape":
        center = _pair(payload.get("position", [0.5, 0.5]))
        center = ((center[0] + progress) % 1, center[1])
        size = _pair(payload.get("size", [0.25, 0.25]))
        mask = (np.abs(x - center[0]) <= size[0] / 2) & (np.abs(y - center[1]) <= size[1] / 2)
        output = np.zeros((height, width, 3), dtype=np.float32)
        output[mask] = first
        return output
    if primitive == "procedural_noise":
        seed = _integer(payload.get("seed")) + int(progress * 1_000_000)
        random = np.random.default_rng(seed)
        amount = random.random((height, width, 1), dtype=np.float32)
        return cast(
            FloatCanvas,
            (first * (np.float32(1) - amount) + second * amount).astype(
                np.float32,
                copy=False,
            ),
        )
    raise ValueError("spatial lighting primitive is unsupported")


def _render_path(
    payload: dict[str, object],
    progress: float,
    width: int,
    height: int,
    paths: dict[str, tuple[tuple[float, float], ...]],
) -> FloatCanvas:
    path_id = _string(payload.get("path_id"))
    points = paths.get(path_id)
    if points is None or len(points) < 2:
        raise ValueError(f"lighting path {path_id} is unavailable")
    direction = _string(payload.get("direction"))
    normalized_position = _number(payload.get("position", 0)) + progress
    normalized_position %= 1
    if direction == "reverse":
        normalized_position = 1 - normalized_position
    center = _point_along_path(points, normalized_position)
    path_width = max(_number(payload.get("width", 0.1)), 1e-6)
    x, y = np.meshgrid(
        (np.arange(width, dtype=np.float32) + 0.5) / width,
        (np.arange(height, dtype=np.float32) + 0.5) / height,
    )
    distance = np.hypot(x - center[0], y - center[1])
    mask = distance <= path_width / 2
    color = np.asarray(_color(payload.get("color")), dtype=np.float32)
    output = np.zeros((height, width, 3), dtype=np.float32)
    output[mask] = color
    return output


def _render_fixture(
    payload: dict[str, object],
    progress: float,
    width: int,
    height: int,
    fixtures: dict[str, tuple[tuple[float, float], ...]],
) -> FloatCanvas:
    target_id = _string(payload.get("target_id"))
    points = fixtures.get(target_id)
    if not points:
        raise ValueError(f"lighting fixture/group {target_id} is unavailable")
    primitive = _string(payload.get("primitive"))
    brightness = _number(payload.get("brightness", 1))
    if primitive == "pulse":
        brightness *= float(0.5 + 0.5 * np.sin(np.float32(progress * np.pi * 2)))
    color = (
        np.asarray(_color(payload.get("color")), dtype=np.float32) * np.float32(brightness)
    ).astype(np.float32, copy=False)
    output = np.zeros((height, width, 3), dtype=np.float32)
    for point_x, point_y in points:
        column = min(width - 1, max(0, round(point_x * (width - 1))))
        row = min(height - 1, max(0, round(point_y * (height - 1))))
        output[row, column] = color
    return output


def _blend(
    destination: FloatCanvas,
    source: FloatCanvas,
    mode: str,
    opacity: np.float32,
) -> FloatCanvas:
    if mode == "replace":
        return destination * (1 - opacity) + source
    if mode == "add":
        return destination + source
    if mode == "max":
        return np.maximum(destination, source)
    if mode == "multiply":
        return destination * (1 - opacity + source)
    raise ValueError("lighting blend mode is unsupported")


def _point_along_path(
    points: tuple[tuple[float, float], ...],
    position: float,
) -> tuple[float, float]:
    lengths = [
        float(np.hypot(right[0] - left[0], right[1] - left[1])) for left, right in pairwise(points)
    ]
    total = sum(lengths)
    if total <= 0:
        raise ValueError("lighting path has zero length")
    target = position * total
    traversed = 0.0
    for index, length in enumerate(lengths):
        if traversed + length >= target:
            amount = (target - traversed) / length
            left = points[index]
            right = points[index + 1]
            return (
                left[0] + (right[0] - left[0]) * amount,
                left[1] + (right[1] - left[1]) * amount,
            )
        traversed += length
    return points[-1]


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("lighting layer string is invalid")
    return value


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("lighting layer number is invalid")
    return float(value)


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("lighting layer integer is invalid")
    return value


def _pair(value: object) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("lighting layer pair is invalid")
    return _number(value[0]), _number(value[1])


def _color(value: object) -> tuple[float, float, float]:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        raise ValueError("lighting layer color is invalid")
    try:
        return (
            int(value[1:3], 16) / 255,
            int(value[3:5], 16) / 255,
            int(value[5:7], 16) / 255,
        )
    except ValueError as error:
        raise ValueError("lighting layer color is invalid") from error
