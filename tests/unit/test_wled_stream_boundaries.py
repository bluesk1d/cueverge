"""Production rendering/serialization, with a byte-offset DDP receiver (no hardware)."""

import asyncio
import json
import logging
import struct
from pathlib import Path

import pytest

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.core.models import (
    LightingCue,
    LightingOutputDefinition,
    LoadedLightingCue,
    LoadedLightingOutput,
)
from cueverge.lighting.ddp import DdpUdpSink
from cueverge.lighting.evaluation import evaluate_lighting_output
from cueverge.lighting.sink import RenderedFrame
from tests.unit.test_controller_clock import package
from tests.unit.test_multi_output_media import ManualClock


class Receiver:
    def __init__(self) -> None:
        self.buffer = bytearray(605 * 3)
        self.frames: list[bytes] = []
        self.packets: list[bytes] = []

    def sendto(self, packet: bytes, address: tuple[str, int]) -> int:
        flags, seq, kind, dest, offset, size = struct.unpack("!BBBBIH", packet[:10])
        assert 0 <= seq <= 15 and kind == 0 and dest == 1
        assert size == len(packet) - 10 and offset % 3 == size % 3 == 0
        assert offset + size <= len(self.buffer)
        self.buffer[offset : offset + size] = packet[10:]
        self.packets.append(packet)
        if flags & 1:
            self.frames.append(bytes(self.buffer))
        return len(packet)


def output(animated: bool) -> LoadedLightingOutput:
    # 96 disjoint segments, including targets on both sides of pixel 480.
    cues = []
    for segment in range(96):
        indices = tuple(range(segment * 605 // 96, (segment + 1) * 605 // 96))
        for half in range(2 if animated else 1):
            cue = LightingCue(
                id=f"{segment}-{half}",
                primitive="color_transition" if animated else "solid",
                start_ms=half * 30000,
                duration_ms=30000 if animated else 60000,
                color="#FF8000",
                end_color="#FF8000" if animated else None,
                brightness=0.27 if half == 0 else 0.36,
                end_brightness=(0.36 if half == 0 else 0.27) if animated else None,
            )
            cues.append(LoadedLightingCue(definition=cue, target_indices=indices))
    return LoadedLightingOutput(
        definition=LightingOutputDefinition(
            id="leds",
            resource="leds",
            timeline=tuple(c.definition for c in cues),
        ),
        pixel_count=605,
        color=(255, 128, 0),
        lighting_cues=tuple(cues),
    )


@pytest.mark.parametrize("animated", [False, True])
def test_three_loops_render_and_reconstruct_all_605_pixels(animated: bool) -> None:
    receiver = Receiver()
    sink = DdpUdpSink("mock", expected_pixel_count=605, _socket=receiver)  # type: ignore[arg-type]
    loaded = output(animated)
    retained = []
    # 25 FPS, plus millisecond boundary probes that ordinary scheduling can miss.
    positions = sorted(set(range(0, 60000, 40)) | {29998, 29999, 30000, 30001, 59998, 59999})
    for _loop in range(3):
        for position in positions:
            pixels = evaluate_lighting_output(loaded, position_ms=position, duration_ms=60000)
            assert len(pixels) == 605
            assert all(69 <= r <= 92 and 35 <= g <= 46 and b == 0 for r, g, b in pixels)
            frame = RenderedFrame("diagnostic", position, pixels)
            sink.send(frame)
            expected = bytes(c for pixel in pixels for c in pixel)
            assert receiver.frames[-1] == expected
            assert [len(p) for p in receiver.packets[-2:]] == [1450, 385]
            assert [struct.unpack_from("!I", p, 4)[0] for p in receiver.packets[-2:]] == [0, 1440]
            if position in (0, 29999, 30000, 59999):
                retained.append((frame, expected))
    # Later rendering/sends cannot mutate frames retained by a pending consumer.
    for frame, expected in retained:
        assert bytes(c for pixel in frame.pixels for c in pixel) == expected


def test_reentrant_render_during_send_preserves_serialized_snapshot() -> None:
    loaded = output(True)
    receiver = Receiver()
    original_send = receiver.sendto

    def send(packet: bytes, address: tuple[str, int]) -> int:
        evaluate_lighting_output(loaded, position_ms=30001, duration_ms=60000)
        return original_send(packet, address)

    receiver.sendto = send  # type: ignore[method-assign]
    sink = DdpUdpSink("mock", _socket=receiver)  # type: ignore[arg-type]
    pixels = evaluate_lighting_output(loaded, position_ms=0, duration_ms=60000)
    sink.send(RenderedFrame("diagnostic", 0, pixels))
    assert receiver.frames[-1] == bytes(c for pixel in pixels for c in pixel)


def test_controller_completion_restarts_605_pixel_scene(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = package(tmp_path, policy="loop")
        installation = json.loads((root / "installation.json").read_text())
        installation["resources"][0]["pixel_count"] = 605
        (root / "installation.json").write_text(json.dumps(installation))
        path = root / "scenes/preshow.json"
        scene = json.loads(path.read_text())
        scene["duration_ms"] = 60000
        scene["lighting"] = [output(True).definition.model_dump(mode="json", exclude_none=True)]
        path.write_text(json.dumps(scene))
        clock = ManualClock()
        receiver = Receiver()
        sink = DdpUdpSink("mock", _socket=receiver)  # type: ignore[arg-type]
        runtime = ControllerRuntime(catalog=load_show_package(root), clock=clock, sink=sink)
        try:
            await runtime.start_scene("preshow")
            for _loop in range(3):
                for delta in (29.999, 0.001, 29.999, 0.001):
                    clock.now += delta
                    runtime.render_frame()
                    await runtime.wait_for_lifecycle_idle()
                runtime.render_frame()
                assert runtime.status()["estimated_position_ms"] == 0
            assert runtime.scene_completions == 3
            assert all(
                all(
                    69 <= r <= 92 and 35 <= g <= 46 and b == 0
                    for r, g, b in zip(frame[::3], frame[1::3], frame[2::3], strict=True)
                )
                for frame in receiver.frames
            )
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_diagnostics_stop_at_budget(caplog: pytest.LogCaptureFixture) -> None:
    receiver = Receiver()
    sink = DdpUdpSink("mock", diagnostic_frames=1, _socket=receiver)  # type: ignore[arg-type]
    frame = RenderedFrame("capture", 29999, ((70, 35, 0),) * 605)
    with caplog.at_level(logging.DEBUG, logger="cueverge.lighting.ddp"):
        sink.send(frame)
        sink.send(frame)
    assert sink.diagnostic_frames == 0
    assert len(caplog.records) == 1
    assert "position_ms=29999" in caplog.text
    assert "frame_crc=" in caplog.text
