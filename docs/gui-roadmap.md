# Web controller and show designer roadmap

This roadmap describes a browser-based Cueverge control and authoring
application. It covers client management, installation mapping, asset
management, timeline-based show creation, visual simulation, deployment, and
LLM-assisted authoring through MCP.

The GUI is a new product surface over the reusable engine. It must not weaken
the existing separation between generic engine behavior, installation-specific
configuration, and private show assets.

## Product outcomes

An operator should be able to:

1. Discover, enroll, configure, and monitor Cueverge clients and downstream
   controllers on the local network.
2. Create an installation from one or more location photos.
3. Import and organize visual and audio assets.
4. Build scenes in a layered, time-based editor.
5. Author autonomous program, encounter, and opportunity behavior.
6. Preview deterministic media and lighting behavior without activating
   hardware.
7. Validate, rehearse, publish, roll back, and operate a show.
8. Optionally use an LLM through MCP to inspect and propose show changes under
   explicit permissions and approval.

The first release does not need to be a browser-based video compositor or
digital audio workstation. Cueverge can reference pre-rendered media, display
proxies and waveforms, and author the deterministic lighting and show-control
data that the current engine executes.

## Product and architecture principles

### Drafts are not live shows

The editor works on a revisioned draft project. Publishing compiles a draft
into an immutable, versioned show package and runs the same package validation
used by the controller. The browser never edits the active package in place.

Drafts may contain editor-only information such as photo annotations, panel
layout, proxy files, waveform caches, selection state, and comments. Runtime
documents remain portable declarative files and do not depend on the GUI.

### Discovery is not trust

Network discovery may find direct Cueverge media clients, hardware adapters,
and other Cueverge controllers, but a discovered endpoint is untrusted until
an operator enrolls it. Enrollment requires authenticated identity,
authorization, capability negotiation, and a visible trust decision.

Downstream controllers and direct clients should appear in one topology while
retaining their different responsibilities:

- A direct client exposes capabilities such as media playback or output.
- A downstream controller owns its local clients and accepts only the
  high-level commands that its declared contract permits.

The current v1 agent protocol does not provide authentication, authorization,
or capability negotiation. Those are prerequisites for non-loopback GUI
deployment and trusted enrollment.

### Author once, preview with the runtime

Timeline playback and visual simulation should use the production schema,
renderer, and director semantics wherever practical. A second implementation
of cue timing in the browser will eventually disagree with the controller.
The backend should expose deterministic evaluation and validation APIs; the
browser should render their results.

### Safety remains explicit

Editing and simulation are safe by default. Hardware activation requires a
separate rehearsal or live mode, clear output targets, preflight checks, and
operator confirmation. Blackout and stop remain available without navigating
away from the current screen.

## Proposed product areas

- **Operate:** health, active show, audience state, logs, manual transport,
  maintenance, and blackout.
- **Network:** discovery, enrollment, topology, capabilities, assignments, and
  connection diagnostics.
- **Design:** installation photos, geometry, resources, fixtures, paths, and
  calibration.
- **Assets:** media library, metadata, proxies, waveforms, provenance, and
  usage.
- **Timeline:** layered scene authoring, cue properties, keyframes, and
  transport.
- **Logic:** program pools, encounters, opportunities, scheduling, and flow
  diagnostics.
- **Simulate:** photo overlays, synthetic observations, accelerated time, and
  deterministic scenario playback.
- **Publish:** validation, package diff, deployment, rollback, and audit.
- **Assist:** permissioned MCP tools for analysis and proposed edits.

## Completion tracking

- `[x]` means the complete roadmap bullet is implemented and validated.
- `[ ]` means work remains. Bullets with meaningful implemented foundations
  include a **Partial** note but remain unchecked until the full statement is
  satisfied.
- Every implementation change must update the applicable milestone Scope and
  Acceptance checkboxes and the milestone summary in this file in the same
  change.
- Last backfilled against the repository and live GUI-11 deployment:
  **2026-10-16**.

| Milestone | Complete | Status |
| --- | ---: | --- |
| GUI-00 | 12 / 12 | Complete |
| GUI-01 | 13 / 13 | Complete |
| GUI-02 | 11 / 11 | Complete |
| GUI-03 | 33 / 39 | In progress |
| GUI-04 | 28 / 28 | Complete |
| GUI-05 | 25 / 27 | In progress |
| GUI-06 | 14 / 14 | Complete |
| GUI-07 | 19 / 19 | Complete |
| GUI-08 | 9 / 10 | In progress |
| GUI-09 | 17 / 17 | Complete |
| GUI-10 | 10 / 10 | Complete |
| GUI-11 | 13 / 13 | Complete |

## Milestone GUI-00: product contracts and secure foundation

Define the boundaries that every later editor feature relies on.

### Scope

- [x] Select a supported web architecture and deployment model. The controller may
  serve the built application, but frontend build concerns should remain
  separate from the Python engine package.
- [x] Define stable, versioned APIs for projects, drafts, validation, runtime
  status, topology, assets, simulation, and publication.
- [x] Add authenticated browser sessions, role-based authorization, CSRF and
  origin protection, rate limits, and an audit event model before allowing
  non-loopback access.
- [x] Define enrollment credentials and identity rotation for clients and
  downstream controllers.
- [x] Define capability documents for media, lighting, observation, simulation,
  and downstream control.
- [x] Define the editor project schema, runtime compilation boundary, migrations,
  and forward/backward compatibility policy.
- [x] Add revision IDs, optimistic concurrency, autosave, undo/redo transaction
  semantics, and crash recovery.
- [x] Establish browser support, accessibility, keyboard interaction, localization,
  and responsive-layout targets.

### Acceptance

- [x] Two browser sessions cannot silently overwrite the same draft revision.
- [x] An unauthenticated user cannot read private assets, inspect topology, change
  a draft, activate hardware, or invoke MCP tools.
- [x] Every change and control command records actor, target, result, and time.
- [x] A minimal draft can be compiled into a package accepted by the existing show
  loader without editor-only data leaking into runtime documents.

GUI-00 is complete at 12/12. The `/api/gui/v1` contract now covers projects,
draft revisions, compilation and validation, runtime status and operations,
topology and installations, assets and resumable uploads, deterministic
simulation scenarios, and immutable publication/package transitions. Browser
sessions, project authorization, mutation CSRF, allowed-origin checks, device
identity, typed capabilities, revision conflicts, audit attribution, schema
migrations, import/export validation, and deployment health checks protect
those surfaces.

