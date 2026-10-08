# Agent protocol v1

An agent opens `/api/v1/agent/ws` and first sends `agent.hello` with a unique
agent session ID and its optional current playback snapshot. The controller
returns `agent.welcome` with its unique process epoch, heartbeat timing, and a
reconciliation action:

- `idle`: neither side has playback to reconcile.
- `continue`: the reported epoch, scene, playback session, and sequence match.
- `stop`: playback is unknown or stale and must not be adopted.

The controller may then send `prepare` and `play` commands. Each command
includes a request ID, controller epoch, agent session ID, scene instance ID,
and playback session ID. The agent returns `command.ack` with `completed` or
`error`. Pending commands expire and degrade the scene rather than being
silently treated as successful.

Media telemetry uses `media.position` and includes:

- protocol version, controller epoch, agent ID, and agent session ID
- scene instance and playback session fencing IDs
- a strictly increasing sequence number
- position, playback rate, player state, and optional duration
- the agent monotonic timestamp for diagnostics only
- an optional `seek` or `loop` discontinuity marker

The controller anchors accepted samples to its own monotonic receipt time.
Messages with the wrong protocol version or invalid values fail schema
validation. Messages for another controller epoch, agent session, scene, or
playback session, repeated sequences, and older sequences are acknowledged as
rejected and do not affect rendering.

Agents send sequenced `agent.heartbeat` messages. Valid heartbeats,
acknowledgements, and telemetry refresh liveness. Timeout removes the logical
connection, freezes the timecode follower, and cancels rendering.

The v1 test transport does not yet implement authentication, authorization,
capability negotiation, durable controller state, or command retry. Those
remain requirements before deployment.
