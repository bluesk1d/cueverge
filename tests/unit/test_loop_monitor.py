import asyncio
import threading
import time

from cueverge.controller.loop_monitor import LoopStallMonitor, LoopStallRequestMiddleware
from cueverge.controller.runtime import ControllerRuntime


def _block_the_loop_for_test() -> None:
    time.sleep(0.15)


def test_blocking_call_on_the_loop_is_recorded_with_its_stack() -> None:
    async def scenario() -> dict[str, object]:
        monitor = LoopStallMonitor(threshold_ms=40)
        await monitor.start()
        try:
            await asyncio.sleep(0.05)
            monitor.request_started(1, "GET /api/gui/v1/slow")
            _block_the_loop_for_test()
            monitor.request_finished(1)
            await asyncio.sleep(0.05)
            return monitor.snapshot()
        finally:
            await monitor.stop()

    snapshot = asyncio.run(scenario())
    assert snapshot["stall_count"] == 1
    assert 100 <= snapshot["longest_ms"] < 1000  # type: ignore[operator]
    stall = snapshot["recent"][0]  # type: ignore[index]
    assert "_block_the_loop_for_test" in stall["blocked_in"]
    assert stall["requests"] == ["GET /api/gui/v1/slow"]


def test_idle_worker_threads_are_not_reported_as_busy() -> None:
    from cueverge.controller.loop_monitor import _busy_package_frame

    waiting = threading.Event()
    seen: list[object] = []

    def idle() -> None:
        waiting.wait(1)

    worker = threading.Thread(target=idle)
    worker.start()
    time.sleep(0.05)
    import sys

    seen.append(_busy_package_frame(sys._current_frames()[worker.ident]))  # type: ignore[index]
    waiting.set()
    worker.join()
    assert seen == [None]


def test_runtime_status_reports_stalls_and_middleware_tracks_requests() -> None:
    monitor = LoopStallMonitor(threshold_ms=50)
    runtime = ControllerRuntime()
    runtime.loop_monitor = monitor
    assert runtime.status()["event_loop_stalls"] == {
        "threshold_ms": 50,
        "stall_count": 0,
        "longest_ms": 0,
        "recent": [],
    }
    seen: list[dict[int, str]] = []

    async def app(scope: object, receive: object, send: object) -> None:
        seen.append(dict(monitor._requests))

    middleware = LoopStallRequestMiddleware(app, monitor)
    asyncio.run(middleware({"type": "http", "method": "GET", "path": "/x"}, None, None))
    assert list(seen[0].values()) == ["GET /x"]
    assert monitor._requests == {}
    assert LoopStallMonitor.from_environment() is not None
