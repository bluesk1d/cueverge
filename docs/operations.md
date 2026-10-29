# Operations and simulation

For task-oriented service documentation, start with the
[service administration guide](service-administration.md) or the
[installation and show operator guide](operator-guide.md). This document is
the supporting engine/deployment reference, not the GUI walkthrough.

Production GUI operating limits and the administrator observability contract
are documented in [GUI scale budgets](scale-budgets.md).

## Local users and project access

The configured administrator remains the bootstrap and recovery identity. It
cannot be replaced through the GUI. Administrators create controller-managed
local users in Settings; passwords are stored only as salted scrypt hashes.
Disabling an account invalidates its existing sessions on their next request,
and role changes also take effect on the next request.

Local users see only projects with an explicit membership. Project roles govern
drafts, comments, change requests, rendering, simulation, and publication on
the server. The separate `can_live_output` membership permission is required
for operator actions and for activating, deactivating, or rolling back a
published package. Grant it only to operators who are authorized to affect the
physical installation. Review links contain high-entropy, hashed-at-rest
tokens, expose only revision identity metadata, and stop resolving after their
configured expiry.

## MCP-assisted authoring

The controller exposes the stateless Streamable HTTP MCP endpoint at `/mcp/`
when the GUI authoring service is enabled. Terminate TLS at the trusted reverse
proxy and configure its public HTTPS origin in `CUEVERGE_GUI_ALLOWED_ORIGINS`.
Cueverge uses that origin for the MCP resource URL and restricts MCP Host and
Origin headers to configured/local controller hosts.

Open a show and create client tokens under
**Advanced authoring and history > MCP-assisted authoring**.
Token creation/revocation and human proposal-review actions are authenticated
GUI mutations and must include the session's `X-CSRF-Token` header. A
`csrf_failed` 403 is not an MCP bearer-token or device-certificate failure;
keep CSRF protection enabled and check the browser request/session.
The token secret is displayed once, stored only as a SHA-256 digest, bound to
one project and user, and limited to a maximum 90-day expiry. The safe default
is the `read` scope. Additional independent scopes are:

- `draft_edit`: create isolated proposals; never changes the working draft.
- `direct_edit`: save a working draft immediately as a new revision, without a
  proposal or human approval/merge. Saving a draft identical to the current one
  creates no revision (audited as `draft.update` `unchanged`), so revision-bound
  bakes stay current; LED-only bakes also stay current across saves that change
  nothing they draw. Requires designer, publisher, or administrator
  project access. Also permits queueing/canceling offline render jobs.
- `simulation`: run bounded deterministic simulations, scene LED frames, and composition image previews
  without hardware.
- `publish_prepare`: inspect publication readiness and package status.
- `publish`: update release metadata/approval, publish a validated immutable
  revision, stage its artifacts, and queue/cancel offline render jobs. Requires
  publisher or administrator access.
- `activate`: activate a staged package's artifacts. Requires publisher or
  administrator project access **and** that project's live-output grant; global
  administrators bypass those membership checks, as in the GUI.
- `live_operation`: explicitly confirmed transport of the token's actually
  loaded show. Requires current operator/administrator permission and live-output
  access. It does not grant editing, publication, enrollment or access administration.
- `input_config`: configure/reconnect installation camera inputs. Requires a
  current global administrator and a project bound to the runtime installation.
  A project administrator or `direct_edit` grant alone is insufficient. Real
  events can trigger loaded automatic-show responses, so grant deliberately.
- `installation_edit`: edit installation photo descriptions through the separately
  scoped tool. Requires global installation-author and bound-project author
  permission; `direct_edit` alone is insufficient. It does not grant device
  enrollment, assignment, geometry writes or physical control.
- `photo_replace`: replace an installation reference image from a ready original
  image asset owned by the token's project. Requires installation-author and
  bound-project author permission, the runtime installation binding, current
  photo revision and explicit alignment review. Existing installation_edit
  tokens do not gain this grant. It does not grant device or geometry control.

All optional scopes start unchecked. Draft/deployment scopes remain
independent: `direct_edit` does not grant `publish` or `activate`, and `publish`
does not grant `activate`. Offline bake queue/cancel reuses an existing explicit
`direct_edit` or `publish` grant; no new scope is added automatically. Read-only
and proposal-only tokens remain unable to bake. To change a token's grants,
issue a new token with the intended scopes, update the client, and revoke its old token.
Grant these permissions only to trusted clients: direct edits and release
approvals can happen through chat without a subsequent GUI confirmation.

### Installation-scoped tokens

Show-scoped tokens above stay bound to one show. A global administrator can also
create an **installation-scoped** token under **Settings > Integrations >
Assistant access for all shows** (`POST /api/gui/v1/integrations/mcp-tokens`,
CSRF-protected; `GET` lists and `DELETE …/{token_id}` revokes). It uses the same
scopes, one-time secret, digest storage and 90-day maximum expiry, and is bound
to the runtime installation instead of a show, so it keeps working when you apply
a different show. Use it for long-lived assistant connections such as a ChatGPT
tunnel.

Every MCP tool accepts an optional `project_id`. `list_shows` returns the shows
bound to the installation that the token's user can access, with the loaded show
marked. With an installation token:

- Show-level tools (drafts, scenes, previews, publication, Apply) require
  `project_id`; omitting it returns an error that points to `list_shows`.
- `get_capabilities`, `get_output_monitor`, `operate_loaded_show` and
  `control_device_player` default to the show currently loaded on the display.
- Installation-level tools (`get_runtime_status`, setup, stage map, inputs and
  Home Assistant) work without a show.
- Each call is authorized against the user's **current** role and live-output
  grant in the selected show, intersected with the token's scopes. The token
  stops working if its user is disabled or is no longer a global administrator.

A show-scoped token may pass `project_id` only for its own show; any other value
is rejected. `get_capabilities` reports `token_scope` (`show` or `installation`)
and the `show_selection` contract. Audit actions are
`mcp.installation_token.create` and `mcp.installation_token.revoke`.

### Home Assistant control over MQTT

Cueverge can appear in Home Assistant as a device through MQTT discovery. No
custom Home Assistant integration is needed; the built-in MQTT integration
creates the entities. It reuses the broker connection configured for camera
events under **Installation > Inputs**, including the stored password and TLS.
The bridge is **off by default**. A global administrator turns it on under
**Settings > Integrations > Home Assistant**, with
`PUT /api/gui/v1/integrations/home-assistant` (`{"enabled": true,
"expected_revision": N}`), or with the MCP tool `configure_home_assistant`, which
requires `input_config`. Each change is audited as `home_assistant.configure`.
`get_home_assistant_integration` and `GET …/integrations/home-assistant` read the
status.

With base topic `cueverge/<installation-id>`:

| Topic | Direction | Payload |
| --- | --- | --- |
| `…/availability` | published, retained, last will | `online` / `offline` |
| `…/state` | published, retained, on change | JSON: `playback`, `active_scene`, `active_scene_name`, `mode`, `mode_label`, `loaded_show`, `loaded_revision`, `degraded` |
| `…/command` | subscribed | `start`, `stop`, `pause`, `resume`, `blackout` |
| `…/scene/set` | subscribed | a scene name (or ID) from the loaded show; starts it |
| `…/mode/set` | subscribed | `Automatic`, `Stopped`, `Maintenance`, `Blackout` |

Discovery configs are retained under `homeassistant/<component>/cueverge_<id>/…`:
five buttons, a **Start scene** select whose options follow the loaded show,
a **Show mode** select, and **Playback**, **Active scene** and **Loaded show**
sensors. Turning the bridge off clears those configs, so the device disappears.

Commands run through the same operate path as **Operate**: Apply interlocks,
recovery requirements and Stop/Blackout priority still apply. Each command is
audited as `mqtt.operate.<action>` (actor `home-assistant`) or `mqtt.operate`
with `result=error`. Payloads over 256 bytes, unknown commands and scenes that
are not in the loaded show are rejected and listed under **Topics and recent
commands**. MQTT has no per-message authentication: anyone the broker allows to
publish to `…/command`, `…/scene/set` or `…/mode/set` can control the show.
Give Cueverge its own broker user and restrict those topics with broker ACLs.
Changing the broker settings reconnects the bridge automatically.

Cueverge drives WLED with DDP while a scene renders, which overrides WLED presets.
Coordinate Home Assistant automations so presets and the show do not run at the
same time, for example by pressing **Stop show** before activating a preset.

### Observing the running installation

`get_capabilities.runtime_status` discovers `get_runtime_status`, its `read`
requirement, and whether the token's project is bound to this controller's
runtime-backed installation. Unbound or unrelated-installation projects cannot
use that tool to observe this runtime. It grants no transport or deployment
permission.

The tool and authenticated `GET /api/gui/v1/runtime/status` expose the same
controller facts, `source`, `observed_at`, `installation_id`, and additive
`loaded_catalog` identity envelope. Legacy/external catalogs have
`state: "unreported"` and null project/revision/digest/load-time/generation.
Managed Apply reports `loaded` with the acknowledged project, friendly name,
revision, digest, load time and generation. Failed retained startup recovery
reports `recovery_required` with an explicit error and playback disabled.
Do not substitute the authoring project's identity or a published/active artifact
record. A status snapshot is not candidate preflight or proof of physical output.
Oversized MCP snapshots fail the existing 200 KiB result boundary explicitly.
The shared envelope also includes compact secret-free camera-input health and
active Apply progress. Connection, recent reception, visitor matching and
playback remain distinct facts; active Apply blocks ordinary transport but not
authorized Stop/Blackout.

### Applying saved shows and recovering

Use **Shows > Apply saved changes** for managed catalog handover, not the
advanced artifact **Activate** button. Saving never applies live output.
`get_capabilities.runtime_apply` and `get_authoring_schema.x-runtime-workflows`
discover `apply_saved_show`, its standalone request schema, `get_apply_jobs`,
and `cancel_apply`.

