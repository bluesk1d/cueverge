# Declarative show packages

A show package is a directory containing:

```text
installation.json
program.json
audience.json
opportunities.json
looks.json
encounter.json
scenes/
  scene-id.json
assets/
  optional-local-assets
```

All documents reject unknown fields. Installation and most auxiliary documents
use `schema_version: 1`. Scene and lighting-look documents support
multi-output version 2 forms.

`program.json`, `audience.json`, `opportunities.json`, `looks.json`, and
`encounter.json` are optional.

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

The GUI exposes these definitions under **Installation > Objects**, scoped to an
explicitly selected show's working draft. MCP exposes the same inventory and
validated edits through `get_installation_objects` and `edit_installation_object`.
Unplaced fixtures are valid and remain visible in both interfaces.

Friendly names and optional layout associations are editor metadata, not extra
fields in `installation.json`. The show draft stores
`editor_metadata.installation_object_details` as a list of:

```json
{
  "resource": "roofline",
  "kind": "fixture",
  "id": "mouth",
  "name": "Talking mouth",
  "geometry_semantic_ids": []
}
```

The tuple `(resource, kind, id)` identifies a logical object; geometry links use
installation semantic IDs so linked photo views retain their identity. Member
geometry on the same output is discovered by matching exact segment pixel
ranges. Links and names do not alter runtime addressing, segment order or
precedence. They survive project export/import as editor metadata, while runtime
packages retain the existing fixture/path schema. See the
[object workflow](operator-guide.md#managing-fixtures-and-installation-objects)
for placement and safe deletion.

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

## Multi-output scenes

Scene schema version 2 allows one scene to address multiple media agents and
pixel outputs. Every output has a scene-local ID and references one claimed
installation resource. Output IDs and referenced resources must be unique
within the scene.

```json
{
  "schema_version": 2,
  "id": "two-window-scene",
  "name": "Two Window Scene",
  "mode": "media_synchronized",
  "resources": [
    "projector.left",
    "projector.right",
    "roofline",
    "porch"
  ],
  "duration_ms": 12000,
  "clock_source": "left-video",
  "media": [
    {
      "id": "left-video",
      "resource": "projector.left",
      "asset": "left.mp4"
    },
    {
      "id": "right-video",
      "resource": "projector.right",
      "asset": "right.mp4"
    }
  ],
  "lighting": [
    {
      "id": "roofline",
      "resource": "roofline",
      "effect": "synthetic_sweep"
    },
    {
      "id": "porch",
      "resource": "porch",
      "effect": "synthetic_sweep"
    }
  ]
}
```

In `media_synchronized` mode, `clock_source` must identify a required media output. Its accepted telemetry is
the authoritative scene clock. Telemetry from other media outputs is fenced
and recorded but does not move scene time.

Media and lighting outputs default to `required: true`. A disconnected optional
media output is omitted when the scene starts. All required media agents must
be connected, and all participating agents must acknowledge `prepare`, before
the controller sends any `play` command.

Media outputs may set `start_delay_ms` from 0 through 5000. The delay starts
after the prepare barrier opens and is intended for measured, static startup
calibration. It does not change clock authority or apply ongoing drift
correction.

`end_behavior` (`hold_last_frame` by default, `loop`, or `stop`) decides what
the output shows after its media ends. `repeat_count` (1 to 10000, omitted means
once) plays the media that many times back to back before `end_behavior`
applies; it cannot be combined with `loop`. Repeats are gapless on the device,
and the reported media position runs continuously across them, so a
`media_synchronized` scene clock covers all plays. Set the scene `duration_ms`
to `start_delay_ms + repeat_count x media length` so lighting and previews line
up. A media-synchronized scene completes when its clock-source output finishes
its last play. Devices must report the `repeat_count` media feature; a required
output with a repeat count refuses to start on older agents.

```json
{
  "id": "klown",
  "resource": "projector.porch",
  "asset": "klown-loop.mp4",
  "end_behavior": "hold_last_frame",
  "repeat_count": 3
}
```

Lighting outputs may set `timing_offset_ms` from -5000 through 5000. Positive
values delay that output by rendering an earlier scene position; negative
values advance it. The existing controller-wide lighting delay is applied
first, followed by the per-output offset. Both values are installation
calibration inputs and should remain zero until measured.

Each lighting output is rendered independently with its own pixel geometry and
brightness limit, then routed by logical resource to its configured sink.
Multiple lighting resources therefore require a resource-keyed sink router.
An output send failure is recorded against that resource and does not prevent
healthy lighting outputs from receiving their frames; failed sends are retried
on later render ticks.
Scene schema version 1 retains its singular `media` and `lighting` objects and
is normalized to the same loaded representation.

### Media-only scenes

Schema v2 permits `lighting: []` when at least one media output is present.
Both media-synchronized and controller-timed clocks are supported. A scene
cannot have both output arrays empty. For example:

```json
{
  "schema_version": 2,
  "id": "window-ghost",
  "name": "Window ghost",
  "mode": "media_synchronized",
  "clock_source": "window",
  "resources": ["projector.demo"],
  "duration_ms": 30000,
  "media": [{"id": "window", "resource": "projector.demo", "asset": "ghost.mp4"}],
  "lighting": [],
  "completion": {"policy": "stop"}
}
```

Media-only scenes prepare and transport their media normally but send no
lighting frames. A persistent baseline is not a separate running layer and
does not add lighting outputs or claims to these scenes. Legacy single-resource
lighting-look packages may include media-only scenes; scenes that do contain
lighting must still match that legacy look's resource. Explicit stop still
applies any configured safe-state lighting looks.

The GUI **Add scene** / **Add output** workflows create schema-v2 definitions.
Adding to a v1 scene retains its old singular outputs under IDs `media` and
`lighting`; media remains the clock unless the author explicitly changes it.
New lighting starts with a blackout timeline, while new media awaits asset
placement. Renaming or removing outputs uses the same schema-v2 upgrade and keeps
every reference consistent; see the
[operator workflow](operator-guide.md#add-scenes-and-outputs).

### Controller-timed scenes

Schema version 2 also supports `mode: "controller_timed"` with
`clock_source: "controller"`. The controller's monotonic clock owns scene
position, pause/resume, seeking, and duration-based completion. It is an
explicit choice, not a fallback for a disconnected or stale media clock.
Version 1 scenes remain media-synchronized.

For an LED-only pre-show, use `media: []` and claim only lighting resources:

```json
{
  "schema_version": 2,
  "id": "preshow",
  "name": "LED pre-show",
  "mode": "controller_timed",
  "clock_source": "controller",
  "resources": ["roofline"],
  "duration_ms": 30000,
  "media": [],
  "lighting": [
    {"id": "roofline", "resource": "roofline", "effect": "synthetic_sweep"}
  ],
  "completion": {"policy": "loop"}
}
```

No media-agent binding, connection, or telemetry is needed for this scene.
The [LED-only demo](../shows/led-only-demo) is a complete loadable package.
Bind its lighting resource to your installation before enabling physical output.

Controller-timed scenes may also include media outputs. Required connections
and the prepare barrier still apply; scene time starts when preparation
succeeds, before per-output startup delays. Pause/resume/seek commands go to
participating agents as well as the local clock. Agent telemetry and drift
remain diagnostic: media completion, pauses, buffering, or disconnection do
not take over scene time. There is no ongoing media drift correction, so use
media-synchronized mode when lighting must track actual visible/audible
playback. Required prepare failures still prevent startup and retain the
configured failure-scene behavior.

Normal completion policies, safe-point requests, lighting offsets, and
brightness limits apply in both modes. Controller time clamps at the scene
duration; `hold` keeps rendering the final position, and `loop` starts a new
scene instance. In the timeline editor's **Scene settings**, select **Clock authority → Controller**;
the clock source then becomes **Controller monotonic clock**.

Stage projector bakes normally give mixed audio to the clock-source media
output. A controller-timed scene instead uses the first declared media output
that belongs to the stage projector set as its audio owner.

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

## Persistent lighting looks

An optional `looks.json` defines show-level lighting independent of media scene
time:

```json
{
  "schema_version": 1,
  "resource": "roofline",
  "cycle_ms": 6000,
  "brightness_limit": 1.0,
  "baseline": [
    {
      "id": "fixture-breathing",
      "primitive": "pulse",
      "start_ms": 0,
      "duration_ms": 6000,
      "target": {"kind": "fixture", "id": "accent"},
      "color": "#FF2060",
      "brightness": 0.25,
      "period_ms": 3000
    }
  ],
  "safe_state": [
    {
      "id": "safe-blackout",
      "primitive": "blackout",
      "start_ms": 0,
      "duration_ms": 1
    }
  ]
}
```

Baseline cues use the same deterministic primitives and semantic targets as
scene timelines, but their cycle follows controller monotonic time. Their phase
therefore survives media completion and scene replacement. A timeline scene
renders over the baseline, changing only pixels addressed by active scene cues.
Fixture and cue precedence continue to determine overlap order.

Version 1 defines one look resource and requires it to appear in every scene.
Version 2 wraps the same per-resource definition in an `outputs` array:

```json
{
  "schema_version": 2,
  "outputs": [
    {
      "resource": "roofline",
      "cycle_ms": 6000,
      "baseline": [
        {
          "id": "roofline-idle",
          "primitive": "blackout",
          "start_ms": 0,
          "duration_ms": 6000
        }
      ],
      "safe_state": [
        {
          "id": "roofline-safe",
          "primitive": "blackout",
          "start_ms": 0,
          "duration_ms": 1
        }
      ]
    },
    {
      "resource": "porch",
      "cycle_ms": 4000,
      "baseline": [
        {
          "id": "porch-idle",
          "primitive": "blackout",
          "start_ms": 0,
          "duration_ms": 4000
        }
      ],
      "safe_state": [
        {
          "id": "porch-safe",
          "primitive": "blackout",
          "start_ms": 0,
          "duration_ms": 1
        }
      ]
    }
  ]
}
```

The `baseline` and `safe_state` arrays must each contain at least one cue.
Resources must be unique and bound to pixel outputs. Baselines are
composited only into the matching scene lighting output. Explicit stop renders
every configured resource's `safe_state`; queued scene continuation does not
insert safe-state frames between scenes. Legacy single-effect scenes retain
their full-output behavior and do not composite with the baseline.

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

Lighting is never rendered at the completion position itself (which equals
`duration_ms` and is past every cue and baked frame). The tick that observes
completion, and any held frames afterwards, render the last valid millisecond
(`duration_ms - 1`), so a looping scene shows its final frame and then frame 0
with no dark frame between passes.

### Seamless scene changes

For `transition_to`, `loop` and `select_next`, the controller chooses the next
scene 3 seconds before the current one ends (for `select_next` this is when the
director and waiting visitor responses are consulted) and preloads its media
behind the current file on every device that plays both scenes. Nothing new is
shown until the change:

- In a `media_synchronized` scene whose clock output is not `loop`, the clock
  device continues into the preloaded file the moment its media ends, so the
  last frame of the current scene is followed directly by the first frame of the
  next. That end of media is also what completes the scene.
- Other preloaded outputs, and all outputs of `controller_timed` scenes, switch
  immediately when the controller changes scene, without stopping or reloading.
- Outputs on devices that are idle in the current scene, outputs with a
  `start_delay_ms`, and devices without the `seamless_transition` media feature
  are prepared and started the existing way.
- Visitor responses, encounters and operator starts interrupt the plan and use
  the existing stop, prepare and play sequence.

While a preloaded change waits for a controller command, a current output set
to `stop` blanks at its end and one set to `hold_last_frame` holds its last
frame, as before. Runtime status reports the planned `next_scene` and a
`seamless_transitions` count.

## Show programming

The browser **Show plan** and MCP `get_show_plan`/`update_show_plan` present this
program and each scene's completion in plain language: the default pool is the
"shuffle list" and completion policies are "When it ends" choices. See
[Show plan authoring](operations.md#show-plan-authoring).

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
