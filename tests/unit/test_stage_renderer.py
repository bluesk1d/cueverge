import copy
import hashlib
import json
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import av
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import ShowPackageError, load_show_package
from cueverge.gui.artifact_deployment import ArtifactDeploymentService
from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.compiler import CompilationError, DraftCompiler
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.frame_compositor import compose_visual_frame
from cueverge.gui.projector_overlap import StoredProjectorOverlap
from cueverge.gui.projector_renderer import ProjectorRenderError
from cueverge.gui.publication import PublicationService
from cueverge.gui.render_jobs import RenderJobError, RenderJobService, _estimate_reservation
from cueverge.gui.render_verification import compare_rgb_frames
from cueverge.gui.repository import GuiRepository
from cueverge.gui.stage_coverage import StageCoverage, StageRegion
from cueverge.gui.stage_renderer import (
    bakes_lighting_only,
    preview_stage_composition_member,
    preview_stage_lighting_samples,
    render_stage_composition,
    selected_stage_lighting_surfaces,
)
from cueverge.gui.sync_rehearsal import SyncAdmission
from tests.unit.media_fixtures import stream_types


@pytest.mark.parametrize("mode", ["media_synchronized", "controller_timed"])
def test_stage_runner_creates_lockstep_projector_set_with_one_audio_owner(
    tmp_path: Path,
    mode: str,
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
    document = _draft()
    scene = cast(list[dict[str, object]], document["scenes"])[0]
    scene["mode"] = mode
    if mode == "controller_timed":
        scene["clock_source"] = "controller"
    draft = DraftProject.model_validate(document)
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
        assert ("audio" in stream_types(output_root / member["filename"])) is member["has_audio"]
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


@pytest.mark.parametrize(
    ("numerator", "denominator", "projector_count"),
    [(2, 1, 2), (30000, 1001, 2), (2, 1, 1)],
)
def test_installation_overlay_bakes_one_animation_for_projectors_and_leds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    numerator: int,
    denominator: int,
    projector_count: int,
) -> None:
    # Fills the 8 x 4 stage at its shape-matching 128 x 64 render raster.
    first = np.zeros((64, 128, 3), dtype=np.uint8)
    first[:, :64] = [255, 0, 0]
    first[:, 64:] = [0, 255, 0]
    second = np.zeros_like(first)
    second[:, :64] = [0, 0, 255]
    second[:, 64:] = [255, 255, 0]
    gif = tmp_path / "flag.gif"
    Image.fromarray(first).save(
        gif,
        save_all=True,
        append_images=[Image.fromarray(second)],
        duration=[500, 500],
    )
    assets = cast(AssetUploadService, _FixtureAssets({"ghost": ("image/gif", gif)}))
    document = _draft()
    document["installation"] = {
        "schema_version": 1,
        "resources": [
            {"resource": name, "kind": "media_agent", "target": name}
            for name in ("projector.left", "projector.right")
        ]
        + [
            {"resource": "lights", "kind": "pixel_output", "target": "memory", "pixel_count": 10},
        ],
    }
    metadata = cast(dict[str, object], document["editor_metadata"])
    compositions = cast(dict[str, dict[str, object]], metadata["compositions"])
    composition = compositions["stage-composition"]
    composition["stage_lighting_surfaces"] = ["stage-pixels", "right-pixels"]
    settings = cast(dict[str, object], composition["render_settings"])
    settings["frame_rate_numerator"] = numerator
    settings["frame_rate_denominator"] = denominator
    settings["render_height"] = 64
    layers = cast(dict[str, list[dict[str, object]]], metadata["timeline_transform_layers"])
    layers["stage-composition"] = [
        {
            **_visual_layer(),
            "kind": "video",
            "playback": "loop",
            "keyframes": [
                {
                    **cast(list[dict[str, object]], _visual_layer()["keyframes"])[0],
                    "opacity": 1,
                }
            ],
        }
    ]
    metadata["timeline_tracks"] = {
        "stage-composition": [
            {
                "id": "stage-track",
                "name": "Overlay",
                "kind": "group",
                "resource_binding": "projector.left",
            }
        ]
    }
    if projector_count == 1:
        scene = cast(list[dict[str, object]], document["scenes"])[0]
        cast(list[str], scene["resources"]).remove("projector.right")
        scene["media"] = cast(list[dict[str, object]], scene["media"])[:1]
    draft = DraftProject.model_validate(document)
    mapper = _mapper_export()
    mapper["projectors"] = cast(list[dict[str, object]], mapper["projectors"])[:projector_count]
    surface = cast(list[dict[str, object]], mapper["lighting_surfaces"])[0]
    pixel = cast(list[dict[str, object]], surface["pixels"])[0]
    pixel.update({"output_index": 8, "stage_x": 1, "stage_y": 3})
    right = copy.deepcopy(surface)
    right["semantic_id"] = "right-pixels"
    cast(list[dict[str, object]], right["pixels"])[0].update(
        {
            "output_index": 2,
            "stage_x": 7.5,
        }
    )
    mapper["lighting_surfaces"] = [surface, right]
    manifest = render_stage_composition(
        project_id="demo-project",
        source_revision=2,
        draft=draft,
        composition_id="stage-composition",
        assets=assets,
        mapper_export=mapper,
        coverage=_coverage(),
        overlaps=(),
        sync_admission=SyncAdmission(True, "test", "test", None),
        output_directory=tmp_path / "renders",
    )
    artifact = tmp_path / "renders" / str(manifest["activation_identity"])
    sequences = cast(list[dict[str, object]], manifest["lighting_sequences"])
    assert len(sequences) == 1
    assert sequences[0]["output_indices"] == [2, 8]
    assert sequences[0]["frame_rate"] == numerator
    assert sequences[0]["frame_rate_denominator"] == denominator
    assert manifest["frame_rate"] == {"numerator": numerator, "denominator": denominator}
    compiled = DraftCompiler(tmp_path / "compiled").compile(
        project_id="demo-project",
        revision=2,
        draft=draft,
        lighting_artifacts={"stage-composition": artifact},
    )
    catalog = load_show_package(compiled.package_root)
    output = catalog.require("ambient").lighting_outputs[0]
    runtime = ControllerRuntime(catalog=catalog)
    for timestamp in (0, 500, 900):
        preview = preview_stage_lighting_samples(
            project_id="demo-project",
            draft=draft,
            composition_id="stage-composition",
            timestamp_ms=timestamp,
            assets=assets,
            mapper_export=mapper,
            device_reported_scales={},
        )
        rendered = runtime._render_lighting_output(
            output,
            render_position_ms=timestamp,
            rendered_at=runtime.clock(),
            duration_ms=1000,
        )
        for sampled_surface in cast(list[dict[str, object]], preview["surfaces"]):
            sample = cast(list[dict[str, object]], sampled_surface["samples"])[0]
            assert list(rendered[cast(int, sample["output_index"])]) == sample["color"]
        assert rendered[0] == (0, 0, 0)
        with patch(
            "cueverge.gui.stage_renderer.compose_visual_frame", wraps=compose_visual_frame
        ) as compose:
            projector = preview_stage_composition_member(
                project_id="demo-project",
                source_revision=2,
                draft=draft,
                composition_id="stage-composition",
                assets=assets,
                mapper_export=mapper,
                coverage=_coverage(),
                overlaps=(),
                resource="projector.left",
                timestamp_ms=timestamp,
            )
            assert compose.call_count == 1
            frame_index = timestamp * numerator // (1000 * denominator)
            assert compose.call_args.kwargs["timestamp_ms"] == (
                frame_index * 1000 * denominator // numerator
            )
        # Projector preview conforms to lossy YUV; LED calibration halves the source RGB.
        assert np.max(np.abs(np.asarray(rendered[8]) - projector[27, 12, :3] * 0.5)) <= 3
    assert output.spatial_sequences[0].frame(1000) is None
    assert output.spatial_sequences[0].frame(-1) is None
    repository = GuiRepository(tmp_path / "publication.sqlite3")
    repository.migrate()
    stored = repository.create_project(
        name="House overlay",
        draft=draft,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-21T18:00:00Z",
    )
    repository.update_draft(
        project_id=stored.project.id,
        expected_revision=1,
        draft=draft.model_copy(
            update={"editor_metadata": {**draft.editor_metadata, "note": "revision 2"}}
        ),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-21T18:01:00Z",
    )
    jobs = cast(
        RenderJobService,
        SimpleNamespace(
            lighting_artifacts=lambda _project_id, _revision: {"stage-composition": artifact},
            current_lighting_artifacts=lambda _project_id, _revision: {
                "stage-composition": artifact
            },
            list=lambda _project_id: [
                SimpleNamespace(
                    source_revision=2,
                    state="completed",
                    composition_id="stage-composition",
                    artifact_id=artifact.name,
                ),
                SimpleNamespace(
                    source_revision=2,
                    state="completed",
                    composition_id="stage-composition",
                    artifact_id="obsolete-stage-bake",
                ),
            ],
        ),
    )
    publication = PublicationService(
        repository=repository,
        compiler=DraftCompiler(tmp_path / "publication-compiled"),
        assets=cast(AssetUploadService, SimpleNamespace(list_assets=lambda _: [])),
        render_jobs=jobs,
        deployment=cast(ArtifactDeploymentService, object()),
        output_root=tmp_path / "published",
    )
    publication.update_release(
        project_id=stored.project.id,
        revision=2,
        name="Flag",
        summary="Mixed artifact selection",
        approval_state="approved",
        actor="admin",
        occurred_at="2026-10-21T18:02:00Z",
    )
    # Device/asset admission is tested separately; exercise publication's artifact selection here.
    monkeypatch.setattr(publication, "validate", lambda _project_id, _revision: ())
    monkeypatch.setattr(repository, "get_render_artifact_manifest", lambda _: manifest)
    published = publication.publish(
        project_id=stored.project.id,
        revision=2,
        actor="admin",
        occurred_at="2026-10-21T18:03:00Z",
    )
    assert published.manifest["render_artifact_ids"] == [artifact.name]
    reference = output.spatial_sequences[0].reference
    (compiled.package_root / reference.path).write_bytes(
        b"x" * len(output.spatial_sequences[0].data)
    )
    with pytest.raises(ShowPackageError, match="checksum"):
        load_show_package(compiled.package_root)


