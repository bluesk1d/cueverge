import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.core.models import (
    LightingCue,
    LightingLookDefinition,
    LoadedLightingCue,
    LoadedLightingLooks,
    MultiLightingLooksDefinition,
)
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.contracts import DraftProject
from cueverge.lighting.ambient import render_ambient, render_ambient_output
from cueverge.lighting.sequence import LightingSequenceReference, LoadedLightingSequence
from cueverge.lighting.sink import MemoryFrameSink
from cueverge.media_agent.fake import FakeMediaAgent


def _sequence(
    channels: int, frames: list[bytes], indices: tuple[int, ...]
) -> LoadedLightingSequence:
    data = b"".join(frames)
    reference = LightingSequenceReference(
        scene_id="ambient",
        resource="lights",
        path="ambient.rgba",
        digest_sha256=hashlib.sha256(data).hexdigest(),
        output_indices=indices,
        frame_rate=1,
        frame_count=len(frames),
        duration_ms=len(frames) * 1000,
        channels=channels,  # type: ignore[arg-type]
    )
    return LoadedLightingSequence(reference, data)


def test_coverage_sequences_leave_undrawn_leds_on_the_lighting_underneath() -> None:
    base = [(10, 20, 30)] * 4
    opaque = _sequence(3, [bytes([200, 0, 0, 0, 0, 0])], (1, 2))
    pixels = list(base)
    opaque.overlay(pixels, 0)
    assert pixels == [(10, 20, 30), (200, 0, 0), (0, 0, 0), (10, 20, 30)]

    # LED 1 fully drawn, LED 2 untouched, LED 3 half covered by white over black (128).
    covered = _sequence(4, [bytes([200, 0, 0, 255, 0, 0, 0, 0, 128, 128, 128, 128])], (1, 2, 3))
    pixels = list(base)
    covered.overlay(pixels, 0)
    assert pixels[0] == (10, 20, 30)
    assert pixels[1] == (200, 0, 0)
    assert pixels[2] == (10, 20, 30)
    assert pixels[3] == (133, 138, 143)
    pixels = list(base)
    covered.overlay(pixels, 5000)
    assert pixels == base


def _looks(
    *,
    cues: tuple[LightingCue, ...] = (),
    sequence: LoadedLightingSequence | None = None,
    excluded: tuple[str, ...] = (),
    pixel_count: int = 4,
    limit: float = 1.0,
) -> LoadedLightingLooks:
    safe = LightingCue(id="safe", primitive="solid", start_ms=0, duration_ms=1, color="#010203")
    definition = LightingLookDefinition(
        resource="lights",
        cycle_ms=2000,
        brightness_limit=limit,
        baseline=cues,
        baseline_sequence=sequence.reference if sequence is not None else None,
        safe_state=(safe,),
        excluded_scene_ids=excluded,
    )
    every = tuple(range(pixel_count))
    return LoadedLightingLooks(
        definition=definition,
        pixel_count=pixel_count,
        baseline_cues=tuple(
            LoadedLightingCue(definition=cue, target_indices=every) for cue in cues
        ),
        safe_state_cues=(LoadedLightingCue(definition=safe, target_indices=every),),
        baseline_sequence=sequence,
    )


def test_ambient_loops_cues_and_a_baked_sequence_on_the_controller_clock() -> None:
    blue = LightingCue(id="blue", primitive="solid", start_ms=0, duration_ms=1000, color="#0000FF")
    red = LightingCue(id="red", primitive="solid", start_ms=1000, duration_ms=1000, color="#FF0000")
    sequence = _sequence(
        4,
        [bytes([0, 255, 0, 255]), bytes([0, 0, 0, 0]), bytes([0, 128, 0, 128])],
        (3,),
    )
    looks = _looks(cues=(blue, red), sequence=sequence, excluded=("dark",), limit=0.5)
    assert render_ambient(looks, 500) == ((0, 0, 255),) * 3 + ((0, 255, 0),)
    # Cues wrap every 2 s and the 3 s sequence wraps on its own length.
    assert render_ambient(looks, 3500) == ((255, 0, 0),) * 3 + ((0, 255, 0),)
    assert render_ambient(looks, 6200)[3] == (0, 255, 0)
    assert render_ambient(looks, 2200 + 3000)[3][1] == 128
    assert render_ambient_output(looks, 500, "scene") == ((0, 0, 128),) * 3 + ((0, 128, 0),)
    assert render_ambient_output(looks, 500, "dark") == ((0, 1, 2),) * 4


@dataclass
class ManualClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        self.sent.append(data)


