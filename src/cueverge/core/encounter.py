from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from cueverge.core.events import ExternalEvent
from cueverge.core.models import EncounterDefinition, EncounterTransition

SceneRequester = Callable[[str], Awaitable[str]]
SceneStopper = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class EventOutcome:
    accepted: bool
    reason: str | None
    state: str
    scene_decision: str | None = None


class EncounterEngine:
    def __init__(
        self,
        definition: EncounterDefinition,
        request_scene: SceneRequester,
        stop_scene: SceneStopper,
    ) -> None:
        self.definition = definition
        self.request_scene = request_scene
        self.stop_scene = stop_scene
        self.state = definition.initial_state
        self.encounter_id: str | None = None
        self.state_entered_at_ms = 0
        self.last_event_at_ms: int | None = None
        self.cooldown_until_ms: int | None = None
        self._seen: dict[str, int] = {}
        self._last_type_at: dict[str, int] = {}
        self.accepted_events = 0
        self.rejected_events = 0

    async def ingest(self, event: ExternalEvent, now_ms: int) -> EventOutcome:
        self._expire_seen(now_ms)
        if event.occurred_at_ms > now_ms + 1000:
            return self._reject("event_in_future")
        if now_ms - event.occurred_at_ms > self.definition.event_ttl_ms:
            return self._reject("event_expired")

        key = event.idempotency_key or event.event_id
        if key in self._seen:
            return self._reject("duplicate")
        self._seen[key] = now_ms + self.definition.event_ttl_ms

        last_type_at = self._last_type_at.get(event.event_type)
        if last_type_at is not None and now_ms - last_type_at < self.definition.debounce_ms:
            return self._reject("debounced")
        self._last_type_at[event.event_type] = now_ms

        transition = self._find_transition(event, now_ms)
        if transition is None:
            return self._reject("no_transition")

        previous_state = self.state
        self.state = transition.to_state
        self.state_entered_at_ms = now_ms
        self.last_event_at_ms = now_ms
        if previous_state == self.definition.initial_state and self.state != previous_state:
            self.encounter_id = str(uuid4())
        if self.state == self.definition.cooldown_state:
            self.cooldown_until_ms = now_ms + self.definition.cooldown_ms
            await self.stop_scene()

        scene_decision = (
            await self.request_scene(transition.scene_id) if transition.scene_id else None
        )
        self.accepted_events += 1
        return EventOutcome(True, None, self.state, scene_decision)

    async def tick(self, now_ms: int) -> None:
        self._expire_seen(now_ms)
        if (
            self.state == self.definition.cooldown_state
            and self.cooldown_until_ms is not None
            and now_ms >= self.cooldown_until_ms
        ):
            self.state = self.definition.initial_state
            self.state_entered_at_ms = now_ms
            self.encounter_id = None
            self.cooldown_until_ms = None
            await self.request_scene(self.definition.ambient_scene_id)
            return
        if (
            self.state not in {self.definition.initial_state, self.definition.cooldown_state}
            and self.last_event_at_ms is not None
            and now_ms - self.last_event_at_ms >= self.definition.encounter_timeout_ms
        ):
            self.state = self.definition.cooldown_state
            self.state_entered_at_ms = now_ms
            self.cooldown_until_ms = now_ms + self.definition.cooldown_ms
            await self.stop_scene()

    def status(self) -> dict[str, object]:
        return {
            "state": self.state,
            "encounter_id": self.encounter_id,
            "state_entered_at_ms": self.state_entered_at_ms,
            "last_event_at_ms": self.last_event_at_ms,
            "cooldown_until_ms": self.cooldown_until_ms,
            "accepted_events": self.accepted_events,
            "rejected_events": self.rejected_events,
        }

    def _find_transition(
        self,
        event: ExternalEvent,
        now_ms: int,
    ) -> EncounterTransition | None:
        state_age = max(0, now_ms - self.state_entered_at_ms)
        return next(
            (
                transition
                for transition in self.definition.transitions
                if transition.event_type == event.event_type
                and self.state in transition.from_states
                and state_age >= transition.min_state_age_ms
                and all(
                    event.attributes.get(name) == expected
                    for name, expected in transition.attributes.items()
                )
            ),
            None,
        )

    def _reject(self, reason: str) -> EventOutcome:
        self.rejected_events += 1
        return EventOutcome(False, reason, self.state)

    def _expire_seen(self, now_ms: int) -> None:
        expired = [key for key, expires_at in self._seen.items() if expires_at <= now_ms]
        for key in expired:
            del self._seen[key]
