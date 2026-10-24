import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import numpy as np

from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject
from cueverge.gui.frame_compositor import (
    AlphaInterpretation,
    AudioLayerBuffer,
    ByteImage,
    FloatImage,
    LayerMask,
    PreparedVisualLayer,
    SourceColorSpace,
    TimedVideoFrame,
    VideoFrameSource,
    compose_visual_frame,
    conform_preview_pixel_format,
    decode_animated_image,
    decode_audio_stereo_48k,
    decode_still,
    mix_audio_layers,
    prepare_video_source,
)
from cueverge.gui.render_encoder import EncoderProfile, encode_output_set


class ProjectorRenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedMedia:
    visual_layers: tuple[PreparedVisualLayer, ...]
    audio_layers: tuple[AudioLayerBuffer, ...]
    source_checksums: dict[str, str]


def preview_projector_composition(
    *,
    project_id: str,
    draft: DraftProject,
    composition_id: str,
    timestamp_ms: int,
    assets: AssetUploadService,
) -> ByteImage:
    metadata = cast(dict[str, object], draft.editor_metadata)
    composition = _mapping(_mapping(metadata.get("compositions")).get(composition_id))
    if composition.get("space_kind") != "projector_native":
        raise ProjectorRenderError("projector preview requires projector-native composition space")
    duration_ms = _integer(composition.get("duration_ms"))
    if timestamp_ms < 0 or timestamp_ms >= duration_ms:
        raise ProjectorRenderError("preview timestamp is outside composition duration")
    settings = _mapping(composition.get("render_settings"))
    raw_layers = _mapping(metadata.get("timeline_transform_layers")).get(composition_id, [])
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ProjectorRenderError("composition has no media layers")
    prepared = prepare_media_layers(
        project_id=project_id,
        raw_layers=raw_layers,
        assets=assets,
    )
    preview = compose_visual_frame(
        prepared.visual_layers,
        timestamp_ms=timestamp_ms,
        output_width=_integer(settings.get("render_width")),
        output_height=_integer(settings.get("render_height")),
        background_rgb=_hex_rgb(_string(settings.get("output_background_color"))),
    )[1]
    profile = _mapping(settings.get("output_profile"))
    return conform_preview_pixel_format(
        preview,
        _string(profile.get("pixel_format")),
    )