def test_runtime_runs_ambient_on_outputs_a_scene_does_not_light_unless_it_opts_out() -> None:
    async def scenario() -> None:
        base = load_show_package(Path("shows/demo"))
        cue = LightingCue(
            id="amber", primitive="solid", start_ms=0, duration_ms=2000, color="#FFAA00"
        )
        looks = _looks(cues=(cue,), excluded=("synthetic-sweep",), pixel_count=8)
        looks = looks.model_copy(
            update={"definition": looks.definition.model_copy(update={"resource": "roofline.demo"})}
        )
        scenes = {
            scene_id: scene.model_copy(update={"lighting_outputs": ()})
            for scene_id, scene in base.scenes.items()
        }
        catalog = base.model_copy(
            update={"scenes": scenes, "lighting_looks_by_resource": {"roofline.demo": looks}}
        )
        clock = ManualClock()
        sink = MemoryFrameSink()
        runtime = ControllerRuntime(clock=clock, sink=sink, catalog=catalog)
        agent = FakeMediaAgent()
        agent.accept_welcome(runtime.register_agent(agent.hello(), MemoryCommandChannel([])))

        ambient = await runtime.start_scene("ambient")
        agent.attach(ambient.scene_instance_id, ambient.playback_session_id)
        clock.now += 1
        assert runtime.ingest(agent.position(1000)).accepted
        assert runtime.render_frame()
        assert sink.frames[-1].resource == "roofline.demo"
        assert sink.frames[-1].pixels == ((255, 170, 0),) * 8

        opted_out = await runtime.start_scene("synthetic-sweep")
        agent.attach(opted_out.scene_instance_id, opted_out.playback_session_id)
        clock.now += 1
        assert runtime.ingest(agent.position(1000)).accepted
        assert runtime.render_frame()
        assert sink.frames[-1].pixels == ((1, 2, 3),) * 8
        await runtime.shutdown()

    asyncio.run(scenario())


def _draft(
    ambient: dict[str, object] | None, extra: dict[str, object] | None = None
) -> dict[str, Any]:
    metadata: dict[str, object] = {**(extra or {})}
    if ambient is not None:
        metadata["ambient_lighting"] = ambient
    return {
        "schema_version": 1,
        "installation": {
            "schema_version": 1,
            "resources": [
                {
                    "resource": "lights",
                    "kind": "pixel_output",
                    "target": "memory",
                    "pixel_count": 4,
                    "segments": [
                        {"id": "first", "start": 0, "end": 1},
                        {"id": "rest", "start": 1, "end": 4},
                    ],
                },
            ],
        },
        "editor_metadata": metadata,
        "scenes": [
            {
                "schema_version": 2,
                "id": scene_id,
                "name": scene_id.title(),
                "mode": "controller_timed",
                "clock_source": "controller",
                "resources": ["lights"],
                "duration_ms": 5000,
                "media": [],
                "lighting": [
                    {
                        "id": "lights",
                        "resource": "lights",
                        "timeline": [
                            {
                                "id": scene_id,
                                "primitive": "solid" if scene_id == "accent" else "blackout",
                                "start_ms": 0,
                                "duration_ms": 5000,
                                "color": "#FF0000",
                                "target": {"kind": "segment", "id": "first"},
                            }
                        ],
                    }
                ],
            }
            for scene_id in ("accent", "plain")
        ],
    }


def _ambient(**overrides: object) -> dict[str, object]:
    output: dict[str, object] = {
        "resource": "lights",
        "cycle_ms": 1000,
        "brightness_limit": 0.5,
        "cues": [
            {
                "id": "wash",
                "primitive": "solid",
                "start_ms": 0,
                "duration_ms": 1000,
                "color": "#0000FF",
            }
        ],
    }
    output.update(overrides)
    return {"outputs": [output], "excluded_scene_ids": ["plain"]}


def test_draft_ambient_lighting_is_validated() -> None:
    DraftProject.model_validate(_draft(_ambient()))
    cases: list[tuple[dict[str, object], str]] = [
        (_ambient(resource="missing"), "not an LED output"),
        ({**_ambient(), "excluded_scene_ids": ["nope"]}, "unknown scenes"),
        (_ambient(cues=[]), "needs cues or a composition"),
        (_ambient(composition_id="ghost"), "stage composition with LED outputs"),
        (
            _ambient(
                cues=[{"id": "late", "primitive": "solid", "start_ms": 900, "duration_ms": 500}]
            ),
            "extends past cycle_ms",
        ),
    ]
    for ambient, message in cases:
        with pytest.raises(ValidationError, match=message):
            DraftProject.model_validate(_draft(ambient))