Example tool arguments (replace versions/scene with the saved project's values):

```json
{
  "request": {
    "source_revision": 3,
    "expected_head_revision": 3,
    "approve_release": true,
    "confirm_interruption": true,
    "starting_scene_id": "ambient",
    "preserve_controller_looks": true
  }
}
```

Normal Apply requires **both** `publish` and `activate`, a current publisher/
administrator with live-output permission, runtime installation binding and
explicit interruption confirmation. Approval covers only the exact revision.
Changing head, authorization, token validity or installation/asset/device/mapping
dependencies during preparation rejects the candidate before load.

Before publishing, the job bakes every requirement for the revision that is not
current (`state: "baking"`, with a `bakes` list of `id`, `kind`, `name`, `job_id`,
`state`, `progress` and `error`). It reuses queued/running render jobs, fails the
Apply with the bake error when a bake fails or is `unavailable`, and on
cancellation cancels only the jobs it queued. The dependency guard is taken
after baking. `get_bake_requirements(revision=None)` (`read`) and
`GET /api/gui/v1/projects/{project_id}/bake-requirements?revision=` list the same
requirements: `kind` `composition`, `lighting`, or `scene_output`, with `state`
`current`, `queued`, `running`, `failed`, `missing`, or `unavailable`.

The job prepares an immutable package, verifies files, stages original or baked
media by checksummed digest and receives transfer acknowledgements before
interruption. Runtime media IDs resolve to device-cache digest filenames; the
working draft's asset IDs are unchanged. Ambiguous multiple baked roots for one
media output are rejected: create one combined native composition first.
Routing/kind/pixel-count deployment changes require deployment configuration,
not a silent GUI sink replacement. Each show's ambient lighting comes only from
its own draft: Apply never carries the running show's ambient baseline into
another show, so outputs without ambient in the applied show black out between
scenes. `preserve_controller_looks` (default `true`) only keeps the running
safe-state look on outputs the applied show's ambient lights, and only when its
fixture/segment/path targets exist in that show's installation; otherwise the
safe state is blackout.

Poll the durable job by `get_apply_jobs(job_id=...)` or authenticated
`GET /api/gui/v1/projects/{id}/apply-jobs/{job}`. Preparation/publishing/staging
can be cancelled. After handover begins, use Stop/Blackout or retained recovery;
Stop fences in-flight starts and cancels the Apply. A playing show restarts
the selected/initial scene from zero after stop/start acknowledgements; paused
or stopped stays stopped with automatic input disabled. No exact-position or
wait-for-scene-end handover is promised.

Restore with `restore_package_id`, its matching `source_revision`, the current
`expected_head_revision`, and interruption confirmation. Restore requires
`activate`, not new publication approval, and never overwrites the working head.
Reapply an edited saved show to change retained content. Controller restart
re-verifies and loads the retained managed catalog **stopped**, before input
ingress; it never resumes a show automatically. Corruption or incomplete
handover is reported, never disguised as a successfully loaded bootstrap show.

### Read-only output monitor and explicit transport

**Operate > Open output monitor** polls at most 2 Hz, without overlapping
requests, only while open and the tab is visible. **Overview** reuses the same
poller automatically while status is current and a scene with LED outputs is
playing (operators can pause it); it draws mapped samples on the hero photo and
falls back to the plain photo on monitor failure or loaded-generation mismatch.
It observes the last successful
sink send, including scene, safe-state, blackout and physical-test frames. It
does not render a second show or send hardware commands. Capture is hard-capped
at 5 Hz, retains the latest frame for at most 32 resources, and returns at most
4096 LED samples per request (1000 per output). Select an output and
`pixel_offset` for another range; omitted/unmapped/unsupported coverage is explicit.
The default controller memory sink retains 120 frames; explicitly supplied
simulation sinks keep their existing full-history default.

`get_output_monitor(request={"resource": "porch.demo", "pixel_offset": 0})`
requires `read`, matching installation and the token's **actually loaded**
project. The authenticated GUI endpoint is
`GET /api/gui/v1/runtime/output-monitor`. Samples identify their age, held/stale
state, send failures and loaded generation. A retained frame is not evidence
that a later send succeeded; stopped LEDs can hold their last colors where no
safe look was configured. Photo overlays use saved physical mappings, not a
wrapping pixel row. Raw address order is collapsed diagnostics.

Media output is agent-reported connection/clock/position data; decoded video,
projector photographs, electrical LED feedback and independent audio verification
are not captured. Inspect the real installation separately. Monitor/network
failure never removes authorized Stop/Blackout controls.

`operate_loaded_show(request={"action": "start", "scene_id": "ambient"},
confirm_physical_output=True)` is **real transport**, not simulation. It
requires separately opted-in `live_operation`, current operator/admin and live
permission, the runtime installation and loaded project match. Omit `scene_id`
to use an existing program; select a loaded scene for LED-only catalogs without
a program. Use actions `pause`, `resume`, `seek` (with `position_ms`), `stop`,
`reset`, `automatic`, `maintenance` or `blackout` as appropriate. Normal actions
are blocked during Apply/recovery; Stop and Blackout remain available. Existing
MCP token rate limits apply even though GUI monitoring polls at 2 Hz.
When loaded project identity is unknown, use the authorized GUI Stop/Blackout
or the site's physical safety procedure. MCP never weakens project isolation
to control another or unidentified show.

`get_device_players` (read) lists enrolled media hosts with their resources,
connection policy, connection, `player_control` and `player_state`
(`stopped`, `starting`, `ready`, `stopping`, `failed`), plus `keep_awake`
(`{mode, active, detail}` or null). `keep_awake` is set on each media host with
`CUEVERGE_KEEP_AWAKE`, so it's read-only through MCP and the GUI. The GUI shows it as
**Keep awake** in device details and warns on the device card when it isn't active.
`control_device_player(device_id=..., action="start"|"stop",
confirm_physical_output=True)` opens or closes a host's fullscreen player. It has
the same `live_operation`, live-permission, loaded-project and confirmation
requirements as `operate_loaded_show`, and is audited as
`mcp.device.player.*`. A `start` action in `operate_loaded_show` also starts
stopped players that the loaded show needs, and fails with the device's reason
if one can't start. `get_capabilities` describes this under `device_players`.

`get_device_agent_settings(device_id)` (read) returns a media host's controller
overrides, its effective settings and host defaults, the detected `displays`
(likely `--fs-screen` indices) and mpv `audio_devices`, any
`pending_player_restart` settings and `sync` (`applied`, `pending`, `offline`,
`unsupported`, `failed`). `set_device_agent_settings(device_id, settings,
expected_revision)` replaces the overrides (`keep_awake`, `player_mode`,
`fullscreen_screen`, `audio_device`, `audio_delay_ms`, `telemetry_hz`; omitted
or null means host default). It requires the explicit `device_config` scope
(global administrators only) and the revision you read. Display changes restart
the player only in maintenance mode with nothing playing. Updates are audited as
`device.agent_settings.update`. The GUI equivalent is **Device details > Agent
settings**; `get_capabilities` describes it under `agent_settings`.

Installation output timing is hardware latency compensation shared by every
show. `get_installation_timing` (read) lists one delay per output of the runtime
installation (`media` projectors 0–5000 ms start later; `lighting` LED outputs
-5000–5000 ms render behind scene time) with each `revision`, plus any running
calibration (`delay_ms`, `saved_ms`, `state`, `drift_ms` per output).
`set_installation_output_timing(resource, delay_ms, expected_revision=None,
confirm_physical_output=True)` saves one value and pushes it to the runtime; no
Apply is needed and scene-level delays stack on top. The live wizard is
`start_timing_calibration(projectors=[{"resource": ..., "click": true}],
leds=[...], confirm_physical_output=True)` (maintenance mode, nothing playing;
it stages the reference clip and starts on-demand players),
`adjust_timing_calibration(resource, delay_ms)`, `resync_timing_calibration()`
and `finish_timing_calibration("accept"|"cancel")`. All write tools require
`live_operation`; saves are audited as `installation.output_timing.set` and runs
as `installation.timing_calibration.*` and `mcp.installation.timing.*`. Client
audio delay stays on `set_device_agent_settings` (`audio_delay_ms`). The GUI
equivalent is **Installation > Timing**; REST endpoints are
`GET/PUT /api/gui/v1/installations/{id}/output-timing[/{resource}]` and
`/api/gui/v1/installations/{id}/timing-calibration[/outputs/{resource}|/resync|/accept|/cancel]`.
`get_capabilities` describes it under `installation_timing`.

`get_agent_updates` (read) lists each media host's running agent version, bundle
digest and update state (`current`, `available`, `updating`, `failed`,
`rolled_back`, `unmanaged`, `unsupported_platform`, `no_bundle`,
`disconnected`) with the bundles this controller offers.
`update_device_agents(device_ids=None, confirm_restart=True)` installs the
controller's bundle on the listed devices, or on every device whose state is
`available`, `failed` or `rolled_back`. It requires the explicit
`device_update` scope (global administrators only), the runtime installation,
and maintenance mode with no scene playing. It returns immediately; poll
`get_agent_updates`. Agents restart, and the host launcher rolls back a version
that can't reconnect within 180 seconds. Starts are audited as
`mcp.device.agent_update.start`. The GUI equivalent is **Installation > Devices >
Connect and manage devices > Agent software updates**; `get_capabilities`
describes this under `agent_updates`.

### Photo-first installation setup

The installation workspace separates **Photos & Layout**, **Devices**, and
**Inputs & connections**. Photo descriptions reuse the persisted `name`; the
additive read-only `description` alias is not a second authority. Existing photo
UUIDs, independent legacy `viewpoint` labels, crop, scale, orientation, image
digests, geometry links and calibration fields remain unchanged by description edits.

Photo upload retains its 25 MB/type/authentication boundary. New browser clients
send `X-Photo-Description` and `X-Original-Filename` URI-encoded with
`X-Photo-Metadata-Encoding: uri`. Legacy `X-Photo-Name`/`X-Photo-Viewpoint` headers
still work; when a new upload omits viewpoint, the description supplies its
legacy label. The UI asks for only one description and explicitly submits after
file selection. Invalid UTF-8/description metadata is rejected.

Authenticated `PATCH /api/gui/v1/installation-photos/{id}/description` requires
the existing installation-author, origin/CSRF and `If-Match` revision checks,
and changes only the description/name. Full legacy photo PUT remains available
for explicit advanced metadata edits. Re-read after conflicts (reload the
Installation page) rather than silently overwriting concurrent work. In the GUI,
**Add photo** opens a dialog: choose the file first; its name prefills the
description.

Example metadata-only request (with an authenticated session, allowed origin
and its CSRF token):

```http
PATCH /api/gui/v1/installation-photos/PHOTO_ID/description
Content-Type: application/json
If-Match: "3"

{"description":"Front porch for roofline placement"}
```

MCP `get_installation_setup` requires `read` plus runtime installation binding
and returns photos, paginated layout summaries (1–100), observed agents and actual
device-setup boundaries. `describe_installation_photo` requires independent
`installation_edit`, an installation-author role, bound-project author permission,
matching photo installation and current photo revision. Discovery and
`get_authoring_schema.x-installation-setup` publish the schema and limits.
Device enrollment/assignment remain administrator GUI workflows with their
existing authorization, CSRF, TLS, protocol, idle/disconnect and lifecycle
confirmation checks. No MCP device-control grant or geometry-write capability
was added for this presentation pass.

### Optional photo backgrounds and safe replacement

Geometry belongs to the installation, not to the reference image.
`InstallationGeometryRequest.photo_id` accepts `null` or omission for a
background-free layout. Existing linked placements keep their IDs, landmarks,
coordinates, wiring and calibration. **Open layout without a photo** works even
in an installation with no images. Blank-canvas scene, visitor, intended-output
and wiring previews do not require uploading a dummy photograph.

For example, an installation author can create a background-free fixture through
the existing authenticated GUI/REST geometry workflow (normal origin/CSRF checks
still apply; this is not a newly granted MCP geometry write):

```json
{
  "photo_id": null,
  "kind": "fixture",
  "name": "Porch decoration",
  "points": [{"x": 0.25, "y": 0.6}],
  "closed": false
}
```

Deleting a photo detaches its reference and advances affected geometry revisions;
it does not delete geometry or calibration. Removing an installation or explicitly
deleting a geometry object remains destructive and uses existing authorization.
The ownership migration cannot recover records already deleted by older versions.

For replacement, use **Replace photo > Replace photo, keep layout**. The
authenticated author endpoint is `PUT /api/gui/v1/installation-photos/{id}/content`
with raw JPEG/PNG/WebP bytes (25 MB maximum), `Content-Type`, `If-Match`,
`X-Original-Filename`, optional `X-Photo-Metadata-Encoding: uri`, and required
`X-Confirm-Alignment-Review: true`. It preserves photo ID/metadata and all
geometry/mappings. Invalid bytes or stale revisions fail without changing the
photo. Revisioned image URLs refresh the displayed background.

MCP `replace_installation_photo` has a **separate, unchecked-by-default**
`photo_replace` scope. It uses an existing ready original image asset in the
token's project; it accepts no local paths or arbitrary URLs. Current installation
and bound-project author roles, runtime installation binding and the photo's
revision are rechecked. `installation_edit` remains description-only.
Discover the standalone schema under `get_capabilities.installation_setup` or
`get_authoring_schema.x-installation-setup`.

```json
{
  "request": {
    "photo_id": "PHOTO_ID",
    "source_revision": 3,
    "asset_id": "READY_IMAGE_ASSET_ID",
    "confirm_alignment_review": true
  }
}
```

A new photograph does not automatically reposition geometry or establish valid
physical calibration. Review different framing/camera angles before relying on
the overlay. Old view coordinate spaces, including projector-mask relationships,
are retained independently of image records. No geometry-write, live-output,
device-enrollment or access-management grant is added.

Layout defaults to Select; Add explicitly enables drawing, and canvas/list
selection reveals contextual properties. Optional strand labels, associations,
stage coordinates and calibration are disclosures. Multi-projector coverage is
an optional **Show projector coverage** overlay on the stage view's canvas.
Output/index conflicts remain visible, including nested ranges on one output.
Linked views are counted once and different outputs are never compared as one
index space. Photo bounds, coverage gaps and draw precedence are nonblocking
observations, not proof of physical wiring faults in a three-dimensional display.
Canvas traversal remains distinct from authored scene Preview or armed physical
tests. If geometry saves but its secondary mapping fails, the retained object ID
is used on retry, avoiding duplicate creations.

### Stage reference view and shared-stage placement

The installation's shared stage is defined by one **stage view** photo. Stage
coordinates are the stage view's normalized layout coordinates scaled by
`units_per_canvas`: `stage_x = x × 1000 × units_per_canvas` and
`stage_y = y × 600 × units_per_canvas`, so a 40-unit-wide view is 40 × 24 stage
units. `unit_label` (`ft`, `m`, `in`, `cm`, or `units`) is for display only.

REST (installation author, origin/CSRF, revisioned):

- `GET /api/gui/v1/installations/{id}/stage-reference` returns the reference,
  counts and per-object `status` (`placed`, `needs_update`, `manual`,
  `unplaced`) plus pairwise `projector_overlaps` fractions. The ETag is the
  reference revision.
- `PUT …/stage-reference` with `StageReferenceRequest`: `photo_id`,
  `unit_label`, and either `stage_width` (width of the whole view) or
  `scale_points` (two normalized points) with `scale_distance`. Omit
  `If-Match` only when no reference exists.
- `POST …/stage-reference/apply` with `If-Match: "REVISION"` derives and
  writes placement for every object drawn on the stage view: geometry
  `stage_points` (for all linked instances), lighting-surface `stage_matrix`
  (an exact similarity for two points, least-squares affine otherwise), and,
  for surface-placed planar projector mappings, a `stage_homography` from
  normalized native `(u, v)` to stage units with a bounding
  `stage_x/y/width/height`. Subdivided projector meshes receive stage
  coordinates only when their vertex count matches the layout points.
  Manually placed projectors are skipped and reported with a `manual` message.
  Revisions advance and one audit event is recorded.

Derived values are stored state: removing the stage photo keeps them, and
changing photo calibration does not move them until the next apply. With a
homography, the mapper export emits an 8×8 perspective mesh, `stage-coverage`
returns exact polygon regions, and the export's `stage` object gives `width`,
`height`, `unit` and the reference photo. Range checks then use the stage view
rather than the projector bounding box, so LEDs outside projector coverage
remain valid.

#### Projector stage placement

`ProjectorMappingRequest.stage_placement` is `surface` (default) or `manual`.

- `surface`: the server derives `stage_x/y/width/height`, the planar
  `stage_homography` (or subdivided mesh stage coordinates) from the drawn
  projector surface and ignores submitted stage values when derivation
  succeeds. The basis, reported as `stage_placement_basis`, is the first
  available of `stage_view` (the linked surface drawn on the stage reference
  photo), `stage_points` (saved geometry stage points), or `default_scale`
  (only when no stage reference exists: the mapped drawing at
  `units_per_canvas = 0.01`). Creating or updating `projector_surface` geometry
  re-derives every surface-placed mapping with that semantic ID, advancing its
  revision and recording an `installation.projector_mapping.stage_placement`
  audit event. When nothing can be derived, the submitted rectangle is stored
  and `stage_placement_message` explains why.
- `manual`: the submitted rectangle is stored without a homography and is never
  rewritten by geometry edits or stage reference apply. A manual mapping on the
  default `0, 0, 4, 2.25` rectangle gets a warning `stage_placement_message`.

Migration 43 adds the column, marks existing planar mappings with a non-default
rectangle and no derived homography as `manual`, and re-derives the rest.

MCP:

- `get_stage_map` (`read`, runtime installation binding) returns the same
  status document.
- `update_stage_reference(request, source_revision, apply=true)` requires the
  explicit `installation_edit` scope and an installation author role. It sets
  the reference and, by default, applies placement. It never changes photo
  calibration, wiring, native projector pixels, or hardware output. Re-read
  with `get_stage_map` after a revision conflict.
- Discover both under `get_capabilities.installation_setup.stage_reference`
  (with `StageReferenceRequest` JSON schema) and `features.stage_reference`.
  `get_spatial_layout` includes the `stage` frame and reports stage units.
- `get_capabilities.installation_setup.projector_stage_placement` describes the
  `surface`/`manual` modes and basis order. Each `get_spatial_layout` projector
  reports `stage_placement` beside its `stage_extent`. MCP reads placement
  only; changing a projector's placement mode is administrator GUI work, and
  `update_stage_reference` apply skips manually placed projectors.

```json
{
  "request": {"photo_id": "PHOTO_ID", "unit_label": "ft", "stage_width": 48},
  "source_revision": 2,
  "apply": true
}
```

### Guided visitor authoring and isolated Preview

The show workspace's **Visitor responses** uses authenticated project reads at
`GET /api/gui/v1/projects/{id}/interactions`. Focused updates use `PUT` on that
path, with `InteractionEditRequest`, project designer/publisher/administrator
authorization, origin/CSRF checks, and a current `source_revision`. Only the
requested response fields, visitor area, transition scene or explicitly consented
scene completion changes are written; existing IDs, extra timing, conditions,
state choreography and unrelated metadata remain intact.

The following MCP tools use the same projection, editing and preview services:

| Tool | Scope | Behavior |
| --- | --- | --- |
| `get_interactions` | `read` | Plain-language cards and existing normal-playback/visitor definitions |
| `update_interaction` | `direct_edit` | Current-revision focused save; no publish, activation or live injection |
| `preview_interaction_workflow` | `simulation` | Isolated saved/proposed-candidate test, checkpoint trace and one sampled evaluated frame |

`get_capabilities.interactions` and `get_authoring_schema`'s
`x-guided-interactions` expose edit/preview schemas and model boundaries. Extract
each nested schema as a standalone JSON Schema document. Human labels live in
`editor_metadata.interaction_names`; they are not a second response engine.
Immediate/custom/state-dependent creation remains in advanced authoring.

Browser `POST /api/gui/v1/projects/{id}/interaction-preview` requires project
designer/operator/publisher/administrator permission and CSRF. `proposed_draft`
or `proposed_edit` is evaluated without saving; installation binding cannot change.
`source_revision`, `draft_digest` and `unsaved` identify the exact candidate.
The client invalidates checkpoints when candidate/revision/test inputs change.
No MQTT/DDP/media adapters are opened, and hardware flags are always false.

Tests use the actual controller and production Frigate normalization, freshness,
presence qualification, response arbitration and rendering. Fake media agents are
healthy by default; this is not a preflight or a guarantee of real playback.
Clock advancement ticks qualification every 100 ms, matching the controller, while
scene completion and interruption/queue decisions remain engine-owned. Per-event
matching comes from the ingestion result, not global-counter differences.
Scene changes within waits are recorded; checkpoints show state after each action.

Limits: 200 actions, ten simulated minutes, 1,000 estimated scene boundaries,
65,536 installation pixels, 20,000,000 estimated pixel-index work,
20,000 media telemetry ticks and 4,096 returned RGB samples. Interactive evaluation
shares the two nonblocking scene-preview slots. Long/high-work tests fail explicitly
rather than queue unbounded work. MCP retains its 200 KiB result budget; reduce the
test/output samples when necessary. Unbaked spatial shows retain a decision trace
but withhold misleading placeholder colors.

The `visitor` scenario step carries camera, optional zone, label, object identity,
arrive/stay/leave and optional observation age. Saved scenarios containing visitor
steps reuse the same normalized ingestion and clock advancement and return
`actual.visitor_checkpoints`. Their `actual.clock_ms` remains elapsed test time.
Legacy direct `observe` scenarios remain supported and explicitly bypass camera
normalization. Visit examples do not prove real source label filters/reception.

### Show plan authoring

**Show plan** reads `GET /api/gui/v1/projects/{id}/show-plan` with project read
access. It is a plain-language projection of existing runtime definitions: program
`initial_scene_id`, each scene's `completion`, and the program default pool
(the "shuffle list"), plus visitor-response lanes from the interactions projection
and scenes not reachable from the program, encounter, opportunities, completion
targets or failure fallbacks. No new runtime package fields are introduced.

`PUT` on the same path takes `ShowPlanEditRequest` with project designer/publisher/
administrator authorization, origin/CSRF checks and a current `source_revision`
(otherwise `409 revision_conflict`; validation failures return
`422 show_plan_invalid`). Operations write only:

| Operation | Writes |
| --- | --- |
| `starting_scene` | `program.initial_scene_id`; without a program, creates one with a single-scene default pool `main` |
| `scene_ending` | One scene's `completion`: `repeat`=`loop`, `play_next`=`transition_to` (`next_scene_id`), `shuffle`=`select_next` (requires a program), `freeze`=`hold`, `stop`=`stop` |
| `shuffle_list` | The default pool's ordered entries and weights; retained entries keep cooldown, conditions and availability |

The following MCP tools use the same projection and edit service:

| Tool | Scope | Behavior |
| --- | --- | --- |
| `get_show_plan` | `read` | Same JSON as the REST read, including `source_revision` |
| `update_show_plan` | `direct_edit` | Current-revision focused save; no publish, apply or hardware command |

`get_capabilities.show_plan` and `get_authoring_schema`'s `x-show-plan` expose the
edit schema and ending-to-completion mapping. Use `preview_interaction_workflow`
to rehearse the resulting order without live output.

### Camera input management

With the GUI enabled, **Installation > Inputs & connections** supports one
installation-owned Frigate/MQTT source. It is off until explicitly enabled.
`CUEVERGE_INPUT_MANAGEMENT=gui` selects durable controller-local configuration.
`CUEVERGE_INPUT_MANAGEMENT=environment` selects read-only operator configuration.
If no mode is specified, the presence of any `CUEVERGE_FRIGATE_*` environment
setting preserves environment management; otherwise GUI management is selected.
GUI mode ignores those Frigate environment values rather than mixing credentials.
Without the GUI service, the existing environment-configured ingress is unchanged.
Changing management mode requires a controller restart.

`GET /api/gui/v1/input-source` is authenticated, secret-free health and settings.
`PUT` requires a global administrator, allowed origin and CSRF token; it validates
before replacing active settings and supports `expected_revision` to reject
conflicting changes. A successful save means settings were persisted and the
affected listener restarted, not that subscription or real event reception
succeeded. The listener drains an in-flight controller delivery before disconnecting
and retries broker failures automatically. Other runtime/show state is not reset.

Browser health polls are serialized, visible-page-only, approximately two seconds
apart, with an eight-second timeout. Failures explicitly label retained facts as
last known; session expiry stops automatic requests without discarding edits.
Source freshness uses the controller's loaded audience freshness limit (five
seconds for diagnostic freshness when no audience is loaded), not browser clocks.
Stale Frigate observations cannot qualify new visitors through the managed source.
Recent diagnostics are limited to 20 entries and omit raw payloads/object IDs;
payloads over 256 KiB and nonfinite source timestamps are rejected. Counters reset
on connection reconfiguration or controller restart. Subscribed, received,
normalized/delivered, controller-accepted, fresh, and audience-matched are distinct.
None guarantees a scene response or visible physical output.

