# Stationary point lights

Use **Stationary point light** in the installation layout's Add menu. Place one
point and assign a saved one-pixel output resource. Each lamp has an independent
resource and spatial position; lighting compositions sample their RGB color at
that position. A point light has one sample at index zero and no traversal.
The first supported hardware brand is LIFX, using IPv4 UDP LAN control.

Create one `pixel_output` installation resource with `pixel_count: 1` per bulb,
and bind your scene's lighting output to that resource. The existing RGB lighting
timeline, surface mapping, brightness limits and timing controls apply.
Transport configuration stays in the controller's output deployment JSON.

The [two-bulb example](../examples/lifx-outputs.json) uses the investigated Left
and Right lamps with a 25% output cap. Set `CUEVERGE_OUTPUT_CONFIG` to its path
in the controller filesystem. Merge entries with any existing DDP outputs;
the resource names must match the show installation. This example is not enabled
automatically. Hardware output tests still require maintenance mode and arming.

LIFX entries use `kind: lifx`, `host`, and the twelve-digit LAN `target` ID.
Optional fields are `port` (56700), `kelvin` (3500), `brightness_limit` (1),
`max_hz` (18, maximum 20), `duration_ms` (0) and `expected_pixel_count` (always 1).
The temperature range supported here is 1500–9000 K. For smooth fades, try a
transition near one frame interval; measure the resulting visible delay.

Each lamp has an independent worker with a single pending color. New frames
replace pending frames, and all outbound messages, including initial power-on,
are paced. The first submitted frame powers the lamp on. Black is brightness
zero; ordinary scene stop/blackout frames use the same output path. Closing the
sink drains its latest pending color and closes the socket. Closing alone does
not power off or restore an earlier lamp state.

Streaming requests LAN acknowledgments without waiting for them or retrying old
colors. Runtime status exposes per-resource transmission/coalescing counts, reply
age, recent-reply status, acknowledgment timing and socket errors under
`lighting_transport`. Replies are matched by source, target, sequence and sender IP.
Acknowledgment timing includes worker polling delay and is not optical latency.
Socket failures surface on subsequent frame submission; successful UDP sends alone
do not prove device availability. There is no device-side watchdog: a disconnected lamp may
retain its last color. Optical synchronization and automatic recovery are not guaranteed. See the [feasibility investigation](lifx-feasibility-2026-11-09.md)
for measured protocol performance and remaining physical timing checks.


Calibration uses the same resource routing, maintenance ownership, delay adjustment,
saved installation timing and final blackout path as other lighting outputs.
Calibration frames force zero transition duration even if ordinary playback uses
fades. The 18 Hz transmission cadence still quantizes flash timing, so adjustments
smaller than roughly one output interval cannot establish millisecond optical
accuracy. The output monitor shows accepted controller samples, which can be
coalesced before transmission; LAN acknowledgment diagnostics are separate.

The [live runtime test evidence](lifx-runtime-live-test.json) records both bulbs
running the production calibration engine for ten seconds, a live delay change,
zero brightness queried after stopping, a completed physical output test on Left,
and verified restoration of both initial states. No camera/photodiode measurement
was performed, so these results establish control and protocol response rather
than measured visible alignment. Re-run with `python tools/lifx_runtime_test.py`
from the repository root on a host with the project dependencies and LAN access.
