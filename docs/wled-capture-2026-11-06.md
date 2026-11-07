# NAS DDP capture analysis — 2026-11-06

## Conclusion

The supplied NAS capture contains no corrupted RGB frames, incorrect packet offsets,
missing frame parts, or sequence discontinuities after accounting for repeated capture
observations. Every one of 6,635 reconstructed frames exactly matches a valid frame
from the production-rendered saved Thanksgiving scene. This is byte equality, not just
RGB bounds or checksums. It does not prove reception or correct electrical output.
Confirmation that flashes were visible during this capture remains needed.

## Input and method

- Input: `cueverge-wled.pcap`, 51,885,724 bytes.
- SHA256: `d09db6b9cfba2341df31fb1143a13353aa7db729e418dea85d767443459cbb76`.
- Time span: 2026-11-06 22:59:30.824610–23:03:56.009447 UTC, approximately
  2:59:30–3:03:56 PM Pacific (265.185 seconds).
- Format: classic PCAP, Linux cooked v1 (no interface IDs).
- The original capture remains unchanged; analysis uses a local temporary copy.
- The existing analyzer was corrected to recognize consecutive identical full IP
  datagrams observed within 1 ms on the same stream path. This is explicitly a capture
  deduplication heuristic; actual network duplication cannot be distinguished solely
  by this capture. Repeated observations remain recorded in the diagnostic JSONL.
- Production compiler/loader/evaluator loaded the private export of Holidays revision
  11. Rendering at 40 ms samples plus boundary probes yielded 40 distinct frame byte
  strings. All 40 occur in the capture; all captured frames belong to that exact set.
  This establishes valid frame content, not precise scene-clock phase for each packet.

## Packet and timing findings

The 53,080 observations comprise two matching paths, each observed twice:

1. Container source `172.29.44.2:52556` to WLED `192.168.40.91:4048`.
2. NAS source after NAT `192.168.1.2:52556` to the same WLED endpoint.

Each path has 13,270 distinct observed datagrams: 6,635 frames, two datagrams each.
Pre-NAT and post-NAT DDP packet bytes match for every corresponding datagram.
No other DDP sender appears in the NAS-visible traffic. A competing sender whose
traffic does not traverse the NAS is not excluded.

| Check | Result at NAS egress |
| --- | --- |
| DDP destination | 1 |
| First datagram | 1,450 bytes, offset 0, 1,440 RGB bytes, no PUSH |
| Second datagram | 385 bytes, offset 1,440, 375 RGB bytes, PUSH |
| Pixel coverage | Exactly 0–604, RGB aligned |
| Missing parts / malformed packets / fragmentation | None observed |
| Sequence | Continuous modulo 16, including zero |
| Packet ordering errors | None observed |
| Median frame interval | 39.835 ms |
| 99th percentile frame interval | 41.544 ms |
| Maximum frame interval | 50.191 ms (50.190 ms at post-NAT timestamps) |
| Median gap within a frame | 17 microseconds |
| Maximum gap within a frame | 1.239 ms |
| RGB bounds | R14–39, G8–28, B4–16 |
| Unexpected frame bytes | None |
| Maximum consecutive per-channel change | One 8-bit step |

Five short approximately 4.1 ms frame intervals occur about 60 seconds apart, at
22:59:50.708, 23:00:50.713, 23:01:50.718, 23:02:50.724, and 23:03:50.729 UTC.
Both frames in each pair are identical (CRC32 1265428727). This is consistent with
the controller loop restart/startup render path and does not introduce a color jump.
Production evaluation also gives identical endpoint frames at 29999/30000 ms and
59999/0 ms. The capture provides no evidence of a transmitted boundary flash.

The analyzer initially reported incomplete frames because each duplicate PUSH reset
its coverage a second time. This was a confirmed diagnostic-tool defect, not a Cueverge
streaming defect. Regression tests now cover repeated observations for every supported
link type and byte order. Fifty-two targeted tests pass, along with lint, formatting
and type checking of the changed analyzer.

## Receiver inspection (read only, after capture)

WLED `192.168.40.91` was reachable directly. `/json/info` reports version 16.0.1,
ESP32, 605 RGB pixels, live DDP, source `192.168.1.2`, and 25 LED FPS. Current
settings were read without changing playback or configuration:

- Lower output: GPIO 16, pixels 0–376, 377 pixels, RMT driver setting (`drv=0`).
- Upper output: GPIO 3, pixels 377–604, 228 pixels, RMT driver setting (`drv=0`).
- Both buses report LED type 22, color order 0, no reversal or skip.
- Realtime offset 0, main-segment-only disabled, LED mapping respected.
- DMX address 1, sequence rejection disabled, gamma correction disabled for realtime.
- Realtime timeout setting 25, corresponding to 2,500 ms.
- Current WLED segment boundary at 465 differs by one from Cueverge's saved 464
  logical boundary. Global DDP addressing with main-segment-only disabled provides
  no evidence that this difference causes the flashes; do not change it speculatively.

The timeout unit and conditional sequence rejection are verified against the matching
[v16.0.1 configuration source](https://github.com/wled/WLED/blob/v16.0.1/wled00/cfg.cpp)
and [DDP handler](https://github.com/wled/WLED/blob/v16.0.1/wled00/e131.cpp).
Sequence-zero hardening is not a supported fix for this installation's current symptom:
the receiver is not applying sequence rejection at all. The NAS send gaps are far below
its realtime timeout; receiver-side stalls remain a separate unmeasured possibility.

An additional 15-second read-only WebSocket Peek subscription produced 357 complete
605-pixel samples. Every sample exactly matched a valid Thanksgiving frame, with no
unexpected pixel colors. The subscription was disabled and closed afterward. This
sample was taken after the PCAP interval, without a correlated visual observation.
The matching [WebSocket implementation](https://github.com/wled/WLED/blob/v16.0.1/wled00/ws.cpp)
reads `strip.getPixelColor`; it does not measure the physical signal or emitted light.
`/json/live` returned 501 on this build, so WebSocket Peek was used instead.

## Remaining hypotheses and next verification

1. WLED output-driver/bus-buffer timing during realtime reception remains a software
   hypothesis. Correct internal pixel samples do not establish correct DMA/peripheral
   output. The physical bus begins at pixel 377, distinct from packet split 480; determine
   whether flashes follow the entire upper bus or only its latter portion.
2. Loss, reordering, or other traffic after the NAS remain unmeasured. A mirrored capture
   at WLED's switch port would distinguish delivery problems from receiver behavior.
3. An electrical signal issue remains possible, but is not established by this capture.
   The clean static test must remain part of the comparison; do not assume wiring fault.
4. Cueverge boundary RGB corruption and shared-buffer serialization races are strongly
   disfavored for this recorded interval. Timing-only anomalies would still not explain
   colors absent from both captured frames and sampled receiver buffers.

First correlate a visible flash with simultaneous sender capture and receiver Peek.
If Peek corrupts while the captured payload remains valid, inspect receiver pixel writes,
other inputs, and assembly. If Peek remains correct through visible flashes, inspect
the selected ESP32 bus drivers and output signal; a logic-analyzer trace can separate
firmware output corruption from downstream signal corruption.

A later controlled packet-boundary experiment can set `max_payload_bytes=1131` (377 RGB
pixels), preserving 605 pixels, two datagrams, 25 FPS, and every rendered RGB value.
This aligns the packet split with the physical bus boundary and tests packet-split
sensitivity; it is not a proposed production fix. No output, driver, FPS, firmware,
scene, or packet-size changes were made during this analysis.
