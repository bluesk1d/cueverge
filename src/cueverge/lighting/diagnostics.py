"""Bounded, hardware-independent DDP replay and local UDP receiver diagnostics.

Run with ``python -m cueverge.lighting.diagnostics --help``.
"""

import argparse
import asyncio
import json
import socket
import struct
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.contracts import DraftProject
from cueverge.lighting.ddp import DdpUdpSink
from cueverge.lighting.sink import RenderedFrame


class DdpReceiver:
    """Offset/PUSH model, not an emulation of firmware display scheduling.

    Keep one instance per source and destination. Coverage resets on sequence change
    or PUSH; zero sequence cannot reliably distinguish interleaved frames.
    """

    def __init__(self, pixel_count: int) -> None:
        if not 0 < pixel_count <= 100000:
            raise ValueError("pixel_count must be within 1..100000")
        self.pixels = bytearray(pixel_count * 3)
        self.covered = bytearray(len(self.pixels))
        self.sequence: int | None = None

    def accept(self, packet: bytes) -> dict[str, Any]:
        if len(packet) < 10:
            raise ValueError("short DDP header")
        flags, seq, kind, dest, offset, size = struct.unpack("!BBBBIH", packet[:10])
        if flags & 0xC0 != 0x40 or flags & 0x1E:
            raise ValueError("unsupported DDP flags")
        if kind not in (0, 1) or seq > 15:
            raise ValueError("unsupported DDP RGB type or sequence")
        if len(packet) != 10 + size or not size or offset % 3 or size % 3:
            raise ValueError("invalid DDP RGB payload")
        if offset + size > len(self.pixels):
            raise ValueError("DDP offset exceeds installation")
        if seq != self.sequence:
            self.covered[:] = bytes(len(self.covered))
            self.sequence = seq
        overlap = any(self.covered[offset : offset + size])
        self.pixels[offset : offset + size] = packet[10:]
        self.covered[offset : offset + size] = b"\x01" * size
        result: dict[str, Any] = {
            "sequence": seq,
            "destination": dest,
            "offset": offset,
            "length": len(packet),
            "packet_crc": zlib.crc32(packet),
            "push": bool(flags & 1),
            "overlap": overlap,
        }
        if flags & 1:
            result.update(
                missing_bytes=self.covered.count(0),
                frame_crc=zlib.crc32(self.pixels),
                blue_nonzero=sum(b != 0 for b in self.pixels[2::3]),
            )
            self.covered[:] = bytes(len(self.covered))
        return result


class ReplaySink:
    def __init__(self) -> None:
        self.receiver = DdpReceiver(605)
        self.packets: list[dict[str, Any]] = []
        self.frames = 0
        self.last: RenderedFrame | None = None
        self.ddp = DdpUdpSink("offline", _socket=self)  # type: ignore[arg-type]

    def sendto(self, packet: bytes, address: tuple[str, int]) -> int:
        self.packets.append(self.receiver.accept(packet))
        return len(packet)

    def send(self, frame: RenderedFrame) -> None:
        self.packets = []
        self.ddp.send(frame)
        expected = bytes(c for pixel in frame.pixels for c in pixel)
        if bytes(self.receiver.pixels) != expected or self.packets[-1]["missing_bytes"]:
            raise AssertionError("serialized frame differs from rendered output")
        self.last = frame
        self.frames += 1

    def close(self) -> None:
        pass


