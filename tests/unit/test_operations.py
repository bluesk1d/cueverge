import asyncio
from typing import Any

from cueverge.controller.runtime import ControllerRuntime, ShowMode
from cueverge.core.events import ExternalEvent
from cueverge.lighting.sink import MemoryFrameSink
from cueverge.media_agent.fake import FakeMediaAgent


class MemoryCommandChannel:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


def test_operations_modes_gate_observations_but_preserve_operator_control() -> None:
    async def scenario() -> None:
        sink = MemoryFrameSink()
        runtime = ControllerRuntime(sink=sink, event_clock=lambda: 0)
        agent = FakeMediaAgent()
        agent.accept_welcome(runtime.register_agent(agent.hello(), MemoryCommandChannel()))

        automatic = await runtime.ingest_event(_event("automatic"))
        assert automatic.accepted

        for mode in (ShowMode.DISABLED, ShowMode.MAINTENANCE, ShowMode.BLACKOUT):
            await runtime.set_show_mode(mode)
            rejected = await runtime.ingest_event(_event(mode.value))
            assert not rejected.accepted
            assert rejected.reason == "show_not_automatic"

        assert sink.frames[-1].scene_instance_id == "blackout"
        assert set(sink.frames[-1].pixels) == {(0, 0, 0)}

        operator_scene = await runtime.start_scene("operator-override")
        assert operator_scene.definition.id == "operator-override"
        await runtime.reset_ambient()
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "ambient"

        status = runtime.status()
        assert status["operations"] == {
            "show_enabled": False,
            "mode": "blackout",
        }
        assert status["health"] == {
            "media_agent": "connected",
            "lighting_output": "MemoryFrameSink",
            "projector": "not_independently_detectable",
            "audio": "not_independently_detectable",
        }
        await runtime.shutdown()

    asyncio.run(scenario())


def _event(event_id: str) -> ExternalEvent:
    return ExternalEvent(
        event_id=event_id,
        event_type="person.entered",
        occurred_at_ms=0,
        idempotency_key=event_id,
        attributes={"zone": "demo"},
    )