## Milestone GUI-01: operator console and client management MVP

Deliver immediate operational value before building the full design suite.

### Scope

- [x] Display current mode, active and queued scenes, media time, audience state,
  opportunities, director diagnostics, connection state, and output health.
- [x] Provide start, pause, resume, seek, stop, reset, maintenance, and blackout
  controls with role checks and confirmation appropriate to impact.
- [x] Discover Cueverge endpoints through configured mechanisms such as explicit
  address entry and local service discovery.
- [x] Enroll or reject discovered endpoints; show identity, software/protocol
  version, capabilities, last seen time, latency, and clock/drift diagnostics.
- [x] For media clients, negotiate supported codecs and cache behavior and report
  total/free storage, content-addressed artifact inventory, pinned artifacts,
  active transfers, and checksum or storage faults.
- [x] Model direct clients and downstream controllers in a topology view.
- [x] Assign logical installation resources to enrolled capabilities.
- [x] Provide connection tests and simulated test patterns that require deliberate
  maintenance-mode activation.
- [x] Make unsupported, disconnected, degraded, and not-independently-detectable
  states visually distinct.

### Acceptance

- [x] An operator can enroll a direct media client and a downstream controller,
  distinguish them in the topology, and bind compatible logical resources.
- [x] Unenrolled or incompatible endpoints cannot receive show commands.
- [x] Disconnect, stale heartbeat, version mismatch, and failed command states are
  visible and actionable.
- [x] The existing controller operations can be performed from the GUI without
  changing their runtime semantics.

## Milestone GUI-02: project and asset workspace

Create the durable authoring model and an After Effects-style project panel.

### Scope

- [x] Create, duplicate, rename, archive, import, and export show projects.
- [x] Add folders, tags, search, favorites, and usage references for assets,
  scenes, installations, and reusable presets.
- [x] Upload assets in chunks with resumable transfer, size/type limits, checksum
  deduplication, malware scanning where deployed, and storage quotas.
  Processing runs asynchronously after atomic promotion and resumes finalized
  pending assets after controller restart. When ClamAV is deployed, clean,
  quarantined, and scanner-failure outcomes are persisted; otherwise scanner
  unavailability remains explicit.
- [x] Extract duration, dimensions, frame rate, codecs, channels, sample rate, and
  loudness metadata.
- [x] Generate thumbnails, low-resolution review proxies, and audio waveforms
  asynchronously without replacing the original asset.
- [x] Track processing status, errors, missing files, unused assets, and every
  scene that references an asset.
- [x] Record ownership, license/provenance notes, review status, and expiration
  where applicable.
- [x] Define relink, replace, version, retention, backup, and garbage-collection
  behavior.

### Acceptance

- [x] A user can import visual and audio assets, inspect metadata and waveforms,
  organize them, and place references without knowing server file paths.
- [x] Publishing rejects missing, failed, quarantined, or incompatible required
  assets with precise diagnostics.
- [x] Export followed by import preserves the project and asset references without
  exposing credentials or host-specific absolute paths.

GUI-02 is complete at 11/11. Asset ingest, processing, governance, placement,
portability, and project organization are complete. Publication fails closed
with distinct remediation for missing, quarantined, failed, incomplete,
unavailable, size-mismatched, unverified, checksum-missing, and
decoder-incompatible required assets.

## Milestone GUI-03: visual installation mapper

Map logical show geometry onto multiple location photographs.

### Scope

- [x] Add multiple photos per installation for wide views, close-ups, and
  alternate angles.
- [x] Store photo metadata, orientation, crop, scale hints, and named viewpoints.
- [x] Draw and edit outputs, pixel segments, fixtures, ordered paths, projector
  surfaces, masks, audience zones, and safety/no-output regions.
- [x] Model each projector's native content canvas, physical projection surface,
  output crop/mask, and mapping into every relevant photo viewpoint. Keep the
  transform from content to physical surface distinct from the transform that
  places that surface into a particular photo.
- [x] Define a shared stage coordinate system spanning all mapped projection
  surfaces. Each surface records its extent and transform in stage space;
  projector-native pixels and photo-view coordinates remain derived views.
  This gives animated elements one continuous logical space in which to move
  across projector boundaries.
- [x] Provide an easy projector-mapping mode in which the user drags the four
  corners of a selected still or asset frame onto the projected area in a
  location photo. Compute and store a projective transform so simulation can
  corner-pin any asset into that quadrilateral. Do not assume the projector is
  level or perpendicular to the surface: arbitrary roll, pitch, yaw,
  trapezoidal perspective, and mirrored mounting must be representable.
- [x] Allow additional control points, masks, and subdivided surfaces when one
  four-corner plane is insufficient. Clearly diagnose that a single homography
  cannot accurately model a curved, folded, or heavily occluded surface
  rather than silently presenting an incorrect simulation.
- [ ] Provide an advanced live alignment mode in maintenance mode. Send a
  generated grid with labeled corners, center marks, aspect-ratio guides, and
  safe-area markers to the selected projector, then let the operator adjust
  corner pin, rotation/orientation, crop, and mask from the UI while observing
  the real surface. Changes remain temporary until explicitly accepted.
  **Partial:** the mapper can request the existing 15-second alignment-grid
  preview with maintenance-mode and idle-runtime gates, and corner/crop edits
  remain local until saved. The preview is intentionally simulation-only; live
  device dispatch, temporary-session accept/cancel, and physical observation
  remain.
- [x] Update the selected photo-view overlay as live alignment controls move. If
  the operator supplies a fresh calibration photo, allow the final physical
  grid corners to be marked so the saved output warp and photo simulation
  transform describe the same projection.
- [x] Support multi-projector surfaces with explicit overlap regions. Add
  calibration patterns and controls for edge-blend ramps, overlap gamma,
  per-projector brightness and white balance, color correction, and black-level
  compensation so adjacent projectors can present one visually continuous
  surface.
- [x] Preview seams, double-bright overlap, blend falloff, black-level mismatch,
  and color mismatch in native and mapped views. Store blend/color calibration
  per projector and surface with the same provenance and stale-calibration
  rules as geometry.
- [x] Preserve native asset aspect ratio by default, show stretch and
  out-of-bounds warnings, and support overscan and intentionally masked
  content only through explicit settings.
- [x] Link the same semantic fixture across views using stable IDs and shared
  landmarks.
- [x] Provide layer visibility, colors, labels, snapping, ordering, lock/hide,
  zoom, pan, and measurement tools.