def test_lights_only_overlay_bakes_led_sequences_without_projector_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "stripes.png"
    pixels = np.zeros((36, 128, 3), dtype=np.uint8)
    pixels[:, :64] = [255, 0, 0]
    pixels[:, 64:] = [255, 200, 0]
    Image.fromarray(pixels).save(image)
    audio = tmp_path / "audio.wav"
    _write_wave(audio)
    assets = cast(
        AssetUploadService,
        _FixtureAssets({"ghost": ("image/png", image), "audio": ("audio/wav", audio)}),
    )
    document = _draft()
    document["installation"] = {
        "schema_version": 1,
        "resources": [
            {"resource": name, "kind": "media_agent", "target": name}
            for name in ("projector.left", "projector.right")
        ]
        + [{"resource": "lights", "kind": "pixel_output", "target": "memory", "pixel_count": 10}],
    }
    metadata = cast(dict[str, object], document["editor_metadata"])
    composition = cast(dict[str, dict[str, object]], metadata["compositions"])["stage-composition"]
    composition["stage_projector_resources"] = []
    composition["stage_lighting_surfaces"] = []
    with pytest.raises(ValidationError, match="lights-only"):
        DraftProject.model_validate(document)
    composition["stage_lighting_surfaces"] = ["stage-pixels", "right-pixels"]
    layers = cast(dict[str, list[dict[str, object]]], metadata["timeline_transform_layers"])
    layers["stage-composition"] = [_visual_layer(), _audio_layer()]
    metadata["timeline_tracks"] = {
        "stage-composition": [
            {
                "id": "stage-track",
                "name": "Media",
                "kind": "group",
                "resource_binding": "shared-stage",
            }
        ]
    }
    mapper = _mapper_export()
    surface = cast(list[dict[str, object]], mapper["lighting_surfaces"])[0]
    cast(list[dict[str, object]], surface["pixels"])[0].update(
        {"output_index": 8, "stage_x": 1, "stage_y": 2}
    )
    right = copy.deepcopy(surface)
    right["semantic_id"] = "right-pixels"
    cast(list[dict[str, object]], right["pixels"])[0].update({"output_index": 2, "stage_x": 7})
    mapper["lighting_surfaces"] = [surface, right]
    render = {
        "project_id": "demo-project",
        "source_revision": 2,
        "composition_id": "stage-composition",
        "assets": assets,
        "mapper_export": mapper,
        "coverage": _coverage(),
        "overlaps": (),
        "sync_admission": SyncAdmission(True, "lights_only_stage", "test", None),
        "output_directory": tmp_path / "renders",
    }
    with pytest.raises(ProjectorRenderError, match="audio"):
        render_stage_composition(draft=DraftProject.model_validate(document), **render)
    layers["stage-composition"] = [{**_visual_layer(), "kind": "image"}]
    draft = DraftProject.model_validate(document)
    assert bakes_lighting_only(composition)
    frames: list[tuple[int, int]] = []
    with patch("cueverge.gui.stage_renderer.render_stage_to_projector") as projector_render:
        manifest = render_stage_composition(
            draft=draft, **render, on_frame=lambda index, total: frames.append((index, total))
        )
    projector_render.assert_not_called()
    assert frames == [(0, 2), (1, 2)]

    def cancel(index: int, total: int) -> None:
        raise InterruptedError("canceled")

    with pytest.raises(InterruptedError):
        render_stage_composition(draft=draft, **render, on_frame=cancel)
    assert _estimate_reservation(composition) < 300 * 1024 * 1024
    assert manifest["lights_only"] is True
    assert manifest["members"] == []
    assert manifest["calibration_revisions"] == {}
    sequences = cast(list[dict[str, object]], manifest["lighting_sequences"])
    assert [item["output_indices"] for item in sequences] == [[2, 8]]
    artifact = tmp_path / "renders" / str(manifest["activation_identity"])
    # A black background shows whatever lighting is underneath, so LEDs record coverage.
    assert sorted(path.name for path in artifact.iterdir()) == [
        "lighting-0001.rgba",
        "render-manifest.json",
    ]
    assert sequences[0]["channels"] == 4
    compiled = DraftCompiler(tmp_path / "compiled").compile(
        project_id="demo-project",
        revision=2,
        draft=draft,
        lighting_artifacts={"stage-composition": artifact},
    )
    catalog = load_show_package(compiled.package_root)
    output = catalog.require("ambient").lighting_outputs[0]
    runtime = ControllerRuntime(catalog=catalog)
    preview = preview_stage_lighting_samples(
        project_id="demo-project",
        draft=draft,
        composition_id="stage-composition",
        timestamp_ms=0,
        assets=assets,
        mapper_export=mapper,
        device_reported_scales={},
    )
    rendered = runtime._render_lighting_output(
        output,
        render_position_ms=0,
        rendered_at=runtime.clock(),
        duration_ms=1000,
    )
    for sampled in cast(list[dict[str, object]], preview["surfaces"]):
        sample = cast(list[dict[str, object]], sampled["samples"])[0]
        assert list(rendered[cast(int, sample["output_index"])]) == sample["color"]
    assert rendered[2] != rendered[8]
    duplicated = copy.deepcopy(document)
    duplicated["editor_metadata"]["composition_instances"] = {
        "ambient": [
            {
                "id": f"copy-{index}",
                "composition_id": "stage-composition",
                "start_offset_ms": 0,
                "time_scale": 1,
                "parameter_overrides": {},
                "placement": {"x": 0, "y": 0},
            }
            for index in range(2)
        ]
    }
    with pytest.raises(CompilationError, match="placed more than once"):
        DraftCompiler(tmp_path / "duplicate").compile(
            project_id="demo-project",
            revision=2,
            draft=DraftProject.model_validate(duplicated),
            lighting_artifacts={"stage-composition": artifact},
        )

    repository = GuiRepository(tmp_path / "publication.sqlite3")
    repository.migrate()
    stored = repository.create_project(
        name="Lights overlay",
        draft=draft,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-21T18:00:00Z",
    )
    repository.update_draft(
        project_id=stored.project.id,
        expected_revision=1,
        draft=draft.model_copy(
            update={"editor_metadata": {**draft.editor_metadata, "note": "revision 2"}}
        ),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-21T18:01:00Z",
    )
    jobs = cast(
        RenderJobService,
        SimpleNamespace(
            lighting_artifacts=lambda _project_id, _revision: {"stage-composition": artifact},
            current_lighting_artifacts=lambda _project_id, _revision: {
                "stage-composition": artifact
            },
            list=lambda _project_id: [
                SimpleNamespace(
                    source_revision=2,
                    state="completed",
                    composition_id="stage-composition",
                    artifact_id=artifact.name,
                )
            ],
        ),
    )
    publication = PublicationService(
        repository=repository,
        compiler=DraftCompiler(tmp_path / "publication-compiled"),
        assets=cast(AssetUploadService, SimpleNamespace(list_assets=lambda _: [])),
        render_jobs=jobs,
        deployment=cast(ArtifactDeploymentService, object()),
        output_root=tmp_path / "published",
    )
    publication.update_release(
        project_id=stored.project.id,
        revision=2,
        name="Stripes",
        summary="Lights-only overlay",
        approval_state="approved",
        actor="admin",
        occurred_at="2026-10-21T18:02:00Z",
    )
    monkeypatch.setattr(publication, "validate", lambda _project_id, _revision: ())
    published = publication.publish(
        project_id=stored.project.id,
        revision=2,
        actor="admin",
        occurred_at="2026-10-21T18:03:00Z",
    )
    # LED data travels in the controller package, not the media-agent artifact set.
    assert published.manifest["render_artifact_ids"] == []
    assert any(
        str(item["path"]).startswith("runtime/lighting/")
        for item in cast(list[dict[str, object]], published.manifest["files"])
    )


