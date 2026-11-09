"""Single-point LIFX LAN output with bounded latest-frame delivery."""

import colorsys
import secrets
import socket
import struct
import threading
import time

from cueverge.lighting.sink import RenderedFrame


def encode_lifx_packet(
    target: bytes, source: int, sequence: int, kind: int, payload: bytes
) -> bytes:
    if len(target) != 6:
        raise ValueError("LIFX target must contain six bytes")
    return (
        struct.pack(
            "<HHI8s6sBB8sHH",
            36 + len(payload),
            0x1400,
            source,
            target + bytes(2),
            bytes(6),
            2,
            sequence,
            bytes(8),
            kind,
            0,
        )
        + payload
    )


class LifxUdpSink:
    expected_pixel_count = 1

    def __init__(
        self,
        host: str,
        target: str,
        *,
        port: int = 56700,
        kelvin: int = 3500,
        brightness_limit: float = 1.0,
        max_hz: float = 18,
        duration_ms: int = 0,
    ) -> None:
        self.host = host
        self.port = port
        self.target = bytes.fromhex(target)
        if len(self.target) != 6 or not any(self.target):
            raise ValueError("LIFX requires an individual six-byte target ID")
        if not 1 <= max_hz <= 20 or not 0 < brightness_limit <= 1:
            raise ValueError("Invalid LIFX cadence or brightness limit")
        if not 1500 <= kelvin <= 9000 or not 0 <= duration_ms <= 1000:
            raise ValueError("Invalid LIFX temperature or transition")
        self.kelvin = kelvin
        self.brightness_limit = brightness_limit
        self.max_hz = max_hz
        self.duration_ms = duration_ms
        self.frames_sent = 0
        self.frames_coalesced = 0
        self.acknowledgments = 0
        self.last_ack_at: float | None = None
        self.last_ack_rtt_ms: float | None = None
        self.last_sent_at: float | None = None
        self._outstanding: dict[int, float] = {}
        self._source = secrets.randbits(32) or 1
        self._sequence = 0
        self._condition = threading.Condition()
        self._pending: bytes | None = None
        self._closed = False
        self._error: OSError | None = None
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._thread = threading.Thread(target=self._run, name="lifx-output", daemon=True)
        self._thread.start()

    def send(self, frame: RenderedFrame) -> None:
        if len(frame.pixels) != 1:
            raise ValueError("Stationary point lights require exactly one RGB sample")
        rgb = frame.pixels[0]
        if any(not 0 <= channel <= 255 for channel in rgb):
            raise ValueError("RGB channels must be between 0 and 255")
        hue, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in rgb))
        payload = struct.pack(
            "<B4HI",
            0,
            round(hue * 65535),
            round(saturation * 65535),
            round(value * self.brightness_limit * 65535),
            self.kelvin,
            0 if frame.scene_instance_id.startswith("timing-calibration:") else self.duration_ms,
        )
        with self._condition:
            if self._closed:
                raise OSError("LIFX output is closed")
            if self._error is not None:
                raise OSError(f"LIFX output failed: {self._error}") from self._error
            if self._pending is not None:
                self.frames_coalesced += 1
            self._pending = payload
            self._condition.notify()

    def _transmit(self, kind: int, payload: bytes) -> None:
        packet = encode_lifx_packet(self.target, self._source, self._sequence, kind, payload)
        if self._socket.sendto(packet, (self.host, self.port)) != len(packet):
            raise OSError("Partial LIFX datagram send")
        self.last_sent_at = time.perf_counter()
        self._outstanding[self._sequence] = self.last_sent_at
        self._sequence = (self._sequence + 1) % 256

    def _receive(self) -> None:
        while True:
            try:
                data, address = self._socket.recvfrom(2048)
            except BlockingIOError:
                return
            if (
                address[0] != self.host
                or len(data) != 36
                or struct.unpack_from("<H", data)[0] != 36
                or struct.unpack_from("<I", data, 4)[0] != self._source
                or data[8:14] != self.target
                or struct.unpack_from("<H", data, 32)[0] != 45
            ):
                continue
            sent = self._outstanding.pop(data[23], None)
            if sent is not None and time.perf_counter() - sent < 3:
                self.last_ack_at = time.perf_counter()
                self.last_ack_rtt_ms = (self.last_ack_at - sent) * 1000
                self.acknowledgments += 1

    def diagnostics(self) -> dict[str, object]:
        age = None if self.last_ack_at is None else time.perf_counter() - self.last_ack_at
        return {
            "driver": "lifx",
            "frames_sent": self.frames_sent,
            "frames_coalesced": self.frames_coalesced,
            "acknowledgments": self.acknowledgments,
            "last_ack_age_ms": None if age is None else round(age * 1000),
            "last_ack_rtt_ms": self.last_ack_rtt_ms,
            "ack_timing_note": "Includes worker polling delay; not optical latency",
            "last_sent_at": self.last_sent_at,
            "reply_recent": age is not None and age < 3,
            "error": None if self._error is None else str(self._error),
            "visible_output_verified": False,
        }

    def _run(self) -> None:
        deadline = 0.0
        powered = False
        try:
            while True:
                self._receive()
                with self._condition:
                    while self._pending is None and not self._closed:
                        self._condition.wait(0.05)
                        self._receive()
                    if self._closed and self._pending is None:
                        return
                    delay = deadline - time.monotonic()
                    if delay > 0:
                        self._condition.wait(delay)
                        continue
                    payload = self._pending
                    if powered:
                        self._pending = None
                if not powered:
                    self._transmit(117, struct.pack("<HI", 65535, 0))
                    powered = True
                elif payload is not None:
                    self._transmit(102, payload)
                    self.frames_sent += 1
                deadline = time.monotonic() + 1 / self.max_hz
        except OSError as error:
            with self._condition:
                self._error = error

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify()
        self._thread.join(timeout=2)
        self._socket.close()
