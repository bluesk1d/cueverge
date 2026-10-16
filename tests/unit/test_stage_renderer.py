import hashlib
import json
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import av
import numpy as np
from PIL import Image

from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject
from cueverge.gui.projector_overlap import StoredProjectorOverlap
from cueverge.gui.render_verification import compare_rgb_frames
from cueverge.gui.stage_coverage import StageCoverage, StageRegion
from cueverge.gui.stage_renderer import (
    preview_stage_composition_member,
    preview_stage_lighting_samples,
    render_stage_composition,
)
from cueverge.gui.sync_rehearsal import SyncAdmission


def test_stage_runner_creates_lockstep_projector_set_with_one_audio_owner(
    tmp_path: Path,
) -> None:
    image = tmp_path / "ghost.png"
    Image.fromarray(
        np.full((2, 2, 4), [255, 255, 255, 128], dtype=np.uint8),
        mode="RGBA",
    ).save(image)
    audio = tmp_path / "audio.wav"
    _write_wave(audio)
    assets = _FixtureAssets(
        {
            "ghost": ("image/png", image),
            "audio": ("audio/wav", audio),
        }
    )
    draft = DraftProject.model_validate(_draft())
    mapper = _mapper_export()
    coverage = _coverage()
    overlaps = (_overlap(),)
    preview = preview_stage_composition_member(
        project_id="demo-project",
        source_revision=2,
        draft=draft,
        composition_id="stage-composition",
        assets=cast(AssetUploadService, assets),
        mapper_export=mapper,
        coverage=coverage,
        overlaps=overlaps,
        resource="projector.left",
        timestamp_ms=0,
    )
    right_preview = preview_stage_composition_member(
        project_id="demo-project",
        source_revision=2,
        draft=draft,
        composition_id="stage-composition",
        assets=cast(AssetUploadService, assets),
        mapper_export=mapper,
        coverage=coverage,
        overlaps=overlaps,
        resource="projector.right",
        timestamp_ms=0,
    )
    lighting_preview = preview_stage_lighting_samples(
        project_id="demo-project",
        draft=draft,
        composition_id="stage-composition",
        timestamp_ms=0,
        assets=cast(AssetUploadService, assets),
        mapper_export=mapper,
        device_reported_scales={"lights": 0.5},
    )
    manifest = render_stage_composition(
        project_id="demo-project",
        source_revision=2,
        draft=draft,
        composition_id="stage-composition",
        assets=cast(AssetUploadService, assets),
        mapper_export=mapper,
        coverage=coverage,
        overlaps=overlaps,
        sync_admission=SyncAdmission(
            True,
            "sync_within_tolerance",
            "within tolerance",
            None,
        ),
        output_directory=tmp_path / "renders",
    )

    output_root = tmp_path / "renders" / str(manifest["activation_identity"])
    stored = json.loads((output_root / "render-manifest.json").read_text(encoding="utf-8"))
    assert stored["audio_owner"] == "projector.left"
    assert stored["calibration_revisions"] == {
        "projector.left": 3,
        "projector.right": 4,
    }
    assert {member["resource"]: member["has_audio"] for member in stored["members"]} == {
        "projector.left": True,
        "projector.right": False,
    }
    assert {member["streams"]["video"]["frame_count"] for member in stored["members"]} == {2}
    for member in stored["members"]:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(output_root / member["filename"]),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        stream_types = {stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]}
        assert ("audio" in stream_types) is member["has_audio"]
    left_member = next(
        member for member in stored["members"] if member["resource"] == "projector.left"
    )
    with av.open(str(output_root / left_member["filename"])) as container:
        baked = next(container.decode(video=0)).to_ndarray(format="rgba")
    comparison = compare_rgb_frames(preview, baked)
    assert comparison.passed, comparison
    assert np.count_nonzero(preview[..., :3]) > 0
    assert np.count_nonzero(right_preview[..., :3]) > 0
    assert np.count_nonzero(preview[..., :3]) < preview[..., :3].size
    assert np.count_nonzero(right_preview[..., :3]) < right_preview[..., :3].size
    samples = lighting_preview["surfaces"][0]["samples"]
    assert any(samples[0]["requested_color"])
    assert samples[0]["color"] == [
        round(channel * 0.25) for channel in samples[0]["requested_color"]
    ]
    assert lighting_preview["hardware_command_sent"] is False


class _FixtureAssets:
    def __init__(self, sources: dict[str, tuple[str, Path]]) -> None:
        self.sources = sources

    def get_original(self, asset_id: str) -> tuple[SimpleNamespace, Path]:
        media_type, path = self.sources[asset_id]
        return (
            SimpleNamespace(
                project_id="demo-project",
                digest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                media_type=media_type,
            ),
            path,
        )


