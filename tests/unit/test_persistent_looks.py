import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.core.models import (
    LightingCue,
    LightingDefinition,
    LightingLooksDefinition,
    LoadedLightingCue,
    LoadedLightingLooks,
)
from cueverge.lighting.sink import MemoryFrameSink
from cueverge.media_agent.fake import FakeMediaAgent


@dataclass
class ManualClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


def test_persistent_baseline_phase_survives_scene_replacement_and_stop_is_safe() -> None:
    async def scenario() -> None:
        clock = ManualClock()
        sink = MemoryFrameSink()
        catalog = _catalog_with_looks()
        runtime = ControllerRuntime(clock=clock, sink=sink, catalog=catalog)
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))

        first = await runtime.start_scene("synthetic-sweep")
        agent.attach(first.scene_instance_id, first.playback_session_id)
        clock.now += 1
        assert runtime.ingest(agent.position(1000)).accepted
        assert runtime.render_frame()
        first_frame = sink.frames[-1].pixels

        second = await runtime.start_scene("ambient")
        agent.attach(second.scene_instance_id, second.playback_session_id)
        clock.now += 1
        assert runtime.ingest(agent.position(0)).accepted
        assert runtime.render_frame()
        second_frame = sink.frames[-1].pixels

        assert first_frame[0] == second_frame[0] == (0, 255, 0)
        assert first_frame[1] != second_frame[1]
        assert second_frame[1][0] in {127, 128}
        assert second_frame[1][2] in {127, 128}

        await runtime.stop()
        assert sink.frames[-1].scene_instance_id == "safe-state"
        assert sink.frames[-1].pixels == ((0, 0, 0),) * 8
        await runtime.shutdown()

    asyncio.run(scenario())


def _catalog_with_looks():
    catalog = load_show_package(Path("shows/demo"))
    scene_cue = LightingCue(
        id="scene-override",
        primitive="solid",
        start_ms=0,
        duration_ms=4000,
        color="#00FF00",
    )
    lighting = LightingDefinition(
        resource="roofline.demo",
        timeline=(scene_cue,),
    )
    scenes = {}
    for scene_id in ("synthetic-sweep", "ambient"):
        scene = catalog.require(scene_id)
        definition = scene.definition.model_copy(update={"lighting": lighting})
        scenes[scene_id] = scene.model_copy(
            update={
                "definition": definition,
                "lighting_cues": (
                    LoadedLightingCue(
                        definition=scene_cue,
                        target_indices=(0,),
                    ),
                ),
            }
        )
    scenes.update(
        {scene_id: scene for scene_id, scene in catalog.scenes.items() if scene_id not in scenes}
    )
    baseline = LightingCue(
        id="baseline",
        primitive="color_transition",
        start_ms=0,
        duration_ms=4000,
        color="#FF0000",
        end_color="#0000FF",
    )
    safe = LightingCue(
        id="safe",
        primitive="blackout",
        start_ms=0,
        duration_ms=1,
    )
    looks = LoadedLightingLooks(
        definition=LightingLooksDefinition(
            schema_version=1,
            resource="roofline.demo",
            cycle_ms=4000,
            baseline=(baseline,),
            safe_state=(safe,),
        ),
        pixel_count=8,
        baseline_cues=(
            LoadedLightingCue(
                definition=baseline,
                target_indices=tuple(range(8)),
            ),
        ),
        safe_state_cues=(
            LoadedLightingCue(
                definition=safe,
                target_indices=tuple(range(8)),
            ),
        ),
    )
    return catalog.model_copy(update={"scenes": scenes, "lighting_looks": looks})
