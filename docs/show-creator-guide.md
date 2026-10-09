# Cueverge show creator guide

This document describes Cueverge from the perspective of a show creator. It is
both a capability reference and a creative brief template. A completed show
definition based on this guide should contain enough information to separate:

1. Content that can be authored with Cueverge today.
2. Generic engine capabilities that should be added for the show.
3. Installation-specific configuration and private media.
4. Creative decisions that still require an answer.

Do not constrain the creative concept to the current renderer. Describe the
desired audience experience first, then identify where Cueverge needs a new
generic capability.

## 1. What Cueverge is

Cueverge is a self-hosted engine for event-driven multimedia shows. It
coordinates:

- Video projected from a Windows media computer.
- Audio played from the same media asset through the configured analog output.
- Addressable RGB lighting rendered from authoritative media time.
- External observations such as Frigate person and zone events.
- Stateful encounters with timeout, cooldown, priority, and ambient recovery.
- Direct operator scene control.

The controller decides **which scene is active**. The media player decides
**where that scene is in time**. Lighting follows the observed media clock, not
the time at which the controller requested playback.

The reusable engine contains no Halloween story, house geometry, private
addresses, credentials, or copyrighted assets. Those belong in a private show
package and deployment configuration.

## 2. Validated installation capabilities

The current installation has been validated with:

- One Windows 11 media agent running mpv.
- One projector display driven fullscreen by that agent.
- One wired analog audio destination.
- One WLED-controlled roofline with 605 RGB pixels.
- Frigate MQTT person observations and named zone-entry events.
- Measured audio and lighting delay compensation.

The projector presents two physical windows as parts of one visual
environment. Content can exploit the real siding between them: a character or
object may leave one projected window, travel through implied off-screen
space, and appear in the other.

The exact media resolution, frame rate, composition mask, safe title area, and
window alignment are content-production decisions. They are not encoded in
the current scene schema and must be specified in the show definition before
final media is rendered.

### Roofline segments

Pixel addressing follows wiring order and uses half-open ranges `[start, end)`.
The final endpoint is 605, so the addressable pixel indices are 0 through 604.

| Segment ID | Range | Count | Strand |
|---|---:|---:|---|
| `garage-eave` | 0–162 | 162 | lower |
| `garage-to-porch` | 162–196 | 34 | lower |
| `porch-gable-right` | 196–242 | 46 | lower |
| `porch-gable-left` | 242–288 | 46 | lower |
| `porch-eave` | 288–377 | 89 | lower |
| `upper-eave` | 377–464 | 87 | upper |
| `upper-gable-right` | 464–536 | 72 | upper |
| `upper-gable-left` | 536–605 | 69 | upper |

The installation has been visually checked for segment placement, pixel
direction, strand transition, and full-line traversal.

## 3. Current scene model

A scene is one media-synchronized unit. The current schema requires:

- A unique scene ID and human-readable name.
- A positive duration in milliseconds.
- A priority from 0 through 100.
- An interruption policy.
- A media resource and optional package-relative media asset.
- A pixel-lighting resource and one lighting effect definition.
- Explicit claims for every referenced resource.

Cueverge currently runs one active scene at a time. It does not composite
multiple videos, mix independent scene audio, or layer independent lighting
scenes.

### Media behavior

Supported now:

- A local media file loaded by mpv.
- Video and its embedded audio played together.
- Prepare-before-play command flow.
- Fullscreen projector placement.
- Pause, resume, seek, and stop.
- Playback-position telemetry.
- Lighting that pauses and seeks with media.
- Controller/agent restart fencing and matching-session reconciliation.

Important current limitations:

- Declarative show pools now support deterministic weights, cooldowns,
  exact-match eligibility conditions, and recent-history exclusion.
- Completion can loop, stop, hold, transition to a declared scene, or call a
  show-director selector.
- Audience-derived director context and safe-cut scheduling are not yet
  implemented.
- There is no independent sound-effect channel or audio ducking.
- There is no video compositing, transparency layer, or simultaneous playback.
- Media assets are deployed to the laptop's private asset root manually.

For the first show, each authored media file should normally contain all video
and audio needed for that scene. Authored transition clips are preferable to
assuming live video compositing.

### Lighting behavior

Lighting is evaluated deterministically from media milliseconds. This means a
frame can be recalculated after pause, seek, reconnection, or timing
correction.