MCP discovery exposes `get_capabilities.input_source` and its configuration schema;
`get_authoring_schema` also includes the separate
`x-installation-input-configuration` schema. Input settings never belong in show
metadata or exports. Tools, all requiring the runtime installation binding:

| Tool | Grant | Effect |
| --- | --- | --- |
| `get_input_source` | `read` | Settings/credential-presence and observed connection/event/loaded-rule health, never credentials |
| `configure_input_source` | `input_config` + global administrator | Validate/persist/reconnect the GUI-managed source; may admit real events |
| `validate_input_source_payload` | `simulation` | Bounded isolated Frigate normalization and loaded visitor-rule matching; no live injection/network/hardware |

The browser's **Try an isolated example** uses authenticated, CSRF-protected
`POST /api/gui/v1/input-source/validate`. It checks saved labels and loaded rules,
not an unsaved draft, accumulated real visitor history, or full qualification
timing. Successful examples are never recorded as received real input.

Validation results include a `validation_context_digest` of installation,
configuration revision, accepted labels and loaded audience rules. Compare it
with current input health before reusing a result; the browser invalidates an
example when that context or pending connection edits change. This digest is
not a loaded-show/project identity or an end-to-end readiness certificate.

**Credential storage and recovery:** GUI-managed settings live in
`CUEVERGE_GUI_DATA_ROOT/input-source/settings.json`, referencing a separate
immutable `credential-<id>.json`. The input directory must be controller-owned
mode 0700, with regular nonsymlink files mode 0600; the controller data parent
(`CUEVERGE_GUI_DATA_ROOT`) must not be group/world-writable. The controller creates
that data root as mode 0700 on first start, but an existing bind-mounted folder
created with a looser mode (for example `770`) must be fixed by the operator, for
example `chmod 750 /path/to/cueverge-data/gui`, before GUI-managed inputs can be saved. This implementation enforces POSIX permissions;
use environment management on platforms without that protection. Permissions
are not encryption at rest. Passwords are write-only and absent from readback,
audit details, MCP results, logs and project exports.

