import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest

from cueverge.controller.runtime import ControllerRuntime, ShowMode
from cueverge.gui.installation_geometry import InstallationGeometryService
from cueverge.gui.lighting_surface import LightingSurfaceService
from cueverge.gui.output_tests import mapped_test_segments
from cueverge.lighting.ddp import DdpUdpSink
from cueverge.lighting.output_test import (
    OutputTestError,
    OutputTestRunner,
    OutputTestSegment,
    OutputTestStatus,
    traversal_color,
    traversal_frame,
)
from cueverge.lighting.sink import FrameSinkRouter, MemoryFrameSink, RenderedFrame


def test_traversal_matches_browser_colors_boundaries_and_five_pixel_tail() -> None:
    assert traversal_color(0) == (90, 216, 242)
    segments = (
        OutputTestSegment("first", 0, 4, traversal_color(0)),
        OutputTestSegment("second", 4, 8, traversal_color(1)),
    )
    frame = traversal_frame(10, 5, segments)
    assert frame[0] == (0, 0, 0)
    assert frame[5] == traversal_color(1)
    assert frame[4] == tuple(round(channel * 0.84) for channel in traversal_color(1))
    assert frame[3] == tuple(round(channel * 0.68) for channel in traversal_color(0))
    assert traversal_frame(10, 9, segments)[9] == (255, 255, 255)


def test_fractional_traversal_crossfades_pixels_without_mixing_segment_colors() -> None:
    segments = (
        OutputTestSegment("first", 0, 4, traversal_color(0)),
        OutputTestSegment("second", 4, 8, traversal_color(1)),
    )
    frame = traversal_frame(10, 3.5, segments)
    assert frame[3] == tuple(round(channel * 0.92) for channel in traversal_color(0))
    assert frame[4] == tuple(round(channel * 0.5) for channel in traversal_color(1))
    assert frame[5] == (0, 0, 0)
    assert traversal_frame(10, 3.1, segments) != traversal_frame(10, 3.9, segments)
    assert traversal_frame(10, 9.5, segments)[9] == (235, 235, 235)
    assert traversal_frame(10, 0, segments)[9] == (0, 0, 0)


def test_runner_preserves_fractional_positions_and_stable_frame_deadlines() -> None:
    async def scenario() -> None:
        now = 0.0
        sink = MemoryFrameSink()

        async def sleep(delay: float) -> None:
            nonlocal now
            now += delay

        def send(frame: RenderedFrame) -> None:
            nonlocal now
            sink.frames.append(frame)
            now += 0.002

        runner = OutputTestRunner(
            sink=sink,
            resource="roof",
            pixel_count=10,
            speed=30,
            duration_seconds=0.105,
            arm_id="arm",
            segments=(),
            clock=lambda: now,
            fps=60,
            on_finish=lambda _: None,
        )
        with (
            patch.object(sink, "send", side_effect=send),
            patch("cueverge.lighting.output_test.asyncio.sleep", side_effect=sleep),
        ):
            runner.start()
            assert runner.task is not None
            await runner.task
        assert [frame.position_ms for frame in sink.frames] == [0, 17, 33, 50, 67, 83, 100, 0]
        assert sink.frames[1].pixels[1] == (128, 128, 128)
        assert runner.status.state == "completed"
        assert runner.status.stop_reason == "timeout"
        assert now == pytest.approx(0.107)
        assert sink.frames[-1].pixels == ((0, 0, 0),) * 10

    asyncio.run(scenario())


