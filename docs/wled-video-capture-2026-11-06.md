# Second NAS capture and visible-flash video

## Findings

The video confirms isolated bright blue flashes on the upper sloped gable and on the
horizontal roofline beneath it. The second capture contains valid Thanksgiving RGB
frames throughout the recording window. This supplies the visual symptom missing from
the first capture analysis; it strongly disfavors Cueverge sending corrupted RGB values
as the cause of this recorded symptom. It does not establish receiver ingress or the
electrical output waveform.

## Recordings

- `cueverge-wled2.pcap`: 51,174,104 bytes; SHA256
  `68bfc61adea014dd140756af4abbe3b0c44e5c8ae983a301b671ea97e0cbca25`.
- Capture span: 2026-11-06 23:26:40.142928–23:31:01.722675 UTC.
- `PXL_20261106_232809964.mp4`: 48.0095 seconds, 1,440 frames, 3840×2160,
  approximately 30 FPS. Filename suggests a 23:28:09.964 UTC start; MP4 creation metadata
  says 23:28:58 UTC, consistent with end-of-recording creation. These clocks were not
  independently synchronized. The capture covers the entire video under either
  timestamp interpretation, so the content comparison does not depend on frame-accurate
  wall-clock alignment.
- Originals were not modified. Local temporary copies/derived images were used.

## Video inspection

The zoomed section provides clear blue points against the normal warm LEDs. Representative
frames occur at offsets 34.005, 37.705, 38.205, and 45.941 seconds. The last example
contains multiple blue points along the sloped section and a blue point on the horizontal
section, with blue reflections visible below. These are localized color changes, not
simply a uniform change in the breathing brightness. A color-threshold pass selected
candidate frames from all 1,440 decoded frames; selected candidates were then visually
inspected. This selection is not an exhaustive flash counter and does not establish
precise physical pixel indices.

These examples are separated by subsecond to several-second intervals. A single
30-second cue boundary or 60-second loop boundary cannot explain their repetition.
Camera exposure/rolling shutter can affect the appearance and duration of LED flashes;
the operator independently reports seeing the flashes in person.

## Packet analysis

As in the first capture, Linux cooked v1 records identical packet observations at
multiple NAS networking stages. After the documented 1 ms full-IP-datagram duplicate
heuristic, each of the container and post-NAT paths contains exactly 13,088 datagrams,
forming 6,544 complete 605-pixel frames. Corresponding DDP packet CRCs agree across NAT.

| Check | Result |
| --- | --- |
| Captured packet observations | 52,352 |
| Duplicate observations excluded from assembly | 26,176 |
| Complete distinct frames per path | 6,544 |
| Missing parts, malformed DDP, unsupported/truncated packets | 0 |
| Sequence discontinuities | 0 |
| Datagrams per frame | 2 |
| Offsets / DDP datagram sizes | 0 / 1,450 bytes; 1,440 / 385 bytes |
| Pixel coverage | 0–604, RGB aligned |
| Median post-NAT frame interval | 39.887 ms |
| Maximum frame interval | 55.682 ms |
| Distinct RGB frame states | 40 |
| Exact matches to production-rendered saved Thanksgiving states | 6,544 / 6,544 |
| RGB bounds | R14–39, G8–28, B4–16 |
| Maximum consecutive channel change | 1 |

The exact-byte comparison used the same private Holidays revision 11 export and
production compiler/loader/evaluator as the first analysis, evaluated at 40 ms samples.
Every captured full RGB buffer belongs to the resulting set of 40 distinct buffers.
There is no blue-dominant, saturated blue, white, or bright red outlier in these bytes.
This test proves valid content, not an exact scene-time match for each capture timestamp.

Short approximately 4.2 ms intervals recur at 23:27:40.148, 23:28:40.153,
23:29:40.157 and 23:30:40.162 UTC, consistent with loop startup rendering. They do not
introduce an RGB jump. No sender gap approaches WLED's current 2.5-second timeout.
The capture filter cannot reveal other protocols or competing traffic that bypasses
the NAS. UDP checksum validation and receiver-port capture were not performed.

## Receiver driver clarification and next experiment

