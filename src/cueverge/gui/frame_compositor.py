from bisect import bisect_right
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import pairwise
from math import cos, radians, sin
from typing import Literal, cast

import av
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageSequence

AlphaInterpretation = Literal["opaque", "straight", "premultiplied"]
SourceColorSpace = Literal["srgb", "bt709", "linear_srgb"]
FloatImage = NDArray[np.float32]
ByteImage = NDArray[np.uint8]


@dataclass(frozen=True)
class AudioLayerBuffer:
    samples: NDArray[np.float32]
    start_sample: int
    gain_db: float
    mute: bool
    solo: bool
    mix_order: int
    fade_in_samples: int
    fade_out_samples: int


@dataclass(frozen=True)
class TimedVideoFrame:
    pts_ms: int
    image: FloatImage


@dataclass(frozen=True)
class VideoFrameSource:
    path: str
    duration_ms: int
    alpha_interpretation: AlphaInterpretation
    source_color_space: SourceColorSpace

    def frame_at(self, timestamp_ms: int) -> FloatImage:
        timestamp_ms = min(timestamp_ms, self.duration_ms - 1)
        with av.open(self.path, mode="r") as container:
            stream = container.streams.video[0]
            if stream.time_base is None:
                raise ValueError("video stream has no time base")
            container.seek(
                int(timestamp_ms / 1000 / stream.time_base),
                stream=stream,
                backward=True,
                any_frame=False,
            )
            selected: av.VideoFrame | None = None
            for frame in container.decode(stream):
                if not isinstance(frame, av.VideoFrame) or frame.pts is None:
                    raise ValueError("video frame has no presentation timestamp")
                if round(float(frame.pts * stream.time_base) * 1000) > timestamp_ms:
                    break
                selected = frame
            if selected is None:
                raise ValueError("video timestamp precedes the first decodable frame")
            if selected.width * selected.height * 16 > 256 * 1024 * 1024:
                raise ValueError("video frame exceeds the 256 MiB decode budget")
            return decode_video_frame(
                selected,
                alpha_interpretation=self.alpha_interpretation,
                source_color_space=self.source_color_space,
            )


def prepare_video_source(
    path: str,
    *,
    alpha_interpretation: AlphaInterpretation,
    source_color_space: SourceColorSpace,
) -> VideoFrameSource:
    with av.open(path, mode="r") as container:
        if not container.streams.video:
            raise ValueError("video source has no video stream")
        stream = container.streams.video[0]
        if stream.duration is not None and stream.time_base is not None:
            duration_ms = round(float(stream.duration * stream.time_base) * 1000)
        elif container.duration is not None:
            duration_ms = round(container.duration / 1000)
        else:
            raise ValueError("video source has no inspected duration")
    if duration_ms <= 0:
        raise ValueError("video source duration must be positive")
    return VideoFrameSource(path, duration_ms, alpha_interpretation, source_color_space)


@dataclass(frozen=True)
class PreparedVisualLayer:
    id: str
    source: FloatImage | tuple[TimedVideoFrame, ...] | VideoFrameSource
    source_kind: Literal["image", "video"]
    start_ms: int
    duration_ms: int
    in_point_ms: int
    z_index: int
    fade_in_ms: int
    fade_out_ms: int
    keyframes: tuple[dict[str, object], ...]
    playback: Literal["hold", "loop", "once"] = "hold"
    source_duration_ms: int | None = None
    corner_pin: tuple[tuple[float, float], ...] | None = None
    mask: "LayerMask | None" = None


@dataclass(frozen=True)
class LayerMask:
    points: tuple[tuple[float, float], ...]
    feather: float = 0.0
    invert: bool = False


def scale_layer_geometry(
    layer: PreparedVisualLayer,
    scale_x: float,
    scale_y: float,
) -> PreparedVisualLayer:
    """Scale logical-space corner pins and masks into output raster pixels."""
    return replace(
        layer,
        corner_pin=(
            tuple((x * scale_x, y * scale_y) for x, y in layer.corner_pin)
            if layer.corner_pin is not None
            else None
        ),
        mask=(
            LayerMask(
                points=tuple((x * scale_x, y * scale_y) for x, y in layer.mask.points),
                feather=layer.mask.feather * (abs(scale_x) + abs(scale_y)) / 2,
                invert=layer.mask.invert,
            )
            if layer.mask is not None
            else None
        ),
    )


def decode_animated_image(
    path: str,
    *,
    alpha_interpretation: AlphaInterpretation,
    source_color_space: SourceColorSpace,
) -> tuple[tuple[TimedVideoFrame, ...], int]:
    frames: list[TimedVideoFrame] = []
    timestamp_ms = 0
    decoded_bytes = 0
    with Image.open(path) as image:
        for frame in ImageSequence.Iterator(image):
            decoded_bytes += frame.width * frame.height * 16
            if decoded_bytes > 256 * 1024 * 1024:
                raise ValueError("animated image exceeds the 256 MiB decoded frame budget")
            duration = frame.info.get("duration")
            if not isinstance(duration, int) or duration <= 0:
                raise ValueError("animated image frames require positive durations")
            frames.append(
                TimedVideoFrame(
                    pts_ms=timestamp_ms,
                    image=to_working_rgba(
                        np.asarray(frame.convert("RGBA"), dtype=np.uint8),
                        alpha_interpretation=alpha_interpretation,
                        source_color_space=source_color_space,
                    ),
                )
            )
            timestamp_ms += duration
    return tuple(frames), timestamp_ms


