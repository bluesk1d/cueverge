# Device protocol v2

Cueverge devices connect only to the controller's dedicated mTLS listener,
normally on port 7443. The installation CA authenticates a per-device
certificate whose URI subject alternative name contains the controller-issued
device UUID. IP addresses, hostnames, and caller-selected agent IDs do not
establish identity.

The device sends `device.hello` with its certificate identity, a new device
session UUID, and the exact capability document persisted during enrollment.
The controller authorizes the active certificate, verifies the capability
document, resolves the device's durable logical-resource assignment, and
returns `device.welcome` with the controller epoch, heartbeat timing, and a
reconciliation action.

Devices may declare the generic `timing_calibration` capability independently
of their media, pixel, downstream-control, or future actuator capabilities. It
lists actuator kinds, separate optional onset and release adjustment ranges,
resolution, whether adjustment is safe while active, the required safe-test
procedure (`maintenance_reference`, `armed_trials`, or `simulation_only`), and
A/B comparison support. At least one of onset or release must be adjustable,
and ranges must divide exactly by their declared resolution.

The same authenticated socket carries:

- `device.heartbeat` and `device.heartbeat.ack`;
- `device.media.command`;
- `device.command.ack`;
- `device.media.position`;
- `device.player.start`, `device.player.stop` and `device.player.ack`;
- two-phase credential rotation requests and responses.

Every operational message is fenced by the device session UUID and controller
epoch. Media commands also include request, scene-instance, and
playback-session UUIDs. Telemetry uses a strictly increasing sequence and
includes position, rate, player state, optional duration, monotonic device
time, and optional seek/loop discontinuity.

A multi-output scene assigns a distinct playback session to each logical media
resource. The controller sends `prepare` to all participating devices and does
not send `play` until every required prepare has completed. Pause, resume,
seek, stop, and replacement commands fan out to all participating resources.
One required output is the declared clock source; other telemetry is
diagnostic and cannot advance authoritative scene time.

`device.media.command` names are `prepare`, `queue`, `advance`, `play`,
`pause`, `resume`, `seek`, `stop` and `get_status`. Only `prepare` and `queue`
carry `asset_id`, `end_behavior` and an optional `repeat_count` (total plays,
not with `loop`). `queue` preloads the next scene's media behind the current
file under the next scene-instance and playback-session UUIDs, and requires
`auto_advance`. With `auto_advance: true` the device continues into that file
by itself when the current media ends. It then sends one `completed` position
for the old session, followed by positions for the new session. Telemetry for
the new session reaches the controller before it adopts the next scene and is
answered with the non-error reason `handoff_pending`. With `auto_advance:
false` the device holds the queued file until `advance` names its playback
session. `advance` is idempotent once the device has already continued into
that session. A new `prepare` or `stop` discards anything queued. A failed
`queue` only cancels preloading for that device, and a failed `advance` makes
the controller fall back to `prepare`.

`device.hello` may list `media_features` (`seamless_transition`,
`repeat_count`) implemented by the agent build. They describe player behavior
rather than trust, so they are reported per connection and are not part of the
enrolled capability document. Devices without them keep the stop, prepare and
play sequence, and refuse repeat counts. Update the controller before media
agents: an older controller rejects a hello that includes `media_features`.

Agents that list the `player_control` feature keep device presence separate from
their media player. `device.hello` and every `device.heartbeat` report
`player_state`: `stopped`, `starting`, `ready`, `stopping`, or `failed`. The
controller registers such an agent as a runtime media target only while its
player is `ready`. When the player stops or fails, it releases the target the same
way as a disconnect.

`device.player.start` and `device.player.stop` carry a request UUID and are
fenced by device session and controller epoch. The device answers with
`device.player.ack`: `status` is `completed` or `error` (with an `error` detail),
plus the resulting `player_state`. Stopping clears any playback the agent was
following. A late acknowledgement after the controller stopped waiting only
updates state. Agents without `player_control` always run their player, and the
controller treats them as ready.

`device.hello` and every `device.heartbeat` may also carry `keep_awake`, the
host's sleep prevention configured locally with `CUEVERGE_KEEP_AWAKE`:
`{"mode": "system" | "display", "active": bool, "detail": str | null}`.
`active` is false when the operating system or desktop refused, and `detail`
then explains why. The field is omitted when the option is `off`, so agents
using it need a controller that recognizes the field. Update the controller first, as with
`media_features`. The controller only reports this status; it can't change host
power settings.

Valid heartbeats refresh both certificate-session presence and runtime
liveness. A stale session is disconnected and cannot update or disconnect a
replacement session. Losing the active clock source freezes time-following and
cancels lighting rendering; losing another required output marks the scene
degraded.

Credential rotation is two phase. The current certificate authenticates a CSR
for a pending replacement. The current credential remains valid until the
device verifies and persists the replacement and reconnects with it. That
successful connection atomically activates the new serial and supersedes the
old one.

The original unauthenticated protocol-v1 WebSocket and HTTP control surface
have been removed.
