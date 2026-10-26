# GUI scale budgets

Cueverge qualifies the production GUI against the following per-installation
budgets. These are supported operating limits, not recommended authoring
targets.

| Surface | Supported budget | Interactive target |
| --- | ---: | ---: |
| Scenes in one project | 250 | Save and compile within 5 seconds |
| Timeline items in one composition | 10,000 | Open within 5 seconds; bounded canvas and editor DOM |
| Assets indexed in one project | 5,000 | Open within 5 seconds; fewer than 20 rendered rows |
| Connected device clients | 100 | Registry update within 2 seconds; health visible within 5 seconds |
| Installation reference photos | 12 | Viewpoint switching within 1 second |
| Deterministic simulation | 100 steps | Complete within 5 seconds with no hardware adapters |
| Publication | 250 scenes | Compile and publish within 5 seconds each |

## Camera input setup and health

- One supported Frigate/MQTT source per runtime installation; it is opt-in.
- 256 KiB per application payload and 20 recent diagnostic entries; no raw
  payloads or object IDs in health. Reconfiguration resets listener counters.
- GUI labels are limited to 50 entries of at most 128 characters. Password
  replacement is write-only and limited to 4096 characters.
- Serialized visible-page health requests roughly every two seconds, an
  eight-second timeout, and explicit last-known/unavailable state on failure.
- Configuration/reconnect is serialized and drains any in-flight controller
  delivery. Broker reconnect delay retains its existing five-second default.
- Isolated normalization/rule checks share the same payload bound and never
  connect to MQTT, inject live input, or command hardware. Their context digest
  must match current saved settings/loaded visitor rules before reuse.

## Interactive scene-native lighting preview

The shared visitor Preview workspace additionally bounds a test to 200 actions,
ten simulated minutes, 1,000 estimated scene boundaries, 20,000,000 estimated
pixel-index work and 20,000 fake-media telemetry ticks. It shares the two
nonblocking evaluation slots and returns at most 4,096 RGB samples at a selected
checkpoint. Qualification advances at the controller's 100 ms tick; intermediate
ticks need not allocate LED frames. No physical adapters or real input are used.
MCP results retain their separate 200 KiB bound. Candidate/revision changes
invalidate displayed test evidence; unsupported spatial colors are withheld,
not replaced with successful-looking raw frames.

UP-03 adds a separate bounded raw-scene preview budget, not a reduction in the
offline compilation/publication or advanced composition budgets above:

- 4096 returned RGB samples per request; pixel pages carry explicit coverage.
- 65536 installation pixels and 2000000 estimated pixel-index work across the
  candidate, checked before loading/rendering it.
- Two nonblocking concurrent evaluations per controller process. Busy REST
  requests return 429; MCP reports a tool error rather than queueing unbounded work.
- The browser samples up to four times/second and serializes slow requests;
  reported frame time, not speculative browser color rendering, is authoritative.
- Temporary compilation is cleaned up. Saved revisions include current LED
  composition bakes; unbaked or unsaved LED compositions are listed in the
  frame's `limitations` rather than silently mixed into a successful-looking
  frame.
- Scene media preview (projector/media outputs) is a separate budget: two
  nonblocking concurrent evaluations per process, stills at most 1920 px wide
  (browser default 640), the existing 256 MiB decoded-frame bound per media
  source, and the MCP payload bound. The browser serializes requests at up to
  four per second alongside the LED preview.

These bounds are reported through MCP capability discovery and covered by
preview tests. The 56-cue synthetic local demo is qualified through real
browser/MCP readback and frame comparison, not a new claim that every maximum
scene/asset combination renders interactively.

Automated qualification compiles and publishes the maximum scene budget, runs a
100-step no-hardware simulation, connects 100 in-memory device sessions, loads
12 mapper viewpoints, and exercises 10,000 timeline markers and 5,000 assets
through bounded browser DOM windows. Production profiling must be repeated on
the deployment-class controller before increasing any limit. Operations should
alert when an installation reaches 80 percent of a budget so it can be split or
archived before authoring becomes unreliable.

The observability endpoint at `/api/gui/v1/observability` is restricted to
administrators and reports bounded API latency samples, HTTP outcomes,
background and asset job states, device WebSocket health, browser errors,
storage capacity, simulation runs, and publication states. Browser errors are
also attributed in the durable audit log. Metrics are intentionally bounded in
memory; an external collector should scrape the endpoint and retain long-term
history.