Back up the private input directory and matching GUI database/installation
identity together using restricted operator backups. Stop the controller before
restoring a matching set, restore ownership/permissions, then restart and verify
actual subscription and reception. Missing credentials, mismatched installation
identity, or insecure storage disables managed ingress explicitly; there is no
anonymous/default-credential fallback. Do not copy these files into show packages,
support bundles or source control. A blank submitted password retains the secret;
`clear_password` removes it; a replacement is written separately before atomically
switching the settings reference and retiring the previous file. Revoke old
broker credentials independently and account for any old private backups. Storage
failures are explicit, including a retired-file cleanup failure after application.

TLS uses system-trusted broker certificates (`CUEVERGE_FRIGATE_MQTT_TLS=true` in
environment mode); configure the platform trust store for a private CA. TLS is
not automatic from the port number. Never expose a plaintext broker to an
untrusted network.

### Snapshot freshness and access

Clients should track elapsed time since a successful response with a monotonic
clock: the recommended visible refresh is 2000 ms and stale boundary is 10000 ms.
The browser uses an 8000 ms request timeout, does not overlap slow live polls,
and handles runtime and topology failures independently. Observation timestamps
remain useful for diagnostics but should not be subtracted from an unrelated
host clock to decide freshness. Hidden browser tabs pause live polling.
These refresh limits are presentation guidance, not the runtime render cadence,
device heartbeat limits, or audience observation freshness.

Current local-user status, project membership, role, and live-output grant are
rechecked on every MCP request. Disabling a user, removing project membership,
reducing the role, or revoking a token therefore takes effect immediately.
Rotate a client credential by creating a replacement, updating the client, and
revoking the old token. Revoke all affected tokens immediately if a secret may
have been disclosed.

Model output, filenames, asset metadata, comments, and imported text remain
untrusted data. MCP has no unrestricted filesystem, network, browser-internal,
device-enrollment, access-administration, or scene-transport tool. Proposals store a complete
schema-validated candidate draft, base revision, changed JSON paths, client/user
identity, diagnostics, and simulation result separately from the working
revision. A human designer or administrator must validate, approve with a
rationale, and merge through the cookie/CSRF-protected GUI API. Merge fails
closed if the working revision changed after proposal creation. Failed and
rejected proposals never affect the working revision or live show.

### Direct editing and deployment through chat

Start with `get_capabilities`. MCP contract version **2** advertises supported
composition spaces, spatial/shared-stage features, tool names, granted scopes,
and whether the current token can preview or bake. Empty project geometry is
not evidence that the renderer lacks spatial support. If the client still has
an old tool inventory after upgrading the container, refresh/reconnect its MCP
connection and call `get_capabilities` again.

Controller-clock support is an additive part of contract version 2.
`get_capabilities` advertises `features.controller_timed_scenes`,
`features.led_only_scenes`, and a `scene_timing` contract describing both clock
modes. `get_project_structure` includes each scene's `mode` and `clock_source`.
`get_authoring_schema` documents the clock-source and media-array constraints:

- For an LED-only show, use scene `schema_version: 2`,
  `mode: "controller_timed"`, `clock_source: "controller"`, `media: []`, and
  only lighting resource claims. No media-agent installation binding is needed.
- Controller-timed scenes may include media outputs. Required agents must be
  connected and participating outputs prepared before scene time starts, but
  telemetry does not drive that clock. Completion uses `duration_ms`; there
  is no automatic clock failover or ongoing media drift correction.
- Media-synchronized scenes still require a required media output as their
  clock source.