def test_lights_only_stage_admission_skips_projector_synchronization(tmp_path: Path) -> None:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    mapper = _mapper_export()
    rehearsal = SimpleNamespace(
        evaluate=lambda **_: pytest.fail("lights-only overlays need no projector rehearsal")
    )
    service = RenderJobService(
        repository,
        cast(AssetUploadService, _FixtureAssets({})),
        tmp_path / "renders",
        mapper_export=cast(Any, SimpleNamespace(export=lambda _: mapper)),
        stage_coverage=cast(Any, SimpleNamespace(analyze=lambda _: _coverage())),
        projector_overlap=cast(Any, SimpleNamespace(list=lambda _: ())),
        sync_rehearsal=cast(Any, rehearsal),
    )
    _, _, _, admission = service._stage_inputs(
        {"installation_id": "demo-installation"},
        {"space_kind": "stage", "stage_projector_resources": []},
    )
    assert admission.allowed
    assert admission.code == "lights_only_stage"


def test_stage_lighting_selection_rejects_missing_duplicate_and_out_of_bounds_surfaces(
    tmp_path: Path,
) -> None:
    document = _draft()
    metadata = cast(dict[str, object], document["editor_metadata"])
    composition = cast(dict[str, dict[str, object]], metadata["compositions"])["stage-composition"]
    composition["stage_lighting_surfaces"] = ["stage-pixels", "stage-pixels"]
    with pytest.raises(ValidationError, match="unique"):
        DraftProject.model_validate(document)
    composition["stage_lighting_surfaces"] = ["not-mapped"]
    with pytest.raises(ProjectorRenderError, match="not mapped"):
        selected_stage_lighting_surfaces(composition, _mapper_export())
    composition["stage_lighting_surfaces"] = ["stage-pixels"]
    cast(dict[str, list[dict[str, object]]], metadata["timeline_transform_layers"])[
        "stage-composition"
    ] = [_visual_layer()]
    image = tmp_path / "ghost.png"
    Image.fromarray(np.full((36, 128, 3), 255, dtype=np.uint8)).save(image)
    mapper = _mapper_export()
    surface = cast(list[dict[str, object]], mapper["lighting_surfaces"])[0]
    cast(list[dict[str, object]], surface["pixels"])[0]["stage_x"] = 9
    with pytest.raises(ProjectorRenderError, match="outside"):
        preview_stage_lighting_samples(
            project_id="demo-project",
            draft=DraftProject.model_validate(document),
            composition_id="stage-composition",
            timestamp_ms=0,
            assets=cast(
                AssetUploadService,
                _FixtureAssets(
                    {
                        "ghost": ("image/png", image),
                        "audio": ("image/png", image),
                    }
                ),
            ),
            mapper_export=mapper,
            device_reported_scales={},
        )


