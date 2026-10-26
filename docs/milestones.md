# Milestones

The current autonomous-show buildout is tracked in the
[show readiness roadmap](show-readiness-roadmap.md).
The planned browser-based operator console and show authoring application is
tracked in the [web controller and show designer roadmap](gui-roadmap.md).

## Usability pass: implementation underway

The [usability pass milestone](usability-pass.md) tracks the current page-by-page,
feature-by-feature browser review for seasonal home-project creators. The first
pass closed on 2026-10-22 with all recommendation groups approved.
The [consolidated implementation plan](usability-pass.md#consolidated-implementation-plan)
defines UP-00 through UP-09, dependencies, checklists, architecture gates, and
acceptance evidence. UP-00 is implemented and verified: long timeline containment,
bounded encounter diagrams, and accessible table scrolling passed 18 focused
browser tests and the frontend type-check/production build. A persistent,
hardware-free local controller and hot-reload frontend support review of each
package. UP-01 is also complete: preservation fixtures, browser/MCP/compiler/
simulation baselines, restored timeline view settings, and
[accepted architecture gates](decisions/0016-lossless-hobbyist-authoring-and-runtime-handover.md).
UP-02 is complete: photo-led Overview, simpler header/account disclosure,
independent polling and freshness, compact truthful activity, and bounded
installation-authorized REST/MCP snapshots. UP-03 is complete: compact library,
scene-native effect editing, shared production LED preview, lossless round trips,
and contextual advanced/collaboration tools. UP-04 is complete: guided
installation-level Frigate/MQTT configuration, protected credentials, retained
operator management, distinct reception/visitor health, isolated example checks,
and installation-bound MCP inspection/configuration/validation.
UP-05 is complete: primary response cards, guided boundary responses, focused
state-sequence scene edits and shared scene/visitor Preview with production
qualification/selection/rendering evidence. UP-06 is complete: photo-first setup,
Select/Add Layout, contextual device/settings/calibration controls, resource-aware
wiring diagnostics and protected MCP description edits. UP-07 adds verified
saved-show Apply, immutable media delivery, real runtime handover and stopped
retained recovery. UP-08 adds approachable Operate and a bounded photo-mapped
intended-output monitor with explicitly authorized MCP inspection/transport.
UP-09 completes cross-workflow qualification: 424 Python tests, 43 frontend
units, 85 browser tests, build/type/lint checks and the retained local browser/MCP
journey. Real hardware/broker commissioning remains installation-specific.

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

- A Windows media-agent configuration was validated with projector fullscreen
  placement and an analog WASAPI audio route. Qualify each deployment
  independently; this is not a required speaker or display configuration.
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
Installation-specific shows require measured geometry and authored media.
OSS hardening, naming, and licensing remain later decisions.