- [x] Add wiring-direction and pixel-index visualization, segment continuity
  checks, overlap/precedence diagnostics, and path traversal previews.
- [x] Define logical lighting surfaces with local coordinates and a transform into
  shared stage space. Each addressable pixel maps its physical output/index to
  a point or footprint on a lighting surface while retaining fixture, group,
  ordered-path, strand, direction, and calibration membership.
- [ ] Let installers draw strips and shapes, distribute a declared pixel count
  along a polyline or grid, place individual pixels, generate rows/rings/
  outlines, and import coordinates. Provide live identify and index-chase
  workflows for correcting positions and verifying physical order.
  **Partial:** the mapper generates rows, serpentine grids, rings, rectangular
  outlines, individual pixels, and imported normalized coordinates; declared
  pixels distribute along open or closed local paths without duplicating the
  ring endpoint. A maintenance-gated 15-second index-chase preview is
  integrated but remains simulation-only pending safe device dispatch.
- [x] Convert photo-assisted pixel placement into surface-local coordinates rather
  than making one photo's raster coordinates authoritative. Support multiple
  planar surfaces now and leave arbitrary 3D geometry as a future extension.
- [x] Add calibration workflows for pixel identification, projector alignment,
  output brightness limits, media start delay, and lighting timing offsets.
- [x] Define a generic timing-calibration capability for any enrolled output or
  actuator that reports support for it, including media, audio, pixels,
  network-controlled lights, smart switches, relays, and future physical
  effects such as fog. A device profile declares which onset and release
  delays can be adjusted, valid ranges and resolution, whether adjustment can
  occur while active, and its safe test procedure.
- [ ] Add a live timing-calibration workflow in maintenance mode. Play a
  purpose-built reference with an unambiguous on-screen flash and audio click,
  and trigger the selected device for the same authored timestamp. Let the
  operator adjust that device's command lead/lag while the test continues, or
  between explicitly armed trials when the device cannot be adjusted safely
  while active, so alignment can be judged immediately by eye and ear.
  **Partial:** maintenance/idle-gated temporary sessions, bounded expiry,
  baseline/proposed comparison, and explicit accept/cancel are implemented
  simulation-only. Purpose-built flash/click playback and live actuator
  triggering remain.
- [x] Provide coarse and fine delay controls, keyboard nudging, clear positive and
  negative direction labels, reset, and A/B comparison. Apply adjustments as
  temporary calibration overrides until the operator explicitly accepts them;
  canceling must restore the prior values.
- [x] Measure activation and deactivation separately where a device behaves
  asymmetrically. Device profiles may require warm-up, cooldown, minimum
  on/off time, a maximum trial count, manual reset, ventilation, consumable
  confirmation, or an observer pressing a marker when the physical effect is
  first visible.
- [x] Require explicit arming, a bounded test pattern, visible device identity,
  and an always-available device-appropriate stop before exercising physical
  outputs. Blackout is not assumed to make every actuator safe.
- [x] Store accepted calibration per resource with the test asset/version,
  device/firmware identity, timestamp, operator, observed variability, and
  optional notes. Warn when hardware, routing, display mode, media settings,
  or environmental conditions differ from those under which calibration was
  recorded.
- [x] Preserve manual mapping as authoritative; optional computer-vision-assisted
  alignment may make reviewable suggestions only.

### Acceptance

- [x] A user can define one installation from a wide photo and two close-ups, edit
  a fixture in any view, and see the same logical object update in all views.
- [x] In easy mode, a user can corner-pin an asset onto a photographed projection
  surface and preview other assets through the same mapping, including a
  projector installed at a non-level compound angle.
- [ ] In advanced mode, an operator can project the labeled grid, adjust the live
  output warp by eye, compare it with the photo overlay, and explicitly save
  or discard the alignment without restarting the media client.
- [x] For an overlapping two-projector surface, an operator can align geometry,
  tune edge blending and color/brightness matching, and preview the combined
  result without destructively changing either projector's source artwork.
- [x] The saved projector model records native canvas, orientation, output warp,
  photo-view transforms, crop, and mask independently enough that simulation
  can place content accurately without baking the photographed perspective
  into authored media.
- [x] Two calibrated projector surfaces occupy one shared stage space with
  explicit covered, overlapping, occluded, and uncovered regions suitable for
  cross-projector animation.
- [x] The mapper exports valid installation resources, segments, fixtures, and
  paths without embedding photo coordinates in runtime geometry.
- [x] A mapped LED installation preserves verified physical output order while
  exposing stable surface-local/stage positions, fixtures, and paths to show
  creators; switching photo viewpoints does not move the logical pixels.
- [x] A user can preview pixel order and path direction before any live output is
  enabled.
- [ ] In maintenance mode, an operator can select any calibration-capable enrolled
  device, run its declared safe test against the flash/click reference, change
  supported delays with immediate feedback when safe, and explicitly save or
  discard the calibrated values.
- [ ] A smart-switch-controlled light can be calibrated for both turn-on and
  turn-off latency. A non-repeatable or potentially hazardous device such as a
  fog machine cannot loop continuously and must obey its arming, cooldown,
  trial-limit, and emergency-stop requirements.
- [x] Saved offsets are included in validation and deployment; leaving or
  canceling calibration cannot leave transient offsets active.
- [x] Ambiguous, disconnected, out-of-range, or multiply assigned geometry blocks
  publication with a direct link to the offending object.

GUI-03 remains at 33/39. Photo-based geometry, projector and pixel mapping,
multi-view stage space, simulation, calibration records, and safe temporary
session semantics are implemented. The six remaining scope and acceptance
items require live dispatch and physical qualification for projector
alignment, pixel identification/index chase, timing calibration, and bounded
actuator-specific safety behavior.

## Milestone GUI-04: layered scene timeline MVP

Provide the core non-linear editor experience while compiling to supported
Cueverge scene semantics.

### Scope

- [x] Add a scalable playhead, ruler, zoom, scroll, snapping, markers, regions,
  selection, copy/paste, duplicate, trim, split where semantically valid, and
  keyboard editing.
- [x] Organize tracks by media output, audio reference, lighting output, fixture,
  path, marker, and annotation.
- [x] Add a composition hierarchy rather than requiring every edit in the full
  installation/photo view. The root scene composition coordinates all
  resources; a creator can open a projector, media output, lighting output,
  fixture, path, or reusable subcomposition and edit only its relevant layers
  and properties.
