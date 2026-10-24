import hashlib
import json
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import av
import numpy as np
from PIL import Image

from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject
from cueverge.gui.projector_renderer import (
    preview_projector_composition,
    render_projector_composition,
)
from cueverge.gui.render_verification import compare_rgb_frames
from tests.unit.media_fixtures import stream_types, write_video


def test_projector_runner_renders_still_alpha_opaque_video_and_mixed_audio(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    still = sources / "still.png"
    Image.fromarray(
        np.full((36, 64, 4), [0, 0, 255, 128], dtype=np.uint8),
        mode="RGBA",
    ).save(still)
    alpha_video = sources / "alpha.mkv"
    write_video(
        alpha_video,
        [np.full((36, 64, 4), [255, 0, 0, 128], dtype=np.uint8)] * 4,
        rate=4,
        codec="ffv1",
        pixel_format="bgra",
    )
    opaque_video = sources / "opaque.mp4"
    write_video(
        opaque_video,
        [np.full((36, 64, 3), [0, 128, 0], dtype=np.uint8)] * 4,
        rate=4,
        codec="libx264",
        pixel_format="yuv420p",
    )
    audio_a = sources / "audio-a.wav"
    audio_b = sources / "audio-b.wav"
    _write_wave(audio_a, 440)
    _write_wave(audio_b, 660)
    assets = _FixtureAssets(
        {
            "still": ("image/png", still),
            "alpha-video": ("video/x-matroska", alpha_video),
            "opaque-video": ("video/mp4", opaque_video),
            "audio-a": ("audio/wav", audio_a),
            "audio-b": ("audio/wav", audio_b),
        }
    )
    draft = DraftProject.model_validate(_draft())
    preview = preview_projector_composition(
        project_id="demo-project",
        draft=draft,
        composition_id="projector-composition",
        timestamp_ms=0,
        assets=cast(AssetUploadService, assets),
    )

    manifest = render_projector_composition(
        project_id="demo-project",
        source_revision=4,
        draft=draft,
        composition_id="projector-composition",
        assets=cast(AssetUploadService, assets),
        output_directory=tmp_path / "renders",
    )

    output_root = tmp_path / "renders" / str(manifest["activation_identity"])
    stored = json.loads((output_root / "render-manifest.json").read_text(encoding="utf-8"))
    assert set(stored["source_checksums"]) == {
        "still",
        "alpha-video",
        "opaque-video",
        "audio-a",
        "audio-b",
    }
    member = stored["members"][0]
    assert member["resource"] == "projector.left"
    assert member["has_audio"] is True
    assert member["streams"]["video"]["frame_count"] == 4
    assert stream_types(output_root / member["filename"]) == {"audio", "video"}
    with av.open(str(output_root / member["filename"])) as container:
        baked_frame = next(container.decode(video=0)).to_ndarray(format="rgba")
    comparison = compare_rgb_frames(preview, baked_frame)
    assert comparison.passed, comparison


class _FixtureAssets:
    def __init__(self, sources: dict[str, tuple[str, Path]]) -> None:
        self.sources = sources

    def get_original(self, asset_id: str) -> tuple[SimpleNamespace, Path]:
        media_type, path = self.sources[asset_id]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return (
            SimpleNamespace(
                project_id="demo-project",
                digest_sha256=digest,
                media_type=media_type,
            ),
            path,
        )


def _draft() -> dict[str, object]:
    render_settings = {
        "render_pipeline_revision": 1,
        "render_width": 64,
        "render_height": 36,
        "frame_rate_numerator": 4,
        "frame_rate_denominator": 1,
        "working_color_space": "linear_srgb",
        "working_alpha_mode": "premultiplied",
        "scaling_filter": "lanczos",
        "output_background_color": "#000000",
        "media_end_behavior": "hold_last_frame",
        "output_profile": {
            "preset_id": "legacy_h264",
            "container": "mp4",
            "video_codec": "h264",
            "video_profile": "baseline",
            "video_level": "3.1",
            "pixel_format": "yuv420p",
            "max_video_bitrate_kbps": 1000,
            "audio_codec": "aac",
            "audio_sample_rate": 48000,
            "audio_channel_layout": "stereo",
            "loudness_target_lufs": -16,
            "keyframe_interval_frames": 2,
            "b_frames": 0,
            "hardware_decoder_compatibility": "h264_baseline_level_3_1_yuv420p",
        },
    }
    layers = [
        _visual_layer("still-layer", "image", "still", 0, "straight"),
        _visual_layer("alpha-layer", "video", "alpha-video", 1, "straight"),
        _visual_layer("opaque-layer", "video", "opaque-video", 2, "opaque"),
        _audio_layer("audio-layer-a", "audio-a", 0),
        _audio_layer("audio-layer-b", "audio-b", 1),
    ]
    return {
        "schema_version": 1,
        "installation": {"schema_version": 1, "resources": []},
        "editor_metadata": {
            "compositions": {
                "projector-composition": {
                    "id": "projector-composition",
                    "name": "Projector",
                    "duration_ms": 1000,
                    "space_kind": "projector_native",
                    "target_id": "projector.left",
                    "width": 64,
                    "height": 36,
                    "local_time_offset_ms": 0,
                    "children": [],
                    "render_settings": render_settings,
                }
            },
            "timeline_transform_layers": {
                "projector-composition": layers,
            },
        },
        "scenes": [
            {
                "schema_version": 2,
                "id": "ambient",
                "name": "Ambient",
                "mode": "media_synchronized",
                "resources": ["projector.left", "lights"],
                "duration_ms": 1000,
                "clock_source": "left",
                "media": [
                    {
                        "id": "left",
                        "resource": "projector.left",
                        "asset": None,
                        "required": True,
                        "start_delay_ms": 0,
                    }
                ],
                "lighting": [
                    {
                        "id": "lights",
                        "resource": "lights",
                        "required": True,
                        "effect": "synthetic_sweep",
                    }
                ],
            }
        ],
    }


def _visual_layer(
    layer_id: str,
    kind: str,
    source_id: str,
    z_index: int,
    alpha: str,
) -> dict[str, object]:
    return {
        "layer_schema_version": 1,
        "id": layer_id,
        "name": layer_id,
        "kind": kind,
        "source_id": source_id,
        "track_id": "media:left",
        "start_ms": 0,
        "duration_ms": 1000,
        "in_point_ms": 0,
        "out_point_ms": 1000,
        "z_index": z_index,
        "fade_in_ms": 0,
        "fade_out_ms": 0,
        "audio": None,
        "alpha_interpretation": alpha,
        "source_color_space": "srgb",
        "scaling_behavior": "native",
        "keyframes": [
            {
                "id": f"{layer_id}-frame",
                "time_ms": 0,
                "position": [32, 18],
                "scale": [1, 1],
                "rotation_deg": 0,
                "anchor": [0.5, 0.5],
                "opacity": 0.5,
                "crop": [0, 0, 0, 0],
                "interpolation": {
                    "kind": "linear",
                    "temporal_bezier": None,
                    "spatial_in": None,
                    "spatial_out": None,
                },
            }
        ],
    }


def _audio_layer(layer_id: str, source_id: str, mix_order: int) -> dict[str, object]:
    return {
        "layer_schema_version": 1,
        "id": layer_id,
        "name": layer_id,
        "kind": "audio",
        "source_id": source_id,
        "track_id": "media:left",
        "start_ms": 0,
        "duration_ms": 1000,
        "in_point_ms": 0,
        "out_point_ms": 1000,
        "z_index": 0,
        "fade_in_ms": 20,
        "fade_out_ms": 20,
        "audio": {
            "gain_db": -6,
            "mute": False,
            "solo": False,
            "mix_order": mix_order,
        },
        "alpha_interpretation": None,
        "source_color_space": None,
        "scaling_behavior": None,
        "keyframes": [],
    }


def _write_wave(path: Path, frequency: float) -> None:
    time = np.arange(48000, dtype=np.float32) / 48000
    samples = (np.sin(2 * np.pi * frequency * time) * 12000).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(samples.tobytes())
