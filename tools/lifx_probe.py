"""Standalone LIFX LAN feasibility probe (stdlib only; no Cueverge runtime changes).

Default is read-only. --stream runs dim smooth color sweeps, then restores state.
ACK timing measures protocol handling, NOT physical light emission latency.
"""

import argparse
import json
import math
import secrets
import socket
import statistics
import struct
import time


class Bulb:
    def __init__(self, host):
        self.host = host
        self.sock = socket.socket(
            socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM
        )
        self.sock.settimeout(1)
        self.source = secrets.randbits(32) or 1
        self.sequence = 0
        self.target = bytes(8)

    def send(self, kind, payload=b"", ack=False):
        seq = self.sequence
        self.sequence = (seq + 1) % 256
        header = struct.pack(
            "<HHI8s6sBB8sHH",
            36 + len(payload),
            0x1400 | (0x2000 if not any(self.target) else 0),
            self.source,
            self.target,
            bytes(6),
            2 if ack else 0,
            seq,
            bytes(8),
            kind,
            0,
        )
        self.sock.sendto(header + payload, (self.host, 56700))
        return seq

    def receive(self, seq, expected):
        deadline = time.perf_counter() + 1
        while time.perf_counter() < deadline:
            data, _ = self.sock.recvfrom(2048)
            if (
                len(data) >= 36
                and struct.unpack_from("<H", data)[0] == len(data)
                and struct.unpack_from("<I", data, 4)[0] == self.source
                and data[23] == seq
                and struct.unpack_from("<H", data, 32)[0] == expected
            ):
                self.target = data[8:16]
                return data[36:]
        raise TimeoutError("no matching response")

    def query(self, kind, expected):
        start = time.perf_counter()
        payload = self.receive(self.send(kind), expected)
        return payload, (time.perf_counter() - start) * 1000

    def color(self, hsbk, duration=0, ack=False):
        return self.send(102, struct.pack("<B4HI", 0, *hsbk, duration), ack)


def summarize(values):
    ordered = sorted(values)
    return {
        "count": len(values),
        "median_ms": statistics.median(values) if values else None,
        "p95_ms": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]
        if values
        else None,
        "max_ms": max(values) if values else None,
    }


def inspect(bulb):
    service, _ = bulb.query(2, 3)
    version, _ = bulb.query(32, 33)
    firmware, _ = bulb.query(14, 15)
    state, _ = bulb.query(101, 107)
    timings = [bulb.query(101, 107)[1] for _ in range(20)]
    return {
        "host": bulb.host,
        "target": bulb.target.hex(),
        "service": struct.unpack("<BI", service),
        "vendor_product_version": struct.unpack("<III", version),
        "firmware_build_reserved_version": struct.unpack("<QQI", firmware),
        "hsbk": struct.unpack_from("<4H", state),
        "power": struct.unpack_from("<H", state, 10)[0],
        "label": state[12:44].rstrip(b"\0").decode(errors="replace"),
        "query_rtt": summarize(timings),
    }


def stream(bulbs, originals, hz, seconds, duration):
    # One socket per bulb; bounded outstanding sequence map and a shared cadence.
    import select

    pending = [{} for _ in bulbs]
    rtts = [[] for _ in bulbs]
    sent = [0 for _ in bulbs]
    gaps = []
    start = time.perf_counter()
    previous = None
    frames = int(hz * seconds)
    for frame in range(frames):
        due = start + frame / hz
        while True:
            remaining = due - time.perf_counter()
            if remaining <= 0:
                break
            readable, _, _ = select.select([b.sock for b in bulbs], [], [], remaining)
            for sock in readable:
                index = next(i for i, b in enumerate(bulbs) if b.sock is sock)
                data, _ = sock.recvfrom(2048)
                if (
                    len(data) >= 36
                    and struct.unpack_from("<I", data, 4)[0] == bulbs[index].source
                    and struct.unpack_from("<H", data, 32)[0] == 45
                ):
                    timestamp = pending[index].pop(data[23], None)
                    if timestamp is not None:
                        rtts[index].append((time.perf_counter() - timestamp) * 1000)
        now = time.perf_counter()
        if previous is not None:
            gaps.append((now - previous) * 1000)
        previous = now
        for index, bulb in enumerate(bulbs):
            # Smooth hue sweep at 12% brightness; no flashing or power cycling.
            hue = round(((frame / hz / 8 + index * 0.12) % 1) * 65535)
            timestamp = time.perf_counter()
            seq = bulb.color((hue, 65535, 7864, originals[index]["hsbk"][3]), duration, True)
            pending[index][seq] = timestamp
            sent[index] += 1
    deadline = time.perf_counter() + 1
    while time.perf_counter() < deadline:
        readable, _, _ = select.select([b.sock for b in bulbs], [], [], 0.05)
        for sock in readable:
            index = next(i for i, b in enumerate(bulbs) if b.sock is sock)
            data, _ = sock.recvfrom(2048)
            if (
                len(data) >= 36
                and struct.unpack_from("<I", data, 4)[0] == bulbs[index].source
                and struct.unpack_from("<H", data, 32)[0] == 45
            ):
                timestamp = pending[index].pop(data[23], None)
                if timestamp is not None:
                    rtts[index].append((time.perf_counter() - timestamp) * 1000)
    return {
        "hz": hz,
        "seconds": seconds,
        "duration_ms": duration,
        "sender_gaps": summarize(gaps),
        "bulbs": [
            {
                "host": b.host,
                "sent": sent[i],
                "ack_rtt": summarize(rtts[i]),
                "unacknowledged": sent[i] - len(rtts[i]),
            }
            for i, b in enumerate(bulbs)
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hosts", nargs="+")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30:
        parser.error("seconds must be 1..30")
    bulbs = [Bulb(host) for host in args.hosts]
    result = {
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "devices": [],
        "streams": [],
    }
    try:
        for bulb in bulbs:
            try:
                result["devices"].append(inspect(bulb))
            except (OSError, TimeoutError) as error:
                result["devices"].append({"host": bulb.host, "error": str(error)})
        if args.stream and all("hsbk" in d for d in result["devices"]):
            try:
                for bulb, original in zip(bulbs, result["devices"], strict=False):
                    bulb.color((*original["hsbk"][:2], 7864, original["hsbk"][3]))
                    time.sleep(0.1)
                    bulb.receive(bulb.send(117, struct.pack("<HI", 65535, 500), True), 45)
                time.sleep(0.6)
                for hz, duration in [(10, 0), (20, 0), (20, 50)]:
                    result["streams"].append(
                        stream(bulbs, result["devices"], hz, args.seconds, duration)
                    )
                    time.sleep(0.2)
            finally:
                result["restoration"] = []
                for bulb, original in zip(bulbs, result["devices"], strict=False):
                    try:
                        bulb.receive(bulb.color(original["hsbk"], ack=True), 45)
                        time.sleep(0.1)
                        bulb.receive(
                            bulb.send(117, struct.pack("<HI", original["power"], 0), True), 45
                        )
                        time.sleep(0.1)
                        state, _ = bulb.query(101, 107)
                        result["restoration"].append(
                            {
                                "host": bulb.host,
                                "verified": list(struct.unpack_from("<4H", state))
                                == list(original["hsbk"])
                                and struct.unpack_from("<H", state, 10)[0] == original["power"],
                            }
                        )
                    except OSError as error:
                        result["restoration"].append({"host": bulb.host, "error": str(error)})
    finally:
        for bulb in bulbs:
            bulb.sock.close()
        output = json.dumps(result, indent=2)
        print(output)
        if args.output:
            from pathlib import Path

            Path(args.output).write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
