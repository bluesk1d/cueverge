from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from cueverge.core.events import ExternalEvent
from cueverge.core.models import AudiencePresenceDefinition


class AudienceState(StrEnum):
    ABSENT = "absent"
    CANDIDATE = "candidate"
    PRESENT = "present"


@dataclass(frozen=True)
class PresenceUpdate:
    relevant: bool
    events: tuple[ExternalEvent, ...]


class AudiencePresenceEstimator:
    def __init__(self, definition: AudiencePresenceDefinition) -> None:
        self.definition = definition
        self.state = AudienceState.ABSENT
        self._objects: dict[str, int] = {}
        self._qualified_objects: set[str] = set()
        self._seen_events: dict[str, int] = {}
        self._candidate_since_ms: int | None = None
        self._absence_since_ms: int | None = None
        self._present_since_ms: int | None = None
        self._departed_at_ms: int | None = None
        self._previous_absence_ms = 0
        self._now_ms = 0
        self._long_idle_emitted = False
        self._recent_observations: deque[dict[str, object]] = deque(maxlen=20)
        self.semantic_events_emitted = 0
        self.relevant_observations = 0
        self.ignored_observations = 0

    def ingest(self, event: ExternalEvent, now_ms: int) -> PresenceUpdate:
        self._now_ms = now_ms
        self._expire(now_ms)
        dedupe_key = event.idempotency_key or event.event_id
        if dedupe_key in self._seen_events:
            self.ignored_observations += 1
            return PresenceUpdate(False, self._evaluate(now_ms))
        self._seen_events[dedupe_key] = now_ms + self.definition.observation_freshness_ms

        relevant = self._apply_observation(event, now_ms)
        self._recent_observations.append(
            {
                "event_type": event.event_type,
                "camera": event.attributes.get("camera"),
                "zone": event.attributes.get("zone"),
                "occurred_at_ms": event.occurred_at_ms,
                "received_at_ms": now_ms,
                "relevant": relevant,
            }
        )
        if relevant:
            self.relevant_observations += 1
        else:
            self.ignored_observations += 1
        return PresenceUpdate(relevant, self._evaluate(now_ms))

    def tick(self, now_ms: int) -> tuple[ExternalEvent, ...]:
        self._now_ms = now_ms
        self._expire(now_ms)
        return self._evaluate(now_ms)

    def context(self) -> dict[str, str | int | float | bool]:
        return {
            "audience": self.state is AudienceState.PRESENT,
            "audience_state": self.state.value,
            "audience_active_objects": len(self._objects),
            "audience_present_ms": (
                max(0, self._now_ms - self._present_since_ms)
                if self._present_since_ms is not None
                else 0
            ),
            "audience_previous_absence_ms": self._previous_absence_ms,
        }

    def status(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "active_objects": len(self._objects),
            "candidate_since_ms": self._candidate_since_ms,
            "present_since_ms": self._present_since_ms,
            "absence_since_ms": self._absence_since_ms,
            "departed_at_ms": self._departed_at_ms,
            "semantic_events_emitted": self.semantic_events_emitted,
            "relevant_observations": self.relevant_observations,
            "ignored_observations": self.ignored_observations,
            "recent_observations": list(self._recent_observations),
        }

    def _apply_observation(self, event: ExternalEvent, now_ms: int) -> bool:
        attributes = event.attributes
        object_id = attributes.get("object_id")
        label = attributes.get("label")
        camera = attributes.get("camera")
        if not isinstance(object_id, str) or label not in self.definition.labels:
            return False

        if event.event_type == "frigate.object.end":
            tracked = object_id in self._objects or object_id in self._qualified_objects
            self._objects.pop(object_id, None)
            self._qualified_objects.discard(object_id)
            return tracked

        camera_matches = not self.definition.cameras or camera in self.definition.cameras
        if not camera_matches:
            return False

        if event.event_type == "frigate.zone.entered":
            zone = attributes.get("zone")
            if self.definition.zones and zone not in self.definition.zones:
                return False
            self._qualified_objects.add(object_id)
        elif self.definition.zones and object_id not in self._qualified_objects:
            return False

        if event.event_type not in {
            "frigate.object.new",
            "frigate.object.updated",
            "frigate.zone.entered",
        }:
            return False
        self._objects[object_id] = now_ms
        return True

    def _expire(self, now_ms: int) -> None:
        expired_objects = [
            object_id
            for object_id, last_seen_ms in self._objects.items()
            if now_ms - last_seen_ms > self.definition.observation_freshness_ms
        ]
        for object_id in expired_objects:
            del self._objects[object_id]
            self._qualified_objects.discard(object_id)
        expired_events = [
            key for key, expires_at_ms in self._seen_events.items() if now_ms >= expires_at_ms
        ]
        for key in expired_events:
            del self._seen_events[key]

    def _evaluate(self, now_ms: int) -> tuple[ExternalEvent, ...]:
        events: list[ExternalEvent] = []
        if self._objects:
            self._absence_since_ms = None
            self._departed_at_ms = None
            self._long_idle_emitted = False
            if self.state is AudienceState.ABSENT:
                self._previous_absence_ms = (
                    max(0, now_ms - self._departed_at_ms) if self._departed_at_ms is not None else 0
                )
                self.state = AudienceState.CANDIDATE
                self._candidate_since_ms = now_ms
            if (
                self.state is AudienceState.CANDIDATE
                and self._candidate_since_ms is not None
                and now_ms - self._candidate_since_ms >= self.definition.arrival_confirmation_ms
            ):
                self.state = AudienceState.PRESENT
                self._present_since_ms = now_ms
                events.extend(
                    (
                        self._event("audience.arrived", now_ms),
                        self._event("audience.present", now_ms),
                    )
                )
        else:
            self._candidate_since_ms = None
            if self.state is AudienceState.CANDIDATE:
                self.state = AudienceState.ABSENT
            elif self.state is AudienceState.PRESENT:
                if self._absence_since_ms is None:
                    self._absence_since_ms = now_ms
                if now_ms - self._absence_since_ms >= self.definition.absence_grace_ms:
                    self.state = AudienceState.ABSENT
                    self._present_since_ms = None
                    self._departed_at_ms = now_ms
                    events.append(self._event("audience.departed", now_ms))
            elif (
                self._departed_at_ms is not None
                and not self._long_idle_emitted
                and now_ms - self._departed_at_ms >= self.definition.long_idle_ms
            ):
                self._long_idle_emitted = True
                events.append(self._event("audience.long_idle", now_ms))
        self.semantic_events_emitted += len(events)
        return tuple(events)

    def _event(self, event_type: str, occurred_at_ms: int) -> ExternalEvent:
        event_id = f"{event_type}:{occurred_at_ms}"
        return ExternalEvent(
            event_id=event_id,
            event_type=event_type,
            occurred_at_ms=occurred_at_ms,
            idempotency_key=event_id,
            attributes=self.context(),
        )
