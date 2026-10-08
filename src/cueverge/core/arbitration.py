from dataclasses import dataclass, field
from enum import StrEnum

from cueverge.core.models import LoadedScene


class ArbitrationDecision(StrEnum):
    START = "start"
    QUEUE = "queue"
    IGNORE = "ignore"


@dataclass
class ResourceArbiter:
    queued: list[LoadedScene] = field(default_factory=list)

    def evaluate(
        self,
        active: LoadedScene | None,
        incoming: LoadedScene,
    ) -> ArbitrationDecision:
        if active is None:
            return ArbitrationDecision.START

        conflicts = set(active.definition.resources).intersection(incoming.definition.resources)
        if not conflicts:
            return ArbitrationDecision.START
        if incoming.definition.priority > active.definition.priority:
            return ArbitrationDecision.START
        if incoming.definition.interruption == "queue":
            if all(item.definition.id != incoming.definition.id for item in self.queued):
                self.queued.append(incoming)
            return ArbitrationDecision.QUEUE
        return ArbitrationDecision.IGNORE

    def pop_next(self) -> LoadedScene | None:
        if not self.queued:
            return None
        return self.queued.pop(0)
