# Milestones

The current autonomous-show buildout is tracked in the
[show readiness roadmap](show-readiness-roadmap.md).
The planned browser-based operator console and show authoring application is
tracked in the [web controller and show designer roadmap](gui-roadmap.md).

## M0/M1: implemented in this bootstrap

- Typed project, development commands, CI, architecture docs, and ADRs.
- Local API and agent WebSocket.
- Versioned fake-agent telemetry.
- Session-fenced bounded timecode following.
- Pure synthetic RGB rendering and an in-memory output sink.
- End-to-end and focused automated tests.

## Post-M1 scheduler increment: implemented

- Controller-owned 25 FPS rendering driven by the timecode follower.
- Explicit cancellation on stop, replacement, disconnect, and shutdown.
- Frame interval, missed deadline, stale frame, and no-timecode metrics.
- In-memory output only, with cadence and lifecycle tests.

## Protocol reliability increment: implemented

- Controller epochs and per-connection agent session fencing.
- Command acknowledgement, error, abandonment, and timeout tracking.
- Sequenced heartbeats and active-scene liveness expiry.
- Matching reconnect continuation and safe mismatch stop behavior.

## Declarative show-package increment: implemented

- Versioned JSON scene and installation schemas.
- Logical media and pixel-output resource bindings.
- Supported-effect, role, duplicate, color, and claim validation.
- Canonical package-confined asset resolution.
- Controller scene registry loaded from the demo package.

## Software-only M3 encounter increment: implemented

- Generic event API with TTL, future-time, idempotency, and debounce checks.
- Declarative encounter states, guards, and scene transitions.
- Inactivity timeout, cooldown, and unattended ambient fallback.
- Priority/resource arbitration with operator precedence and queued policy.
- Synthetic end-to-end tests without cameras or home automation.

## M2: planned, hardware opt-in

### Software adapter portion: implemented and hardware validated

- Windows named-pipe mpv JSON IPC and local asset confinement.
- Outbound media agent with commands, acknowledgements, telemetry, heartbeat,
  reconnection snapshot, and epoch/session fencing.
- Controller APIs for pause, resume, seek, and explicit agent stop.
- Exact RGB DDP packet encoding, segmentation, and opt-in unicast UDP sink.
- Environment configuration with hardware output disabled by default.

### Target-device validation completed

- Windows 11 portable mpv 0.41.0, projector fullscreen screen index, and wired
  Home Max analog WASAPI route are validated.
- Named-pipe prepare/play, telemetry, pause, seek, resume, explicit stop, and
  controller restart reconciliation are validated end to end.
- The agent runs in the logged-in Windows desktop through Task Scheduler and
  can be managed remotely over key-authenticated SSH.
- WLED host/version, count, RGB layout, two-output linear indexing, 20 FPS dim
  reception, 30 FPS black throughput, and off-state relinquish are validated.
- The integrated media/lighting slice renders at 25 FPS with no missed local
  deadlines and restores a clean idle state after stop.

### Remaining target-device work

- Packet loss, visible update latency, and restoration from an active preset
  remain unmeasured.
- Flash/click calibration and measured projector/audio/LED offsets and jitter.

## M3 integration and later work: planned

HA/Frigate/MQTT adapters require deployment choices and live payload examples.
The private Halloween package requires actual geometry and authored media.
OSS hardening, naming, and licensing remain later decisions.