Use the existing `propose_project_revision` → `validate_proposal` →
`simulate_proposal` workflow to author and rehearse either mode without
hardware. With an explicit `direct_edit` grant, `update_project_draft` can save
the same schema directly. Preserve unrelated draft/editor metadata and remove
unused media references when converting an existing scene to LED-only.
Simulation results expose `actual.status.clock_authority`,
`estimated_position_ms`, and `playback_state` so clients can verify clock
behavior. These additions do not grant live transport or output access.
See [controller-timed scenes](scene-schema.md#controller-timed-scenes) and
the [LED-only example](../shows/led-only-demo).

`get_authoring_schema` describes composition settings (including
`stage_lighting_surfaces` and `stage_projector_resources`), output presets,
image/video/GIF media layers (including optional `corner_pin` and `mask`),
playback policy, keyframes, and timeline tracks under `editor_metadata`.
Draft/proposal saves still enforce resource references, unique IDs, intervals,
and other cross-field rules. Preserve unrelated GUI metadata when editing a
complete draft.

`get_capabilities.authoring_representations` distinguishes raw runtime scene
lighting from editor-owned timelines. `get_project_draft` is the authoritative
read for schema-v1 lighting objects and schema-v2 lighting output arrays,
including legacy effects and `lighting.timeline` cues described by `LightingCue`
in the authoring schema. They do not require editor metadata.
`get_timeline_range` reads editor markers, synchronized cues, transform layers,
and lighting author layers under `editor_metadata`; an empty range result does
not mean the scene has no runtime lighting. Direct draft writes still require
`direct_edit`; discovery does not add permission. Preserve the stored
representation, targets, policy, and unrelated metadata rather than rebuilding
the scene from editor-only range results.

`get_capabilities.authoring_representations.runtime_scene_media` describes scene
media separately from editor transform layers. Read and edit these outputs through
`get_project_draft` and the existing revision/proposal workflows; their schemas are
`MediaDefinition` (schema v1) and `MediaOutputDefinition` (schema v2).
`get_timeline_range` does not include these runtime media outputs. In the UI,
open **Scenes > Edit timeline**, select the media strip, and use the **Inspector**
to change its asset, or (v2) `start_delay_ms`, `end_behavior`, and `required`.
Preserve output IDs, resource bindings, unrelated outputs and editor metadata.
Start delay remains an integer in 0–5000 ms, and a media clock source must remain
required. Save through the existing validated working-draft boundary; this does
not publish, apply, or send hardware commands. Simulation semantics are unchanged:
these are existing runtime media fields, not a new clip or playback representation.

Scene length is `scenes[].duration_ms` (a positive integer) in the draft read by
`get_project_draft`; edit it with the same direct-edit or proposal workflows.
Lengthening a scene never rewrites existing layers. Shortening one is rejected by
draft validation when runtime lighting cues, safe points or protected intervals
would run past the new end; trim those first. Editor transform layers that run past
the end are flagged by the timeline editor. The UI equivalent is **Scene settings →
Scene duration** in the timeline editor (or **Scene details** on the Scenes page).

The accepted [UP-01 architecture decisions](decisions/0016-lossless-hobbyist-authoring-and-runtime-handover.md)
record the accepted preservation, explicit Apply, unified Preview and monitoring
contracts. Discover current tool schemas and grants before attempting a workflow.

### Logical installation objects

Author-facing API/MCP changes must ship an equivalent discoverable UI workflow,
with shared validation, authorization and revision boundaries. API/MCP-created
objects must remain visible and editable in the UI even without geometry.
Document intentionally headless/operator-only exceptions. Verify both interfaces
before declaring a feature complete; see the
[project contribution requirements](../.github/copilot-instructions.md).

The UI equivalent for logical fixture/path authoring is **Installation > Objects**
(or **Manage installation objects** from an open show).
Discover `get_capabilities.features.installation_objects` and
`get_authoring_schema.x-installation-objects`.

- `get_installation_objects()` requires `read` and returns the token-bound show's
  revision, saved LED resources, logical segments/fixtures/paths, traversal ranges,
  reference paths, explicit geometry links and mapped member geometry IDs.
  Unplaced objects are included. Layout geometry is available only for the bound
  runtime installation.
- `edit_installation_object(action, base_revision, request)` requires `direct_edit`.
  Actions are `create`, `update`, and `delete`. Fixtures and paths are editable;
  segments are read-only so contiguous output partitions cannot be accidentally
  changed. IDs cannot be renamed by update. The same service backs authenticated
  REST `GET /api/gui/v1/projects/{project_id}/installation-objects` and
  `POST /api/gui/v1/projects/{project_id}/installation-objects/{action}`. REST
  mutations require CSRF/Origin authorization and the current `If-Match` revision.
- Names and optional `geometry_semantic_ids` are saved in show-owned
  `editor_metadata.installation_object_details`; member definitions remain in
  installation resource bindings. Layout links reference existing fixture/path
  geometry or pixel segments on the same output. Mapped member segments are also
  discovered by output and exact pixel range. Geometry is not created or mutated
  by these tools; MCP geometry editing remains disabled.
- Deleting referenced objects is rejected, as are unknown/repeated members,
  invalid precedence, missing/foreign layout links, archived shows and stale
  revisions. Unlinking/deleting definitions never deletes geometry. No operation
  publishes, applies, or sends hardware commands.

Example (use the revision and segment IDs returned by discovery):

```json
{
  "name": "edit_installation_object",
  "arguments": {
    "action": "create",
    "base_revision": 12,
    "request": {
      "resource": "roofline",
      "kind": "fixture",
      "id": "porch-outline",
      "name": "Porch outline",
      "segments": [
        {"segment": "right-eave", "reverse": false},
        {"segment": "left-eave", "reverse": true}
      ],
      "precedence": 100,
      "geometry_semantic_ids": []
    }
  }
}
```

Read back with `get_installation_objects`, inspect it in Objects, and verify
effect targeting using `preview_scene_lighting` before publishing.

### Creating scenes and adding outputs

Discover `get_capabilities.features.scene_creation`, `scene_output_creation`,
and `media_only_scenes`, plus `get_authoring_schema.x-scene-authoring`.
`get_scene_authoring_options` requires `read` and returns the token-bound
project's current revision, usable media/pixel bindings and
`importable_resources`. New bindings are discoverable only when the show is
bound to the running installation. Existing show bindings take precedence;
choosing one new resource imports only that binding.

Both writes require `direct_edit` and a current `base_revision`. For example:

```json
{
  "name": "create_scene",
  "arguments": {
    "base_revision": 7,
    "request": {
      "scene_id": "exhibit-preshow",
      "name": "Exhibit preshow",
      "duration_ms": 180000,
      "mode": "controller_timed",
      "completion_policy": "loop",
      "output": {"output_id": "roofline", "resource": "roofline.demo"}
    }
  }
}
```

Then call `add_scene_output` with `scene_id`, the returned revision as
`base_revision`, and a `request` such as
`{"output_id":"window-video","resource":"projector.demo","make_clock_source":false}`.
Resource names must come from discovery. Output/scene IDs use 1–100 ASCII
letters, numbers, dots, underscores or hyphens, beginning with a letter/number.
Output IDs, output resources, and bound media agents must be unique per scene.

New lighting contains a full-duration blackout cue. New media is unassigned;
use asset placement before publishing. Media-only scenes are valid without
dummy LED outputs. Adding outputs preserves the existing clock by default;
`make_clock_source: true` explicitly selects the new required media output and
switches to media-synchronized mode. Schema-v1 scenes are upgraded with old
output IDs `media`/`lighting`, preserving their content and metadata.

`rename_scene_output(scene_id, output_id, new_output_id, base_revision)` and
`remove_scene_output(scene_id, output_id, base_revision)` also require
`direct_edit` and the current revision; each creates one revision, is audited,
and sends no hardware commands. Rename refuses duplicate or invalid IDs and
updates `clock_source` plus the scene's `timeline_transform_layers`,
`timeline_cues` and `timeline_sync_schedule` track IDs (`media:<id>` /
`lighting:<id>`). Remove refuses the last output, the media clock of a
media-synchronized scene, and outputs still used by media layers or synchronized
cues; the error lists them. It drops the resource claim only when no other output
uses that resource. Discover these through `get_capabilities` (`features.
scene_output_rename`, `features.scene_output_removal`, `tools`) and
`get_authoring_schema` (`x-scene-authoring.rename_output_*`/`remove_output_*`).
The UI equivalent is **Rename output** / **Remove output** in the timeline
Inspector or on the Scenes page.

HTTP equivalents are read-authorized
`GET /api/gui/v1/projects/{project_id}/scene-authoring-options` and
designer-authorized, CSRF-protected `POST .../projects/{project_id}/scenes`
and `POST .../projects/{project_id}/scenes/{scene_id}/outputs`,
`POST .../scenes/{scene_id}/outputs/{output_id}/rename` (body
`{"new_output_id": "..."}`) and `DELETE .../scenes/{scene_id}/outputs/{output_id}`, using the same
request schemas and `If-Match` revision fence. Writes return the saved draft
and ETag; MCP returns project/scene/output IDs, base/new revisions and
`hardware_commands_sent: false`. Archived shows and revision conflicts cannot
be edited. Neither path publishes, Applies, configures device enrollment,
creates mapped compositions, or modifies automatic-program/visitor-response
membership.

`preview_interaction_workflow` can simulate a new media-only scene using
`starting_scene_id`. It returns the decision trace with no sampled LED frame
and an explicit explanation that media is simulated rather than streamed.

### Asset trash over MCP

`get_capabilities` reports `features.asset_trash` and an `asset_lifecycle`
block (tools, scope, `retention_days`, blocker rules). The workflow is:

1. `list_asset_metadata` (read) returns each asset's `metadata_revision`,
   `draft_usage`, and `removal_blockers`.
2. Remove the asset's live uses with the normal draft tools (for example delete
   its media layer with `update_project_draft`). Assets are blocked while the
   current draft of any show, installation projector review content, an accepted
   timing-calibration test asset, or a staged/active release uses them.
   Historical revisions and stale `editor_metadata.asset_bindings` entries do
   not block.
3. `trash_asset(asset_id, metadata_revision)` (`direct_edit`) soft-deletes the
   asset, cancels an unfinished upload, and returns `purge_after`. A blocked
   or stale call fails with the blockers or current revision; nothing changes.
4. `list_trashed_assets` (read) shows trash with `purge_after` and
   `purge_blockers`; `restore_asset(asset_id, metadata_revision)`
   (`direct_edit`) restores the same ID, revisions, and metadata.

Calls are audited (`mcp.asset.trash`/`mcp.asset.restore` plus the service
`asset.trash`/`asset.restore`) and send no hardware commands. Immediate
permanent deletion is intentionally **administrator GUI-only** (typed name
confirmation) and is not exposed over MCP. The UI equivalent is
**Assets > Move to trash** and **Assets > Trash** (Restore, Delete
permanently).

HTTP equivalents: project-reader `GET .../projects/{project_id}/assets/trash`;
designer-authorized, CSRF-protected `POST .../projects/{project_id}/assets/{asset_id}/trash`
and `.../restore`; project-administrator `POST .../assets/{asset_id}/purge` with body
`{"confirmation_name": "<exact asset name>"}`. All three writes require
`If-Match` with the asset `metadata_revision` and bump it. Errors are
`409 asset_in_use` (with `blockers`), `409 asset_metadata_conflict`,
`409 asset_trash_state`, `409 asset_metadata_locked` (archived show or trashed
asset metadata edit), `422 asset_purge_confirmation_mismatch`, and `404`.
Placing a trashed asset returns `409 asset_trashed`; publication validation
reports `asset_trashed` for references to it.

The controller purges trash older than 30 days at startup and hourly, skipping
assets that any published release still references (kept for rollback). Each
purge is audited as `asset.purge` with the released digests. Stored originals
and review artifacts are content-addressed and shared across assets, shows,
and installation photos; a file is deleted only when no asset revision or photo
references it, and files modified within the last 10 minutes are kept until a
later sweep. Exports omit trashed assets.

### Asset file replacement over MCP and HTTP

Replacing an asset's file adds a new file revision under the **same asset ID**,
so scene media outputs, timeline layers, compositions, and presets keep their
placement and switch to the new file. Drafts, previews, bakes, and the next
publication use the asset's current revision; already published and rollback
releases keep the digest they were published with. A replacement must be the
same media kind (image, video, or audio) as the current file.

`get_capabilities` reports `features.asset_replacement` and an
`asset_replacement` block (tools, scope, rules, HTTP upload path). Over MCP:

1. `list_asset_metadata` (read) returns `current_revision`,
   `replacement_status` (`uploading`, `processing`, or null),
   `replacement_error`, and up to 20 `revisions` (newest first, each with
   `revision`, `current`, `status`, filename, type, size, and `created_at`).
2. `replace_asset_from_asset(asset_id, source_asset_id, metadata_revision)`
   (`direct_edit`) makes another ready asset's current file the next revision of
   `asset_id`, reusing its processed metadata and review artifacts without
   copying bytes. The source asset is unchanged.
3. `revert_asset_revision(asset_id, revision, metadata_revision)`
   (`direct_edit`) makes an earlier successfully processed revision current.

MCP cannot transfer file bytes. Uploading an edited file uses the GUI
(**Assets > Replace file**) or HTTP:

