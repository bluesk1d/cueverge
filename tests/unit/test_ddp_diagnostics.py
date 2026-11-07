import struct
from pathlib import Path

import pytest

from cueverge.lighting.ddp import encode_ddp_frame
from cueverge.lighting.diagnostics import DdpReceiver, analyze_pcap


def test_receiver_checks_every_pixel_and_reports_missing_packet() -> None:
    pixels = tuple((i % 256, (i // 256) % 256, 17) for i in range(605))
    first, last = encode_ddp_frame(pixels, sequence=1)
    receiver = DdpReceiver(605)
    result = receiver.accept(last)
    assert result["missing_bytes"] == 1440
    receiver.accept(first)
    result = receiver.accept(last)
    assert result["missing_bytes"] == 0
    assert bytes(receiver.pixels) == bytes(c for pixel in pixels for c in pixel)


def test_sequence_change_does_not_count_previous_frame_coverage() -> None:
    old = encode_ddp_frame(((1, 2, 3),) * 605, sequence=15)
    new = encode_ddp_frame(((4, 5, 6),) * 605, sequence=0)
    receiver = DdpReceiver(605)
    receiver.accept(old[0])
    assert receiver.accept(new[1])["missing_bytes"] == 1440
    assert receiver.pixels[:3] == bytes((1, 2, 3))
    assert receiver.pixels[-3:] == bytes((4, 5, 6))


@pytest.mark.parametrize(
    "packet", [b"", b"\x40" * 10, encode_ddp_frame(((1, 2, 3),) * 606, sequence=1)[-1]]
)
def test_receiver_rejects_malformed_or_out_of_range_packets(packet: bytes) -> None:
    with pytest.raises(ValueError):
        DdpReceiver(605).accept(packet)


@pytest.mark.parametrize("link", [1, 101, 113, 276])
@pytest.mark.parametrize("endian", ["<", ">"])
@pytest.mark.parametrize("copies", [1, 2])
def test_pcap_reconstructs_actual_ddp_packets(
    tmp_path: Path, link: int, endian: str, copies: int
) -> None:
    pixels = tuple((i % 256, i // 256, 7) for i in range(605))
    packets = encode_ddp_frame(pixels, sequence=3)
    capture = bytearray(struct.pack(endian + "IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, link))
    for packet in packets:
        udp = struct.pack("!HHHH", 50000, 4048, len(packet) + 8, 0) + packet
        ip = (
            struct.pack(
                "!BBHHHBBH4s4s",
                0x45,
                0,
                len(udp) + 20,
                0,
                0,
                64,
                17,
                0,
                bytes((10, 0, 0, 1)),
                bytes((10, 0, 0, 2)),
            )
            + udp
        )
        prefix = {
            1: bytes(12) + b"\x08\x00",
            101: b"",
            113: bytes(14) + b"\x08\x00",
            276: b"\x08\x00" + bytes(18),
        }[link]
        data = prefix + ip
        for copy in range(copies):
            capture.extend(struct.pack(endian + "IIII", 100, 123456 + copy, len(data), len(data)))
            capture.extend(data)
    path = tmp_path / "capture.pcap"
    path.write_bytes(capture)
    result = analyze_pcap(path, tmp_path / "trace.jsonl")
    assert result["ddp"] == 2
    assert result["pushes"] == 1
    assert result["incomplete_pushes"] == 0
    assert result["unsupported_or_truncated"] == 0
    assert result["duplicate_observations"] == 2 * (copies - 1)


def test_pcap_rejects_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "capture.pcapng"
    path.write_bytes(bytes(24))
    with pytest.raises(ValueError, match="classic PCAP"):
        analyze_pcap(path, tmp_path / "trace.jsonl")
