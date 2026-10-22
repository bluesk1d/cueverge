# Architecture

## Authority

Cueverge deliberately separates two kinds of authority:

- The controller is authoritative for which scene instance is active, its
  resource claims, and when it is stopped or replaced.
- In media-synchronized scenes, the media agent is authoritative for where its
  current playback session is in media time.
- Explicit controller-timed scenes use the controller's monotonic clock,
  starting after media preparation if any outputs participate. LED-only scenes
  need no media agent.

For media-synchronized scenes the controller renders lighting from a bounded estimate anchored to
accepted agent telemetry. It does not use elapsed time since issuing `play` as
the scene clock. Scene instance IDs and playback session IDs fence late traffic
from earlier runs.

Controller-timed scenes retain the same render scheduler and lifecycle rules,
but derive position from a local pauseable, seekable clock bounded by scene
duration. Media telemetry cannot change this clock. Clock authority never
switches automatically on agent loss.

## M1 components

1. A FastAPI controller starts one synthetic scene and accepts an
   agent-initiated WebSocket.
2. Versioned Pydantic messages validate agent identity, commands, and media
   telemetry.
3. A timecode follower anchors media position to controller monotonic receipt
   time, extrapolates only for a configured interval, and freezes on pause,
   disconnect, or stale telemetry.
4. A controller-owned 25 FPS task evaluates the bounded estimate independently
   from telemetry arrival. Missed deadlines are measured rather than replayed.
5. A pure renderer maps media milliseconds to an exact synthetic RGB frame.
6. An in-memory sink records frames without network or hardware access.
7. A deterministic fake agent creates valid pause, seek, loop, jitter,
   duplicate, and out-of-order scenarios for tests.
8. Controller epochs and agent session IDs fence all runtime messages.
   Commands require acknowledgements, heartbeats enforce liveness, and matching
   playback snapshots allow safe reconnect continuation.
9. A versioned show-package loader validates scenes, logical resource bindings,
   supported effects, and package-confined assets before runtime.
10. Generic events drive declarative encounter transitions with deduplication,
    debounce, timeout, cooldown, and priority-aware scene requests.
11. Replaceable adapter boundaries now include Windows named-pipe and Linux
    Unix-socket mpv JSON IPC plus RGB DDP packet output. Hardware output
    remains explicitly disabled by default.

The M1 runtime is intentionally in-memory and supports one active scene. It
does not persist state across restarts or attempt hardware recovery.

## Assumptions

- Python 3.12 is the initial supported runtime; the development host need not
  have it preinstalled when uv can provision it.
- Controller receipt time is the only clock used for interpolation.
  `agent_monotonic_ms` is retained for diagnostics and is never directly
  subtracted from the controller clock.
- A lower position is accepted when carried by a newer sequence number. The
  optional discontinuity field records whether the agent observed a seek or
  loop; rendering remains pure and does not fire irreversible actions.
- Pause and stale telemetry continue emitting a held deterministic frame.
  Clock-source agent disconnect, explicit stop, replacement, and shutdown cancel
  rendering. Controller-timed scenes continue on media-agent disconnect, with
  degraded output diagnostics.
- Scheduler metrics are diagnostic software measurements and do not describe
  when a projector or physical pixel visibly updates.
- Authentication, durable state, multi-scene arbitration, and rate limiting
  are required before deployment but do not block the local software slice.
- The controller intentionally tells an agent to stop after an epoch or
  playback mismatch. It does not adopt or replay unknown playback.

## Package boundaries

The `cueverge` package has no Halloween concepts, local addresses, device
credentials, or private geometry. Generic synthetic content lives in
`shows/demo`; the future installation-specific package lives separately in
`shows/halloween`.

Scenes refer only to logical resource names. The installation document owns
the mapping from those names to agent and output targets.

The synthetic demo owns its event names and encounter states. Core logic only
interprets the versioned transition model and never imports detector-specific
schemas or Halloween content.
