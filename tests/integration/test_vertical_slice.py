import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime, ShowMode
from cueverge.core.events import ExternalEvent
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.models import Discontinuity, PlaybackState


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


@dataclass
class FakeEventIngress:
    started: bool = False
    stopped: bool = False

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def test_event_ingress_follows_application_lifespan() -> None:
    ingress = FakeEventIngress()

    with TestClient(create_app(ControllerRuntime(), ingress)):
        assert ingress.started
        assert not ingress.stopped

    assert ingress.stopped


def test_agent_timecode_rendering_and_disconnect() -> None:
    asyncio.run(_agent_timecode_scenario())


async def _agent_timecode_scenario() -> None:
    clock = ManualClock()
    runtime = ControllerRuntime(clock=clock)
    agent = FakeMediaAgent()
    commands = MemoryCommandChannel(sent=[])
    agent.accept_welcome(runtime.register_agent(agent.hello(), commands))

    started = await runtime.start_scene("synthetic-sweep")
    assert [command["command"] for command in commands.sent] == ["prepare"]
    assert commands.sent[0]["playback_session_id"] == started.playback_session_id
    agent.attach(started.scene_instance_id, started.playback_session_id)

    agent.reconnect()
    reconnected_commands = MemoryCommandChannel(sent=[])
    welcome = runtime.register_agent(
        agent.hello(agent.snapshot(0)),
        reconnected_commands,
    )
    assert welcome.reconciliation == "continue"
    assert runtime.heartbeat(agent.heartbeat(100)).accepted

    assert runtime.ingest(agent.position(1000)).accepted
    await asyncio.sleep(0.06)
    assert runtime.sink.frames[-1].position_ms == 1000
    assert runtime.sink.frames[-1].pixels[2] == (0, 255, 96)

    clock.now += 0.08
    status = runtime.status()
    assert status["estimated_position_ms"] == 1080
    assert not status["degraded"]

    paused = agent.position(1200, state=PlaybackState.PAUSED)
    assert runtime.ingest(paused).accepted
    clock.now += 0.2
    await asyncio.sleep(0.06)
    assert runtime.status()["estimated_position_ms"] == 1200

    seek = agent.position(3200, discontinuity=Discontinuity.SEEK)
    assert runtime.ingest(seek).accepted
    await asyncio.sleep(0.06)
    loop = agent.position(50, discontinuity=Discontinuity.LOOP)
    assert runtime.ingest(loop).accepted
    await asyncio.sleep(0.06)

    duplicate = runtime.ingest(loop)
    assert not duplicate.accepted
    assert duplicate.reason == "out_of_order"

    old_session = agent.position(70).model_copy(update={"playback_session_id": "old-session"})
    assert runtime.ingest(old_session).reason == "session_fence"
    runtime.disconnect_agent(agent.agent_id, reconnected_commands)

    disconnected = runtime.status()
    assert disconnected["degraded"]
    assert disconnected["degraded_reason"] == "agent_disconnected"
    assert not disconnected["render_loop_running"]
    assert disconnected["rendered_frames"] >= 4
    assert disconnected["rejected_telemetry"] == 2
    assert disconnected["reconciliations"] == 1
    await runtime.shutdown()


def test_start_requires_known_scene_and_connected_agent() -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime()
        with pytest.raises(LookupError):
            await runtime.start_scene("missing")
        with pytest.raises(RuntimeError, match="not connected"):
            await runtime.start_scene("synthetic-sweep")
        with pytest.raises(RuntimeError, match="does not define a program"):
            await runtime.start_show()
        await runtime.shutdown()

    asyncio.run(scenario())


def test_media_controls_dispatch_fenced_agent_commands() -> None:
    asyncio.run(_media_control_scenario())


async def _media_control_scenario() -> None:
    runtime = ControllerRuntime()
    agent = FakeMediaAgent()
    commands = MemoryCommandChannel(sent=[])
    agent.accept_welcome(runtime.register_agent(agent.hello(), commands))

    started = await runtime.start_scene("synthetic-sweep")
    prepare = commands.sent[-1]
    agent.attach(started.scene_instance_id, started.playback_session_id)
    await runtime.pause()
    await runtime.seek(2500)
    await runtime.resume()
    await runtime.stop()

    assert [command["command"] for command in commands.sent] == [
        "prepare",
        "pause",
        "seek",
        "resume",
        "stop",
    ]
    assert commands.sent[2]["target_position_ms"] == 2500
    assert all(
        command["agent_session_id"] == prepare["agent_session_id"] for command in commands.sent
    )
    assert runtime.active_scene is None
    assert not runtime.render_loop_running
    status = runtime.status()
    assert status["estimated_position_ms"] is None
    assert status["playback_state"] is None
    assert not status["degraded"]
    await runtime.shutdown()


def test_media_controls_require_active_scene() -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime()
        with pytest.raises(RuntimeError):
            await runtime.pause()
        with pytest.raises(RuntimeError):
            await runtime.resume()
        with pytest.raises(RuntimeError):
            await runtime.seek(0)
        await runtime.shutdown()

    asyncio.run(scenario())


def test_operations_control_mode_reset_and_blackout() -> None:
    asyncio.run(_operations_scenario())


async def _operations_scenario() -> None:
    runtime = ControllerRuntime()
    agent = FakeMediaAgent()
    agent.accept_welcome(runtime.register_agent(agent.hello(), MemoryCommandChannel(sent=[])))

    await runtime.set_show_mode(ShowMode.MAINTENANCE)
    assert runtime.status()["operations"] == {
        "show_enabled": False,
        "mode": "maintenance",
    }
    await runtime.start_scene("operator-override")
    reset = await runtime.reset_ambient()
    assert reset.definition.id == "ambient"
    await runtime.set_show_mode(ShowMode.BLACKOUT)
    status = runtime.status()
    assert status["active_scene"] is None
    assert status["health"]["media_agent"] == "connected"
    assert status["health"]["projector"] == "not_independently_detectable"
    await runtime.shutdown()


def test_generic_event_starts_declarative_encounter() -> None:
    asyncio.run(_event_scenario())


async def _event_scenario() -> None:
    event_clock = ManualClock(now=10.0)
    runtime = ControllerRuntime(event_clock=lambda: round(event_clock.now * 1000))
    agent = FakeMediaAgent()
    commands = MemoryCommandChannel(sent=[])
    agent.accept_welcome(runtime.register_agent(agent.hello(), commands))
    runtime.start_background_tasks()

    outcome = await runtime.ingest_event(
        ExternalEvent(
            event_id="event-1",
            event_type="person.entered",
            occurred_at_ms=10_000,
            attributes={"zone": "demo"},
        )
    )
    assert outcome.accepted
    assert outcome.reason is None
    assert outcome.state == "suspicious"
    assert outcome.scene_decision == "start"
    status = runtime.status()
    assert status["active_scene"] == "encounter-suspicious"
    assert status["encounter"]["state"] == "suspicious"
    assert status["encounter_loop_running"]
    await runtime.shutdown()
