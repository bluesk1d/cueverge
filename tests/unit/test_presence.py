import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.events import ExternalEvent
from cueverge.core.loader import load_show_package
from cueverge.core.models import AudiencePresenceDefinition
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.observation.presence import AudiencePresenceEstimator, AudienceState


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


def test_overlapping_observations_use_confirmation_and_absence_hysteresis() -> None:
    estimator = AudiencePresenceEstimator(_definition())

    first = estimator.ingest(_event("1", "frigate.zone.entered", "person-1"), 0)
    assert first.relevant
    assert first.events == ()
    assert estimator.state is AudienceState.CANDIDATE

    assert [event.event_type for event in estimator.tick(1000)] == [
        "audience.arrived",
        "audience.present",
    ]
    assert estimator.state is AudienceState.PRESENT

    second = estimator.ingest(_event("2", "frigate.zone.entered", "person-2"), 1500)
    assert second.relevant
    assert second.events == ()
    assert estimator.status()["active_objects"] == 2

    estimator.ingest(_event("3", "frigate.object.end", "person-1"), 2000)
    estimator.ingest(_event("4", "frigate.object.end", "person-2"), 2500)
    assert estimator.tick(7000) == ()
    departed = estimator.tick(7500)
    assert [event.event_type for event in departed] == ["audience.departed"]
    assert estimator.state is AudienceState.ABSENT

    idle = estimator.tick(12_500)
    assert [event.event_type for event in idle] == ["audience.long_idle"]
    assert estimator.tick(20_000) == ()


def test_tracking_gap_and_irrelevant_zone_do_not_change_presence() -> None:
    estimator = AudiencePresenceEstimator(_definition(arrival_confirmation_ms=0))

    ignored = estimator.ingest(
        _event("1", "frigate.zone.entered", "street-person", zone="Street"),
        0,
    )
    assert not ignored.relevant
    assert estimator.state is AudienceState.ABSENT

    arrived = estimator.ingest(_event("2", "frigate.zone.entered", "person-1"), 100)
    assert [event.event_type for event in arrived.events] == [
        "audience.arrived",
        "audience.present",
    ]
    estimator.ingest(_event("3", "frigate.object.end", "person-1"), 1000)
    assert estimator.state is AudienceState.PRESENT

    returned = estimator.ingest(_event("4", "frigate.zone.entered", "person-1"), 4000)
    assert returned.events == ()
    assert estimator.state is AudienceState.PRESENT
    assert estimator.tick(10_000) == ()


def test_recent_observations_are_bounded_and_do_not_expose_object_ids() -> None:
    estimator = AudiencePresenceEstimator(_definition(arrival_confirmation_ms=0))

    for index in range(25):
        estimator.ingest(
            _event(
                str(index),
                "frigate.zone.entered",
                f"private-object-{index}",
                zone="Porch" if index % 2 == 0 else "Street",
            ),
            index,
        )

    observations = estimator.status()["recent_observations"]
    assert isinstance(observations, list)
    assert len(observations) == 20
    assert observations[0]["received_at_ms"] == 5
    assert {observation["relevant"] for observation in observations} == {True, False}
    assert all("object_id" not in observation for observation in observations)
    assert "private-object" not in repr(observations)


def test_runtime_can_drive_encounter_from_semantic_arrival() -> None:
    async def scenario() -> None:
        clock = ManualEventClock()
        catalog = load_show_package(Path("shows/demo"))
        assert catalog.encounter is not None
        transitions = list(catalog.encounter.transitions)
        transitions[0] = transitions[0].model_copy(
            update={
                "event_type": "audience.arrived",
                "attributes": {"audience_state": "present"},
            }
        )
        encounter = catalog.encounter.model_copy(update={"transitions": tuple(transitions)})
        audience_definition = AudiencePresenceDefinition(
            schema_version=1,
            cameras=("doorbell",),
            arrival_confirmation_ms=0,
        )
        catalog = catalog.model_copy(
            update={"encounter": encounter, "audience": audience_definition}
        )
        runtime = ControllerRuntime(catalog=catalog, event_clock=clock)
        agent = FakeMediaAgent()
        agent.accept_welcome(runtime.register_agent(agent.hello(), MemoryCommandChannel([])))

        outcome = await runtime.ingest_event(
            _event("live-1", "frigate.object.new", "person-1", zone=None)
        )

        assert outcome.accepted
        assert outcome.state == "suspicious"
        assert runtime.audience is not None
        assert runtime.audience.context()["audience"]
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "encounter-suspicious"
        await runtime.shutdown()

    asyncio.run(scenario())


def _definition(
    *,
    arrival_confirmation_ms: int = 1000,
) -> AudiencePresenceDefinition:
    return AudiencePresenceDefinition(
        schema_version=1,
        cameras=("doorbell", "garage_exterior"),
        zones=("Porch",),
        observation_freshness_ms=20_000,
        arrival_confirmation_ms=arrival_confirmation_ms,
        absence_grace_ms=5000,
        long_idle_ms=5000,
    )


def _event(
    event_id: str,
    event_type: str,
    object_id: str,
    *,
    zone: str | None = "Porch",
) -> ExternalEvent:
    attributes: dict[str, str | int | float | bool] = {
        "source": "frigate",
        "object_id": object_id,
        "label": "person",
        "camera": "doorbell",
    }
    if zone is not None:
        attributes["zone"] = zone
    return ExternalEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at_ms=0,
        idempotency_key=event_id,
        attributes=attributes,
    )