- [x] Give each composition a logical editing space appropriate to its target. A
  projector composition uses its native pixel canvas and safe areas; a pixel
  output uses ordered pixels and semantic geometry; a device composition uses
  its declared channels and states. Photo perspective and physical output
  warp are preview transforms, not the coordinates in which source content
  must be authored.
- [x] Add a stage composition for elements that span projection surfaces. A layer
  can be authored in stage space, surface-local space, or projector-native
  space, with the active coordinate system always visible. Converting or
  parenting between spaces must preserve the evaluated transform at the
  current time.
- [x] Keep the global playhead synchronized while moving between composition
  levels. Provide breadcrumbs, open-in-context and isolated views, a clear
  indication of inherited scene time, and a one-action return to the parent or
  full installation preview.
- [x] Allow reusable compositions to be instantiated in scenes with explicit
  start, duration, target binding, and supported parameter overrides. Define
  how local time maps to parent scene time, reject recursive composition
  references, and do not offer time stretching or remapping unless the runtime
  can represent it deterministically.
- [x] Add composition dependency management: show where each composition is used,
  distinguish a linked instance from a duplicated independent copy, display
  instance overrides and stale bakes, preview the impact of an edit, and block
  deletion while referenced unless the user deliberately replaces or removes
  those references.
- [x] Make named timeline markers shared synchronization anchors. Visual, audio,
  lighting, switch, and physical-effect cues may link to a marker with an
  explicit offset. Moving a marker moves linked cues atomically; creators can
  detach a cue without changing unrelated timing. Markers map predictably
  through nested composition time into explicit compiled timestamps.
- [x] Compile the composition graph into explicit runtime scene outputs and cues,
  or fail with a precise unsupported-composition diagnostic. Published
  packages must not require the runtime to interpret opaque editor state.
- [x] Represent each lighting cue as a movable and resizable layer with editable
  primitive, target, timing, color, brightness, blend mode, precedence, seed,
  and primitive-specific properties.
- [x] Show media thumbnails and audio waveforms for timing reference.
- [x] Add transform keyframes for 2D image, video, and nested-composition layers:
  position, independent X/Y scale, rotation, anchor point, opacity, and
  supported crop/reveal bounds. Support hold, linear, ease-in/ease-out, and
  temporal and spatial Bezier interpolation.
- [x] Display editable motion paths, handles, previous/next position ghosts, and
  snapping to landmarks, surfaces, seams, and named markers. Warn when a path
  enters an uncovered, occluded, or insufficiently calibrated region.
- [x] Keyframes are authored once in their declared coordinate space. Do not
  require users to duplicate a moving layer or create manual handoff fades for
  each projector. Static installation calibration warps remain separate from
  scene animation and are not ordinary transform keyframes.
- [x] Add property keyframes only where the runtime or GUI-05 baking pipeline can
  represent them deterministically; otherwise expose explicit boundaries
  rather than inventing preview-only behavior.
- [x] Add safe points, protected intervals, completion policy, request policy,
  scene priority, resource claims, required/optional outputs, clock source,
  and calibration offsets.
- [x] Provide reusable cue presets and compound selection without making presets a
  hidden runtime dependency.
- [x] Validate continuously and explain errors in the timeline and property panel.

### Acceptance

- [x] A creator can build a multi-output scene containing media, waveform
  reference, baseline lighting, overlapping fixture cues, a path sweep,
  markers, and safe transition points without hand-editing JSON.
- [x] A creator can open one projector composition, edit against its native canvas
  and focused timeline without displaying the location photo, then return to
  the root scene and see that output mapped onto every calibrated photo view.
- [x] Editing a reusable subcomposition updates its instances predictably;
  instance overrides remain visible, composition cycles are rejected, and the
  compiled package contains explicit supported runtime data.
- [x] Usage inspection identifies every parent and scene affected by a
  subcomposition edit, and a referenced composition cannot be silently
  deleted or converted from linked to independent.
- [x] A creator can align projection, audio, lighting, and device actions to one
  named marker, move that marker once, and verify all linked compiled
  timestamps and intentional per-cue offsets.
- [x] A creator can animate one semi-transparent 2D layer along a curved stage-space
  path from one projector surface, through their calibrated overlap, and onto
  a second surface without duplicating the layer or editing projector-native
  handoff keyframes.
- [x] Scrubbing to the same timestamp repeatedly produces the same cue state.
- [x] Save, reload, undo, redo, and compile preserve exact cue timing and ordering.
- [x] Unsupported operations are disabled or produce explicit schema-gap
  diagnostics rather than lossy output.

## Milestone GUI-05: advanced projector imaging and media baking

Turn a projector composition authored in GUI-04 into a deterministic,
calibration-aware media deliverable that remains practical for low-power
clients.

### Scope

- [x] Support image, video, and audio layers in projector compositions. Layers have
  explicit in/out points, ordering, position, scale, rotation, opacity, crop,
  and supported fades; audio layers have gain, mute/solo, fades, and an
  explicit mix order.
- [x] Preserve alpha channels from supported image and video sources throughout
  controller-side composition. Define premultiplied versus straight alpha,
  color space, frame rate, resolution, and scaling behavior so preview and
  final rendering agree.
- [x] Perform all layer and timeline editing through GUI-04 on the controller.
  Playback clients receive compiled assets and timing metadata, not editable
  composition graphs or a requirement to composite layers in real time.
- [x] Extend GUI-03 projector calibration with projection-object metadata.
  Surfaces may provide homography, mesh/UV warp, masks, occlusion regions,
  overlap regions, orientation, and calibration revision. Projector layers can
  target those surfaces without baking photo-view coordinates into source
  artwork.
- [x] Show the calibrated warp in native-canvas and installation previews. Make it
  clear whether a transform comes from a measured live grid, easy-mode corner
  pinning, or an unverified estimate.
- [x] Add controller-side render jobs that flatten the layer graph, alpha
  composition, masks, projection warps, color/output transforms, and audio mix
  into a muxed MP4 targeted to a specific projector profile.
- [x] For a stage-space layer visible to multiple projectors, evaluate one
  authoritative transform per frame, intersect it with each projector's
  calibrated coverage, and render the correct clipped and edge-blended
  contribution into each projector-specific bake.