def test_physical_test_uses_60_fps_independently_of_show_render_rate() -> None:
    async def scenario() -> None:
        sink = DdpUdpSink("127.0.0.1", expected_pixel_count=8)
        runtime = ControllerRuntime(sink=sink, render_fps=25)
        await runtime.set_show_mode(ShowMode.MAINTENANCE)
        try:
            with patch(
                "cueverge.controller.runtime.OutputTestRunner", wraps=OutputTestRunner
            ) as runner:
                runtime.start_output_test(
                    resource="roofline.demo",
                    pixel_count=8,
                    speed=30,
                    duration_seconds=5,
                    arm_id="arm",
                    segments=(),
                    on_finish=lambda _: None,
                )
                assert runner.call_args.kwargs["fps"] == 60
                assert runtime.render_fps == 25
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_actual_ddp_frames_use_configured_brightness_expire_and_finish_black() -> None:
    async def scenario() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(0.1)
            sink = DdpUdpSink(
                "127.0.0.1",
                port=receiver.getsockname()[1],
                expected_pixel_count=8,
                brightness_limit=0.5,
            )
            untouched = MemoryFrameSink()
            runtime = ControllerRuntime(
                sink=FrameSinkRouter({"roofline.demo": sink, "untouched": untouched}),
                render_fps=100,
            )
            completed: list[OutputTestStatus] = []
            try:
                await runtime.set_show_mode(ShowMode.MAINTENANCE)
                status = runtime.start_output_test(
                    resource="roofline.demo",
                    pixel_count=8,
                    speed=30,
                    duration_seconds=0.1,
                    arm_id="arm",
                    segments=(),
                    on_finish=completed.append,
                )
                first = receiver.recv(1024)
                assert len(first) == 10 + 8 * 3
                assert first[10:13] == bytes([128, 128, 128])
                assert first[13:] == bytes(7 * 3)
                with pytest.raises(OutputTestError, match="already owns"):
                    runtime.start_output_test(
                        resource="roofline.demo",
                        pixel_count=8,
                        speed=30,
                        duration_seconds=0.1,
                        arm_id="other",
                        segments=(),
                        on_finish=completed.append,
                    )
                with pytest.raises(RuntimeError, match="active output test"):
                    await runtime.start_scene("ambient")
                await asyncio.sleep(0.15)
                assert status.state == "completed"
                assert status.stop_reason == "timeout"
                assert status.frames_sent > 1
                assert completed == [status]
                packets = []
                while True:
                    try:
                        packets.append(receiver.recv(1024))
                    except TimeoutError:
                        break
                assert packets[-1][10:] == bytes(8 * 3)
                assert untouched.frames == []
                assert runtime.active_scene is None
            finally:
                await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["stop", "mode", "shutdown"])
def test_runtime_actions_stop_physical_test_and_release_output(action: str) -> None:
    async def scenario() -> None:
        sink = DdpUdpSink("127.0.0.1", expected_pixel_count=8)
        memory = MemoryFrameSink()
        runtime = ControllerRuntime(sink=sink)
        with patch.object(sink, "send", side_effect=memory.send):
            await runtime.set_show_mode(ShowMode.MAINTENANCE)
            status = runtime.start_output_test(
                resource="roofline.demo",
                pixel_count=8,
                speed=30,
                duration_seconds=5,
                arm_id="arm",
                segments=(),
                on_finish=lambda _: None,
            )
            if action == "mode":
                await runtime.set_show_mode(ShowMode.AUTOMATIC)
            elif action == "stop":
                await runtime.stop()
            else:
                await runtime.shutdown()
            assert status.state == "stopped"
            assert memory.frames[-1].pixels == ((0, 0, 0),) * 8
            sent = len(memory.frames)
            await asyncio.sleep(0.05)
            assert len(memory.frames) == sent
            await runtime.shutdown()

    asyncio.run(scenario())


def test_failed_send_is_explicit_and_attempts_a_final_black_frame() -> None:
    async def scenario() -> None:
        sink = DdpUdpSink("127.0.0.1", expected_pixel_count=8)
        runtime = ControllerRuntime(sink=sink)
        await runtime.set_show_mode(ShowMode.MAINTENANCE)
        finished: list[OutputTestStatus] = []
        with patch.object(sink, "send", side_effect=OSError("disconnected")) as send:
            with pytest.raises(OutputTestError, match="disconnected"):
                runtime.start_output_test(
                    resource="roofline.demo",
                    pixel_count=8,
                    speed=30,
                    duration_seconds=5,
                    arm_id="arm",
                    segments=(),
                    on_finish=finished.append,
                )
            assert send.call_count == 2
            assert finished[0].state == "failed"
            assert "final black frame failed" in (finished[0].error or "")
            assert runtime.output_test_status() == finished[0]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_unconfigured_outputs_and_nonmaintenance_mode_are_rejected() -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime()
        with pytest.raises(OutputTestError, match="idle maintenance"):
            runtime.start_output_test(
                resource="roofline.demo",
                pixel_count=8,
                speed=30,
                duration_seconds=5,
                arm_id="arm",
                segments=(),
                on_finish=lambda _: None,
            )
        await runtime.set_show_mode(ShowMode.MAINTENANCE)
        with pytest.raises(OutputTestError, match="configured DDP"):
            runtime.start_output_test(
                resource="roofline.demo",
                pixel_count=8,
                speed=30,
                duration_seconds=5,
                arm_id="arm",
                segments=(),
                on_finish=lambda _: None,
            )
        await runtime.shutdown()

    asyncio.run(scenario())