- `POST /api/gui/v1/projects/{project_id}/assets/{asset_id}/replacements` with
  `{"original_filename", "media_type", "total_bytes", "expected_sha256"?}`
  reserves the next revision and returns an upload. Send chunks with the normal
  `PUT /api/gui/v1/assets/uploads/{upload_id}` (`Upload-Offset`). After the last
  chunk the controller scans and processes the file in the background. The
  revision becomes current only when processing succeeds. A failed or
  quarantined replacement is discarded and recorded in `replacement_error`.
  Starting a new replacement cancels an unfinished one; it is refused while a
  replacement is processing.
- `POST .../assets/{asset_id}/replace-from` with `{"source_asset_id"}` and
  `POST .../assets/{asset_id}/revert` with `{"revision"}` mirror the MCP tools.

All three writes are designer-authorized and CSRF-protected, and they require
`If-Match` with the asset `metadata_revision`. Each one bumps that revision, and
so does finishing (or discarding) a replacement upload. Errors are
`409 asset_metadata_conflict`, `409 asset_replacement_state` (busy, already
current, unprocessed, or missing revision), `409 asset_trash_state`,
`409 asset_metadata_locked` (archived show), `422 asset_replacement_rejected`
(different media kind, disallowed type, size, or quota), and `404`. Calls are
audited as `asset.replace.begin`, `asset.replace.process`, `asset.replace`,
and `asset.revert`, plus `mcp.asset.replace`/`mcp.asset.revert`. None of them
sends hardware commands. Trashing an asset cancels an unfinished replacement
upload. Earlier revisions stay stored until the asset is permanently deleted.
Exports include completed revisions but not a pending replacement.

Review artifacts (`GET /api/gui/v1/assets/{asset_id}/artifacts/{kind}`) are
served as immutable only when the request carries `?revision=` equal to the
current revision; otherwise they are `no-cache`, so browsers never keep showing
a replaced file.

### Ambient lighting over MCP

`get_capabilities` reports `features.ambient_lighting` and an
`ambient_lighting` block. Ambient lighting is stored in the draft at
`editor_metadata.ambient_lighting` (schema `AmbientLighting` in
`get_authoring_schema`) and edited with `get_project_draft` and
`update_project_draft` (`direct_edit`), the same object as **Show > Ambient
lighting** in the GUI:

```json
{
  "schema_version": 1,
  "outputs": [
    {
      "resource": "roofline.demo",
      "cycle_ms": 8000,
      "brightness_limit": 0.6,
      "cues": [
        {"id": "ambient", "primitive": "pulse", "start_ms": 0,
         "duration_ms": 8000, "color": "#FFB060", "brightness": 0.3,
         "period_ms": 4000}
      ]
    },
    {"resource": "porch", "composition_id": "ambient-shimmer", "cues": []}
  ],
  "excluded_scene_ids": ["finale-blackout"]
}
```

Each output targets a distinct `pixel_output` resource and needs `cues`
(procedural `LightingCue`s within `cycle_ms`), a `composition_id`, or both. A
composition must be a stage composition with `stage_lighting_surfaces`; its LED
bake (`create_render_job`) loops on its own duration and must be current before
publish. `excluded_scene_ids` lists existing scenes that do not inherit the
ambient (the GUI's per-scene **Inherit ambient lighting** checkbox). Invalid
resources, compositions, scenes, duplicate IDs, or cues outside the cycle are
rejected with a validation error. Stage compositions accept
`led_background: "ambient" | "background"` to choose whether undrawn LEDs show
the ambient (four-channel coverage bake) or the background color. Changing it
requires a re-bake. Opaque media covers LEDs even where it is black; set a
media layer's `alpha_interpretation` to `black_transparent` to derive coverage
from its brightest channel so black drops out and the layer blends over the
ambient like a screen blend (re-bake afterward).

`preview_scene_lighting` includes the ambient beneath scene outputs and reports
LED resources the scene doesn't light as `ambient:<resource>` outputs (the safe
state for excluded scenes). Live, the ambient runs on the controller clock from
show load and does not restart when a scene loops or changes. Pass the optional
`ambient_elapsed_ms` (time since the ambient started, for example
`loops * duration_ms + timestamp_ms`) to evaluate the ambient there; the scene's
own lighting still uses `timestamp_ms`. It defaults to `timestamp_ms`, which
restarts the ambient with the scene, and the response's `limitations` say so.
The response echoes `ambient_elapsed_ms`, and `ambient:<resource>` outputs
report their loop position as `output_position_ms` together with `cycle_ms`.
The Scene workspace preview sends the same value so preview loops stay
continuous.

An ambient composition that is also placed on an inheriting scene's timeline
plays on scene time and restarts with that scene. `get_show_plan` lists it in
`warnings`, and `get_lighting_bake_status` reports those scenes in
`ambient_scene_timeline_ids`. The UI shows the same warning on the scene's LED
bake chip and in the Show plan. Compilation writes the ambient to `looks.json`
(`baseline`, `baseline_sequence`, `excluded_scene_ids`), merged per resource
over looks preserved from the controller. None of this sends hardware commands.

`preview_ambient_lighting(timestamp_ms, source_revision=None,
proposed_draft=None, resource=None, pixel_offset=0, pixel_limit=1000)`
(`simulation`) evaluates the ambient alone, without any scene, at a time since
its loop started; `timestamp_ms` wraps on each output's `cycle_ms`, and each
output reports `cycle_ms` and `output_position_ms`. Saved revisions include
current ambient composition bakes and list unbaked ones in `limitations`;
`proposed_draft` previews an isolated unsaved candidate. Browser clients use
authenticated, CSRF-protected
`POST /api/gui/v1/projects/{project_id}/ambient-lighting-preview` with the same
`AmbientLightingPreviewRequest` validation. It is discovered as
`get_capabilities.tools.ambient_lighting_preview` and
`ambient_lighting.preview_tool`.

Every cue in `outputs[].cues` is listed, inspectable and editable in **Show >
Ambient lighting**: the effect list shows each cue's target and span on the
loop, and the inspector edits the same `LightingCue` fields as scene effects.
Cues must fit inside `cycle_ms`; the GUI blocks saving until they do, matching
draft validation.

### Scene-native LED preview

`get_capabilities.scene_lighting_preview` discovers `preview_scene_lighting`
and its limits. It requires `simulation`, not direct-edit or live-operation
permission. Browser clients use authenticated, CSRF-protected
`POST /api/gui/v1/projects/{project_id}/scene-lighting-preview`.

Both surfaces use the same `SceneLightingPreviewRequest` validation and
production `evaluate_lighting_output` function as the controller. For example:

```json
{
  "scene_id": "baseline-timeline",
  "timestamp_ms": 1000,
  "source_revision": 3,
  "resource": "roofline.demo",
  "pixel_offset": 0,
  "pixel_limit": 120
}
```

The immutable source revision is explicit; omitting it over MCP selects the
current saved revision. Optional `proposed_draft` evaluates unsaved changes
without saving or merging them and cannot change installation binding.
The result reports source revision, scene time, `unsaved`, output/resource
identity, calibrated `output_position_ms`, sampled RGB colors, next offset,
limitations, and `hardware_commands_sent: false`.

The browser photo visualization is a separate display treatment: Installation's
1000 x 600 letterboxed coordinate space, linear-to-sRGB visibility, screen blend,
glow and transparent off LEDs. API/MCP sampled RGB, simulation evaluation,
brightness limits and device output are **not** changed by this treatment.
Clients should not bake display compensation into authored brightness or apply
it again to device commands. Raw RGB remains available in the browser's
collapsed LED output diagnostics. No new MCP grant/schema or output transform
is needed for this presentation-only fix.

Compilation/validation occurs in cleaned-up temporary storage. No controller
instance, media client, broker, hardware adapter, render job or live transport
is opened. Preview cannot publish or activate. Invalid candidates/dependencies
fail explicitly; they are not rendered as partial success. For the saved
revision, LED compositions (`lighting_surface`, or `stage` with
`stage_lighting_surfaces`) are compiled in from their current bakes exactly as
Apply would. Each current bake is used even when another LED composition still
needs one. Unsaved candidates and compositions without a current bake are
omitted, and the top-level `limitations` list names them instead of failing the
preview; `preview_composition` still previews an individual composition before
baking. Legacy effects retain their existing runtime behavior;
an unapplied legacy brightness limit is explicitly reported.

Limits: at most 4096 returned samples, 65536 installation pixels, 2000000 estimated
pixel-index work, and two concurrent evaluations per controller process. Busy
REST evaluation returns 429; MCP returns a tool error. Select a resource and
page pixels rather than assume a truncated frame is complete. Large/complex
workflows should use advanced/offline rendering.
The browser samples scene frames up to four times/second, serializes slow
requests to prevent overlap/starvation, and labels the actual evaluated time.
This cadence does not change the runtime renderer or prove real-time media/
hardware synchronization. Omitting `resource` evaluates every lighting output of
the scene; the browser does this by default and splits the 4096-sample budget
evenly across outputs.

### Repeat counts and seamless scene changes

