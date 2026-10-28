"""Live installation timing calibration: one flash-and-click beat on every selected output.

Projectors loop a reference clip whose first frames are white with a click, LED outputs flash
white on the same beat, and each output is shifted by its proposed delay. LED changes apply on
the next frame; projector changes restart the loop so every projector starts together again.
"""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from cueverge.lighting.renderer import Rgb
from cueverge.lighting.sink import FrameSink, RenderedFrame
from cueverge.protocol.models import AgentCommand, CommandAck, MediaCommandName

LOGGER = logging.getLogger(__name__)

CALIBRATION_SCENE_ID = "installation-timing-calibration"
DEFAULT_PERIOD_MS = 2000
DEFAULT_FLASH_MS = 100
START_LEAD_SECONDS = 0.75
COMMAND_TIMEOUT_SECONDS = 10.0
STOP_TIMEOUT_SECONDS = 2.0
RESYNC_DEBOUNCE_SECONDS = 0.3
FLASH_COLOR: Rgb = (255, 255, 255)


class TimingCalibrationRunError(RuntimeError):
    pass


class CalibrationAgentLink(Protocol):
    def connection(self, agent_id: str) -> tuple[str, Callable[[object], Awaitable[None]]] | None:
        """(agent session ID, send) for a connected agent, or None."""


@dataclass(frozen=True)
class CalibrationMediaTarget:
    resource: str
    agent_id: str
    asset_id: str


@dataclass(frozen=True)
class CalibrationLightTarget:
    resource: str
    pixel_count: int


@dataclass
class CalibrationOutputStatus:
    resource: str
    kind: Literal["media", "lighting"]
    delay_ms: int
    agent_id: str | None = None
    state: Literal["preparing", "waiting", "playing", "flashing", "error"] = "preparing"
    error: str | None = None
    drift_ms: int | None = None


@dataclass
class TimingCalibrationRunStatus:
    id: str
    period_ms: int
    flash_ms: int
    started_at: str
    outputs: dict[str, CalibrationOutputStatus]
    state: Literal["starting", "active", "stopped", "failed"] = "starting"
    cycles: int = 0
    error: str | None = None
    stop_reason: str | None = None
    playback_session_ids: dict[str, str] = field(default_factory=dict)


