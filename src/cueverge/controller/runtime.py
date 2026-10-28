import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from cueverge.controller.loop_monitor import LoopStallMonitor
from cueverge.controller.timing_calibration import (
    CalibrationLightTarget,
    CalibrationMediaTarget,
    TimingCalibrationRun,
    TimingCalibrationRunError,
    TimingCalibrationRunStatus,
)
from cueverge.core.arbitration import ArbitrationDecision, ResourceArbiter
from cueverge.core.director import ShowDirector
from cueverge.core.encounter import EncounterEngine, EventOutcome, ObservedEventOutcome
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
from cueverge.lighting.ambient import render_ambient, render_ambient_output, scene_inherits_ambient
from cueverge.lighting.ddp import DdpUdpSink
from cueverge.lighting.evaluation import evaluate_lighting_output
from cueverge.lighting.output_test import (
    OutputTestError,
    OutputTestRunner,
    OutputTestSegment,
    OutputTestStatus,
)
from cueverge.lighting.sink import FrameSink, FrameSinkRouter, MemoryFrameSink, RenderedFrame
from cueverge.lighting.timeline import LightingTimeline
from cueverge.observation.presence import AudiencePresenceEstimator
from cueverge.protocol.models import (
    MEDIA_REPEAT_CAPABILITY,
    MEDIA_SEAMLESS_CAPABILITY,
    AgentCommand,
    AgentHeartbeat,
    AgentHello,
    AgentWelcome,
    CommandAck,
    MediaCommandName,
    MediaPosition,
    PlaybackState,
)
from cueverge.timing.controller import ControllerClock
from cueverge.timing.follower import TelemetryDecision, TimecodeFollower, TimeEstimate

Clock = Callable[[], float]
EventClock = Callable[[], int]
SceneSelector = Callable[["ActiveScene"], Awaitable[str | None]]


SEAMLESS_PRELOAD_LEAD_MS = 3000
# A follower whose media ends within this of the clock media also cuts by itself.
AUTOMATIC_CUT_TOLERANCE_MS = 50
"""How long before a scene's end its successor is chosen and its media preloaded."""


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
    end_behavior: Literal["stop", "hold_last_frame", "loop"]
    repeat_count: int | None = None
    # Installation latency compensation included in start_delay_ms.
    installation_delay_ms: int = 0


@dataclass(frozen=True)
class PlannedHandoff:
    """The next scene chosen ahead of time, with media preloaded behind the current files.

    ``sessions`` maps agent IDs to the next scene's media sessions queued on those agents.
    ``auto_agent_id`` continues into its queued file by itself when the current clock media
    ends, so the cut is frame-accurate. ``follow_agent_ids`` are other agents whose media
    ends with the clock media and that cut by themselves too; the rest switch on
    ``advance``.
    """

    from_scene_instance_id: str
    scene_id: str
    scene_instance_id: str
    sessions: dict[str, ActiveMediaSession]
    auto_agent_id: str | None
    follow_agent_ids: frozenset[str] = frozenset()


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
        if self.media_sessions or self.definition.mode == "controller_timed":
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
                end_behavior=output.definition.end_behavior,
                repeat_count=getattr(output.definition, "repeat_count", None),
            ),
        )

    @property
    def clock_session(self) -> ActiveMediaSession | None:
        if self.definition.mode == "controller_timed":
            return None
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
    protocol_version: int
    capabilities: tuple[str, ...]
    last_heartbeat_sequence: int = -1


@dataclass(frozen=True)
class RuntimeAgentStatus:
    agent_id: str
    protocol_version: int
    capabilities: tuple[str, ...]
    last_seen_age_ms: int


@dataclass(frozen=True)
class RuntimeLightingTarget:
    resource: str
    host: str
    expected_pixel_count: int | None


@dataclass(frozen=True)
class PendingCommand:
    command: AgentCommand
    sent_at: float


