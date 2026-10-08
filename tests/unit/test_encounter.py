import asyncio
from dataclasses import dataclass
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.arbitration import ArbitrationDecision, ResourceArbiter
from cueverge.core.events import ExternalEvent
from cueverge.media_agent.fake import FakeMediaAgent


@dataclass
class ManualEventClock:
    now_ms: int = 10_000

    def __call__(self) -> int:
        return self.now_ms


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


def test_encounter_progression_deduplication_and_operator_priority() -> None:
    async def scenario() -> None:
        event_clock = ManualEventClock()
        runtime = ControllerRuntime(event_clock=event_clock)
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))

        entered = _event("event-1", "person.entered", event_clock.now_ms)
        started = await runtime.ingest_event(entered)
        assert started.accepted
        assert started.state == "suspicious"
        assert started.scene_decision == "start"
        encounter_id = runtime.encounter.status()["encounter_id"] if runtime.encounter else None
        assert encounter_id is not None
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "encounter-suspicious"

        duplicate = await runtime.ingest_event(entered)
        assert not duplicate.accepted
        assert duplicate.reason == "duplicate"

        event_clock.now_ms += 5000
        interested = await runtime.ingest_event(
            _event("event-2", "person.present", event_clock.now_ms)
        )
        assert interested.accepted
        assert interested.state == "interested"
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "encounter-interested"

        await runtime.start_scene("operator-override")
        event_clock.now_ms += 600
        reaction = await runtime.ingest_event(
            _event("event-3", "porch.entered", event_clock.now_ms)
        )
        assert reaction.accepted
        assert reaction.scene_decision == "ignore"
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "operator-override"

        event_clock.now_ms += 600
        left = await runtime.ingest_event(_event("event-4", "person.left", event_clock.now_ms))
        assert left.accepted
        assert left.state == "cooldown"
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "operator-override"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_timeout_cooldown_and_ambient_fallback() -> None:
    async def scenario() -> None:
        event_clock = ManualEventClock()
        runtime = ControllerRuntime(event_clock=event_clock)
        agent = FakeMediaAgent()
        agent.accept_welcome(runtime.register_agent(agent.hello(), MemoryCommandChannel([])))

        await runtime.ingest_event(_event("event-1", "person.entered", event_clock.now_ms))
        event_clock.now_ms += 15_000
        await runtime.tick_encounter()
        assert runtime.encounter is not None
        assert runtime.encounter.state == "cooldown"
        assert runtime.active_scene is None

        event_clock.now_ms += 5_000
        await runtime.tick_encounter()
        assert runtime.encounter.state == "ambient"
        assert runtime.encounter.encounter_id is None
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "ambient"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_ttl_debounce_and_no_transition_are_rejected() -> None:
    async def scenario() -> None:
        event_clock = ManualEventClock()
        runtime = ControllerRuntime(event_clock=event_clock)
        agent = FakeMediaAgent()
        agent.accept_welcome(runtime.register_agent(agent.hello(), MemoryCommandChannel([])))

        expired = await runtime.ingest_event(
            _event("expired", "person.entered", event_clock.now_ms - 3001)
        )
        assert expired.reason == "event_expired"

        future = await runtime.ingest_event(
            _event("future", "person.entered", event_clock.now_ms + 1001)
        )
        assert future.reason == "event_in_future"

        assert (
            await runtime.ingest_event(_event("event-1", "person.entered", event_clock.now_ms))
        ).accepted
        event_clock.now_ms += 100
        debounced = await runtime.ingest_event(
            _event("event-2", "person.entered", event_clock.now_ms)
        )
        assert debounced.reason == "debounced"

        event_clock.now_ms += 500
        unavailable = await runtime.ingest_event(
            _event("event-3", "person.present", event_clock.now_ms)
        )
        assert unavailable.reason == "no_transition"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_resource_arbiter_queues_once_and_releases_in_order() -> None:
    runtime = ControllerRuntime()
    active = runtime.catalog.require("operator-override")
    original = runtime.catalog.require("ambient")
    queued_definition = original.definition.model_copy(
        update={"id": "queued", "priority": 5, "interruption": "queue"}
    )
    queued = original.model_copy(update={"definition": queued_definition})
    arbiter = ResourceArbiter()

    assert arbiter.evaluate(active, queued) is ArbitrationDecision.QUEUE
    assert arbiter.evaluate(active, queued) is ArbitrationDecision.QUEUE
    assert [scene.definition.id for scene in arbiter.queued] == ["queued"]
    assert arbiter.pop_next() == queued
    assert arbiter.pop_next() is None


def _event(event_id: str, event_type: str, occurred_at_ms: int) -> ExternalEvent:
    return ExternalEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at_ms=occurred_at_ms,
    )
