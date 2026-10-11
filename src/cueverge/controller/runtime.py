import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from cueverge.core.arbitration import ArbitrationDecision, ResourceArbiter
from cueverge.core.director import ShowDirector
from cueverge.core.encounter import EncounterEngine, EventOutcome
from cueverge.core.events import ExternalEvent
from cueverge.core.loader import load_show_package
from cueverge.core.models import (
    LoadedLightingLooks,
    LoadedLightingOutput,
    LoadedScene,
    SceneCatalog,
    SceneDefinition,
)
from cueverge.core.opportunities import OpportunityManager
from cueverge.lighting.renderer import SegmentPulse, SyntheticSweep, TargetPulse
from cueverge.lighting.sink import FrameSink, FrameSinkRouter, MemoryFrameSink, RenderedFrame
from cueverge.lighting.timeline import LightingTimeline
from cueverge.observation.presence import AudiencePresenceEstimator
from cueverge.protocol.models import (
    AgentCommand,
    AgentHeartbeat,
    AgentHello,
    AgentWelcome,
    CommandAck,
    MediaPosition,
    PlaybackState,
)
from cueverge.timing.follower import TelemetryDecision, TimecodeFollower

Clock = Callable[[], float]
EventClock = Callable[[], int]
SceneSelector = Callable[["ActiveScene"], Awaitable[str | None]]


class CommandChannel(Protocol):
    async def send_json(self, data: Any) -> None: ...


class ShowMode(StrEnum):
    AUTOMATIC = "automatic"
    DISABLED = "disabled"
    MAINTENANCE = "maintenance"
    BLACKOUT = "blackout"


@dataclass(frozen=True)
class ActiveMediaSession:
    output_id: str
    agent_id: str
    playback_session_id: str
    asset_id: str | None
    required: bool
    start_delay_ms: int


@dataclass(frozen=True)
class ActiveScene:
    scene: LoadedScene
    scene_instance_id: str
    playback_session_id: str
    media_sessions: tuple[ActiveMediaSession, ...] = ()

    @property
    def definition(self) -> SceneDefinition:
        return self.scene.definition

    @property
    def resolved_media_sessions(self) -> tuple[ActiveMediaSession, ...]:
        if self.media_sessions:
            return self.media_sessions
        output = self.scene.media_outputs[0]
        return (
            ActiveMediaSession(
                output_id=output.definition.id,
                agent_id=output.agent_id,
                playback_session_id=self.playback_session_id,
                asset_id=output.asset_id,
                required=output.definition.required,
                start_delay_ms=output.definition.start_delay_ms,
            ),
        )

    @property
    def clock_session(self) -> ActiveMediaSession:
        return next(
            session
            for session in self.resolved_media_sessions
            if session.output_id == self.scene.clock_source_id
        )

    def session_for_agent(self, agent_id: str) -> ActiveMediaSession | None:
        return next(
            (session for session in self.resolved_media_sessions if session.agent_id == agent_id),
            None,
        )

    def session_for_playback(self, playback_session_id: str) -> ActiveMediaSession | None:
        return next(
            (
                session
                for session in self.resolved_media_sessions
                if session.playback_session_id == playback_session_id
            ),
            None,
        )


@dataclass
class AgentConnection:
    channel: CommandChannel
    session_id: str
    last_seen_at: float
    last_heartbeat_sequence: int = -1


@dataclass(frozen=True)
class PendingCommand:
    command: AgentCommand
    sent_at: float