def render_projector_composition(
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    composition_id: str,
    assets: AssetUploadService,
    output_directory: Path,
) -> dict[str, object]:
    metadata = cast(dict[str, object], draft.editor_metadata)
    compositions = _mapping(metadata.get("compositions"))
    composition = _mapping(compositions.get(composition_id))
    if not composition:
        raise ProjectorRenderError("composition does not exist")
    if composition.get("space_kind") != "projector_native":
        raise ProjectorRenderError("projector composition runner requires projector-native space")
    target_id = _string(composition.get("target_id"))
    duration_ms = _integer(composition.get("duration_ms"))
    settings = _mapping(composition.get("render_settings"))
    render_width = _integer(settings.get("render_width"))
    render_height = _integer(settings.get("render_height"))
    frame_rate_numerator = _integer(settings.get("frame_rate_numerator"))
    frame_rate_denominator = _integer(settings.get("frame_rate_denominator"))
    layers_by_space = _mapping(metadata.get("timeline_transform_layers"))
    raw_layers = layers_by_space.get(composition_id, [])
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ProjectorRenderError("composition has no media layers")
    prepared = prepare_media_layers(
        project_id=project_id,
        raw_layers=raw_layers,
        assets=assets,
    )
    visual_layers = prepared.visual_layers
    audio_layers = prepared.audio_layers
    source_checksums = prepared.source_checksums
    frame_count = max(
        1,
        (duration_ms * frame_rate_numerator + 1000 * frame_rate_denominator - 1)
        // (1000 * frame_rate_denominator),
    )
    background = _hex_rgb(_string(settings.get("output_background_color")))
    frames = tuple(
        compose_visual_frame(
            visual_layers,
            timestamp_ms=(index * 1000 * frame_rate_denominator) // frame_rate_numerator,
            output_width=render_width,
            output_height=render_height,
            background_rgb=background,
        )[1]
        for index in range(frame_count)
    )
    mixed_audio = mix_audio_layers(
        audio_layers,
        total_samples=duration_ms * 48,
    )
    output_profile = _mapping(settings.get("output_profile"))
    profile = EncoderProfile(
        video_profile=_string(output_profile.get("video_profile")),
        video_level=_string(output_profile.get("video_level")),
        pixel_format=_string(output_profile.get("pixel_format")),
        max_video_bitrate_kbps=_integer(output_profile.get("max_video_bitrate_kbps")),
        keyframe_interval_frames=_integer(output_profile.get("keyframe_interval_frames")),
        b_frames=_integer(output_profile.get("b_frames")),
        loudness_target_lufs=_number(output_profile.get("loudness_target_lufs")),
    )
    composition_digest = hashlib.sha256(
        json.dumps(
            composition,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return encode_output_set(
        output_directory=output_directory,
        members={target_id: frames},
        audio_owner=target_id,
        audio_samples=mixed_audio,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        duration_ms=duration_ms,
        profile=profile,
        source_revision=source_revision,
        composition_id=composition_id,
        composition_digest=composition_digest,
        source_checksums=source_checksums,
        calibration_revisions={target_id: 0},
        media_end_behavior=_string(settings.get("media_end_behavior")),
    )


def prepare_media_layers(
    *,
    project_id: str,
    raw_layers: list[object],
    assets: AssetUploadService,
) -> PreparedMedia:
    visual_layers: list[PreparedVisualLayer] = []
    audio_layers: list[AudioLayerBuffer] = []
    source_checksums: dict[str, str] = {}
    decoded_bytes = 0
    for item in raw_layers:
        layer = _mapping(item)
        source_id = _string(layer.get("source_id"))
        kind = _string(layer.get("kind"))
        asset, path = assets.get_original(source_id)
        if asset.project_id != project_id:
            raise ProjectorRenderError("composition source belongs to another project")
        assert asset.digest_sha256 is not None
        assert asset.media_type is not None
        source_checksums[source_id] = asset.digest_sha256
        source_duration_ms: int | None = None
        if kind == "audio":
            if not asset.media_type.startswith(("audio/", "video/")):
                raise ProjectorRenderError("audio layer source has no audio media")
            decoded = decode_audio_stereo_48k(str(path))
            in_sample = _integer(layer.get("in_point_ms")) * 48
            out_sample = _integer(layer.get("out_point_ms")) * 48
            trimmed = decoded[in_sample:out_sample]
            expected = _integer(layer.get("duration_ms")) * 48
            if trimmed.shape[0] < expected:
                raise ProjectorRenderError("audio source is shorter than its layer trim")
            audio = _mapping(layer.get("audio"))
            audio_layers.append(
                AudioLayerBuffer(
                    samples=trimmed[:expected],
                    start_sample=_integer(layer.get("start_ms")) * 48,
                    gain_db=_number(audio.get("gain_db")),
                    mute=_boolean(audio.get("mute")),
                    solo=_boolean(audio.get("solo")),
                    mix_order=_integer(audio.get("mix_order")),
                    fade_in_samples=_integer(layer.get("fade_in_ms")) * 48,
                    fade_out_samples=_integer(layer.get("fade_out_ms")) * 48,
                )
            )
            continue
        alpha = _alpha_interpretation(layer.get("alpha_interpretation"))
        color_space = _source_color_space(layer.get("source_color_space"))
        source: FloatImage | tuple[TimedVideoFrame, ...] | VideoFrameSource
        if kind in {"image", "video"} and asset.media_type == "image/gif":
            source, source_duration_ms = decode_animated_image(
                str(path),
                alpha_interpretation=alpha,
                source_color_space=color_space,
            )
            kind = "video"
        elif kind == "image":
            if not asset.media_type.startswith("image/"):
                raise ProjectorRenderError("image layer source is not an image")
            source = decode_still(
                str(path),
                alpha_interpretation=alpha,
                source_color_space=color_space,
            )
        elif kind == "video":
            if not asset.media_type.startswith("video/"):
                raise ProjectorRenderError("video layer source is not a video")
            source = prepare_video_source(
                str(path),
                alpha_interpretation=alpha,
                source_color_space=color_space,
            )
            source_duration_ms = source.duration_ms
        else:
            raise ProjectorRenderError(
                f"media layer kind {kind!r} is not supported by the projector runner"
            )
        keyframes = layer.get("keyframes")
        if not isinstance(keyframes, list):
            raise ProjectorRenderError("visual layer keyframes are invalid")
        if isinstance(source, np.ndarray):
            decoded_bytes += source.nbytes
        elif isinstance(source, tuple):
            decoded_bytes += sum(frame.image.nbytes for frame in source)
        if decoded_bytes > 256 * 1024 * 1024:
            raise ProjectorRenderError("media layers exceed the 256 MiB decoded frame budget")
        visual_layers.append(
            PreparedVisualLayer(
                id=_string(layer.get("id")),
                source=source,
                source_kind=cast("Literal['image', 'video']", kind),
                start_ms=_integer(layer.get("start_ms")),
                duration_ms=_integer(layer.get("duration_ms")),
                in_point_ms=_integer(layer.get("in_point_ms")),
                z_index=_integer(layer.get("z_index")),
                fade_in_ms=_integer(layer.get("fade_in_ms")),
                fade_out_ms=_integer(layer.get("fade_out_ms")),
                keyframes=tuple(_mapping(keyframe) for keyframe in keyframes),
                playback=cast(
                    "Literal['hold', 'loop', 'once']",
                    layer.get("playback", "hold"),
                ),
                source_duration_ms=source_duration_ms,
                corner_pin=_corner_pin(layer.get("corner_pin")),
                mask=_layer_mask(layer.get("mask")),
            )
        )
    return PreparedMedia(
        visual_layers=tuple(visual_layers),
        audio_layers=tuple(audio_layers),
        source_checksums=source_checksums,
    )


def _corner_pin(value: object) -> tuple[tuple[float, float], ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise ProjectorRenderError("corner pin requires four [x, y] corners")
    return tuple((_number(point[0]), _number(point[1])) for point in _point_list(value))


def _layer_mask(value: object) -> LayerMask | None:
    if value is None:
        return None
    mask = _mapping(value)
    points = mask.get("points")
    if not isinstance(points, list) or len(points) < 3:
        raise ProjectorRenderError("layer mask requires at least three points")
    return LayerMask(
        points=tuple((_number(point[0]), _number(point[1])) for point in _point_list(points)),
        feather=_number(mask.get("feather", 0)),
        invert=mask.get("invert", False) is True,
    )


def _point_list(values: list[object]) -> list[list[object]]:
    if not all(isinstance(point, list) and len(point) == 2 for point in values):
        raise ProjectorRenderError("layer geometry points must be [x, y] pairs")
    return cast(list[list[object]], values)


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ProjectorRenderError("render value must be a nonempty string")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProjectorRenderError("render value must be an integer")
    return value


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ProjectorRenderError("render value must be numeric")
    return float(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ProjectorRenderError("render value must be boolean")
    return value


def _alpha_interpretation(
    value: object,
) -> AlphaInterpretation:
    if value not in {"opaque", "straight", "premultiplied"}:
        raise ProjectorRenderError("alpha interpretation is invalid")
    return cast(AlphaInterpretation, value)


def _source_color_space(
    value: object,
) -> SourceColorSpace:
    if value not in {"srgb", "bt709", "linear_srgb"}:
        raise ProjectorRenderError("source color space is invalid")
    return cast(SourceColorSpace, value)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    if len(value) != 7 or not value.startswith("#"):
        raise ProjectorRenderError("output background color is invalid")
    try:
        return (
            int(value[1:3], 16),
            int(value[3:5], 16),
            int(value[5:7], 16),
        )
    except ValueError as error:
        raise ProjectorRenderError("output background color is invalid") from error
