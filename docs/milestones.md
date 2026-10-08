# Milestones

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

## Next smallest increment

Implement the software-only M3 event and encounter domain: generic event
intake, TTL/idempotency, debouncing, cooldowns, explicit encounter transitions,
priority/resource arbitration, operator interruption, timeout, and ambient
fallback. Keep HA, Frigate, MQTT, and physical outputs out of this increment.

## M2: planned, hardware opt-in

After learning the laptop OS and validating actual devices: mpv IPC, asset
validation/cache, DDP packetization, WLED restoration policy, calibration
scene, and measured latency/jitter.

## M3+: planned

Generic event ingestion, encounter state, arbitration/cooldowns, HA/Frigate
examples, then a private Halloween reference show. OSS hardening, naming, and
licensing remain later decisions.