Two effects are implemented:

#### `synthetic_sweep`

- Lights one pixel at a time.
- Traverses the full output in wiring order.
- Repeats over the declared scene duration.
- Uses one RGB color.

This is primarily a diagnostic primitive, not a finished creative effect.

#### `segment_pulse`

- Targets one named roofline segment.
- Uses one RGB color.
- Accepts one or more media-relative pulse start times.
- Uses one pulse duration for every pulse.
- Turns the entire segment on for each pulse.

This is suitable for synchronized accents but cannot yet express fades,
traveling pulses, multisegment choreography, brightness curves, or layered
looks.

### Lighting features that may be requested

The show definition should freely request effects such as:

- Solid looks and blackout.
- Fades and crossfades.
- Whole-roof or per-segment brightness envelopes.
- Segment chases and direction-aware sweeps.
- Paths crossing multiple segments.
- Charge-up, traveling pulse, ripple, flicker, sparkle, electrical arc, and
  malfunction effects.
- Multiple timed lighting cues in one scene.
- Multiple colors or simultaneous segment groups.
- Deterministic variation selected from a seed.

These are reasonable generic Cueverge features, but they are not implemented
until explicitly added and tested. For every requested effect, describe its
visible result, target geometry, timing, colors, direction, repetition, and
behavior under pause or seek.

## 4. Encounters and external events

An encounter is a state machine that turns observations into scene requests.
It has:

- An initial state.
- A cooldown state.
- An ambient fallback scene.
- An inactivity timeout.
- A cooldown duration.
- A global event debounce duration.
- An event freshness limit.
- Declarative state transitions.

Each transition currently supports:

- One event type.
- One or more eligible source states.
- One destination state.
- An optional scene request.
- A minimum amount of time in the current state.
- Exact-match scalar event attributes.

### Available Frigate observations

Cueverge normalizes live Frigate MQTT messages into:

| Event | Meaning | Common attributes |
|---|---|---|
| `frigate.object.new` | A tracked accepted object began | `label`, `camera`, `object_id` |
| `frigate.object.updated` | Tracking updated without a newly entered zone | `label`, `camera`, `object_id` |
| `frigate.zone.entered` | The object entered a new zone | `label`, `camera`, `object_id`, `zone` |
| `frigate.object.end` | Tracking ended | `label`, `camera`, `object_id` |

The live path has been validated for `person` observations from the doorbell
and garage exterior cameras. Observed zone names include `Porch`,
`Frontyard`, `Driveway`, `Sidewalk`, `Street`, and `East sideyard`. A show
should use only zones that have a deliberate narrative purpose.

### Event behavior supported now

- Duplicate source deliveries are idempotent.
- Stale observations are rejected.
- Repeated event types are debounced.
- A person or group can be treated as one active encounter.
- A porch-zone event can advance an encounter.
- An object-end event can enter cooldown.
- Inactivity can force cooldown.
- Cooldown expiry returns to ambient.
- The same flow can be tested with synthetic events and no camera.

### Encounter behavior not yet supported declaratively

- Boolean combinations such as “Porch and present for 8 seconds.”
- Numeric comparisons or arbitrary predicates.
- Time-of-day, weather, calendar, or show-enabled guards.
- Per-transition debounce or per-scene cooldown.
- Counting people.
- Correlating multiple Frigate objects as one group beyond the single active
  encounter policy.
- Weighted or non-repeating reaction selection.
- Automatic scene transition on media completion.
- Branching based on prior variant history.

If the creative design needs one of these, describe the desired rule plainly.
Do not simulate it with fragile camera-zone naming.

## 5. Priorities and interruption

Automatic encounter scenes pass through priority arbitration. Direct operator
scene starts are authoritative and bypass automatic arbitration.

Current automatic behavior:

- With no active scene, the incoming scene starts.
- If scenes claim a common resource, a strictly higher-priority incoming scene
  replaces the active scene.
- An equal- or lower-priority incoming scene with `queue` is queued once.
- Other equal- or lower-priority conflicting scenes are ignored.
- Stopping a scene can start the next queued scene.

Although resource claims are modeled, the runtime still has one active scene.
Non-conflicting resources do not currently produce simultaneous scenes.

A useful starting priority plan is:

| Range | Suggested purpose |
|---:|---|
| 90–100 | Emergency stop or shutdown behavior |
| 70–89 | Explicit operator or scheduled main show |
| 50–69 | Major interactive reaction |
| 30–49 | Suspicious, lookout, or low-impact interaction |
| 0–29 | Ambient behavior |

The show definition must provide a priority and desired interruption result for
every scene. It must also say what happens if a visitor arrives during a main
show, if an operator intervenes during an encounter, and if another detection
arrives during cooldown.

## 6. Timing and synchronization

All creative cue times should be expressed relative to the media timeline,
preferably in milliseconds. Cueverge currently renders lighting at 25 FPS from
bounded media-time estimates.

The installation has measured compensation for projector, audio, and lighting
latency. The accepted calibration reduced projector/audio median residual
alignment to approximately 7 ms in the test recording. This does not promise
that every cue is frame-perfect; projector processing, audio DSP, WLED refresh,
and network variability still exist.

For every sharp synchronized moment, provide:

- Media timestamp.
- What is visible in the projection.
- What is heard.
- Which roof segment or path reacts.
- Desired color and brightness shape.
- Acceptable timing tolerance.
- Whether the cue may repeat after a seek.

Lighting rendering is seekable and safe to reevaluate. Irreversible physical
effects such as fog, relays, pneumatics, or motors are not supported and must
not be modeled as ordinary lighting cues.

## 7. Show package and asset requirements

A private show package has this conceptual structure:

```text
show-package/
  installation.json
  encounter.json
  scenes/
    scene-id.json
  assets/
    private-media-files
```

The controller package and laptop asset cache are separate in the current
deployment. Scene asset paths are normalized relative paths; absolute paths,
backslashes, and parent traversal are rejected.

For each media asset, specify:

- Proposed file name and owning scene.
- Exact intended duration.
- Whether it should loop.
- Canvas resolution and frame rate.
- Codec/container delivery preference.
- Audio channel layout and loudness target.
- Whether silence is intentional.
- First and last visible frame behavior.
- Required black handles or transition frames.
- All sync-critical cue timestamps.
- Whether the content is original, licensed, or otherwise private.

Do not place copyrighted media, camera footage, credentials, private network
addresses, or house-specific geometry in the public demo package.

## 8. Required creative definition

Return a show definition containing all sections below. Narrative prose is
welcome, but the tables are required because they expose implementation gaps
and ambiguous transitions.

### A. Experience summary

- Show title and one-sentence premise.
- Intended emotional tone.
- Audience and viewing position.
- Typical visitor group size.
- Desired total visit length.
- Desired ambient-to-interaction ratio.
- Content rating and prohibited material.
- What should make the experience memorable.

### B. Operating modes

For each desired mode, state whether it is required for the first release:

- Continuous ambient.
- Visitor-triggered encounter.
- Scheduled main show.
- Manual operator scene.
- Quiet-hours behavior.
- Disabled/maintenance look.
- Startup and shutdown sequence.

### C. Encounter state table

Provide one row per state:

| State ID | Audience meaning | Entry condition | Scene or look | Maximum duration | Exit conditions | Timeout destination |
|---|---|---|---|---:|---|---|
| `ambient` | No active visitor interaction | Startup/cooldown complete | TBD | Unlimited | Person detected | N/A |

Include initial, intermediate, reaction, completion, cooldown, and failure
states. State IDs should be lowercase kebab-case and should describe narrative
meaning rather than camera implementation.

### D. Transition table

Provide one row per transition:

| From state(s) | Observation or condition | Required camera/zone | Minimum state age | To state | Scene request | Duplicate behavior |
|---|---|---|---:|---|---|---|
| `ambient` | New person | Doorbell or TBD | 0 ms | `suspicious` | TBD | Ignore |

Also answer:

- What constitutes sustained presence?
- What event means the visitor has left?
- Can a second person restart or advance the encounter?
- Can the encounter regress?
- What happens if tracking briefly disappears?
- What should happen when no porch-zone event ever arrives?
- How long is cooldown?

### E. Scene inventory

Provide one row per scene:

| Scene ID | Purpose | Duration | Loop? | Priority | Interruption | Trigger/from state | Completion/next state | Variants |
|---|---|---:|---|---:|---|---|---|---|

