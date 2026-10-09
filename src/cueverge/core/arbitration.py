from dataclasses import dataclass, field
from enum import StrEnum

from cueverge.core.models import LoadedScene


class ArbitrationDecision(StrEnum):
    START = "start"
    QUEUE = "queue"
    IGNORE = "ignore"


@dataclass(frozen=True)
class PendingSceneRequest:
    scene: LoadedScene
    policy: str
    requested_at_ms: int
    expires_at_ms: int


@dataclass
class ResourceArbiter:
    pending: list[PendingSceneRequest] = field(default_factory=list)

    @property
    def queued(self) -> list[LoadedScene]:
        return [request.scene for request in self.pending]

    def evaluate(
        self,
        active: LoadedScene | None,
        incoming: LoadedScene,
        *,
        now_ms: int = 0,
        position_ms: int | None = None,
    ) -> ArbitrationDecision:
        if active is None:
            return ArbitrationDecision.START

        conflicts = set(active.definition.resources).intersection(incoming.definition.resources)
        if not conflicts:
            return ArbitrationDecision.START
        policy = self._policy(incoming)
        if policy == "ignore":
            return ArbitrationDecision.IGNORE
        if policy == "after_completion":
            self._enqueue(incoming, policy, now_ms)
            return ArbitrationDecision.QUEUE
        if incoming.definition.priority <= active.definition.priority:
            return ArbitrationDecision.IGNORE
        if policy == "at_safe_point":
            if position_ms in active.definition.scheduling.safe_points_ms and not self._protected(
                active, position_ms
            ):
                return ArbitrationDecision.START
            self._enqueue(incoming, policy, now_ms)
            return ArbitrationDecision.QUEUE
        if self._protected(active, position_ms):
            deferred_policy = (
                "at_safe_point"
                if active.definition.scheduling.safe_points_ms
                else "after_completion"
            )
            self._enqueue(incoming, deferred_policy, now_ms)
            return ArbitrationDecision.QUEUE
        if policy in {"priority", "immediate"}:
            return ArbitrationDecision.START
        return ArbitrationDecision.IGNORE

    def pop_ready(
        self,
        active: LoadedScene,
        *,
        previous_position_ms: int,
        position_ms: int,
        now_ms: int,
        completed: bool = False,
    ) -> LoadedScene | None:
        self._expire(now_ms)
        eligible = [
            request
            for request in self.pending
            if completed
            or (
                request.policy == "at_safe_point"
                and any(
                    previous_position_ms < point <= position_ms
                    and not self._protected(active, point)
                    for point in active.definition.scheduling.safe_points_ms
                )
            )
        ]
        if not eligible:
            return None
        selected = max(
            eligible,
            key=lambda request: (
                request.scene.definition.priority,
                -request.requested_at_ms,
            ),
        )
        self.pending.remove(selected)
        return selected.scene

    def pop_next(self, *, now_ms: int | None = None) -> LoadedScene | None:
        if now_ms is not None:
            self._expire(now_ms)
        if not self.pending:
            return None
        return self.pending.pop(0).scene

    def _enqueue(self, scene: LoadedScene, policy: str, now_ms: int) -> None:
        if any(request.scene.definition.id == scene.definition.id for request in self.pending):
            return
        self.pending.append(
            PendingSceneRequest(
                scene=scene,
                policy=policy,
                requested_at_ms=now_ms,
                expires_at_ms=now_ms + scene.definition.scheduling.request_ttl_ms,
            )
        )

    def _expire(self, now_ms: int) -> None:
        self.pending = [request for request in self.pending if now_ms < request.expires_at_ms]

    @staticmethod
    def _protected(scene: LoadedScene, position_ms: int | None) -> bool:
        return position_ms is not None and any(
            interval.start_ms <= position_ms < interval.end_ms
            for interval in scene.definition.scheduling.protected_intervals
        )

    @staticmethod
    def _policy(scene: LoadedScene) -> str:
        policy = scene.definition.scheduling.request_policy
        if policy != "priority":
            return policy
        if scene.definition.interruption == "queue":
            return "after_completion"
        if scene.definition.interruption == "ignore":
            return "ignore"
        return "priority"
