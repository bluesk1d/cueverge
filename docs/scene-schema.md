# Declarative show packages

A show package is a directory containing:

```text
installation.json
program.json
audience.json
encounter.json
scenes/
  scene-id.json
assets/
  optional-local-assets
```

All documents use `schema_version: 1` and reject unknown fields.

`program.json`, `audience.json`, and `encounter.json` are optional.

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
- An optional completion policy and failure fallback scene.

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

## Scene lifecycle

The optional `completion` object defaults to `{"policy": "hold"}` so existing
scenes retain their final state. Supported policies are:

- `loop`: start a new playback session for the same scene.
- `transition_to`: start `target_scene_id`.
- `select_next`: ask the configured show director for the next scene.
- `hold`: retain the completed scene and deterministic final frame.
- `stop`: stop media-dependent rendering and relinquish the active scene.

Only `transition_to` accepts `target_scene_id`. A scene may also declare
`failure_scene_id`; a failed `prepare` command starts that scene. Completion
targets and failure scenes must exist in the same package and cannot reference
the originating scene.

Completion is fenced by playback session and applied once even if completed
telemetry is delivered repeatedly or late. `select_next` requires a configured
director; without one, the active scene is held and status reports
`scene_selector_unavailable`.

## Show programming

An optional `program.json` defines continuous scene selection:

```json
{
  "schema_version": 1,
  "initial_scene_id": "quiet-ambient",
  "default_pool_id": "ambient",
  "recent_history_size": 2,
  "random_seed": 42,
  "pools": [
    {
      "id": "ambient",
      "category": "ambient",
      "scenes": [
        {
          "scene_id": "quiet-ambient",
          "weight": 3,
          "cooldown_ms": 120000,
          "conditions": {"audience": false}
        },
        {
          "scene_id": "machinery-ambient",
          "weight": 1
        }
      ]
    }
  ]
}
```

The initial scene starts through `POST /api/v1/show/start`. A scene whose
completion policy is `select_next` asks the director to choose from the active
pool. Entries support integer weights, selection cooldowns, and exact-match
scalar conditions. Pools carry semantic categories for future engagement and
operations policy.

Selection is deterministic for a configured random seed. The director first
excludes recent history, then relaxes history if that is the only way to keep
a valid program running. It never relaxes cooldown, condition, or availability
checks. If no scene is eligible, the active scene stops. Program status reports
the active pool/category, recent history, selection count, and empty-selection
count.

Every program scene must exist in the package. Conditions default to an empty
runtime context until an observation or operations component supplies semantic
values; show files must not use raw Frigate payload fields as director
conditions.

## Aggregate audience presence

An optional `audience.json` converts granular object tracking into semantic
audience state:

```json
{
  "schema_version": 1,
  "labels": ["person"],
  "cameras": ["doorbell"],
  "zones": ["Porch"],
  "observation_freshness_ms": 5000,
  "arrival_confirmation_ms": 1000,
  "absence_grace_ms": 5000,
  "long_idle_ms": 300000
}
```

At least one camera or zone must scope the estimator. When zones are declared,
an object becomes relevant only after entering one of them; later updates keep
that object fresh. Multiple overlapping objects still represent one audience.
Arrival requires continuous relevant tracking for the confirmation interval.
The audience remains present through short tracking gaps and departs only after
the absence grace period.

The estimator emits `audience.arrived`, `audience.present`,
`audience.departed`, and `audience.long_idle`. It exposes `audience`,
`audience_state`, and `audience_active_objects` as semantic director context.
The active-object value is diagnostic overlap, not an assertion of accurate
crowd counting.

## Encounters

An optional `encounter.json` defines initial and cooldown states, ambient scene,
timeouts, debounce, event TTL, and transitions. Each transition names an event,
eligible source states, destination state, optional scene, and optional minimum
state age. A transition may also declare scalar `attributes`; all declared
key-value pairs must exactly match the incoming event. Every referenced scene
must exist in the same package.