def test_mixed_bake_cache_tracks_layers_and_mapping_and_preserves_last_valid_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    document = _draft()
    metadata = cast(dict[str, object], document["editor_metadata"])
    composition = cast(dict[str, dict[str, object]], metadata["compositions"])["stage-composition"]
    composition["stage_lighting_surfaces"] = ["stage-pixels"]
    stored = repository.create_project(
        name="House overlay",
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-21T18:00:00Z",
    )
    image = tmp_path / "ghost.png"
    Image.fromarray(np.full((36, 128, 3), 255, dtype=np.uint8)).save(image)
    audio = tmp_path / "audio.wav"
    _write_wave(audio)
    assets = _FixtureAssets({"ghost": ("image/png", image), "audio": ("audio/wav", audio)})
    assets.project_id = stored.project.id
    service = RenderJobService(repository, cast(AssetUploadService, assets), tmp_path / "renders")
    mapper = _mapper_export()
    monkeypatch.setattr(service, "_ensure_worker", lambda: None)
    monkeypatch.setattr(
        service,
        "_stage_inputs",
        lambda *_: (
            copy.deepcopy(mapper),
            _coverage(),
            (),
            SyncAdmission(True, "test", "test", None),
        ),
    )
    first = service.create(
        project_id=stored.project.id,
        composition_id="stage-composition",
        actor="admin",
    )
    claimed = service._next_job()
    assert claimed is not None
    service._process(claimed)
    completed = service.get(first.id)
    assert completed.state == "completed", completed.error
    assert service.lighting_artifacts(stored.project.id, 1)["stage-composition"].name == (
        completed.artifact_id
    )
    cached = service.create(
        project_id=stored.project.id,
        composition_id="stage-composition",
        actor="admin",
    )
    assert cached.state == "completed"
    assert cached.artifact_id == completed.artifact_id
    changed = copy.deepcopy(stored.draft.editor_metadata)
    layer_map = cast(dict[str, list[dict[str, object]]], changed["timeline_transform_layers"])
    layer_map["stage-composition"][0]["playback"] = "loop"
    assert service._render_inputs(changed, "stage-composition", stored.project.id) != (
        service._render_inputs(stored.draft.editor_metadata, "stage-composition", stored.project.id)
    )
    surface = cast(list[dict[str, object]], mapper["lighting_surfaces"])[0]
    surface["brightness_limit"] = 0.25
    with pytest.raises(RenderJobError, match="current bake"):
        service.lighting_artifacts(stored.project.id, 1)

    replacement = service.create(
        project_id=stored.project.id,
        composition_id="stage-composition",
        actor="admin",
    )
    calls = 0

    def changing_stage_inputs(
        *_: object,
    ) -> tuple[dict[str, object], StageCoverage, tuple[StoredProjectorOverlap, ...], SyncAdmission]:
        nonlocal calls
        calls += 1
        if calls == 3:
            surface["brightness_limit"] = 0.75
        return copy.deepcopy(mapper), _coverage(), (), SyncAdmission(True, "test", "test", None)

    monkeypatch.setattr(service, "_stage_inputs", changing_stage_inputs)
    claimed = service._next_job()
    assert claimed is not None
    service._process(claimed)
    failed = service.get(replacement.id)
    assert failed.state == "failed"
    assert failed.error is not None and "changed during" in failed.error
    assert (
        service.active_artifact_id(stored.project.id, "stage-composition") == completed.artifact_id
    )