- [x] Define one normative render pipeline used by preview and baking:
  1. Decode and conform source layers into the composition's declared frame
     rate, resolution, alpha interpretation, and working color space.
  2. Evaluate layer transforms, opacity, masks, and alpha composition in
     logical composition coordinates.
  3. Map layers onto calibrated physical objects and apply occlusion masks.
  4. Apply projector-native geometry warp, crop, and orientation.
  5. Apply multi-projector overlap and edge-blend masks.
  6. Apply projector-specific brightness, black-level, and color correction.
  7. Mix and conform audio, then encode and mux the target artifact.
  Changing this order requires an explicit versioned render-pipeline revision.
- [x] Make output codec, profile, level, pixel format, resolution, frame rate,
  audio codec, sample rate, channel layout, loudness target, keyframe policy,
  and hardware-decoder compatibility explicit. Provide conservative presets
  for enrolled low-power clients.
- [x] Treat bakes for one multi-projector composition as an output set with a
  common duration, frame rate, frame/time origin, render-pipeline revision,
  and activation identity. Prepare the complete set before allowing any member
  to activate.
- [x] Define a measured cross-projector synchronization tolerance. Use a shared
  prepare/play barrier and calibrated start offsets, record drift during
  rehearsal, and reject or warn on cross-projector motion when the selected
  clients cannot maintain the declared tolerance. Pre-baking removes live
  compositing load but does not by itself prevent independent clients from
  drifting.
- [x] Produce a render manifest containing source revision, asset checksums,
  composition settings, calibration revision, encoder identity/settings,
  duration, stream metadata, and output checksum. Invalidate or mark the bake
  stale whenever one of those inputs changes.
- [x] Cache identical renders, report progress and actionable failures, support
  cancellation, bound CPU/storage concurrency, and retain the last valid bake
  until a replacement completes.
- [x] Support explicit media end behavior including `stop`, `hold_last_frame`, and
  `loop`. A held final frame must remain visible without a black flash until
  the controller replaces or stops it. Loop validation inspects the first and
  last frames, audio boundary, encoder delay/padding, and keyframe placement;
  optional authored handles or a crossfade may be used when a truly gapless
  boundary is required.
- [x] Make renders reproducible and verifiable: pin the render-pipeline and encoder
  versions and settings, require every external dependency, seed variation,
  capture reference frame and audio checks, and compare selected timestamps
  within documented pixel, color, and audio tolerances. Never silently
  substitute a missing asset, font, calibration, or effect.
- [x] Use one pre-baked audio/video file when possible so the client performs
  simple prepare/play/seek operations and preserves a stable internal A/V
  relationship. Continue to apply measured per-device timing offsets for
  projector, audio path, lighting, and other outputs; baking does not pretend
  to remove physical hardware latency.
- [x] Treat alpha-bearing intermediate renders as authoring artifacts. The final
  projector MP4 is normally composited to the target output background because
  broadly compatible MP4 playback profiles do not provide a portable alpha
  channel.

### Acceptance

- [x] A creator can layer still images, alpha images, alpha video, opaque video,
  and multiple audio clips in a projector composition and edit them entirely
  through GUI-04.
- [x] The same composition produces matching controller preview and baked frames
  at defined test timestamps within documented color and pixel tolerances.
- [x] A layer mapped to a calibrated physical object uses the selected surface's
  warp and mask, including a non-level projector and a mesh that cannot be
  represented by one four-corner homography.
- [x] One stage-space animation produces a lockstep set of projector-native bakes;
  at every tested frame their clipped overlap contributions reconstruct the
  authoritative stage element after warp, blend, and color correction.
- [x] The controller creates a single compatible MP4 with mixed audio that a
  declared low-power client can play without live layer compositing.
- [x] Preview and bake execute the same versioned pipeline order, including
  edge-blend and per-projector color correction, and reference-frame tests
  detect changes outside documented tolerances.
- [ ] A projector client can hold the decoded last frame without flashing black.
  A file declared gapless passes first/last frame and audio-boundary checks
  and loops on the target client without an unintended visible or audible gap.
  **Partial:** the MPV client configures `keep-open=always` for last-frame hold
  and gapless audio plus file looping for loop playback. Render verification
  rejects first/last-frame, audio-boundary, timestamp, and GOP violations.
  Target-client playback still needs on-hardware qualification proving there is
  no decoder, display, or audio discontinuity.
- [ ] An on-hardware rehearsal measures cross-projector start error and drift and
  blocks a seam-sensitive animation when the selected client group exceeds
  its declared synchronization tolerance.
- [x] Repeating a bake with identical inputs is reproducible and cacheable. A
  source, timeline, encoder-profile, or calibration change marks the old bake
  stale and produces a separately checksummed artifact.
- [x] A failed or canceled bake cannot replace the last valid artifact, exhaust
  unbounded controller resources, or leave publication appearing ready.

GUI-05 remains at 25/27. Deterministic composition, baking, verification,
mapping, synchronization contracts, and client playback configuration are
implemented. Completion requires target-hardware proof of gapless/last-frame
behavior and measured multi-projector start error and drift enforcement.

## Milestone GUI-06: spatial addressable-light authoring

Let creators paint and animate addressable lighting spatially without encoding
physical LED order, while retaining precise path and fixture effects.

### Scope

- [x] Add lighting-surface compositions to GUI-04. Creators can edit a focused
  surface, fixture, path, output, or the full shared stage using the same
  playhead, markers, nested compositions, transform keyframes, masks, and
  blend modes as projector content.
- [x] Support three complementary authoring modes:
  - **Spatial canvas:** composite solid shapes, gradients, images, video or
    image sequences, animated masks, nested compositions, and deterministic
    procedural textures in surface-local or stage space.
  - **Ordered path:** animate normalized position, width, direction, fill,
    chase, pulse, and sweep behavior along verified semantic paths.
  - **Fixture/group:** apply reusable semantic looks and cues without exposing
    indices or geometry details.
- [x] Keep a direct physical-index view for installation diagnostics only; normal
  authored cues target spatial layers, paths, fixtures, or groups.
- [x] Allow raster layers and existing deterministic lighting primitives to
  coexist with explicit precedence and `replace`, `add`, `max`, and `multiply`
  blending.
- [x] Sample the final lighting canvas at each pixel's calibrated point or
  footprint. Support nearest, bilinear, and bounded area/footprint sampling so
  sparse pixels respond predictably when animated edges move between them.
- [x] Define one deterministic lighting pipeline:
  1. Evaluate stage- and surface-space layers at authoritative scene time.
  2. Transform shared stage content into each lighting surface.
  3. Composite raster, procedural, path, fixture, and persistent-look layers.
  4. Sample colors at logical pixel positions.
  5. Apply per-pixel/fixture color calibration, gamma, and brightness; report
     normalized load and device-reported limiting while electrical power
     limits remain device-owned.
  6. Reorder the frame into verified physical output/index order and send it
     through the configured adapter.