class ControllerRuntime:
    def __init__(
        self,
        *,
        clock: Clock = time.monotonic,
        sink: FrameSink | None = None,
        stale_after_ms: int = 500,
        render_fps: float = 25.0,
        heartbeat_interval_ms: int = 1000,
        heartbeat_timeout_ms: int = 3000,
        command_timeout_ms: int = 10000,
        lighting_delay_ms: int = 0,
        controller_epoch: str | None = None,
        catalog: SceneCatalog | None = None,
        event_clock: EventClock | None = None,
        scene_selector: SceneSelector | None = None,
        director: ShowDirector | None = None,
        audience: AudiencePresenceEstimator | None = None,
    ) -> None:
        if render_fps <= 0:
            raise ValueError("render_fps must be positive")
        if heartbeat_interval_ms <= 0 or heartbeat_timeout_ms <= heartbeat_interval_ms:
            raise ValueError("heartbeat timeout must be greater than the positive interval")
        if command_timeout_ms <= 0:
            raise ValueError("command_timeout_ms must be positive")
        if lighting_delay_ms < 0:
            raise ValueError("lighting_delay_ms must be non-negative")
        self.clock = clock
        self.follower = TimecodeFollower(stale_after_ms=stale_after_ms)
        self.render_fps = render_fps
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.heartbeat_timeout_ms = heartbeat_timeout_ms
        self.command_timeout_ms = command_timeout_ms
        self.lighting_delay_ms = lighting_delay_ms
        self.controller_epoch = controller_epoch or str(uuid4())
        self.catalog = catalog or load_show_package(Path("shows/demo"))
        lighting_resources = {
            output.definition.resource
            for scene in self.catalog.scenes.values()
            for output in scene.lighting_outputs
        }
        configured_sink = sink or MemoryFrameSink()
        if isinstance(configured_sink, FrameSinkRouter):
            configured_sink.require_resources(lighting_resources)
        elif len(lighting_resources) > 1:
            raise ValueError("multiple lighting resources require a FrameSinkRouter")
        self.sink = configured_sink
        self.event_clock = event_clock or (lambda: round(time.time() * 1000))
        if scene_selector is not None and director is not None:
            raise ValueError("configure either scene_selector or director, not both")
        self.director = director
        self.audience = audience or (
            AudiencePresenceEstimator(self.catalog.audience)
            if self.catalog.audience is not None
            else None
        )
        self.opportunities = (
            OpportunityManager(
                self.catalog.opportunities,
                self.audience.context if self.audience is not None else lambda: {},
            )
            if self.catalog.opportunities is not None
            else None
        )
        self.scene_selector = scene_selector or (
            self._select_next_scene
            if director is not None or self.opportunities is not None
            else None
        )
        self.arbiter = ResourceArbiter()
        self.encounter = (
            EncounterEngine(
                self.catalog.encounter,
                self.request_scene,
                self._stop_encounter_scene,
            )
            if self.catalog.encounter is not None
            else None
        )
        self.active_scene: ActiveScene | None = None
        self._agents: dict[str, AgentConnection] = {}
        self._pending_commands: dict[str, PendingCommand] = {}
        self._failed_prepare_sessions: set[str] = set()
        self._render_task: asyncio.Task[None] | None = None
        self._encounter_task: asyncio.Task[None] | None = None
        self._lifecycle_tasks: set[asyncio.Task[None]] = set()
        self._handled_completion_sessions: set[str] = set()
        self._completion_history: deque[str] = deque()
        self._last_media_positions: dict[str, int] = {}
        self._media_sessions_with_telemetry: set[str] = set()
        self._lighting_output_errors: dict[str, str] = {}
        self._degraded_reason: str | None = None
        self.show_mode = ShowMode.AUTOMATIC
        self.rejected_telemetry = 0
        self.rejected_protocol_messages = 0
        self.command_errors = 0
        self.command_timeouts = 0
        self.abandoned_commands = 0
        self.heartbeat_timeouts = 0
        self.reconciliations = 0
        self.scene_completions = 0
        self.scene_completion_errors = 0
        self.scene_failovers = 0
        self.render_ticks = 0
        self.rendered_frames = 0
        self.lighting_output_error_count = 0
        self.render_ticks_without_timecode = 0
        self.stale_frames = 0
        self.skipped_render_deadlines = 0
        self._last_frame_at: float | None = None
        self._lighting_started_at = self.clock()
        self._render_interval_total_ms = 0.0
        self._render_interval_count = 0

    @property
    def connected_agent_count(self) -> int:
        return len(self._agents)

    @property
    def render_loop_running(self) -> bool:
        return self._render_task is not None and not self._render_task.done()

    @property
    def encounter_loop_running(self) -> bool:
        return self._encounter_task is not None and not self._encounter_task.done()

    def start_background_tasks(self) -> None:
        if (
            self.encounter is not None or self.audience is not None
        ) and not self.encounter_loop_running:
            self._encounter_task = asyncio.create_task(
                self._run_encounter_loop(),
                name="encounter-timer",
            )

    def register_agent(self, hello: AgentHello, websocket: CommandChannel) -> AgentWelcome:
        previous = self._agents.get(hello.agent_id)
        if previous is not None:
            self._abandon_commands(hello.agent_id)
        self._agents[hello.agent_id] = AgentConnection(
            channel=websocket,
            session_id=hello.agent_session_id,
            last_seen_at=self.clock(),
        )

        reconciliation: Literal["idle", "continue", "stop"] = "idle"
        active = self.active_scene
        playback = hello.playback
        media_session = active.session_for_agent(hello.agent_id) if active is not None else None
        if active is not None and media_session is not None:
            matches = (
                playback is not None
                and playback.controller_epoch == self.controller_epoch
                and playback.scene_instance_id == active.scene_instance_id
                and playback.playback_session_id == media_session.playback_session_id
            )
            if matches and playback is not None:
                if media_session == active.clock_session:
                    telemetry = MediaPosition(
                        agent_id=hello.agent_id,
                        agent_session_id=hello.agent_session_id,
                        controller_epoch=self.controller_epoch,
                        scene_instance_id=playback.scene_instance_id,
                        playback_session_id=playback.playback_session_id,
                        sequence=playback.sequence,
                        position_ms=playback.position_ms,
                        playback_rate=playback.playback_rate,
                        state=playback.state,
                        duration_ms=playback.duration_ms,
                        agent_monotonic_ms=playback.agent_monotonic_ms,
                    )
                    decision = self.follower.reconcile(telemetry, self.clock())
                else:
                    decision = TelemetryDecision(True)
                if decision.accepted:
                    reconciliation = "continue"
                    self.reconciliations += 1
                    self._degraded_reason = None
                    self._ensure_render_loop(active.scene_instance_id)
                else:
                    reconciliation = "stop"
                    if media_session == active.clock_session:
                        self._degraded_reason = "reconciliation_required"
                        self._cancel_render_loop()
                    else:
                        self._degraded_reason = (
                            f"media_output_reconciliation_required:{media_session.output_id}"
                        )
            else:
                reconciliation = "stop"
                if media_session == active.clock_session:
                    self._degraded_reason = "reconciliation_required"
                    self._cancel_render_loop()
                else:
                    self._degraded_reason = (
                        f"media_output_reconciliation_required:{media_session.output_id}"
                    )
        elif playback is not None:
            reconciliation = "stop"

        return AgentWelcome(
            controller_epoch=self.controller_epoch,
            heartbeat_interval_ms=self.heartbeat_interval_ms,
            heartbeat_timeout_ms=self.heartbeat_timeout_ms,
            reconciliation=reconciliation,
        )

    def disconnect_agent(self, agent_id: str, websocket: CommandChannel) -> None:
        connection = self._agents.get(agent_id)
        if connection is not None and connection.channel is websocket:
            del self._agents[agent_id]
            self._abandon_commands(agent_id)
            media_session = (
                self.active_scene.session_for_agent(agent_id)
                if self.active_scene is not None
                else None
            )
            if self.active_scene is not None and media_session is not None:
                if media_session == self.active_scene.clock_session:
                    self.follower.disconnect()
                    self._degraded_reason = "agent_disconnected"
                    self._cancel_render_loop()
                else:
                    self._degraded_reason = f"media_output_disconnected:{media_session.output_id}"

    async def start_scene(self, scene_id: str) -> ActiveScene:
        scene = self.catalog.require(scene_id)
        missing_required = [
            output.agent_id
            for output in scene.media_outputs
            if output.definition.required and output.agent_id not in self._agents
        ]
        if missing_required:
            raise RuntimeError(f"required agents are not connected: {sorted(missing_required)}")

        await self._stop_render_loop()
        if self.active_scene is not None:
            await self._stop_media_sessions(self.active_scene)
        for output in scene.media_outputs:
            self._abandon_commands(output.agent_id)
        self._degraded_reason = None
        media_sessions = tuple(
            ActiveMediaSession(
                output_id=output.definition.id,
                agent_id=output.agent_id,
                playback_session_id=str(uuid4()),
                asset_id=output.asset_id,
                required=output.definition.required,
                start_delay_ms=output.definition.start_delay_ms,
            )
            for output in scene.media_outputs
            if output.agent_id in self._agents
        )
        clock_session = next(
            session for session in media_sessions if session.output_id == scene.clock_source_id
        )
        active = ActiveScene(
            scene=scene,
            scene_instance_id=str(uuid4()),
            playback_session_id=clock_session.playback_session_id,
            media_sessions=media_sessions,
        )
        self.active_scene = active
        self._failed_prepare_sessions.clear()
        for session in media_sessions:
            self._last_media_positions[session.playback_session_id] = 0
        self.follower.begin(active.scene_instance_id, active.playback_session_id)
        for session in media_sessions:
            connection = self._agents[session.agent_id]
            await self._send_command(active, session, "prepare", connection)
        self._ensure_render_loop(active.scene_instance_id)
        return active

    async def start_show(self) -> ActiveScene:
        if self.director is None:
            raise RuntimeError("show package does not define a program")
        return await self.start_scene(self.director.start())

    async def set_show_mode(self, mode: ShowMode) -> None:
        self.show_mode = mode
        if mode is ShowMode.AUTOMATIC:
            return
        await self.stop(continue_queue=False)
        if mode is ShowMode.BLACKOUT:
            self._emit_blackout()

    async def reset_ambient(self) -> ActiveScene:
        if self.encounter is not None:
            scene_id = self.encounter.definition.ambient_scene_id
        elif self.director is not None:
            scene_id = self.director.definition.initial_scene_id
        else:
            raise RuntimeError("show package does not define an ambient scene")
        return await self.start_scene(scene_id)

    async def request_scene(self, scene_id: str) -> str:
        incoming = self.catalog.require(scene_id)
        active = self.active_scene.scene if self.active_scene is not None else None
        estimate = self.follower.estimate(self.clock())
        decision = self.arbiter.evaluate(
            active,
            incoming,
            now_ms=round(self.clock() * 1000),
            position_ms=estimate.position_ms if estimate is not None else None,
        )
        if decision is ArbitrationDecision.START:
            await self.start_scene(scene_id)
        return decision.value

    async def pause(self) -> None:
        await self._send_to_active_media("pause")

    async def resume(self) -> None:
        await self._send_to_active_media("resume")

    async def seek(self, position_ms: int) -> None:
        await self._send_to_active_media("seek", target_position_ms=position_ms)

    async def ingest_event(self, event: ExternalEvent) -> EventOutcome:
        if self.show_mode is not ShowMode.AUTOMATIC:
            state = (
                self.encounter.state
                if self.encounter is not None
                else self.audience.state.value
                if self.audience is not None
                else "inactive"
            )
            return EventOutcome(False, "show_not_automatic", state, None)
        if self.encounter is None and self.audience is None:
            raise RuntimeError("show package does not define an encounter")
        now_ms = self.event_clock()
        presence_update = self.audience.ingest(event, now_ms) if self.audience else None
        if self.opportunities is not None:
            self.opportunities.ingest(event, now_ms)
            for semantic_event in presence_update.events if presence_update is not None else ():
                self.opportunities.ingest(semantic_event, now_ms)
        if self.encounter is None:
            assert self.audience is not None
            return EventOutcome(
                presence_update.relevant if presence_update is not None else False,
                None if presence_update is not None and presence_update.relevant else "irrelevant",
                self.audience.state.value,
                None,
            )
        await self.encounter.tick(now_ms)
        semantic_outcome = await self._ingest_semantic_events(
            presence_update.events if presence_update is not None else (),
            now_ms,
        )
        source_outcome = await self.encounter.ingest(event, now_ms)
        return semantic_outcome if semantic_outcome is not None else source_outcome

    async def tick_encounter(self) -> None:
        now_ms = self.event_clock()
        semantic_events = self.audience.tick(now_ms) if self.audience else ()
        if self.opportunities is not None:
            self.opportunities.tick(now_ms)
            for event in semantic_events:
                self.opportunities.ingest(event, now_ms)
        if self.encounter is not None:
            await self.encounter.tick(now_ms)
            await self._ingest_semantic_events(semantic_events, now_ms)

    async def _ingest_semantic_events(
        self,
        events: tuple[ExternalEvent, ...],
        now_ms: int,
    ) -> EventOutcome | None:
        if self.encounter is None:
            return None
        accepted: EventOutcome | None = None
        for event in events:
            outcome = await self.encounter.ingest(event, now_ms)
            if outcome.accepted:
                accepted = outcome
        return accepted

    async def _stop_encounter_scene(self) -> None:
        if self.encounter is None or self.active_scene is None:
            return
        managed = {self.encounter.definition.ambient_scene_id}
        managed.update(
            transition.scene_id
            for transition in self.encounter.definition.transitions
            if transition.scene_id is not None
        )
        if self.active_scene.definition.id in managed:
            await self.stop(continue_queue=False)

    def ingest(self, telemetry: MediaPosition) -> TelemetryDecision:
        self.check_timeouts()
        active = self.active_scene
        if active is None:
            self.rejected_telemetry += 1
            return TelemetryDecision(False, "no_active_scene")
        media_session = active.session_for_agent(telemetry.agent_id)
        if media_session is None:
            self.rejected_telemetry += 1
            return TelemetryDecision(False, "wrong_agent")
        connection = self._agents.get(telemetry.agent_id)
        if connection is None or telemetry.agent_session_id != connection.session_id:
            self.rejected_telemetry += 1
            return TelemetryDecision(False, "agent_session_fence")
        if telemetry.controller_epoch != self.controller_epoch:
            self.rejected_telemetry += 1
            return TelemetryDecision(False, "controller_epoch_fence")

        if media_session != active.clock_session:
            if telemetry.scene_instance_id != active.scene_instance_id:
                self.rejected_telemetry += 1
                return TelemetryDecision(False, "scene_fence")
            if telemetry.playback_session_id != media_session.playback_session_id:
                self.rejected_telemetry += 1
                return TelemetryDecision(False, "session_fence")
            connection.last_seen_at = self.clock()
            self._last_media_positions[media_session.playback_session_id] = telemetry.position_ms
            self._media_sessions_with_telemetry.add(media_session.playback_session_id)
            return TelemetryDecision(True)

        decision = self.follower.ingest(telemetry, self.clock())
        if not decision.accepted:
            self.rejected_telemetry += 1
        else:
            connection.last_seen_at = self.clock()
            self._media_sessions_with_telemetry.add(media_session.playback_session_id)
            previous_position_ms = self._last_media_positions.get(
                active.playback_session_id,
                telemetry.position_ms,
            )
            self._last_media_positions[active.playback_session_id] = telemetry.position_ms
            if telemetry.state is PlaybackState.COMPLETED:
                self._schedule_completion(active)
            else:
                ready = self.arbiter.pop_ready(
                    active.scene,
                    previous_position_ms=previous_position_ms,
                    position_ms=telemetry.position_ms,
                    now_ms=round(self.clock() * 1000),
                )
                if ready is not None:
                    self._schedule_scene_start(ready.definition.id, active)
        return decision

    async def acknowledge_command(self, acknowledgment: CommandAck) -> TelemetryDecision:
        connection = self._agents.get(acknowledgment.agent_id)
        if acknowledgment.controller_epoch != self.controller_epoch:
            self.rejected_protocol_messages += 1
            return TelemetryDecision(False, "controller_epoch_fence")
        if connection is None or acknowledgment.agent_session_id != connection.session_id:
            self.rejected_protocol_messages += 1
            return TelemetryDecision(False, "agent_session_fence")
        pending = self._pending_commands.get(acknowledgment.request_id)
        if pending is None or pending.command.agent_id != acknowledgment.agent_id:
            self.rejected_protocol_messages += 1
            return TelemetryDecision(False, "unknown_request")

        del self._pending_commands[acknowledgment.request_id]
        connection.last_seen_at = self.clock()
        media_session = (
            self.active_scene.session_for_playback(pending.command.playback_session_id)
            if self.active_scene is not None
            else None
        )
        if acknowledgment.status == "error":
            self.command_errors += 1
            self._degraded_reason = f"command_error:{acknowledgment.error_code or 'unknown'}"
            if media_session is not None:
                self._failed_prepare_sessions.add(media_session.playback_session_id)
            if pending.command.command == "prepare" and (
                media_session is None or media_session.required
            ):
                self._cancel_render_loop()
            if (
                pending.command.command == "prepare"
                and self.active_scene is not None
                and media_session is not None
                and media_session.required
                and self.active_scene.definition.failure_scene_id is not None
            ):
                fallback = self.active_scene.definition.failure_scene_id
                self.scene_failovers += 1
                await self.start_scene(fallback)
        if pending.command.command == "prepare" and self.active_scene is not None:
            await self._play_when_prepared(self.active_scene)
        return TelemetryDecision(True)

    async def _send_command(
        self,
        active: ActiveScene,
        media_session: ActiveMediaSession,
        command_name: Literal[
            "prepare",
            "play",
            "pause",
            "resume",
            "seek",
            "stop",
            "get_status",
        ],
        connection: AgentConnection,
        *,
        target_position_ms: int | None = None,
    ) -> None:
        command = AgentCommand(
            request_id=str(uuid4()),
            command=command_name,
            agent_id=media_session.agent_id,
            agent_session_id=connection.session_id,
            controller_epoch=self.controller_epoch,
            scene_id=active.definition.id,
            scene_instance_id=active.scene_instance_id,
            playback_session_id=media_session.playback_session_id,
            asset_id=media_session.asset_id,
            target_position_ms=target_position_ms,
        )
        self._pending_commands[command.request_id] = PendingCommand(command, self.clock())
        await connection.channel.send_json(command.model_dump(mode="json"))

    async def _play_when_prepared(self, active: ActiveScene) -> None:
        if self.active_scene is not active:
            return
        has_pending_prepare = any(
            pending.command.command == "prepare"
            and pending.command.scene_instance_id == active.scene_instance_id
            for pending in self._pending_commands.values()
        )
        required_failed = any(
            session.required and session.playback_session_id in self._failed_prepare_sessions
            for session in active.resolved_media_sessions
        )
        if has_pending_prepare or required_failed:
            return
        for session in active.resolved_media_sessions:
            if session.playback_session_id in self._failed_prepare_sessions:
                continue
            connection = self._agents.get(session.agent_id)
            if connection is not None:
                if session.start_delay_ms == 0:
                    await self._send_command(active, session, "play", connection)
                else:
                    task = asyncio.create_task(
                        self._send_delayed_play(active, session),
                        name=f"delayed-play-{session.output_id}",
                    )
                    self._lifecycle_tasks.add(task)
                    task.add_done_callback(self._lifecycle_task_done)

    async def _send_delayed_play(
        self,
        active: ActiveScene,
        media_session: ActiveMediaSession,
    ) -> None:
        await asyncio.sleep(media_session.start_delay_ms / 1000)
        if self.active_scene is not active:
            return
        connection = self._agents.get(media_session.agent_id)
        if connection is not None:
            await self._send_command(active, media_session, "play", connection)

    async def _send_to_active_media(
        self,
        command_name: Literal["pause", "resume", "seek"],
        *,
        target_position_ms: int | None = None,
    ) -> None:
        active = self.active_scene
        if active is None:
            raise RuntimeError("no active scene")
        for session in active.resolved_media_sessions:
            connection = self._agents.get(session.agent_id)
            if connection is None:
                if session.required:
                    raise RuntimeError(f"required agent is not connected: {session.agent_id}")
                continue
            await self._send_command(
                active,
                session,
                command_name,
                connection,
                target_position_ms=target_position_ms,
            )

    def heartbeat(self, heartbeat: AgentHeartbeat) -> TelemetryDecision:
        if heartbeat.controller_epoch != self.controller_epoch:
            self.rejected_protocol_messages += 1
            return TelemetryDecision(False, "controller_epoch_fence")
        connection = self._agents.get(heartbeat.agent_id)
        if connection is None or heartbeat.agent_session_id != connection.session_id:
            self.rejected_protocol_messages += 1
            return TelemetryDecision(False, "agent_session_fence")
        if heartbeat.sequence <= connection.last_heartbeat_sequence:
            self.rejected_protocol_messages += 1
            return TelemetryDecision(False, "out_of_order")
        connection.last_heartbeat_sequence = heartbeat.sequence
        connection.last_seen_at = self.clock()
        return TelemetryDecision(True)

    def check_timeouts(self) -> None:
        now = self.clock()
        heartbeat_limit = self.heartbeat_timeout_ms / 1000
        expired_agents = [
            agent_id
            for agent_id, connection in self._agents.items()
            if now - connection.last_seen_at > heartbeat_limit
        ]
        for agent_id in expired_agents:
            del self._agents[agent_id]
            self._abandon_commands(agent_id)
            self.heartbeat_timeouts += 1
            media_session = (
                self.active_scene.session_for_agent(agent_id)
                if self.active_scene is not None
                else None
            )
            if self.active_scene is not None and media_session is not None:
                if media_session == self.active_scene.clock_session:
                    self.follower.disconnect()
                    self._degraded_reason = "heartbeat_timeout"
                    self._cancel_render_loop()
                else:
                    self._degraded_reason = (
                        f"media_output_heartbeat_timeout:{media_session.output_id}"
                    )

        command_limit = self.command_timeout_ms / 1000
        expired_commands = {
            request_id: pending
            for request_id, pending in self._pending_commands.items()
            if now - pending.sent_at > command_limit
        }
        if expired_commands:
            for request_id in expired_commands:
                del self._pending_commands[request_id]
            self.command_timeouts += len(expired_commands)
            self._degraded_reason = "command_timeout"
            required_prepare_timed_out = any(
                pending.command.command == "prepare"
                and self.active_scene is not None
                and (
                    session := self.active_scene.session_for_playback(
                        pending.command.playback_session_id
                    )
                )
                is not None
                and session.required
                for pending in expired_commands.values()
            )
            if required_prepare_timed_out:
                self._cancel_render_loop()

    def render_frame(self) -> bool:
        self.check_timeouts()
        self.render_ticks += 1
        active = self.active_scene
        estimate = self.follower.estimate(self.clock())
        if active is None or estimate is None or estimate.reason == "agent_disconnected":
            self.render_ticks_without_timecode += 1
            return False

        rendered_at = self.clock()
        render_position_ms = max(0, estimate.position_ms - self.lighting_delay_ms)
        for output in active.scene.lighting_outputs:
            output_position_ms = max(
                0,
                render_position_ms - output.definition.timing_offset_ms,
            )
            pixels = self._render_lighting_output(
                output,
                render_position_ms=output_position_ms,
                rendered_at=rendered_at,
                duration_ms=active.definition.duration_ms,
            )
            try:
                self.sink.send(
                    RenderedFrame(
                        scene_instance_id=active.scene_instance_id,
                        position_ms=output_position_ms,
                        pixels=pixels,
                        resource=output.definition.resource,
                    )
                )
            except (OSError, ValueError) as error:
                self.lighting_output_error_count += 1
                self._lighting_output_errors[output.definition.resource] = (
                    f"{type(error).__name__}: {error}"
                )
                continue
            self._lighting_output_errors.pop(output.definition.resource, None)
            self.rendered_frames += 1
        if estimate.degraded:
            self.stale_frames += 1
        if self._last_frame_at is not None:
            self._render_interval_total_ms += (rendered_at - self._last_frame_at) * 1000
            self._render_interval_count += 1
        self._last_frame_at = rendered_at
        return True

    def _render_lighting_output(
        self,
        output: LoadedLightingOutput,
        *,
        render_position_ms: int,
        rendered_at: float,
        duration_ms: int,
    ) -> tuple[tuple[int, int, int], ...]:
        lighting = output.definition
        if lighting.timeline:
            base_frame = self._render_persistent_baseline(rendered_at, output)
            looks = self._lighting_looks_for(lighting.resource)
            brightness_limit = (
                min(
                    lighting.brightness_limit,
                    looks.definition.brightness_limit,
                )
                if looks is not None
                else lighting.brightness_limit
            )
            return LightingTimeline(
                pixel_count=output.pixel_count,
                cues=output.lighting_cues,
                brightness_limit=brightness_limit,
            ).render(render_position_ms, base_frame=base_frame)
        if lighting.effect == "segment_pulse":
            segment = output.lighting_segment
            if output.lighting_target_indices:
                return TargetPulse(
                    pixel_count=output.pixel_count,
                    target_indices=output.lighting_target_indices,
                    pulse_times_ms=lighting.pulse_times_ms,
                    pulse_duration_ms=lighting.pulse_duration_ms,
                    color=output.color,
                ).render(render_position_ms)
            if segment is not None:
                return SegmentPulse(
                    pixel_count=output.pixel_count,
                    segment_start=segment.start,
                    segment_end=segment.end,
                    pulse_times_ms=lighting.pulse_times_ms,
                    pulse_duration_ms=lighting.pulse_duration_ms,
                    color=output.color,
                ).render(render_position_ms)
            raise RuntimeError("segment_pulse scene has no bound pixel target")
        return SyntheticSweep(
            pixel_count=output.pixel_count,
            cycle_ms=duration_ms,
            color=output.color,
        ).render(render_position_ms)

    async def stop(self, *, continue_queue: bool = True) -> None:
        await self._stop_render_loop()
        active = self.active_scene
        if active is not None:
            await self._stop_media_sessions(active)
        self._degraded_reason = None
        self.active_scene = None
        self.follower.reset()
        queued = None
        if continue_queue:
            queued = self.arbiter.pop_next(now_ms=round(self.clock() * 1000))
        if queued is not None:
            await self.start_scene(queued.definition.id)
        else:
            self._emit_safe_lighting()

    def _render_persistent_baseline(
        self,
        now: float,
        output: LoadedLightingOutput,
    ) -> tuple[tuple[int, int, int], ...] | None:
        looks = self._lighting_looks_for(output.definition.resource)
        if looks is None:
            return None
        elapsed_ms = round((now - self._lighting_started_at) * 1000)
        position_ms = elapsed_ms % looks.definition.cycle_ms
        return LightingTimeline(
            pixel_count=looks.pixel_count,
            cues=looks.baseline_cues,
            brightness_limit=1.0,
        ).render(position_ms)

    def _emit_safe_lighting(self) -> None:
        looks_by_resource = self.catalog.lighting_looks_by_resource
        if not looks_by_resource and self.catalog.lighting_looks is not None:
            looks = self.catalog.lighting_looks
            looks_by_resource = {looks.definition.resource: looks}
        for resource, looks in looks_by_resource.items():
            pixels = LightingTimeline(
                pixel_count=looks.pixel_count,
                cues=looks.safe_state_cues,
                brightness_limit=looks.definition.brightness_limit,
            ).render(0)
            try:
                self.sink.send(
                    RenderedFrame(
                        scene_instance_id="safe-state",
                        position_ms=0,
                        pixels=pixels,
                        resource=resource,
                    )
                )
            except (OSError, ValueError) as error:
                self.lighting_output_error_count += 1
                self._lighting_output_errors[resource] = f"{type(error).__name__}: {error}"
                continue
            self._lighting_output_errors.pop(resource, None)
            self.rendered_frames += 1

    def _lighting_looks_for(self, resource: str) -> LoadedLightingLooks | None:
        looks = self.catalog.lighting_looks_by_resource.get(resource)
        if looks is not None:
            return looks
        legacy = self.catalog.lighting_looks
        if legacy is not None and legacy.definition.resource == resource:
            return legacy
        return None

    def _emit_blackout(self) -> None:
        outputs = {
            output.definition.resource: output.pixel_count
            for scene in self.catalog.scenes.values()
            for output in scene.lighting_outputs
        }
        for resource, pixel_count in outputs.items():
            self.sink.send(
                RenderedFrame(
                    scene_instance_id="blackout",
                    position_ms=0,
                    pixels=((0, 0, 0),) * pixel_count,
                    resource=resource,
                )
            )
            self.rendered_frames += 1

    async def _stop_media_sessions(self, active: ActiveScene) -> None:
        for session in active.resolved_media_sessions:
            connection = self._agents.get(session.agent_id)
            self._abandon_commands(session.agent_id)
            if connection is not None:
                await self._send_command(active, session, "stop", connection)

    async def shutdown(self) -> None:
        await self.stop(continue_queue=False)
        await self._stop_encounter_loop()
        lifecycle_tasks = tuple(self._lifecycle_tasks)
        for task in lifecycle_tasks:
            task.cancel()
        for task in lifecycle_tasks:
            with suppress(asyncio.CancelledError):
                await task
        self.sink.close()

    async def wait_for_lifecycle_idle(self) -> None:
        while self._lifecycle_tasks:
            await asyncio.gather(*tuple(self._lifecycle_tasks), return_exceptions=True)

    def _schedule_completion(self, active: ActiveScene) -> None:
        session_id = active.playback_session_id
        if session_id in self._handled_completion_sessions:
            return
        self._handled_completion_sessions.add(session_id)
        self._completion_history.append(session_id)
        if len(self._completion_history) > 1024:
            expired = self._completion_history.popleft()
            self._handled_completion_sessions.discard(expired)
        task = asyncio.create_task(
            self._apply_completion(active),
            name=f"complete-{active.scene_instance_id}",
        )
        self._lifecycle_tasks.add(task)
        task.add_done_callback(self._lifecycle_task_done)

    async def _apply_completion(self, completed: ActiveScene) -> None:
        active = self.active_scene
        if active is None or active.playback_session_id != completed.playback_session_id:
            return
        self.scene_completions += 1
        queued = self.arbiter.pop_ready(
            completed.scene,
            previous_position_ms=self._last_media_positions.get(
                completed.playback_session_id,
                0,
            ),
            position_ms=completed.definition.duration_ms,
            now_ms=round(self.clock() * 1000),
            completed=True,
        )
        if queued is not None:
            await self.start_scene(queued.definition.id)
            return
        completion = completed.definition.completion
        if completion.policy == "hold":
            return
        if completion.policy == "stop":
            await self.stop()
            return
        if completion.policy == "loop":
            await self.start_scene(completed.definition.id)
            return
        if completion.policy == "transition_to":
            if completion.target_scene_id is None:
                raise RuntimeError("transition_to completion has no target scene")
            await self.start_scene(completion.target_scene_id)
            return
        if self.scene_selector is None:
            self.scene_completion_errors += 1
            self._degraded_reason = "scene_selector_unavailable"
            return
        selected = await self.scene_selector(completed)
        if selected is None:
            await self.stop()
        else:
            await self.start_scene(selected)

    async def _select_next_scene(self, completed: ActiveScene) -> str | None:
        if self.opportunities is not None:
            opportunity = self.opportunities.consume_next(self.event_clock())
            if opportunity is not None:
                return opportunity.scene_id
        if self.director is not None:
            return self.director.select_next(completed.definition.id)
        return None

    def _schedule_scene_start(self, scene_id: str, expected_active: ActiveScene) -> None:
        async def start_if_current() -> None:
            if (
                self.active_scene is not None
                and self.active_scene.playback_session_id == expected_active.playback_session_id
            ):
                await self.start_scene(scene_id)

        task = asyncio.create_task(
            start_if_current(),
            name=f"scheduled-scene-{scene_id}",
        )
        self._lifecycle_tasks.add(task)
        task.add_done_callback(self._lifecycle_task_done)

    def _lifecycle_task_done(self, task: asyncio.Task[None]) -> None:
        self._lifecycle_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.scene_completion_errors += 1
            self._degraded_reason = f"scene_completion_error:{type(error).__name__}"

    async def _run_render_loop(self, scene_instance_id: str) -> None:
        interval = 1 / self.render_fps
        next_deadline = self.clock()
        try:
            while (
                self.active_scene is not None
                and self.active_scene.scene_instance_id == scene_instance_id
            ):
                self.render_frame()
                next_deadline += interval
                now = self.clock()
                delay = next_deadline - now
                if delay <= 0:
                    missed = int((now - next_deadline) // interval) + 1
                    self.skipped_render_deadlines += missed
                    next_deadline += missed * interval
                    delay = next_deadline - now
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        finally:
            if asyncio.current_task() is self._render_task:
                self._render_task = None

    async def _run_encounter_loop(self) -> None:
        try:
            while self.encounter is not None or self.audience is not None:
                await self.tick_encounter()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        finally:
            if asyncio.current_task() is self._encounter_task:
                self._encounter_task = None

    def _ensure_render_loop(self, scene_instance_id: str) -> None:
        if self.render_loop_running:
            return
        self._render_task = asyncio.create_task(
            self._run_render_loop(scene_instance_id),
            name=f"render-{scene_instance_id}",
        )

    def _abandon_commands(self, agent_id: str) -> None:
        abandoned = [
            request_id
            for request_id, pending in self._pending_commands.items()
            if pending.command.agent_id == agent_id
        ]
        for request_id in abandoned:
            del self._pending_commands[request_id]
        self.abandoned_commands += len(abandoned)

    def _cancel_render_loop(self) -> None:
        task = self._render_task
        self._render_task = None
        if task is not None:
            task.cancel()

    async def _stop_render_loop(self) -> None:
        task = self._render_task
        self._cancel_render_loop()
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task

    async def _stop_encounter_loop(self) -> None:
        task = self._encounter_task
        self._encounter_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    def status(self) -> dict[str, object]:
        self.check_timeouts()
        estimate = self.follower.estimate(self.clock())
        mean_interval_ms = (
            self._render_interval_total_ms / self._render_interval_count
            if self._render_interval_count
            else None
        )
        estimate_reason = estimate.reason if estimate else None
        lighting_error_reason = (
            f"lighting_output_error:{sorted(self._lighting_output_errors)[0]}"
            if self._lighting_output_errors
            else None
        )
        degraded_reason = self._degraded_reason or lighting_error_reason or estimate_reason
        if degraded_reason is None and estimate is None and self.active_scene:
            degraded_reason = "awaiting_telemetry"
        clock_session_id = (
            self.active_scene.clock_session.playback_session_id
            if self.active_scene is not None
            else None
        )
        clock_position = (
            self._last_media_positions.get(clock_session_id)
            if clock_session_id in self._media_sessions_with_telemetry
            else None
        )
        active_media = (
            [
                {
                    "output_id": session.output_id,
                    "agent_id": session.agent_id,
                    "playback_session_id": session.playback_session_id,
                    "required": session.required,
                    "start_delay_ms": session.start_delay_ms,
                    "clock_source": session == self.active_scene.clock_session,
                    "connected": session.agent_id in self._agents,
                    "prepare_failed": (
                        session.playback_session_id in self._failed_prepare_sessions
                    ),
                    "last_position_ms": (
                        self._last_media_positions.get(session.playback_session_id)
                        if session.playback_session_id in self._media_sessions_with_telemetry
                        else None
                    ),
                    "drift_ms": (
                        self._last_media_positions[session.playback_session_id] - clock_position
                        if clock_position is not None
                        and session.playback_session_id in self._media_sessions_with_telemetry
                        else None
                    ),
                    "pending_commands": sum(
                        pending.command.playback_session_id == session.playback_session_id
                        for pending in self._pending_commands.values()
                    ),
                }
                for session in self.active_scene.resolved_media_sessions
            ]
            if self.active_scene is not None
            else []
        )
        active_lighting = (
            [
                {
                    "output_id": output.definition.id,
                    "resource": output.definition.resource,
                    "required": output.definition.required,
                    "pixel_count": output.pixel_count,
                    "timing_offset_ms": output.definition.timing_offset_ms,
                    "error": self._lighting_output_errors.get(output.definition.resource),
                }
                for output in self.active_scene.scene.lighting_outputs
            ]
            if self.active_scene is not None
            else []
        )
        return {
            "controller_epoch": self.controller_epoch,
            "active_scene": self.active_scene.definition.id if self.active_scene else None,
            "scene_instance_id": self.active_scene.scene_instance_id if self.active_scene else None,
            "playback_session_id": self.active_scene.playback_session_id
            if self.active_scene
            else None,
            "estimated_position_ms": estimate.position_ms if estimate else None,
            "playback_state": estimate.state if estimate else None,
            "degraded": degraded_reason is not None,
            "degraded_reason": degraded_reason,
            "connected_agents": sorted(self._agents),
            "media_outputs": active_media,
            "lighting_outputs": active_lighting,
            "pending_commands": len(self._pending_commands),
            "command_errors": self.command_errors,
            "command_timeouts": self.command_timeouts,
            "abandoned_commands": self.abandoned_commands,
            "heartbeat_timeouts": self.heartbeat_timeouts,
            "reconciliations": self.reconciliations,
            "scene_completions": self.scene_completions,
            "scene_completion_errors": self.scene_completion_errors,
            "scene_failovers": self.scene_failovers,
            "rejected_protocol_messages": self.rejected_protocol_messages,
            "rendered_frames": self.rendered_frames,
            "lighting_output_error_count": self.lighting_output_error_count,
            "rejected_telemetry": self.rejected_telemetry,
            "render_target_fps": self.render_fps,
            "render_loop_running": self.render_loop_running,
            "render_ticks": self.render_ticks,
            "render_ticks_without_timecode": self.render_ticks_without_timecode,
            "stale_frames": self.stale_frames,
            "skipped_render_deadlines": self.skipped_render_deadlines,
            "mean_render_interval_ms": mean_interval_ms,
            "encounter": self.encounter.status() if self.encounter is not None else None,
            "director": self.director.status() if self.director is not None else None,
            "audience": self.audience.status() if self.audience is not None else None,
            "opportunities": (
                self.opportunities.status(self.event_clock())
                if self.opportunities is not None
                else None
            ),
            "encounter_loop_running": self.encounter_loop_running,
            "queued_scenes": [scene.definition.id for scene in self.arbiter.queued],
            "operations": {
                "show_enabled": self.show_mode is ShowMode.AUTOMATIC,
                "mode": self.show_mode.value,
            },
            "health": {
                "media_agent": ("connected" if self.connected_agent_count > 0 else "disconnected"),
                "lighting_output": type(self.sink).__name__,
                "projector": "not_independently_detectable",
                "audio": "not_independently_detectable",
            },
        }