def test_saved_mapping_colors_are_ordered_deduplicated_and_scoped_to_output() -> None:
    geometry = create_autospec(InstallationGeometryService, instance=True)
    lighting = create_autospec(LightingSurfaceService, instance=True)
    geometry.list.return_value = [
        SimpleNamespace(
            id=id_,
            kind="pixel_segment",
            name=id_,
            semantic_id=semantic,
            start_index=start,
            pixel_count=count,
        )
        for id_, semantic, start, count in [
            ("second", "second", 4, 4),
            ("other-output", "other", 0, 8),
            ("first-photo-a", "first", 0, 4),
            ("first-photo-b", "first", 0, 4),
        ]
    ]
    lighting.get.side_effect = lambda id_: SimpleNamespace(
        resource="porch" if id_ == "other-output" else "roofline.demo",
    )
    segments = mapped_test_segments(geometry, lighting, "installation", "roofline.demo", 8)
    assert [(segment.start, segment.end) for segment in segments] == [(0, 4), (4, 8)]
    assert [segment.color for segment in segments] == [traversal_color(0), traversal_color(1)]
    geometry.list.return_value[0].start_index = 3
    with pytest.raises(OutputTestError, match="overlapping"):
        mapped_test_segments(geometry, lighting, "installation", "roofline.demo", 8)
    geometry.list.return_value[0].start_index = 4
    geometry.list.return_value[-1].pixel_count = 3
    with pytest.raises(OutputTestError, match="linked photo views disagree"):
        mapped_test_segments(geometry, lighting, "installation", "roofline.demo", 8)


def test_scene_transition_cannot_race_a_physical_test() -> None:
    async def scenario() -> None:
        sink = DdpUdpSink("127.0.0.1", expected_pixel_count=8)
        runtime = ControllerRuntime(sink=sink)
        await runtime.set_show_mode(ShowMode.MAINTENANCE)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def starting(_: str, expected_generation: int) -> None:
            assert expected_generation == runtime._scene_control_generation
            entered.set()
            await release.wait()
            raise RuntimeError("test transition ended")

        with patch.object(runtime, "_start_scene", side_effect=starting):
            task = asyncio.create_task(runtime.start_scene("ambient"))
            await entered.wait()
            with pytest.raises(OutputTestError, match="idle maintenance"):
                runtime.start_output_test(
                    resource="roofline.demo",
                    pixel_count=8,
                    speed=30,
                    duration_seconds=5,
                    arm_id="arm",
                    segments=(),
                    on_finish=lambda _: None,
                )
            release.set()
            with pytest.raises(RuntimeError, match="test transition ended"):
                await task
        await runtime.shutdown()

    asyncio.run(scenario())


def test_midstream_failure_is_visible_in_runtime_health() -> None:
    async def scenario() -> None:
        sink = DdpUdpSink("127.0.0.1", expected_pixel_count=8)
        runtime = ControllerRuntime(sink=sink, render_fps=100)
        await runtime.set_show_mode(ShowMode.MAINTENANCE)
        with patch.object(sink, "send", side_effect=[None, OSError("lost output"), None]):
            status = runtime.start_output_test(
                resource="roofline.demo",
                pixel_count=8,
                speed=30,
                duration_seconds=5,
                arm_id="arm",
                segments=(),
                on_finish=lambda _: None,
            )
            await asyncio.sleep(0.04)
            assert status.state == "failed"
            assert "lost output" in (status.error or "")
            assert runtime.status()["lighting_output_error_count"] == 1
            assert runtime.status()["degraded"] is True
            assert status.frames_sent == 2
        await runtime.shutdown()

    asyncio.run(scenario())
