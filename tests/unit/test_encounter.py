import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.arbitration import ArbitrationDecision, ResourceArbiter
from cueverge.core.events import ExternalEvent
from cueverge.core.loader import load_show_package
from cueverge.core.models import SchedulingDefinition, TimeInterval
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


def test_transition_attribute_guards_filter_normalized_events() -> None:
    async def scenario() -> None:
        event_clock = ManualEventClock()
        catalog = load_show_package(Path("shows/demo"))
        assert catalog.encounter is not None
        transitions = list(catalog.encounter.transitions)
        transitions[0] = transitions[0].model_copy(
            update={
                "event_type": "frigate.object.new",
                "attributes": {"label": "person", "camera": "porch_east"},
            }
        )
        encounter = catalog.encounter.model_copy(update={"transitions": tuple(transitions)})
        runtime = ControllerRuntime(
            event_clock=event_clock,
            catalog=catalog.model_copy(update={"encounter": encounter}),
        )
        agent = FakeMediaAgent()
        agent.accept_welcome(runtime.register_agent(agent.hello(), MemoryCommandChannel([])))

        wrong_camera = await runtime.ingest_event(
            _event(
                "event-1",
                "frigate.object.new",
                event_clock.now_ms,
                attributes={"label": "person", "camera": "driveway"},
            )
        )
        assert wrong_camera.reason == "no_transition"

        event_clock.now_ms += encounter.debounce_ms
        porch = await runtime.ingest_event(
            _event(
                "event-2",
                "frigate.object.new",
                event_clock.now_ms,
                attributes={"label": "person", "camera": "porch_east"},
            )
        )
        assert porch.accepted
        assert porch.state == "suspicious"
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


def test_resource_arbiter_releases_coalesced_request_at_safe_point() -> None:
    runtime = ControllerRuntime()
    source = runtime.catalog.require("ambient")
    active = source.model_copy(
        update={
            "definition": source.definition.model_copy(
                update={
                    "priority": 10,
                    "scheduling": SchedulingDefinition(
                        safe_points_ms=(1000, 2000),
                    ),
                }
            )
        }
    )
    incoming = source.model_copy(
        update={
            "definition": source.definition.model_copy(
                update={
                    "id": "reaction",
                    "priority": 50,
                    "scheduling": SchedulingDefinition(
                        request_policy="at_safe_point",
                        request_ttl_ms=5000,
                    ),
                }
            )
        }
    )
    arbiter = ResourceArbiter()

    assert (
        arbiter.evaluate(active, incoming, now_ms=100, position_ms=100) is ArbitrationDecision.QUEUE
    )
    assert (
        arbiter.evaluate(active, incoming, now_ms=200, position_ms=200) is ArbitrationDecision.QUEUE
    )
    assert len(arbiter.queued) == 1
    assert (
        arbiter.pop_ready(
            active,
            previous_position_ms=500,
            position_ms=900,
            now_ms=900,
        )
        is None
    )
    assert (
        arbiter.pop_ready(
            active,
            previous_position_ms=900,
            position_ms=1100,
            now_ms=1100,
        )
        == incoming
    )


def test_protected_interval_defers_immediate_request_and_expiration_discards_it() -> None:
    runtime = ControllerRuntime()
    source = runtime.catalog.require("ambient")
    active = source.model_copy(
        update={
            "definition": source.definition.model_copy(
                update={
                    "priority": 10,
                    "scheduling": SchedulingDefinition(
                        safe_points_ms=(2000,),
                        protected_intervals=(TimeInterval(start_ms=0, end_ms=2000),),
                    ),
                }
            )
        }
    )
    incoming = source.model_copy(
        update={
            "definition": source.definition.model_copy(
                update={
                    "id": "arrival",
                    "priority": 50,
                    "scheduling": SchedulingDefinition(
                        request_policy="immediate",
                        request_ttl_ms=500,
                    ),
                }
            )
        }
    )
    arbiter = ResourceArbiter()

    assert (
        arbiter.evaluate(active, incoming, now_ms=100, position_ms=500) is ArbitrationDecision.QUEUE
    )
    assert (
        arbiter.pop_ready(
            active,
            previous_position_ms=1900,
            position_ms=2100,
            now_ms=601,
        )
        is None
    )
    assert arbiter.queued == []


def test_after_completion_request_runs_even_at_lower_priority() -> None:
    runtime = ControllerRuntime()
    active = runtime.catalog.require("operator-override")
    source = runtime.catalog.require("ambient")
    incoming = source.model_copy(
        update={
            "definition": source.definition.model_copy(
                update={
                    "id": "next-ambient",
                    "scheduling": SchedulingDefinition(
                        request_policy="after_completion",
                    ),
                }
            )
        }
    )
    arbiter = ResourceArbiter()

    assert arbiter.evaluate(active, incoming, now_ms=0) is ArbitrationDecision.QUEUE
    assert (
        arbiter.pop_ready(
            active,
            previous_position_ms=0,
            position_ms=active.definition.duration_ms,
            now_ms=1000,
            completed=True,
        )
        == incoming
    )


def _event(
    event_id: str,
    event_type: str,
    occurred_at_ms: int,
    *,
    attributes: dict[str, str | int | float | bool] | None = None,
) -> ExternalEvent:
    return ExternalEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at_ms=occurred_at_ms,
        attributes=attributes or {},
    )
