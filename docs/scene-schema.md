# Declarative show packages

A show package is a directory containing:

```text
installation.json
program.json
audience.json
opportunities.json
encounter.json
scenes/
  scene-id.json
assets/
  optional-local-assets
```

All documents use `schema_version: 1` and reject unknown fields.

`program.json`, `audience.json`, `opportunities.json`, and `encounter.json` are
optional.

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

Pixel outputs may additionally define semantic fixtures and ordered paths:

```json
{
  "fixtures": [
    {
      "id": "mouth",
      "precedence": 100,
      "segments": [
        {"segment": "right-gable"},
        {"segment": "left-gable", "reverse": true}
      ]
    }
  ],
  "paths": [
    {
      "id": "eave-to-mouth",
      "segments": [
        {"segment": "eave"},
        {"segment": "connector"},
        {"segment": "right-gable"}
      ]
    }
  ]
}
```

Fixtures are logical groups and carry explicit precedence for future blended
lighting timelines. Different fixtures may overlap. Paths preserve section and
pixel order for traveling effects. Each section can reverse its local wiring
direction without changing the authoritative segment range. A target cannot
repeat a segment, and every referenced segment must exist on the same output.

`segment_pulse` can target a segment, fixture, or path:

```json
{
  "effect": "segment_pulse",
  "target": {"kind": "fixture", "id": "mouth"},
  "pulse_times_ms": [1000],
  "pulse_duration_ms": 120
}
```

The legacy `segment` field remains supported, but a pulse must specify exactly
one legacy segment or semantic target.

## Multi-cue lighting timelines

Instead of one legacy effect, a scene may declare a deterministic timeline:

```json
{
  "resource": "roofline",
  "brightness_limit": 0.8,
  "timeline": [
    {
      "id": "ship-baseline",
      "primitive": "solid",
      "start_ms": 0,
      "duration_ms": 12000,
      "target": {"kind": "fixture", "id": "spaceship-exterior"},
      "color": "#100020",
      "precedence": 0
    },
    {
      "id": "raygun-path",
      "primitive": "path_sweep",
      "start_ms": 8200,
      "duration_ms": 1900,
      "target": {"kind": "path", "id": "garage-to-mouth"},
      "color": "#00FF66",
      "width_pixels": 5,
      "blend_mode": "max",
      "precedence": 20
    }
  ]
}
```

Every cue has a unique ID, start, duration, optional semantic target, color,
brightness, blend mode, and precedence. A cue without a target addresses the
whole output. Fixture precedence is added to cue precedence; lower values
render first and higher values render later. Declaration order resolves equal
precedence.

Supported primitives:

| Primitive | Behavior |
|---|---|
| `solid` | Constant color and brightness |
| `blackout` | Replaces the target with black |
| `envelope` | Color with optional fade-in and fade-out |
| `pulse` | Smooth periodic sine-squared brightness pulse |
| `chase` | Moving pixel window across target order |
| `path_sweep` | Moving window across an ordered semantic path |
| `flicker` | Seeded, bucketed whole-target brightness variation |
| `sparkle` | Seeded, bucketed spatial accents using `density` |
| `ripple` | Expanding symmetric ring through target order |
| `color_transition` | Linear RGB interpolation to `end_color` |

Common parameters include `brightness`, `end_brightness`, `fade_in_ms`,
`fade_out_ms`, `period_ms`, `width_pixels`, `density`, `seed`, and `reverse`.
Blend modes are `replace`, saturating `add`, per-channel `max`, and
`multiply`. After all active cues blend, `brightness_limit` scales the final
RGB frame.

All randomness is derived from cue seed, media-time bucket, and pixel index.
Rendering the same media timestamp therefore returns the same frame after
pause, seek, or reconnect. Cue ranges and fades must fit within the scene.
Timeline form and legacy `effect` form are mutually exclusive.

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

## Safe transitions and automatic requests

Scenes may declare scheduling behavior:

```json
{
  "scheduling": {
    "request_policy": "at_safe_point",
    "request_ttl_ms": 10000,
    "safe_points_ms": [5000, 12000],
    "protected_intervals": [
      {"start_ms": 7000, "end_ms": 11000}
    ]
  }
}
```

`safe_points_ms` and `protected_intervals` describe when the scene is safe to
leave. They are media-relative, must fit within scene duration, and protected
intervals cannot overlap. Intervals are half-open: the end time itself is not
protected.

The incoming scene's `request_policy` controls an automatic request:

- `priority`: preserve the legacy priority and `interruption` behavior.
- `immediate`: replace a lower-priority scene unless its current media time is
  protected.
- `at_safe_point`: wait until the active scene crosses its next unprotected
  safe point.
- `after_completion`: wait for the active playback session to complete,
  regardless of relative priority.
- `ignore`: reject the automatic request.

Direct operator starts remain authoritative and bypass automatic scheduling.
Deferred requests expire after `request_ttl_ms`, and repeated requests for the
same scene coalesce without extending the original expiration. When multiple
requests become ready together, higher priority wins, followed by the oldest
request. A ready completion-boundary request runs before autonomous director
selection, preventing a stale director choice from hiding an audience or
operator-requested transition.

If an immediate request arrives during a protected interval, it waits for a
safe point when one exists; otherwise it waits for completion. Equal- or
lower-priority `immediate` and `at_safe_point` requests remain ignored.

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
`audience_state`, `audience_active_objects`, `audience_present_ms`, and
`audience_previous_absence_ms` as semantic context. The active-object value is
diagnostic overlap, not an assertion of accurate crowd counting.

## Engagement opportunities

An optional `opportunities.json` turns observations and audience context into
time-limited scene opportunities:

```json
{
  "schema_version": 1,
  "opportunities": [
    {
      "id": "porch-reaction",
      "scene_id": "mouth-gotcha",
      "trigger_event": "frigate.zone.entered",
      "trigger_attributes": {"label": "person", "zone": "Porch"},
      "required_context": {"audience": true},
      "event_ttl_ms": 3000,
      "qualification_window_ms": 600000,
      "ttl_ms": 30000,
      "min_presence_ms": 10000,
      "min_absence_before_arrival_ms": 120000,
      "acknowledgement_cooldown_ms": 120000,
      "priority": 60
    }
  ]
}
```

The trigger event and scalar attributes establish a qualifying candidate.
Candidates remain bounded by `qualification_window_ms`, allowing conditions
such as sustained presence to mature on the controller timer without requiring
a detector update at the exact threshold. Future or older-than-`event_ttl_ms`
source events do not create candidates.

When all required context, minimum presence, prior-absence, and acknowledgement
cooldown conditions hold, the candidate becomes an active opportunity for
`ttl_ms`. Opportunities with the same ID coalesce. At a `select_next`
completion boundary, the highest-priority oldest opportunity is consumed once
before autonomous program selection. It never interrupts the currently playing
scene. Expired opportunities are discarded and every opportunity scene must
exist in the same package.

## Encounters

An optional `encounter.json` defines initial and cooldown states, ambient scene,
timeouts, debounce, event TTL, and transitions. Each transition names an event,
eligible source states, destination state, optional scene, and optional minimum
state age. A transition may also declare scalar `attributes`; all declared
key-value pairs must exactly match the incoming event. Every referenced scene
must exist in the same package.
