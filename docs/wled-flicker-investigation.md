# WLED flicker investigation

## Status and evidence

Follow-up: [analysis of the supplied NAS capture](wled-capture-2026-11-06.md) verifies
6,635 transmitted Thanksgiving frames and documents direct WLED configuration and
receiver Peek observations. These supersede the earlier unknowns where stated.

No confirmed renderer corruption or software root cause for the visible flashes.
The supplied observations are hardware evidence; the original tests below are synthetic
software evidence. A subsequent read-only inspection of the connected controller and
Holidays revision 11 supplied the actual saved scenes and roofline mapping (see follow-up
below). Deployed DDP payload configuration, WLED firmware version, and a network capture
remain unverified.
Neither named scene was edited. Default packet dimensions below must be checked against
the deployed `max_payload_bytes`, destination, brightness limit and pixel count.

## Production pipeline

1. `controller/app.py::_configured_components` selects `DdpUdpSink` from deployment
   outputs or legacy DDP settings; without either it selects memory output.
   `config.py` defines these settings. Deployment outputs use `FrameSinkRouter`.
2. `controller/runtime.py::_start_scene` creates a `ControllerClock` for controller-timed
   playback. `timing/controller.py::estimate` uses rounded absolute monotonic elapsed
   milliseconds, clamped to duration. There is no negative delta after wrapping.
3. `_run_render_loop` calls `render_frame` each deadline (40 ms at 25 FPS). Missed
   deadlines increment a counter and are skipped, rather than queued for catch-up.
4. `render_frame` estimates time, schedules completion, clamps completion rendering to
   duration minus one, applies lighting/output delays, then `_render_lighting_output`
   calls `lighting/evaluation.py::evaluate_lighting_output`.
5. Timeline output uses `LightingTimeline.render`. Each frame starts with a fresh black
   list or copy of ambient. Cues sort by resolved target precedence then declaration
   order; later writes win for replace blending. Loader resolves target indices and
   precedence. Add/max/multiply blending is explicit. Spatial sequences, if present,
   overlay the timeline. Legacy effects run only when no timeline is configured.
6. Active interval is `[start, start + duration)`. `_render_cue` evaluates absolute local
   time, interpolating over duration minus one, applying brightness and fades, then
   clamping channels. At 29999 the outgoing transition reaches its endpoint; at 30000
   only the incoming cue is active. Matched endpoint parameters are continuous.
7. Rendering returns a new tuple of RGB tuples. `_send_lighting_frames` builds frozen
   `RenderedFrame` objects; `_send_output_frame` calls the sink synchronously, then
   updates monitoring. No await, output worker, coalescing, reused mutable framebuffer,
   or render/send concurrency exists on this path.
8. `lighting/ddp.py::send` creates brightness-scaled tuples; `encode_ddp_frame` copies
   them into immutable bytes and serializes every datagram before the first send.
   Headers are big endian, ten bytes, byte offsets, RGB-aligned payloads; PUSH appears
   only on the final packet. Default 605-pixel output is 1815 RGB bytes: datagrams
   1450 and 385 bytes, offsets 0 and 1440, pixels 0–479 and 480–604 (zero based).
   No E1.31 universes are used. Default packets fit a normal 1500-byte IPv4 MTU.
9. UDP `sendto` is synchronous, with errors/short sends checked. Success confirms local
   submission only. There are no acknowledgements, retries, reconnect handshake, or
   receiver resynchronization. Each subsequent frame refreshes every pixel.
10. At completion `_schedule_completion` schedules `_apply_completion`; loop policy
    calls `start_scene` again, creating a new instance/clock/render task. The completion
    tick holds the last valid millisecond. This can extend the loop and introduce a
    scheduling jump, but is not a color generator. Ambient is possible during startup
    or on outputs not covered by a scene; inherited ambient can show through cue gaps.

Every-frame operations include sorting, rendering, allocation and full transmission for
both solid and transition cues. Transitions add interpolation; there is no special
transition transport path, deduplication, packet count change, or boundary buffer reset.
Only scene completion/start changes lifecycle state. A slow render can cause animation
steps, not arbitrary RGB values by itself.

## Ranked hypotheses (provisional)