async def replay(draft_path: Path, output_path: Path, loops: int) -> None:
    draft = DraftProject.model_validate_json(draft_path.read_text())
    with tempfile.TemporaryDirectory(prefix="cueverge-ddp-") as temporary:
        compiled = DraftCompiler(Path(temporary)).compile(
            project_id="diagnostic",
            revision=1,
            draft=draft,
        )
        catalog = load_show_package(compiled.package_root)
        with output_path.open("x", encoding="utf-8") as trace:
            for scene in catalog.scenes.values():
                if scene.definition.mode != "controller_timed" or len(scene.lighting_outputs) != 1:
                    raise ValueError("replay supports controller-timed scenes with one LED output")
                if scene.lighting_outputs[0].pixel_count != 605:
                    raise ValueError("this investigation replay requires 605 pixels")
                now = [0.0]
                sink = ReplaySink()

                def clock(now: list[float] = now) -> float:
                    return now[0]

                runtime = ControllerRuntime(catalog=catalog, clock=clock, sink=sink)
                cues = scene.lighting_outputs[0].lighting_cues
                duration = scene.definition.duration_ms
                boundaries = {0, duration}
                for cue in cues:
                    boundaries.update(
                        (
                            cue.definition.start_ms,
                            cue.definition.start_ms + cue.definition.duration_ms,
                        )
                    )
                positions = sorted(
                    set(range(0, duration, 40))
                    | {
                        max(0, min(duration - 1, b + delta))
                        for b in boundaries
                        for delta in (-1, 0, 1)
                    }
                )
                channel_min = [255, 255, 255]
                channel_max = [0, 0, 0]
                try:
                    await runtime.start_scene(scene.definition.id)
                    for loop in range(loops):
                        anchor = now[0]
                        for position in positions:
                            now[0] = anchor + position / 1000
                            started = time.monotonic()
                            runtime.render_frame()
                            elapsed = time.monotonic() - started
                            frame = sink.last
                            assert frame is not None
                            for channel in range(3):
                                channel_min[channel] = min(
                                    channel_min[channel],
                                    min(pixel[channel] for pixel in frame.pixels),
                                )
                                channel_max[channel] = max(
                                    channel_max[channel],
                                    max(pixel[channel] for pixel in frame.pixels),
                                )
                            if any(abs(position - b) <= 80 for b in boundaries):
                                trace.write(
                                    json.dumps(
                                        {
                                            "scene_id": scene.definition.id,
                                            "loop": loop,
                                            "instance": frame.scene_instance_id,
                                            "position_ms": frame.position_ms,
                                            "render_send_ms": elapsed * 1000,
                                            "active_cues": [
                                                c.definition.id
                                                for c in cues
                                                if c.definition.start_ms
                                                <= frame.position_ms
                                                < c.definition.start_ms + c.definition.duration_ms
                                            ],
                                            "frame_crc": zlib.crc32(sink.receiver.pixels),
                                            "packets": sink.packets,
                                        }
                                    )
                                    + "\n"
                                )
                        now[0] = anchor + duration / 1000
                        runtime.render_frame()
                        await runtime.wait_for_lifecycle_idle()
                    print(
                        json.dumps(
                            {
                                "scene_id": scene.definition.id,
                                "frames_verified": sink.frames,
                                "completions": runtime.scene_completions,
                                "channel_min": channel_min,
                                "channel_max": channel_max,
                            }
                        )
                    )
                finally:
                    await runtime.stop()


def receive(bind: str, port: int, pixels: int, seconds: float, output: Path) -> None:
    # A local receiver sees only traffic addressed to it, never another host's unicast.
    receivers: dict[tuple[str, int, int], DdpReceiver] = {}
    deadline = time.monotonic() + seconds
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp, output.open("x") as trace:
        udp.bind((bind, port))
        udp.settimeout(0.25)
        count = 0
        while time.monotonic() < deadline and count < 100000:
            try:
                packet, source = udp.recvfrom(65535)
            except TimeoutError:
                continue
            count += 1
            record: dict[str, Any] = {"time_ns": time.time_ns(), "source": source}
            try:
                if len(packet) < 10:
                    raise ValueError("short DDP header")
                key = (*source, packet[3])
                if key not in receivers:
                    if len(receivers) >= 32:
                        raise ValueError("source/destination limit reached")
                    receivers[key] = DdpReceiver(pixels)
                record.update(receivers[key].accept(packet))
            except ValueError as error:
                record["error"] = str(error)
            trace.write(json.dumps(record) + "\n")
        print(json.dumps({"datagrams": count, "sources": len(receivers)}))


