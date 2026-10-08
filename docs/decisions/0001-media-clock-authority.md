# ADR 0001: Follow media playback telemetry

- Status: Accepted
- Date: 2026-10-08

## Context

Network commands do not establish when a player visibly or audibly starts.
Pause, seek, rate changes, buffering, and reconnection make controller elapsed
time diverge further from media playback.

## Decision

The controller owns scene lifecycle, but the active media agent's reported
playback position owns media time. The controller anchors each accepted sample
to local monotonic receipt time, interpolates only while playing, and bounds
extrapolation. Scene and playback IDs fence stale sessions; sequence numbers
reject duplicate or reordered samples.

## Consequences

Lighting remains seekable and deterministic. Brief telemetry gaps can be
smoothed without requiring synchronized absolute clocks. Long gaps degrade and
freeze rather than allowing unbounded drift. Hardware latency still requires
measurement and a future calibrated output offset.
