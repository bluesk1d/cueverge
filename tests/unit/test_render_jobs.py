import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import cueverge.gui.render_jobs as render_jobs_module
from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.render_jobs import RenderJobService, StoredRenderJob
from cueverge.gui.repository import GuiRepository


def test_render_queue_caches_cancels_and_preserves_last_valid_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    revision = repository.create_project(
        name="Render fixture",
        draft=DraftProject.model_validate(_draft("#000000")),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-15T12:00:00Z",
    )
    gate = threading.Event()
    started = threading.Event()

    def render(**values: object) -> dict[str, object]:
        started.set()
        assert gate.wait(timeout=5)
        output_directory = cast(Path, values["output_directory"])
        activation = f"artifact-{values['source_revision']}"
        target = output_directory / activation
        target.mkdir(parents=True)
        (target / "member.mp4").write_bytes(b"valid")
        return {
            "activation_identity": activation,
            "members": [],
        }

    monkeypatch.setattr(render_jobs_module, "render_projector_composition", render)
    fixture_assets = _FixtureAssets()
    fixture_assets.project_id = revision.project.id
    service = RenderJobService(
        repository,
        cast(AssetUploadService, fixture_assets),
        tmp_path / "renders",
    )
    first = service.create(
        project_id=revision.project.id,
        composition_id="projector-composition",
        actor="admin",
    )
    assert started.wait(timeout=5)
    assert _wait_state(service, first.id, "running").progress >= 0.05

    queued = service.create(
        project_id=revision.project.id,
        composition_id="projector-composition",
        actor="admin",
    )
    canceled = service.cancel(queued.id)
    assert canceled.state == "canceled"
    gate.set()
    completed = _wait_state(service, first.id, "completed")
    assert completed.artifact_id == "artifact-1"
    assert service.active_artifact_id(revision.project.id, "projector-composition") == "artifact-1"

    cache_hit = service.create(
        project_id=revision.project.id,
        composition_id="projector-composition",
        actor="admin",
    )
    assert cache_hit.state == "completed"
    assert cache_hit.artifact_id == "artifact-1"

    repository.update_draft(
        project_id=revision.project.id,
        expected_revision=1,
        draft=DraftProject.model_validate(_draft("#112233")),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-15T12:05:00Z",
    )

    def fail(**_: object) -> dict[str, object]:
        raise RuntimeError("encoder unavailable")

    monkeypatch.setattr(render_jobs_module, "render_projector_composition", fail)
    failed = service.create(
        project_id=revision.project.id,
        composition_id="projector-composition",
        actor="admin",
    )
    failed = _wait_state(service, failed.id, "failed")
    assert failed.error == "encoder unavailable"
    assert failed.cache_key != first.cache_key
    assert service.active_artifact_id(revision.project.id, "projector-composition") == "artifact-1"

    monkeypatch.setattr(render_jobs_module, "render_projector_composition", render)
    replacement = service.create(
        project_id=revision.project.id,
        composition_id="projector-composition",
        actor="admin",
    )
    replacement = _wait_state(service, replacement.id, "completed")
    assert replacement.artifact_id == "artifact-2"
    assert replacement.cache_key == failed.cache_key
    assert service.active_artifact_id(revision.project.id, "projector-composition") == "artifact-2"
    assert len(service.list(revision.project.id)) == 5


def test_render_cache_key_covers_source_timeline_profile_and_calibration() -> None:
    base = _draft("#000000")
    composition = cast(
        dict[str, object],
        cast(dict[str, object], base["editor_metadata"])["compositions"],
    )["projector-composition"]
    assert isinstance(composition, dict)
    key = render_jobs_module._cache_key(
        composition=composition,
        source_checksums={"asset-1": "a" * 64},
        source_revision=1,
        environment={"calibration_revision": 1},
    )
    assert key != render_jobs_module._cache_key(
        composition=composition,
        source_checksums={"asset-1": "b" * 64},
        source_revision=1,
        environment={"calibration_revision": 1},
    )
    assert key != render_jobs_module._cache_key(
        composition=composition,
        source_checksums={"asset-1": "a" * 64},
        source_revision=2,
        environment={"calibration_revision": 1},
    )
    assert key != render_jobs_module._cache_key(
        composition=composition,
        source_checksums={"asset-1": "a" * 64},
        source_revision=1,
        environment={"calibration_revision": 2},
    )


class _FixtureAssets:
    def get_asset(self, asset_id: str) -> SimpleNamespace:
        return (
            SimpleNamespace(
                project_id="unused",
                digest_sha256=None,
            )
            if asset_id != "asset-1"
            else SimpleNamespace(
                project_id=self.project_id,
                digest_sha256="a" * 64,
            )
        )

    @property
    def project_id(self) -> str:
        return self._project_id

    @project_id.setter
    def project_id(self, value: str) -> None:
        self._project_id = value

    _project_id = ""


def _wait_state(
    service: RenderJobService,
    job_id: str,
    state: str,
) -> StoredRenderJob:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = service.get(job_id)
        if job.state == state:
            return job
        time.sleep(0.02)
    raise AssertionError(f"render job {job_id} did not reach {state}")


def _draft(background: str) -> dict[str, object]:
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
                    "render_settings": {
                        "render_pipeline_revision": 1,
                        "render_width": 64,
                        "render_height": 36,
                        "frame_rate_numerator": 4,
                        "frame_rate_denominator": 1,
                        "working_color_space": "linear_srgb",
                        "working_alpha_mode": "premultiplied",
                        "scaling_filter": "lanczos",
                        "output_background_color": background,
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
            "timeline_transform_layers": {
                "projector-composition": [
                    {
                        "layer_schema_version": 1,
                        "id": "layer",
                        "name": "Layer",
                        "kind": "image",
                        "source_id": "asset-1",
                        "track_id": "media:left",
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
                                "id": "frame",
                                "time_ms": 0,
                                "position": [32, 18],
                                "scale": [1, 1],
                                "rotation_deg": 0,
                                "anchor": [0.5, 0.5],
                                "opacity": 1,
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
                ]
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
