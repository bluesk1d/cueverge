# LIFX point light feasibility — 2026-11-09

LIFX is a promising experimental single-point output for Cueverge at 10–20 Hz.
LAN connectivity and bounded streamed SetColor reception were exercised on both
physical bulbs. Optical timing, long-duration reliability, and production controller
network reachability remain unverified. No production output configuration changed.

## Hardware and transport

| Position | IPv4 | LAN target ID | Product | Firmware |
| --- | --- | --- | --- | --- |
| Left | 192.168.40.71 | d073d58b1a6d | 181, LIFX Mini | 4.5 |
| Right | 192.168.40.194 | d073d58b1e1f | 181, LIFX Mini | 4.5 |

Both advertise UDP port 56700. The official [product registry](https://github.com/LIFX/products)
identifies product 181 as color-capable, without multizone or matrix support,
and with 1500–9000 K white range on this firmware.
The supplied IPv6 addresses timed out on GetService from this Windows host;
this does not establish whether the cause is routing, firewall, or device support.
Use the verified IPv4 transport initially.

## Protocol findings

- [LAN communication](https://lan.developer.lifx.com/docs/communicating-with-device)
  uses UDP, with optional acknowledgments and state queries. No account or cloud
  credential is needed. Pin the discovered target ID as well as the configured IP.
- [Packet format](https://lan.developer.lifx.com/docs/packet-contents): 36-byte
  little-endian header; source ID and per-device wrapping sequence correlate replies.
- [SetColor (102)](https://lan.developer.lifx.com/docs/changing-a-device)
  carries 16-bit hue, saturation, brightness, kelvin and a millisecond transition
  duration. Total UDP payload is 49 bytes. Convert Cueverge RGB to HSV and scale
  to 16-bit HSB, retaining a configured white temperature. Color matching and
  brightness calibration require visual validation.
- LIFX's [published protocol guidance](https://github.com/LIFX/lifx-protocol-docs)
  recommends a maximum of 20 messages per second per device. Budget queries,
  power commands and retries within that total. A 60 Hz renderer should coalesce
  to the latest frame rather than accumulate a queue. Start at 15–18 Hz to leave
  room for health traffic, with 20 Hz available for controlled tests.
- ACK means a protocol reply, not measured LED emission. Set responses can contain
  the state before the change. Verify settled state with a later GetColor.
- These SetColor messages offer no shared presentation timestamp or atomic
  two-bulb commit. Simultaneous sends cannot guarantee simultaneous visible output.

## Experiment and evidence

The standalone [probe](../tools/lifx_probe.py) uses only Python's standard library.
Default operation reads state. `--stream` snapshots HSBK/power, runs smooth hue
sweeps at 12% brightness on both bulbs, then restores and queries both states.
Each stage lasts ten seconds: 10 Hz instantaneous, 20 Hz instantaneous, and
20 Hz with 50 ms transitions. ACKs are requested on every test frame; production
should generally send frames without ACKs and use sparse health checks.

Raw evidence is in [IPv4 streaming results](lifx-probe-stream-2026-11-09.json)
and [IPv6 probes](lifx-probe-ipv6-2026-11-09.json). Timing uses perf_counter;
the earlier read-only inspection file used a coarse Windows monotonic clock
and must not be used for latency estimates. An overlapping preliminary run was
discarded; the final retained streaming run starts from the original white state
and runs by itself. Sender cadence includes Windows scheduling jitter.

Final isolated run (1,000 SetColor packets total, every packet acknowledged):

| Cadence / transition | ACKs Left / Right | Median ACK RTT Left / Right | p95 Left / Right |
| --- | --- | --- | --- |
| 10 Hz / 0 ms | 100/100 each | 4.82 / 5.20 ms | 13.05 / 13.96 ms |
| 20 Hz / 0 ms | 200/200 each | 5.09 / 5.21 ms | 14.49 / 14.34 ms |
| 20 Hz / 50 ms | 200/200 each | 9.56 / 8.98 ms | 22.76 / 20.24 ms |

Worst ACK RTT was 52.45 ms. At 20 Hz, sender gaps had a median around 46.6 ms
and p95 around 62.1 ms, so this host did not deliver perfectly uniform 50 ms
intervals. Both bulbs' original powered-on, full-brightness 3508 K white states
were restored and verified by separate state queries.

This is a short protocol reception test, not proof that every frame was visibly
displayed. It does not measure color fidelity, LED refresh rate, inter-bulb optical
skew, or behavior under Wi-Fi contention. A camera/photodiode test and a longer
soak from the deployed controller are the next acceptance checks.

## Cueverge integration proposal

`lighting/sink.py` already defines resource-routed RGB `RenderedFrame` output.
A bulb can be represented as one RGB sample per independent lighting resource,
with Left and Right assigned separate spatial positions. Implement a `LifxUdpSink`
behind that existing interface, validating exactly one pixel. Keep transport
configuration (host, target ID, kelvin, brightness limit, cadence) in deployment
configuration, while show definitions continue using lighting resources.

`config.py` currently accepts only `DdpResourceConfig` in its deployment lighting
list; it would need a discriminated LIFX alternative. Runtime construction and
hardware test/calibration paths also currently assume DDP and need explicit LIFX
support. This investigation deliberately leaves those production paths unchanged.

Use a bounded latest-frame worker so send/response handling cannot block the
controller timeline. Reserve message budget for start/stop power and health,
avoid retries of stale color frames, expose last reply age and dropped/coalesced
frame counts, and define disconnect/stop behavior explicitly: a LIFX bulb retains
its last state rather than providing a Cueverge-specific realtime timeout.
Compare duration=0 with one-frame interpolation visually; interpolation can smooth
cadence but adds a changing response profile. Existing output timing offsets can
only compensate stable latency, not Wi-Fi jitter.

Reproduce from a host with LAN access:

```sh
python tools/lifx_probe.py 192.168.40.71 192.168.40.194
python tools/lifx_probe.py 192.168.40.71 192.168.40.194 --stream --seconds 10 --output result.json
```

The streaming command visibly changes the bulbs. Restoration is attempted in a
finally block and verified, but cannot be guaranteed after process termination
or loss of connectivity.
