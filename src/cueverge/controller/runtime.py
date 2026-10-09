import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from cueverge.core.arbitration import ArbitrationDecision, ResourceArbiter
from cueverge.core.encounter import EncounterEngine, EventOutcome
from cueverge.core.events import ExternalEvent
from cueverge.core.loader import load_show_package
from cueverge.core.models import LoadedScene, SceneCatalog, SceneDefinition
from cueverge.lighting.renderer import SegmentPulse, SyntheticSweep
from cueverge.lighting.sink import FrameSink, MemoryFrameSink, RenderedFrame
from cueverge.protocol.models import (
    AgentCommand,
    AgentHeartbeat,
    AgentHello,
    AgentWelcome,
    CommandAck,
    MediaPosition,
)
from cueverge.timing.follower import TelemetryDecision, TimecodeFollower

Clock = Callable[[], float]
EventClock = Callable[[], int]


class CommandChannel(Protocol):
    async def send_json(self, data: Any) -> None: ...


@dataclass(frozen=True)
class ActiveScene:
    scene: LoadedScene
    scene_instance_id: str
    playback_session_id: str

    @property
    def definition(self) -> SceneDefinition:
        return self.scene.definition


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
        self.sink = sink or MemoryFrameSink()
        self.follower = TimecodeFollower(stale_after_ms=stale_after_ms)
        self.render_fps = render_fps
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.heartbeat_timeout_ms = heartbeat_timeout_ms
        self.command_timeout_ms = command_timeout_ms
        self.lighting_delay_ms = lighting_delay_ms
        self.controller_epoch = controller_epoch or str(uuid4())
        self.catalog = catalog or load_show_package(Path("shows/demo"))
        self.event_clock = event_clock or (lambda: round(time.time() * 1000))
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
        self._render_task: asyncio.Task[None] | None = None
        self._encounter_task: asyncio.Task[None] | None = None
        self._degraded_reason: str | None = None
        self.rejected_telemetry = 0
        self.rejected_protocol_messages = 0
        self.command_errors = 0
        self.command_timeouts = 0
        self.abandoned_commands = 0
        self.heartbeat_timeouts = 0
        self.reconciliations = 0
        self.render_ticks = 0
        self.rendered_frames = 0
        self.render_ticks_without_timecode = 0
        self.stale_frames = 0
        self.skipped_render_deadlines = 0
        self._last_frame_at: float | None = None
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
        if self.encounter is not None and not self.encounter_loop_running:
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
        if active is not None:
            matches = (
                playback is not None
                and playback.controller_epoch == self.controller_epoch
                and playback.scene_instance_id == active.scene_instance_id
                and playback.playback_session_id == active.playback_session_id
            )
            if matches and playback is not None:
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
                if decision.accepted:
                    reconciliation = "continue"
                    self.reconciliations += 1
                    self._degraded_reason = None
                    self._ensure_render_loop(active.scene_instance_id)
                else:
                    reconciliation = "stop"
                    self._degraded_reason = "reconciliation_required"
            else:
                reconciliation = "stop"
                self._degraded_reason = "reconciliation_required"
                self._cancel_render_loop()
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
            if self.active_scene is not None and self.active_scene.scene.agent_id == agent_id:
                self.follower.disconnect()
                self._degraded_reason = "agent_disconnected"
                self._cancel_render_loop()

    async def start_scene(self, scene_id: str) -> ActiveScene:
        scene = self.catalog.require(scene_id)
        connection = self._agents.get(scene.agent_id)
        if connection is None:
            raise RuntimeError(f"required agent is not connected: {scene.agent_id}")

        await self._stop_render_loop()
        self._abandon_commands(scene.agent_id)
        self._degraded_reason = None
        active = ActiveScene(
            scene=scene,
            scene_instance_id=str(uuid4()),
            playback_session_id=str(uuid4()),
        )
        self.active_scene = active
        self.follower.begin(active.scene_instance_id, active.playback_session_id)
        await self._send_command(active, "prepare", connection)
        self._ensure_render_loop(active.scene_instance_id)
        return active

    async def request_scene(self, scene_id: str) -> str:
        incoming = self.catalog.require(scene_id)
        active = self.active_scene.scene if self.active_scene is not None else None
        decision = self.arbiter.evaluate(active, incoming)
        if decision is ArbitrationDecision.START:
            await self.start_scene(scene_id)
        return decision.value

    async def pause(self) -> None:
        active, connection = self._active_media_connection()
        await self._send_command(active, "pause", connection)

    async def resume(self) -> None:
        active, connection = self._active_media_connection()
        await self._send_command(active, "resume", connection)

    async def seek(self, position_ms: int) -> None:
        active, connection = self._active_media_connection()
        await self._send_command(
            active,
            "seek",
            connection,
            target_position_ms=position_ms,
        )

    async def ingest_event(self, event: ExternalEvent) -> EventOutcome:
        if self.encounter is None:
            raise RuntimeError("show package does not define an encounter")
        now_ms = self.event_clock()
        await self.encounter.tick(now_ms)
        return await self.encounter.ingest(event, now_ms)

    async def tick_encounter(self) -> None:
        if self.encounter is not None:
            await self.encounter.tick(self.event_clock())

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
        if telemetry.agent_id != active.scene.agent_id:
            self.rejected_telemetry += 1
            return TelemetryDecision(False, "wrong_agent")
        connection = self._agents.get(telemetry.agent_id)
        if connection is None or telemetry.agent_session_id != connection.session_id:
            self.rejected_telemetry += 1
            return TelemetryDecision(False, "agent_session_fence")
        if telemetry.controller_epoch != self.controller_epoch:
            self.rejected_telemetry += 1
            return TelemetryDecision(False, "controller_epoch_fence")

        decision = self.follower.ingest(telemetry, self.clock())
        if not decision.accepted:
            self.rejected_telemetry += 1
        else:
            connection.last_seen_at = self.clock()
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
        if acknowledgment.status == "error":
            self.command_errors += 1
            self._degraded_reason = f"command_error:{acknowledgment.error_code or 'unknown'}"
            self._cancel_render_loop()
        elif pending.command.command == "prepare" and self.active_scene is not None:
            await self._send_command(self.active_scene, "play", connection)
        return TelemetryDecision(True)

    async def _send_command(
        self,
        active: ActiveScene,
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
            agent_id=active.scene.agent_id,
            agent_session_id=connection.session_id,
            controller_epoch=self.controller_epoch,
            scene_id=active.definition.id,
            scene_instance_id=active.scene_instance_id,
            playback_session_id=active.playback_session_id,
            asset_id=active.scene.asset_id,
            target_position_ms=target_position_ms,
        )
        self._pending_commands[command.request_id] = PendingCommand(command, self.clock())
        await connection.channel.send_json(command.model_dump(mode="json"))

    def _active_media_connection(self) -> tuple[ActiveScene, AgentConnection]:
        active = self.active_scene
        if active is None:
            raise RuntimeError("no active scene")
        connection = self._agents.get(active.scene.agent_id)
        if connection is None:
            raise RuntimeError(f"required agent is not connected: {active.scene.agent_id}")
        return active, connection

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
            if self.active_scene is not None and self.active_scene.scene.agent_id == agent_id:
                self.follower.disconnect()
                self._degraded_reason = "heartbeat_timeout"
                self._cancel_render_loop()

        command_limit = self.command_timeout_ms / 1000
        expired_commands = [
            request_id
            for request_id, pending in self._pending_commands.items()
            if now - pending.sent_at > command_limit
        ]
        if expired_commands:
            for request_id in expired_commands:
                del self._pending_commands[request_id]
            self.command_timeouts += len(expired_commands)
            self._degraded_reason = "command_timeout"
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
        lighting = active.definition.lighting
        if lighting.effect == "segment_pulse":
            segment = active.scene.lighting_segment
            if segment is None:
                raise RuntimeError("segment_pulse scene has no bound pixel segment")
            pixels = SegmentPulse(
                pixel_count=active.scene.pixel_count,
                segment_start=segment.start,
                segment_end=segment.end,
                pulse_times_ms=lighting.pulse_times_ms,
                pulse_duration_ms=lighting.pulse_duration_ms,
                color=active.scene.color,
            ).render(render_position_ms)
        else:
            pixels = SyntheticSweep(
                pixel_count=active.scene.pixel_count,
                cycle_ms=active.definition.duration_ms,
                color=active.scene.color,
            ).render(render_position_ms)
        self.sink.send(
            RenderedFrame(
                scene_instance_id=active.scene_instance_id,
                position_ms=render_position_ms,
                pixels=pixels,
            )
        )
        self.rendered_frames += 1
        if estimate.degraded:
            self.stale_frames += 1
        if self._last_frame_at is not None:
            self._render_interval_total_ms += (rendered_at - self._last_frame_at) * 1000
            self._render_interval_count += 1
        self._last_frame_at = rendered_at
        return True

    async def stop(self, *, continue_queue: bool = True) -> None:
        await self._stop_render_loop()
        active = self.active_scene
        if active is not None:
            connection = self._agents.get(active.scene.agent_id)
            self._abandon_commands(active.scene.agent_id)
            if connection is not None:
                await self._send_command(active, "stop", connection)
        self._degraded_reason = None
        self.active_scene = None
        self.follower.reset()
        if continue_queue:
            queued = self.arbiter.pop_next()
            if queued is not None:
                await self.start_scene(queued.definition.id)

    async def shutdown(self) -> None:
        await self._stop_render_loop()
        await self._stop_encounter_loop()
        self.sink.close()

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
            while self.encounter is not None:
                await self.encounter.tick(self.event_clock())
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
        degraded_reason = self._degraded_reason or estimate_reason
        if degraded_reason is None and estimate is None and self.active_scene:
            degraded_reason = "awaiting_telemetry"
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
            "pending_commands": len(self._pending_commands),
            "command_errors": self.command_errors,
            "command_timeouts": self.command_timeouts,
            "abandoned_commands": self.abandoned_commands,
            "heartbeat_timeouts": self.heartbeat_timeouts,
            "reconciliations": self.reconciliations,
            "rejected_protocol_messages": self.rejected_protocol_messages,
            "rendered_frames": self.rendered_frames,
            "rejected_telemetry": self.rejected_telemetry,
            "render_target_fps": self.render_fps,
            "render_loop_running": self.render_loop_running,
            "render_ticks": self.render_ticks,
            "render_ticks_without_timecode": self.render_ticks_without_timecode,
            "stale_frames": self.stale_frames,
            "skipped_render_deadlines": self.skipped_render_deadlines,
            "mean_render_interval_ms": mean_interval_ms,
            "encounter": self.encounter.status() if self.encounter is not None else None,
            "encounter_loop_running": self.encounter_loop_running,
            "queued_scenes": [scene.definition.id for scene in self.arbiter.queued],
        }