class _FixtureAssets:
    project_id = "demo-project"

    def __init__(self, sources: dict[str, tuple[str, Path]]) -> None:
        self.sources = sources

    def get_original(self, asset_id: str) -> tuple[SimpleNamespace, Path]:
        media_type, path = self.sources[asset_id]
        return (
            SimpleNamespace(
                project_id=self.project_id,
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


def test_lights_only_bake_composes_empty_frames_once_with_identical_output(
    tmp_path: Path,
) -> None:
    image = tmp_path / "stripes.png"
    pixels = np.zeros((36, 128, 3), dtype=np.uint8)
    pixels[:, :64] = [255, 0, 0]
    pixels[:, 64:] = [255, 200, 0]
    Image.fromarray(pixels).save(image)
    assets = cast(AssetUploadService, _FixtureAssets({"ghost": ("image/png", image)}))
    document = _draft()
    document["installation"] = {
        "schema_version": 1,
        "resources": [
            {"resource": name, "kind": "media_agent", "target": name}
            for name in ("projector.left", "projector.right")
        ]
        + [{"resource": "lights", "kind": "pixel_output", "target": "memory", "pixel_count": 10}],
    }
    scene = cast(list[dict[str, object]], document["scenes"])[0]
    scene["duration_ms"] = 10_000
    metadata = cast(dict[str, object], document["editor_metadata"])
    composition = cast(dict[str, dict[str, object]], metadata["compositions"])["stage-composition"]
    composition["duration_ms"] = 10_000
    composition["stage_projector_resources"] = []
    composition["stage_lighting_surfaces"] = ["stage-pixels", "right-pixels"]
    settings = cast(dict[str, object], composition["render_settings"])
    settings["output_background_color"] = "#102030"
    layers = cast(dict[str, list[dict[str, object]]], metadata["timeline_transform_layers"])
    layers["stage-composition"] = [
        {**_visual_layer(), "start_ms": 7000, "duration_ms": 2000, "out_point_ms": 2000}
    ]
    metadata["timeline_tracks"] = {
        "stage-composition": [
            {
                "id": "stage-track",
                "name": "Media",
                "kind": "group",
                "resource_binding": "shared-stage",
            }
        ]
    }
    mapper = _mapper_export()
    surface = cast(list[dict[str, object]], mapper["lighting_surfaces"])[0]
    cast(list[dict[str, object]], surface["pixels"])[0].update(
        {"output_index": 8, "stage_x": 1, "stage_y": 2}
    )
    right = copy.deepcopy(surface)
    right["semantic_id"] = "right-pixels"
    cast(list[dict[str, object]], right["pixels"])[0].update({"output_index": 2, "stage_x": 7})
    mapper["lighting_surfaces"] = [surface, right]
    draft = DraftProject.model_validate(document)
    assert bakes_lighting_only(composition)

    def bake(directory: str) -> tuple[bytes, int, int]:
        calls = 0
        frames = 0

        def counted(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            return compose_visual_frame(*args, **kwargs)

        def progress(index: int, total: int) -> None:
            nonlocal frames
            frames += 1

        with patch("cueverge.gui.stage_renderer.compose_visual_frame", side_effect=counted):
            manifest = render_stage_composition(
                project_id="demo-project",
                source_revision=2,
                draft=draft,
                composition_id="stage-composition",
                assets=assets,
                mapper_export=mapper,
                coverage=_coverage(),
                overlaps=(),
                sync_admission=SyncAdmission(True, "lights_only_stage", "test", None),
                output_directory=tmp_path / directory,
                on_frame=progress,
            )
        artifact = tmp_path / directory / str(manifest["activation_identity"])
        return (artifact / "lighting-0001.rgb").read_bytes(), calls, frames

    reused, reused_calls, reused_frames = bake("reused")
    # 20 frames at 2 fps; the layer draws on 4 (7.0 to 8.5 s), the other 16 share one blank frame.
    assert reused_frames == 20
    assert reused_calls == 5
    with patch("cueverge.gui.stage_renderer.layer_source_time", return_value=0):
        every, every_calls, _ = bake("every-frame")
    assert every_calls == 20
    assert reused == every
    blank = reused[:6]
    assert blank != bytes(6)
    assert reused[14 * 6 : 15 * 6] != blank


def _drawn_square_units(composition: dict[str, object]) -> tuple[float, float]:
    from cueverge.gui.frame_compositor import (
        PreparedVisualLayer,
        to_working_rgba,
        working_to_srgb8,
    )
    from cueverge.gui.stage_renderer import stage_raster_layers, stage_raster_size

    source = to_working_rgba(
        np.full((100, 100, 4), 255, np.uint8),
        alpha_interpretation="straight",
        source_color_space="srgb",
    )
    keyframe = {
        "id": "key",
        "time_ms": 0,
        "position": [17.5, 10.5],
        "scale": [1, 1],
        "rotation_deg": 0,
        "anchor": [0.5, 0.5],
        "opacity": 1,
        "crop": [0, 0, 0, 0],
        "interpolation": {"kind": "hold"},
    }
    layer = PreparedVisualLayer(
        id="square",
        source=source,
        source_kind="image",
        start_ms=0,
        duration_ms=1000,
        in_point_ms=0,
        z_index=0,
        fade_in_ms=0,
        fade_out_ms=0,
        keyframes=(keyframe,),
    )
    width, height = stage_raster_size(composition)
    layers = stage_raster_layers((layer,), composition, width, height)
    frame = working_to_srgb8(
        compose_visual_frame(
            layers,
            timestamp_ms=0,
            output_width=width,
            output_height=height,
            background_rgb=(0, 0, 0),
        )[0]
    )
    rows, columns = np.nonzero(frame[..., 0] > 128)
    return (
        (columns.max() - columns.min() + 1) * 35 / width,
        (rows.max() - rows.min() + 1) * 21 / height,
    )


def test_stage_media_keeps_its_shape_and_size_at_any_render_size() -> None:
    from cueverge.gui.render_jobs import _stage_raster_identity

    def composition(
        render_width: int, render_height: int, units: float | None
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "space_kind": "stage",
            "width": 35,
            "height": 21,
            "render_settings": {"render_width": render_width, "render_height": render_height},
        }
        if units is not None:
            value["source_pixel_units"] = units
        return value

    # Older composition whose stored height did not match its shape: no longer stretched.
    legacy_width, legacy_height = _drawn_square_units(composition(1280, 720, None))
    assert legacy_width == pytest.approx(legacy_height, rel=0.02)
    assert _stage_raster_identity(composition(1280, 720, None)) == {"stage_raster": [1280, 768]}
    # A matching legacy composition renders and caches exactly as before.
    assert _stage_raster_identity(composition(1280, 768, None)) == {}
    matched = _drawn_square_units(composition(1280, 768, None))
    assert matched == pytest.approx((100 * 35 / 1280, 100 * 35 / 1280), rel=0.02)

    # Pinned media size: the render width only changes sampling detail, not size or shape.
    units = 35 / 1280
    for render_width in (640, 1280, 1920):
        drawn = _drawn_square_units(composition(render_width, 1, units))
        assert drawn == pytest.approx((100 * units, 100 * units), rel=0.03)


def test_source_pixel_units_are_validated_for_stage_compositions_only() -> None:
    document = _draft()
    compositions = cast(
        dict[str, dict[str, object]],
        cast(dict[str, object], document["editor_metadata"])["compositions"],
    )
    compositions["stage-composition"]["source_pixel_units"] = 35 / 1280
    DraftProject.model_validate(document)
    for invalid in (0, -1, True, "1"):
        compositions["stage-composition"]["source_pixel_units"] = invalid
        with pytest.raises(ValidationError, match="source_pixel_units"):
            DraftProject.model_validate(document)
    compositions["stage-composition"]["source_pixel_units"] = 1
    compositions["stage-composition"]["space_kind"] = "projector_native"
    with pytest.raises(ValidationError, match="only stage compositions"):
        DraftProject.model_validate(document)


@pytest.mark.parametrize("led_background", ["ambient", "background"])
def test_undrawn_leds_show_ambient_lighting_unless_background_is_chosen(
    tmp_path: Path, led_background: str
) -> None:
    image = tmp_path / "half.png"
    pixels = np.zeros((36, 128, 4), dtype=np.uint8)
    pixels[:, :64] = [255, 0, 0, 255]
    Image.fromarray(pixels, mode="RGBA").save(image)
    assets = cast(AssetUploadService, _FixtureAssets({"ghost": ("image/png", image)}))
    document = _draft()
    document["installation"] = {
        "schema_version": 1,
        "resources": [
            {"resource": name, "kind": "media_agent", "target": name}
            for name in ("projector.left", "projector.right")
        ]
        + [{"resource": "lights", "kind": "pixel_output", "target": "memory", "pixel_count": 10}],
    }
    metadata = cast(dict[str, object], document["editor_metadata"])
    composition = cast(dict[str, dict[str, object]], metadata["compositions"])["stage-composition"]
    composition["stage_projector_resources"] = []
    composition["stage_lighting_surfaces"] = ["stage-pixels", "right-pixels"]
    composition["led_background"] = led_background
    layer = _visual_layer()
    cast(list[dict[str, object]], layer["keyframes"])[0]["opacity"] = 1
    layers = cast(dict[str, list[dict[str, object]]], metadata["timeline_transform_layers"])
    layers["stage-composition"] = [layer]
    metadata["timeline_tracks"] = {
        "stage-composition": [
            {
                "id": "stage-track",
                "name": "Media",
                "kind": "group",
                "resource_binding": "shared-stage",
            }
        ]
    }
    metadata["ambient_lighting"] = {
        "schema_version": 1,
        "outputs": [
            {
                "resource": "lights",
                "cycle_ms": 1000,
                "cues": [
                    {
                        "id": "blue",
                        "primitive": "solid",
                        "start_ms": 0,
                        "duration_ms": 1000,
                        "color": "#0000FF",
                    }
                ],
            }
        ],
    }
    mapper = _mapper_export()
    surface = cast(list[dict[str, object]], mapper["lighting_surfaces"])[0]
    cast(list[dict[str, object]], surface["pixels"])[0].update(
        {"output_index": 8, "stage_x": 1, "stage_y": 2}
    )
    right = copy.deepcopy(surface)
    right["semantic_id"] = "right-pixels"
    cast(list[dict[str, object]], right["pixels"])[0].update({"output_index": 2, "stage_x": 7})
    mapper["lighting_surfaces"] = [surface, right]
    draft = DraftProject.model_validate(document)
    manifest = render_stage_composition(
        draft=draft,
        project_id="demo-project",
        source_revision=2,
        composition_id="stage-composition",
        assets=assets,
        mapper_export=mapper,
        coverage=_coverage(),
        overlaps=(),
        sync_admission=SyncAdmission(True, "lights_only_stage", "test", None),
        output_directory=tmp_path / "renders",
    )
    sequence = cast(list[dict[str, object]], manifest["lighting_sequences"])[0]
    assert sequence["channels"] == (4 if led_background == "ambient" else 3)
    artifact = tmp_path / "renders" / str(manifest["activation_identity"])
    compiled = DraftCompiler(tmp_path / "compiled").compile(
        project_id="demo-project",
        revision=2,
        draft=draft,
        lighting_artifacts={"stage-composition": artifact},
    )
    catalog = load_show_package(compiled.package_root)
    runtime = ControllerRuntime(catalog=catalog)
    rendered = runtime._render_lighting_output(
        catalog.require("ambient").lighting_outputs[0],
        render_position_ms=0,
        rendered_at=runtime.clock(),
        duration_ms=1000,
        scene_id="ambient",
    )
    assert rendered[8][0] > 0 and rendered[8][1:] == (0, 0)
    assert rendered[2] == ((0, 0, 255) if led_background == "ambient" else (0, 0, 0))
    # LEDs the composition never maps keep the ambient underneath.
    assert rendered[0] == (0, 0, 255)
