# DDP output

The controller includes an opt-in unicast DDP v1 UDP sink:

- UDP port 4048 by default.
- Ten-byte headers with 32-bit channel offsets and 16-bit payload lengths.
- Four-bit packet sequence and push flag only on the final packet.
- RGB payload segmentation only on complete pixel boundaries.
- No optional DDP timecode because WLED does not consume it.

`CUEVERGE_DDP_ENABLED` defaults to false. Enabling it also requires an explicit
host. The implementation has exact packet tests but has not sent to the user's
Dig-Uno.

Before enabling output, validate:

- WLED version, configured LED count, RGB/RGBW layout, and color order.
- Unicast reachability from the NAS and sustainable frame rate.
- Brightness and power ceiling.
- Realtime ownership and behavior when packets stop.
- How to restore or relinquish to the prior WLED preset.

RGBW output and preset restoration are not implemented. Do not enable the sink
for an RGBW installation.
