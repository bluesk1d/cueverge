# Agent protocol v1

An agent opens `/api/v1/agent/ws` and first sends `agent.hello`. The controller
may then send `prepare` and `play` commands. Each command includes a request ID,
scene instance ID, and playback session ID.

Media telemetry uses `media.position` and includes:

- protocol version and agent ID
- scene instance and playback session fencing IDs
- a strictly increasing sequence number
- position, playback rate, player state, and optional duration
- the agent monotonic timestamp for diagnostics only
- an optional `seek` or `loop` discontinuity marker

The controller anchors accepted samples to its own monotonic receipt time.
Messages with the wrong protocol version or invalid values fail schema
validation. Messages for another scene/session, repeated sequences, or older
sequences are acknowledged as rejected and do not affect rendering.

The v1 test transport does not yet implement command acknowledgements,
heartbeats, capability negotiation, authentication, or controller epochs.
Those are protocol requirements for the real media-agent increment.