def test_compiled_ambient_lighting_reaches_the_runtime_package(tmp_path: Path) -> None:
    draft = DraftProject.model_validate(_draft(_ambient()))
    compiled = DraftCompiler(tmp_path / "compiled").compile(
        project_id="project", revision=3, draft=draft
    )
    catalog = load_show_package(compiled.package_root)
    looks = catalog.lighting_looks_by_resource["lights"]
    assert looks.definition.excluded_scene_ids == ("plain",)
    assert looks.definition.brightness_limit == 0.5
    assert render_ambient(looks, 0) == ((0, 0, 255),) * 4
    runtime = ControllerRuntime(catalog=catalog)
    accent = catalog.require("accent").lighting_outputs[0]
    # Scene cues override only their pixels; the rest show the (limited) ambient.
    rendered = runtime._render_lighting_output(
        accent,
        render_position_ms=0,
        rendered_at=runtime.clock(),
        duration_ms=5000,
        scene_id="accent",
    )
    assert rendered[0] == (128, 0, 0)
    assert rendered[1:] == ((0, 0, 128),) * 3
    plain = catalog.require("plain").lighting_outputs[0]
    assert (
        runtime._render_lighting_output(
            plain,
            render_position_ms=0,
            rendered_at=runtime.clock(),
            duration_ms=5000,
            scene_id="plain",
        )
        == ((0, 0, 0),) * 4
    )


