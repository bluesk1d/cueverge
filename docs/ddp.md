# DDP output

The controller includes an opt-in unicast DDP v1 UDP sink:

- UDP port 4048 by default.
- Ten-byte headers with 32-bit channel offsets and 16-bit payload lengths.
- Four-bit packet sequence and push flag only on the final packet.
- RGB payload segmentation only on complete pixel boundaries.
- No optional DDP timecode because WLED does not consume it.

`CUEVERGE_DDP_ENABLED` defaults to false. Enabling it requires an explicit host
and expected pixel count. RGB channel values are sent unscaled by default.
`CUEVERGE_DDP_BRIGHTNESS_LIMIT` can optionally scale output for artistic or
installation-specific reasons; Cueverge does not treat it as an electrical
safety control.

Before enabling output, validate:

- WLED version, configured LED count, RGB/RGBW layout, and color order.
- Unicast reachability from the NAS and sustainable frame rate.
- Realtime ownership and behavior when packets stop.
- How to restore or relinquish to the prior WLED preset.

The downstream controller and physical installation own electrical
provisioning, protection, and safe operating limits. Cueverge assumes an
enabled output target has already been configured appropriately.

RGBW output is not implemented. Do not enable the sink for an RGBW installation.

## 2026-10-08 hardware spike

Read-only inspection and an authorized controlled test established:

- WLED 0.16.0.1 on ESP32 with 605 RGB pixels.
- Two physical outputs: 377 and 228 pixels, represented as one linear range.
- DDP input accepted all 605 pixels and identified the realtime source as DDP.
- A dim three-pixel frame at 20 FPS was received successfully.
- 300 all-black frames were sent at exactly 30.00 FPS over ten seconds with
  zero sender deadline misses.
- WLED relinquished realtime mode within five seconds and returned to the exact
  prior off state after both tests.

WLED's instantaneous JSON FPS field reported values different from commanded
cadence and is treated as diagnostic only. Packet loss and visible update
latency were not measured.
