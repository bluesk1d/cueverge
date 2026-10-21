import hashlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from PIL import Image
from pydantic import ValidationError

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import ShowPackageError, load_show_package
from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.lighting_bake import render_lighting_composition
from cueverge.gui.lighting_preview import (
    evaluate_lighting_preview,
    stage_points_to_surface,
)
from cueverge.gui.mapper_export import MapperExportService
from cueverge.gui.mcp_authoring import McpAuthoringError
from cueverge.gui.render_jobs import RenderJobError, RenderJobService
from cueverge.gui.repository import GuiRepository
from cueverge.gui.spatial_authoring import layout_digest, propose_effect_draft, spatial_context
from cueverge.gui.spatial_effects import SpatialRipple


def test_stage_points_are_mapped_to_normalized_surface_coordinates() -> None:
    assert stage_points_to_surface(
        ((1, 1), (5, 3), (9, 5)),
        stage_matrix=(2, 0, 0, 2, 1, 1),
        local_width=4,
        local_height=2,
    ) == ((0, 0), (0.5, 0.5), (1, 1))


def test_lighting_preview_is_deterministic_and_hardware_free() -> None:
    draft = DraftProject.model_validate(_draft())
    mapper = _mapper_export()
    assets = cast(AssetUploadService, object())

    first = evaluate_lighting_preview(
        project_id="demo-project",
        draft=draft,
        composition_id="lighting",
        timestamp_ms=500,
        assets=assets,
        mapper_export=mapper,
        device_reported_scale=0.5,
    )
    second = evaluate_lighting_preview(
        project_id="demo-project",
        draft=draft,
        composition_id="lighting",
        timestamp_ms=500,
        assets=assets,
        mapper_export=mapper,
        device_reported_scale=0.5,
    )

    assert first == second
    assert first["hardware_command_sent"] is False
    assert first["samples"] == [
        {
            "output_index": 2,
            "requested_color": [48, 0, 207],
            "color": [24, 0, 104],
        },
        {
            "output_index": 8,
            "requested_color": [0, 0, 255],
            "color": [0, 0, 128],
        },
    ]
    assert first["diagnostics"] == {
        "mapping_revision": 3,
        "calibration_id": "calibration-1",
        "mapping_stale": False,
        "unmapped_pixels": 0,
        "duplicate_output_indices": [],
        "device_reported_scale": 0.5,
    }
    assert first["performance_budget"] == {
        "profile": "interactive_30fps",
        "target_fps": 30,
        "maximum_canvas_pixels": 262144,
        "maximum_output_pixels": 4096,
        "maximum_author_layers": 128,
        "qualified": True,
        "exceeded_dimensions": [],
    }


def test_spatial_ripple_uses_physical_distance_and_shared_stage_origin() -> None:
    draft = DraftProject.model_validate(_draft())
    mapper = _mapper_export()
    effect = SpatialRipple(
        coordinate_space="stage",
        origin=(3.5, 3.5),
        speed=8,
        ring_width=2,
        color="#FF0000",
    )
    proposal = propose_effect_draft(
        draft,
        mapper,
        expected_layout_digest=layout_digest(mapper),
        composition_ids=["lighting"],
        effect=effect,
        start_ms=0,
        duration_ms=1000,
        blend_mode="replace",
        opacity=1,
    )
    assets = cast(AssetUploadService, object())
    at_start = evaluate_lighting_preview(
        project_id="p",
        draft=proposal,
        composition_id="lighting",
        timestamp_ms=0,
        assets=assets,
        mapper_export=mapper,
    )
    at_end = evaluate_lighting_preview(
        project_id="p",
        draft=proposal,
        composition_id="lighting",
        timestamp_ms=375,
        assets=assets,
        mapper_export=mapper,
    )
    assert at_start["samples"] == [
        {"output_index": 2, "requested_color": [255, 0, 0], "color": [255, 0, 0]},
        {"output_index": 8, "requested_color": [0, 0, 0], "color": [0, 0, 0]},
    ]
    assert cast(list[dict[str, object]], at_end["samples"])[1]["color"] == [255, 0, 0]
    assert draft.editor_metadata != proposal.editor_metadata
    assert (
        draft.editor_metadata["lighting_author_layers"]
        == DraftProject.model_validate(
            _draft(),
        ).editor_metadata["lighting_author_layers"]
    )
    with pytest.raises(McpAuthoringError, match="layout changed"):
        propose_effect_draft(
            draft,
            mapper,
            expected_layout_digest="stale",
            composition_ids=["lighting"],
            effect=effect,
            start_ms=0,
            duration_ms=1000,
            blend_mode="add",
            opacity=1,
        )
    with pytest.raises(ValidationError):
        SpatialRipple(origin=(0, 0), speed=float("nan"), ring_width=1, color="#FFFFFF")