@dataclass(frozen=True)
class EvaluatedTransform:
    position: tuple[float, float]
    scale: tuple[float, float]
    rotation_degrees: float
    anchor: tuple[float, float]
    opacity: float
    crop: tuple[float, float, float, float]


def decode_still(
    path: str,
    *,
    alpha_interpretation: AlphaInterpretation,
    source_color_space: SourceColorSpace,
) -> FloatImage:
    with Image.open(path) as image:
        if image.width * image.height * 16 > 256 * 1024 * 1024:
            raise ValueError("image exceeds the 256 MiB decoded frame budget")
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    return to_working_rgba(
        rgba,
        alpha_interpretation=alpha_interpretation,
        source_color_space=source_color_space,
    )


def decode_video_frame(
    frame: av.VideoFrame,
    *,
    alpha_interpretation: AlphaInterpretation,
    source_color_space: SourceColorSpace,
) -> FloatImage:
    rgba = cast(ByteImage, frame.to_ndarray(format="rgba"))
    return to_working_rgba(
        rgba,
        alpha_interpretation=alpha_interpretation,
        source_color_space=source_color_space,
    )


def decode_video_frames(
    path: str,
    *,
    alpha_interpretation: AlphaInterpretation,
    source_color_space: SourceColorSpace,
) -> tuple[TimedVideoFrame, ...]:
    decoded: list[TimedVideoFrame] = []
    decoded_bytes = 0
    with av.open(path, mode="r") as container:
        video_streams = [stream for stream in container.streams if stream.type == "video"]
        if not video_streams:
            raise ValueError("video source has no video stream")
        stream = video_streams[0]
        if stream.time_base is None:
            raise ValueError("video stream has no time base")
        for frame in container.decode(stream):
            if not isinstance(frame, av.VideoFrame):
                raise ValueError("video stream yielded a non-video frame")
            if frame.pts is None:
                raise ValueError("decoded video frame has no presentation timestamp")
            decoded_bytes += frame.width * frame.height * 16
            if decoded_bytes > 256 * 1024 * 1024:
                raise ValueError(
                    "video exceeds the 256 MiB decoded frame budget; use a smaller proxy"
                )
            decoded.append(
                TimedVideoFrame(
                    pts_ms=round(float(frame.pts * stream.time_base) * 1000),
                    image=decode_video_frame(
                        frame,
                        alpha_interpretation=alpha_interpretation,
                        source_color_space=source_color_space,
                    ),
                )
            )
    if not decoded:
        raise ValueError("video source decoded no frames")
    if any(right.pts_ms <= left.pts_ms for left, right in pairwise(decoded)):
        raise ValueError("video frame timestamps must be strictly increasing")
    return tuple(decoded)


def decode_audio_stereo_48k(path: str) -> NDArray[np.float32]:
    chunks: list[NDArray[np.float32]] = []
    with av.open(path, mode="r") as container:
        streams = [stream for stream in container.streams if stream.type == "audio"]
        if not streams:
            raise ValueError("audio source has no audio stream")
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
        for decoded in container.decode(streams[0]):
            if not isinstance(decoded, av.AudioFrame):
                raise ValueError("audio stream yielded a non-audio frame")
            for frame in resampler.resample(decoded):
                array = frame.to_ndarray()
                if array.ndim != 2 or array.shape[0] != 2:
                    raise ValueError("resampled audio is not planar stereo")
                chunks.append(
                    cast(
                        NDArray[np.float32],
                        array.astype(np.float32, copy=False).T,
                    )
                )
        for frame in resampler.resample(None):
            array = frame.to_ndarray()
            chunks.append(
                cast(
                    NDArray[np.float32],
                    array.astype(np.float32, copy=False).T,
                )
            )
    if not chunks:
        raise ValueError("audio source decoded no samples")
    return np.concatenate(chunks, axis=0).astype(np.float32, copy=False)


def select_video_frame(
    frames: tuple[TimedVideoFrame, ...],
    timestamp_ms: int,
) -> TimedVideoFrame:
    if not frames:
        raise ValueError("video frame sequence is empty")
    index = bisect_right([frame.pts_ms for frame in frames], timestamp_ms) - 1
    if index < 0:
        raise ValueError("composition timestamp precedes the first video frame")
    return frames[index]


def evaluate_transform(
    keyframes: tuple[dict[str, object], ...],
    timestamp_ms: int,
) -> EvaluatedTransform:
    if not keyframes:
        raise ValueError("visual layer requires transform keyframes")
    ordered = tuple(sorted(keyframes, key=lambda item: _integer(item.get("time_ms"))))
    if timestamp_ms <= _integer(ordered[0].get("time_ms")):
        return _transform_from_keyframe(ordered[0])
    if timestamp_ms >= _integer(ordered[-1].get("time_ms")):
        return _transform_from_keyframe(ordered[-1])
    for left, right in pairwise(ordered):
        left_time = _integer(left.get("time_ms"))
        right_time = _integer(right.get("time_ms"))
        if left_time <= timestamp_ms <= right_time:
            interpolation = _mapping(left.get("interpolation"))
            kind = interpolation.get("kind")
            if kind == "hold":
                return _transform_from_keyframe(left)
            progress = (timestamp_ms - left_time) / (right_time - left_time)
            temporal_progress = _temporal_progress(progress, interpolation)
            left_transform = _transform_from_keyframe(left)
            right_transform = _transform_from_keyframe(right)
            position = _position_progress(
                left_transform.position,
                right_transform.position,
                temporal_progress,
                interpolation,
                _mapping(right.get("interpolation")),
            )
            return EvaluatedTransform(
                position=position,
                scale=_interpolate_pair(
                    left_transform.scale, right_transform.scale, temporal_progress
                ),
                rotation_degrees=_lerp(
                    left_transform.rotation_degrees,
                    right_transform.rotation_degrees,
                    temporal_progress,
                ),
                anchor=_interpolate_pair(
                    left_transform.anchor, right_transform.anchor, temporal_progress
                ),
                opacity=_lerp(left_transform.opacity, right_transform.opacity, temporal_progress),
                crop=cast(
                    tuple[float, float, float, float],
                    tuple(
                        _lerp(left_value, right_value, temporal_progress)
                        for left_value, right_value in zip(
                            left_transform.crop,
                            right_transform.crop,
                            strict=True,
                        )
                    ),
                ),
            )
    raise ValueError("transform timestamp could not be evaluated")