def _draft() -> dict[str, object]:
    return {
        "schema_version": 1,
        "installation": {"schema_version": 1, "resources": []},
        "editor_metadata": {
            "compositions": {
                "stage-composition": {
                    "id": "stage-composition",
                    "name": "Stage",
                    "duration_ms": 1000,
                    "space_kind": "stage",
                    "target_id": "shared-stage",
                    "width": 8,
                    "height": 4,
                    "local_time_offset_ms": 0,
                    "children": [],
                    "render_settings": {
                        "render_pipeline_revision": 1,
                        "render_width": 128,
                        "render_height": 36,
                        "frame_rate_numerator": 2,
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
                            "hardware_decoder_compatibility": ("h264_baseline_level_3_1_yuv420p"),
                        },
                    },
                }
            },
            "scene_compositions": {"ambient": ["stage-composition"]},
            "timeline_transform_layers": {
                "stage-composition": [
                    _visual_layer(),
                    _audio_layer(),
                ]
            },
        },
        "scenes": [
            {
                "schema_version": 2,
                "id": "ambient",
                "name": "Ambient",
                "mode": "media_synchronized",
                "resources": ["projector.left", "projector.right", "lights"],
                "duration_ms": 1000,
                "clock_source": "left",
                "media": [
                    {
                        "id": "left",
                        "resource": "projector.left",
                        "asset": None,
                        "required": True,
                        "start_delay_ms": 0,
                    },
                    {
                        "id": "right",
                        "resource": "projector.right",
                        "asset": None,
                        "required": True,
                        "start_delay_ms": 0,
                    },
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


def _visual_layer() -> dict[str, object]:
    return {
        "layer_schema_version": 1,
        "id": "ghost-layer",
        "name": "Ghost",
        "kind": "image",
        "source_id": "ghost",
        "track_id": "stage-track",
        "start_ms": 0,
        "duration_ms": 1000,
        "in_point_ms": 0,
        "out_point_ms": 1000,
        "z_index": 0,
        "fade_in_ms": 0,
        "fade_out_ms": 0,
        "audio": None,
        "alpha_interpretation": "straight",
        "source_color_space": "srgb",
        "scaling_behavior": "native",
        "keyframes": [
            {
                "id": "ghost-frame",
                "time_ms": 0,
                "position": [4, 2],
                "scale": [1, 1],
                "rotation_deg": 0,
                "anchor": [0.5, 0.5],
                "opacity": 0.6,
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


def _audio_layer() -> dict[str, object]:
    return {
        "layer_schema_version": 1,
        "id": "audio-layer",
        "name": "Audio",
        "kind": "audio",
        "source_id": "audio",
        "track_id": "stage-track",
        "start_ms": 0,
        "duration_ms": 1000,
        "in_point_ms": 0,
        "out_point_ms": 1000,
        "z_index": 0,
        "fade_in_ms": 0,
        "fade_out_ms": 0,
        "audio": {
            "gain_db": 0,
            "mute": False,
            "solo": False,
            "mix_order": 0,
        },
        "alpha_interpretation": None,
        "source_color_space": None,
        "scaling_behavior": None,
        "keyframes": [],
    }


def _mapper_export() -> dict[str, object]:
    return {
        "projectors": [
            {
                "geometry_id": "left-geometry",
                "resource": "projector.left",
                "native_width": 64,
                "native_height": 36,
                "stage_extent": [0, 0, 5, 4],
                "surface_model": "planar",
                "mesh_vertices": [],
                "mesh_triangles": [],
                "brightness_limit": 1,
                "calibration_revision": 3,
            },
            {
                "geometry_id": "right-geometry",
                "resource": "projector.right",
                "native_width": 64,
                "native_height": 36,
                "stage_extent": [3, 0, 5, 4],
                "surface_model": "subdivided",
                "mesh_vertices": [
                    [0, 0, 3, 0],
                    [1, 0, 8, 0],
                    [1, 1, 8, 4],
                    [0, 1, 3, 4],
                    [0.5, 0.5, 5.25, 2.2],
                ],
                "mesh_triangles": [
                    [0, 1, 4],
                    [1, 2, 4],
                    [2, 3, 4],
                    [3, 0, 4],
                ],
                "brightness_limit": 1,
                "calibration_revision": 4,
            },
        ],
        "lighting_surfaces": [
            {
                "semantic_id": "stage-pixels",
                "resource": "lights",
                "local_width": 8,
                "local_height": 4,
                "brightness_limit": 0.5,
                "pixels": [
                    {
                        "output_index": 0,
                        "local_x": 4,
                        "local_y": 2,
                        "footprint_width": 1,
                        "footprint_height": 1,
                        "stage_x": 4,
                        "stage_y": 2,
                    }
                ],
            }
        ],
    }


def _coverage() -> StageCoverage:
    return StageCoverage(
        "demo-installation",
        0,
        0,
        8,
        4,
        (
            StageRegion(
                "covered",
                ((0, 0), (3, 0), (3, 4), (0, 4)),
                ("projector.left",),
                ("left",),
            ),
            StageRegion(
                "overlap",
                ((3, 0), (5, 0), (5, 4), (3, 4)),
                ("projector.left", "projector.right"),
                ("left", "right"),
            ),
            StageRegion(
                "covered",
                ((5, 0), (8, 0), (8, 4), (5, 4)),
                ("projector.right",),
                ("right",),
            ),
            StageRegion(
                "occluded",
                ((6, 1), (7, 1), (7, 2), (6, 2)),
                ("projector.right",),
                ("right-mask",),
            ),
        ),
    )


def _overlap() -> StoredProjectorOverlap:
    return StoredProjectorOverlap(
        id="overlap",
        installation_id="demo-installation",
        first_geometry_id="left-geometry",
        second_geometry_id="right-geometry",
        stage_points=((3, 0), (5, 0), (5, 4), (3, 4)),
        ramp_width=1,
        overlap_gamma=1,
        first_brightness=1,
        second_brightness=1,
        first_white=(1, 1, 1),
        second_white=(1, 1, 1),
        first_color_matrix=(1, 0, 0, 0, 1, 0, 0, 0, 1),
        second_color_matrix=(1, 0, 0, 0, 1, 0, 0, 0, 1),
        first_black_level=0,
        second_black_level=0,
        diagnostics=(),
        revision=1,
        created_at="2026-10-15T12:00:00Z",
        updated_at="2026-10-15T12:00:00Z",
    )


def _write_wave(path: Path) -> None:
    time = np.arange(48000, dtype=np.float32) / 48000
    samples = (np.sin(2 * np.pi * 440 * time) * 12000).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(samples.tobytes())