The operator confirmed a **QuinLED Dig-Uno**, using a QuinLED firmware build, with the
affected upper section connected to channel 2. The vendor's
[build definitions](https://github.com/intermittech/QuinLED-Firmware/blob/main/platformio_override.ini)
list GPIO 16 and GPIO 3 for the corresponding Dig-Uno configuration, consistent with
the observed settings. The exact historical build commit has not been established.

A subsequent 60-second read-only Peek session starting at 23:35:01.431 UTC returned
1,427 complete 605-pixel samples. All exactly matched valid Thanksgiving frames, with
no unexpected per-pixel colors. Bounds were R14–39/G8–28/B4–16. The session ended and
unsubscribed normally. It was recorded after the supplied video, not simultaneously;
short transient corruption between Peek samples remains possible.

Upstream [driver timing investigation #5264](https://github.com/wled/WLED/issues/5264)
documents differing RMT/I2S behavior and timings. The historical
[RMT corruption issue #4389](https://github.com/wled/WLED/issues/4389) concerns older
releases and is marked fixed; it is precedent for this failure category, not proof
that the same bug exists in this 16.0.1 installation. The proposed driver comparison
tests this hypothesis without assuming firmware corruption or defective wiring.

The earlier label "automatic" for WLED `drv=0` was incorrect. Matching
[v16.0.1 LED settings source](https://github.com/wled/WLED/blob/v16.0.1/wled00/data/settings_leds.htm)
identifies driver setting 0 as **RMT** and 1 as **I2S**. Both configured outputs currently
report 0. The upper physical bus is GPIO 3, pixels 377–604; packet split 480 lies within
that bus. The video alone cannot assign each flash to a byte offset, but the visual
problem spans multiple parts of the upper bus.

The next software experiment should compare the receiver's pixel buffer during visible
flashes, followed by a controlled RMT/I2S driver comparison once the controller board
and firmware build are identified. Preserve 605 pixels, 25 FPS, all scene data, bus
pins, color order, brightness and packet sizes. Change only the upper output's driver
if supported by that build, then restore it for an A/B/A comparison. This is a proposed
diagnostic change, not an applied fix or evidence of a known firmware defect.

If the receiver's internal buffer stays correct during visible flashes, prioritize the
RMT/peripheral output path and signal timing. A logic-analyzer capture of GPIO 3 versus
the signal arriving at the first upper LED can distinguish firmware/peripheral output
corruption from downstream signal degradation. If internal buffer data corrupts instead,
inspect receiver assembly and concurrent inputs first. The clean static streaming test
remains an essential control; this result does not justify assuming a wiring fault.

The investigation agent made no runtime, output-driver, scene, FPS, firmware, or
packet-size changes. The operator's subsequent driver experiment is recorded below.

## Operator RMT / I2S / RMT result

The operator tested I2S on the affected second output and observed the same type of
flicker at an estimated ten times the severity. Returning to RMT restored the original
severity. This is a qualitative visual A/B/A result, not a measured tenfold error rate.
RMT remains the baseline; I2S was not a successful fix.

This result weakens a defect specific to RMT and raises sensitivity to output waveform
timing, receiver output-buffer handling, and the LED input's timing/signal margin.
Both firmware behavior and downstream signal/LED decoding remain possible. A change
in output backend can change timing, memory handling and scheduling together, so the
test does not identify which of those changed the error rate. It does not implicate
Cueverge RGB generation or establish faulty wiring.

The upstream timing investigation documents different pulse timings for RMT and I2S.
Another [WS2813 investigation](https://github.com/wled/WLED/issues/5358) reports
chip-specific I2S timing sensitivity; that is an example of a possible mechanism,
not evidence that these installed pixels are WS2813 or share that defect.

Before changing LED type/timing settings, identify the actual upper and lower pixel
models. The current generic WS281x setting does not identify the installed chip.
Also record the channel-2 lead length/type. Do not select arbitrary protocol settings
or infer electrical failure from the A/B/A result alone. A native WLED animation using
the same low amber RGB range would be a stronger control than the previously stable
but otherwise unspecified native preset: a similar failure there would remove Cueverge
and streaming from the minimal reproduction. If that control remains clean, investigate
realtime-specific receiver scheduling/buffer behavior with simultaneous Peek and
output-waveform measurements. Neither control changes the Thanksgiving scene design.

## Installed strip identified: BTF-LIGHTING WS2815

The operator confirmed all house strips, on both channels, are BTF-LIGHTING WS2815.
The channel-2 cable length/type remains unknown. The earlier WS2813 example is not
the installed chipset and must not be treated as an identified defect.

A directly relevant historical [NeoPixelBus timing proposal #795](https://github.com/Makuna/NeoPixelBus/pull/795)
describes BTF-LIGHTING WS2815 flickering on a second RMT output and reports APA106
timing as an effective alternative. Its measurements and installation are not this
installation; in particular its I2S result differs from the operator's result here.

The matching upstream v16.0.1 [bus wrapper](https://github.com/wled/WLED/blob/v16.0.1/wled00/bus_wrapper.h)
selects Ws2812x timing for generic WS281x RGB output and Apa106 timing for APA106 RGB
output. Its classic ESP32 high-priority RMT backend uses the same timing classes from
NeoPixelBus, pinned to commit `1d7ff38f14d04a976f9c6e365c83a232bcba04fc` in platformio.ini.
The [pinned timing definitions](https://github.com/Makuna/NeoPixelBus/blob/1d7ff38f14d04a976f9c6e365c83a232bcba04fc/src/internal/methods/NeoEsp32RmtMethod.h)
specify the following nominal high/low pulse durations:

| RMT profile | Zero bit high/low | One bit high/low | Reset |
| --- | --- | --- | --- |
| Ws2812x | 400 / 850 ns | 800 / 450 ns | 300 us |
| Apa106 | 350 / 1350 ns | 1350 / 350 ns | 50 us |

These are source-defined timings, not oscilloscope measurements of the installed
QuinLED binary or the signal arriving at the strip. The profiles also differ in reset
duration and overall bit period, so this experiment cannot isolate a single pulse edge.
At 25 FPS, the normal interframe idle interval exceeds either reset duration.

Proposed diagnostic: keep RMT and change only upper channel 2's LED type from WS281x
to APA106, retaining GPIO 3, start 377, length 228, color order and all other settings.
Compare the same animated Thanksgiving scene for several minutes, then restore WS281x
to check whether the symptom returns. Keep the lower channel unchanged. The profile
is selected as a documented timing experiment, not because the installed LEDs are APA106.
This preserves logical RGB values, LED count and target FPS. No such setting change
has been made by the investigation agent.

A repeatable improvement would strengthen the timing-compatibility hypothesis and
justify investigating a WS2815-specific firmware timing correction. It would not by
itself distinguish nominal pulse timing from cable-induced pulse deformation. Failure
would motivate waveform comparison and a matched native-animation control rather than
more arbitrary LED-type changes. Cueverge requires no speculative RGB/render changes
based on the present capture evidence.

## APA106 timing experiment: no visible flickering

The operator reports zero visible flickering after selecting APA106 on the affected
output. In the preceding experiment I2S made flickering approximately ten times worse,
and returning to RMT restored baseline flickering. With RMT retained, the subsequent
APA106 profile test eliminated the observed symptom. This is a successful operator-
observed mitigation; observation duration and a subsequent WS281x/APA106 reversal have
not yet been reported. No new synchronized packet or waveform capture was supplied
for this setting.

Combined with the clean transmitted frames and receiver Peek samples, this strongly
supports an incompatibility or insufficient margin in the WS281x output waveform as
delivered to these WS2815 strips. The timing-profile change is the demonstrated control;
the exact physical mechanism remains unconfirmed. Nominal pulse widths, inter-bit/reset
behavior, cable deformation and strip input tolerances have not been measured separately.
It is not evidence of a Cueverge cue-boundary RGB corruption bug or proof of faulty wiring.

Practical disposition: retain the successful RMT + APA106 profile for upper channel 2
while verifying several 60-second Thanksgiving and breathing-diagnostic loops at the
original 605 pixels and 25 FPS. Preserve the lower channel, artistic content and RGB
pipeline. Save a WLED configuration backup and document the installed strip as WS2815
with APA106 selected for compatible output timing. Do not change the actual hardware
inventory to APA106. A brief WS281x → APA106 recheck, when convenient, would strengthen
causal attribution; it is not necessary to leave the installation flickering.

No Cueverge rendering/transport correction is justified by this result. A future
firmware-level correction would provide/select suitable WS2815 timing explicitly,
validated against the actual QuinLED build and output waveform rather than a speculative
scene-specific adjustment. Confirming long-duration stability remains the final field
verification step; no long-duration result is claimed here.

## Why streaming / Holidays could expose an issue not noticed at Halloween

Read-only inspection of current saved Halloween2026-ShowNight revision 71 found a
purple/blue upper ambient base (`#6500FF`) with breathing, cyan/purple moving overlays,
and scene cues including intentional sweeps and bright accents. Halloween2026 revision
57 also contains purple upper baselines and intentional flicker/sweep cues. Holidays
instead sends low warm values; the recorded Thanksgiving stream spans R14–39/G8–28/B4–16.
These current saved drafts are evidence of design differences, not historical captures
of the exact configuration or artifacts running on Halloween night.

Ranked explanations, all still hypotheses about the difference between shows:

1. Data-dependent decoding sensitivity. LED RGB bytes become a serial train of zero
   and one pulses. Different colors and brightness produce different pulse patterns.
   A marginal output can decode a repeated static pattern correctly yet fail for some
   animated patterns. A single high-bit error could turn a small blue value into a
   bright blue flash: e.g. 8 (`00001000`) → 136 (`10001000`). This is an illustrative
   mechanism, not an observed bit-error trace. APA106's successful timing change
   makes it plausible; exact failing bytes/edges remain unmeasured.
2. Visual detectability. Isolated blue flashes are conspicuous on a dim amber field;
   similar errors could be harder to notice amid intentional blue/purple moving accents.
   This explains possible non-detection, not proof that Halloween was also corrupt.
3. Realtime processing/cadence. DDP input and native effects drive updates through
   different code paths and scheduling. Network handling and gamma/brightness choices
   can change the work/timing and final bit patterns. Realtime gamma correction was
   disabled in the observed settings. Native-preset RGB values and output cadence were
   not matched to this capture. Halloween also used Cueverge, so streaming load alone
   cannot explain the show difference without an additional factor.
4. Historical configuration/conditions. Firmware, driver/profile settings or physical
   conditions may have differed; no before/after WLED configuration or signal trace
   establishes whether they did. Do not assume an upgrade or wiring change occurred.

The strongest narrow conclusion is a profile-sensitive output problem exposed by this
workload. Saying that transitions create invalid RGB, that streaming inherently corrupts
the data, or that Halloween must have been flickering unnoticed would exceed the evidence.
A future matched native-animation control should compare actual low amber output bytes,
not just a similar-looking UI color. It need not interrupt the successful APA106 setup
merely to obtain another visual reproduction.