- [x] Show normalized channel load, calibration clipping, device-reported
  limiting/scaling,
  unmapped or duplicate pixels, sampling footprints, output boundaries, and
  stale geometry calibration.
- [x] Keep evaluation deterministic and seekable under pause, seek, reconnect, and
  simulation. Establish frame-rate and pixel-count performance budgets before
  considering optional pre-baked lighting frame sequences.
- [x] Let one stage-space layer contribute to both projector and lighting
  compositions. Each output samples or renders that shared layer through its
  own calibrated geometry while retaining its own color and safety limits.

### Acceptance

- [x] An installer maps a multi-strand LED shape, verifies it with an index chase,
  and corrects one misplaced pixel without changing authored cues.
- [x] A creator paints an animated 2D gradient and moving shape over a facade,
  previews the sampled LED colors, and produces the same frame after repeated
  evaluation of the same timestamp without referring to physical indices.
- [x] The same show combines a spatial raster wash, a topology-aware path chase,
  a semantic fixture pulse, and a persistent look with deterministic blending.
- [x] One animated stage-space object moves from projected content into an
  addressable-light surface; projector pixels and LED samples remain aligned
  to the calibrated physical path in simulation.
- [x] Gamma and brightness occur after composition but before physical output
  ordering, normalized load is visible in preview, and any device-reported
  limiting cannot be bypassed by a raster layer. Electrical limits remain
  configured and enforced by the output device.

## Milestone GUI-07: visual, lighting, and audio simulation

Make the photo mapper and timeline a trustworthy rehearsal environment.

### Scope

- [x] Overlay simulated pixel colors, fixtures, paths, projector surfaces, masks,
  and labels on any installation photo.
- [x] Provide preview modes for the current logical composition, its parent
  context, a selected native projector canvas, a selected device/output, and
  the full mapped installation. Switching preview modes must not change
  authored coordinates or timing.
- [x] Support instant switching and synchronized side-by-side playback of multiple
  viewpoints.
- [x] Show stage-space motion paths and the per-projector contributions together,
  including coverage, overlap, occlusion, and uncovered-region warnings.
- [x] Composite review proxies into mapped projector surfaces using GUI-03's
  content-to-surface and surface-to-photo transforms. Label the result as an
  approximation unless the photo viewpoint and physical grid were calibrated;
  expose stale or missing projector calibration instead of silently falling
  back to an apparently precise overlay.
- [x] Play proxy audio and display meters and waveform position. Browser audio is
  for editorial preview, not a claim of hardware synchronization.
- [x] Preview either the editable controller composition or its selected GUI-05
  baked artifact, with optional side-by-side/frame-difference inspection. The
  editable preview and bake must share composition, alpha, warp, mask, and
  audio-mix semantics rather than maintaining unrelated renderers.
- [x] Preview GUI-06 lighting canvases, sampled LED points/footprints, paths,
  fixtures, power limiting, and physical output order together with projector
  content in the same calibrated stage and photo views.
- [x] Drive the preview from backend deterministic scene evaluation and expose
  frame rate, degraded resources, and timing diagnostics.
- [x] Support play, pause, seek, loop region, speed, frame/step controls, and
  before/after comparison.
- [x] Integrate synthetic observations, accelerated time, client disconnects,
  output failures, and operator overrides from the existing simulation
  harness.
- [x] Save repeatable simulation scenarios and expected outcomes.

### Acceptance

- [x] Timeline, waveform, photo overlay, and controller-evaluated scene time remain
  aligned during play, pause, seek, and loop.
- [x] A semi-transparent element can cross two projectors and their edge-blended
  overlap in simulation as one continuous stage-space motion, while focused
  projector previews show the corresponding clipped contributions.
- [x] A shared stage-space element can continue from a projector onto an LED
  surface, with the photo view, logical lighting canvas, sampled LEDs, and
  deterministic output frame agreeing at selected timestamps.
- [x] The UI identifies whether it is previewing source layers or a particular
  checksummed bake and warns when that bake is stale.
- [x] A saved scenario can reproduce the same scene history and simulated lighting
  frames.
- [x] Simulation never opens hardware adapters or sends commands to enrolled
  clients.
- [x] The UI clearly distinguishes approximate visual compositing from measured
  output behavior.

## Milestone GUI-08: autonomous show logic designer

Extend creation from individual scenes to complete unattended shows.

### Scope

- [x] Edit program pools, weights, categories, cooldowns, history exclusions,
  availability, and eligibility conditions.
- [x] Edit audience filters, presence timing, opportunities, cooldowns, and
  consumption rules using semantic fields rather than raw detector payloads.
- [x] Build encounter states and transitions in a node/flow view with an
  equivalent accessible tabular editor.
- [x] Visualize completion transitions, fallbacks, safe-point arbitration,
  priorities, resource conflicts, and unreachable or cyclic logic.
- [ ] Add scheduling and quiet-hours design once the runtime gains an authoritative
  scheduling model.

The scheduling and quiet-hours item is deferred to a separate runtime-design
milestone rather than introducing controller-only semantics in GUI-08.
- [x] Provide templates for ambient loops, visitor-triggered interactions, manual
  operator scenes, and scheduled main shows.
- [x] Run deterministic long-duration simulation and summarize repetition,
  cooldown, dead-end, starvation, and unavailable-resource behavior.

### Acceptance

- [x] A creator can build the complete autonomous show package described in the
  show creator guide without editing JSON.
- [x] The editor identifies missing fallbacks, invalid references, unreachable
  states, resource conflicts, and empty eligible pools before publication.
- [x] A repeatable accelerated scenario demonstrates ambient operation, visitor
  interaction, protected scenes, return to idle, and client interruption.

GUI-08 remains at 9/10. Semantic autonomous programs, audience rules,
opportunities, encounters, templates, diagnostics, and accelerated simulation
are complete. Scheduling and quiet-hours authoring remains intentionally
blocked on a separate authoritative runtime scheduling model.

## Milestone GUI-09: rehearsal, publication, and rollback

Turn an edited project into a controlled deployed artifact.

### Scope

- [x] Add named revisions, change summaries, schema migrations, package diff, and
  approval state.
- [x] Compile a draft into an immutable package with a manifest, asset checksums,
  schema versions, creator, timestamp, and required capabilities.
