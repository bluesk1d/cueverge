# Operations and simulation

## Operating modes

Cueverge starts in `automatic` mode. `POST /api/v1/show/mode` accepts:

- `automatic`: external observations may drive audience, encounter, and
  opportunity behavior.
- `disabled`: stops the active scene and rejects automatic observations.
- `maintenance`: stops the active scene and rejects automatic observations
  while leaving direct operator scene control available.
- `blackout`: performs normal stop handling, emits an explicit all-black pixel
  frame, and rejects automatic observations.

`POST /api/v1/blackout` is a shortcut for blackout mode.
`POST /api/v1/show/reset` starts the encounter ambient scene, or the program
initial scene when no encounter is configured. Reset and direct scene starts do
not silently change the selected operating mode.

Observation ingress may remain connected outside automatic mode. The runtime
rejects those observations before they can alter presence, opportunities,
encounters, or scene selection.

## Status and health

`GET /api/v1/status` includes:

- operating mode and whether automatic show behavior is enabled
- active and queued scenes
- director pool, recent selection history, eligible scenes, conditions,
  availability, and remaining cooldowns
- audience state, counters, and the 20 most recent observation summaries
- pending, qualifying, consumed, and expired engagement opportunities
- media connection, command, lifecycle, rendering, and timing diagnostics

Recent observation summaries include event type, camera, zone, timestamps, and
whether the observation was relevant. They intentionally omit detector object
IDs.

Health reporting is capability-aware:

- media-agent connectivity is reported as connected or disconnected
- the configured lighting sink type is reported
- projector and audio are `not_independently_detectable`

Cueverge does not report undetectable devices as healthy. Downstream electrical
provisioning and device-local safety remain installation responsibilities.

## Accelerated simulation

`cueverge.simulation.ShowSimulation` runs a loaded `SceneCatalog` with:

- a manual monotonic and event clock
- `FakeMediaAgent`
- `MemoryFrameSink`
- deterministic director selection
- synthetic observations
- automatic scene completion while advancing simulated time
- operator scene starts and automatic scene requests
- media-agent disconnect and reconnect

The harness never constructs MQTT, mpv, DDP, or physical output adapters.

```python
simulation = ShowSimulation(catalog)
await simulation.start()
await simulation.observe(
    "frigate.zone.entered",
    attributes={
        "source": "frigate",
        "object_id": "synthetic-1",
        "label": "person",
        "camera": "doorbell",
        "zone": "Porch",
    },
)
await simulation.advance(30 * 60 * 1000)
status = simulation.runtime.status()
await simulation.shutdown()
```

Simulation results are available through `scene_history`, the in-memory
lighting frames, recorded agent commands, and normal runtime status.
