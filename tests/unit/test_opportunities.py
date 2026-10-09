import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.events import ExternalEvent
from cueverge.core.loader import load_show_package
from cueverge.core.models import (
    CompletionDefinition,
    OpportunityDefinition,
    OpportunityProgramDefinition,
)
from cueverge.core.opportunities import OpportunityManager
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.models import PlaybackState


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


def test_opportunity_expires_consumes_once_and_respects_acknowledgement_cooldown() -> None:
    context: dict[str, str | int | float | bool] = {
        "audience": True,
        "audience_present_ms": 20_000,
        "audience_previous_absence_ms": 300_000,
    }
    manager = OpportunityManager(_definition(), lambda: context)
    event = _event("zone-1", 1000)

    created = manager.ingest(event, 1000)
    assert [opportunity.opportunity_id for opportunity in created] == ["porch-reaction"]
    assert manager.ingest(event, 1100) == ()

    consumed = manager.consume_next(2000)
    assert consumed is not None
    assert consumed.scene_id == "encounter-reaction"
    assert manager.consume_next(2000) is None

    assert manager.ingest(_event("zone-2", 30_000), 30_000) == ()
    assert manager.ingest(_event("zone-3", 62_000), 62_000)
    manager.expire(73_000)
    status = manager.status(73_000)
    assert status["pending"] == []
    assert status["created_count"] == 2
    assert status["consumed_count"] == 1
    assert status["expired_count"] == 1


def test_opportunity_requires_current_context_and_presence_duration() -> None:
    context: dict[str, str | int | float | bool] = {
        "audience": False,
        "audience_present_ms": 0,
        "audience_previous_absence_ms": 300_000,
    }
    manager = OpportunityManager(_definition(), lambda: context)

    assert manager.ingest(_event("zone-1", 1000), 1000) == ()
    context["audience"] = True
    context["audience_present_ms"] = 5000
    assert manager.ingest(_event("zone-2", 2000), 2000) == ()
    context["audience_present_ms"] = 10_000
    assert manager.ingest(_event("zone-3", 3000), 3000)


def test_sustained_presence_candidate_matures_on_tick_without_new_event() -> None:
    context: dict[str, str | int | float | bool] = {
        "audience": True,
        "audience_present_ms": 0,
        "audience_previous_absence_ms": 300_000,
    }
    manager = OpportunityManager(_definition(), lambda: context)

    assert manager.ingest(_event("zone-1", 1000), 1000) == ()
    assert manager.status(1000)["qualifying_count"] == 1
    context["audience_present_ms"] = 10_000
    created = manager.tick(11_000)

    assert [opportunity.opportunity_id for opportunity in created] == ["porch-reaction"]
    assert manager.status(11_000)["qualifying_count"] == 0


def test_runtime_prefers_valid_opportunity_at_completion_boundary() -> None:
    async def scenario() -> None:
        clock = ManualEventClock()
        catalog = load_show_package(Path("shows/demo"))
        active_scene = catalog.require("synthetic-sweep")
        active_definition = active_scene.definition.model_copy(
            update={"completion": CompletionDefinition(policy="select_next")}
        )
        scenes = dict(catalog.scenes)
        scenes[active_definition.id] = active_scene.model_copy(
            update={"definition": active_definition}
        )
        opportunities = OpportunityProgramDefinition(
            schema_version=1,
            opportunities=(
                OpportunityDefinition(
                    id="porch-reaction",
                    scene_id="encounter-reaction",
                    trigger_event="frigate.zone.entered",
                    trigger_attributes={"zone": "Porch", "label": "person"},
                    ttl_ms=30_000,
                ),
            ),
        )
        runtime = ControllerRuntime(
            catalog=catalog.model_copy(update={"scenes": scenes, "opportunities": opportunities}),
            event_clock=clock,
        )
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        active = await runtime.start_scene("synthetic-sweep")
        agent.attach(active.scene_instance_id, active.playback_session_id)

        outcome = await runtime.ingest_event(_event("zone-1", clock.now_ms))
        assert not outcome.accepted
        assert runtime.opportunities is not None
        assert len(runtime.opportunities.status(clock.now_ms)["pending"]) == 1

        assert runtime.ingest(agent.position(4000, state=PlaybackState.COMPLETED)).accepted
        await runtime.wait_for_lifecycle_idle()

        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "encounter-reaction"
        assert runtime.opportunities.status(clock.now_ms)["consumed_count"] == 1
        await runtime.shutdown()

    asyncio.run(scenario())


def _definition() -> OpportunityProgramDefinition:
    return OpportunityProgramDefinition(
        schema_version=1,
        opportunities=(
            OpportunityDefinition(
                id="porch-reaction",
                scene_id="encounter-reaction",
                trigger_event="frigate.zone.entered",
                trigger_attributes={"zone": "Porch", "label": "person"},
                required_context={"audience": True},
                ttl_ms=10_000,
                min_presence_ms=10_000,
                min_absence_before_arrival_ms=120_000,
                acknowledgement_cooldown_ms=60_000,
                priority=60,
            ),
        ),
    )


def _event(event_id: str, occurred_at_ms: int) -> ExternalEvent:
    return ExternalEvent(
        event_id=event_id,
        event_type="frigate.zone.entered",
        occurred_at_ms=occurred_at_ms,
        idempotency_key=event_id,
        attributes={
            "source": "frigate",
            "object_id": "person-1",
            "label": "person",
            "camera": "doorbell",
            "zone": "Porch",
        },
    )
