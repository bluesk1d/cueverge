import struct

import pytest

from cueverge.lighting.ddp import DDP_PUSH, DDP_VERSION_1, DdpUdpSink, encode_ddp_frame
from cueverge.lighting.sink import RenderedFrame


def test_encodes_exact_single_ddp_packet() -> None:
    packets = encode_ddp_frame(
        ((1, 2, 3), (254, 255, 0)),
        sequence=5,
        destination_id=1,
    )

    assert packets == (
        struct.pack("!BBBBIH", DDP_VERSION_1 | DDP_PUSH, 5, 0, 1, 0, 6)
        + bytes((1, 2, 3, 254, 255, 0)),
    )


def test_segments_on_pixel_boundaries_and_pushes_only_final_packet() -> None:
    first, second = encode_ddp_frame(
        ((1, 2, 3), (4, 5, 6), (7, 8, 9)),
        sequence=15,
        max_payload_bytes=7,
    )

    assert first[:10] == struct.pack("!BBBBIH", DDP_VERSION_1, 15, 0, 1, 0, 6)
    assert first[10:] == bytes((1, 2, 3, 4, 5, 6))
    assert second[:10] == struct.pack("!BBBBIH", DDP_VERSION_1 | DDP_PUSH, 15, 0, 1, 6, 3)
    assert second[10:] == bytes((7, 8, 9))


@pytest.mark.parametrize("sequence", [-1, 16])
def test_rejects_invalid_sequence(sequence: int) -> None:
    with pytest.raises(ValueError, match="sequence"):
        encode_ddp_frame(((0, 0, 0),), sequence=sequence)


def test_rejects_empty_frames() -> None:
    with pytest.raises(ValueError, match="at least one pixel"):
        encode_ddp_frame((), sequence=0)


class FakeSocket:
    def __init__(self) -> None:
        self.packets: list[bytes] = []

    def sendto(self, packet: bytes, address: tuple[str, int]) -> int:
        assert address == ("wled.local", 4048)
        self.packets.append(packet)
        return len(packet)

    def close(self) -> None:
        pass


def test_sink_enforces_pixel_count_and_brightness_limit() -> None:
    fake_socket = FakeSocket()
    sink = DdpUdpSink(
        "wled.local",
        expected_pixel_count=2,
        brightness_limit=0.25,
        _socket=fake_socket,  # type: ignore[arg-type]
    )
    frame = RenderedFrame("scene", 0, ((255, 128, 4), (0, 0, 0)))

    sink.send(frame)
    assert fake_socket.packets[0][10:] == bytes((64, 32, 1, 0, 0, 0))

    with pytest.raises(ValueError, match="expected 2"):
        sink.send(RenderedFrame("scene", 0, ((0, 0, 0),)))
