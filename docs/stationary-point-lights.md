# Stationary point lights

As an administrator, open **Installation → Devices → Connect and manage devices →
Add a device** and choose **LIFX bulb (stationary point light)** under *What are
you adding?*. Enter a resource name, IPv4 address and twelve-digit LIFX target ID
(the bulb's MAC address without colons). Registration creates both the one-sample
logical output and its live LIFX driver. Registration sends no lighting commands;
stop playback/calibration/testing first. The form defaults to a 25% brightness cap
and 18 Hz transmission; **Advanced LIFX bulb settings** holds temperature, update
rate, transition and port.

Registered LIFX bulbs and [WLED controllers](ddp.md#registering-wled-controllers-from-the-gui)
are listed under **Lighting devices (LIFX, WLED)** on the same page, with
**Edit** (address, target, limits) and **Remove**. The resource name, device type
and pixel count are fixed; remove and re-add to change them. Remove is refused
while the loaded show uses the output; show drafts and layout points that
reference it are kept, and applying such a show fails until it is registered
again. Registrations are saved in `lighting-devices.json` under the controller GUI
data root (with a registry `revision` used for edit/remove conflict checks) and
restored on startup. An existing `lifx-outputs.json` from earlier versions is
migrated automatically and renamed `lifx-outputs.json.migrated`. Changes are
audited as `installation.lighting_device.create|update|remove`. MCP clients use the
equivalent [lighting device tools](operations.md#mcp-assisted-authoring).

The Devices tree shows every registered device, labelled **LIFX bulb** or
**WLED / DDP output**. LIFX bulbs are never probed over HTTP; they show as
connected only while recent LAN acknowledgments are observed.

For the investigated bulbs:

| Resource | IPv4 | Target ID |
| --- | --- | --- |
| light.left | 192.168.40.71 | d073d58b1a6d |
| light.right | 192.168.40.194 | d073d58b1e1f |

In **Photos & Layout**, choose **Stationary point light**, place one point and
select the new resource. Each lamp has an independent resource and position;
lighting compositions sample RGB at that position. A point light has one sample
at index zero and no traversal.

New shows inherit registered outputs. For an existing show, open **Installation →
Objects**, select the show and click **Add light.left to this show** (and Right;
registered WLED controllers get the same button).
This revises only that working draft. Select the resource when adding a lighting
output to a scene, author its lighting, then publish/apply through the normal flow.
Registration and draft import alone do not start playback or apply a show.
The installation outputs remain available when other shows are applied.
Timing calibration and armed physical output testing can target them directly.

File-managed deployments can instead use the [two-bulb example](../examples/lifx-outputs.json)
with `CUEVERGE_OUTPUT_CONFIG`; LIFX drivers also register their logical resources
at runtime startup. Do not register the same bulb through both mechanisms.

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