def render_transformed_layer(
    source: FloatImage,
    *,
    output_width: int,
    output_height: int,
    transform: EvaluatedTransform,
    fade_opacity: float = 1.0,
) -> FloatImage:
    if source.ndim != 3 or source.shape[2] != 4:
        raise ValueError("visual source must be HxWx4 RGBA")
    if output_width <= 0 or output_height <= 0:
        raise ValueError("visual output dimensions must be positive")
    if transform.scale[0] == 0 or transform.scale[1] == 0:
        raise ValueError("visual scale cannot be zero")
    left, top, right, bottom = transform.crop
    if (
        any(value < 0 or value > 1 for value in transform.crop)
        or left + right >= 1
        or top + bottom >= 1
    ):
        raise ValueError("visual crop is invalid")
    output = np.zeros((output_height, output_width, 4), dtype=np.float32)
    raw_x, raw_y = np.meshgrid(
        np.arange(output_width, dtype=np.float32) + np.float32(0.5),
        np.arange(output_height, dtype=np.float32) + np.float32(0.5),
    )
    grid_x = raw_x.astype(np.float32, copy=False)
    grid_y = raw_y.astype(np.float32, copy=False)
    translated_x = grid_x - np.float32(transform.position[0])
    translated_y = grid_y - np.float32(transform.position[1])
    angle = radians(-transform.rotation_degrees)
    rotated_x = translated_x * np.float32(cos(angle)) - translated_y * np.float32(sin(angle))
    rotated_y = translated_x * np.float32(sin(angle)) + translated_y * np.float32(cos(angle))
    normalized_x = rotated_x / np.float32(transform.scale[0]) + np.float32(
        transform.anchor[0] * source.shape[1]
    )
    normalized_y = rotated_y / np.float32(transform.scale[1]) + np.float32(
        transform.anchor[1] * source.shape[0]
    )
    minimum_x = np.float32(left * source.shape[1])
    maximum_x = np.float32((1 - right) * source.shape[1])
    minimum_y = np.float32(top * source.shape[0])
    maximum_y = np.float32((1 - bottom) * source.shape[0])
    inside = (
        (normalized_x >= minimum_x)
        & (normalized_x < maximum_x)
        & (normalized_y >= minimum_y)
        & (normalized_y < maximum_y)
    )
    if np.any(inside):
        u = normalized_x[inside] / np.float32(max(source.shape[1] - 1, 1))
        v = normalized_y[inside] / np.float32(max(source.shape[0] - 1, 1))
        sampled = _bilinear_sample(source, u, v)
        opacity = np.float32(np.clip(transform.opacity * fade_opacity, 0, 1))
        sampled *= opacity
        output[inside] = sampled
    return output


