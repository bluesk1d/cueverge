from dataclasses import dataclass
from typing import Protocol

from cueverge.core.events import ExternalEvent
from cueverge.core.models import OpportunityDefinition, OpportunityProgramDefinition


class ContextProvider(Protocol):
    def __call__(self) -> dict[str, str | int | float | bool]: ...


@dataclass(frozen=True)
class EngagementOpportunity:
    opportunity_id: str
    scene_id: str
    created_at_ms: int
    expires_at_ms: int
    priority: int
    source_event_id: str


@dataclass(frozen=True)
class OpportunityCandidate:
    definition: OpportunityDefinition
    event: ExternalEvent
    expires_at_ms: int


class OpportunityManager:
    def __init__(
        self,
        definition: OpportunityProgramDefinition,
        context_provider: ContextProvider,
    ) -> None:
        self.definition = definition
        self._context_provider = context_provider
        self._active: dict[str, EngagementOpportunity] = {}
        self._candidates: dict[tuple[str, str], OpportunityCandidate] = {}
        self._seen_events: dict[str, int] = {}
        self._acknowledged_at: dict[str, int] = {}
        self.created_count = 0
        self.consumed_count = 0
        self.expired_count = 0

    def ingest(self, event: ExternalEvent, now_ms: int) -> tuple[EngagementOpportunity, ...]:
        self.expire(now_ms)
        source_key = event.idempotency_key or event.event_id
        if source_key in self._seen_events:
            return ()
        max_event_ttl_ms = max(
            definition.event_ttl_ms for definition in self.definition.opportunities
        )
        self._seen_events[source_key] = now_ms + max_event_ttl_ms
        context = self._context_provider()
        created: list[EngagementOpportunity] = []
        for definition in self.definition.opportunities:
            if not self._trigger_matches(definition, event, now_ms):
                continue
            candidate = OpportunityCandidate(
                definition=definition,
                event=event,
                expires_at_ms=now_ms + definition.qualification_window_ms,
            )
            self._candidates[(definition.id, source_key)] = candidate
            opportunity = self._qualify(candidate, context, now_ms)
            if opportunity is not None:
                created.append(opportunity)
        return tuple(created)

    def tick(self, now_ms: int) -> tuple[EngagementOpportunity, ...]:
        self.expire(now_ms)
        context = self._context_provider()
        return tuple(
            opportunity
            for candidate in tuple(self._candidates.values())
            if (opportunity := self._qualify(candidate, context, now_ms)) is not None
        )

    def consume_next(self, now_ms: int) -> EngagementOpportunity | None:
        self.expire(now_ms)
        if not self._active:
            return None
        selected = max(
            self._active.values(),
            key=lambda opportunity: (
                opportunity.priority,
                -opportunity.created_at_ms,
            ),
        )
        del self._active[selected.opportunity_id]
        self._acknowledged_at[selected.opportunity_id] = now_ms
        self.consumed_count += 1
        return selected

    def expire(self, now_ms: int) -> None:
        expired = [
            opportunity_id
            for opportunity_id, opportunity in self._active.items()
            if now_ms >= opportunity.expires_at_ms
        ]
        for opportunity_id in expired:
            del self._active[opportunity_id]
        self.expired_count += len(expired)
        self._candidates = {
            key: candidate
            for key, candidate in self._candidates.items()
            if now_ms < candidate.expires_at_ms
        }
        self._seen_events = {
            key: expires_at_ms
            for key, expires_at_ms in self._seen_events.items()
            if now_ms < expires_at_ms
        }

    def status(self, now_ms: int) -> dict[str, object]:
        self.expire(now_ms)
        return {
            "pending": [
                {
                    "id": opportunity.opportunity_id,
                    "scene_id": opportunity.scene_id,
                    "created_at_ms": opportunity.created_at_ms,
                    "expires_at_ms": opportunity.expires_at_ms,
                    "priority": opportunity.priority,
                }
                for opportunity in sorted(
                    self._active.values(),
                    key=lambda item: (-item.priority, item.created_at_ms),
                )
            ],
            "qualifying_count": len(self._candidates),
            "created_count": self.created_count,
            "consumed_count": self.consumed_count,
            "expired_count": self.expired_count,
        }

    def _trigger_matches(
        self,
        definition: OpportunityDefinition,
        event: ExternalEvent,
        now_ms: int,
    ) -> bool:
        if event.event_type != definition.trigger_event:
            return False
        if event.occurred_at_ms > now_ms + 1000:
            return False
        if now_ms - event.occurred_at_ms > definition.event_ttl_ms:
            return False
        return all(
            event.attributes.get(name) == expected
            for name, expected in definition.trigger_attributes.items()
        )

    def _qualify(
        self,
        candidate: OpportunityCandidate,
        context: dict[str, str | int | float | bool],
        now_ms: int,
    ) -> EngagementOpportunity | None:
        definition = candidate.definition
        if definition.id in self._active:
            return None
        if not all(
            context.get(name) == expected for name, expected in definition.required_context.items()
        ):
            return None
        presence_ms = context.get("audience_present_ms", 0)
        if not isinstance(presence_ms, int | float) or presence_ms < definition.min_presence_ms:
            return None
        absence_ms = context.get("audience_previous_absence_ms", 0)
        if (
            not isinstance(absence_ms, int | float)
            or absence_ms < definition.min_absence_before_arrival_ms
        ):
            return None
        acknowledged_at = self._acknowledged_at.get(definition.id)
        if acknowledged_at is not None and (
            now_ms - acknowledged_at < definition.acknowledgement_cooldown_ms
        ):
            return None
        opportunity = EngagementOpportunity(
            opportunity_id=definition.id,
            scene_id=definition.scene_id,
            created_at_ms=now_ms,
            expires_at_ms=now_ms + definition.ttl_ms,
            priority=definition.priority,
            source_event_id=candidate.event.event_id,
        )
        self._active[definition.id] = opportunity
        self._candidates = {
            key: item
            for key, item in self._candidates.items()
            if item.definition.id != definition.id
        }
        self.created_count += 1
        return opportunity
