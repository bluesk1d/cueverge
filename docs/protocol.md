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

A multi-output scene assigns a distinct playback session ID to each media
agent. The controller sends `prepare` to every participating agent and uses a
barrier: no `play` command is sent until every prepare command has completed.
An output's optional static start delay is measured from that barrier.
Pause, resume, seek, stop, and scene replacement commands are fanned out to all
participating agents. One required media output is the declared clock source;
other agents' telemetry cannot change authoritative scene time.
Status reports each participating agent's latest position and its drift from
the declared clock source. Drift is diagnostic only; the controller does not
yet apply unmeasured automatic seek or playback-rate corrections.

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
connection. Losing a required active media agent freezes the timecode follower
and cancels rendering when that agent is the declared clock source. Losing a
non-clock media output leaves the remaining outputs running and reports the
scene as degraded.

The v1 test transport does not yet implement authentication, authorization,
capability negotiation, durable controller state, or command retry. Those
remain requirements before deployment.