def quad_homography(
    source: tuple[tuple[float, float], ...],
    destination: tuple[tuple[float, float], ...],
) -> NDArray[np.float64]:
    if len(source) != 4 or len(destination) != 4:
        raise ValueError("corner pin requires four corners")
    rows: list[list[float]] = []
    values: list[float] = []
    for (x, y), (u, v) in zip(source, destination, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values.extend((u, v))
    try:
        solution = np.linalg.solve(np.asarray(rows), np.asarray(values))
    except np.linalg.LinAlgError as error:
        raise ValueError("corner pin corners do not define a valid plane") from error
    return np.append(solution, 1.0).reshape(3, 3)


def render_corner_pinned_layer(
    source: FloatImage,
    *,
    output_width: int,
    output_height: int,
    corners: tuple[tuple[float, float], ...],
    crop: tuple[float, float, float, float],
    opacity: float,
) -> FloatImage:
    """Map the cropped source rectangle onto four output corners (TL, TR, BR, BL)."""
    if source.ndim != 3 or source.shape[2] != 4:
        raise ValueError("visual source must be HxWx4 RGBA")
    left, top, right, bottom = crop
    if any(value < 0 or value > 1 for value in crop) or left + right >= 1 or top + bottom >= 1:
        raise ValueError("visual crop is invalid")
    height, width = source.shape[0], source.shape[1]
    minimum_x, maximum_x = left * width, (1 - right) * width
    minimum_y, maximum_y = top * height, (1 - bottom) * height
    rectangle = (
        (minimum_x, minimum_y),
        (maximum_x, minimum_y),
        (maximum_x, maximum_y),
        (minimum_x, maximum_y),
    )
    # Maps output pixels back to source pixels.
    inverse = quad_homography(tuple(corners), rectangle)
    grid_x, grid_y = np.meshgrid(
        np.arange(output_width, dtype=np.float64) + 0.5,
        np.arange(output_height, dtype=np.float64) + 0.5,
    )
    denominator = inverse[2, 0] * grid_x + inverse[2, 1] * grid_y + inverse[2, 2]
    safe = np.where(np.abs(denominator) < 1e-12, 1e-12, denominator)
    source_x = (inverse[0, 0] * grid_x + inverse[0, 1] * grid_y + inverse[0, 2]) / safe
    source_y = (inverse[1, 0] * grid_x + inverse[1, 1] * grid_y + inverse[1, 2]) / safe
    inside = (
        (denominator > 0)
        & (source_x >= minimum_x)
        & (source_x < maximum_x)
        & (source_y >= minimum_y)
        & (source_y < maximum_y)
    )
    output = np.zeros((output_height, output_width, 4), dtype=np.float32)
    if np.any(inside):
        u = (source_x[inside] / max(width - 1, 1)).astype(np.float32)
        v = (source_y[inside] / max(height - 1, 1)).astype(np.float32)
        sampled = _bilinear_sample(source, u, v)
        sampled *= np.float32(np.clip(opacity, 0, 1))
        output[inside] = sampled
    return output


@lru_cache(maxsize=16)
def layer_mask_alpha(
    points: tuple[tuple[float, float], ...],
    feather: float,
    invert: bool,
    output_width: int,
    output_height: int,
) -> NDArray[np.float32]:
    """Static per-layer coverage; feather fades inward from the polygon edge."""
    if len(points) < 3 or feather < 0:
        raise ValueError("layer mask requires three points and nonnegative feather")
    raw_x, raw_y = np.meshgrid(
        np.arange(output_width, dtype=np.float32) + np.float32(0.5),
        np.arange(output_height, dtype=np.float32) + np.float32(0.5),
    )
    inside = _points_in_polygon(raw_x, raw_y, points)
    if feather > 0:
        distance = _distance_to_polygons(raw_x, raw_y, (points,))
        alpha = np.where(inside, np.clip(distance / np.float32(feather), 0, 1), 0).astype(
            np.float32
        )
    else:
        alpha = inside.astype(np.float32)
    if invert:
        alpha = np.float32(1) - alpha
    alpha.setflags(write=False)
    return alpha


def compose_visual_frame(
    layers: tuple[PreparedVisualLayer, ...],
    *,
    timestamp_ms: int,
    output_width: int,
    output_height: int,
    background_rgb: tuple[int, int, int],
) -> tuple[FloatImage, ByteImage]:
    background_rgba = np.zeros((output_height, output_width, 4), dtype=np.uint8)
    background_rgba[..., :3] = np.asarray(background_rgb, dtype=np.uint8)
    background_rgba[..., 3] = 255
    working = to_working_rgba(
        background_rgba,
        alpha_interpretation="opaque",
        source_color_space="srgb",
    )
    for layer in sorted(layers, key=lambda item: (item.z_index, item.id)):
        local_time = timestamp_ms - layer.start_ms
        if local_time < 0 or local_time >= layer.duration_ms:
            continue
        source_time = layer.in_point_ms + local_time
        if layer.source_kind == "video" and layer.playback != "hold":
            if layer.source_duration_ms is None or layer.source_duration_ms <= 0:
                raise ValueError("timed media playback requires a source duration")
            if layer.playback == "loop":
                source_time %= layer.source_duration_ms
            elif source_time >= layer.source_duration_ms:
                continue
        source = (
            layer.source.frame_at(source_time)
            if isinstance(layer.source, VideoFrameSource)
            else layer.source
            if layer.source_kind == "image"
            else select_video_frame(
                cast(tuple[TimedVideoFrame, ...], layer.source),
                source_time,
            ).image
        )
        if not isinstance(source, np.ndarray):
            raise ValueError("prepared visual source is invalid")
        fade = _fade_opacity(
            local_time,
            layer.duration_ms,
            layer.fade_in_ms,
            layer.fade_out_ms,
        )
        transform = evaluate_transform(layer.keyframes, local_time)
        foreground = (
            render_corner_pinned_layer(
                source,
                output_width=output_width,
                output_height=output_height,
                corners=layer.corner_pin,
                crop=transform.crop,
                opacity=transform.opacity * fade,
            )
            if layer.corner_pin is not None
            else render_transformed_layer(
                source,
                output_width=output_width,
                output_height=output_height,
                transform=transform,
                fade_opacity=fade,
            )
        )
        if layer.mask is not None:
            foreground = (
                foreground
                * layer_mask_alpha(
                    layer.mask.points,
                    layer.mask.feather,
                    layer.mask.invert,
                    output_width,
                    output_height,
                )[..., None]
            )
        working = composite_over(working, foreground)
    return working, working_to_srgb8(working)


def to_working_rgba(
    rgba: ByteImage,
    *,
    alpha_interpretation: AlphaInterpretation,
    source_color_space: SourceColorSpace,
) -> FloatImage:
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("source image must be HxWx4 RGBA")
    encoded = rgba.astype(np.float32) / np.float32(255)
    alpha = (
        np.ones_like(encoded[..., 3:4]) if alpha_interpretation == "opaque" else encoded[..., 3:4]
    )
    color = encoded[..., :3]
    if alpha_interpretation == "premultiplied":
        color = np.divide(
            color,
            alpha,
            out=np.zeros_like(color),
            where=alpha > np.float32(1e-8),
        )
    linear = _decode_transfer(color, source_color_space)
    return np.concatenate((linear * alpha, alpha), axis=2).astype(
        np.float32,
        copy=False,
    )


def composite_over(background: FloatImage, foreground: FloatImage) -> FloatImage:
    if background.shape != foreground.shape or background.shape[-1] != 4:
        raise ValueError("premultiplied images must have identical RGBA shapes")
    foreground_alpha = foreground[..., 3:4]
    result = foreground + background * (np.float32(1) - foreground_alpha)
    return np.clip(result, 0, 1).astype(np.float32, copy=False)


def working_to_srgb8(image: FloatImage) -> ByteImage:
    if image.ndim != 3 or image.shape[2] != 4:
        raise ValueError("working image must be HxWx4 RGBA")
    alpha = np.clip(image[..., 3:4], 0, 1)
    straight = np.divide(
        image[..., :3],
        alpha,
        out=np.zeros_like(image[..., :3]),
        where=alpha > np.float32(1e-8),
    )
    encoded = _encode_srgb(np.clip(straight, 0, 1))
    return np.rint(np.concatenate((encoded, alpha), axis=2) * 255).astype(np.uint8)


def conform_preview_pixel_format(
    image: ByteImage,
    pixel_format: str,
) -> ByteImage:
    if pixel_format != "yuv420p":
        raise ValueError("preview pixel format is unsupported")
    if image.ndim != 3 or image.shape[2] != 4 or image.shape[0] % 2 or image.shape[1] % 2:
        raise ValueError("yuv420p preview requires even-dimension RGBA")
    source = av.VideoFrame.from_ndarray(image, format="rgba")
    conformed = source.reformat(format="yuv420p").reformat(format="rgba")
    return cast(ByteImage, conformed.to_ndarray(format="rgba"))


def rasterize_indexed_mesh(
    source: FloatImage,
    *,
    output_width: int,
    output_height: int,
    vertices: tuple[tuple[float, float, float, float], ...],
    triangles: tuple[tuple[int, int, int], ...],
    stage_bounds: tuple[float, float, float, float],
) -> FloatImage:
    if output_width <= 0 or output_height <= 0:
        raise ValueError("mesh output dimensions must be positive")
    if source.ndim != 3 or source.shape[2] != 4:
        raise ValueError("mesh source must be HxWx4 RGBA")
    minimum_x, minimum_y, maximum_x, maximum_y = stage_bounds
    if maximum_x <= minimum_x or maximum_y <= minimum_y:
        raise ValueError("mesh stage bounds are invalid")
    output = np.zeros((output_height, output_width, 4), dtype=np.float32)
    for triangle in triangles:
        if len(set(triangle)) != 3 or any(
            index < 0 or index >= len(vertices) for index in triangle
        ):
            raise ValueError("mesh triangle indices are invalid")
        selected = [vertices[index] for index in triangle]
        uv = np.asarray([(item[0], item[1]) for item in selected], dtype=np.float32)
        target = np.asarray(
            [
                (
                    (item[2] - minimum_x) / (maximum_x - minimum_x) * output_width,
                    (item[3] - minimum_y) / (maximum_y - minimum_y) * output_height,
                )
                for item in selected
            ],
            dtype=np.float32,
        )
        _rasterize_triangle(output, source, target, uv)
    return output


def render_stage_to_projector(
    stage_image: FloatImage,
    *,
    stage_bounds: tuple[float, float, float, float],
    projector_width: int,
    projector_height: int,
    mesh_vertices: tuple[tuple[float, float, float, float], ...],
    mesh_triangles: tuple[tuple[int, int, int], ...],
    blend_alpha: NDArray[np.float32] | None = None,
    brightness: float = 1.0,
    black_level: float = 0.0,
    color_matrix: tuple[float, ...] = (1, 0, 0, 0, 1, 0, 0, 0, 1),
) -> FloatImage:
    minimum_x, minimum_y, maximum_x, maximum_y = stage_bounds
    if maximum_x <= minimum_x or maximum_y <= minimum_y:
        raise ValueError("projector stage bounds are invalid")
    projector_mesh = tuple(
        (
            (stage_x - minimum_x) / (maximum_x - minimum_x),
            (stage_y - minimum_y) / (maximum_y - minimum_y),
            projector_u,
            projector_v,
        )
        for projector_u, projector_v, stage_x, stage_y in mesh_vertices
    )
    output = rasterize_indexed_mesh(
        stage_image,
        output_width=projector_width,
        output_height=projector_height,
        vertices=projector_mesh,
        triangles=mesh_triangles,
        stage_bounds=(0, 0, 1, 1),
    )
    if blend_alpha is not None:
        if blend_alpha.shape != (projector_height, projector_width):
            raise ValueError("projector blend mask dimensions are invalid")
        output *= np.clip(blend_alpha, 0, 1)[..., None]
    if len(color_matrix) != 9:
        raise ValueError("projector color matrix must contain nine values")
    matrix = np.asarray(color_matrix, dtype=np.float32).reshape(3, 3)
    alpha = output[..., 3:4]
    straight = np.divide(
        output[..., :3],
        alpha,
        out=np.zeros_like(output[..., :3]),
        where=alpha > np.float32(1e-8),
    )
    corrected = np.einsum("...c,dc->...d", straight, matrix, optimize=True)
    corrected = np.clip(
        corrected * np.float32(brightness) + np.float32(black_level),
        0,
        1,
    )
    output[..., :3] = corrected * alpha
    return output.astype(np.float32, copy=False)


def planar_projector_mesh(
    stage_extent: tuple[float, float, float, float],
) -> tuple[
    tuple[tuple[float, float, float, float], ...],
    tuple[tuple[int, int, int], ...],
]:
    x, y, width, height = stage_extent
    if width <= 0 or height <= 0:
        raise ValueError("projector stage extent must be positive")
    return (
        (
            (0, 0, x, y),
            (1, 0, x + width, y),
            (1, 1, x + width, y + height),
            (0, 1, x, y + height),
        ),
        ((0, 1, 2), (0, 2, 3)),
    )


def overlap_distance_field_masks(
    *,
    output_width: int,
    output_height: int,
    stage_bounds: tuple[float, float, float, float],
    overlap_polygon: tuple[tuple[float, float], ...],
    first_exclusive_polygons: tuple[tuple[tuple[float, float], ...], ...],
    second_exclusive_polygons: tuple[tuple[tuple[float, float], ...], ...],
    ramp_width: float,
    gamma: float,
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    if (
        output_width <= 0
        or output_height <= 0
        or len(overlap_polygon) < 3
        or not first_exclusive_polygons
        or not second_exclusive_polygons
        or ramp_width <= 0
        or ramp_width > 1
        or gamma <= 0
    ):
        raise ValueError("overlap blend calibration is invalid")
    minimum_x, minimum_y, maximum_x, maximum_y = stage_bounds
    raw_x, raw_y = np.meshgrid(
        minimum_x
        + (np.arange(output_width, dtype=np.float32) + np.float32(0.5))
        / output_width
        * (maximum_x - minimum_x),
        minimum_y
        + (np.arange(output_height, dtype=np.float32) + np.float32(0.5))
        / output_height
        * (maximum_y - minimum_y),
    )
    grid_x = raw_x.astype(np.float32, copy=False)
    grid_y = raw_y.astype(np.float32, copy=False)
    overlap = _points_in_polygon(grid_x, grid_y, overlap_polygon)
    distance_to_first = _distance_to_polygons(grid_x, grid_y, first_exclusive_polygons)
    distance_to_second = _distance_to_polygons(grid_x, grid_y, second_exclusive_polygons)
    denominator = distance_to_first + distance_to_second
    first_raw = np.divide(
        distance_to_second,
        denominator,
        out=np.full_like(denominator, 0.5),
        where=denominator > np.float32(1e-8),
    )
    first_ramped = np.clip(
        (first_raw - np.float32((1 - ramp_width) / 2)) / np.float32(ramp_width),
        0,
        1,
    )
    second_ramped = np.float32(1) - first_ramped
    first_gamma = np.power(first_ramped, np.float32(gamma))
    second_gamma = np.power(second_ramped, np.float32(gamma))
    gamma_sum = first_gamma + second_gamma
    first_weight = np.divide(
        first_gamma,
        gamma_sum,
        out=np.full_like(gamma_sum, 0.5),
        where=gamma_sum > np.float32(1e-8),
    )
    second_weight = np.float32(1) - first_weight
    first_mask = np.ones((output_height, output_width), dtype=np.float32)
    second_mask = np.ones((output_height, output_width), dtype=np.float32)
    first_mask[overlap] = first_weight[overlap]
    second_mask[overlap] = second_weight[overlap]
    return first_mask, second_mask


def polygon_mask(
    *,
    output_width: int,
    output_height: int,
    stage_bounds: tuple[float, float, float, float],
    polygon: tuple[tuple[float, float], ...],
) -> NDArray[np.float32]:
    if output_width <= 0 or output_height <= 0 or len(polygon) < 3:
        raise ValueError("polygon mask input is invalid")
    minimum_x, minimum_y, maximum_x, maximum_y = stage_bounds
    raw_x, raw_y = np.meshgrid(
        minimum_x
        + (np.arange(output_width, dtype=np.float32) + np.float32(0.5))
        / output_width
        * (maximum_x - minimum_x),
        minimum_y
        + (np.arange(output_height, dtype=np.float32) + np.float32(0.5))
        / output_height
        * (maximum_y - minimum_y),
    )
    return _points_in_polygon(
        raw_x.astype(np.float32, copy=False),
        raw_y.astype(np.float32, copy=False),
        polygon,
    ).astype(np.float32)


def mix_audio_layers(
    layers: tuple[AudioLayerBuffer, ...],
    *,
    total_samples: int,
) -> NDArray[np.float32]:
    if total_samples < 0:
        raise ValueError("audio mix length cannot be negative")
    output = np.zeros((total_samples, 2), dtype=np.float32)
    audible = [layer for layer in layers if not layer.mute]
    if any(layer.solo for layer in audible):
        audible = [layer for layer in audible if layer.solo]
    for layer in sorted(audible, key=lambda item: item.mix_order):
        if (
            layer.samples.ndim != 2
            or layer.samples.shape[1] != 2
            or layer.start_sample < 0
            or layer.fade_in_samples < 0
            or layer.fade_out_samples < 0
            or layer.fade_in_samples + layer.fade_out_samples > layer.samples.shape[0]
        ):
            raise ValueError("audio layer buffer is invalid")
        start = min(layer.start_sample, total_samples)
        count = min(layer.samples.shape[0], total_samples - start)
        if count <= 0:
            continue
        samples = layer.samples[:count].astype(np.float32, copy=True)
        envelope = np.ones(count, dtype=np.float32)
        fade_in = min(layer.fade_in_samples, count)
        fade_out = min(layer.fade_out_samples, count)
        if fade_in:
            envelope[:fade_in] = np.linspace(0, 1, fade_in, dtype=np.float32)
        if fade_out:
            envelope[-fade_out:] *= np.linspace(1, 0, fade_out, dtype=np.float32)
        gain = np.float32(10 ** (layer.gain_db / 20))
        output[start : start + count] += samples * envelope[:, None] * gain
    return np.clip(output, -1, 1).astype(np.float32, copy=False)


def _transform_from_keyframe(keyframe: dict[str, object]) -> EvaluatedTransform:
    position = _number_tuple(keyframe.get("position"), 2)
    scale = _number_tuple(keyframe.get("scale"), 2)
    anchor = _number_tuple(keyframe.get("anchor"), 2)
    crop = _number_tuple(keyframe.get("crop"), 4)
    return EvaluatedTransform(
        position=(position[0], position[1]),
        scale=(scale[0], scale[1]),
        rotation_degrees=_number(keyframe.get("rotation_deg")),
        anchor=(anchor[0], anchor[1]),
        opacity=_number(keyframe.get("opacity")),
        crop=(crop[0], crop[1], crop[2], crop[3]),
    )


def _fade_opacity(
    local_time_ms: int,
    duration_ms: int,
    fade_in_ms: int,
    fade_out_ms: int,
) -> float:
    if (
        duration_ms <= 0
        or fade_in_ms < 0
        or fade_out_ms < 0
        or fade_in_ms + fade_out_ms > duration_ms
    ):
        raise ValueError("visual layer fade interval is invalid")
    fade_in = min(1.0, local_time_ms / fade_in_ms) if fade_in_ms else 1.0
    remaining = duration_ms - local_time_ms
    fade_out = min(1.0, remaining / fade_out_ms) if fade_out_ms else 1.0
    return max(0.0, min(fade_in, fade_out))


def _temporal_progress(progress: float, interpolation: dict[str, object]) -> float:
    kind = interpolation.get("kind")
    if kind == "linear":
        return progress
    if kind == "ease_in":
        return progress * progress
    if kind == "ease_out":
        return 1 - (1 - progress) * (1 - progress)
    if kind == "bezier":
        controls = _number_tuple(interpolation.get("temporal_bezier"), 4)
        return _cubic_bezier_y_for_x(progress, controls)
    raise ValueError("transform interpolation kind is unsupported")


def _cubic_bezier_y_for_x(
    progress: float,
    controls: tuple[float, ...],
) -> float:
    x1, y1, x2, y2 = controls
    lower = 0.0
    upper = 1.0
    parameter = progress
    for _ in range(24):
        parameter = (lower + upper) / 2
        x_value = _cubic_bezier(0, x1, x2, 1, parameter)
        if x_value < progress:
            lower = parameter
        else:
            upper = parameter
    return _cubic_bezier(0, y1, y2, 1, parameter)


def _position_progress(
    start: tuple[float, float],
    end: tuple[float, float],
    progress: float,
    left_interpolation: dict[str, object],
    right_interpolation: dict[str, object],
) -> tuple[float, float]:
    if left_interpolation.get("kind") != "bezier":
        return _interpolate_pair(start, end, progress)
    outgoing = _number_tuple(left_interpolation.get("spatial_out"), 2)
    incoming = _number_tuple(right_interpolation.get("spatial_in"), 2)
    return (
        _cubic_bezier(
            start[0],
            start[0] + outgoing[0],
            end[0] + incoming[0],
            end[0],
            progress,
        ),
        _cubic_bezier(
            start[1],
            start[1] + outgoing[1],
            end[1] + incoming[1],
            end[1],
            progress,
        ),
    )


def _cubic_bezier(
    start: float,
    first: float,
    second: float,
    end: float,
    progress: float,
) -> float:
    remaining = 1 - progress
    return (
        remaining**3 * start
        + 3 * remaining**2 * progress * first
        + 3 * remaining * progress**2 * second
        + progress**3 * end
    )


def _interpolate_pair(
    start: tuple[float, float],
    end: tuple[float, float],
    progress: float,
) -> tuple[float, float]:
    return (
        _lerp(start[0], end[0], progress),
        _lerp(start[1], end[1], progress),
    )


def _lerp(start: float, end: float, progress: float) -> float:
    return start + (end - start) * progress


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("transform value must be numeric")
    return float(value)


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("transform time must be an integer")
    return value


def _number_tuple(value: object, length: int) -> tuple[float, ...]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != length
        or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value)
    ):
        raise ValueError("transform vector is invalid")
    return tuple(float(item) for item in value)