def test_spatial_context_is_bounded_and_omits_physical_indices() -> None:
    mapper = _mapper_export()
    for surface in cast(list[dict[str, object]], mapper["lighting_surfaces"]):
        for pixel in cast(list[dict[str, object]], surface["pixels"]):
            pixel["stage_x"] = pixel["local_x"]
            pixel["stage_y"] = pixel["local_y"]
    context = spatial_context(DraftProject.model_validate(_draft()), mapper, pixel_limit=1)
    page = cast(dict[str, object], context["pixel_page"])
    assert page["total"] == 2
    assert page["next_offset"] == 1
    assert "output_index" not in cast(list[dict[str, object]], page["pixels"])[0]
    with pytest.raises(McpAuthoringError, match=r"0\.\.500"):
        spatial_context(DraftProject.model_validate(_draft()), mapper, pixel_limit=501)


def test_baked_lighting_package_matches_preview_and_preserves_unmapped_baseline(
    tmp_path: Path,
) -> None:
    document = _draft()
    document["installation"] = {
        "schema_version": 1,
        "resources": [
            {"resource": "projector.left", "kind": "media_agent", "target": "fake-media-1"},
            {"resource": "lights", "kind": "pixel_output", "target": "memory", "pixel_count": 9},
        ],
    }
    draft = DraftProject.model_validate(document)
    mapper = _mapper_export()
    assets = cast(AssetUploadService, object())
    unbaked = DraftCompiler(tmp_path / "unbaked").compile(
        project_id="p",
        revision=1,
        draft=draft,
    )
    with pytest.raises(ShowPackageError, match="bake"):
        load_show_package(unbaked.package_root)
    manifest = render_lighting_composition(
        project_id="p",
        source_revision=1,
        draft=draft,
        composition_id="lighting",
        assets=assets,
        mapper_export=mapper,
        output_directory=tmp_path / "renders",
    )
    artifact = tmp_path / "renders" / str(manifest["activation_identity"])
    assert manifest["frame_count"] == 30
    assert manifest["size_bytes"] == 180
    assert (
        hashlib.sha256((artifact / "frames.rgb").read_bytes()).hexdigest()
        == manifest["digest_sha256"]
    )
    compiled = DraftCompiler(tmp_path / "compiled").compile(
        project_id="p",
        revision=1,
        draft=draft,
        lighting_artifacts={"lighting": artifact},
    )
    catalog = load_show_package(compiled.package_root)
    output = catalog.require("scene").lighting_outputs[0]
    runtime = ControllerRuntime(catalog=catalog)
    preview = evaluate_lighting_preview(
        project_id="p",
        draft=draft,
        composition_id="lighting",
        timestamp_ms=500,
        assets=assets,
        mapper_export=mapper,
        include_canvas=True,
    )
    frame = runtime._render_lighting_output(
        output,
        render_position_ms=500,
        rendered_at=runtime.clock(),
        duration_ms=1000,
    )
    for sample in cast(list[dict[str, object]], preview["samples"]):
        assert list(frame[cast(int, sample["output_index"])]) == sample["color"]
    assert frame[0] == (0, 0, 0)
    limited = output.model_copy(
        update={
            "definition": output.definition.model_copy(update={"brightness_limit": 0.25}),
        }
    )
    limited_frame = runtime._render_lighting_output(
        limited,
        render_position_ms=500,
        rendered_at=runtime.clock(),
        duration_ms=1000,
    )
    assert limited_frame == tuple(
        tuple(round(channel * 0.25) for channel in color) for color in frame
    )
    assert (
        runtime._render_lighting_output(
            output,
            render_position_ms=500,
            rendered_at=runtime.clock(),
            duration_ms=1000,
        )
        == frame
    )
    assert isinstance(preview["canvas_png_base64"], str)
    sequence_path = compiled.package_root / output.spatial_sequences[0].reference.path
    sequence_path.write_bytes(b"x" * 180)
    with pytest.raises(ShowPackageError, match="checksum mismatch"):
        load_show_package(compiled.package_root)