1. Receiver-side realtime rendering/assembly interaction or competing stream: strongest
   remaining software category, not a confirmed defect. Fits valid previews and output
   success counters; varying frames expose stale/partial display that static frames hide.
   Capture traffic from ALL senders and inspect the exact firmware. Amber-only dropped
   or reordered packets should still be amber: this category needs additional evidence
   to explain unexpected blue/white.
2. Datagram loss/reordering: two-packet frames permit mixed frame ages. Correlation with
   pixels 480–604 would support it, but physical mapping is unknown. Clean static output
   does not rule this out because every static payload is identical. Does not explain
   new channel values absent from transmitted frames.
3. Actual scene gaps, precedence, inherited ambient or loop startup: plausible for
   Thanksgiving until its compiled scene is inspected. Synthetic adjacent transitions
   are clean; a brightness jump is distinct from corrupted colors. Check every affected
   pixel's active cues and ambient at the actual compensated output timestamp.
4. Sender serialization/buffer race: low likelihood on inspected path. Fresh immutable
   tuples/bytes, synchronous sends and exact receiver reconstruction argue against it.
5. Deadline skips/loop restart: credible explanation for animation jumps, weak explanation
   for multicolored corruption. Measure actual send gaps against WLED realtime timeout.

External reference: upstream [WLED DDP handler](https://github.com/Aircoookie/WLED/blob/main/wled00/e131.cpp)
writes arriving packets directly to realtime pixels and PUSH requests display; it does
not require receipt of every part of a frame. It treats sequence zero as unused.
The sender cycles 0–15, disabling checking periodically: confirmed sequence weakness,
not a proven cause. Proposed small hardening is initialization at 1 and wrapping 15→1,
with explicit wrap tests. Do not claim this fixes flashes or implement frame retransmit
without capture evidence. Upstream main is not evidence of the installed version.

## Diagnostics and reproduction

Run `.venv/bin/pytest tests/unit/test_wled_stream_boundaries.py tests/unit/test_ddp.py
tests/unit/test_controller_clock.py tests/unit/test_lighting_timeline.py`.
New tests use 96 disjoint targets (192 transitions), production evaluation and DDP send,
25 FPS timestamp samples for three loops, extra millisecond boundary samples, exact
byte reconstruction, retained-frame immutability, rendering during a send callback,
and real controller completion/restart. They use a deterministic schedule, not wall-clock
network timing; the receiver models offset/PUSH assembly, not ESP firmware internals.
There is no asynchronous production sender to test; reentrant rendering and retained
snapshots cover the equivalent ownership concern without inventing one.

Temporary opt-in diagnostics: set `diagnostic_frames=100` on the selected `DdpUdpSink`
in an instrumented harness and enable DEBUG for `cueverge.lighting.ddp`. This records
instance/resource/position, sequence, monotonic send start/end, pre-sink framebuffer CRC,
and each packet's length/offset/CRC. It stops after the budget of successful frames.
Default zero does no checksum work. No RGB dumps. This is a programmatic hook, not a
new environment setting. Boundaries must be selected by the harness; the sink cannot
infer cue IDs or loop counts. Associate instance IDs with scene IDs and count restarts.
For a selected boundary capture, record active cue IDs using the same half-open predicate
at runtime's compensated output position, plus render start/end, skipped deadlines,
stale count, output errors and scene completions from runtime status. These additional
runtime fields are a recommended harness extension, not implemented logging.

Capture UDP destination port 4048 at sender and receiver network ingress, with timestamps,
for a short observed flash interval. Compare reassembled byte offsets to the CRC trace;
include other source IPs. Sender capture alone cannot prove receiver arrival/display.
Do not continuously log every frame in production. Capture firmware version, realtime
timeout, realtime override, DMX address/mapping, payload configuration and roofline
pixel indices before interpreting a packet-boundary correlation.

## Verification and fix decision

Replay `diagnostic-static-amber-605` in Holidays (retained ID now names the breathing
diagnostic) and Thanksgiving `scene-928886bc-1772-4e3f-bee0-bee8fc71ccd9` unchanged.
Compare static diagnostic separately using a saved/exported copy if available. Keep
605 pixels and 25 FPS. Observe several loops and mark visible flashes/jumps with time.
Check 29999/30000 and 59999/completion/new-instance-zero as well as nonboundary frames.
Record affected physical indices, especially around 479/480. Compare frame CRCs and
packet payloads across the same interval. Zero blue applies only to the synthetic
`#FF8000` fixture; the real diagnostic uses `#B66A24` and legitimately includes blue.

If sender bytes are wrong, fix the first renderer/serializer divergence and add a
fixture reproducing the deployed scene. If ingress bytes are missing/reordered, isolate
transport/receiver assembly and test a targeted correction. If ingress bytes are correct
but light output is wrong, instrument the exact receiver firmware's pixel writes and
display scheduling. If a timeout releases realtime mode, measure the gap and fix its
source. Do not change FPS or artistic cues to conceal it. A fix is verified only when
captures and repeated visual runs agree; zero output errors alone is insufficient.

## Follow-up: connected controller and real-scene replay

Read-only inspection on 2026-11-06 found Holidays revision 11 loaded and Thanksgiving
playing at 25 FPS. The snapshot had zero output errors, stale frames and skipped render
deadlines; these are counters at the observed moment, not proof of correct reception.
The resource `wled-001` has a 90 ms installation lighting delay. Saved segment ranges
(zero based, end exclusive) are upper-eave 377–464, upper-gable-right 464–536, and
upper-gable-left 536–605. The default packet split at 480 is therefore inside the
upper-right gable, not at the beginning of the upper strand.

The actual saved scenes were exported privately, compiled locally, and replayed through
the real ControllerRuntime and DdpUdpSink using a mock socket. At 25 FPS timestamp
samples plus boundary probes, three loops verified 4,531 Thanksgiving frames and 4,519
diagnostic frames. Every serialized frame reconstructed exactly. Channel ranges before
any deployed sink brightness limit were Thanksgiving R14–39/G8–28/B4–16 and diagnostic
R29–40/G17–23/B6–8. Both scenes apply a 0.6 output brightness limit. No live scene or
playback changes were made. This replay uses locally compiled saved content, not a
download of the immutable deployed artifacts; no separate controller ambient looks or
deployment output settings were imported.

Reusable tooling is in `lighting/diagnostics.py`:

```sh
# Offline saved draft, no hardware commands; output must not already exist.
.venv/bin/python -m cueverge.lighting.diagnostics replay saved-draft.json boundaries.jsonl --loops 3

# Analyze a capture made by tcpdump. No hardware commands.
.venv/bin/python -m cueverge.lighting.diagnostics analyze cueverge-wled.pcap packets.jsonl

# Optional local mock receiver. It sees only packets addressed to this host/port.
.venv/bin/python -m cueverge.lighting.diagnostics receive receiver.jsonl --seconds 30
```

Replay emits boundary records with scene/instance/loop/active cue IDs, framebuffer and
packet CRCs, offsets, sizes, sequence and render/send timing. It verifies every frame,
not only logged boundary samples. Its manual clock intentionally cannot measure real
render deadline skips. All byte comparison uses the default sink brightness limit 1.

For the NAS operator, capture on the host while the symptom is visible:

```sh
sudo tcpdump -i any -nn -s 0 -B 4096 -U -w /tmp/cueverge-wled.pcap 'udp port 4048'
```

Capture approximately three minutes, press Ctrl+C, retain tcpdump's final dropped-packet
statistics, and record flash times and scene changes. Include the NAS UTC time (`date -u`)
so observations can be aligned. Capture both scenes if practical. A host capture shows
local transmission, not receiver arrival. It also captures competing senders only if
their traffic traverses the NAS. No firmware or artistic changes are required.

The analyzer supports classic PCAP, Ethernet/VLAN, raw IPv4 and Linux cooked v1/v2
headers. It separates cooked interfaces/directions to avoid treating NAS bridge copies
as one stream. It records source/destination, packet CRC, sequence, PUSH, overlapping
coverage, missing bytes on PUSH and displayed-buffer channel bounds. Incomplete initial
frames can simply mean capture began midframe. Sequence zero, same-sequence late packets,
missing PUSH packets and repeated sequence values remain ambiguous; results are evidence
to inspect, not automatic loss diagnoses. It explicitly counts unsupported/truncated
packets (including fragmentation/IPv6), does not validate UDP checksums, and does not
emulate WLED sequence rejection, realtime overrides or physical display scheduling.
No continuous RGB buffer dumps are produced. Malformed and missing-packet tests cover
the receiver; PCAP tests exercise all supported link types and both byte orders.