def analyze_pcap(capture: Path, output: Path, pixels: int = 605) -> dict[str, int]:
    """Read classic tcpdump PCAP; separate Linux-any interfaces/directions.

    No payload is logged. Fragmented/truncated/unsupported network packets are
    counted explicitly rather than interpreted as complete DDP datagrams.
    """
    streams: dict[tuple[Any, ...], DdpReceiver] = {}
    previous: dict[tuple[Any, ...], tuple[bytes, int]] = {}
    summary = {
        "packets": 0,
        "ddp": 0,
        "pushes": 0,
        "incomplete_pushes": 0,
        "sequence_zero": 0,
        "unsupported_or_truncated": 0,
        "invalid_ddp": 0,
        "duplicate_observations": 0,
    }
    with capture.open("rb") as source, output.open("x") as trace:
        header = source.read(24)
        formats = {
            b"\xd4\xc3\xb2\xa1": ("<", 1000),
            b"\xa1\xb2\xc3\xd4": (">", 1000),
            b"\x4d\x3c\xb2\xa1": ("<", 1),
            b"\xa1\xb2\x3c\x4d": (">", 1),
        }
        if len(header) != 24 or header[:4] not in formats:
            raise ValueError("expected classic PCAP (tcpdump -w), not PCAPNG")
        endian, nanos = formats[header[:4]]
        link = struct.unpack(endian + "I", header[20:24])[0]
        if link not in (1, 101, 113, 276):
            raise ValueError(f"unsupported PCAP link type {link}")
        while record := source.read(16):
            if len(record) != 16:
                raise ValueError("truncated PCAP record header")
            seconds, fraction, size, original = struct.unpack(endian + "IIII", record)
            if size > 1048576:
                raise ValueError("PCAP record exceeds one MiB")
            data = source.read(size)
            if len(data) != size:
                raise ValueError("truncated PCAP packet")
            summary["packets"] += 1
            interface = direction = 0
            if link == 276 and len(data) >= 20:
                interface = struct.unpack_from("!I", data, 4)[0]
                direction = data[10]
                protocol, data = int.from_bytes(data[:2], "big"), data[20:]
            elif link == 113 and len(data) >= 16:
                direction = int.from_bytes(data[:2], "big")
                protocol, data = int.from_bytes(data[14:16], "big"), data[16:]
            elif link == 1 and len(data) >= 14:
                protocol, data = int.from_bytes(data[12:14], "big"), data[14:]
                while protocol in (0x8100, 0x88A8) and len(data) >= 4:
                    protocol, data = int.from_bytes(data[2:4], "big"), data[4:]
            elif link == 101:
                protocol = 0x0800
            else:
                summary["unsupported_or_truncated"] += 1
                continue
            if (
                size != original
                or protocol != 0x0800
                or len(data) < 28
                or data[0] >> 4 != 4
                or data[9] != 17
            ):
                summary["unsupported_or_truncated"] += 1
                continue
            ihl = (data[0] & 15) * 4
            total = int.from_bytes(data[2:4], "big")
            fragmented = int.from_bytes(data[6:8], "big") & 0x3FFF
            if ihl < 20 or total > len(data) or total < ihl + 8 or fragmented:
                summary["unsupported_or_truncated"] += 1
                continue
            src, dst = socket.inet_ntoa(data[12:16]), socket.inet_ntoa(data[16:20])
            sport, dport, length = struct.unpack_from("!HHH", data, ihl)
            if dport != 4048 and sport != 4048:
                continue
            if length < 18 or ihl + length != total:
                summary["unsupported_or_truncated"] += 1
                continue
            payload = data[ihl + 8 : total]
            key = (interface, direction, src, sport, dst, dport, payload[3])
            if key not in streams:
                if len(streams) >= 256:
                    raise ValueError("PCAP exceeds 256 stream paths")
                streams[key] = DdpReceiver(pixels)
            item: dict[str, Any] = {
                "time_ns": seconds * 1000000000 + fraction * nanos,
                "stream": key,
            }
            # Linux cooked v1 omits interface IDs. A bridge/bond can expose the
            # exact same IP datagram more than once on `any`. Preserve the
            # observation, but don't apply its PUSH twice to the assembly model.
            # This is an analysis heuristic, not proof of wire-level duplication.
            observed_ns = item["time_ns"]
            prior = previous.get(key)
            previous[key] = (data[:total], observed_ns)
            if (
                prior is not None
                and prior[0] == data[:total]
                and 0 <= observed_ns - prior[1] <= 1000000
            ):
                summary["duplicate_observations"] += 1
                item.update(duplicate_observation=True, packet_crc=zlib.crc32(payload))
                trace.write(json.dumps(item) + "\n")
                continue
            try:
                item.update(streams[key].accept(payload))
                summary["ddp"] += 1
                summary["sequence_zero"] += item["sequence"] == 0
                if item["push"]:
                    summary["pushes"] += 1
                    summary["incomplete_pushes"] += item["missing_bytes"] > 0
                    rgb = streams[key].pixels
                    item["channel_min"] = [min(rgb[c::3]) for c in range(3)]
                    item["channel_max"] = [max(rgb[c::3]) for c in range(3)]
            except ValueError as error:
                summary["invalid_ddp"] += 1
                item["error"] = str(error)
            trace.write(json.dumps(item) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay", help="offline saved-draft verification")
    replay_parser.add_argument("draft", type=Path)
    replay_parser.add_argument("output", type=Path)
    replay_parser.add_argument("--loops", type=int, choices=range(1, 11), default=3)
    receiver_parser = commands.add_parser("receive", help="bounded local UDP receiver")
    receiver_parser.add_argument("output", type=Path)
    receiver_parser.add_argument("--bind", default="127.0.0.1")
    receiver_parser.add_argument("--port", type=int, default=4048)
    receiver_parser.add_argument("--pixels", type=int, default=605)
    receiver_parser.add_argument("--seconds", type=float, default=30)
    capture_parser = commands.add_parser("analyze", help="inspect a tcpdump PCAP offline")
    capture_parser.add_argument("capture", type=Path)
    capture_parser.add_argument("output", type=Path)
    capture_parser.add_argument("--pixels", type=int, default=605)
    args = parser.parse_args()
    if args.command == "replay":
        asyncio.run(replay(args.draft, args.output, args.loops))
    elif args.command == "analyze":
        print(json.dumps(analyze_pcap(args.capture, args.output, args.pixels)))
    else:
        if not 0 < args.seconds <= 300:
            parser.error("seconds must be within (0, 300]")
        receive(args.bind, args.port, args.pixels, args.seconds, args.output)


if __name__ == "__main__":
    main()
