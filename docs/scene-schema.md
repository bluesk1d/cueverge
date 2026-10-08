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

## Scenes

A scene declares:

- ID, display name, mode, priority, and duration.
- Every logical resource it claims.
- A media resource and optional package-relative asset.
- A lighting resource, supported effect name, and RGB color.

Referenced resources must be claimed and bound to the correct role. Asset paths
are resolved canonically and must remain inside the package root. Missing files,
path traversal, unsupported effects, duplicate IDs, invalid colors, and unknown
schema fields prevent the package from loading.

Version 1 intentionally supports only `media_synchronized` scenes and the
`synthetic_sweep` effect. Expanding those literals requires an implemented
runtime behavior, not only a schema change.

## Encounters

An optional `encounter.json` defines initial and cooldown states, ambient scene,
timeouts, debounce, event TTL, and transitions. Each transition names an event,
eligible source states, destination state, optional scene, and optional minimum
state age. Every referenced scene must exist in the same package.
