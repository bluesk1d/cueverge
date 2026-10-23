# Events and encounters

`POST /api/v1/events` accepts generic observations independently of any Home
Assistant, Frigate, or MQTT schema:

```json
{
  "event_id": "source-unique-id",
  "event_type": "person.entered",
  "occurred_at_ms": 1791490000000,
  "idempotency_key": "optional-source-key",
  "attributes": {"zone": "demo"}
}
```

`occurred_at_ms` is Unix epoch milliseconds supplied by the event producer.
The controller rejects expired events and events more than one second in the
future. Deployment clock health therefore matters for event intake, but not
for media interpolation, which remains monotonic.

The show package's `encounter.json` defines state names, transitions, scene
requests, minimum state age, event TTL, debounce, inactivity timeout, cooldown,
and ambient fallback. The engine generates encounter IDs; it does not identify
people. Duplicate IDs or idempotency keys, debounced event types, and events
without an eligible transition are rejected with explicit reasons.

Transitions may include scalar `attributes` that must exactly match the
normalized event. This keeps source-specific labels, cameras, and zones in show
configuration rather than engine logic:

```json
{
  "event_type": "frigate.zone.entered",
  "from_states": ["suspicious"],
  "to_state": "reaction",
  "attributes": {"label": "person", "zone": "example-zone"}
}
```

## Frigate normalization

`FrigateEventNormalizer` converts Frigate `frigate/events` payloads into the
generic event envelope accepted by the controller. It emits:

- `frigate.object.new` for a new accepted object.
- `frigate.object.updated` for an update with no newly entered zone.
- `frigate.zone.entered` once for each zone added to `after.entered_zones`,
  with the zone name in the `zone` attribute.
- `frigate.object.end` when tracking ends.

Normalized events include `source`, `object_id`, `label`, and `camera`
attributes. Zone-entry events also include `zone`. Event and idempotency IDs
are deterministic so duplicate broker delivery is harmless. Labels are
allowlisted by the normalizer and default to `person`; other labels produce no
events.

The normalizer expects Frigate's `type`, `before`, and `after` envelope, with
an object ID, label, camera, timestamps in epoch seconds, and
`entered_zones`. Unrelated Frigate fields are ignored. Event timestamps are
bounded by the local receive time to prevent future observations from entering
the controller. MQTT transport is an opt-in adapter and is not required for
direct `POST /api/v1/events` ingestion.

With the GUI enabled, use **Installation > Inputs & connections** for guided
Frigate/MQTT setup and separate subscription, reception and visitor-matching health.
See [camera input management](operations.md#camera-input-management) for durable
settings, write-only credentials and the isolated example checker.

Alternatively retain operator-managed MQTT ingress with environment configuration:

```text
CUEVERGE_INPUT_MANAGEMENT=environment
CUEVERGE_FRIGATE_MQTT_ENABLED=true
CUEVERGE_FRIGATE_MQTT_HOST=mqtt.example
CUEVERGE_FRIGATE_MQTT_PORT=1883
CUEVERGE_FRIGATE_MQTT_TLS=false
CUEVERGE_FRIGATE_MQTT_TOPIC=frigate/events
CUEVERGE_FRIGATE_MQTT_USERNAME=cueverge
CUEVERGE_FRIGATE_MQTT_PASSWORD=...
CUEVERGE_FRIGATE_ACCEPTED_LABELS=person
```

Only the enabled flag and host are required; the port, topic, reconnect delay,
and accepted labels have defaults. Username and password are optional for
anonymous brokers, but a password requires a username. Environment-managed
credentials belong only in private deployment configuration, never show packages
or source control. The ingress reconnects after broker failures, logs invalid payloads
without stopping, and starts and stops with the controller application
lifespan.

An independent controller timer advances inactivity and cooldown transitions
without requiring new traffic. Automatic scene requests use declared scene
priority and interruption policy. Direct scene-start API calls are operator
commands and can replace automatic scenes. Encounter cleanup does not stop an
unrelated operator scene.

The demo flow is synthetic:

```text
ambient -> suspicious -> interested -> reaction -> cooldown -> ambient
```

These state names and event types are demo content, not engine constants.

## Audience presence semantics

Show packages may define aggregate audience presence independently of their
encounter state machine. Relevant Frigate objects are tracked by object ID only
to merge overlapping observations and apply freshness; Cueverge does not
identify people or claim an accurate crowd count.

The presence estimator emits:

- `audience.arrived` after configured arrival confirmation.
- `audience.present` with the confirmed arrival.
- `audience.departed` after every relevant object has been absent for the
  configured grace period.
- `audience.long_idle` once after sustained post-departure absence.

Semantic events can drive encounter transitions. Current audience state is also
available to declarative show-program conditions, keeping raw camera and zone
fields out of director policy.

Engagement opportunities may combine a recent raw or semantic event with this
audience context. They qualify and expire independently of encounter state and
are consumed only at a scene completion boundary. This lets a recent porch
observation make a reaction eligible without forcing an immediate
interruption.