def test_compiled_ambient_uses_a_baked_composition_and_keeps_controller_safe_state(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "bake"
    artifact.mkdir()
    data = bytes([0, 200, 0, 255] * 4) + bytes([0, 0, 0, 0] * 4)
    (artifact / "lighting-0001.rgba").write_bytes(data)
    (artifact / "render-manifest.json").write_text(
        json.dumps(
            {
                "source_revision": 3,
                "composition_id": "loop",
                "duration_ms": 2000,
                "lighting_sequences": [
                    {
                        "resource": "lights",
                        "output_indices": [0, 1, 2, 3],
                        "frame_rate": 1,
                        "frame_rate_denominator": 1,
                        "frame_count": 2,
                        "duration_ms": 2000,
                        "digest_sha256": hashlib.sha256(data).hexdigest(),
                        "path": "lighting-0001.rgba",
                        "channels": 4,
                    }
                ],
            }
        )
    )
    composition = {
        "id": "loop",
        "name": "Ship lights loop",
        "duration_ms": 2000,
        "space_kind": "stage",
        "target_id": "shared-stage",
        "width": 10,
        "height": 6,
        "stage_lighting_surfaces": ["roof"],
        "stage_projector_resources": [],
    }
    draft = DraftProject.model_validate(
        _draft(
            {"outputs": [{"resource": "lights", "composition_id": "loop"}]},
            {"compositions": {"loop": composition}},
        )
    )
    retained_safe = LightingCue(
        id="keep", primitive="solid", start_ms=0, duration_ms=1, color="#202020"
    )
    controller = MultiLightingLooksDefinition(
        schema_version=2,
        outputs=(
            LightingLookDefinition(
                resource="lights",
                cycle_ms=1000,
                baseline=(LightingCue(id="old", primitive="solid", start_ms=0, duration_ms=1000),),
                safe_state=(retained_safe,),
            ),
        ),
    )
    compiled = DraftCompiler(tmp_path / "compiled").compile(
        project_id="project",
        revision=3,
        draft=draft,
        lighting_artifacts={"loop": artifact},
        lighting_looks=controller,
    )
    looks = load_show_package(compiled.package_root).lighting_looks_by_resource["lights"]
    assert looks.definition.baseline == ()
    assert looks.definition.safe_state == (retained_safe,)
    assert looks.definition.cycle_ms == 2000
    assert looks.baseline_sequence is not None
    assert render_ambient(looks, 500) == ((0, 200, 0),) * 4
    assert render_ambient(looks, 1500) == ((0, 0, 0),) * 4
    assert render_ambient(looks, 2500) == ((0, 200, 0),) * 4


def _looping_ambient_draft() -> DraftProject:
    """Accent repeats every 5 s; the ambient is blue for 2 s then green for 1 s (3 s loop)."""
    document = _draft(
        _ambient(
            cycle_ms=3000,
            brightness_limit=1.0,
            cues=[
                {
                    "id": "blue",
                    "primitive": "solid",
                    "start_ms": 0,
                    "duration_ms": 2000,
                    "color": "#0000FF",
                },
                {
                    "id": "green",
                    "primitive": "solid",
                    "start_ms": 2000,
                    "duration_ms": 1000,
                    "color": "#00FF00",
                },
            ],
        )
    )
    for scene in document["scenes"]:
        scene["completion"] = {"policy": "loop"}
    return DraftProject.model_validate(document)


def test_scene_loop_does_not_restart_the_ambient_on_the_controller(tmp_path: Path) -> None:
    async def scenario() -> None:
        compiled = DraftCompiler(tmp_path / "compiled").compile(
            project_id="project", revision=1, draft=_looping_ambient_draft()
        )
        clock = ManualClock()
        sink = MemoryFrameSink()
        runtime = ControllerRuntime(
            clock=clock, sink=sink, catalog=load_show_package(compiled.package_root)
        )
        first = await runtime.start_scene("accent")
        clock.now += 0.5
        assert runtime.render_frame()
        # Scene time 500 ms, ambient 500 ms: LED 0 is the scene's red, the rest ambient blue.
        assert sink.frames[-1].pixels[1:] == ((0, 0, 255),) * 3

        clock.now += 4.6
        runtime.render_frame()
        for _ in range(20):
            await asyncio.sleep(0)
            if (
                runtime.active_scene is not None
                and runtime.active_scene.scene_instance_id != first.scene_instance_id
            ):
                break
        looped = runtime.active_scene
        assert looped is not None and looped.definition.id == "accent"
        assert looped.scene_instance_id != first.scene_instance_id

        clock.now += 0.4
        assert runtime.render_frame()
        # The scene restarted (position ~400 ms) but the ambient is ~5.5 s in: green, not blue.
        assert sink.frames[-1].pixels[0] == (255, 0, 0)
        assert sink.frames[-1].pixels[1:] == ((0, 255, 0),) * 3
        await runtime.shutdown()

    asyncio.run(scenario())


def test_scene_preview_evaluates_the_ambient_at_its_own_elapsed_time() -> None:
    from cueverge.gui.scene_preview import evaluate_scene_lighting

    draft = _looping_ambient_draft()

    def preview(**extra: Any) -> dict[str, Any]:
        return evaluate_scene_lighting(
            project_id="project",
            source_revision=1,
            draft=draft,
            scene_id="accent",
            timestamp_ms=500,
            **extra,
        )

    restarted = preview()
    continuous = preview(ambient_elapsed_ms=5500)
    assert restarted["ambient_elapsed_ms"] == 500
    assert continuous["ambient_elapsed_ms"] == 5500
    assert restarted["outputs"][0]["colors"][1:] == [[0, 0, 255]] * 3
    assert continuous["outputs"][0]["colors"][1:] == [[0, 255, 0]] * 3
    # The scene's own pixel follows scene time either way.
    assert restarted["outputs"][0]["colors"][0] == continuous["outputs"][0]["colors"][0]
    assert any("continues across scene loops" in item for item in continuous["limitations"])
    assert any("ambient_elapsed_ms" in item for item in restarted["limitations"])


def _ambient_composition_draft(placed_in: str | None) -> DraftProject:
    composition = {
        "id": "loop",
        "name": "Ship lights loop",
        "duration_ms": 2000,
        "space_kind": "stage",
        "target_id": "shared-stage",
        "width": 10,
        "height": 6,
        "stage_lighting_surfaces": ["roof"],
        "stage_projector_resources": [],
    }
    extra: dict[str, object] = {"compositions": {"loop": composition}}
    if placed_in is not None:
        extra["scene_compositions"] = {placed_in: ["loop"]}
    return DraftProject.model_validate(
        _draft(
            {
                "outputs": [{"resource": "lights", "composition_id": "loop"}],
                "excluded_scene_ids": ["plain"],
            },
            extra,
        )
    )


def test_ambient_composition_placed_on_an_inheriting_scene_timeline_is_reported() -> None:
    from cueverge.gui.compiler import ambient_compositions_on_scene_timelines
    from cueverge.gui.show_plan import describe_show_plan

    assert ambient_compositions_on_scene_timelines(_ambient_composition_draft(None)) == {}
    # Excluded scenes may deliberately play the composition on scene time.
    assert ambient_compositions_on_scene_timelines(_ambient_composition_draft("plain")) == {}
    placed = _ambient_composition_draft("accent")
    assert ambient_compositions_on_scene_timelines(placed) == {"loop": ("accent",)}
    warnings = describe_show_plan(placed)["warnings"]
    assert isinstance(warnings, list)
    assert any("Ship lights loop" in item and "Accent" in item for item in warnings)
    assert not any(
        "Ship lights loop" in item
        for item in describe_show_plan(_ambient_composition_draft(None))["warnings"]  # type: ignore[union-attr]
    )
