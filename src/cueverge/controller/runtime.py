import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from cueverge.core.models import SYNTHETIC_SWEEP, SceneDefinition
from cueverge.lighting.renderer import SyntheticSweep
from cueverge.lighting.sink import MemoryFrameSink, RenderedFrame
from cueverge.protocol.models import AgentCommand, MediaPosition
from cueverge.timing.follower import TelemetryDecision, TimecodeFollower

Clock = Callable[[], float]


class CommandChannel(Protocol):
    async def send_json(self, data: Any) -> None: ...


@dataclass(frozen=True)
class ActiveScene:
    definition: SceneDefinition
    scene_instance_id: str
    playback_session_id: str


class ControllerRuntime:
    def __init__(
        self,
        *,
        clock: Clock = time.monotonic,
        sink: MemoryFrameSink | None = None,
        stale_after_ms: int = 500,
        render_fps: float = 25.0,
    ) -> None:
        if render_fps <= 0:
            raise ValueError("render_fps must be positive")
        self.clock = clock
        self.sink = sink or MemoryFrameSink()
        self.follower = TimecodeFollower(stale_after_ms=stale_after_ms)
        self.render_fps = render_fps
        self.active_scene: ActiveScene | None = None
        self._agents: dict[str, CommandChannel] = {}
        self._render_task: asyncio.Task[None] | None = None
        self.rejected_telemetry = 0
        self.render_ticks = 0
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

    def register_agent(self, agent_id: str, websocket: CommandChannel) -> None:
        self._agents[agent_id] = websocket

    def disconnect_agent(self, agent_id: str, websocket: CommandChannel) -> None:
        if self._agents.get(agent_id) is websocket:
            del self._agents[agent_id]
            if self.active_scene is not None and self.active_scene.definition.agent_id == agent_id:
                self.follower.disconnect()
                self._cancel_render_loop()

    async def start_scene(self, scene_id: str) -> ActiveScene:
        if scene_id != SYNTHETIC_SWEEP.id:
            raise LookupError(f"unknown scene: {scene_id}")
        scene = SYNTHETIC_SWEEP
        websocket = self._agents.get(scene.agent_id)
        if websocket is None:
            raise RuntimeError(f"required agent is not connected: {scene.agent_id}")

        await self._stop_render_loop()
        active = ActiveScene(
            definition=scene,
            scene_instance_id=str(uuid4()),
            playback_session_id=str(uuid4()),
        )
        self.active_scene = active
        self.follower.begin(active.scene_instance_id, active.playback_session_id)
        for command_name in ("prepare", "play"):
            command = AgentCommand(
                request_id=str(uuid4()),
                command=command_name,
                agent_id=scene.agent_id,
                scene_id=scene.id,
                scene_instance_id=active.scene_instance_id,
                playback_session_id=active.playback_session_id,
            )
            await websocket.send_json(command.model_dump(mode="json"))
        self._render_task = asyncio.create_task(
            self._run_render_loop(active.scene_instance_id),
            name=f"render-{active.scene_instance_id}",
        )
        return active

    def ingest(self, telemetry: MediaPosition) -> TelemetryDecision:
        active = self.active_scene
        if active is None:
            self.rejected_telemetry += 1
            return TelemetryDecision(False, "no_active_scene")
        if telemetry.agent_id != active.definition.agent_id:
            self.rejected_telemetry += 1
            return TelemetryDecision(False, "wrong_agent")

        decision = self.follower.ingest(telemetry, self.clock())
        if not decision.accepted:
            self.rejected_telemetry += 1
        return decision

    def render_frame(self) -> bool:
        self.render_ticks += 1
        active = self.active_scene
        estimate = self.follower.estimate(self.clock())
        if active is None or estimate is None or estimate.reason == "agent_disconnected":
            self.render_ticks_without_timecode += 1
            return False

        rendered_at = self.clock()
        renderer = SyntheticSweep(
            pixel_count=active.definition.pixel_count,
            cycle_ms=active.definition.duration_ms,
        )
        self.sink.send(
            RenderedFrame(
                scene_instance_id=active.scene_instance_id,
                position_ms=estimate.position_ms,
                pixels=renderer.render(estimate.position_ms),
            )
        )
        if estimate.degraded:
            self.stale_frames += 1
        if self._last_frame_at is not None:
            self._render_interval_total_ms += (rendered_at - self._last_frame_at) * 1000
            self._render_interval_count += 1
        self._last_frame_at = rendered_at
        return True

    async def stop(self) -> None:
        await self._stop_render_loop()
        self.active_scene = None

    async def shutdown(self) -> None:
        await self._stop_render_loop()

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

    def status(self) -> dict[str, object]:
        estimate = self.follower.estimate(self.clock())
        mean_interval_ms = (
            self._render_interval_total_ms / self._render_interval_count
            if self._render_interval_count
            else None
        )
        return {
            "active_scene": self.active_scene.definition.id if self.active_scene else None,
            "scene_instance_id": self.active_scene.scene_instance_id if self.active_scene else None,
            "playback_session_id": self.active_scene.playback_session_id
            if self.active_scene
            else None,
            "estimated_position_ms": estimate.position_ms if estimate else None,
            "playback_state": estimate.state if estimate else None,
            "degraded": estimate.degraded if estimate else self.active_scene is not None,
            "degraded_reason": estimate.reason
            if estimate
            else "awaiting_telemetry"
            if self.active_scene
            else None,
            "connected_agents": sorted(self._agents),
            "rendered_frames": len(self.sink.frames),
            "rejected_telemetry": self.rejected_telemetry,
            "render_target_fps": self.render_fps,
            "render_loop_running": self.render_loop_running,
            "render_ticks": self.render_ticks,
            "render_ticks_without_timecode": self.render_ticks_without_timecode,
            "stale_frames": self.stale_frames,
            "skipped_render_deadlines": self.skipped_render_deadlines,
            "mean_render_interval_ms": mean_interval_ms,
        }