- [x] Run structural, semantic, asset, capability, and safety validation before
  publication.
- [x] Require current successful bakes for projector compositions and verify that
  each target client's declared decoder capabilities match its baked media
  profile.
- [x] Separate publish, stage to clients, activate, deactivate, and roll back.
- [x] Define a content-addressed client cache contract. The controller sends a
  required-artifact manifest; the client reports inventory and capacity,
  downloads missing artifacts to temporary storage with resumable ranges,
  verifies size and checksum, and atomically promotes them into its cache.
- [x] Pin every artifact used by the active package, the configured rollback
  package, active playback, and an in-progress activation. A client may evict
  only unpinned artifacts, using a documented policy such as least recently
  used, and must never delete a playing or rollback-critical file to make room.
- [x] Reconcile inventory after reconnect, distinguish absent, partial, corrupt,
  verified, pinned, and in-use states, and expose transfer progress and
  actionable insufficient-space or integrity errors. Support explicit
  operator cleanup without allowing arbitrary filesystem access.
- [x] Stage only required artifacts, deduplicate identical checksums across
  packages, estimate transfer size/time, and require every necessary client to
  report verified readiness before activation. Retain the previously active
  package until the replacement has activated successfully.
- [x] Add rehearsal mode with explicit target selection, preflight checklist,
  countdown, stop, blackout, and post-run diagnostics.
- [x] Maintain a deploy-time dependency inventory that drives the controller
  Dockerfile and image preflight. Pin and report required native tool versions,
  install `ffmpeg`/`ffprobe` for asset processing and rendering, explicitly
  configure optional ClamAV plus definition updates, keep frontend tooling in
  the build stage, and keep host display/audio dependencies such as `mpv` on
  media agents rather than in the headless controller image.
- [x] Add backup/restore and disaster-recovery documentation for projects, assets,
  enrollment identities, and published packages.

### Acceptance

- [x] A failed validation, transfer, or activation cannot partially replace the
  active show.
- [x] Given a nearly full low-power client, staging a new package evicts only
  unpinned artifacts, resumes an interrupted transfer, redownloads a corrupt
  artifact, verifies its checksum, and preserves the active and rollback
  packages throughout. Insufficient safe space blocks activation explicitly.
- [x] An operator can compare a candidate with the active revision, rehearse it,
  publish it, verify client readiness, activate it, and roll back.
- [x] Every deployment action is attributable and its package can be reproduced
  from stored revision and asset checksums.
- [x] Live activation is impossible from an unapproved draft or from simulation
  mode.

Multi-client activation uses an explicit two-phase contract: every client
prepares the checksummed artifact against one shared activation timestamp
before any commit is issued. A failed prepare leaves the active show unchanged;
a failed commit triggers rollback commands that atomically restore each
client's saved prior activation before the controller changes package state.

GUI-09 is complete at 17/17. The machine-readable native dependency inventory
drives pinned required package installation and startup verification of
executables, FFmpeg encoders and filters, FFprobe features, and optional ClamAV
definitions. Builder/runtime base images are digest-pinned and installed native
tool versions are reported at startup. Publication, staging, two-phase
activation, rollback, cache safety, rehearsal, attribution, and disaster
recovery are complete.

## Milestone GUI-10: collaboration and production hardening

Harden the application for real creative and operational teams.

### Scope

- [x] Add project roles such as viewer, operator, designer, publisher, and
  administrator, with separate permission for live output.
- [x] Add comments, review links, change requests, revision comparison, and
  presence indicators before considering real-time co-editing.
- [x] Add accessibility testing, complete keyboard workflows, reduced-motion and
  high-contrast support, and screen-reader alternatives to spatial and flow
  editors.
- [x] Add observability for API latency, background asset jobs, WebSocket health,
  browser errors, storage use, simulation performance, and publication.
- [x] Define project and asset scale targets, then test long timelines, many
  assets, many clients, and multiple photo views against measurable budgets.
- [x] Add import/export compatibility tests and migration rehearsals for every
  supported project and package version.

### Acceptance

- [x] Roles are enforced on the server, not only hidden in the UI.
- [x] A keyboard-only user can complete core operator and timeline workflows.
- [x] Supported maximum-size projects meet documented interaction, save,
  simulation, and publication budgets.
- [x] Backup restoration and schema migration are tested against representative
  production projects.

GUI-10 is complete at 10/10. Controller-managed local identities preserve the
configured administrator as a recovery account. Explicit project memberships
govern project visibility, drafts, review, rendering, simulation, publication,
and activation on the server; live output remains a separate grant. Comments,
change requests, expiring review links, and presence heartbeats are available
in the project workspace. The administrator-only observability snapshot covers
bounded API latency and outcomes, asset/render states, device WebSocket health,
browser error reports, storage, simulations, and publication. Browser errors
are attributed in the durable audit log. Supported import/export versions,
schema migrations, and a full data-root backup restoration are exercised by
tests. Automated WCAG scans and keyboard-only operator and timeline scenarios
cover accessibility alongside forced-colors, reduced-motion, focus, and
semantic spatial/flow alternatives. Measured scale qualification covers 250
scenes, 10,000 timeline markers, 5,000 assets, 100 connected device sessions,
12 mapper viewpoints, 100-step deterministic simulations, and maximum-size
publication. Timeline and asset collections use bounded rendering windows, and
the timeline implementation is lazy-loaded so the primary application bundle
remains below the production warning threshold.

## Milestone GUI-11: MCP-assisted show creation

Expose a narrow, auditable authoring interface for LLM clients after the
underlying GUI APIs and transaction model are stable.

### Scope

- [x] Run an authenticated MCP server next to the controller's authoring service;
  do not expose browser internals or unrestricted filesystem/network tools.
- [x] Provide read tools for project structure, capabilities, schemas, asset
  metadata, scene summaries, timeline ranges, validation results, simulation
  results, and published revision status.
- [x] Provide proposal tools for creating or changing scenes, cues, mappings,
  show logic, metadata, and simulation scenarios.
- [x] Apply proposals to a new draft transaction with a structured diff. Require
  validation and explicit human approval before merging into the working
  revision.
- [x] Separate scopes for read, draft edit, simulation, publish preparation, and
  live operation. Do not grant publish or live-output permission by default.
- [x] Treat asset metadata, filenames, comments, imported package text, and model
  output as untrusted content. They cannot alter tool permissions or bypass
  validation and approval.
- [x] Add limits for tool-call count, payload size, asset access, timeline range,
  simulation duration, and concurrent work.
