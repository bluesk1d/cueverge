import asyncio
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.director import ShowDirector
from cueverge.core.loader import load_show_package
from cueverge.core.models import (
    CompletionDefinition,
    ProgramScene,
    ScenePool,
    ShowProgramDefinition,
)
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.models import PlaybackState


@dataclass
class ManualDirectorClock:
    now_ms: int = 0

    def __call__(self) -> int:
        return self.now_ms


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


def test_simulated_thirty_minute_program_avoids_immediate_repetition() -> None:
    clock = ManualDirectorClock()
    director = ShowDirector(_program(), clock=clock)
    selected = [director.start()]

    for _ in range(29):
        clock.now_ms += 60_000
        next_scene = director.select_next(selected[-1])
        assert next_scene is not None
        selected.append(next_scene)

    assert len(selected) == 30
    assert all(left != right for left, right in pairwise(selected))
    assert set(selected) == {"ambient-a", "ambient-b", "ambient-c"}
    assert director.status()["selection_count"] == 30


def test_conditions_cooldowns_and_unavailable_scenes_filter_selection() -> None:
    clock = ManualDirectorClock(now_ms=10_000)
    context: dict[str, str | int | float | bool] = {"audience": False}
    unavailable = {"offline"}
    program = ShowProgramDefinition(
        schema_version=1,
        initial_scene_id="ambient",
        default_pool_id="ambient",
        recent_history_size=0,
        random_seed=1,
        pools=(
            ScenePool(
                id="ambient",
                category="ambient",
                scenes=(
                    ProgramScene(
                        scene_id="engaged",
                        conditions={"audience": True},
                    ),
                    ProgramScene(scene_id="offline"),
                    ProgramScene(scene_id="ambient", cooldown_ms=30_000),
                ),
            ),
        ),
    )
    director = ShowDirector(
        program,
        clock=clock,
        context_provider=lambda: context,
        scene_available=lambda scene_id: scene_id not in unavailable,
    )
    director.start()

    assert director.select_next("ambient") is None
    assert director.status()["no_eligible_scene_count"] == 1

    context["audience"] = True
    assert director.select_next("ambient") == "engaged"
    clock.now_ms += 30_000
    context["audience"] = False
    assert director.select_next("engaged") == "ambient"


def test_runtime_starts_program_and_uses_director_at_completion() -> None:
    async def scenario() -> None:
        catalog = load_show_package(Path("shows/demo"))
        first = catalog.require("synthetic-sweep")
        first_definition = first.definition.model_copy(
            update={"completion": CompletionDefinition(policy="select_next")}
        )
        scenes = dict(catalog.scenes)
        scenes[first_definition.id] = first.model_copy(update={"definition": first_definition})
        program = ShowProgramDefinition(
            schema_version=1,
            initial_scene_id="synthetic-sweep",
            default_pool_id="ambient",
            recent_history_size=1,
            pools=(
                ScenePool(
                    id="ambient",
                    category="ambient",
                    scenes=(ProgramScene(scene_id="ambient"),),
                ),
            ),
        )
        catalog = catalog.model_copy(update={"scenes": scenes, "program": program})
        runtime = ControllerRuntime(catalog=catalog, director=ShowDirector(program))
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))

        active = await runtime.start_show()
        assert active.definition.id == "synthetic-sweep"
        agent.attach(active.scene_instance_id, active.playback_session_id)
        assert runtime.ingest(agent.position(4000, state=PlaybackState.COMPLETED)).accepted
        await runtime.wait_for_lifecycle_idle()

        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "ambient"
        assert runtime.status()["director"] == {
            "active_pool_id": "ambient",
            "active_category": "ambient",
            "recent_history": ["ambient"],
            "selection_count": 2,
            "no_eligible_scene_count": 0,
            "eligible_scene_ids": ["ambient"],
            "eligibility": {
                "ambient": {
                    "eligible": True,
                    "available": True,
                    "conditions_match": True,
                    "cooldown_remaining_ms": 0,
                }
            },
        }
        await runtime.shutdown()

    asyncio.run(scenario())


def _program() -> ShowProgramDefinition:
    return ShowProgramDefinition(
        schema_version=1,
        initial_scene_id="ambient-a",
        default_pool_id="ambient",
        recent_history_size=1,
        random_seed=42,
        pools=(
            ScenePool(
                id="ambient",
                category="ambient",
                scenes=(
                    ProgramScene(scene_id="ambient-a", weight=1),
                    ProgramScene(scene_id="ambient-b", weight=2),
                    ProgramScene(scene_id="ambient-c", weight=1),
                ),
            ),
        ),
    )
