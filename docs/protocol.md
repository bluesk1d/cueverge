# Device protocol v2

Cueverge devices connect only to the controller's dedicated mTLS listener,
normally on port 8443. The installation CA authenticates a per-device
certificate whose URI subject alternative name contains the controller-issued
device UUID. IP addresses, hostnames, and caller-selected agent IDs do not
establish identity.

The device sends `device.hello` with its certificate identity, a new device
session UUID, and the exact capability document persisted during enrollment.
The controller authorizes the active certificate, verifies the capability
document, resolves the device's durable logical-resource assignment, and
returns `device.welcome` with the controller epoch, heartbeat timing, and a
reconciliation action.

The same authenticated socket carries:

- `device.heartbeat` and `device.heartbeat.ack`;
- `device.media.command`;
- `device.command.ack`;
- `device.media.position`;
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
