import asyncio
import colorsys
import logging
import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from cueverge.lighting.renderer import Rgb
from cueverge.lighting.sink import FrameSink, RenderedFrame

logger = logging.getLogger(__name__)


class OutputTestError(RuntimeError):
    pass


@dataclass(frozen=True)
class OutputTestSegment:
    name: str
    start: int
    end: int
    color: Rgb


def traversal_color(order: int) -> Rgb:
    # Match the browser's segment palette, including JavaScript's rounding.
    hue = math.floor((order * 137.508 + 190) % 360 + 0.5)
    red, green, blue = colorsys.hls_to_rgb(hue / 360, 0.65, 0.85)
    return (
        math.floor(red * 255 + 0.5),
        math.floor(green * 255 + 0.5),
        math.floor(blue * 255 + 0.5),
    )


def traversal_frame(
    pixel_count: int, position: float, segments: tuple[OutputTestSegment, ...]
) -> tuple[Rgb, ...]:
    index = math.floor(position)
    fraction = position - index
    pixels: list[Rgb] = [(0, 0, 0)] * pixel_count
    for output_index in range(max(0, index - 4), min(pixel_count, math.ceil(position) + 1)):
        color = next(
            (segment.color for segment in segments if segment.start <= output_index < segment.end),
            (255, 255, 255),
        )
        offset = index - output_index
        current = 1 - offset * 0.16 if 0 <= offset < 5 else 0
        following = 1 - (offset + 1) * 0.16 if 0 <= offset + 1 < 5 else 0
        factor = current + (following - current) * fraction
        pixels[output_index] = (
            round(color[0] * factor),
            round(color[1] * factor),
            round(color[2] * factor),
        )
    return tuple(pixels)


@dataclass
class OutputTestStatus:
    id: str
    arm_id: str
    resource: str
    pixel_count: int
    speed: int
    duration_seconds: float
    started_at: str
    expires_at: str
    segments: tuple[OutputTestSegment, ...]
    state: Literal["active", "stopped", "completed", "failed"] = "active"
    pixel_index: int = 0
    frames_sent: int = 0
    error: str | None = None
    stop_reason: str | None = None


class OutputTestRunner:
    def __init__(
        self,
        *,
        sink: FrameSink,
        resource: str,
        pixel_count: int,
        speed: int,
        duration_seconds: float,
        arm_id: str,
        segments: tuple[OutputTestSegment, ...],
        clock: Callable[[], float],
        fps: float,
        on_finish: Callable[[OutputTestStatus], None],
    ) -> None:
        self.sink = sink
        self.clock = clock
        self.fps = fps
        self.on_finish = on_finish
        self.started = clock()
        now = datetime.now(UTC)
        self.status = OutputTestStatus(
            id=str(uuid4()),
            arm_id=arm_id,
            resource=resource,
            pixel_count=pixel_count,
            speed=speed,
            duration_seconds=duration_seconds,
            started_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=duration_seconds)).isoformat(),
            segments=segments,
        )
        self.task: asyncio.Task[None] | None = None

    def start(self) -> OutputTestStatus:
        try:
            self._send(0)
        except (OSError, ValueError) as error:
            self._finish("failed", "send_failed", error)
            raise OutputTestError(self.status.error) from error
        self.task = asyncio.create_task(self._run(), name=f"output-test-{self.status.id}")
        return self.status

    def _send(self, elapsed: float) -> None:
        position = elapsed * self.status.speed % self.status.pixel_count
        self.sink.send(
            RenderedFrame(
                scene_instance_id=f"output-test:{self.status.id}",
                position_ms=round(elapsed * 1000),
                pixels=traversal_frame(self.status.pixel_count, position, self.status.segments),
                resource=self.status.resource,
            )
        )
        self.status.pixel_index = math.floor(position)
        self.status.frames_sent += 1

    async def _run(self) -> None:
        deadline = self.started
        interval = 1 / self.fps
        expires = self.started + self.status.duration_seconds
        try:
            while self.status.state == "active":
                deadline += interval
                await asyncio.sleep(max(0, min(deadline, expires) - self.clock()))
                elapsed = self.clock() - self.started
                if elapsed >= self.status.duration_seconds:
                    self._finish("completed", "timeout")
                    return
                self._send(elapsed)
                now = self.clock()
                if now >= deadline + interval:
                    # Skip missed frames without accumulating ordinary send/wakeup latency.
                    deadline += math.floor((now - deadline) / interval) * interval
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError) as error:
            self._finish("failed", "send_failed", error)

    def _finish(
        self,
        state: Literal["stopped", "completed", "failed"],
        reason: str,
        error: Exception | None = None,
    ) -> None:
        if self.status.state != "active":
            return
        self.status.state = state
        self.status.stop_reason = reason
        if error is not None:
            self.status.error = f"{type(error).__name__}: {error}"
        try:
            self.sink.send(
                RenderedFrame(
                    scene_instance_id=f"output-test-stop:{self.status.id}",
                    position_ms=0,
                    pixels=((0, 0, 0),) * self.status.pixel_count,
                    resource=self.status.resource,
                )
            )
            self.status.frames_sent += 1
        except (OSError, ValueError) as stop_error:
            self.status.state = "failed"
            self.status.error = (
                f"{self.status.error + '; ' if self.status.error else ''}"
                f"final black frame failed: {stop_error}"
            )
        if self.status.error is not None:
            logger.error("Physical output test %s failed: %s", self.status.id, self.status.error)
        self.on_finish(self.status)

    async def stop(self, reason: str = "operator") -> OutputTestStatus:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self._finish("stopped", reason)
        return self.status
