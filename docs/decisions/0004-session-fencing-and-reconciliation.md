# ADR 0004: Fence sessions and reconcile agent state

- Status: Accepted
- Date: 2026-10-08

## Context

A controller or agent can restart while commands and telemetry from an earlier
connection remain in flight. Blindly replaying commands after reconnect can
duplicate a scene, while trusting an unrelated playback session can drive
lighting from the wrong media.

## Decision

Every controller process generates a unique controller epoch, and every agent
connection supplies a unique agent session ID. Commands, acknowledgements,
heartbeats, and telemetry carry both values. The controller rejects messages
that do not match its current epoch and registered agent session.

Commands remain pending until acknowledged and degrade the active scene on
explicit error or timeout. Valid traffic refreshes agent liveness; heartbeat
expiry disconnects the logical agent and stops media-dependent rendering.

On reconnect, an agent reports its actual playback snapshot. Rendering resumes
only when controller epoch, scene instance, playback session, and non-regressed
sequence all match current controller state. Otherwise the welcome response
instructs the agent to stop. A newly restarted in-memory controller therefore
stops playback inherited from its previous epoch rather than adopting it.

## Consequences

Late traffic cannot mutate a new controller or agent session, and matching
playback can continue without duplicate commands. Mismatches require an
explicit new scene start. The current command timeout and heartbeat checks are
in-memory and monotonic; durable recovery remains a later decision.