Scene IDs should be lowercase kebab-case. Include ambient, transition,
interactive, main-show, cooldown, startup, shutdown, and operator-only scenes
where applicable.

### F. Detailed scene sheets

For every scene, provide:

1. **Narrative action:** Beat-by-beat visible action.
2. **Window blocking:** Left window, right window, implied off-screen travel,
   entrances, exits, and eyelines.
3. **Media:** Duration, loop points, frame rate, audio, dialogue, music, and
   transitions.
4. **Lighting baseline:** Roof appearance when no accent cue is active.
5. **Cue timeline:** One row per synchronized cue.
6. **Completion:** Hold, stop, loop, transition, return to ambient, or await
   another event.
7. **Interruption:** Safe cut points and desired behavior if replaced.
8. **Failure look:** What should happen if media or lighting is unavailable.

Use this cue table:

| At time | Duration | Projection action | Audio action | Lighting target | Desired effect | Colors | Direction/path | Criticality |
|---:|---:|---|---|---|---|---|---|---|

Use timestamps such as `00:12.400` and durations in milliseconds. If an effect
does not exist yet, describe the desired appearance rather than choosing the
closest current effect.

### G. Variation plan

- Which scenes need variants?
- How many variants are needed initially?
- Random, weighted, shuffled, or deterministic rotation?
- How soon may the same variant repeat?
- Are variants interchangeable in duration and cue timing?
- Does variation occur at scene selection, inside media, or in lighting only?

### H. Priority and concurrency matrix

For every pair of operating categories, specify replace, queue, or ignore:

| Active category | Incoming category | Desired result | Reason |
|---|---|---|---|
| Ambient | Interactive encounter | Replace | Visitor response |
| Main show | Interactive encounter | TBD | |
| Interactive encounter | Operator command | Replace | Operator authority |
| Cooldown | New detection | TBD | |

### I. Recovery and edge cases

Explicitly decide:

- Visitor leaves during suspicious or reaction scene.
- Frigate emits duplicate, late, or out-of-order observations.
- Person never enters the target zone.
- Multiple cameras observe the same person.
- Media agent disconnects.
- Projector is unavailable.
- Audio output is disconnected.
- WLED is unavailable.
- Controller restarts during a scene.
- Show is stopped manually.
- Power returns after an outage.
- A scene asset is missing.

### J. Content-production specification

- Master canvas resolution and frame rate.
- Exact two-window mask and safe areas.
- Viewing-distance readability rules.
- Color and contrast rules.
- Audio loudness and low-frequency constraints.
- File naming and versioning convention.
- Review proxy format.
- Final delivery format.
- Copyright/licensing status.

### K. Operator controls

List the controls needed for:

- Enable/disable automatic encounters.
- Start ambient.
- Start each major scene.
- Start a full main show.
- Pause, resume, seek, and stop.
- Blackout or restore lighting.
- Maintenance/calibration mode.
- See health and current encounter state.

State whether each control is required in Home Assistant, a web UI, a command
line, or only through the API.

### L. Requested engine capabilities

List each behavior that is not obviously covered by current capabilities:

| Capability | Needed by scene(s) | Required for first show? | Desired behavior | Acceptable fallback |
|---|---|---|---|---|

Examples include playlists, completion transitions, scene loops, variant
selection, new lighting effects, multiple cue tracks, scheduling, time-of-day
guards, and richer presence logic.

### M. Open questions and assumptions

End the definition with:

- Questions requiring installation measurements.
- Questions requiring creative decisions.
- Assumptions that are safe for a prototype.
- Assumptions that must be validated before hardware activation.
- Features intentionally deferred from the first show.

## 9. Minimum definition needed to start implementation

Implementation can begin when the returned definition includes:

1. A complete encounter state and transition table.
2. A complete scene inventory with duration and priority.
3. Detailed sheets for the first ambient scene and one full interactive path.
4. Media canvas/frame-rate decisions or an explicit request to prototype them.
5. Cue timelines precise enough to identify required lighting primitives.
6. Completion and interruption behavior for every scene in that path.
7. A list of required engine gaps ranked as first-show blockers or later work.
8. Explicit confirmation that automatic hardware activation remains disabled
   until scene-by-scene validation is complete.

The first implementation pass should then build one end-to-end encounter path
with proxy media and simulated lighting before expanding the full scene pool.