def test_render_job_pins_mapping_revision_and_preserves_last_valid_bake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    document = _draft()
    metadata = cast(dict[str, object], document["editor_metadata"])
    metadata["installation_id"] = "installation"
    revision = repository.create_project(
        name="Spatial bake",
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-21T18:00:00Z",
    )
    mapper = _mapper_export()
    mapping = SimpleNamespace(export=lambda _: mapper)
    service = RenderJobService(
        repository,
        cast(AssetUploadService, object()),
        tmp_path / "renders",
        mapper_export=cast(MapperExportService, mapping),
    )
    monkeypatch.setattr(service, "_ensure_worker", lambda: None)
    first = service.create(project_id=revision.project.id, composition_id="lighting", actor="admin")
    claimed = service._next_job()
    assert claimed is not None
    service._process(claimed)
    completed = service.get(first.id)
    assert completed.state == "completed", completed.error
    assert set(service.lighting_artifacts(revision.project.id, 1)) == {"lighting"}
    cached = service.create(
        project_id=revision.project.id, composition_id="lighting", actor="admin"
    )
    assert cached.state == "completed"
    assert cached.artifact_id == completed.artifact_id
    cast(list[dict[str, object]], mapper["lighting_surfaces"])[0]["mapping_revision"] = 4
    with pytest.raises(RenderJobError, match="needs a current bake"):
        service.lighting_artifacts(revision.project.id, 1)
    second = service.create(
        project_id=revision.project.id, composition_id="lighting", actor="admin"
    )
    cast(list[dict[str, object]], mapper["lighting_surfaces"])[0]["mapping_revision"] = 5
    claimed = service._next_job()
    assert claimed is not None
    service._process(claimed)
    assert service.get(second.id).state == "failed"
    assert service.active_artifact_id(revision.project.id, "lighting") == completed.artifact_id


@pytest.mark.parametrize("media_type", ["image/gif", "video/mp4"])
def test_animated_media_overlay_bakes_the_same_seekable_frames(
    tmp_path: Path,
    media_type: str,
) -> None:
    source = tmp_path / "overlay.gif"
    Image.new("RGB", (2, 2), "red").save(
        source,
        save_all=True,
        append_images=[Image.new("RGB", (2, 2), "green")],
        duration=[100, 300],
        loop=0,
    )
    if media_type == "video/mp4":
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            pytest.skip("ffmpeg is required for the video fixture")
        video = tmp_path / "overlay.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(source),
                "-t",
                "0.4",
                "-vf",
                "fps=10",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv444p",
                str(video),
            ],
            check=True,
            capture_output=True,
        )
        source = video

    class FixtureAssets:
        def get_original(self, _: str) -> tuple[SimpleNamespace, Path]:
            return SimpleNamespace(
                project_id="p",
                media_type=media_type,
                metadata={"duration_ms": 400},
                digest_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ), source

    document = _draft()
    metadata = cast(dict[str, object], document["editor_metadata"])
    metadata["lighting_author_layers"] = {}
    metadata["timeline_transform_layers"] = {
        "lighting": [
            {
                "layer_schema_version": 1,
                "id": "overlay",
                "name": "Media overlay",
                "kind": "video",
                "source_id": "animation",
                "track_id": "lights",
                "start_ms": 0,
                "duration_ms": 1000,
                "in_point_ms": 0,
                "out_point_ms": 1000,
                "playback": "loop",
                "z_index": 0,
                "fade_in_ms": 0,
                "fade_out_ms": 0,
                "alpha_interpretation": "straight",
                "source_color_space": "srgb",
                "scaling_behavior": "stretch",
                "keyframes": [
                    {
                        "id": "frame",
                        "time_ms": 0,
                        "position": [2, 1],
                        "scale": [2, 1],
                        "rotation_deg": 0,
                        "anchor": [0.5, 0.5],
                        "opacity": 1,
                        "crop": [0, 0, 0, 0],
                        "interpolation": {"kind": "linear"},
                    }
                ],
            }
        ]
    }
    draft = DraftProject.model_validate(document)
    assets = cast(AssetUploadService, FixtureAssets())
    manifest = render_lighting_composition(
        project_id="p",
        source_revision=1,
        draft=draft,
        composition_id="lighting",
        assets=assets,
        mapper_export=_mapper_export(),
        output_directory=tmp_path / "bakes",
    )
    data = (tmp_path / "bakes" / str(manifest["activation_identity"]) / "frames.rgb").read_bytes()
    for frame_index in (0, 3, 12, 15, 29):
        preview = evaluate_lighting_preview(
            project_id="p",
            draft=draft,
            composition_id="lighting",
            timestamp_ms=frame_index * 1000 // 30,
            assets=assets,
            mapper_export=_mapper_export(),
        )
        expected = bytes(
            channel
            for sample in cast(list[dict[str, object]], preview["samples"])
            for channel in cast(list[int], sample["requested_color"])
        )
        assert data[frame_index * 6 : (frame_index + 1) * 6] == expected
    assert data[:6] == data[12 * 6 : 13 * 6]
    assert data[:6] != data[3 * 6 : 4 * 6]