def _rasterize_triangle(
    output: FloatImage,
    source: FloatImage,
    target: NDArray[np.float32],
    uv: NDArray[np.float32],
) -> None:
    height, width, _ = output.shape
    minimum_x = max(0, int(np.floor(np.min(target[:, 0]))))
    maximum_x = min(width - 1, int(np.ceil(np.max(target[:, 0]))))
    minimum_y = max(0, int(np.floor(np.min(target[:, 1]))))
    maximum_y = min(height - 1, int(np.ceil(np.max(target[:, 1]))))
    if minimum_x > maximum_x or minimum_y > maximum_y:
        return
    x0, y0 = target[0]
    x1, y1 = target[1]
    x2, y2 = target[2]
    denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(float(denominator)) < 1e-8:
        raise ValueError("mesh triangle is degenerate")
    grid_x, grid_y = np.meshgrid(
        np.arange(minimum_x, maximum_x + 1, dtype=np.float32) + np.float32(0.5),
        np.arange(minimum_y, maximum_y + 1, dtype=np.float32) + np.float32(0.5),
    )
    first = ((y1 - y2) * (grid_x - x2) + (x2 - x1) * (grid_y - y2)) / denominator
    second = ((y2 - y0) * (grid_x - x2) + (x0 - x2) * (grid_y - y2)) / denominator
    third = np.float32(1) - first - second
    mask = (first >= -1e-6) & (second >= -1e-6) & (third >= -1e-6)
    if not np.any(mask):
        return
    mapped_u = first * uv[0, 0] + second * uv[1, 0] + third * uv[2, 0]
    mapped_v = first * uv[0, 1] + second * uv[1, 1] + third * uv[2, 1]
    sampled = _bilinear_sample(source, mapped_u[mask], mapped_v[mask])
    region = output[minimum_y : maximum_y + 1, minimum_x : maximum_x + 1]
    region[mask] = sampled


