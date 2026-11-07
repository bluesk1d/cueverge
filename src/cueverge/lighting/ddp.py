import logging
import socket
import struct
import time
import zlib
from dataclasses import dataclass, field

from cueverge.lighting.renderer import Rgb
from cueverge.lighting.sink import RenderedFrame

DDP_PORT = 4048
DDP_VERSION_1 = 0x40
DDP_PUSH = 0x01
DDP_HEADER_SIZE = 10


def encode_ddp_frame(
    pixels: tuple[Rgb, ...],
    *,
    sequence: int,
    destination_id: int = 1,
    max_payload_bytes: int = 1440,
) -> tuple[bytes, ...]:
    if not 0 <= sequence <= 15:
        raise ValueError("DDP sequence must be between 0 and 15")
    if not 1 <= destination_id <= 255:
        raise ValueError("DDP destination_id must be between 1 and 255")
    if max_payload_bytes < 3:
        raise ValueError("DDP max payload must fit at least one RGB pixel")
    if not pixels:
        raise ValueError("DDP frame must contain at least one pixel")

    payload_limit = max_payload_bytes - (max_payload_bytes % 3)
    pixel_bytes = bytes(channel for pixel in pixels for channel in pixel)
    packets: list[bytes] = []
    for offset in range(0, len(pixel_bytes), payload_limit):
        payload = pixel_bytes[offset : offset + payload_limit]
        is_last = offset + len(payload) == len(pixel_bytes)
        flags = DDP_VERSION_1 | (DDP_PUSH if is_last else 0)
        header = struct.pack(
            "!BBBBIH",
            flags,
            sequence,
            0,
            destination_id,
            offset,
            len(payload),
        )
        packets.append(header + payload)
    return tuple(packets)


@dataclass
class DdpUdpSink:
    host: str
    port: int = DDP_PORT
    destination_id: int = 1
    max_payload_bytes: int = 1440
    expected_pixel_count: int | None = None
    brightness_limit: float = 1.0
    _sequence: int = 0
    frames_sent: int = 0
    # Explicit bounded capture; zero keeps checksum/logging work off the hot path.
    diagnostic_frames: int = 0
    _socket: socket.socket = field(
        default_factory=lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    )

    def send(self, frame: RenderedFrame) -> None:
        started = time.monotonic() if self.diagnostic_frames > 0 else None
        if self.expected_pixel_count is not None and len(frame.pixels) != self.expected_pixel_count:
            raise ValueError(
                f"DDP frame has {len(frame.pixels)} pixels; expected {self.expected_pixel_count}"
            )
        if not 0 < self.brightness_limit <= 1:
            raise ValueError("DDP brightness_limit must be greater than 0 and at most 1")
        pixels: tuple[Rgb, ...] = tuple(
            (
                round(pixel[0] * self.brightness_limit),
                round(pixel[1] * self.brightness_limit),
                round(pixel[2] * self.brightness_limit),
            )
            for pixel in frame.pixels
        )
        packets = encode_ddp_frame(
            pixels,
            sequence=self._sequence,
            destination_id=self.destination_id,
            max_payload_bytes=self.max_payload_bytes,
        )
        for packet in packets:
            sent = self._socket.sendto(packet, (self.host, self.port))
            if sent != len(packet):
                raise OSError(f"partial DDP datagram send: {sent}/{len(packet)} bytes")
        if started is not None:
            self.diagnostic_frames -= 1
            logging.getLogger(__name__).debug(
                "ddp_frame instance=%s resource=%s position_ms=%s sequence=%s "
                "start=%.6f end=%.6f frame_crc=%08x packets=%s",
                frame.scene_instance_id,
                frame.resource,
                frame.position_ms,
                self._sequence,
                started,
                time.monotonic(),
                zlib.crc32(bytes(c for pixel in frame.pixels for c in pixel)),
                [(len(p), struct.unpack_from("!I", p, 4)[0], zlib.crc32(p)) for p in packets],
            )
        self._sequence = (self._sequence + 1) % 16
        self.frames_sent += 1

    def close(self) -> None:
        self._socket.close()