- [x] Record model/client identity, user identity, requested tools, resulting
  diffs, validation, approvals, and final disposition.
- [x] Provide high-level task tools such as:
  - create a scene outline from an approved brief
  - align cues to supplied markers or waveform events
  - suggest fixture/path mappings for operator review
  - diagnose validation and simulation failures
  - generate a test scenario for a selected interaction path
  - summarize differences between revisions

### Acceptance

- [x] An MCP client can create a useful proposed scene in an isolated draft, run
  validation and simulation, and present a human-readable diff.
- [x] The same client cannot read assets outside its project, enroll devices,
  publish, activate hardware, or bypass approval without separately granted
  scopes.
- [x] Rejected or failed proposals leave the working revision and live show
  unchanged.
- [x] Every accepted change can be traced to its user, MCP client, proposal, and
  approval.

GUI-11 is complete at 13/13. Cueverge serves authenticated, stateless MCP over
Streamable HTTP with project-bound, hashed, expiring tokens and independently
enforced read, proposal, direct-edit, simulation, publish-preparation, publish,
activate, and reserved live scopes. Direct-edit, publication, and activation
are separately opted in and checked against current GUI-equivalent permissions;
legacy tokens gain no permissions. Bounded tools operate only on authoritative
project models; there is no unrestricted filesystem, arbitrary network,
browser-internal, device-enrollment, access-administration, or scene-transport
tool. Candidate
drafts, changed JSON paths, diagnostics, and deterministic no-hardware
simulations remain isolated until a CSRF-protected human review validates,
approves, and merges them. Revision conflicts fail closed. Per-token request,
rate, concurrency, project asset, timeline, simulation, and active-proposal
limits are enforced, and durable audit events attribute requests and final
disposition to the user, MCP client, proposal, and human reviewer. The project
workspace provides one-time token issuance/revocation and proposal review.
Opt-in `direct_edit` creates a revision immediately with schema validation and
optimistic concurrency rather than proposal merge. `publish` permits release
approval, validated publication, and staging; `activate` additionally requires
live-output authorization and permits staged artifact activation, not controller
catalog replacement. Proposal approval remains a human-only GUI operation.

## Dependency order

```text
GUI-00 -> GUI-01
GUI-00 -> GUI-02 -> GUI-04
GUI-01 + GUI-02 -> GUI-03
GUI-03 + GUI-04 -> GUI-05
GUI-03 + GUI-04 -> GUI-06
GUI-03 + GUI-04 + GUI-05 + GUI-06 -> GUI-07
GUI-04 + GUI-07 -> GUI-08
GUI-01 + GUI-02 + GUI-05 + GUI-06 + GUI-07 + GUI-08 -> GUI-09
GUI-09 -> GUI-10
GUI-00 + GUI-02 + GUI-04 + GUI-05 + GUI-06 + GUI-07 + GUI-08 + GUI-09 -> GUI-11
```

GUI-01 and GUI-02 can proceed in parallel after the foundation. GUI-03 can
start once the resource/capability and project models are stable. MCP follows
the normal authoring APIs rather than becoming a second privileged path into
the engine.

## Suggested release cuts

### Release A: operator MVP

GUI-00 and GUI-01: authenticated operations, topology, enrollment, health, and
manual control.

### Release B: scene creator MVP

GUI-02 through GUI-07: projects, assets, photo mapping, timeline editing,
advanced projector compositions, spatial lighting, pre-baked media, and
hardware-free visual simulation for individual scenes.

### Release C: complete show workflow

GUI-08 and GUI-09: autonomous logic, rehearsal, immutable publication,
deployment, and rollback.

### Release D: team and assisted authoring

GUI-10 and GUI-11: production collaboration, scale/accessibility hardening,
and permissioned MCP assistance.

## Cross-cutting work not to defer

- **Security:** authenticated identities, least privilege, secret storage,
  secure enrollment, dependency scanning, upload handling, and audit.
- **Data durability:** atomic saves, revisioning, migrations, checksums,
  backups, recovery, and storage-capacity alarms.
- **Safety:** simulation by default, explicit live mode, output limits,
  device-appropriate emergency stop, and no irreversible effects through
  ordinary cue tools. Physical-effect adapters require capability-specific
  arming, interlocks, duty-cycle limits, and failure behavior.
- **Performance:** virtualized project/timeline views, proxy media, background
  jobs, bounded status streams, and documented scale targets.
- **Accessibility:** keyboard editing, focus management, non-color status
  indicators, text equivalents for visual maps, captions, and reduced motion.
- **Privacy and rights:** private asset isolation, retention controls,
  provenance/license fields, and no automatic transfer of media to LLM
  providers.
- **Testability:** schema fixtures, browser component tests, end-to-end editor
  flows, deterministic simulation, protocol compatibility, and deployment
  failure injection.

## Additional suggestions captured by the roadmap

The original feature list implies several supporting capabilities that are easy
to overlook:

- Secure discovery and enrollment rather than discovery alone.
- Capability negotiation and compatibility diagnostics.
- Revision history, autosave, undo/redo, package diff, and rollback.
- Media proxies, waveforms, checksums, provenance, and missing-asset handling.
- Calibration tools for photos, projectors, pixels, brightness, audio, and
  timing.
- Reusable templates and presets.
- Autonomous show-flow authoring in addition to per-scene timelines.
- Repeatable failure and audience scenarios, not only a happy-path preview.
- Preflight, staging, rehearsal, immutable publication, and disaster recovery.
- Role separation between designer, operator, and publisher.
- Accessibility, keyboard-first editing, performance budgets, and observability.
- A scoped proposal-and-approval MCP workflow, with independent opt-in scopes
  for direct draft editing, publication/staging, and artifact activation.

## Deliberately deferred

- Full browser video compositing, color grading, motion graphics, or
  frame-accurate media rendering comparable to After Effects.
- Destructive editing of original media.
- A general-purpose audio workstation, independent live mixing, or a promise
  that browser preview audio matches venue timing.
- Automatic trust of devices discovered on a network.
- Multi-user real-time co-editing until revision and conflict semantics are
  proven.
- LLM access to raw private media by default.
- LLM publication, client enrollment, or live hardware activation without
  explicit narrowly scoped authorization and human confirmation.
- Modeling fog, motors, relays, pneumatics, or other irreversible effects as
  ordinary seekable timeline cues. Such devices may participate in bounded
  live calibration only after a dedicated adapter defines its safety,
  interlock, arming, timing, and stop semantics.