class TimingCalibrationRun:
    def __init__(
        self,
        *,
        sink: FrameSink,
        agents: CalibrationAgentLink,
        controller_epoch: str,
        media: tuple[CalibrationMediaTarget, ...],
        lights: tuple[CalibrationLightTarget, ...],
        delays: Mapping[str, int],
        clock: Callable[[], float],
        on_frame: Callable[[RenderedFrame], None] | None = None,
        on_finish: Callable[["TimingCalibrationRunStatus"], None] | None = None,
        period_ms: int = DEFAULT_PERIOD_MS,
        flash_ms: int = DEFAULT_FLASH_MS,
        fps: float = 60.0,
    ) -> None:
        if not media and not lights:
            raise TimingCalibrationRunError("choose at least one projector or LED output")
        if not 0 < flash_ms < period_ms:
            raise TimingCalibrationRunError("flash must be shorter than the beat")
        self.sink = sink
        self.agents = agents
        self.controller_epoch = controller_epoch
        self.media = media
        self.lights = lights
        self.clock = clock
        self.on_frame = on_frame
        self.on_finish = on_finish
        self.fps = fps
        outputs = {
            target.resource: CalibrationOutputStatus(
                resource=target.resource,
                kind="media",
                delay_ms=int(delays.get(target.resource, 0)),
                agent_id=target.agent_id,
            )
            for target in media
        }
        outputs.update(
            {
                target.resource: CalibrationOutputStatus(
                    resource=target.resource,
                    kind="lighting",
                    delay_ms=int(delays.get(target.resource, 0)),
                    state="flashing",
                )
                for target in lights
            }
        )
        self.status = TimingCalibrationRunStatus(
            id=str(uuid4()),
            period_ms=period_ms,
            flash_ms=flash_ms,
            started_at=datetime.now(UTC).isoformat(),
            outputs=outputs,
        )
        self._origin: float | None = None
        self._scene_instance_id = str(uuid4())
        self._pending: dict[str, asyncio.Future[CommandAck]] = {}
        self._render_task: asyncio.Task[None] | None = None
        self._cycle_task: asyncio.Task[None] | None = None
        self._resync_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # Lifecycle -----------------------------------------------------------------------------

    def start(self) -> TimingCalibrationRunStatus:
        self._loop = asyncio.get_running_loop()
        self._render_task = asyncio.create_task(
            self._render(), name=f"timing-calibration-leds-{self.status.id}"
        )
        self._cycle_task = asyncio.create_task(
            self._cycle(), name=f"timing-calibration-media-{self.status.id}"
        )
        return self.status

    @property
    def active(self) -> bool:
        return self.status.state in {"starting", "active"}

    async def stop(self, reason: str = "operator") -> TimingCalibrationRunStatus:
        if not self.active:
            return self.status
        self.status.state = "stopped"
        self.status.stop_reason = reason
        await self._cancel_tasks()
        await self._send_all("stop")
        self._black()
        if self.on_finish is not None:
            self.on_finish(self.status)
        return self.status

    async def fail(self, error: str) -> None:
        if not self.active:
            return
        self.status.state = "failed"
        self.status.error = error
        self.status.stop_reason = "failed"
        await self._cancel_tasks()
        with suppress(TimingCalibrationRunError):
            await self._send_all("stop")
        self._black()
        LOGGER.error("Timing calibration %s failed: %s", self.status.id, error)
        if self.on_finish is not None:
            self.on_finish(self.status)

    # Adjustments ---------------------------------------------------------------------------

    def set_delay(self, resource: str, delay_ms: int) -> TimingCalibrationRunStatus:
        output = self.status.outputs.get(resource)
        if output is None:
            raise TimingCalibrationRunError(f"{resource} is not part of this calibration")
        if not self.active:
            raise TimingCalibrationRunError("the calibration run has ended")
        output.delay_ms = int(delay_ms)
        if output.kind == "media":
            self.resync(debounce=True)
        return self.status

    def resync(self, *, debounce: bool = False) -> None:
        if not self.media or not self.active or self._loop is None:
            return
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if not on_loop:
            # Requests handled on worker threads must not touch the run's loop directly.
            self._loop.call_soon_threadsafe(lambda: self.resync(debounce=debounce))
            return
        if self._resync_task is not None and not self._resync_task.done():
            self._resync_task.cancel()
        self._resync_task = asyncio.create_task(
            self._resync(RESYNC_DEBOUNCE_SECONDS if debounce else 0),
            name=f"timing-calibration-resync-{self.status.id}",
        )

    def delays(self) -> dict[str, int]:
        return {resource: output.delay_ms for resource, output in self.status.outputs.items()}

    # Agent messages --------------------------------------------------------------------------

    def acknowledge(self, acknowledgment: CommandAck) -> bool:
        future = self._pending.pop(acknowledgment.request_id, None)
        if future is None:
            return False
        if not future.done():
            future.set_result(acknowledgment)
        return True

    def owns_playback(self, playback_session_id: str) -> bool:
        return playback_session_id in self.status.playback_session_ids.values()

    def observe_position(self, agent_id: str, position_ms: int) -> None:
        """Track how far a projector has drifted from the beat it should be on."""
        origin = self._origin
        target = next((item for item in self.media if item.agent_id == agent_id), None)
        if origin is None or target is None:
            return
        output = self.status.outputs[target.resource]
        period = self.status.period_ms
        expected = (self.clock() - origin) * 1000 - output.delay_ms
        if expected < 0:
            return
        difference = (position_ms - expected) % period
        output.drift_ms = round(difference - period if difference > period / 2 else difference)

    # Internals -----------------------------------------------------------------------------

    async def _resync(self, wait_seconds: float) -> None:
        if wait_seconds:
            await asyncio.sleep(wait_seconds)
        if self._cycle_task is not None and not self._cycle_task.done():
            self._cycle_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cycle_task
        self._cycle_task = asyncio.create_task(
            self._cycle(), name=f"timing-calibration-media-{self.status.id}"
        )

    async def _cycle(self) -> None:
        """Load the clip everywhere, then start each projector at the beat origin + delay."""
        try:
            self._origin = None
            for output in self.status.outputs.values():
                if output.kind == "media":
                    output.state = "preparing"
                    output.error = None
                    output.drift_ms = None
            self.status.playback_session_ids = {
                target.agent_id: str(uuid4()) for target in self.media
            }
            await asyncio.gather(*(self._prepare(target) for target in self.media))
            origin = self.clock() + START_LEAD_SECONDS
            self._origin = origin
            self.status.cycles += 1
            self.status.state = "active"
            await asyncio.gather(*(self._play_at(target, origin) for target in self.media))
        except asyncio.CancelledError:
            raise
        except TimingCalibrationRunError as error:
            await self.fail(str(error))

    async def _prepare(self, target: CalibrationMediaTarget) -> None:
        ack = await self._command(target, "prepare")
        if ack.status != "completed":
            output = self.status.outputs[target.resource]
            output.state = "error"
            output.error = ack.detail or ack.error_code or "prepare failed"
            raise TimingCalibrationRunError(
                f"{target.resource} could not load the clip: {output.error}"
            )
        self.status.outputs[target.resource].state = "waiting"

    async def _play_at(self, target: CalibrationMediaTarget, origin: float) -> None:
        output = self.status.outputs[target.resource]
        await asyncio.sleep(max(0.0, origin + output.delay_ms / 1000 - self.clock()))
        ack = await self._command(target, "play")
        if ack.status != "completed":
            output.state = "error"
            output.error = ack.detail or ack.error_code or "play failed"
            raise TimingCalibrationRunError(f"{target.resource} could not play: {output.error}")
        output.state = "playing"

    async def _command(
        self,
        target: CalibrationMediaTarget,
        name: MediaCommandName,
        timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
    ) -> CommandAck:
        link = self.agents.connection(target.agent_id)
        if link is None:
            raise TimingCalibrationRunError(f"{target.resource}'s client is not connected")
        session_id, send = link
        loads = name == "prepare"
        command = AgentCommand(
            request_id=str(uuid4()),
            command=name,
            agent_id=target.agent_id,
            agent_session_id=session_id,
            controller_epoch=self.controller_epoch,
            scene_id=CALIBRATION_SCENE_ID,
            scene_instance_id=self._scene_instance_id,
            playback_session_id=self.status.playback_session_ids.get(target.agent_id, str(uuid4())),
            asset_id=target.asset_id if loads else None,
            end_behavior="loop" if loads else None,
        )
        future: asyncio.Future[CommandAck] = asyncio.get_running_loop().create_future()
        self._pending[command.request_id] = future
        try:
            await send(command.model_dump(mode="json"))
            return await asyncio.wait_for(future, timeout_seconds)
        except TimeoutError as error:
            raise TimingCalibrationRunError(
                f"{target.resource}'s client did not answer {name} in time"
            ) from error
        finally:
            self._pending.pop(command.request_id, None)

    async def _send_all(self, name: MediaCommandName) -> None:
        async def send(target: CalibrationMediaTarget) -> None:
            with suppress(TimingCalibrationRunError):
                await self._command(target, name, STOP_TIMEOUT_SECONDS)

        await asyncio.gather(*(send(target) for target in self.media))

    async def _render(self) -> None:
        interval = 1 / self.fps
        deadline = self.clock()
        start = self.clock()
        try:
            while self.active:
                self._frame(self.clock(), start)
                deadline += interval
                now = self.clock()
                if now >= deadline + interval:
                    deadline += math.floor((now - deadline) / interval) * interval
                await asyncio.sleep(max(0.0, deadline - now))
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError) as error:
            await self.fail(f"LED output failed: {type(error).__name__}: {error}")

    def _frame(self, now: float, start: float) -> None:
        # Without projectors the LEDs keep their own beat from when the run started.
        origin = self._origin if self.media else start
        for target in self.lights:
            delay = self.status.outputs[target.resource].delay_ms
            lit = False
            if origin is not None:
                position = (now - origin) * 1000 - delay
                lit = position >= 0 and position % self.status.period_ms < self.status.flash_ms
            self._send(target, FLASH_COLOR if lit else (0, 0, 0))

    def _send(self, target: CalibrationLightTarget, color: Rgb) -> None:
        frame = RenderedFrame(
            scene_instance_id=f"timing-calibration:{self.status.id}",
            position_ms=0,
            pixels=(color,) * target.pixel_count,
            resource=target.resource,
        )
        self.sink.send(frame)
        if self.on_frame is not None:
            self.on_frame(frame)

    def _black(self) -> None:
        for target in self.lights:
            with suppress(OSError, ValueError):
                self._send(target, (0, 0, 0))

    async def _cancel_tasks(self) -> None:
        current = asyncio.current_task()
        for task in (self._resync_task, self._cycle_task, self._render_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
