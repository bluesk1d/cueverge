import random
import time
from collections import deque
from collections.abc import Callable, Mapping

from cueverge.core.models import ProgramScene, ShowProgramDefinition

DirectorClock = Callable[[], int]
ContextProvider = Callable[[], Mapping[str, str | int | float | bool]]
SceneAvailability = Callable[[str], bool]


class ShowDirector:
    def __init__(
        self,
        definition: ShowProgramDefinition,
        *,
        clock: DirectorClock | None = None,
        context_provider: ContextProvider | None = None,
        scene_available: SceneAvailability | None = None,
    ) -> None:
        self.definition = definition
        self._clock = clock or (lambda: time.monotonic_ns() // 1_000_000)
        self._context_provider = context_provider or (lambda: {})
        self._scene_available = scene_available or (lambda _: True)
        self._random = random.Random(definition.random_seed)
        self._pools = {pool.id: pool for pool in definition.pools}
        self._active_pool_id = definition.default_pool_id
        self._history: deque[str] = deque(maxlen=definition.recent_history_size or None)
        self._selected_at: dict[str, int] = {}
        self.selection_count = 0
        self.no_eligible_scene_count = 0

    @property
    def active_pool_id(self) -> str:
        return self._active_pool_id

    def set_active_pool(self, pool_id: str) -> None:
        if pool_id not in self._pools:
            raise LookupError(f"unknown scene pool: {pool_id}")
        self._active_pool_id = pool_id

    def start(self) -> str:
        self._record_selection(self.definition.initial_scene_id)
        return self.definition.initial_scene_id

    def select_next(self, completed_scene_id: str) -> str | None:
        if not self._history or self._history[-1] != completed_scene_id:
            self._record_history(completed_scene_id)
        now_ms = self._clock()
        context = self._context_provider()
        entries = self._eligible_entries(now_ms, context, exclude_recent=True)
        if not entries:
            entries = self._eligible_entries(now_ms, context, exclude_recent=False)
        if not entries:
            self.no_eligible_scene_count += 1
            return None
        selected = self._weighted_choice(entries)
        self._record_selection(selected.scene_id, now_ms)
        return selected.scene_id

    def status(self) -> dict[str, object]:
        pool = self._pools[self._active_pool_id]
        return {
            "active_pool_id": self._active_pool_id,
            "active_category": pool.category,
            "recent_history": list(self._history),
            "selection_count": self.selection_count,
            "no_eligible_scene_count": self.no_eligible_scene_count,
        }

    def _eligible_entries(
        self,
        now_ms: int,
        context: Mapping[str, str | int | float | bool],
        *,
        exclude_recent: bool,
    ) -> list[ProgramScene]:
        recent = set(self._history) if exclude_recent else set()
        return [
            entry
            for entry in self._pools[self._active_pool_id].scenes
            if entry.scene_id not in recent
            and self._scene_available(entry.scene_id)
            and now_ms - self._selected_at.get(entry.scene_id, -entry.cooldown_ms)
            >= entry.cooldown_ms
            and all(context.get(key) == value for key, value in entry.conditions.items())
        ]

    def _weighted_choice(self, entries: list[ProgramScene]) -> ProgramScene:
        total = sum(entry.weight for entry in entries)
        value = self._random.randrange(total)
        for entry in entries:
            if value < entry.weight:
                return entry
            value -= entry.weight
        raise RuntimeError("weighted scene selection failed")

    def _record_selection(self, scene_id: str, now_ms: int | None = None) -> None:
        selected_at = self._clock() if now_ms is None else now_ms
        self._selected_at[scene_id] = selected_at
        self._record_history(scene_id)
        self.selection_count += 1

    def _record_history(self, scene_id: str) -> None:
        if self.definition.recent_history_size > 0:
            self._history.append(scene_id)