def _points_in_polygon(
    x: NDArray[np.float32],
    y: NDArray[np.float32],
    polygon: tuple[tuple[float, float], ...],
) -> NDArray[np.bool_]:
    inside = np.zeros(x.shape, dtype=np.bool_)
    previous = polygon[-1]
    for current in polygon:
        current_x, current_y = current
        previous_x, previous_y = previous
        intersects = (current_y > y) != (previous_y > y)
        boundary_x = (previous_x - current_x) * (y - current_y) / np.float32(
            previous_y - current_y or 1e-12
        ) + current_x
        inside ^= intersects & (x < boundary_x)
        previous = current
    return inside


def _distance_to_polygons(
    x: NDArray[np.float32],
    y: NDArray[np.float32],
    polygons: tuple[tuple[tuple[float, float], ...], ...],
) -> NDArray[np.float32]:
    distance = np.full(x.shape, np.inf, dtype=np.float32)
    for polygon in polygons:
        if len(polygon) < 3:
            raise ValueError("coverage polygon requires at least three points")
        previous = polygon[-1]
        for current in polygon:
            distance = np.minimum(
                distance,
                _distance_to_segment(x, y, previous, current),
            )
            previous = current
    return distance


def _distance_to_segment(
    x: NDArray[np.float32],
    y: NDArray[np.float32],
    start: tuple[float, float],
    end: tuple[float, float],
) -> NDArray[np.float32]:
    delta_x = np.float32(end[0] - start[0])
    delta_y = np.float32(end[1] - start[1])
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared <= np.float32(1e-12):
        return cast(
            NDArray[np.float32],
            np.hypot(
                x - np.float32(start[0]),
                y - np.float32(start[1]),
            ).astype(np.float32, copy=False),
        )
    projection = np.clip(
        ((x - np.float32(start[0])) * delta_x + (y - np.float32(start[1])) * delta_y)
        / length_squared,
        0,
        1,
    )
    nearest_x = np.float32(start[0]) + projection * delta_x
    nearest_y = np.float32(start[1]) + projection * delta_y
    return np.hypot(x - nearest_x, y - nearest_y).astype(
        np.float32,
        copy=False,
    )