`get_capabilities` reports `features.media_repeat_count`,
`features.seamless_scene_transitions` and a `scene_transitions` block. Author
`scenes[].media[].repeat_count` (1-10000; not with `end_behavior: "loop"`)
through `update_project_draft` or proposals. The authoring schema documents it
under `MediaOutputDefinition`; the UI equivalent is **Plays** in the timeline
inspector. Seamless changes are automatic, with no authoring field. MCP clients
observe them through `get_runtime_status`: `status.next_scene` (`scene_id`,
`preloaded_outputs`, `automatic_cut`) once the next scene is planned 3 seconds
before the end, and `status.seamless_transitions`. `preview_scene_media` reports
`repeat_count` and the 1-based `play_number` for each output. See
[scene lifecycle](scene-schema.md#seamless-scene-changes) and the device
protocol for `queue`/`advance` semantics.

### Scene media preview

`get_capabilities.scene_media_preview` discovers `preview_scene_media`
(`simulation` scope). Browser clients use authenticated, CSRF-protected
`POST /api/gui/v1/projects/{project_id}/scene-media-preview` with the shared
`SceneMediaPreviewRequest`: `source_revision`, `scene_id`, `timestamp_ms`,
optional `output_id`, `include_frames` (default true), `max_frame_width`
(64-1920, default 640) and optional unsaved `proposed_draft`. Use the same
`timestamp_ms` as `preview_scene_lighting` to see LEDs and media together.

For each media output it mirrors controller media selection: a root or instance
composition of the scene that targets the output (a `projector_native`
composition whose `target_id` is the resource, or a `stage` composition that
includes the projector) takes precedence; otherwise the assigned asset plays.
Media position is scene time minus `start_delay_ms`, then `end_behavior`
(`loop`, `hold_last_frame`, `stop`) applies after the media duration. Each output
reports `source`, `state` (`unassigned`, `waiting`, `playing`, `looping`,
`holding`, `stopped`, `error`), `media_position_ms`, `media_duration_ms`, active
`audio` sources with their source position, gain and mute, and a PNG `frame`
rendered by the bake compositor (`preview_projector_composition` /
`preview_stage_composition_member`, or the asset's own frame). Several
compositions targeting one output return `state: "error"`, matching Apply. REST
embeds frames as base64; MCP strips the base64 from structured content and
attaches each frame as an image (`frame.attached_image_index`). Payloads over the
MCP budget fail; request one `output_id` or a smaller `max_frame_width`.

Nothing is sent to devices, and frames are stills: device decode timing, codecs
and projector calibration on site can differ. Two evaluations run concurrently
per controller process; busy REST calls return 429
(`scene_media_preview_busy`), invalid requests 422
(`scene_media_preview_invalid`). The UI equivalent is **Projectors and media** in
**Scenes** scene preview and **Preview > Scene preview**, which can also play
audio sources through asset review proxies with approximate browser timing.

For geometry-aware LED authoring, MCP also provides:

- `get_spatial_layout`: project-bound named surfaces, local/stage transforms,
  LED coverage, projector extents/native dimensions/crops/meshes, zones,
  calibration revisions, semantic landmarks, existing media/lighting
  compositions/layers, and a layout digest. Stage bounds include projector
  coverage as well as LED and semantic geometry. Logical samples are opt-in and
  paginated with `pixel_offset` and `pixel_limit` (maximum 500); normal
  authoring context omits physical indices. `include_photo_geometry=true` adds
  installation-photo references and their calibrated geometry. Installation
  calibration is read-only through MCP; edit it in the GUI.
- `get_spatial_effect_catalog`: authoritative parameter schemas. The initial
  procedural effect is a true expanding 2D `ripple`, distinct from the
  existing ordered-strip ripple.
- `propose_spatial_effect`: adds a typed ripple to 1-16 mapped lighting
  compositions in an isolated proposal. Supply the base revision and
  `expected_layout_digest`; stale geometry is rejected. An effect is placed
  above existing author layers by precedence. Use stage coordinates to share
  one origin and propagation speed across surfaces. Coordinates and speeds
  use installation units, which must not be assumed to be metres.
- `preview_spatial_proposal`: requires `simulation`, evaluates the isolated
  candidate at an explicit timestamp, and returns diagnostics, samples, and a
  logical LED PNG image. It sends no hardware commands, rejects stale
  revisions/layouts, and does not merge or activate anything.

For example, resolve "front of house" and "front door" from the spatial context,
select the corresponding lighting compositions, propose a stage-space ripple,
and preview representative timestamps before normal validation, human
approval and merge. Ambiguous origins, missing geometry, and units require
clarification rather than invented coordinates. The existing
`propose_project_revision` tool can also propose image/video/GIF transform
layers; use ready project asset IDs and the normal transform schema.

For installation-wide image/video/GIF overlays:

1. Read the layout, draft, schema, and `list_asset_metadata`. Asset metadata
   includes available source dimensions and duration, but not storage paths.
   It also lists each asset's `metadata_revision`, `draft_usage`, and
   `removal_blockers`; see [asset trash over MCP](#asset-trash-over-mcp).
2. Author media layers in a shared-stage composition sized to the layout's
   `stage.width`/`stage.height`, select mapped LED semantic IDs in
   `stage_lighting_surfaces`, optionally restrict projectors with
   `stage_projector_resources` (omit for all, or `[]` for a lights-only
   overlay that bakes LED sequences without projector video or audio), bind
   timeline tracks, and attach
   the composition to a scene. To place media across separate devices, set the
   layer's `corner_pin` to four convex stage points (TL, TR, BR, BL); position,
   scale, rotation and anchor keyframes are then ignored while opacity and crop
   still animate. A layer `mask` (`points`, `feather` in stage units, `invert`)
   limits where it appears. Corner pins and masks are static per layer. `get_timeline_range` accepts scene **or composition**
   IDs and returns overlapping media/lighting layers as well as cues and markers.
3. Use the proposal/approval/merge workflow, or explicitly granted `direct_edit`.
4. Call `preview_composition(composition_id, timestamp_ms)`. For a stage this
   returns the common canvas plus LED diagnostics; pass `resource` for a
   projector's warped output. Lighting-surface and projector-native compositions
   are also supported. An optional `proposal_id` previews an isolated candidate
   without merging it. Previews require `simulation`, never send hardware
   commands, and reject stale proposals/installation digests. Images are
   thumbnails up to 512 pixels per dimension and 64 KiB PNG; source canvases are
   limited to 2 megapixels for MCP preview, and warped projector previews require
   an output set of at most 8 megapixels. Diagnostic samples use `pixel_offset`
   and `pixel_limit` (default 100, maximum 500). The complete result must fit the
   256 KiB MCP payload budget.
5. Call `create_render_job(base_revision, composition_id)` to bake the saved
   revision. `composition_id` may also be a whole-scene output bake ID,
   `scene-output:<scene_id>:<output_id>` (as listed by `get_bake_requirements`):
   it mixes the audio layers stored under
   `editor_metadata.timeline_transform_layers[<scene_id>]` with
   `track_id: "media:<output_id>"` into a remux of the output's base video
   (video packets copied unchanged, AAC stereo audio). A `loop` output is copied
   in whole loops covering the scene duration and its last audio layer
   (`scene_output.video_loops` in the manifest); for other end behaviors, audio
   past the end of the base media fails the job. Visual layers on such a
   track make the requirement `unavailable` and preflight reports
   `scene_output_layers_unsupported`; place them in a composition instead. This requires **either** `direct_edit` **or** `publish`; `read`,
   `draft_edit`, and `simulation` alone cannot queue or cancel jobs. Existing
   storage/calibration/projector-rehearsal checks still apply.
6. Poll `get_render_job(job_id)` for state, progress, errors, and artifact ID.
   `include_artifact=true` returns the completed manifest, including matching
   projector members and LED sequences. `get_render_jobs(offset, limit,
   composition_id)` lists project jobs (maximum 100 per page).
   `cancel_render_job(job_id)` cancels queued/running jobs with the same write
   grants as queueing. Cross-project job IDs are rejected.
   `get_lighting_bake_status(revision=None, scene_id=None)` (`read`) lists every
   LED composition for a saved revision with `state` (`current`, `queued`,
   `running`, `failed`, `missing`, or `unavailable` when inputs such as the
   installation mapping cannot be read), `scene_ids` (including scenes that
   inherit an ambient composition), `baked_revision`, and `bake_required`. The
   GUI shows the same status as the scene **LED bake** chip, and
   `GET /api/gui/v1/projects/{project_id}/lighting-bakes?revision=` returns it
   to project readers.

   LED-only bakes (`lighting_surface` compositions and `stage` compositions with
   `stage_projector_resources: []`) are keyed on the composition, its media and
   lighting author layers, asset checksums, and the installation mapping, not
   on the revision. Saving edits that change none of these keeps the bake
   current, and queueing one returns the existing artifact immediately.
   Projector and mixed bakes stay bound to the revision they were made for. A
   draft save identical to the head revision creates no new revision.
7. Publish/stage/activate using the existing separately granted scopes below.
   Offline bakes do not merge proposals, approve releases, deploy, or activate
   hardware. Controller package handover/restart and physical synchronization
   rehearsal remain necessary.

Full runtime simulation/publication needs current bakes. Use image previews
for unmerged proposals. Spatial context remains bounded to 200 KiB and 100
combined LED/projector surfaces; oversized detail requests fail explicitly.

1. Use `get_project_draft` to read the complete working draft and revision,
   and `get_authoring_schema` for its authoritative JSON Schema.
2. With `direct_edit`, call `update_project_draft(base_revision, draft)` with the
   **complete** edited draft. It uses the same schema and transactional revision
   checks as GUI draft saves, creates a new immutable revision, and returns its
   revision and changed paths. Invalid, unchanged, or stale edits are rejected.
   On a conflict, reread and reconcile; do not blindly retry against a newer
   revision. Draft saves do not themselves compile, publish, or affect live output.
3. With `publish_prepare`, `publish`, or `activate`, inspect
   `get_publication_status` for release metadata, validation, and package states.
4. With `publish`, call `update_revision_release` for an explicit revision,
   providing release metadata and `approval_state="approved"` when ready.
   Then call `publish_revision(revision)`. Existing structural, semantic, asset,
   capability, bake, and cache validation still applies; a scope cannot bypass it.
5. With `publish`, call `stage_published_package(package_id)`. With the separate
   `activate` scope, call `activate_published_package(package_id)` to activate
   staged artifacts. Cross-project package IDs are rejected, and
   `simulation_mode=true` refuses activation.

Activation can change physical device output. Follow the same supervised
physical-rehearsal and safety procedures as GUI activation. It does **not**
replace the running controller scene catalog, start a scene, or restart the
controller. Catalog handover still requires an administrator to update
`CUEVERGE_SHOW_PACKAGE` and recreate/restart the controller.

The proposal/validation/simulation/human-merge workflow remains available with
`draft_edit`. A direct edit can make an outstanding proposal stale; that
proposal must still pass its original human review and revision-conflict checks.
Direct editing and release approval never implicitly approve or merge a proposal.

The server enforces a 256 KiB request limit, 60 tool calls per token per minute,
four concurrent calls per token, 20 active proposals per project, 100 returned
assets, five-minute timeline windows, 200 simulation steps, and ten minutes of
simulated time. Audit history records token creation/revocation, each requested
tool with an argument digest (not raw potentially sensitive content), proposal
diffs and validation, human approval/rejection, merge, and final disposition.
During an incident, revoke the token, disable the owning account if necessary,
review `mcp.tool.call`, `mcp.draft.update`, `mcp.revision.release.update`,
`mcp.package.*`, and `mcp.proposal.*` audit events, and inspect edited revisions,
release approvals, deployed packages, and every non-final proposal before
restoring access.

## Operating modes

Cueverge starts in `automatic` mode. The engine supports:

- `automatic`: external observations may drive audience, encounter, and
  opportunity behavior.
- `disabled`: stops the active scene and rejects automatic observations.
- `maintenance`: stops the active scene and rejects automatic observations
  while leaving direct operator scene control available.
- `blackout`: performs normal stop handling, emits an explicit all-black pixel
  frame, and rejects automatic observations.

The authenticated GUI uses `POST /api/gui/v1/operate/actions` with an `action`
of `automatic`, `maintenance`, or `blackout`; the current GUI does not expose
disabled mode. Its `reset` action starts the encounter ambient scene, or the
program initial scene when no encounter is configured. Reset and direct
engine scene starts do not silently change the selected operating mode.
Browser mutations require the authenticated session, allowed origin, CSRF
header, and live-output authorization. The old `/api/v1/...` endpoints are
removed, not aliases for these actions.

Observation ingress may remain connected outside automatic mode. The runtime
rejects those observations before they can alter presence, opportunities,
encounters, or scene selection.

## Status and health

The authenticated `GET /api/gui/v1/runtime/status` response wraps runtime
facts in `status`, alongside `observed_at`. Runtime facts include:

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

## Deployment dependency inventory

The machine-readable source for controller image construction and preflight is
[`deploy/controller-native-dependencies.json`](../deploy/controller-native-dependencies.json).
Update that manifest in the same change whenever controller code adds a native
executable, codec, filter, system library, build tool, or persistent path. This
section explains the policy; the Dockerfile and image-preflight checks
consume or validate the manifest rather than maintaining an independent list.

The controller Dockerfile derives its required and optional Debian package
specifications from this inventory. The startup preflight reads the same file
and fails closed when required executables, Python media libraries, media
encoders, decoders, filters, or container formats, or required ClamAV
definitions are unavailable. Keep the inventory synchronized with runtime
media operations rather than adding package or capability lists directly to
Docker or shell files:

| Tool | Image role | Requirement |
| --- | --- | --- |
| Python 3.12+ and the locked `pyproject.toml`/`uv.lock` environment | Controller runtime | Required |
| FFmpeg libraries bundled in the locked PyAV (`av`) wheel | Asset metadata, loudness analysis, thumbnails, review proxies, waveforms, preview frames, and render jobs | Required; supplied by the Python environment, so no distribution `ffmpeg` package or `ffmpeg`/`ffprobe` executables are installed |
| `clamscan` and current ClamAV definitions | Uploaded-asset malware scanning | Optional, but production deployments that require malware scanning must install both and arrange definition updates |
| Node.js and npm | Building the React/Vite application | Build-stage only; do not retain them in the final controller image |
| CA certificates | TLS connections to external services and devices | Required in the runtime image |

Base images are digest-pinned and required native package versions are pinned
in the inventory. Update each package version and its base-image digest
together after rebuilding and running the dependency inventory tests. Startup
reports the PyAV-bundled FFmpeg version and configured scanner versions and
verifies the declared encoders, decoders, filters, and container formats used
by asset processing and rendering. Upgrading `av` in `uv.lock` changes the
bundled FFmpeg; rerun the preflight and render reproducibility tests when doing
so.

Asset processing decodes untrusted uploads in a child interpreter
(`python -m cueverge.gui.media_tools`) with per-operation timeouts, so a
malformed upload cannot crash or stall the controller process. Render encoding
and artifact preview frames, which only handle controller-generated media, run
in-process.
The Dockerfile must also create writable persistent mounts for GUI data,
original assets, derived artifacts, logs, and other configured state; these
must not live only in the container layer.

`mpv` is a media-agent dependency, not a headless controller-image dependency.
It needs access to the host display, audio devices, GPU/decoder stack, and IPC,
so install and validate it on each Windows or Linux media host as described in
the media-agent setup guides. If controller-side rendering later introduces
fonts, color profiles, GPU runtimes, or additional codecs and filters, add them
to this inventory and to reproducibility/preflight checks before relying on
them.

### Controller container

The repository Dockerfile builds the React application and locked Python
environment in separate stages. The final image runs as an unprivileged user,
contains the locked Python environment (including PyAV's bundled FFmpeg
libraries) and CA certificates, exposes the existing
liveness/readiness endpoints as its container healthcheck, and stores mutable
GUI state under `/var/lib/cueverge`. It includes the synthetic demo show as a
safe default; mount a private show package read-only and set
`CUEVERGE_SHOW_PACKAGE` to use another package.

Build and start the controller-only default:

```bash
docker compose build
docker compose up -d
docker compose ps
```

The GUI remains disabled unless its required authentication and origin
settings are supplied. Generate the administrator hash outside the container,
store it in a private Compose environment file or secret-management system,
and set at least:

```dotenv
CUEVERGE_GUI_ENABLED=true
CUEVERGE_GUI_ADMIN_PASSWORD_HASH=scrypt$...
CUEVERGE_GUI_ALLOWED_ORIGINS=https://cueverge.example.test
CUEVERGE_GUI_SECURE_COOKIES=true
```

Terminate TLS at a trusted reverse proxy for non-localhost browser access.
Do not publish the mTLS device port unless device enrollment and listener
identity settings are fully configured.

ClamAV is deliberately an image variant because its package and definitions
materially increase image and update size:

```bash
CUEVERGE_INSTALL_CLAMAV=true docker compose build
```

Arrange regular signature updates in the deployment platform and persist or
inject the definition directory. Set `CUEVERGE_CLAMAV_REQUIRED=true` only after
definitions are available; container preflight then fails closed if `clamscan`
or its definitions are missing. Without that setting, scanner absence remains
visible in asset processing as documented above.

## Deleting unwanted scenes

`delete_scene(scene_id, base_revision)` requires a project-bound `direct_edit`
token and the current saved revision. For example:

```json
{"scene_id": "unwanted-example", "base_revision": 7}
```

Use `get_project_draft` first to identify exact IDs; never select scenes for
deletion just because their names look synthetic. The tool refuses the last
scene and incoming fallback/completion, program, visitor-response, and encounter
references. Update those references explicitly before retrying. Stale revisions
fail without overwriting newer edits. Archived shows cannot be edited.

Success returns `scene_id`, `base_revision`, the new `revision`,
`requires_human_approval: false`, and `hardware_commands_sent: false`. This is
not a simulation or publication: it saves a working revision only. Scene-owned
timeline/mapping metadata is removed; reusable compositions, presets, media
assets and historical revisions are retained. `get_authoring_schema` describes
the request under `x-scene-deletion`; capability discovery advertises
`scene_deletion` and the `delete_scene` tool.

The browser uses `DELETE /api/gui/v1/projects/{project_id}/scenes/{scene_id}`
with its normal author session, CSRF protection and `If-Match` revision header.
It returns the updated draft and ETag, or explicit validation/conflict errors.
Publication and apply remain separate, authorized operations.

## Deleting compositions

Use `get_composition_usage(composition_id)` (read scope) to list the scenes,
parent compositions, scene instances and nested-composition media layers that
use a composition. Then, with a project-bound `direct_edit` token and the
current saved revision:

- `delete_composition(composition_id, base_revision)` deletes the composition
  and removes it from every use listed above. Its own layers, tracks, markers,
  cues and lighting author layers are discarded, and its bakes are no longer
  published. Media assets and historical revisions are kept.
- `remove_composition_from_scene(scene_id, composition_id, base_revision)`
  takes it out of one scene (root entry, instances and nested layers in that
  scene) and keeps the composition for reuse. It fails when the scene does not
  use the composition directly.

Both validate the resulting draft like any other save, fail on stale
revisions without overwriting newer edits, and return the new `revision`,
`requires_human_approval: false` and `hardware_commands_sent: false`. They save
a working revision only; publication and apply stay separate. Capability
discovery advertises `composition_deletion`, and `get_authoring_schema`
describes the tools under `x-composition-deletion`. The timeline editor offers
the same actions: **Delete…** with a confirmation that lists every use, and
**Remove from this scene**.

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
        "camera": "exhibit-camera",
        "zone": "interaction-area",
    },
)
await simulation.advance(30 * 60 * 1000)
status = simulation.runtime.status()
await simulation.shutdown()
```

Simulation results are available through `scene_history`, the in-memory
lighting frames, recorded agent commands, and normal runtime status.

## Publication and client cache contract

Publishing snapshots an approved project revision into an immutable,
content-addressed directory under `/var/lib/cueverge/published`. Its manifest
records the source revision, revision name and summary, creator, timestamp,
compiled package digest, per-file schema version/size/checksum, referenced
asset checksums, render artifact identities, required capabilities, and
validation results. Publication never changes the active show.

Deployment is deliberately split into four attributable operations:

1. **Publish** creates the immutable controller package.
2. **Stage** transfers only missing checksummed render members. Clients resume
   partial range downloads, verify size and SHA-256, and atomically rename
   completed temporary files.
3. **Activate** is allowed only after staging, activates rendered artifacts
   with required client acknowledgements, and updates the active publication
   record.
4. **Deactivate/roll back** retains the previous package and reactivates a
   previously staged or inactive package.

These operations do not reload the running controller's scene catalog.
Deactivation updates publication state and does not stop physical playback.
For actual scene-logic handover/rollback, use the separate managed Apply workflow
described above; the advanced artifact buttons alone are insufficient. See
[release handover](service-administration.md#release-handover-and-upgrades).

Every stage command carries the complete protected digest set for the
candidate, active, and retained rollback packages. The media agent also
protects the currently playing digest. When capacity is insufficient it
evicts only other artifacts, oldest access first; it never deletes partial
metadata, the active file, a rollback file, or a candidate file. If safe
reclamation cannot satisfy the manifest, staging fails explicitly and the
active package remains unchanged. Inventory distinguishes verified cache
members, active transfers, pinning/in-use state, free capacity, and faults.
Operators interact only with package/cache identities and never arbitrary
filesystem paths.

## Backup, restore, and disaster recovery

Back up the complete `/var/lib/cueverge` volume as one consistency unit. It
contains the configured GUI data root, normally `/var/lib/cueverge/gui` in
the supplied image. Paths below are relative to that GUI data root:

- `gui.sqlite3` plus its SQLite WAL/SHM files: projects, revisions,
  approvals, device identities, assignments, render metadata, publication
  state, and audit history.
- `assets/`: original uploads, installation reference images, and generated
  review artifacts.
- `renders/`: immutable baked projector artifacts and LED frame sequences.
- `compiled/` and `published/`: reproducible compiled and published packages.
- `device-ca/`: the controller device CA and signing key material.
- `simulation-scenarios/`: saved deterministic rehearsal inputs.

Use a storage snapshot or stop the controller before a file-level backup.
Never copy only the SQLite main file while the controller is writing. Encrypt
backups containing the device CA and restrict them as production credentials.
Retain at least one known-good backup outside the Docker host.

Restore procedure:

1. Stop the controller and preserve the failed volume for forensic recovery.
2. Restore the entire saved volume into a fresh, empty volume with ownership
   matching the unprivileged `cueverge` user.
3. Start the same application/image version first so repository migrations run
   in their original order.
4. Verify `/health/ready`, project revision history, published manifest
   checksums, asset availability, device CA presence, and audit continuity.
5. Reconnect clients and wait for fresh cache inventories. Do not activate
   until assignments, capabilities, free space, pinning support, checksums, and
   current bake validation all pass.
6. Rehearse the retained rollback package, then stage and activate explicitly.

If the device CA is lost, enrolled device identities cannot be reconstructed
from project data; provision a new CA and re-enroll every device. If derived
artifacts are lost but originals and revision history survive, rerun bakes and
publish a new package rather than manufacturing files under an old digest.
