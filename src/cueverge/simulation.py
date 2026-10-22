from dataclasses import dataclass, field
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.director import ShowDirector
from cueverge.core.events import ExternalEvent
from cueverge.core.models import SceneCatalog
from cueverge.lighting.sink import FrameSink, FrameSinkRouter, MemoryFrameSink, RenderedFrame
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
        lighting_resources = sorted(
            {
                output.definition.resource
                for scene in catalog.scenes.values()
                for output in scene.lighting_outputs
            }
        )
        self.sinks = {resource: MemoryFrameSink() for resource in lighting_resources}
        if len(self.sinks) == 1:
            self.sink = next(iter(self.sinks.values()))
            runtime_sink: FrameSink = self.sink
        else:
            runtime_sink = FrameSinkRouter(self.sinks)
            self.sink = next(iter(self.sinks.values()))
        agent_ids = sorted(
            {output.agent_id for scene in catalog.scenes.values() for output in scene.media_outputs}
        )
        self.agents = {agent_id: FakeMediaAgent(agent_id=agent_id) for agent_id in agent_ids}
        self.channels = {agent_id: SimulationCommandChannel() for agent_id in agent_ids}
        self.agent = next(iter(self.agents.values()), None)
        self.channel = self.channels[self.agent.agent_id] if self.agent is not None else None
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
            sink=runtime_sink,
            clock=self.clock.monotonic,
            event_clock=self.clock.epoch_ms,
            heartbeat_timeout_ms=86_400_000,
            command_timeout_ms=86_400_000,
            director=director,
            audience=audience,
        )
        for agent_id, agent in self.agents.items():
            channel = self.channels[agent_id]
            agent.accept_welcome(self.runtime.register_agent(agent.hello(), channel))
        self.scene_history: list[str] = []
        self._active_started_at_ms = 0
        self._attached_playback_session_id: str | None = None
        self._attached_playback_sessions: dict[str, str] = {}
        self._event_sequence = 0
        self._acknowledged_requests: set[str] = set()

    async def start(self) -> None:
        await self.runtime.start_show()
        self._sync_active()
        await self._acknowledge_prepares()

    async def advance(self, delta_ms: int) -> None:
        if delta_ms < 0:
            raise ValueError("simulation cannot move backward")
        target_ms = self.clock.now_ms + delta_ms
        while self.runtime.active_scene is not None:
            self._sync_active()
            await self._acknowledge_prepares()
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
            clock_session = active.clock_session
            if clock_session is None:
                self.runtime.render_frame()
            else:
                telemetry = self.agents[clock_session.agent_id].position(
                    duration_ms,
                    state=PlaybackState.COMPLETED,
                    duration_ms=duration_ms,
                )
                self.runtime.ingest(telemetry)
            await self.runtime.wait_for_lifecycle_idle()
            if self.runtime.active_scene is None:
                break
            if self.runtime.active_scene is active:
                break
            self._sync_active()
        self.clock.now_ms = target_ms
        await self.runtime.tick_encounter()
        self._sync_active()
        await self._acknowledge_prepares()
        if (
            self.runtime.active_scene is not None
            and self.runtime.active_scene.clock_session is None
        ):
            self.runtime.render_frame()

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
        await self._acknowledge_prepares()

    async def request_scene(self, scene_id: str) -> str:
        self.report_position()
        decision = await self.runtime.request_scene(scene_id)
        self._sync_active()
        await self._acknowledge_prepares()
        return decision

    def report_position(self) -> None:
        active = self.runtime.active_scene
        if active is None:
            raise RuntimeError("simulation has no active scene")
        for session in active.resolved_media_sessions:
            self.runtime.ingest(
                self.agents[session.agent_id].position(
                    self.clock.now_ms - self._active_started_at_ms,
                    duration_ms=active.definition.duration_ms,
                )
            )

    def disconnect_agent(self, agent_id: str | None = None) -> None:
        selected_id = agent_id or self._active_clock_agent_id()
        self.runtime.disconnect_agent(selected_id, self.channels[selected_id])

    def reconnect_agent(self, agent_id: str | None = None) -> None:
        selected_id = agent_id or self._active_clock_agent_id()
        agent = self.agents[selected_id]
        agent.reconnect()
        welcome = self.runtime.register_agent(
            agent.hello(agent.snapshot(0)),
            self.channels[selected_id],
        )
        agent.accept_welcome(welcome)

    def frames_for(self, resource: str) -> tuple[RenderedFrame, ...]:
        try:
            sink = self.sinks[resource]
        except KeyError as error:
            raise LookupError(f"unknown simulated lighting resource: {resource}") from error
        return tuple(sink.frames)

    def set_output_failure(self, resource: str, *, failed: bool) -> None:
        try:
            sink = self.sinks[resource]
        except KeyError as error:
            raise LookupError(f"unknown simulated lighting resource: {resource}") from error
        sink.failure = "injected simulation output failure" if failed else None

    def _active_clock_agent_id(self) -> str:
        active = self.runtime.active_scene
        if active is not None and active.clock_session is not None:
            return active.clock_session.agent_id
        if self.agent is not None:
            return self.agent.agent_id
        raise RuntimeError("simulation has no media agents")

    async def _acknowledge_prepares(self) -> None:
        for agent_id, channel in self.channels.items():
            for command in tuple(channel.sent):
                request_id = command["request_id"]
                if command["command"] != "prepare" or request_id in self._acknowledged_requests:
                    continue
                self._acknowledged_requests.add(request_id)
                await self.runtime.acknowledge_command(
                    self.agents[agent_id].acknowledge(request_id)
                )

    async def shutdown(self) -> None:
        await self.runtime.shutdown()

    def _sync_active(self) -> None:
        active = self.runtime.active_scene
        if active is None:
            return
        for session in active.resolved_media_sessions:
            if (
                self._attached_playback_sessions.get(session.agent_id)
                == session.playback_session_id
            ):
                continue
            self.agents[session.agent_id].attach(
                active.scene_instance_id,
                session.playback_session_id,
            )
            self._attached_playback_sessions[session.agent_id] = session.playback_session_id
        if active.playback_session_id == self._attached_playback_session_id:
            return
        self._active_started_at_ms = self.clock.now_ms
        self._attached_playback_session_id = active.playback_session_id
        self.scene_history.append(active.definition.id)