def _bilinear_sample(
    source: FloatImage,
    u: NDArray[np.float32],
    v: NDArray[np.float32],
) -> FloatImage:
    height, width, _ = source.shape
    x = np.clip(u, 0, 1) * np.float32(width - 1)
    y = np.clip(v, 0, 1) * np.float32(height - 1)
    x0 = np.floor(x).astype(np.intp)
    y0 = np.floor(y).astype(np.intp)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    x_weight = (x - x0).astype(np.float32)[..., None]
    y_weight = (y - y0).astype(np.float32)[..., None]
    top = source[y0, x0] * (1 - x_weight) + source[y0, x1] * x_weight
    bottom = source[y1, x0] * (1 - x_weight) + source[y1, x1] * x_weight
    return cast(
        FloatImage,
        (top * (1 - y_weight) + bottom * y_weight).astype(
            np.float32,
            copy=False,
        ),
    )


def _decode_transfer(
    color: NDArray[np.float32],
    color_space: SourceColorSpace,
) -> NDArray[np.float32]:
    if color_space == "linear_srgb":
        return color
    if color_space == "bt709":
        return np.where(
            color < np.float32(0.081),
            color / np.float32(4.5),
            np.power(
                (color + np.float32(0.099)) / np.float32(1.099),
                np.float32(1 / 0.45),
            ),
        ).astype(np.float32)
    return np.where(
        color <= np.float32(0.04045),
        color / np.float32(12.92),
        np.power(
            (color + np.float32(0.055)) / np.float32(1.055),
            np.float32(2.4),
        ),
    ).astype(np.float32)


def _encode_srgb(color: NDArray[np.float32]) -> NDArray[np.float32]:
    return np.where(
        color <= np.float32(0.0031308),
        color * np.float32(12.92),
        np.float32(1.055) * np.power(color, np.float32(1 / 2.4)) - np.float32(0.055),
    ).astype(np.float32)
