from dataclasses import dataclass, field
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.director import ShowDirector
from cueverge.core.events import ExternalEvent
from cueverge.core.models import SceneCatalog
from cueverge.lighting.sink import MemoryFrameSink
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.observation.presence import AudiencePresenceEstimator
from cueverge.protocol.models import PlaybackState


@dataclass
class SimulationClock:
    now_ms: int = 0

    def monotonic(self) -> float:
        return self.now_ms / 1000

    def epoch_ms(self) -> int:
        return self.now_ms


@dataclass
class SimulationCommandChannel:
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send_json(self, data: Any) -> None:
        if not isinstance(data, dict):
            raise TypeError("simulation command must be a dictionary")
        self.sent.append(data)


class ShowSimulation:
    def __init__(self, catalog: SceneCatalog) -> None:
        self.clock = SimulationClock()
        self.sink = MemoryFrameSink()
        self.agent = FakeMediaAgent()
        self.channel = SimulationCommandChannel()
        audience = (
            AudiencePresenceEstimator(catalog.audience) if catalog.audience is not None else None
        )
        director = (
            ShowDirector(
                catalog.program,
                clock=self.clock.epoch_ms,
                context_provider=audience.context if audience is not None else None,
            )
            if catalog.program is not None
            else None
        )
        self.runtime = ControllerRuntime(
            catalog=catalog,
            sink=self.sink,
            clock=self.clock.monotonic,
            event_clock=self.clock.epoch_ms,
            heartbeat_timeout_ms=86_400_000,
            command_timeout_ms=86_400_000,
            director=director,
            audience=audience,
        )
        self.agent.accept_welcome(self.runtime.register_agent(self.agent.hello(), self.channel))
        self.scene_history: list[str] = []
        self._active_started_at_ms = 0
        self._attached_playback_session_id: str | None = None
        self._event_sequence = 0

    async def start(self) -> None:
        await self.runtime.start_show()
        self._sync_active()

    async def advance(self, delta_ms: int) -> None:
        if delta_ms < 0:
            raise ValueError("simulation cannot move backward")
        target_ms = self.clock.now_ms + delta_ms
        while self.runtime.active_scene is not None:
            self._sync_active()
            duration_ms = self.runtime.active_scene.definition.duration_ms
            completes_at_ms = self._active_started_at_ms + duration_ms
            if completes_at_ms > target_ms:
                break
            completing_session_id = self.runtime.active_scene.playback_session_id
            self.clock.now_ms = completes_at_ms
            await self.runtime.tick_encounter()
            self._sync_active()
            active = self.runtime.active_scene
            if active is None:
                break
            if active.playback_session_id != completing_session_id:
                continue
            telemetry = self.agent.position(
                duration_ms,
                state=PlaybackState.COMPLETED,
                duration_ms=duration_ms,
            )
            self.runtime.ingest(telemetry)
            await self.runtime.wait_for_lifecycle_idle()
            if self.runtime.active_scene is None:
                break
            self._sync_active()
        self.clock.now_ms = target_ms
        await self.runtime.tick_encounter()
        self._sync_active()

    async def observe(
        self,
        event_type: str,
        *,
        attributes: dict[str, str | int | float | bool],
    ) -> None:
        self._event_sequence += 1
        event_id = f"simulation-{self._event_sequence}"
        await self.runtime.ingest_event(
            ExternalEvent(
                event_id=event_id,
                event_type=event_type,
                occurred_at_ms=self.clock.now_ms,
                idempotency_key=event_id,
                attributes=attributes,
            )
        )
        self._sync_active()

    async def operator_start(self, scene_id: str) -> None:
        await self.runtime.start_scene(scene_id)
        self._sync_active()

    async def request_scene(self, scene_id: str) -> str:
        self.report_position()
        decision = await self.runtime.request_scene(scene_id)
        self._sync_active()
        return decision

    def report_position(self) -> None:
        active = self.runtime.active_scene
        if active is None:
            raise RuntimeError("simulation has no active scene")
        self.runtime.ingest(
            self.agent.position(
                self.clock.now_ms - self._active_started_at_ms,
                duration_ms=active.definition.duration_ms,
            )
        )

    def disconnect_agent(self) -> None:
        self.runtime.disconnect_agent(self.agent.agent_id, self.channel)

    def reconnect_agent(self) -> None:
        self.agent.reconnect()
        welcome = self.runtime.register_agent(
            self.agent.hello(self.agent.snapshot(0)),
            self.channel,
        )
        self.agent.accept_welcome(welcome)

    async def shutdown(self) -> None:
        await self.runtime.shutdown()

    def _sync_active(self) -> None:
        active = self.runtime.active_scene
        if active is None or active.playback_session_id == self._attached_playback_session_id:
            return
        self.agent.attach(active.scene_instance_id, active.playback_session_id)
        self._active_started_at_ms = self.clock.now_ms
        self._attached_playback_session_id = active.playback_session_id
        self.scene_history.append(active.definition.id)
