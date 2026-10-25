# Installation and show operator guide

This guide follows the operator's workflow: prepare an installation, create a
show, rehearse it, release it, and run it safely. For host setup, networking,
accounts, backups, and upgrades, use the
[service administration guide](service-administration.md).

It documents the current application, not a promise that every roadmap item
is available. Read the [current boundaries](#current-boundaries) before
planning a live opening.

## Contents

- [Orientation and safety](#orientation-and-safety)
- [Creating an installation](#creating-an-installation)
- [Managing fixtures and installation objects](#managing-fixtures-and-installation-objects)
- [Connecting camera events](#connecting-camera-events)
- [Creating and organizing a show](#creating-and-organizing-a-show)
- [Authoring scenes and timelines](#authoring-scenes-and-timelines)
- [Authoring autonomous behavior](#authoring-autonomous-behavior)
- [Previewing and rehearsing](#previewing-and-rehearsing)
- [Publishing and handing over](#publishing-and-handing-over)
- [Running a show](#running-a-show)
- [Current boundaries](#current-boundaries)


## Managing fixtures and installation objects

Open **Installation > Objects**, then explicitly choose the show whose working
draft you want to inspect. From an open show, **Manage installation objects**
opens the same inventory with that show selected. It never silently chooses the
currently playing show.

The inventory includes fixtures, segments and ordered paths created through the
UI, REST API or MCP, including **Not placed** objects. Search by name, stable ID,
type or output. Inspect an object to see membership, pixel traversal and effect/
authoring reference paths. Viewers can inspect; project designers, publishers and
administrators can edit unarchived shows.

Use **Create fixture or path** to group existing segments on one saved LED output.
In **Object properties**, add/remove members, move them up/down, reverse their
traversal and give the object a friendly name. Fixture precedence is added to cue
precedence for overlapping effects. IDs stay stable during editing. Segments are
read-only here: this editor does not repartition wiring or import runtime bindings.

**Show in Layout** highlights an object's mapped member segments and explicitly
linked geometry. A separate marker is optional. To place an unplaced fixture,
use **Place in Layout**, select existing geometry under **Link existing layout
geometry**, then **Save object**. If geometry does not exist yet, draw it in
**Photos & Layout** first, then link it from Objects. No photo is required.
An unmapped fixture remains a valid effect target.

Definitions, friendly names and optional semantic geometry links belong to the
**show's working draft**. Geometry, wiring and calibration belong to the
**installation**. Saving a definition creates a revision; it does not publish,
apply, or change live playback. A stale revision is rejected; refresh and
reconcile before retrying. Archived shows remain inspectable but cannot be edited.

Clear a geometry checkbox to unlink it. **Remove placement** in Layout removes
geometry only, never the logical definition or its effects. Missing links are
reported so they can be cleared or replaced. **Delete definition** is separate:
effect/authoring references block deletion until removed, and deleting an unused
definition never deletes installation geometry.

## Orientation and safety

### The objects you work with

| Object | Meaning |
| --- | --- |
| Installation | Physical resources and their mapping: projectors, lighting, geometry, viewpoints, assignments, and calibration |
| Logical resource | Stable show-facing name, such as `projector.demo`; not an IP address or a device credential |
| Project | Revisioned working show, assets, editor metadata, comments, and access permissions |
| Scene | A media-synchronized or controller-timed unit with duration, resources, lighting, interruption, and completion behavior |
| Composition | Reusable time-based content in projector-native, lighting-surface, or shared-stage coordinates |
| Revision | Persisted version of a project; the basis for review, render, and publication |
| Published package | Immutable release snapshot; publication, staging, activation, and playback are different steps |

Editing a draft does not edit the active runtime package. Browser preview
time is not venue playback time. During actual playback, lighting follows the
designated media output's reported clock, or the controller's monotonic clock
when the scene explicitly selects controller timing.

### Navigation

![Overview showing a stopped synthetic installation and disconnected media resource](assets/gui/operator-overview.png)

*Screenshots illustrate earlier implementation checkpoints; Overview/header
were simplified in UP-02 and Shows in UP-03. All captures use the integrated browser
against an isolated controller using synthetic demo data. No live hardware
was connected. Empty, disconnected, and blocked states are intentional.*

- **Overview:** a saved installation photo, current or explicitly last-known
  playback, equipment connections, useful destinations, and compact activity.
  Open **Setup checklist** or **System details** for optional setup facts.
- **Installation:** camera inputs/connections, topology, photos, mapper, device management, assignments,
  logical object inventory, and connection diagnostics.
- **Shows:** a compact library; **Open show** leads directly to scenes, lighting
  effects and a safe preview. Assets, show options, sharing, and advanced
  authoring/history remain available when needed.
- **Operate:** live transport, operating mode, maintenance simulation,
  per-output status, and runtime diagnostics.
- **Settings:** administrator-managed local users and installation records.

Live runtime and device snapshots refresh independently about every two seconds
while the tab is visible. Setup/activity refresh about every 30 seconds.
After ten seconds without an updated snapshot, the UI marks it out of date;
failed requests report unavailable status and retain last-known information
without an unqualified green live claim. Requests time out after eight seconds.
Background polling pauses in hidden tabs and refreshes on return.
Use **Refresh status** to retry, not as a prerequisite for normal monitoring.

The header names the single runtime-backed installation without pretending
additional planning records can be selected as controllers. Open the avatar's
**Account** disclosure for role and **Sign out**; Escape dismisses it and
returns focus. Session expiry stops automatic live requests and offers an
explicit return to sign-in, warning before unsaved local edits are discarded.

Controller connected does not mean equipment is ready or physically producing
output. Overview and Installation use the same topology connection facts;
**Offline · on demand** describes connection policy, not confirmed readiness.
Activity audit outcomes are not device-health badges. Repeated connection
changes are grouped with every event retained in audit details; routine token/
tool administration stays in **More activity**.

Managed Apply reports the controller's acknowledged loaded show identity.
Legacy/external catalogs remain unreported; saved edits and active artifact
labels do not establish identity.
**Open controls** or **Open Operate** provides access to transport and blackout; the
current application does not provide a global emergency-stop control on every
editor screen. Stale status blocks ordinary playback/mode changes; authorized
Stop and Blackout remain available, but a broken network cannot guarantee
delivery. Follow the physical safety procedure when needed.

### Before any physical output

- Confirm who has authority to operate and who can invoke the site's physical
  safe-stop procedure.
- Verify the exact installation, resource assignments, show revision, and
  operator permissions.
- Inspect projector aim, audience access, pixel wiring/power, brightness, audio
  level, and any installation-specific hazards.
- Keep a physical safety procedure independent of the browser and network.
  Software blackout is not an electrical interlock.
- Do not use a browser grid, simulation result, or green controller badge as
  evidence that a speaker, projector, or real pixel has been checked.

## Creating an installation

### 1. Establish the running installation

An administrator first deploys the controller with a validated runtime package
and enables the necessary adapters. The active installation is derived from
that package. On a fresh synthetic deployment it is named `demo`; older
screens use "Home" for this runtime-backed installation.

Use **Settings > Your installation > Installation settings** to change the
display name. Folder, tags and favorite are under **Organization (optional)**.
People/project access administration is also collapsed by default.

To create an additional planning record:

1. Open **Settings > Additional installation records (advanced)** as administrator.
2. Enter **New installation name**, then choose **Create installation**.
3. Set its folder, comma-separated tags, and favorite state as needed; save.

**Expected result:** a durable **Planning record** with no controller attached. It cannot
be selected as a running installation in the header. This action does not
attach a package, change the active installation, or deploy another controller.
There is no end-to-end GUI provisioning wizard for that handover yet; involve
the technical operator before planning live output from the new record.

The runtime-backed record cannot be deleted. Do not delete a record that
another project or installation artifact still uses.

### 2. Verify resources and devices

Open **Installation > Devices** and compare **Configured outputs and logical
resources** with the physical
inventory. Record resource names, media hosts, native output sizes, lighting
pixel counts, segment ranges, fixtures, paths, and intended clock source.

An administrator enrolls devices, reviews capabilities, and assigns identities
to logical resources. Discovery never grants trust. Assignments must be
changed with playback idle and the device disconnected; follow
[device enrollment](service-administration.md#enrolling-and-maintaining-media-hosts).
Controller-owned DDP destinations remain private deployment configuration,
not photo geometry.

Device cards show identity, display role and connection state. Open **Device
details** for protocol, formats, storage, transfers, certificates and timing.
Compatibility conflicts, transfer failures, storage faults and diagnostic messages
remain visible without opening details. **Add a device** opens the enrollment
guide: choose its kind, load/review its capability file, then create a one-time
token. Manual capability JSON is under **Advanced declaration editing**; the
example template is not a device observation. A compatible declaration does not
mean connected or ready. **Assign display roles**, connection expectations/checks
and advanced suspend/revoke controls are separate contextual disclosures.

**Expected result:** the correct enrolled devices and resources appear in
topology, with fresh connection/capability information. Independently check
video, audio, and pixels; "not independently detectable" is not a fault to
hide or a health check to waive.

### 3. Optionally add or replace reference photos

1. Open **Installation > Photos & Layout** and choose **Add photo**.
2. **Choose photo** to select a JPEG, PNG, or WebP that you own or have
   permission to use. The description defaults to the file name; replace it
   with something useful, such as "Wide front view for roofline placement".
3. Choose **Add installation photo**.
4. Open **Photo details** to edit its description. Orientation, crop and scale
   are optional under **Image and calibration details**; choose **Save photo**
   after adjustments. Existing view IDs/legacy labels and mapping metadata are
   preserved by a description-only edit.
5. Add useful alternate angles or close-ups, within the 12-photo budget.

Use non-sensitive photos where practical. Photos describe viewpoints, not
separate physical outputs. Photos are compact cards: the open photo is
outlined, and the stage view (see below) carries a **Stage view** badge.
Photos are optional: **Open layout without a photo** opens an editable
installation canvas immediately. **Open Layout** on a card uses that image as a
reference background for the same installation geometry. Reload the page after
external/MCP changes; revision conflicts never silently overwrite another edit.

To update an image, choose **Replace photo** on its card (also available in
**Photo details**). Choose a JPEG, PNG or WebP, acknowledge the alignment review,
then **Replace photo, keep layout**. This preserves the photo's ID, description,
view identity, crop/orientation/scale metadata and every geometry, wiring and
calibration record. Geometry is never automatically moved or rescaled to fit a
different photograph. Review alignment when framing or camera angle changes.
Save/cancel pending layout edits before changing its existing photo.

**Remove photo** now removes only the reference image record: geometry remains
editable on the installation canvas, with its semantic/landmark IDs, pixel
allocations and calibration intact. Delete geometry explicitly in Layout only
when the installation object itself should be removed. This does not recover
geometry already deleted by older application versions.

### 4. Map geometry and outputs

![Visual Mapper controls for an uncalibrated synthetic installation](assets/gui/operator-installation.png)

*The capture shows mapper inputs before calibration, not a calibrated venue.
The reference uploaded for this walkthrough was a generated courtyard diagram.*

1. Choose **Open Layout** on the photo. Layout starts in **Select**, not Draw.
   Select a shape on the photo or in the list to reveal its properties.
2. Choose **Add to photo** to create an LED segment, projector area, or other
   object, then name it and place points on the reference view. Choose the
   appropriate kind: output, pixel segment, fixture, path, projector surface,
   mask, audience zone, or safety zone.
3. Use grid snapping, zoom/pan, color, labels, ordering, and locked/hidden
   states to keep the map legible. Save geometry after placing valid points.
4. When the same real object appears in another view, use **Link geometry
   across viewpoints** rather than inventing a second semantic object.
5. Keep photo coordinates, physical/shared-stage coordinates, native
   projector pixels, and lighting-local coordinates distinct.

Navigate the reference image with **Hand** and drag, or temporarily pan using
**Space + drag** or the middle mouse button. **Select** changes the selected
object and permits editable point handles; it does not add points on empty space.
**Draw** explicitly adds points when creating or editing an object. The mouse wheel zooms around the cursor; trackpad pinch
zooms and two-finger scrolling pans. Wheel-device detection is heuristic:
small pixel-scroll deltas pan, while larger vertical wheel steps zoom.
**Shift + wheel** explicitly pans on any device. On touchscreens, use two
fingers to pan and pinch to zoom; one finger follows the selected tool.
Navigation never changes saved geometry and is also available to read-only users.

Use **-**, **+**, and **Fit view** in the canvas toolbar to navigate without
gestures. With the canvas focused, arrow keys pan, **+/-** zoom, and **0**
fits the view. Zoom ranges from 50% to 800%; **Fit view** resets zoom and pan
without clearing points. Changing the reference photo also resets the view.

For a planar projector surface:

- Select its **Projector resource**. Open **Projector mapping** for actual native
  width/height, calibration content, fit and precise alignment.
- Draw exactly four corners in consistent perimeter order.
- Set stage placement/size, orientation, mirroring, crop, fit, linked masks,
  and brightness limit deliberately.
- Record the calibration source and revision. An **Unverified estimate**
  is not a measured calibration.
- Use native and installation warp previews to inspect the transform. Use
  subdivided surfaces only when the physical shape requires a validated mesh.

For lighting:

- Select **pixel segment** under **Geometry type** in the properties inspector.
  Give it a **Geometry name** and choose its configured **Output resource**.
- Use **Trace on photo** for existing strands: click the first pixel's location,
  then trace the strand in wiring order. Two points define a straight section;
  additional points define bends. Use **Undo point**, **Clear points**, and
  **Fit view** in the drawing toolbar without clearing the segment's properties.
- Enter **First pixel index** (zero-based), **Pixel count**, and an optional
  **Strand ID** under **Optional strand label**. The allocation summary displays the inclusive first and last
  indices. Out-of-range allocations cannot be saved; overlaps on the same output
  are warnings, excluding linked views of the same semantic object.
- Alternatively, choose **Generate layout** for a row, grid, ring, outline,
  individual pixel, or imported coordinates. It uses the same **Pixel count**;
  grids and imports calculate their count. Generating replaces the current drawing.
- Use **Appearance** for labels/color/layering, **Grouping & associations** for
  fixture/group/path IDs and cross-view linking, **Shared stage coordinates**
  for shared points, and **Advanced calibration** for local dimensions, pixel
  corrections, stage transforms, brightness, and timing.
- Choose **Wiring direction**, then **Save segment** (or **Update geometry**
  when editing). **Preview traversal** uses saved mappings for the selected
  output, not unsaved edits or just the selected segment. A five-pixel pulse
  travels through increasing output indices across the entire device. Each
  segment has a distinct color and a numbered legend showing its index range
  and direction. Linked photo views count as one segment; segments on another
  photo are identified in the legend and progress readout.
- Pause, change the speed, or scrub **Output pixel index** to inspect segment
  boundaries and the first/last pixels. Restart returns to index zero and pauses.
  Unmapped indices remain gaps in the sequence rather than being skipped.
  Overlaps and invalid ranges are reported explicitly. Reduced-motion
  preferences start the preview paused. These controls are browser-only and
  do not send hardware commands or certify physical wiring.
- Pixel segment ranges use half-open intervals: `[start, end)` includes
  `start` but excludes `end`. An eight-pixel output has indices 0 through 7.
- **Maintenance preview** records an index-chase simulation for the selected
  output and duration. It requires maintenance mode and idle playback but does
  not drive physical LEDs. Verify actual wiring in a separate authorized physical
  test. Drawing a path does not change wiring order.

To check the real output from Cueverge, open **Test this output on physical
LEDs** inside the traversal preview, or use **Operate > Physical LED output
tests**. This is separate from the simulation-only maintenance preview:

1. Stop playback and enter **Maintenance**.
2. Choose a configured LED output, duration (5, 15, or 30 seconds), and chase
   speed. The output must have an enabled DDP driver whose expected pixel count
   matches the running installation.
3. Leave **Use saved segment colors** checked to match the preview's segment
   colors and boundaries. Linked photo views count once. Save complete mappings
   and resolve conflicting ranges first. For an unsegmented white index chase,
   uncheck this option. Unmapped indices also use white.
4. Type the exact output resource name into **Confirm physical output name**
   and choose **Start physical chase**. This sends real output, not a simulation.
   Within the mapper, the preview follows the controller's reported pixel
   position, sampled once per second.
5. Observe the pulse through the complete output and compare the physical
   segment boundaries/directions with the photo. Choose **Stop physical chase**
   when finished. Otherwise the controller stops at the deadline, even if the
   browser closes.

The test uses existing output-driver brightness settings without an extra
test-specific brightness cap. It targets 60 frames/second with fractional-pixel
crossfading for smooth motion, without changing the show's configured render rate.
Network loss or device refresh limits can still cause stuttering.
Downstream devices and power supplies are assumed
to be correctly configured. Only one physical test runs at a time, and show
playback is blocked while it owns the output. Stop/disarm, mode changes, global
Stop, and controller shutdown terminate the test. Completion sends a black frame
to the tested output and releases realtime output; subsequent preset/timeout
behavior remains controlled by the downstream device. Transmission errors are
reported, but successful UDP sending is not proof that the LEDs visibly responded.
Testing does not publish, alter mappings, or reload the show.

#### Choose the stage view

Effects that travel across several devices, such as a flag rippling over
projectors and LEDs, are drawn on one **shared stage**. The **Stage map** panel
(below the photos) builds that stage from one photo:

1. Pick the **Stage view**: a photo of the whole display taken from where
   visitors watch. Everything drawn on that photo is placed on the stage
   automatically.
2. Enter the **Width of the whole photo view** and its **Unit**. An estimate is
   fine; it sets the real-world size of stage effects.
3. Choose **Use as stage** (later **Save stage settings**).

The summary reports the stage size and how many objects are placed. When you
move or add objects on the stage view, it shows how many changed; choose
**Update stage map** so shows use the new placement. Shows keep the previous
placement until you do. Objects not drawn on the stage view are listed under
**Not drawn on the stage view**; draw them on that photo to include them in
shared effects, or keep a manually entered stage position.

Projector pairs that overlap are reported in plain language, for example
"projector.left and projector.right overlap 18%". Two projectors that cover the
same area usually means their surfaces were drawn over each other or they use a
manual stage rectangle. If the stacking is intentional, set blending in the
projector's overlap settings.

Updating the stage map writes ordinary stage data: geometry stage points, LED
stage transforms, and a perspective stage mapping for planar projectors. Photo
calibration, wiring, and native projector pixels are not changed. Removing the
stage photo keeps this placement.

#### Projector stage placement

Each projector's **Stage placement** (projector details in the Visual Mapper)
decides where it sits on the shared stage, and therefore which part of a
shared-stage effect it shows:

- **From drawn surface** (default): the projector's stage position, size and
  perspective follow its drawn projector surface. They update automatically
  whenever you save the projector or redraw its surface; the Stage X/Y/width/
  height fields are read-only. Placement comes from the surface drawn on the
  stage view when there is one, otherwise from the surface's saved stage
  points. Before any stage view is chosen, the drawn surface is placed at a
  default scale so projectors drawn on the same photo already sit side by side;
  choose a stage view to give them real-world size.
- **Manual stage rectangle**: you enter Stage X/Y/width/height. Redrawing the
  surface and **Update stage map** leave it unchanged. A projector left on the
  default 4 x 2.25 rectangle at the stage origin is flagged because every
  projector on that default overlaps the others.

On the stage view, **Show projector coverage** draws each projector's area on
the photo with a key: one projector, projectors overlapping (blend the seam),
blocked by a mask, and gaps between projectors. Review seams, masks, crop,
brightness/color matching, and mapped/native previews. Correct mapper
diagnostics before rendering or publication; do not use distorted content as a
substitute for calibration.

### 5. Establish timing and acceptance

Where the assigned device advertises timing-calibration support, use its
declared ranges, resolution, and safe-test procedure. Compare baseline and
proposed settings, record the test asset/version, observed variability,
hardware/routing/display mode, and notes, then accept or cancel the session.
Re-review calibration after changing hardware, cabling, display mode, routing,
or media settings.

Accepted calibration records and browser previews are not proof of a physical
timing measurement. The current maintenance patterns are simulation-only.
Have the technical operator perform the required physical reference test.
Controller lighting delay and agent audio delay are separate deployment
settings; positive audio delay delays audio, and positive lighting delay delays
lighting. Do not guess compensation from the time a command was clicked.

**Installation acceptance:** correct resource identities, measured geometry and
pixel order, usable video/audio, deliberate safe output states, reconnect
behavior, and recorded physical timing checks.

## Connecting camera events

Open **Installation > Inputs & connections**. Frigate over MQTT is the supported
camera-event connection; Cueverge does not need a camera video stream.

1. Open **Set up Frigate camera events** (or **Connection settings**).
2. Enter the MQTT broker hostname and port, choose TLS if the broker supports it,
   and open **Broker login (optional)** if credentials are needed.
   The topic defaults to `frigate/events`
   and the accepted object label to `person`; both are under **Advanced connection
   settings**. Use a trusted local network if TLS is off.
3. Select **Enable camera events**, then **Save and connect**. Confirm that real
   events may reach the loaded automatic show. This reconnects only the camera
   listener, not the show. Only installation administrators can change settings.
4. Wait for **Subscribed to camera events**, then open **Check event reception**
   and cause an authorized real detection in Frigate. Check received messages,
   controller acceptance, matching visitor rules, and camera/zone names separately.
   A connected broker with no events is not proof that the integration works.

Passwords are write-only: an empty password field retains a stored credential;
**Remove stored password** explicitly removes it. Saved settings survive a
controller restart, independently of show drafts. If another administrator
changed settings, **Reload saved settings** before submitting again.
Changing a connection starts a new listener session and resets its counters.

**Try an isolated example** checks a camera and optional zone against saved
connection labels and the controller's currently loaded visitor rules. It never
injects a live event or commands hardware. It does not test MQTT reception or
promise scene playback. Unsaved show rules are not loaded rules.

No events: check that Frigate publishes to the configured broker/topic.
Filtered labels: compare accepted labels. Unmatched input: compare exact camera
and zone names with the loaded rules. Stale input: check clock synchronization
and the loaded freshness limit. A matched visitor may still wait for arrival
confirmation, repetition limits or an eligible response. Guided response
authoring and explicit verified Apply are separate actions.

### Controlling the show from Home Assistant

The same broker can also let Home Assistant control the show. An administrator
opens **Settings > Integrations > Home Assistant** and chooses **Turn on Home
Assistant control**. A **Cueverge** device then appears in Home Assistant's MQTT
integration with Start, Stop, Pause, Resume and Blackout buttons, a scene picker,
a show mode picker, and playback, scene and loaded-show sensors. Use them in
automations like any other entity. Commands behave exactly like the Operate
buttons and are recorded in the activity log. **Topics and recent commands**
shows what was received and why anything was rejected. Turning control off
removes the device from Home Assistant. Anyone allowed to publish to the command
topics on the broker can control the show, so restrict them on the broker.

An **Operator-managed connection** is deliberately read-only. The operator changes
`CUEVERGE_FRIGATE_*` settings and restarts the controller. See
[input deployment and credential recovery](operations.md#camera-input-management)
for management selection, TLS, backup/restore and protected-storage requirements.

## Creating and organizing a show

![Shows workspace with a revisioned synthetic project and collaboration tools](assets/gui/operator-shows.png)

### 1. Open or create a show

1. Open **Shows** with authoring permission.
2. Choose **Open show** on an existing show, or expand **New show**, enter
   **Show name**, and choose **Create show**.
3. An existing show opens on its **Show plan**. A new show opens into its
   inherited scenes so you can start editing right away. Creation and saving
   do not change live playback.

The button copies the controller's current runtime catalog; it does not
create an empty show or use the unprovisioned record you just created in
Settings. Alternatively, use **Import project** with a compatible Cueverge
project JSON document.

Use **Find a show** in the library. Within an opened show, **Show options**
contains name, duplicate/archive, export, and saved revision details.
Export is a portable saved project document, not a backup of original
media, device credentials, or the complete controller service. Keep those
separately.

### 2. Arrange collaboration

Collaboration is opt-in: open **Show options > Share & review**, then ask an
administrator to add team members under **Project access**. It is not part of
the default hobby workflow. A person needs an account and project membership.
Project permissions are checked independently of opening sharing. Use comments and
change requests for review; associate an object ID when feedback concerns a
specific scene or object.

Designers edit; publishers approve/release; operators need live-output
authorization to run the installation. A publisher also needs **Permit live
output** for package activation, deactivation, and rollback.

Concurrent edits are revision checked. If a save reports a conflict, preserve
your intended changes, reload the latest draft, compare, and reapply them
deliberately. Do not repeatedly submit an obsolete revision.

### 3. Import and review assets

1. In the project **Assets** area choose **Add visual or audio asset**.
2. Wait for upload verification and processing, not just completion of the
   browser progress indicator.
3. Review each asset card's name and processing state, filename/size, media
   properties, organization, and file availability. Thumbnails, review proxies,
   waveforms, and processing errors remain visible where available.
4. Set useful names, folder/tags/favorite state, ownership, license,
   provenance, review state, and expiry where appropriate.
5. Place a ready asset into the intended scene/output or composition.

Managed formats include MP4/QuickTime/WebM video, FLAC/MP3/Ogg/WAV audio,
and JPEG/PNG/WebP images. A format name alone does not guarantee the codec or
profile works on a target device. Failed, quarantined, missing, or incomplete
assets must be resolved before release.

Asset details wrap on narrow screens. Expand **Organization & governance** to
edit metadata; cards resize without overlapping neighboring assets. If the show
has no media outputs, add one to a scene before placing an asset.

Review proxies are editorial aids. Preserve the originals and verify the
actual render/playback output. Package-relative media used outside the managed
artifact workflow must also exist beneath the media agent's private asset
root; importing a project JSON file does not copy those files.

#### Remove assets

Each asset card shows where the current draft uses it (scenes, timeline media
layers, scene presets, compositions). To remove an asset from the show:

1. Remove every current use first, for example delete its media layer in the
   timeline editor or replace it in the scene output. **Move to trash** stays
   disabled and lists what still uses the asset while it is used by the current
   draft of any show, as installation projector review content, as an accepted
   timing-calibration test asset, or by a staged or active release. Earlier
   draft revisions do not block removal.
2. Choose **Move to trash** (designer, publisher, or administrator). Unfinished
   uploads for that asset are canceled. The asset leaves the library, can no
   longer be placed, and publishing a revision that still references it fails
   with `asset_trashed`.
3. Open **Assets > Trash** to review trashed assets. **Restore** brings an asset
   back with the same ID, revisions, and metadata; it is not re-placed in
   scenes.

Trashed assets keep their files for **30 days**, then the controller deletes
them permanently (it checks at startup and hourly). An asset still referenced by
any published release is kept so rollback keeps working; the trash shows why.
Administrators can **Delete permanently** sooner by typing the asset name. The
same file uploaded as several assets or shows, or used by an installation
photo, is stored once and is deleted only when nothing references it. Restoring
an older draft revision that used a permanently deleted asset reports
`asset_missing` until it is relinked.

### 4. Optionally author through chat

Open **Advanced authoring and history > MCP-assisted authoring** and create a named
client token. Copy the secret once and connect the chat client to `/mcp/` on
the controller's HTTPS origin using bearer authentication. Keep the token
private; it is bound to your user and this project.

Choose only the permissions the client needs:

- **Create isolated draft proposals** keeps the existing human
  validation/approval/merge workflow.
- **Edit working drafts directly** lets chat save changes immediately as a new
  revision, without a proposal or GUI confirmation. The client should read the
  draft and revision first, then save the complete edited draft against that
  revision. Conflicts require rereading and reconciling, not overwriting.
- **Approve releases, publish, and stage packages** allows the client to approve
  release metadata and publish/stage a validated revision.
- **Activate staged packages** is separate and can affect physical output.
  It requires publisher access plus a live-output grant, or global administrator
  access. Keep it disabled unless the client is trusted to deploy output.

The new permissions are unchecked by default; existing tokens do not gain them.
Direct edits do not implicitly publish or activate. Publishing and activation
still follow the checks and handover boundary in
[Publishing and handing over](#publishing-and-handing-over): artifact activation
does not replace the running controller scene catalog or start a scene.
The proposal path remains available, although direct edits can make pending
proposals stale. See [MCP-assisted authoring](operations.md#mcp-assisted-authoring)
for exact tool names, scopes, and limits.

For an assistant that should work with every show on the installation, for
example one that keeps working after you apply a different show, an
administrator can create one token under **Settings > Integrations > Assistant
access for all shows**. The assistant chooses a show for each request
(`list_shows`), and your current permission in that show still applies.

## Authoring scenes and timelines

### Start with a scene contract

For each scene, define:

- Purpose, unique ID, name, and positive duration.
- Media and lighting resource claims.
- Priority and interruption policy, safe points, and protected intervals.
- Completion behavior: loop, stop, hold, transition, or director selection.
- Fallback on media preparation failure where required.
- Expected visual/audio experience and safe stopped state.

Cueverge runs one active scene at a time, but a scene may contain multiple
media and lighting outputs. Every required media output prepares before play;
one required output supplies the scene clock in media-synchronized mode.
For an LED-only pre-show, use a schema-v2 scene with `media: []`, only lighting
resource claims, and **Clock authority → Controller** in the timeline editor's
**Scene settings**.
No media client is required. Controller-timed scenes can also include media
outputs, but lighting follows controller time rather than buffering or actual
playback. See [controller-timed scenes](scene-schema.md#controller-timed-scenes)
and the [LED-only package](../shows/led-only-demo).

Under **Advanced authoring and history**, use **Scenes & reusable presets** to organize scenes by folder/tags, create
reusable presets, and apply them to a selected scene. Check resource references
after reuse. Presets do not make an installation-specific resource portable by
renaming it automatically.

### Add scenes and outputs

Open a show in **Scenes**, then choose **Add scene**. Enter a name, duration,
end behavior, installation resource, and output name/ID. A scene starts with
one output; add others with **Add output** on the selected scene.
Save or cancel pending scene and visitor-response edits first.

To rename or remove an output, select its track in the timeline editor (or use
the output under **Lighting output** / **Media in this scene** on the Scenes page),
then use **Rename output** or **Remove output**. Each action saves a new working
draft revision immediately; save or undo unsaved timeline edits first. It does
not publish or change live playback.

- **Rename** changes the output's stable ID and updates the scene clock source and
  every media layer, synchronized cue and sync schedule entry on its track.
- **Remove** is blocked, with a list of what to change, while the output is the
  scene's media clock, the scene's only output, or still used by media layers or
  synchronized cues. Move or delete those first, or pick another clock in
  **Scene settings**. Removing drops the output's resource claim when no other
  output uses that resource; assets and installation bindings are never deleted.
- Legacy single-output scenes are upgraded to the multi-output format first, with
  output IDs `media` and `lighting`.

- Choose a video/audio resource for a media-only scene, an LED resource for a
  lighting-only scene, or add both kinds for a mixed scene.
- New lighting outputs contain an editable, full-duration **Blackout** cue.
  New media outputs have no asset: open **Assets**, select the new
  **scene name · output name** under **Place in scene output**, then **Place asset**.
  Assign the media before publication.
- The resource list includes the show's saved installation bindings and new
  resources from its bound running installation. Saving copies only the selected
  new binding; it never replaces existing bindings or pixel topology. An unbound
  show, or a show bound to another installation, cannot import runtime resources.
- Projector surfaces describe mapping/geometry, not independent playback slots.
  Several surfaces on the same projector share one media-agent resource/output;
  configure surface-mapped compositions separately in advanced authoring.
- New scenes expose **Scene clock** explicitly. Adding an output preserves the
  existing clock unless **Use this media output as the scene clock** is selected.
  In particular, adding video to a controller-timed preshow does not make its
  lighting depend on media telemetry.
- Adding an output to a schema-v1 scene upgrades it to schema v2, preserving its
  old outputs as `media` and `lighting`, together with cues, timing, policies,
  asset references and unrelated editor metadata.

These actions save a revision only. They do not add scenes to automatic-program
pools or visitor responses, create a surface composition, publish, Apply, or
change live playback. Configure those separately. A media-only scene sends no
lighting frames during playback, including persistent baselines; it is not a
background overlay. Explicit stop still applies configured safe-state lighting.

### Edit scene-native lighting

Open a show and select a scene. Its existing scene-native timeline effects are
shown as selectable rows fitted to the entire duration, not reconstructed from
editor metadata. Use **Find an effect** for long scenes. Selecting an effect
moves the preview to its start and exposes its supported target, color,
brightness, timing and primitive-specific controls. Optional fades, blend,
seed/precedence and output/scene details stay contextual.

Targets retain their original segment/fixture/path identities; saved geometry
names are used when a matching semantic identity is available. Save changes
with **Save scene** in the sticky scene heading, or **Cancel scene edits**.
Scene name/duration are under **Scene details**. Invalid timing is explained and
cannot be saved. Shortening a scene does not automatically delete or trim effects,
markers, or protected intervals.

**Play scene preview** and the seconds scrubber drive one preview of every scene
output: all LED outputs together and every projector/media output. The Show's
**Preview** tab uses the same combined preview, with **Preview output** narrowing
the LED view to one output.

LED frames come from the same lighting evaluator used by the controller.
Frames include declared output timing offsets. Looping scenes wrap to the start
using whole-millisecond preview positions; other completion policies stop the
preview at the last millisecond. For a saved show, LED compositions (spatial
lighting, or whole-display compositions that drive LEDs) are included when they
have a current bake for that revision. Otherwise the preview still shows the
scene's own lighting effects and names the compositions that need **Bake LED
animation** first. All outputs share a 4,096-LED sample budget; large
installations show a paged sample (choose one output in Preview to see more).
A matching saved photo displays evaluated LED locations; the output
strip remains available when mapping is absent or incomplete. Different
allocations withhold the overlay rather than paint incorrect locations.
Photo previews use the same 1000 x 600 canvas and centered, aspect-preserving
photo fit as Installation, including letterboxing for wide or portrait photos.
The nighttime visualization applies a display-only linear-to-sRGB brightness
curve, a small glow and screen blending. Off LEDs are transparent rather than
black dots. This improves low-level visibility without increasing saved effect
brightness or device output. Collapsed **LED output details** retains the
unmodified sampled RGB values. Scene, visitor Preview and Operate's intended
output monitor share this photo presentation; it is not photometric calibration.

**Projectors and media** shows one card per media output with a still rendered
by the same compositor used for bakes. As on the controller, a scene composition
that targets the output (projector-only or whole-display overlay) takes
precedence over the output's assigned asset. Media starts after the output's
start delay and then follows its end behavior, so each card reports **Waiting
for start delay**, **Playing**, **Looping**, **Holding last frame**, **Stopped**,
or **No media assigned**, with the source and media time. Two compositions
targeting one output are reported as an error, matching Apply. Turn on **Play
audio during preview playback** to hear asset and composition audio layers
through their review proxies while the preview plays; browser audio timing is
approximate. Unsaved scene edits are previewed before saving.

This is a candidate scene preview, not a live monitor, automatic visitor/show
simulation, or proof of physical output. Media device timing, codecs and
projector calibration on site can still differ.

To remove an unwanted scene, select it in **Scenes** and choose **Delete scene**.
Save or cancel any unsaved scene edits first, then confirm the scene's name in
the confirmation message. The last scene cannot be deleted. If another scene's
fallback/completion transition, the Show plan (starting scene or shuffle list),
a visitor response, or an encounter uses it, deletion is refused. The reasons
appear directly under **Delete scene**, each saying where to change it. The
shuffle list must keep at least one scene, so to delete its only scene, add
another scene to the shuffle list first and then remove it.
Deletion saves a new working revision and selects a remaining scene. It removes
the deleted scene's editor data, not reusable compositions, presets, media assets,
or historical revisions. It does not change the loaded show: publish/apply the
new revision separately to update live playback.

Only supported fields are changed. Scene-native v1/v2 legacy effects remain in
their original form; legacy pulse targets/times/color are editable. The renderer's
existing legacy brightness-limit behavior is reported rather than silently
changed. Unsupported simplified editing never removes saved data.
Compositions, synchronized editor cues and transforms are identified as separate
contributions with a direct **Open advanced timeline** destination; spatial
composition preview continues through its existing advanced renderer.

Unsaved scene edits warn before scene changes, return to the library, workspace
navigation, sign-out, or browser unload. Background asset refreshes do not
replace those edits. While raw edits are pending, other mutating show tools are
unavailable; save or cancel first. Stale saves retain edits and report conflict.

### Advanced time-based content

![Full-screen timeline editor with scene header, transport, timeline, and tool tabs](assets/gui/operator-timeline.png)

1. In **Scenes**, choose **Edit timeline** (save or cancel scene edits first).
   The timeline editor opens full-screen; **Back to scene** returns. It is also
   reachable from **Advanced authoring and history > Open timeline editor**.
   Its **Scene** selection stays synchronized with the main scene workspace.
2. Use the tabs above the viewer to switch between **Whole scene** and each
   composition. **+ New composition** opens presets: **Whole display
   overlay** (sized to the installation stage, output `shared-stage`), **One
   projector** (sized to the chosen projector's native pixels), or **One LED
   surface**. **Add to this scene at 0:00** (on by default) also places the new
   composition in the scene.
3. Use the transport bar: stop, frame steps, play/pause, the playhead
   timecode (composition time, plus scene time inside a composition), **Go to
   (ms)**, speed, zoom, and snap.
4. The **Media** bin lists ready uploads and other compositions. Drag an item
   onto a track to place it at the drop time, or onto the **Viewer** to place
   it at the playhead on the selected track (or the first media track). The
   **+** button does the same without dragging. Nested compositions and videos
   with a known length use it; other media defaults to 5 seconds; both are shortened to
   fit the remaining time.
5. The **Viewer** shows the open composition on the house (stage and LED
   surface compositions) and, for whole display overlays, **Outputs in this
   effect**. On **Whole scene** it defaults to **House view**: each scene
   media output's review proxy is warped onto the projector surface drawn for
   that output's projector on the installation photo (pick another photo
   above the frame when there are several). The drawn surface is treated as
   the content area: media is fitted to the surface's shape using the
   mapping's **Content fit** (preserve or stretch), then corner-pinned. If the asset's
   aspect ratio differs from the projector's native output (after
   orientation), a note warns that direct playback will letterbox or
   pillarbox it on the projector. It follows the playhead and
   play/pause, muted, honoring the output's start delay and end behavior
   (hold last frame, loop, or stop). Other projector surfaces appear as dashed
   outlines. Outputs without a drawn surface on a photo are listed below the
   frame. **Program monitor** instead shows the top source at the playhead:
   the topmost visual layer, or else the scene's media output, without
   projector warp, placement, or keyframed transforms. Both are approximate;
   bakes are the exact output.

   On a media output track, the clip bar starts at the output's start delay
   and spans the asset's length, showing its head frame and name. A dashed
   **hold** or **loop** marker shows where the asset ends and the end behavior
   takes over.

   **Layout** (always shown for projector compositions; a toggle beside
   **House view** otherwise) draws the composition frame and the layers at the
   playhead exactly where the renderer places them. Drag a layer to move it,
   drag a corner to resize it (proportions locked; hold **Shift** for free
   resizing), and drag the knob above it to rotate (**Shift** snaps to 15°).
   Content outside the frame stays visible, dimmed, so handles are always
   reachable. These edit the selected keyframe; each drag is one undo step.
   Corner-pinned layers show their pinned outline; edit those corners in
   **House view**. New media added to a composition starts centered and
   scaled to fit; new compositions get a **Media** track automatically.

   The **Inspector** shows the media layer, keyframe, or track you last
   clicked on the timeline. Transform values are labeled fields: position
   (composition units, pixels for projectors), scale and anchor in %, rotation
   in degrees, opacity as a slider, and crop per edge in %. Drag a field's
   letter label left or right to adjust it (**Shift** ×10, **Alt** fine), type
   a value, or use the arrow keys. The chain button locks scale proportions;
   the 3×3 anchor grid moves the anchor without moving the layer.
6. On the timeline:
   - Click a scene media clip (the strip with the asset name or thumbnail) to
     select its output without moving the playhead. The **Inspector** shows its
     resource and lets you change the managed asset. Schema-v2 outputs also expose
     **Media start delay (ms)** (0–5000), **Media end behavior** (hold, loop, or
     stop), and **Required**. The clock-source output must remain required.
     These are output settings, not transform-layer trim or composition settings.
     Keyboard users can focus the strip and press **Enter** or **Space**.
     Click empty timeline space to seek instead. **Save timeline** creates a
     working-draft revision; it does not publish or change live playback.
   - The scene length is the scene's duration. Change it in the timeline under
     **Scene settings → Scene duration (seconds)**, or on the Scenes page under
     **Scene details**. A timeline duration change is one undo step and is saved
     with **Save timeline**. Clips, cues, markers and regions are never trimmed
     automatically: anything that now runs past the end is reported as a
     timeline issue and blocks saving until you fix it.
   - Adding a clip whose natural length runs past the scene end offers
     **Extend scene to …** (lengthens the scene so the whole clip fits),
     **Trim clip to fit** (ends the clip at the scene end) or **Cancel**.
     Extending and placing together are one undo step. Still images have no
     natural length; they are placed for up to 5 s and trimmed to fit. Inside a
     composition, clips are trimmed to the composition length instead.
   - Drag the orange playhead handle (or the playhead line, or anywhere on the
     ruler) to scrub. Focus the handle to step with the arrow keys; **Home** and
     **End** jump to the start and end. Drag the blue loop handles (or focus one
     and use the arrow keys).
   - Drag a marker to move it; click a marker to remove it.
   - Drag a media layer to move it; drag its left or right edge to trim it.
     Trimming the start also advances the source in-point. Keyframes cut off by
     a trim collapse onto the new edge (the nearest one is kept); undo restores
     them.
   - Drag a keyframe diamond to retime it; click it to move the playhead to it
     and edit it in the Inspector.
   - Overlapping layers on one track are drawn on separate rows; **Visual
     order** in the Inspector decides which is on top.
   - Remove a layer with **Delete layer** in the Inspector or the
     **Delete**/**Backspace** key while it is selected.
   - Track names stay pinned while scrolling. Each drag is one undo step.
7. Use the tool tabs below the timeline:
   - **Media layers**: add images, video/GIFs, audio, or nested compositions by
     picking a ready asset; edit keyframes, crop, playback, and render settings.
   - **Markers & cues**: named markers, synchronized cues, cue presets, and
     edits to selected items.
   - **Tracks**: add audio-reference, fixture, group, or path tracks (bindings
     suggest installation fixtures, paths, and audio assets).
   - **Compositions**: create, open, nest, and add compositions to the scene.
   - **Render & bake**: render settings, controller render and bake jobs,
     rehearsal evidence, and rendered-frame inspection for projector and whole
     display overlay compositions.
   - **Loop & ranges**: exact loop range and protected regions.
8. Use **Scene settings** for priority, completion, request policy, resource
   claims, clock authority/source, and per-output requirements and calibration
   offsets. These are saved with the timeline.
9. Use **Save timeline** to persist changes. Undo/redo and browser preview do
   not replace saving a revision.
10. The status chip beside **Scene settings** shows **Valid** or the issue
   count; it opens **Diagnostics** (validation issues, backend deterministic
   evaluation, and evaluated state).

Keyboard shortcuts (outside text fields): **Space** play/pause, **M** add a
marker at the playhead, **Ctrl/⌘+Z** undo, **Ctrl/⌘+Shift+Z** redo, and
**Ctrl/⌘+S** save (also inside fields). Arrow keys move the playhead when the
timeline canvas has focus.

Saved zoom and snap precision are restored when entering that scene or editing
space; opening and saving it should not replace those view settings with defaults.
Existing raw runtime lighting cues and legacy effects are edited in the main
scene workspace and preserved by advanced saves. This advanced timeline shows
editor-owned synchronized cues/compositions; an empty lane does not establish
that a scene has no scene-native lighting.

Choose coordinates deliberately:

| Space | Use |
| --- | --- |
| One projector (native pixels) | Content sized to an identified projector's pixel canvas |
| One LED surface | Logical raster/content sampled onto mapped physical pixels |
| Whole display overlay | Content spanning calibrated physical/stage coordinates and outputs |

The editor supports composition reuse/nesting, asset references, transform
keyframes and motion paths, time markers and synchronized cue relationships,
and deterministic lighting authoring. Resolve dependency and timing
diagnostics rather than treating the displayed editor state as a valid release.

Lighting cues can target whole outputs or semantic segments, fixtures, and
paths. Use explicit precedence/blending and deliberate brightness limits.
Seeded effects must reproduce the same result when seeking or reconnecting.
For schema details and creative planning, see the
[show creator guide](show-creator-guide.md) and [scene schema](scene-schema.md).

### Render and inspect compositions

#### Shared-stage effects across projectors and LEDs

Use a **Whole display overlay** composition (shared stage) to author one image,
video, or GIF across projectors and LEDs, such as a flag rippling over the
whole house. Overlay effects belong to the show; the installation only defines where each output
sits: projectors follow their drawn surfaces (see
[Projector stage placement](#projector-stage-placement)) and the **Stage map**
scales everything to real-world size. New shared-stage compositions default to
the installation stage size and a render resolution that covers it.

To create one, open the timeline editor, choose **+ New composition**, keep
**Whole display overlay**, name it, and choose **Create and open**. Then drag
the image, video, or GIF from the **Media** bin onto the Viewer or a track.
(The **Compositions** tab still offers the full form: **Covers → Whole display
overlay**.)

1. In the Viewer, under **Outputs in this effect**, choose which projectors and
   LED outputs take part. All mapped projectors are included by default; at least one is
   required. Unselected LED outputs keep their existing lighting behavior.
2. If the composition size differs from the installation stage, choose
   **Match installation stage**. LED stage coordinates must fall within the
   composition; out-of-bounds samples fail the bake rather than silently
   sampling the edge.
3. The stage preview shows the stage-view photo in true proportions, the
   rendered frame at the playhead (**Show rendered frame**), projector
   coverage, LED pixels, and the composition boundary.
4. Select the media layer on the timeline; its properties open in the
   Inspector. Under **Shape on stage**, choose **Stretch to four
   corners** or **Fit to selected outputs**, then drag the four corner handles
   on the preview to place the media over separate devices. Corner-pinned
   layers ignore position, scale, rotation, and anchor keyframes; opacity and
   crop still animate. Keep the corners in top-left, top-right, bottom-right,
   bottom-left order without crossing.
5. Optionally choose a **Shape mask**: **Ellipse**, **Rectangle**, or
   **Outline of selected outputs**, with a **Soft edge** in stage units and
   **Show outside the mask instead** to invert it.

Save the timeline, open **Render & bake**, and choose **Bake installation
overlay**. One job creates
the projector videos and RGB sequences from the same stage frames, at the
composition's frame rate (up to 60 fps for mixed output, including fractional
rates such as 30000/1001). Projector warps, masks, and overlap corrections
apply only to projector video; LEDs sample the stage before those corrections,
with each lighting surface's brightness limit applied.

A mixed stage can use one projector or a synchronized projector group.
Multiple projectors retain their existing synchronization-rehearsal gate.
Do not also target the same LED indices with an overlapping lighting-surface
composition: publication rejects competing spatial sequences.

Publish the package to include the LED sequences in the controller package
and the matching videos in the media-agent artifact set. **Stage and activate
client group** alone only installs projector media, not controller LED data.
The existing controller package handover/restart is still required; publication
does not hot-reload the controller scene catalog.

Rehearse on the actual installation before live use. Common baked timestamps
do not compensate for projector decoding/display latency, LED transport latency,
or physical color differences. This workflow is baked playback, not live media
streaming, and the photo remains a calibrated authoring reference rather than
an automatic reconstruction of the house's depth.

#### Spatial LED media overlays

Open a mapped **One LED surface** composition. On **Media layers**, add an
image, video, or GIF by choosing it under **Source** (the picker lists ready
uploads and sets the layer type).
GIF assets use timed frames rather than a still image. Newly added video/GIF
lighting overlays loop by default; **Media playback** can instead hold the last
frame or play once and become transparent. The layer's interval controls how
long it contributes to the composition.

Use position, scale, rotation, opacity, crop and transform keyframes to place
and animate the source. Clicking the logical lighting canvas positions the
selected media keyframe. Save the timeline to refresh backend evaluation.
The logical canvas shows the composed content, while installation-photo
previews show both a translucent source reference on four-corner surfaces and
the resulting sampled LED colors. The translucent reference is not a claim
that the installation has a dense video display.

Choose **Bake LED animation** after saving. Lighting bakes run at 30 fps and
contain final sampled/calibrated RGB values in verified physical order. They
are included in compiled/published controller packages, not sent as videos to
media agents. Media, draft, geometry or calibration changes require a current
bake before compilation/publication. Missing bakes cannot silently fall back
to a strip effect at runtime.

A **2D expanding ripple** lighting author layer can generate content without
media. Its origin, speed and ring width use installation distance units, not
strip indices or photo pixels. Use **Shared stage** for a common origin across
surfaces. These units are not assumed to be metres.

Preview and baking send no hardware commands. The existing explicit package
handover/controller restart and live-output permissions still apply. Published
package activation alone does not replace the controller's scene catalog.

#### Projector media renders

For content that requires a baked render:

1. Save the intended revision and set composition output dimensions, frame
   rate, target resource, and render settings.
2. Queue the bake in the composition render controls.
3. Wait for a completed job. Read failures rather than retrying unchanged
   settings indefinitely.
4. Select the **Checksummed bake** and inspect **Editable source**,
   **Selected bake**, **Before / after**, or **Frame difference**.
5. Check the reported source revision. Changes after a bake can make it stale;
   render the new revision before publication.

Browser editorial audio is for editing, not proof of venue output, audio
latency, or multi-output synchronization. Qualify the actual baked media and
physical playback separately.

## Authoring autonomous behavior

### Show plan: putting scenes together

A show is built from scenes, much like a video edit is built from clips. Open a
show and choose **Show plan** (the first tab, next to **Scenes**, **Visitor
responses** and **Preview**) to see how they fit together:

- **Main show** plays when you press Start and whenever no visitor response is
  playing. **Starts with** picks its first scene. Each scene block shows its
  length and a **When it ends** choice:
  - **Repeat this scene**: plays it again forever.
  - **Play a specific scene next**: choose the **Next scene**; the plan follows
    the chain and shows where it goes back to.
  - **Let the show pick next**: plays a waiting visitor response if there is
    one, otherwise picks a scene from the shuffle list.
  - **Stay on the last moment** or **Stop the show**.
- **Shuffle list** is the set of scenes the show picks from at random. Use
  **How often** to make a scene more likely, **Remove**, or **Add to shuffle
  list**. Cooldowns, conditions and on/off rules set in advanced authoring are
  kept and labeled.
- **Visitor responses** shows each "When visitors arrive → scene → afterward"
  lane. **Edit visitor responses** opens the guided editor.
- **Scenes not in the show yet** lists scenes nothing ever plays (failure
  backup scenes count as used).

Choose any scene block (**Open scene**) to edit that scene's lights and media.
The Scenes tab then shows a **Show plan › scene name** breadcrumb; choose
**Show plan** to return. Each plan change saves immediately as a new draft
revision and is not live until you apply saved changes. Warnings explain
problems such as no starting scene, or visitor responses that can never play
because no main-show scene lets the show pick next.

### Visitor responses and the shared Preview workspace

Open a show and choose **Visitor responses** next to **Scenes** and **Preview**.
Cards explain when a response matches, what scene it requests, whether it waits
for a boundary, its repeat limits, and what happens afterward. The shared visitor
area applies to every response; **Inputs & connections** opens real Frigate setup.

**Add visitor response** guides When/Where, Response, Timing/Repetition, then
Afterward/Review. Guided creation makes a boundary-based response: it waits until
a scene with **choose next** end behavior completes. It does not silently turn an
event into an immediate interrupt. Existing state-sequence requests remain visible;
**Change response scene** preserves their events, states, conditions and ordering.
Custom matching stays in **Advanced interactions** rather than being flattened.

Review the shared scene-change checkbox carefully. It explicitly changes the
response, starting scene and normal default-group scenes to choose what plays
next; their names and previous end behavior are listed. Leave it unchecked to
preserve loop/hold/stop/fixed-next behavior. A new automatic program requires an
explicit normal-playback scene and confirmation. Editing an existing response
never creates a missing program implicitly.

**Try changes before saving** runs the same Preview workspace on an unsaved
candidate. It never submits the editor form or changes the saved/live show.
Save remains separate from making changes live. Cancel/Escape and navigation
protect pending edits.

In **Preview**, choose **Scene preview** for ordinary LED timing or **Visitor
test** for arrival/tracking/departure and time advancement. **Try it** opens a
visitor test directly. Set example camera/zone names; **Other test conditions**
contains object identity, label and observation age. **Visitor stays** sends a
tracking update, not a new arrival. Advance time separately to test confirmation,
freshness, waiting, expiry, scene boundaries and return. Inspect earlier action
checkpoints without replaying physical outputs.

The photo view uses the actual isolated runtime's evaluated LED frame; sampled
coverage and unsupported/unbaked spatial output are explicit. **What happened**
separates qualified visitors, waiting/qualifying responses, rejection reasons,
boundary selection, actual scene history, normal-playback eligibility and state
sequence activity. A selected response is not proof of physical playback.
Once prepared, a boundary response remains available until expiry even if visitors
leave; its required context is checked while qualifying, not again at consumption.
Use a shorter waiting lifetime when a late response would make no sense.

These are deterministic tests with memory outputs and healthy fake media agents,
not live camera reception or device-readiness checks. Input filters must still be
verified in Installation. Full choreography, unusual matching conditions and
spatial composition editing remain contextual advanced tools.

Expand **Autonomous show logic designer**:

1. Set the program's initial scene and default pool.
2. Define pools, weights, categories, cooldowns, conditions, recent-history
   exclusion, and deterministic seed.
3. Define audience opportunities and their qualification/consumption rules.
4. Define encounter states, observations/semantic events, transitions,
   timing, and ambient recovery.
5. Review completion/fallback behavior, resource ownership, and arbitration.
6. Save, analyze, and simulate the resulting behavior.

The encounter transition editor and scene completion/fallback table scroll
horizontally within their own regions when space is limited. Tab to
**Encounter transitions** or **Scene transition details**, then use the left/right
arrow keys to reach additional columns. Long timelines scroll independently;
opening one should not enlarge the encounter diagram or hide later controls.

Raw detector observations are not the same as semantic audience state.
Test arrival, continued presence, departure, long idle, repeated observations,
cooldowns, and competing scene requests. Conditions use supported scalar
matches, not arbitrary expressions.

Opening/closing wall-clock schedules are not an available autonomous
start mechanism. The Overview schedule placeholder does not schedule a show.
Arrange a supervised operating window or separately controlled site process.

## Previewing and rehearsing

Use a progression from safe authoring checks to supervised physical acceptance.

### Hardware-free checks

- In the mapper, inspect simulation overlays, selected output, native
  projector, parent/logical context, or full installation views as appropriate.
- In the timeline editor's **Diagnostics**, inspect deterministic backend
  evaluation at representative timestamps, including cue boundaries, seeks,
  protected regions, and end behavior.
- On the show's **Preview** tab, open **Fault-injection test scenarios
  (advanced)** to name and save a
  scenario with accelerated advances, synthetic observations, operator
  overrides, disconnect/reconnect, and output-failure/recovery steps.
  Inspect the results and expected-outcome mismatches.
- Under **Rehearsal, publication, and rollback**, select scenes and use
  **Start rehearsal** for the predefined deterministic scenario.

These simulations use fake media and in-memory lighting. They do not open
hardware adapters or enrolled client sessions. A passing scenario with no
meaningful expected outcomes is not a creative or physical acceptance test.

**Current rehearsal caution:** during this documentation walkthrough, the
stock demo's publication rehearsal stalled and the isolated controller stopped
answering health requests until restarted. Do not count a countdown or
"Preflight complete" message as a completed rehearsal. Verify completion and
service responsiveness on your exact package before relying on this workflow.

**Stop rehearsal** cancels the browser countdown/result handling; it is not
a general server-side simulation cancellation mechanism. **Blackout rehearsal**
is a local rehearsal message, not the live blackout action or proof that a
simulation's output state changed.

### Maintenance simulation

In **Operate**, enter **Maintenance**, ensure playback is idle, select a
resource and bounded duration, type its exact name to arm, and choose
**Start simulated pattern**. Use the selected-output disarm control when done.

Current patterns are a browser alignment grid or pixel-index chase. Arming
records resource, identity, expiry, and stop action, but neither arming nor
these patterns sends a command to hardware. The mapper alignment-grid preview
has the same simulation-only boundary. Do not use them to certify real pixel
order, projector alignment, or an electrical safe state.

### Supervised physical rehearsal

Coordinate with the technical operator after the release/package handover.
Confirm real media, correct output assignment, decode, audio level/routing,
lighting direction/brightness, measured timing, pause/seek/resume/stop,
disconnect recovery, and ambient fallback. Keep the physical safe-stop
procedure available. Record the tested revision, hardware/configuration,
results, and unresolved faults.

## Publishing and handing over

![Publication preflight showing a blocked, unassigned synthetic resource](assets/gui/operator-publication.png)

*The synthetic controller has no enrolled media assignment, so
`resource_unassigned` blocks publication. This screenshot does not show a
successful deployment.*

In the project's **Rehearsal, publication, and rollback** section:

| Step | Operator action | Expected result |
| --- | --- | --- |
| Review | Compare candidate with a prior revision; enter revision name and change summary | Attributable release candidate |
| Approve | Choose approval state and **Save release metadata** as publisher/administrator | Approval is persisted for that revision |
| Validate | **Run preflight**; resolve errors and review warnings | Preflight passes for the exact candidate |
| Publish | **Publish immutable package** | Package marked `published`; active playback is unchanged |
| Stage | **Stage** the published package | Missing rendered members transferred and checksummed; state becomes `staged` |
| Activate | Authorized publisher/administrator chooses **Activate** | Device artifact activation and release record update after required acknowledgements |
| Retain/recover | Retain a known-good package; use **Roll back** on a staged/inactive candidate when required | Prior artifacts can be restaged/reactivated |

Preflight checks include resources/assignments, assets, capabilities, current
bakes, and client cache state/capacity. Do not bypass a diagnostic because
editorial preview looks correct. Devices protect candidate, active, rollback,
and playing artifacts; staging can fail rather than evict a protected file.

For everyday changes use **Shows > Apply saved changes** instead of coordinating
the advanced release buttons. The dialog summarizes what will load, lists
**What's changing** compared with the version on the display, and explains
**What happens to playback**. Pick the scene to restart with if the show is
playing, then confirm **This version is ready to go live on the display** and
**It's OK to briefly stop playback to load it**.
Preparation, publication and checksummed device transfers happen before the
controller stops and loads the verified catalog. Progress and failures persist
when the browser closes. Cancel during preparation; Stop/Blackout take priority
once handover starts. A playing show restarts from zero; paused/stopped stays
stopped. Under **Advanced options**, the controller's standby and safety lighting
looks are kept unless you deliberately clear **Keep the controller's standby and
safety lighting looks**. Routing changes need deployment configuration, not
automatic rewiring.

Open **Earlier versions** and choose **Restore version N** to restore a verified
retained version without overwriting saved editing work. Failed/interrupted handover
attempts stopped recovery; restart re-verifies the retained package before
accepting input and never automatically starts playback. Review any
`recovery_required` failure and recover/apply a valid show.
See [managed Apply](operations.md#applying-saved-shows-and-recovering) and
[service handover](service-administration.md#release-handover-and-upgrades).
Advanced artifact `Activate` alone is still not catalog handover.

**Activate** is a real device-artifact operation, not a preview. Arrange the
maintenance window and confirm targets before using it. **Deactivate** updates
release state; it is not an emergency stop. For an urgent problem use live
**Blackout** or the site's physical safety procedure, not a publication button.

## Running a show

![Operate with playback, position, mode, and maintenance simulation controls](assets/gui/operator-controls.png)

### Opening checklist

- [ ] Correct controller origin and installation; current observed status.
- [ ] Authorized operator account and required live-output permission.
- [ ] Intended show loaded through verified Apply (or a deliberately managed external catalog).
- [ ] Required agents connected with correct assignments and fresh inventories.
- [ ] Actual video, audio, lighting, safe states, and physical surroundings checked.
- [ ] Content/render validation and supervised rehearsal accepted.
- [ ] Known-good rollback package and technical operator available.
- [ ] No unexplained degraded state, command failures, timing faults, or storage alarms.
- [ ] Correct mode and deliberate decision about automatic observations.
- [ ] Physical safe-stop procedure available even if the browser/network fails.

### Start and monitor

1. Open **Operate** and check fresh loaded-show/playback status.
2. Choose a loaded starting scene if needed. Only its required media devices
   block Start; unused/optional media does not block an LED-only scene.
3. Choose **Start show** and confirm the affected installation/outputs.
   Start enables Automatic input; the advanced Automatic control can separately
   enable audience-driven behavior without starting playback.
4. Verify the expected active scene, playback state, advancing authoritative
   time, per-output media/lighting state, and physical result.
5. Optionally **Open output monitor** for last successfully sent LED colors on
   mapped photos, sample age/held/stale/send-error coverage and separate
   device-reported media status. It is read-only, at most 2 Hz and pauses in hidden
   tabs. It does not capture decoded projector video or prove physical output.
   More playback/recovery controls, setup tests and diagnostics are contextual;
   monitor loss does not hide Stop/Blackout.

Do not treat an enabled Start button as a substitute for the opening
checklist. On required clock-source loss, time-following freezes and lighting
rendering is cancelled; another required output's loss marks the scene
degraded. Inspect and recover rather than repeatedly pressing Start.

### Controls and operating modes

| Control | Effect and operator caution |
| --- | --- |
| Pause / Resume | Controls active playback; verify required outputs and time-following respond |
| Position (ms) / Seek | Seeks an active scene to media-relative milliseconds; test interruption and cue effects before using with an audience |
| Stop | Cancels Apply and stops without draining the queued scenes; disables automatic input; verify physical safe handling |
| Automatic | Permits external observations to drive audience/director behavior |
| Maintenance | Stops active playback and rejects automatic observations; direct operator control remains possible |
| Reset ambient | Starts configured encounter ambient, or program initial scene if no encounter; does not change the selected mode |
| Blackout | Stops normally, emits an explicit all-black pixel frame, and rejects automatic observations |

The engine also has a disabled mode, but the current GUI does not expose a
Disabled button. Reset is not "return to automatic": after a reset in
maintenance or blackout, deliberately select the intended mode.

**Stop alone does not select Maintenance or Blackout.** If automatic behavior
must remain suppressed, choose an appropriate mode and confirm its resulting
state. Observations may still arrive over a connected ingress outside automatic
mode, but are rejected before they alter show behavior.

### Incident and recovery runbook

1. For an unsafe visible result, choose **Blackout** if the controller is
   reachable. If delivery is uncertain or hardware remains unsafe, invoke the
   site's independent physical safe-stop procedure.
2. Confirm mode and physical state; do not assume an HTTP success means an
   unreachable device received a command.
3. Stop/restrict automatic behavior while diagnosing. Record time, scene,
   release/revision, resource, observed health, and the action taken.
4. Check **Recent command failures**, per-output status, connection freshness,
   and automation facts. Ask the technical operator to inspect service logs,
   device lifecycle, assignment, cache, and host display/audio state as needed.
5. For content failure, have an authorized publisher restore known-good
   artifacts and coordinate the runtime catalog rollback if required.
6. Re-test under supervision, verify ambient recovery and the physical result,
   then deliberately resume automatic operation.

If an action is rejected, refresh and inspect the displayed fault and
permissions. Avoid repeated commands that conceal the initial failure.
Reconnect/restart reconciliation uses fenced sessions; do not work around it
by copying credentials or impersonating another device identity.

### Closing checklist

- [ ] Select Maintenance or Blackout to suppress further automatic observations.
- [ ] Confirm active playback stopped and physical outputs are safe.
- [ ] Disarm maintenance simulations and close editorial previews.
- [ ] Stop on-demand media-agent tasks according to the site's host procedure.
- [ ] Record faults, performance observations, and any manual calibration changes.
- [ ] Confirm backup/retention tasks with the technical operator; sign out.

## Current boundaries

Keep these differences between the interface, runtime, and product roadmap
in the operating plan:

- New installation records are unprovisioned; the selector does not switch
  between independently running installations.
- Publication/device artifact activation is not controller catalog reload,
  and release deactivation is not a physical stop.
- Mapper and Operate maintenance patterns are simulation-only.
- The publication rehearsal has the observed completion issue described above;
  rehearsal stop/blackout controls are not live safety controls.
- The application runs one scene at a time. Authored composition/audio layers
  and multi-output scenes do not imply arbitrary simultaneous independent scenes.
- Browser audio/proxies and mpv telemetry are not measurements of photons or
  sound reaching an audience.
- There is no native wall-clock start schedule, full anonymous review preview,
  or complete GUI account-lifecycle panel.
- Linux target-hardware acceptance remains outstanding; consult the platform
  runbook instead of assuming qualification.

For creative specifications beyond the UI walkthrough, use the
[show creator guide](show-creator-guide.md). For implementation plans rather
than operator procedures, use the [GUI roadmap](gui-roadmap.md).
