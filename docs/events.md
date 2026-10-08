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