@dataclass(frozen=True)
class RuntimeCommandFailure:
    agent_id: str
    command: str
    error_code: str
    detail: str | None
    occurred_at: float


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
        automatic_rendering: bool = True,
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
        self.automatic_rendering = automatic_rendering
        self.follower = TimecodeFollower(stale_after_ms=stale_after_ms)
        self._controller_clock: ControllerClock | None = None
        # Installation latency compensation per output resource, stacked under scene delays.
        self._output_timing: dict[str, int] = {}
        # (playback session, completed loops, last raw position) for a looping media clock.
        self._looping_clock: tuple[str, int, int] | None = None
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
        configured_sink = sink or MemoryFrameSink(max_frames=120)
        if isinstance(configured_sink, FrameSinkRouter):
            configured_sink.require_resources(lighting_resources)
        elif len(lighting_resources) > 1:
            raise ValueError("multiple lighting resources require a FrameSinkRouter")
        self.sink = configured_sink
        self._output_test: OutputTestRunner | None = None
        self._timing_calibration: TimingCalibrationRun | None = None
        self._scene_starting = False
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
        self._handoff: PlannedHandoff | None = None
        self._played_scene_instance: str | None = None
        self._media_durations: dict[str, int] = {}
        self._handoff_considered_for: str | None = None
        self._preselected_next: tuple[str, str | None] | None = None
        self.seamless_transitions = 0
        self._agents: dict[str, AgentConnection] = {}
        self._pending_commands: dict[str, PendingCommand] = {}
        self._recent_command_failures: deque[RuntimeCommandFailure] = deque(maxlen=50)
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
        self.loop_monitor: LoopStallMonitor | None = None
        self._last_frame_at: float | None = None
        self._lighting_started_at = self.clock()
        self._render_interval_total_ms = 0.0
        self._render_interval_count = 0
        self._catalog_loading = False
        self._catalog_command_acks: set[tuple[str, str]] | None = None
        self._catalog_commands_sent: set[tuple[str, str]] | None = None
        self._scene_control_generation = 0
        self._background_enabled = False
        self._last_output_frames: dict[str, tuple[float, RenderedFrame]] = {}
        self._monitor_sample_at = float("-inf")
        self._monitor_sample: tuple[tuple[str, float, RenderedFrame], ...] = ()
        self.loaded_catalog: dict[str, object] = {
            "state": "unreported",
            "project_id": None,
            "revision": None,
            "digest_sha256": None,
            "loaded_at": None,
            "generation": None,
        }

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
        self._background_enabled = True
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
            protocol_version=hello.protocol_version,
            capabilities=hello.capabilities,
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

    def set_output_timing(self, delays: Mapping[str, int]) -> None:
        """Replace installation timing; LEDs use it on the next frame, media on the next start."""
        self._output_timing = {resource: int(delay) for resource, delay in delays.items() if delay}

    def output_delay_ms(self, resource: str) -> int:
        return self._output_timing.get(resource, 0)

    def is_agent_registered(self, agent_id: str, websocket: CommandChannel) -> bool:
        connection = self._agents.get(agent_id)
        return connection is not None and connection.channel is websocket

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
        if self._catalog_loading:
            raise RuntimeError("show catalog is changing; retry after Apply finishes")
        test = self.output_test_status()
        if (
            self._scene_starting
            or (test is not None and test.state == "active")
            or self.timing_calibration_active
        ):
            raise RuntimeError(
                "scene start is blocked by an active output test, timing calibration or "
                "scene transition"
            )
        self._scene_starting = True
        try:
            return await self._start_scene(scene_id, self._scene_control_generation)
        finally:
            self._scene_starting = False

    async def _start_scene(self, scene_id: str, expected_generation: int) -> ActiveScene:
        scene = self.catalog.require(scene_id)
        missing_required = [
            output.agent_id
            for output in scene.media_outputs
            if output.definition.required and output.agent_id not in self._agents
        ]
        if missing_required:
            raise RuntimeError(f"required agents are not connected: {sorted(missing_required)}")
        unsupported_repeat = [
            output.agent_id
            for output in scene.media_outputs
            if output.definition.required
            and getattr(output.definition, "repeat_count", None) is not None
            and MEDIA_REPEAT_CAPABILITY not in self._agents[output.agent_id].capabilities
        ]
        if unsupported_repeat:
            raise RuntimeError(
                "media devices do not support repeat counts; update their media agent: "
                f"{sorted(unsupported_repeat)}"
            )

        handoff = self._handoff
        self._handoff = None
        self._preselected_next = None
        if handoff is not None and (
            handoff.scene_id != scene_id
            or self.active_scene is None
            or self.active_scene.scene_instance_id != handoff.from_scene_instance_id
        ):
            handoff = None
        adopted = {
            agent_id: session
            for agent_id, session in (handoff.sessions.items() if handoff else ())
            if agent_id in self._agents
        }

        await self._stop_render_loop()
        if self.active_scene is not None:
            await self._stop_media_sessions(self.active_scene, keep_agents=frozenset(adopted))
        if expected_generation != self._scene_control_generation:
            raise RuntimeError("scene start was cancelled by Stop")
        for output in scene.media_outputs:
            self._abandon_commands(output.agent_id)
        self._degraded_reason = None
        media_sessions = tuple(
            adopted.get(output.agent_id)
            or ActiveMediaSession(
                output_id=output.definition.id,
                agent_id=output.agent_id,
                playback_session_id=str(uuid4()),
                asset_id=output.asset_id,
                required=output.definition.required,
                start_delay_ms=output.definition.start_delay_ms
                + self.output_delay_ms(output.definition.resource),
                end_behavior=output.definition.end_behavior,
                repeat_count=getattr(output.definition, "repeat_count", None),
                installation_delay_ms=self.output_delay_ms(output.definition.resource),
            )
            for output in scene.media_outputs
            if output.agent_id in self._agents
            and (
                getattr(output.definition, "repeat_count", None) is None
                or MEDIA_REPEAT_CAPABILITY in self._agents[output.agent_id].capabilities
            )
        )
        clock_session = (
            next(
                session for session in media_sessions if session.output_id == scene.clock_source_id
            )
            if scene.definition.mode == "media_synchronized"
            else None
        )
        active = ActiveScene(
            scene=scene,
            scene_instance_id=handoff.scene_instance_id if handoff else str(uuid4()),
            playback_session_id=(
                clock_session.playback_session_id if clock_session is not None else str(uuid4())
            ),
            media_sessions=media_sessions,
        )
        self.active_scene = active
        self._failed_prepare_sessions.clear()
        self._media_durations.clear()
        for session in media_sessions:
            if self.active_scene is not active:
                raise RuntimeError("scene start was cancelled by Stop")
            self._last_media_positions[session.playback_session_id] = 0
        self.follower.begin(active.scene_instance_id, active.playback_session_id)
        self._controller_clock = (
            ControllerClock(active.definition.duration_ms, self.clock())
            if active.definition.mode == "controller_timed"
            else None
        )
        prepared = False
        for session in media_sessions:
            connection = self._agents[session.agent_id]
            if adopted.get(session.agent_id) is session:
                # Already loaded behind the previous file; advance is a no-op if the agent
                # continued into it by itself at the end of the clock media.
                await self._send_command(active, session, "advance", connection)
            else:
                await self._send_command(active, session, "prepare", connection)
                prepared = True
        if adopted:
            self.seamless_transitions += 1
        if not prepared:
            await self._play_when_prepared(active)
        self._ensure_render_loop(active.scene_instance_id)
        return active

    async def start_show(self) -> ActiveScene:
        if self.director is None:
            raise RuntimeError("show package does not define a program")
        return await self.start_scene(self.director.start())

    async def set_show_mode(self, mode: ShowMode) -> None:
        await self.stop_output_test(reason="mode_changed")
        await self.stop_timing_calibration(reason="mode_changed")
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
        estimate = self._estimate_time()
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
        if self._controller_clock is not None:
            self._controller_clock.pause(self.clock())

    async def resume(self) -> None:
        await self._send_to_active_media("resume")
        if self._controller_clock is not None:
            self._controller_clock.resume(self.clock())

    async def seek(self, position_ms: int) -> None:
        if position_ms < 0:
            raise ValueError("position_ms must be non-negative")
        if self._controller_clock is not None:
            position_ms = min(position_ms, self._controller_clock.duration_ms)
        await self._send_to_active_media("seek", target_position_ms=position_ms)
        if self._controller_clock is not None:
            self._controller_clock.seek(position_ms, self.clock())

    async def ingest_event(self, event: ExternalEvent) -> EventOutcome:
        return (await self.ingest_event_with_presence(event)).outcome

    async def ingest_event_with_presence(self, event: ExternalEvent) -> ObservedEventOutcome:
        if self._catalog_loading:
            return ObservedEventOutcome(EventOutcome(False, "catalog_loading", "inactive"), None)
        if self.show_mode is not ShowMode.AUTOMATIC:
            state = (
                self.encounter.state
                if self.encounter is not None
                else self.audience.state.value
                if self.audience is not None
                else "inactive"
            )
            return ObservedEventOutcome(
                EventOutcome(False, "show_not_automatic", state, None),
                None,
            )
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
            return ObservedEventOutcome(
                EventOutcome(
                    presence_update.relevant if presence_update is not None else False,
                    None
                    if presence_update is not None and presence_update.relevant
                    else "irrelevant",
                    self.audience.state.value,
                    None,
                ),
                presence_update.relevant if presence_update is not None else None,
            )
        await self.encounter.tick(now_ms)
        semantic_outcome = await self._ingest_semantic_events(
            presence_update.events if presence_update is not None else (),
            now_ms,
        )
        source_outcome = await self.encounter.ingest(event, now_ms)
        return ObservedEventOutcome(
            semantic_outcome if semantic_outcome is not None else source_outcome,
            presence_update.relevant if presence_update is not None else None,
        )

    async def tick_encounter(self) -> None:
        if self._catalog_loading or self.show_mode is not ShowMode.AUTOMATIC:
            return
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

    def _unwrap_looping_clock(
        self, active: ActiveScene, session: ActiveMediaSession, telemetry: MediaPosition
    ) -> MediaPosition:
        """Scene time from a clock video that loops: its reported position wraps every loop.

        Each wrap adds one file length, so cues and completion follow the scene duration
        rather than the file's.
        """
        file_ms = telemetry.duration_ms
        if session.end_behavior != "loop" or file_ms is None:
            return telemetry
        raw_ms = telemetry.position_ms
        tracked = self._looping_clock
        loops = 0
        if tracked is not None and tracked[0] == telemetry.playback_session_id:
            loops, last_raw_ms = tracked[1], tracked[2]
            if raw_ms + max(250, file_ms // 2) < last_raw_ms:
                loops += 1
        self._looping_clock = (telemetry.playback_session_id, loops, raw_ms)
        return telemetry.model_copy(
            update={
                "position_ms": loops * file_ms + raw_ms,
                "duration_ms": max(active.definition.duration_ms, loops * file_ms + raw_ms),
            }
        )

    def ingest(self, telemetry: MediaPosition) -> TelemetryDecision:
        self.check_timeouts()
        calibration = self._timing_calibration
        if calibration is not None and calibration.owns_playback(telemetry.playback_session_id):
            calibrating = self._agents.get(telemetry.agent_id)
            if calibrating is None or telemetry.agent_session_id != calibrating.session_id:
                return TelemetryDecision(False, "agent_session_fence")
            calibrating.last_seen_at = self.clock()
            calibration.observe_position(telemetry.agent_id, telemetry.position_ms)
            return TelemetryDecision(True)
        active = self.active_scene
        handoff = self._handoff
        if handoff is not None and any(
            session.playback_session_id == telemetry.playback_session_id
            for session in handoff.sessions.values()
        ):
            # Preloaded media that already started; the controller adopts it when the
            # current scene completes, so this is expected rather than a fencing error.
            self._complete_after_automatic_cut(active, handoff, telemetry)
            return TelemetryDecision(False, "handoff_pending")
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
            self._record_media_duration(media_session, telemetry)
            self._media_sessions_with_telemetry.add(media_session.playback_session_id)
            return TelemetryDecision(True)

        self._record_media_duration(media_session, telemetry)
        telemetry = self._unwrap_looping_clock(active, media_session, telemetry)
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
            looped_past_end = (
                media_session.end_behavior == "loop"
                and telemetry.state is PlaybackState.PLAYING
                and telemetry.position_ms >= active.definition.duration_ms
            )
            if telemetry.state is PlaybackState.COMPLETED or looped_past_end:
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
                elif telemetry.state is PlaybackState.PLAYING:
                    self._maybe_plan_handoff(
                        active,
                        telemetry.position_ms,
                        telemetry.duration_ms or active.definition.duration_ms,
                    )
        return decision

    def _complete_after_automatic_cut(
        self,
        active: ActiveScene | None,
        handoff: PlannedHandoff,
        telemetry: MediaPosition,
    ) -> None:
        """Finish the scene once its clock device is playing the preloaded next file.

        The device reports the finished file only once; if that report is lost, its
        telemetry for the next file is proof that the current scene ended.
        """
        clock = active.clock_session if active is not None else None
        connection = self._agents.get(telemetry.agent_id)
        if (
            active is None
            or clock is None
            or handoff.auto_agent_id != telemetry.agent_id
            or clock.agent_id != telemetry.agent_id
            or handoff.from_scene_instance_id != active.scene_instance_id
            or connection is None
            or telemetry.agent_session_id != connection.session_id
            or telemetry.controller_epoch != self.controller_epoch
        ):
            return
        connection.last_seen_at = self.clock()
        self._schedule_completion(active)

    async def acknowledge_command(self, acknowledgment: CommandAck) -> TelemetryDecision:
        connection = self._agents.get(acknowledgment.agent_id)
        if acknowledgment.controller_epoch != self.controller_epoch:
            self.rejected_protocol_messages += 1
            return TelemetryDecision(False, "controller_epoch_fence")
        if connection is None or acknowledgment.agent_session_id != connection.session_id:
            self.rejected_protocol_messages += 1
            return TelemetryDecision(False, "agent_session_fence")
        calibration = self._timing_calibration
        if calibration is not None and calibration.acknowledge(acknowledgment):
            connection.last_seen_at = self.clock()
            return TelemetryDecision(True)
        pending = self._pending_commands.get(acknowledgment.request_id)
        if pending is None or pending.command.agent_id != acknowledgment.agent_id:
            self.rejected_protocol_messages += 1
            return TelemetryDecision(False, "unknown_request")

        del self._pending_commands[acknowledgment.request_id]
        connection.last_seen_at = self.clock()
        if pending.command.command in {"queue", "advance"} and acknowledgment.status == "error":
            await self._handoff_command_failed(pending.command, acknowledgment)
            return TelemetryDecision(True)
        media_session = (
            self.active_scene.session_for_playback(pending.command.playback_session_id)
            if self.active_scene is not None
            else None
        )
        if acknowledgment.status == "error":
            self.command_errors += 1
            self._degraded_reason = f"command_error:{acknowledgment.error_code or 'unknown'}"
            self._recent_command_failures.appendleft(
                RuntimeCommandFailure(
                    agent_id=acknowledgment.agent_id,
                    command=pending.command.command,
                    error_code=acknowledgment.error_code or "unknown",
                    detail=acknowledgment.detail,
                    occurred_at=self.clock(),
                )
            )
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
        elif self._catalog_command_acks is not None:
            self._catalog_command_acks.add(
                (
                    pending.command.playback_session_id,
                    pending.command.command,
                )
            )
        if pending.command.command in {"prepare", "advance"} and self.active_scene is not None:
            await self._play_when_prepared(self.active_scene)
        return TelemetryDecision(True)

    def _maybe_plan_handoff(self, active: ActiveScene, position_ms: int, duration_ms: int) -> None:
        if (
            self._handoff_considered_for == active.scene_instance_id
            or self._catalog_loading
            or self._scene_starting
            or duration_ms - position_ms > SEAMLESS_PRELOAD_LEAD_MS
        ):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # Synchronous callers without an event loop cannot preload.
        self._handoff_considered_for = active.scene_instance_id
        task = asyncio.create_task(
            self._plan_handoff(active),
            name=f"plan-handoff-{active.scene_instance_id}",
        )
        self._lifecycle_tasks.add(task)
        task.add_done_callback(self._lifecycle_task_done)

    async def _plan_handoff(self, active: ActiveScene) -> None:
        """Choose the next scene shortly before the end and preload its media.

        Only outputs whose device already plays this scene and supports seamless
        transitions are preloaded; other outputs start normally when the scene changes.
        """
        if self.active_scene is not active:
            return
        completion = active.definition.completion
        next_scene_id: str | None
        if completion.policy == "transition_to":
            next_scene_id = completion.target_scene_id
        elif completion.policy == "loop":
            next_scene_id = active.definition.id
        elif completion.policy == "select_next" and self.scene_selector is not None:
            next_scene_id = await self.scene_selector(active)
            if self.active_scene is not active:
                return
            self._preselected_next = (active.scene_instance_id, next_scene_id)
        else:
            return
        if next_scene_id is None or next_scene_id not in self.catalog.scenes:
            return
        next_scene = self.catalog.require(next_scene_id)
        current = {session.agent_id: session for session in active.resolved_media_sessions}
        clock = active.clock_session
        auto_agent: str | None = None
        following: set[str] = set()
        clock_media_ms = (
            self._media_durations.get(clock.playback_session_id) if clock is not None else None
        )
        sessions: dict[str, ActiveMediaSession] = {}
        for output in next_scene.media_outputs:
            connection = self._agents.get(output.agent_id)
            running = current.get(output.agent_id)
            repeat_count = getattr(output.definition, "repeat_count", None)
            if (
                connection is None
                or running is None
                or running.playback_session_id in self._failed_prepare_sessions
                or MEDIA_SEAMLESS_CAPABILITY not in connection.capabilities
                or output.definition.start_delay_ms != 0
                or output.asset_id is None
                or (
                    repeat_count is not None
                    and MEDIA_REPEAT_CAPABILITY not in connection.capabilities
                )
            ):
                continue
            sessions[output.agent_id] = ActiveMediaSession(
                output_id=output.definition.id,
                agent_id=output.agent_id,
                playback_session_id=str(uuid4()),
                asset_id=output.asset_id,
                required=output.definition.required,
                # Calibrated output timing applies to a preloaded change as to a normal start.
                start_delay_ms=self.output_delay_ms(output.definition.resource),
                end_behavior=output.definition.end_behavior,
                repeat_count=repeat_count,
                installation_delay_ms=self.output_delay_ms(output.definition.resource),
            )
            if (
                clock is not None
                and clock.agent_id == output.agent_id
                and clock.end_behavior != "loop"
            ):
                auto_agent = output.agent_id
            elif self._ends_with_clock_media(running, clock_media_ms):
                following.add(output.agent_id)
        if not sessions:
            return
        handoff = PlannedHandoff(
            from_scene_instance_id=active.scene_instance_id,
            scene_id=next_scene_id,
            scene_instance_id=str(uuid4()),
            sessions=sessions,
            auto_agent_id=auto_agent,
            # Followers only cut by themselves alongside a clock that does.
            follow_agent_ids=frozenset(following) if auto_agent is not None else frozenset(),
        )
        self._handoff = handoff
        for agent_id, session in sessions.items():
            connection = self._agents.get(agent_id)
            if connection is None or self._handoff is not handoff:
                continue
            await self._send_command(
                active,
                session,
                "queue",
                connection,
                target_scene=(handoff.scene_id, handoff.scene_instance_id),
                auto_advance=agent_id == auto_agent or agent_id in handoff.follow_agent_ids,
            )

    def _record_media_duration(self, session: ActiveMediaSession, telemetry: MediaPosition) -> None:
        if telemetry.duration_ms is not None and session.end_behavior != "loop":
            self._media_durations[session.playback_session_id] = telemetry.duration_ms

    def _ends_with_clock_media(
        self, running: ActiveMediaSession, clock_media_ms: int | None
    ) -> bool:
        """Whether a follower's current media ends together with the clock media."""
        media_ms = self._media_durations.get(running.playback_session_id)
        return (
            running.end_behavior != "loop"
            and clock_media_ms is not None
            and media_ms is not None
            and abs(media_ms - clock_media_ms) <= AUTOMATIC_CUT_TOLERANCE_MS
        )

    async def _handoff_command_failed(
        self, command: AgentCommand, acknowledgment: CommandAck
    ) -> None:
        self._recent_command_failures.appendleft(
            RuntimeCommandFailure(
                agent_id=acknowledgment.agent_id,
                command=command.command,
                error_code=acknowledgment.error_code or "unknown",
                detail=acknowledgment.detail,
                occurred_at=self.clock(),
            )
        )
        handoff = self._handoff
        if command.command == "queue":
            if handoff is not None and command.agent_id in handoff.sessions:
                sessions = {
                    agent_id: session
                    for agent_id, session in handoff.sessions.items()
                    if agent_id != command.agent_id
                }
                self._handoff = replace(
                    handoff,
                    sessions=sessions,
                    auto_agent_id=(
                        None if handoff.auto_agent_id == command.agent_id else handoff.auto_agent_id
                    ),
                    follow_agent_ids=handoff.follow_agent_ids - {command.agent_id},
                )
            return
        active = self.active_scene
        session = (
            active.session_for_playback(command.playback_session_id) if active is not None else None
        )
        connection = self._agents.get(command.agent_id)
        if active is not None and session is not None and connection is not None:
            # The preloaded file could not be switched to; load it the normal way instead.
            await self._send_command(active, session, "prepare", connection)

    async def _send_command(
        self,
        active: ActiveScene,
        media_session: ActiveMediaSession,
        command_name: MediaCommandName,
        connection: AgentConnection,
        *,
        target_position_ms: int | None = None,
        target_scene: tuple[str, str] | None = None,
        auto_advance: bool | None = None,
    ) -> None:
        if command_name != "stop" and self.active_scene is not active:
            raise RuntimeError("scene command was cancelled by Stop")
        loads_media = command_name in {"prepare", "queue"}
        scene_id, scene_instance_id = target_scene or (
            active.definition.id,
            active.scene_instance_id,
        )
        command = AgentCommand(
            request_id=str(uuid4()),
            command=command_name,
            agent_id=media_session.agent_id,
            agent_session_id=connection.session_id,
            controller_epoch=self.controller_epoch,
            scene_id=scene_id,
            scene_instance_id=scene_instance_id,
            playback_session_id=media_session.playback_session_id,
            asset_id=media_session.asset_id if loads_media else None,
            end_behavior=media_session.end_behavior if loads_media else None,
            target_position_ms=target_position_ms,
            repeat_count=media_session.repeat_count if loads_media else None,
            auto_advance=auto_advance if command_name == "queue" else None,
        )
        self._pending_commands[command.request_id] = PendingCommand(command, self.clock())
        if self._catalog_commands_sent is not None:
            self._catalog_commands_sent.add((command.playback_session_id, command_name))
        await connection.channel.send_json(command.model_dump(mode="json"))

    async def _play_when_prepared(self, active: ActiveScene) -> None:
        if self.active_scene is not active:
            return
        if self._played_scene_instance == active.scene_instance_id:
            return
        # Every device must have its file open before any of them starts, or the fastest
        # device starts ahead by the slowest one's load time.
        has_pending_prepare = any(
            pending.command.command in {"prepare", "advance"}
            and pending.command.scene_instance_id == active.scene_instance_id
            for pending in self._pending_commands.values()
        )
        required_failed = any(
            session.required and session.playback_session_id in self._failed_prepare_sessions
            for session in active.resolved_media_sessions
        )
        if has_pending_prepare or required_failed:
            return
        self._played_scene_instance = active.scene_instance_id
        if self._controller_clock is not None:
            self._controller_clock.start(self.clock())
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
            for pending in expired_commands.values():
                self._recent_command_failures.appendleft(
                    RuntimeCommandFailure(
                        agent_id=pending.command.agent_id,
                        command=pending.command.command,
                        error_code="command_timeout",
                        detail="The device did not acknowledge the command before its deadline",
                        occurred_at=now,
                    )
                )
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

    def agent_status(self) -> tuple[RuntimeAgentStatus, ...]:
        self.check_timeouts()
        now = self.clock()
        return tuple(
            RuntimeAgentStatus(
                agent_id=agent_id,
                protocol_version=connection.protocol_version,
                capabilities=connection.capabilities,
                last_seen_age_ms=max(0, round((now - connection.last_seen_at) * 1000)),
            )
            for agent_id, connection in sorted(self._agents.items())
        )

    def lighting_targets(self) -> tuple[RuntimeLightingTarget, ...]:
        sinks: Mapping[str, FrameSink]
        if isinstance(self.sink, FrameSinkRouter):
            sinks = self.sink.sinks
        elif isinstance(self.sink, DdpUdpSink):
            resources = [
                binding.resource
                for binding in self.catalog.installation.resources
                if binding.kind.value == "pixel_output"
            ]
            sinks = {resource: self.sink for resource in resources} if len(resources) == 1 else {}
        else:
            sinks = {}
        return tuple(
            RuntimeLightingTarget(
                resource=resource,
                host=sink.host,
                expected_pixel_count=sink.expected_pixel_count,
            )
            for resource, sink in sorted(sinks.items())
            if isinstance(sink, DdpUdpSink)
        )

    def output_test_status(self) -> OutputTestStatus | None:
        return self._output_test.status if self._output_test is not None else None

    def start_output_test(
        self,
        *,
        resource: str,
        pixel_count: int,
        speed: int,
        duration_seconds: float,
        arm_id: str,
        segments: tuple[OutputTestSegment, ...],
        on_finish: Callable[[OutputTestStatus], None],
    ) -> OutputTestStatus:
        if self.show_mode is not ShowMode.MAINTENANCE or self.active_scene or self._scene_starting:
            raise OutputTestError("Physical output tests require idle maintenance mode")
        current = self.output_test_status()
        if current is not None and current.state == "active":
            raise OutputTestError("An output test already owns the lighting output")
        if self.timing_calibration_active:
            raise OutputTestError("Timing calibration owns the outputs; finish it first")
        target = next((item for item in self.lighting_targets() if item.resource == resource), None)
        if target is None or target.expected_pixel_count != pixel_count:
            raise OutputTestError(
                "Output requires a configured DDP driver with matching pixel count"
            )
        if pixel_count < 1 or speed < 1 or not 0 < duration_seconds <= 30:
            raise OutputTestError("Invalid output test count, speed or duration")
        if any(not 0 <= segment.start < segment.end <= pixel_count for segment in segments):
            raise OutputTestError("Output test segment is outside the configured output")

        def finished(test: OutputTestStatus) -> None:
            if test.error is not None:
                self.lighting_output_error_count += 1
                self._lighting_output_errors[test.resource] = test.error
            else:
                self._lighting_output_errors.pop(test.resource, None)
            on_finish(test)

        self._output_test = OutputTestRunner(
            sink=self.sink,
            on_frame=self._observe_output_frame,
            resource=resource,
            pixel_count=pixel_count,
            speed=speed,
            duration_seconds=duration_seconds,
            arm_id=arm_id,
            segments=segments,
            clock=self.clock,
            fps=60.0,
            on_finish=finished,
        )
        return self._output_test.start()

    @property
    def timing_calibration_active(self) -> bool:
        return self._timing_calibration is not None and self._timing_calibration.active

    def timing_calibration_status(self) -> TimingCalibrationRunStatus | None:
        return self._timing_calibration.status if self._timing_calibration is not None else None

    def start_timing_calibration(
        self,
        *,
        media: tuple[CalibrationMediaTarget, ...],
        lights: tuple[str, ...],
        delays: Mapping[str, int],
        on_finish: Callable[[TimingCalibrationRunStatus], None] | None = None,
    ) -> TimingCalibrationRunStatus:
        """Own the selected outputs and play the calibration beat on them."""
        if self.show_mode is not ShowMode.MAINTENANCE or self.active_scene or self._scene_starting:
            raise TimingCalibrationRunError("Timing calibration requires idle maintenance mode")
        if self._catalog_loading:
            raise TimingCalibrationRunError("the show is being applied; retry when it finishes")
        test = self.output_test_status()
        if test is not None and test.state == "active":
            raise TimingCalibrationRunError("finish the physical output test first")
        if self.timing_calibration_active:
            raise TimingCalibrationRunError("a timing calibration is already running")
        missing = [target.resource for target in media if target.agent_id not in self._agents]
        if missing:
            raise TimingCalibrationRunError(
                f"{', '.join(missing)} has no running player connected to the controller"
            )
        targets = {item.resource: item for item in self.lighting_targets()}
        unknown = [resource for resource in lights if resource not in targets]
        if unknown:
            raise TimingCalibrationRunError(
                f"{', '.join(unknown)} needs a configured LED driver to calibrate"
            )
        runtime = self

        class Agents:
            def connection(
                self, agent_id: str
            ) -> tuple[str, Callable[[object], Awaitable[None]]] | None:
                connection = runtime._agents.get(agent_id)
                return (
                    (connection.session_id, connection.channel.send_json)
                    if connection is not None
                    else None
                )

        self._timing_calibration = TimingCalibrationRun(
            sink=self.sink,
            agents=Agents(),
            controller_epoch=self.controller_epoch,
            media=media,
            lights=tuple(
                CalibrationLightTarget(resource, self._pixel_count(resource, targets[resource]))
                for resource in lights
            ),
            delays=delays,
            clock=self.clock,
            on_frame=self._observe_output_frame,
            on_finish=on_finish,
        )
        return self._timing_calibration.start()

    def _pixel_count(self, resource: str, target: RuntimeLightingTarget) -> int:
        if target.expected_pixel_count is not None:
            return target.expected_pixel_count
        binding = next(
            (item for item in self.catalog.installation.resources if item.resource == resource),
            None,
        )
        if binding is None or binding.pixel_count is None:
            raise TimingCalibrationRunError(f"{resource} has no known pixel count")
        return binding.pixel_count

    def set_timing_calibration_delay(
        self, resource: str, delay_ms: int
    ) -> TimingCalibrationRunStatus:
        if self._timing_calibration is None:
            raise TimingCalibrationRunError("no timing calibration is running")
        return self._timing_calibration.set_delay(resource, delay_ms)

    def resync_timing_calibration(self) -> TimingCalibrationRunStatus:
        if self._timing_calibration is None or not self._timing_calibration.active:
            raise TimingCalibrationRunError("no timing calibration is running")
        self._timing_calibration.resync()
        return self._timing_calibration.status

    async def stop_timing_calibration(
        self, *, reason: str = "operator"
    ) -> TimingCalibrationRunStatus | None:
        if self._timing_calibration is None:
            return None
        return await self._timing_calibration.stop(reason)

    async def stop_output_test(self, *, reason: str = "operator") -> OutputTestStatus | None:
        if self._output_test is None:
            return None
        return await self._output_test.stop(reason)

    def _estimate_time(self) -> TimeEstimate | None:
        if self._controller_clock is not None:
            return self._controller_clock.estimate(self.clock())
        return self.follower.estimate(self.clock())

    def render_frame(self, *, render_pixels: bool = True) -> bool:
        self.check_timeouts()
        self.render_ticks += 1
        active = self.active_scene
        estimate = self._estimate_time()
        if active is None or estimate is None or estimate.reason == "agent_disconnected":
            self.render_ticks_without_timecode += 1
            return False

        if self._controller_clock is not None:
            previous_position_ms = self._last_media_positions.get(active.playback_session_id, 0)
            if estimate.state is PlaybackState.COMPLETED:
                self._schedule_completion(active)
            else:
                ready = self.arbiter.pop_ready(
                    active.scene,
                    previous_position_ms=previous_position_ms,
                    position_ms=estimate.position_ms,
                    now_ms=round(self.clock() * 1000),
                )
                if ready is not None:
                    self._schedule_scene_start(ready.definition.id, active)
                elif estimate.state is PlaybackState.PLAYING:
                    self._maybe_plan_handoff(
                        active, estimate.position_ms, active.definition.duration_ms
                    )
            self._last_media_positions[active.playback_session_id] = estimate.position_ms

        if not render_pixels:
            return True
        rendered_at = self.clock()
        # Completion reports the full duration, which is past every cue and baked frame;
        # render the last valid millisecond so the end-of-scene tick does not go dark.
        last_ms = max(0, active.definition.duration_ms - 1)
        # A media clock starts late by its projector's installation delay; that delay only
        # compensates the projector's own latency, so LEDs must not inherit it.
        clock = active.clock_session
        clock_compensation_ms = clock.installation_delay_ms if clock is not None else 0
        render_position_ms = max(
            0,
            min(estimate.position_ms + clock_compensation_ms, last_ms) - self.lighting_delay_ms,
        )
        frames: list[tuple[str, int, tuple[tuple[int, int, int], ...]]] = []
        for output in active.scene.lighting_outputs:
            output_position_ms = min(
                last_ms,
                max(
                    0,
                    render_position_ms
                    - output.definition.timing_offset_ms
                    - self.output_delay_ms(output.definition.resource),
                ),
            )
            frames.append(
                (
                    output.definition.resource,
                    output_position_ms,
                    self._render_lighting_output(
                        output,
                        render_position_ms=output_position_ms,
                        rendered_at=rendered_at,
                        duration_ms=active.definition.duration_ms,
                        scene_id=active.definition.id,
                    ),
                )
            )
        # Ambient keeps running on LED outputs this scene does not light itself.
        lit = {output.definition.resource for output in active.scene.lighting_outputs}
        for resource, looks in self._all_lighting_looks().items():
            if resource not in lit:
                frames.append(
                    (
                        resource,
                        render_position_ms,
                        render_ambient_output(
                            looks,
                            self._ambient_elapsed_ms(rendered_at),
                            active.definition.id,
                        ),
                    )
                )
        for resource, output_position_ms, pixels in frames:
            try:
                self._send_output_frame(
                    RenderedFrame(
                        scene_instance_id=active.scene_instance_id,
                        position_ms=output_position_ms,
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
        scene_id: str | None = None,
    ) -> tuple[tuple[int, int, int], ...]:
        lighting = output.definition
        looks = self._lighting_looks_for(lighting.resource)
        if scene_id is None and self.active_scene is not None:
            scene_id = self.active_scene.definition.id
        return evaluate_lighting_output(
            output,
            position_ms=render_position_ms,
            duration_ms=duration_ms,
            base_frame=(
                render_ambient(looks, self._ambient_elapsed_ms(rendered_at))
                if looks is not None
                and (output.spatial_sequences or lighting.timeline)
                and scene_inherits_ambient(looks, scene_id)
                else None
            ),
            brightness_limit=min(
                lighting.brightness_limit,
                looks.definition.brightness_limit if looks is not None else 1,
            ),
        )

    async def stop(self, *, continue_queue: bool = True) -> None:
        self._scene_control_generation += 1
        if self.timing_calibration_active:
            await self.stop_timing_calibration(reason="stop")
        test = self.output_test_status()
        if test is not None and test.state == "active":
            await self.stop_output_test()
            if self.active_scene is None:
                return
        await self._stop_render_loop()
        active = self.active_scene
        self._handoff = None
        self._preselected_next = None
        if active is not None:
            await self._stop_media_sessions(active)
        self._degraded_reason = None
        self.active_scene = None
        self.follower.reset()
        self._controller_clock = None
        queued = None
        if continue_queue:
            queued = self.arbiter.pop_next(now_ms=round(self.clock() * 1000))
        if queued is not None:
            await self.start_scene(queued.definition.id)
        else:
            self._emit_safe_lighting()

    def _ambient_elapsed_ms(self, now: float) -> int:
        return round((now - self._lighting_started_at) * 1000)

    def _all_lighting_looks(self) -> dict[str, LoadedLightingLooks]:
        looks = dict(self.catalog.lighting_looks_by_resource)
        legacy = self.catalog.lighting_looks
        if legacy is not None:
            looks.setdefault(legacy.definition.resource, legacy)
        return looks

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
                self._send_output_frame(
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
            self._send_output_frame(
                RenderedFrame(
                    scene_instance_id="blackout",
                    position_ms=0,
                    pixels=((0, 0, 0),) * pixel_count,
                    resource=resource,
                )
            )
            self.rendered_frames += 1

    def _observe_output_frame(self, frame: RenderedFrame) -> None:
        if frame.resource is None:
            return
        self._last_output_frames.pop(frame.resource, None)
        self._last_output_frames[frame.resource] = (self.clock(), frame)
        if len(self._last_output_frames) > 32:
            del self._last_output_frames[next(iter(self._last_output_frames))]

    def _send_output_frame(self, frame: RenderedFrame) -> None:
        self.sink.send(frame)
        self._observe_output_frame(frame)

    def output_monitor(
        self,
        resource: str | None = None,
        pixel_offset: int = 0,
        pixel_limit: int = 1000,
    ) -> dict[str, object]:
        if pixel_offset < 0 or not 1 <= pixel_limit <= 1000:
            raise ValueError("Monitor offset must be nonnegative and limit between 1 and 1000")
        now = self.clock()
        if now - self._monitor_sample_at >= 0.2:
            self._monitor_sample_at = now
            self._monitor_sample = tuple(
                (key, observed, frame)
                for key, (observed, frame) in self._last_output_frames.items()
            )
        bindings = [
            item for item in self.catalog.installation.resources if item.kind == "pixel_output"
        ]
        if resource is not None and resource not in {item.resource for item in bindings}:
            raise ValueError("Monitor resource is not a loaded LED output")
        samples = {key: (observed, frame) for key, observed, frame in self._monitor_sample}
        budget = 4096
        outputs: list[dict[str, object]] = []
        selected = (
            [item for item in bindings if item.resource == resource] if resource else bindings[:32]
        )
        for binding in selected:
            value = samples.get(binding.resource)
            observed, frame = value if value else (None, None)
            count = len(frame.pixels) if frame else binding.pixel_count or 0
            end = min(count, pixel_offset + min(pixel_limit, budget))
            colors = frame.pixels[pixel_offset:end] if frame else ()
            budget -= len(colors)
            age = max(0, round((now - observed) * 1000)) if observed is not None else None
            outputs.append(
                {
                    "resource": binding.resource,
                    "output_id": binding.resource,
                    "scene_instance_id": frame.scene_instance_id if frame else None,
                    "source": (
                        "physical_test"
                        if frame and frame.scene_instance_id.startswith("output-test")
                        else "blackout"
                        if frame and frame.scene_instance_id == "blackout"
                        else "safe_state"
                        if frame and frame.scene_instance_id == "safe-state"
                        else "scene"
                        if frame
                        else "unobserved"
                    ),
                    "age_ms": age,
                    "stale": bool(frame and self.active_scene and age is not None and age > 1000),
                    "held": bool(frame and self.active_scene is None),
                    "error": self._lighting_output_errors.get(binding.resource),
                    "pixel_count": count,
                    "pixel_offset": pixel_offset,
                    "output_position_ms": frame.position_ms if frame else None,
                    "colors": [list(color) for color in colors],
                    "next_pixel_offset": end if end < count else None,
                    "limitations": [
                        "Last successful sink send, not proof of visible physical output."
                    ],
                }
            )
        return {
            "observed_at": datetime.now(UTC).isoformat(),
            "source": "successful_controller_sink_frames",
            "loaded_catalog": dict(self.loaded_catalog),
            "sample_interval_ms": 200,
            "maximum_resources": 32,
            "maximum_samples": 4096,
            "truncated_resources": max(0, len(bindings) - 32),
            "hardware_commands_sent": False,
            "outputs": outputs,
        }

    async def _stop_media_sessions(
        self,
        active: ActiveScene,
        *,
        keep_agents: frozenset[str] = frozenset(),
    ) -> None:
        for session in active.resolved_media_sessions:
            if session.agent_id in keep_agents:
                continue
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

    def validate_catalog_handover(self, catalog: SceneCatalog) -> None:
        if self._scene_starting or self._catalog_loading:
            raise RuntimeError("another scene/catalog change is in progress")
        test = self.output_test_status()
        if test is not None and test.state == "active":
            raise RuntimeError("finish the active physical output test before Apply")
        if self.timing_calibration_active:
            raise RuntimeError("finish timing calibration before Apply")
        current = {item.resource: item for item in self.catalog.installation.resources}
        used = {
            resource for scene in catalog.scenes.values() for resource in scene.definition.resources
        }
        look_resources = set(catalog.lighting_looks_by_resource)
        if catalog.lighting_looks is not None:
            look_resources.add(catalog.lighting_looks.definition.resource)
        used.update(look_resources)
        for binding in catalog.installation.resources:
            if binding.resource not in used:
                continue
            previous = current.get(binding.resource)
            if (
                previous is None
                or previous.kind != binding.kind
                or previous.target != binding.target
                or previous.pixel_count != binding.pixel_count
            ):
                raise RuntimeError(
                    f"resource {binding.resource} changes deployment routing; "
                    "configure the controller deployment before applying"
                )
        if isinstance(self.sink, FrameSinkRouter):
            self.sink.require_resources(
                {
                    output.definition.resource
                    for scene in catalog.scenes.values()
                    for output in scene.lighting_outputs
                }
                | look_resources
            )
        if self.active_scene is not None:
            missing = [
                session.agent_id
                for session in self.active_scene.resolved_media_sessions
                if session.agent_id not in self._agents
            ]
            if missing:
                raise RuntimeError("cannot acknowledge stop for disconnected playing devices")

    async def replace_catalog(
        self,
        catalog: SceneCatalog,
        *,
        identity: dict[str, object],
        start_scene_id: str | None = None,
        before_load: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        self.validate_catalog_handover(catalog)
        if start_scene_id is not None:
            catalog.require(start_scene_id)
        self._catalog_loading = True
        self._catalog_command_acks = set()
        self._catalog_commands_sent = set()
        was_background = self._background_enabled
        try:
            await self._stop_encounter_loop()
            for task in tuple(self._lifecycle_tasks):
                task.cancel()
            for task in tuple(self._lifecycle_tasks):
                with suppress(asyncio.CancelledError):
                    await task
            errors = self.command_errors
            await self.stop(continue_queue=False)
            handover_generation = self._scene_control_generation
            expected_stop_acks = {
                (item.command.playback_session_id, "stop")
                for item in self._pending_commands.values()
                if item.command.command == "stop"
            }
            deadline = asyncio.get_running_loop().time() + 8
            while not expected_stop_acks.issubset(self._catalog_command_acks):
                pending_stops = {
                    (item.command.playback_session_id, "stop")
                    for item in self._pending_commands.values()
                    if item.command.command == "stop"
                }
                if expected_stop_acks.difference(self._catalog_command_acks, pending_stops):
                    raise RuntimeError(
                        "stop acknowledgement failed or was abandoned; catalog unchanged"
                    )
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError(
                        "playing devices did not acknowledge stop; catalog unchanged"
                    )
                await asyncio.sleep(0.02)
            if self.command_errors != errors:
                raise RuntimeError("a playing device rejected stop; catalog unchanged")
            self._emit_blackout()
            if before_load is not None:
                await before_load()
            if handover_generation != self._scene_control_generation:
                raise RuntimeError("catalog load was cancelled by Stop")
            self.catalog = catalog
            self._last_output_frames.clear()
            self._monitor_sample = ()
            self._monitor_sample_at = float("-inf")
            self.audience = (
                AudiencePresenceEstimator(catalog.audience) if catalog.audience else None
            )
            self.director = (
                ShowDirector(
                    catalog.program,
                    clock=self.event_clock,
                    context_provider=self.audience.context if self.audience else None,
                )
                if catalog.program
                else None
            )
            self.opportunities = (
                OpportunityManager(
                    catalog.opportunities,
                    self.audience.context if self.audience else lambda: {},
                )
                if catalog.opportunities
                else None
            )
            self.scene_selector = (
                self._select_next_scene if self.director or self.opportunities else None
            )
            self.encounter = (
                EncounterEngine(
                    catalog.encounter,
                    self.request_scene,
                    self._stop_encounter_scene,
                )
                if catalog.encounter
                else None
            )
            self.arbiter = ResourceArbiter()
            self._handled_completion_sessions.clear()
            self._completion_history.clear()
            self._last_media_positions.clear()
            self._media_durations.clear()
            self._media_sessions_with_telemetry.clear()
            self._failed_prepare_sessions.clear()
            self._lighting_output_errors.clear()
            self._lighting_started_at = self.clock()
            self.loaded_catalog = dict(identity)
            if start_scene_id is not None:
                self._scene_starting = True
                try:
                    await self._start_scene(start_scene_id, handover_generation)
                finally:
                    self._scene_starting = False
                deadline = asyncio.get_running_loop().time() + 8
                while True:
                    if self.active_scene is None:
                        raise RuntimeError("show restart was cancelled by Stop")
                    required = {
                        session.playback_session_id
                        for session in self.active_scene.resolved_media_sessions
                        if session.required
                    }
                    if required.intersection(self._failed_prepare_sessions):
                        raise RuntimeError("a device rejected the new show")
                    pending = any(
                        item.command.playback_session_id in required
                        for item in self._pending_commands.values()
                    )
                    expected_start_acks = {
                        (session_id, command)
                        for session_id in required
                        for command in ("prepare", "play")
                    }
                    pending_pairs = {
                        (item.command.playback_session_id, item.command.command)
                        for item in self._pending_commands.values()
                    }
                    if expected_start_acks.intersection(self._catalog_commands_sent).difference(
                        self._catalog_command_acks,
                        pending_pairs,
                    ):
                        raise RuntimeError("a required start acknowledgement was lost or timed out")
                    if (
                        not pending
                        and expected_start_acks.issubset(self._catalog_command_acks)
                        and self.status().get("playback_state") in {"playing", "completed"}
                    ):
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise RuntimeError("new show playback was not acknowledged")
                    await asyncio.sleep(0.02)
        finally:
            self._catalog_loading = False
            self._catalog_command_acks = None
            self._catalog_commands_sent = None
            if was_background:
                self.start_background_tasks()

    async def wait_for_lifecycle_idle(self) -> None:
        while self._lifecycle_tasks:
            await asyncio.gather(*tuple(self._lifecycle_tasks), return_exceptions=True)
            # Let done-callbacks remove finished tasks; gathering already-finished tasks
            # does not yield to the event loop.
            await asyncio.sleep(0)

    def _schedule_completion(self, active: ActiveScene) -> None:
        if self._catalog_loading:
            return
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
        preselected = self._preselected_next
        if preselected is not None and preselected[0] == completed.scene_instance_id:
            selected = preselected[1]
            if selected is None:
                await self.stop()
            else:
                await self.start_scene(selected)
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
        if not self.automatic_rendering:
            return
        if self.render_loop_running:
            return
        self._render_task = asyncio.create_task(
            self._run_render_loop(scene_instance_id),
            name=f"render-{scene_instance_id}",
        )

    def _abandon_commands(self, agent_id: str) -> None:
        abandoned = [
            (request_id, pending)
            for request_id, pending in self._pending_commands.items()
            if pending.command.agent_id == agent_id
        ]
        for request_id, pending in abandoned:
            del self._pending_commands[request_id]
            self._recent_command_failures.appendleft(
                RuntimeCommandFailure(
                    agent_id=agent_id,
                    command=pending.command.command,
                    error_code="connection_lost",
                    detail="The command was abandoned when the device disconnected",
                    occurred_at=self.clock(),
                )
            )
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
        estimate = self._estimate_time()
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
            if self.active_scene is not None and self.active_scene.clock_session is not None
            else None
        )
        clock_position = (
            self._last_media_positions.get(clock_session_id)
            if clock_session_id in self._media_sessions_with_telemetry
            else None
        )
        if self._controller_clock is not None and estimate is not None:
            clock_position = estimate.position_ms
        active_media = (
            [
                {
                    "output_id": session.output_id,
                    "agent_id": session.agent_id,
                    "playback_session_id": session.playback_session_id,
                    "required": session.required,
                    "start_delay_ms": session.start_delay_ms,
                    "installation_delay_ms": session.installation_delay_ms,
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
                    "installation_offset_ms": self.output_delay_ms(output.definition.resource),
                    "error": self._lighting_output_errors.get(output.definition.resource),
                }
                for output in self.active_scene.scene.lighting_outputs
            ]
            if self.active_scene is not None
            else []
        )
        return {
            "controller_epoch": self.controller_epoch,
            "loaded_catalog": dict(self.loaded_catalog),
            "active_scene": self.active_scene.definition.id if self.active_scene else None,
            "next_scene": (
                {
                    "scene_id": self._handoff.scene_id,
                    "preloaded_outputs": sorted(
                        session.output_id for session in self._handoff.sessions.values()
                    ),
                    "automatic_cut": self._handoff.auto_agent_id is not None,
                }
                if self._handoff is not None
                and self.active_scene is not None
                and self._handoff.from_scene_instance_id == self.active_scene.scene_instance_id
                else None
            ),
            "scene_instance_id": self.active_scene.scene_instance_id if self.active_scene else None,
            "playback_session_id": self.active_scene.playback_session_id
            if self.active_scene
            else None,
            "estimated_position_ms": estimate.position_ms if estimate else None,
            "clock_authority": (
                "controller"
                if self._controller_clock is not None
                else "media"
                if self.active_scene is not None
                else None
            ),
            "playback_state": estimate.state if estimate else None,
            "degraded": degraded_reason is not None,
            "degraded_reason": degraded_reason,
            "connected_agents": sorted(self._agents),
            "media_outputs": active_media,
            "lighting_outputs": active_lighting,
            "output_test": (
                {
                    "id": self._output_test.status.id,
                    "resource": self._output_test.status.resource,
                    "state": self._output_test.status.state,
                    "error": self._output_test.status.error,
                }
                if self._output_test is not None
                else None
            ),
            "timing_calibration": (
                {
                    "id": calibration.id,
                    "state": calibration.state,
                    "error": calibration.error,
                    "resources": sorted(calibration.outputs),
                }
                if (calibration := self.timing_calibration_status()) is not None
                else None
            ),
            "output_timing": dict(sorted(self._output_timing.items())),
            "pending_commands": len(self._pending_commands),
            "command_errors": self.command_errors,
            "command_timeouts": self.command_timeouts,
            "abandoned_commands": self.abandoned_commands,
            "recent_command_failures": [
                {
                    "agent_id": failure.agent_id,
                    "command": failure.command,
                    "error_code": failure.error_code,
                    "detail": failure.detail,
                    "age_ms": max(0, round((self.clock() - failure.occurred_at) * 1000)),
                }
                for failure in self._recent_command_failures
            ],
            "heartbeat_timeouts": self.heartbeat_timeouts,
            "reconciliations": self.reconciliations,
            "scene_completions": self.scene_completions,
            "seamless_transitions": self.seamless_transitions,
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
            "event_loop_stalls": (
                self.loop_monitor.snapshot() if self.loop_monitor is not None else None
            ),
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
                "output_resources": [
                    {"resource": item.resource, "pixel_count": item.pixel_count}
                    for item in self.catalog.installation.resources
                    if item.kind == "pixel_output"
                ],
                "scene_options": [
                    {
                        "id": scene.definition.id,
                        "name": scene.definition.name,
                        "ready": all(
                            output.agent_id in self._agents
                            for output in scene.media_outputs
                            if output.definition.required
                        ),
                    }
                    for scene in self.catalog.scenes.values()
                ],
                "starting_scene_id": self.catalog.program.initial_scene_id
                if self.catalog.program
                else None,
            },
            "health": {
                "media_agent": ("connected" if self.connected_agent_count > 0 else "disconnected"),
                "lighting_output": type(self.sink).__name__,
                "projector": "not_independently_detectable",
                "audio": "not_independently_detectable",
            },
        }
