# ADR 0003: Use a controller-owned fixed-rate render scheduler

- Status: Accepted
- Date: 2026-10-08

## Context

Media telemetry is expected at roughly 10–20 Hz while lighting output targets
20–30 FPS. Rendering only when telemetry arrives couples visible frame cadence
to network delivery and produces uneven output.

## Decision

The controller runs one asynchronous render task for the active scene at a
configurable rate, initially 25 FPS. Every tick evaluates the bounded timecode
estimate and sends a pure frame to the configured sink. Pause and stale
telemetry hold deterministic frames. Agent disconnect, explicit stop, scene
replacement, and application shutdown cancel the task.

The scheduler advances deadlines from the prior target rather than sleeping
after work. If work misses a deadline, it records the miss and resets from the
current monotonic time instead of issuing a burst of catch-up frames.

## Consequences

Output cadence is independent of telemetry cadence, and its basic behavior can
be measured without hardware. Python and host scheduling are not real-time;
M2 must measure actual send and display behavior before making latency claims.
