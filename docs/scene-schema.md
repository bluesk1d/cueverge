# Declarative show packages

A show package is a directory containing:

```text
installation.json
scenes/
  scene-id.json
assets/
  optional-local-assets
```

All documents use `schema_version: 1` and reject unknown fields.

## Installation bindings

`installation.json` maps logical resource names to adapter targets. Version 1
supports only the simulated roles needed by the vertical slice:

- `media_agent`: target is an agent ID.
- `pixel_output`: target identifies the sink and `pixel_count` is required.

Bindings must be unique. Addresses, credentials, and real hardware parameters
do not belong in the redistributable demo.

A pixel output may optionally declare named `segments` as half-open
`[start, end)` ranges. Segment IDs must be unique and the declared order must
form a contiguous partition from zero through `pixel_count`. An optional
`strand` label can identify separately wired physical runs without exposing
adapter-specific output concepts to scenes. For example:

```json
{
  "resource": "roofline",
  "kind": "pixel_output",
  "target": "wled",
  "pixel_count": 8,
  "segments": [
    {"id": "right-eave", "start": 0, "end": 3, "strand": "lower"},
    {"id": "left-eave", "start": 3, "end": 8, "strand": "lower"}
  ]
}
```

Half-open ranges avoid an extra address at the endpoint: `start: 0, end: 8`
contains the eight addressable pixels `0` through `7`.

## Scenes

A scene declares:

- ID, display name, mode, priority, and duration.
- Every logical resource it claims.
- A media resource and optional package-relative asset.
- A lighting resource, supported effect name, and RGB color.

Referenced resources must be claimed and bound to the correct role. Asset IDs
must be normalized relative paths without traversal. The media agent resolves
them beneath its configured local cache and rejects missing files during
`prepare`; only after that acknowledgement does the controller send `play`.
Path traversal, unsupported effects, duplicate IDs, invalid colors, and unknown
schema fields prevent the package from loading.

Version 1 supports `media_synchronized` scenes with:

- `synthetic_sweep`: one deterministic pixel traverses the full output over
  the scene duration.
- `segment_pulse`: a declared pixel segment illuminates at strictly increasing
  media timestamps for a configured duration. The segment must exist in the
  installation binding, and every pulse must fit within the scene duration.

The pulse effect is useful for calibration and deterministic emphasis cues. It
is driven by authoritative media time, so pause and seek behavior remains
consistent with other lighting effects.

## Encounters

An optional `encounter.json` defines initial and cooldown states, ambient scene,
timeouts, debounce, event TTL, and transitions. Each transition names an event,
eligible source states, destination state, optional scene, and optional minimum
state age. Every referenced scene must exist in the same package.
