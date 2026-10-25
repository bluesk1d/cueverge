"""Detect event-loop stalls and record what was running while the loop was blocked.

Everything the controller renders shares one asyncio event loop with the web API. A
synchronous call that holds the loop (or another thread that holds the GIL) delays
LED frames. A watchdog thread notices a late heartbeat and samples the loop thread's
stack, which names the blocking code without any per-request instrumentation.
"""

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import FrameType
from typing import Any

LOGGER = logging.getLogger(__name__)
_PACKAGE_MARKER = f"{os.sep}cueverge{os.sep}"
_STACK_LIMIT = 12
_HISTORY = 20
_WAIT_MODULES = {"threading.py", "queue.py", "selectors.py", "thread.py"}


@dataclass
class _Capture:
    started_at: float
    loop_stack: list[str]
    other_threads: list[str]
    requests: list[str]


@dataclass
class LoopStall:
    observed_at: str
    duration_ms: int
    blocked_in: str
    loop_stack: list[str]
    other_threads: list[str]
    requests: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "observed_at": self.observed_at,
            "duration_ms": self.duration_ms,
            "blocked_in": self.blocked_in,
            "loop_stack": self.loop_stack,
            "other_threads": self.other_threads,
            "requests": self.requests,
        }


@dataclass
class LoopStallMonitor:
    threshold_ms: float = 50.0
    heartbeat_ms: float = 10.0
    clock: object = field(default=time.monotonic, repr=False)
    stalls: deque[LoopStall] = field(default_factory=lambda: deque(maxlen=_HISTORY))
    stall_count: int = 0
    longest_ms: int = 0
    _beat: float = 0.0
    _loop_thread_id: int | None = None
    _capture: _Capture | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _task: asyncio.Task[None] | None = None
    _requests: dict[int, str] = field(default_factory=dict)

    @classmethod
    def from_environment(cls) -> "LoopStallMonitor | None":
        raw = os.environ.get("CUEVERGE_LOOP_STALL_MS", "50")
        try:
            threshold = float(raw)
        except ValueError:
            threshold = 50.0
        return None if threshold <= 0 else cls(threshold_ms=threshold)

    def _now(self) -> float:
        return self.clock()  # type: ignore[operator, no-any-return]

    async def start(self) -> None:
        self._loop_thread_id = threading.get_ident()
        self._beat = self._now()
        self._stop.clear()
        self._task = asyncio.create_task(self._heartbeat(), name="loop-stall-heartbeat")
        self._thread = threading.Thread(target=self._watch, name="loop-stall-watchdog", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 1)

    def request_started(self, key: int, description: str) -> None:
        self._requests[key] = description

    def request_finished(self, key: int) -> None:
        self._requests.pop(key, None)

    async def _heartbeat(self) -> None:
        interval = self.heartbeat_ms / 1000
        while True:
            self.beat()
            await asyncio.sleep(interval)

    def beat(self) -> None:
        now = self._now()
        with self._lock:
            capture = self._capture
            self._capture = None
            self._beat = now
        if capture is not None:
            self._record(capture, now)

    def _watch(self) -> None:
        interval = self.heartbeat_ms / 1000
        threshold = self.threshold_ms / 1000
        while not self._stop.wait(interval):
            self.check(threshold)

    def check(self, threshold: float | None = None) -> None:
        """Sample the loop thread once if its heartbeat is late (called by the watchdog)."""
        limit = self.threshold_ms / 1000 if threshold is None else threshold
        with self._lock:
            if self._capture is not None or self._now() - self._beat < limit:
                return
            started_at = self._beat
        frames = sys._current_frames()
        loop_frame = frames.get(self._loop_thread_id) if self._loop_thread_id else None
        others = [
            summary
            for ident, frame in frames.items()
            if ident not in {self._loop_thread_id, threading.get_ident()}
            and (summary := _busy_package_frame(frame)) is not None
        ]
        capture = _Capture(
            started_at=started_at,
            loop_stack=_format_stack(loop_frame) if loop_frame is not None else [],
            other_threads=others,
            requests=list(self._requests.values()),
        )
        with self._lock:
            if self._capture is None and self._beat == started_at:
                self._capture = capture

    def _record(self, capture: _Capture, now: float) -> None:
        duration_ms = round((now - capture.started_at) * 1000)
        package_frames = [line for line in capture.loop_stack if _PACKAGE_MARKER in line]
        blocked_in = (
            package_frames[-1]
            if package_frames
            else capture.loop_stack[-1]
            if capture.loop_stack
            else "unknown"
        )
        stall = LoopStall(
            observed_at=datetime.now(UTC).isoformat(),
            duration_ms=duration_ms,
            blocked_in=blocked_in,
            loop_stack=capture.loop_stack,
            other_threads=capture.other_threads,
            requests=capture.requests,
        )
        self.stalls.append(stall)
        self.stall_count += 1
        self.longest_ms = max(self.longest_ms, duration_ms)
        LOGGER.warning(
            "event loop stalled %d ms in %s (requests in flight: %s; busy threads: %s)",
            duration_ms,
            blocked_in,
            "; ".join(capture.requests) or "none",
            "; ".join(capture.other_threads) or "none",
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "threshold_ms": self.threshold_ms,
            "stall_count": self.stall_count,
            "longest_ms": self.longest_ms,
            "recent": [stall.as_dict() for stall in reversed(self.stalls)],
        }


def _format_stack(frame: FrameType) -> list[str]:
    entries = traceback.extract_stack(frame)[-_STACK_LIMIT:]
    return [f"{entry.filename}:{entry.lineno} in {entry.name}" for entry in entries]


def _busy_package_frame(frame: FrameType) -> str | None:
    """The innermost cueverge frame of a thread that is running package code.

    Threads parked in a blocking wait (idle workers) are not busy and are skipped.
    """
    entries = traceback.extract_stack(frame)
    if entries and os.path.basename(entries[-1].filename) in _WAIT_MODULES:
        return None
    for entry in reversed(entries):
        if _PACKAGE_MARKER in entry.filename and "loop_monitor" not in entry.filename:
            return f"{entry.filename}:{entry.lineno} in {entry.name}"
    return None


class LoopStallRequestMiddleware:
    """ASGI middleware that lists in-flight HTTP requests for stall reports."""

    def __init__(self, app: Any, monitor: LoopStallMonitor) -> None:
        self.app = app
        self.monitor = monitor

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        key = id(scope)
        self.monitor.request_started(key, f"{scope.get('method')} {scope.get('path')}")
        try:
            await self.app(scope, receive, send)
        finally:
            self.monitor.request_finished(key)
