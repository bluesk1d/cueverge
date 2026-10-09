import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cueverge.controller.runtime import ActiveScene, ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.core.models import CompletionDefinition, SceneCatalog
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.models import PlaybackState


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


@pytest.mark.parametrize(
    ("completion", "expected_scene"),
    [
        (CompletionDefinition(policy="loop"), "synthetic-sweep"),
        (
            CompletionDefinition(policy="transition_to", target_scene_id="ambient"),
            "ambient",
        ),
    ],
)
def test_completed_scene_loops_or_transitions_once(
    completion: CompletionDefinition,
    expected_scene: str,
) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=_catalog(completion=completion))
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        active = await runtime.start_scene("synthetic-sweep")
        agent.attach(active.scene_instance_id, active.playback_session_id)
        completed = agent.position(4000, state=PlaybackState.COMPLETED)

        assert runtime.ingest(completed).accepted
        assert runtime.ingest(agent.position(4000, state=PlaybackState.COMPLETED)).accepted
        await runtime.wait_for_lifecycle_idle()

        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == expected_scene
        assert runtime.active_scene.playback_session_id != active.playback_session_id
        assert runtime.scene_completions == 1
        assert [command["command"] for command in channel.sent] == ["prepare", "prepare"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_completion_can_hold_or_stop() -> None:
    async def scenario(policy: str) -> ControllerRuntime:
        completion = CompletionDefinition.model_validate({"policy": policy})
        runtime = ControllerRuntime(catalog=_catalog(completion=completion))
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        active = await runtime.start_scene("synthetic-sweep")
        agent.attach(active.scene_instance_id, active.playback_session_id)
        assert runtime.ingest(agent.position(4000, state=PlaybackState.COMPLETED)).accepted
        await runtime.wait_for_lifecycle_idle()
        return runtime

    async def test_both() -> None:
        held = await scenario("hold")
        assert held.active_scene is not None
        assert held.active_scene.definition.id == "synthetic-sweep"
        await held.shutdown()

        stopped = await scenario("stop")
        assert stopped.active_scene is None
        await stopped.shutdown()

    asyncio.run(test_both())


def test_select_next_uses_injected_director_boundary() -> None:
    async def scenario() -> None:
        selections: list[str] = []

        async def select_next(completed: ActiveScene) -> str:
            selections.append(completed.definition.id)
            return "ambient"

        runtime = ControllerRuntime(
            catalog=_catalog(completion=CompletionDefinition(policy="select_next")),
            scene_selector=select_next,
        )
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        active = await runtime.start_scene("synthetic-sweep")
        agent.attach(active.scene_instance_id, active.playback_session_id)

        assert runtime.ingest(agent.position(4000, state=PlaybackState.COMPLETED)).accepted
        await runtime.wait_for_lifecycle_idle()

        assert selections == ["synthetic-sweep"]
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "ambient"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_prepare_failure_starts_declared_fallback() -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=_catalog(failure_scene_id="ambient"))
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        await runtime.start_scene("synthetic-sweep")
        prepare = channel.sent[-1]

        failed = agent.acknowledge(
            prepare["request_id"],
            status="error",
            error_code="player_error",
            detail="asset missing",
        )
        assert (await runtime.acknowledge_command(failed)).accepted

        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "ambient"
        assert runtime.scene_failovers == 1
        assert [command["command"] for command in channel.sent] == ["prepare", "prepare"]
        await runtime.shutdown()

    asyncio.run(scenario())


def _catalog(
    *,
    completion: CompletionDefinition | None = None,
    failure_scene_id: str | None = None,
) -> SceneCatalog:
    catalog = load_show_package(Path("shows/demo"))
    scene = catalog.require("synthetic-sweep")
    definition = scene.definition.model_copy(
        update={
            "completion": completion or CompletionDefinition(),
            "failure_scene_id": failure_scene_id,
        }
    )
    scenes = dict(catalog.scenes)
    scenes[definition.id] = scene.model_copy(update={"definition": definition})
    return catalog.model_copy(update={"scenes": scenes})
