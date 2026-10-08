import asyncio
from dataclasses import dataclass
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.models import PlaybackState


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


def test_render_loop_runs_at_independent_cadence_and_stops() -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(render_fps=50)
        agent = FakeMediaAgent()
        channel = MemoryCommandChannel([])
        runtime.register_agent(agent.agent_id, channel)

        active = await runtime.start_scene("synthetic-sweep")
        agent.attach(active.scene_instance_id, active.playback_session_id)
        assert runtime.ingest(agent.position(500)).accepted
        await asyncio.sleep(0.075)

        assert len(runtime.sink.frames) >= 3
        assert len({frame.position_ms for frame in runtime.sink.frames}) >= 2
        assert runtime.render_loop_running
        assert runtime.status()["mean_render_interval_ms"] is not None

        await runtime.stop()
        stopped_count = len(runtime.sink.frames)
        await asyncio.sleep(0.04)
        assert len(runtime.sink.frames) == stopped_count
        assert not runtime.render_loop_running
        assert runtime.active_scene is None

    asyncio.run(scenario())


def test_pause_and_stale_telemetry_render_bounded_hold_frames() -> None:
    @dataclass
    class ManualClock:
        now: float = 10.0

        def __call__(self) -> float:
            return self.now

    clock = ManualClock()
    runtime = ControllerRuntime(clock=clock, stale_after_ms=500)
    runtime.active_scene = active = _active_scene(runtime)
    agent = FakeMediaAgent()
    agent.attach(active.scene_instance_id, active.playback_session_id)

    runtime.ingest(agent.position(1000, state=PlaybackState.PAUSED))
    clock.now += 0.1
    assert runtime.render_frame()
    assert runtime.sink.frames[-1].position_ms == 1000

    runtime.ingest(agent.position(2000))
    clock.now += 1
    assert runtime.render_frame()
    assert runtime.sink.frames[-1].position_ms == 2500
    assert runtime.stale_frames == 1


def _active_scene(runtime: ControllerRuntime):
    from cueverge.controller.runtime import ActiveScene
    from cueverge.core.models import SYNTHETIC_SWEEP

    active = ActiveScene(SYNTHETIC_SWEEP, "scene-1", "playback-1")
    runtime.follower.begin(active.scene_instance_id, active.playback_session_id)
    return active