def _draft() -> dict[str, object]:
    return {
        "schema_version": 1,
        "installation": {"schema_version": 1, "resources": []},
        "editor_metadata": {
            "compositions": {
                "lighting": {
                    "id": "lighting",
                    "name": "Facade",
                    "duration_ms": 1000,
                    "space_kind": "lighting_surface",
                    "target_id": "facade",
                    "width": 4,
                    "height": 2,
                    "local_time_offset_ms": 0,
                    "children": [],
                    "safe_points_ms": [],
                    "protected_intervals": [],
                    "lighting_settings": {
                        "canvas_width": 8,
                        "canvas_height": 4,
                        "sampling": "nearest",
                        "gamma": 1,
                        "brightness": 1,
                    },
                }
            },
            "scene_compositions": {"scene": ["lighting"]},
            "timeline_transform_layers": {"lighting": []},
            "lighting_author_layers": {
                "lighting": [
                    {
                        "id": "red",
                        "name": "Red",
                        "mode": "spatial",
                        "start_ms": 0,
                        "duration_ms": 1000,
                        "precedence": 0,
                        "blend_mode": "replace",
                        "opacity": 1,
                        "spatial": {
                            "primitive": "linear_gradient",
                            "color": "#FF0000",
                            "end_color": "#0000FF",
                            "seed": 1,
                        },
                    }
                ]
            },
        },
        "scenes": [
            {
                "schema_version": 2,
                "id": "scene",
                "name": "Scene",
                "mode": "media_synchronized",
                "resources": ["projector.left", "lights"],
                "duration_ms": 1000,
                "clock_source": "media",
                "media": [
                    {
                        "id": "media",
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


def _mapper_export() -> dict[str, object]:
    return {
        "lighting_surfaces": [
            {
                "semantic_id": "facade",
                "resource": "lights",
                "local_width": 4,
                "local_height": 2,
                "stage_matrix": [2, 0, 0, 2, 1, 1],
                "brightness_limit": 1,
                "mapping_revision": 3,
                "calibration_id": "calibration-1",
                "pixels": [
                    {
                        "output_index": 8,
                        "local_x": 3,
                        "local_y": 1,
                        "footprint_width": 0.1,
                        "footprint_height": 0.1,
                    },
                    {
                        "output_index": 2,
                        "local_x": 1,
                        "local_y": 1,
                        "footprint_width": 0.1,
                        "footprint_height": 0.1,
                    },
                ],
            }
        ],
        "paths": [],
        "fixtures": [],
    }
