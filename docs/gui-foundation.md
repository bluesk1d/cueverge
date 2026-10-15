# GUI foundation gap analysis

This analysis covers the first GUI-00 implementation slice. The milestone
scope in `gui-roadmap.md` remains authoritative; `gui-implementation-spec.md`
supplies the detailed product and engineering constraints.

## Current capabilities and gaps

The existing controller already exposes runtime status and control endpoints,
loads versioned declarative show packages, runs deterministic simulation, and
keeps media time authoritative at the media agent. It does not provide browser
authentication, authorization, durable authoring state, project revisions,
package publication, enrollment, managed assets, or a frontend.

The first slice therefore adds an authoring boundary without changing:

- `/api/v1/*`, the agent WebSocket, or protocol version 1;
- scene, installation, program, encounter, or opportunity schemas;
- controller/agent timing and reconciliation;
- hardware adapter defaults or controller operation semantics.

## Selected architecture

### Frontend

Use React, TypeScript, and Vite in the independent `web/` workspace. The
frontend consumes versioned HTTP contracts and does not import Python engine
models or implement lighting, director, or media-clock evaluation. Vite serves
the development application and proxies `/api`; a later deployment slice may
serve immutable build output from the controller or a separate static server.

The browser support target is the current and previous major releases of
Chrome, Edge, Firefox, and desktop Safari, plus current iOS Safari. Required
layout checks are 2560×1440, 1440×900, 820×1180, and 390×844 with no horizontal
overflow. The accessibility target is WCAG 2.2 AA for contrast, semantics,
labels, focus visibility, keyboard reachability, status text that does not
depend on color, and reduced-motion behavior.

The source locale is English (`en`). User-visible dates, times, numbers, byte
counts, and durations must use `Intl`/locale-aware formatting rather than
locale-specific parsing or concatenation. Future translation catalogs use
BCP-47 locale identifiers and English fallback. Full translation extraction is
deferred until editable product vocabulary stabilizes, but new APIs and stored
documents must not persist localized display strings as authoritative values.

The accepted complete browser stack is:

| Concern | Technology and boundary |
|---|---|
| UI primitives | shadcn/ui and Radix UI for ordinary accessible controls |
| Styling | Tailwind CSS expressed through Cueverge semantic design tokens |
| Transient state | Zustand for selection, tools, viewport, and local interaction |
| Server state | TanStack Query for API state, mutations, and event reconciliation |
| API contracts | FastAPI OpenAPI with generated TypeScript types |
| Forms | React Hook Form and Zod for interaction and immediate feedback |
| Timeline | Custom DOM, SVG, and Canvas hybrid with optional Web Workers |
| Mapper | Konva/react-konva behind a replaceable view adapter |
| Tests | Vitest, React Testing Library, Playwright, axe-core, and MSW |

Zustand is not durable project storage. Zod is not publication validation.
Konva nodes are not persisted geometry. Browser timeline and mapper code may
perform view transforms and editing calculations but never implements
authoritative lighting, director, media-clock, or hardware-output semantics.

### Backend API and security

Add an opt-in `/api/gui/v1` router, isolated under `cueverge.gui`. The
unauthenticated proof-of-concept runtime routes have been removed; browser
status and operations use only the secured management API.

The first identity is a configured bootstrap administrator. Passwords use
scrypt hashes supplied through private configuration. Successful login creates
an opaque, expiring server-side session whose token is stored only as a digest.
The browser receives an HttpOnly, SameSite=Strict session cookie and a separate
CSRF token. State-changing requests require both an allowed `Origin` and the
matching CSRF header. Roles are enforced by dependencies in the server, not by
navigation visibility. Login attempts are rate limited and authentication and
authoring mutations are audited.

The original unauthenticated agent protocol was a proof of concept and has
been removed.

### Durable projects and revisions

Use SQLite with foreign keys, WAL journaling, explicit migrations, and atomic
transactions. A project has an immutable sequence of JSON draft revisions and
a monotonic head revision. Updates require `If-Match`; a stale writer receives
a typed conflict containing the requested and current revisions and changed
top-level paths. No last-write-wins update is permitted.

The schema deliberately stores the small editor draft independently from live
show files. Editor metadata is allowed in the draft but is never emitted into
runtime documents.

Autosave uses the ordinary `PUT` draft transaction after a bounded client
debounce and only when content changed. It sends the last observed head in
`If-Match`; a conflict pauses autosave and requires resolution. Undo and redo
never move or delete the head: restoring any historical revision through the
restore endpoint copies that content into a new immutable head revision with
transaction kind `restore`, source revision in audit context, and the same
optimistic concurrency check. Redo restores the revision that contained the
pre-undo content. Crash recovery reloads the durable head and revision list;
the browser may retain unsent local text separately, but it cannot claim that
text was saved until the server returns the new revision and ETag.

GUI-00 intentionally starts with the standard-library SQLite API. Before
GUI-02 expands the schema, migrate the repository implementation to SQLAlchemy
2.x typed models with Alembic as the authoritative database migration history.
Keep Pydantic API contracts independent from ORM models. SQLite remains the
initial deployment database; PostgreSQL is not required without measured need.

### Compilation boundary

The GUI compiler serializes the supported draft fields into a temporary show
package, validates that package through `load_show_package`, computes a stable
digest, and atomically promotes it into managed immutable storage. Compilation
does not activate the result, mutate the active package, contact an agent, or
open a hardware adapter.

Draft schema version 1 intentionally mirrors only already-supported runtime
documents: installation schema v1 and scene schema v1/v2. Geometry, arbitrary
media tracks, enrollment, assets, deployment, and publication need separate
versioned contracts in later milestones.

### Live state and background processing

Use authenticated HTTP snapshots plus a bounded, versioned WebSocket event
stream for live GUI status. Events carry sequence, observation time, source,
and typed payloads. Sequence gaps or reconnects trigger a fresh HTTP snapshot.
The GUI status stream is distinct from the media-agent protocol and is not a
media clock.

Asset analysis, proxies, waveforms, long simulations, baking, and staging use
a durable bounded job service beginning in GUI-02. Start with a SQLite-backed
worker rather than adding Redis or Celery prematurely.

Store binary assets and generated artifacts in configured content-addressed
filesystem roots. SQLite stores metadata and references. Writes use temporary
storage, digest verification, and atomic promotion; active and rollback
artifacts are pinned against cleanup.

### Target stack summary

| Layer | Accepted technology |
|---|---|
| Frontend | React 19 and TypeScript |
| Build | Vite static production assets |
| Components | shadcn/ui and Radix UI |
| Styling | Tailwind CSS with semantic tokens |
| Application state | Zustand, transient state only |
| Server state | TanStack Query |
| Timeline | Custom DOM/SVG/Canvas hybrid |
| Spatial mapper | Konva initially, behind an abstraction |
| Forms | React Hook Form and Zod |
| Backend | Existing FastAPI with isolated GUI services |
| API types | OpenAPI-generated TypeScript contracts |
| Database | SQLite, SQLAlchemy 2.x, and Alembic |
| Binary storage | Content-addressed filesystem |
| Background work | Bounded durable SQLite-backed worker initially |
| Live status | HTTP snapshots plus authenticated sequenced WebSocket events |
| Testing | Vitest, React Testing Library, Playwright, axe-core, and MSW |
| Production edge | TLS reverse proxy where appropriate |

## Initial navigation and read-only topology

The first shell contains:

- **Overview**: truthful active-package and controller-status summary;
- **Installation**: authenticated read-only topology plus active-package
  logical output resources;
- **Shows**: durable authoring-project summaries and head revisions;
- **Operate**: authenticated controller status and role-gated show controls;
- **Settings**: explicit unavailable state until editable controller settings
  have versioned contracts.

Operator and administrator roles may invoke typed start, pause, resume, seek,
stop, reset, automatic, maintenance, and blackout actions. Mutations require
CSRF and allowed-origin validation, record success or failure audit events,
and use confirmations appropriate to impact. Start and reset remain disabled
while required assigned media clients are unavailable. Blackout is described
as the configured playback stop and lighting safe-state action, not as a
latched hardware guarantee.

The unauthenticated proof-of-concept `/api/v1` HTTP surface has been removed;
runtime status and operations are available only through authenticated GUI
routes.

The Operate workspace also renders the complete authoritative runtime
diagnostic snapshot: queued scenes, playback state and time, per-media-output
connection/position/drift/pending commands, active lighting routes and errors,
command/heartbeat/render counters, director eligibility, encounter state,
audience state, and opportunity availability. Null or inactive runtime fields
remain visibly unavailable or not configured rather than being inferred.

The Overview recent-activity panel reads a bounded, authenticated projection of
the durable audit log. It includes device connection changes, operations,
enrollment, lifecycle, assignment, rotation, configuration, and project
events, but omits authorization context and routine successful-login noise.
The full administrator audit endpoint remains separate.

Pairing-token creation returns the explicitly configured public mTLS transport
URL. The browser shows a live expiry countdown, reviewed-capability reminder,
copyable enrollment command, transport setting, and private-key boundary. The
device still generates its key and submits the token at a hidden local prompt;
the browser never receives device private-key material.

Maintenance simulation provides bounded 5–30 second alignment-grid and
pixel-index-chase previews for configured resources. The backend requires
maintenance mode and idle playback, audits the session, and returns a typed
simulation-only contract. The browser labels the preview as simulation and no
projector command or DDP frame is emitted.

Controller status retains the 50 most recent command errors, timeouts, and
disconnect-abandoned requests in memory. Operate displays agent, command,
error code, detail, age, and error-specific remediation. Topology supplies a
human-readable compatibility explanation covering enrollment state, protocol
range, missing resources, capability mismatch, and compatible assignments.

Installation metadata is now durable and revisioned. The current Home record
is runtime-backed and seeded from the configured private package. Administrator
CRUD supports creating inactive design records, renaming with optimistic
revision checks, and deleting inactive records. The runtime-backed record
cannot be deleted, and inactive records are labeled not provisioned rather
than being presented as runnable packages. Package attachment and activation
remain a later installation-binding workflow.

The Shows workspace supports creating a draft from the active Home package,
renaming with a separate optimistic metadata revision, duplicating the current
head, archiving, searching, and portable JSON import/export. Archive preserves
all immutable draft revisions. Export contains only the project name and
portable draft document; it does not contain credentials or host-specific
absolute paths.

The initial managed asset store accepts sequential resumable chunks with
server-generated temporary names, offset fencing, an 8 MiB maximum chunk,
10 GB per-asset limit, 100 GB reserved/managed-store quota, visual/audio MIME
allowlisting, optional expected SHA-256 verification, content-addressed object
deduplication, atomic promotion, and explicit failure state. Original
filenames are metadata only and cannot influence storage paths. Finalized
uploads are processed outside the request and pending processing resumes after
controller restart. ClamAV records clean, quarantined, and scanner-failure
outcomes when deployed; scanner absence remains explicit. ffprobe extracts
duration, dimensions, frame rate, codecs, channels, sample rate, and container
metadata, while ffmpeg measures integrated loudness for audio-bearing assets.
The same asynchronous processing pass writes versioned, content-addressed
thumbnails, low-resolution review proxies, and audio waveforms under a
separate derived-artifact root. Originals remain immutable. Generated files
count toward the managed-store quota and are available only through
authenticated artifact endpoints; internal storage paths are not part of the
API. Tooling and extraction failures are persisted rather than reported as
success.

Scene media outputs reference managed assets by stable asset UUID in their
existing `asset` field. Asset responses resolve every matching scene and output
from the current project head, identify assets unused by that draft, and
diagnose missing or size-mismatched originals and derivatives at read time.
Legacy runtime package paths remain unmanaged references and are never guessed
to match an uploaded filename.

Asset governance metadata has an independent optimistic revision so concurrent
ownership, license, provenance, review-status, and expiration edits cannot
silently overwrite one another. Designer-or-higher edits are validated,
recorded atomically with their audit event, and locked when the containing
project is archived.

Managed assets can be organized with logical slash-separated folders,
case-insensitively unique tags, and favorites. These values share the asset
metadata revision and audit boundary. Workspace search matches asset names,
original filenames, folders, tags, owners, and licenses without querying
server paths.

Scene organization and reusable scene presets live in editor metadata inside
the immutable project draft. Scene folders, tags, and favorites therefore
participate in project revision history, undo/restore, and portable export.
The workspace reports incoming completion/failure references for each scene.
A reusable preset is a validated snapshot of a scene definition with its own
name, folder, tags, and favorite state. Applying it is revision-fenced,
requires the target scene schema to match, preserves the target scene identity
and name, revalidates the complete draft, and records which preset the scene
uses.

Installation folders, tags, and favorites use the installation's independent
optimistic revision. Installation responses also list projects whose current
draft explicitly binds that installation ID. Search surfaces cover names,
folders, tags, and usage references for installations, scenes, and presets.

Placing an asset is a specialized revision-fenced draft mutation. The
controller verifies that the asset belongs to the project, is ready, and has
an available original; verifies the selected scene and media output; writes
the stable asset UUID into that output; and records the selected immutable
asset revision in `editor_metadata.asset_bindings`. Stale project revisions,
cross-project assets, missing files, and invalid outputs are rejected without
changing the draft.

Portable project export schema v2 includes path-free manifests for every
managed asset and immutable asset revision, including digest, size, media
type, scan/analysis metadata, governance, organization, and the source logical
ID. Original and derived bytes are deliberately excluded. Schema-v1
project-only documents remain importable.

Import validates the complete document before mutation, rejects filenames or
metadata containing storage paths, generates new asset UUIDs, and atomically
creates the project, missing-file asset placeholders, immutable revision
records, and audit event. Scene media references and
`editor_metadata.asset_bindings` are rewritten through the same ID map.
Imported assets retain their digests and therefore become eligible for
checksum-safe relink; until their originals are restored they remain failed
with an explicit diagnostic. Credentials, sessions, temporary upload state,
and host-specific paths are never part of the document.

### Asset lifecycle contract

Managed asset identity and binary revision are separate:

- The asset UUID is the stable logical identity used by scene media outputs.
- Every accepted original creates a monotonically increasing, immutable asset
  revision containing its digest, size, media type, original filename, scan
  result, extracted metadata, and derived-artifact profile.
- A project draft records `asset_bindings` in editor metadata from each asset
  UUID to the selected asset revision. Compilers and publishers resolve that
  binding, never whichever revision happens to be current later. Historical
  drafts therefore remain reproducible, while runtime documents receive only
  staged package-relative media paths.
- Published and rollback releases pin exact asset revision and digest pairs.
  Replacing an authoring asset never mutates an existing release.

Relink repairs storage without changing meaning. The user selects or uploads a
candidate for a missing asset revision; Cueverge streams it into temporary
storage and accepts it only when size and SHA-256 match that revision. The
verified object is atomically restored to its content-addressed location and
the relink is audited. A candidate with different bytes is not a relink and
must enter the replacement workflow. Browser-supplied paths are never stored.

Replace creates and processes a new immutable revision under the same asset
UUID. The prior revision remains addressable. After processing succeeds, the
user may explicitly update the current project draft's binding in the same
authoring transaction. No other project, historical draft, published release,
or rollback release follows the replacement automatically. If the replacement
fails or is quarantined, the current binding does not move. “Version as new
asset” instead creates a new UUID and leaves all references unchanged.

Removal is a soft-delete operation. It is rejected while the asset revision is
referenced by a current draft, retained historical draft, published or rollback
release, active processing/publication job, or explicit legal/administrative
hold. Removing the last eligible reference moves the logical asset to
recoverable trash for **30 days**. Restore preserves UUIDs, revisions,
governance metadata, and references. Expiration and rejected review status do
not delete content automatically; they affect validation and operator
diagnostics.

Retention and garbage collection use mark-and-sweep:

1. Mark every digest reachable from current and retained historical draft
   bindings, published/rollback manifests, active jobs and uploads, trash still
   inside its 30-day window, and explicit holds.
2. Include all database references to a digest before considering a deduplicated
   object unreachable.
3. Produce an audited dry-run manifest containing digest, bytes, reason, and
   owning asset revisions.
4. Under the asset-store maintenance lock, re-read reachability and delete only
   objects still unmarked. A failed or interrupted sweep is safe to repeat.
5. Derived artifacts are reconstructible caches. They may be evicted before
   originals under quota pressure when no active job or release staging area
   uses them; their metadata is changed to pending so regeneration is explicit.
6. Temporary uploads and failed-generation temporary files have separate
   bounded cleanup and are never treated as accepted originals.

Automatic sweeping may reclaim only items whose 30-day trash period has
elapsed. Immediate permanent purge requires administrator authorization,
impact disclosure, typed confirmation, and an audit event. Garbage collection
never runs concurrently with publication promotion, backup snapshot creation,
or asset replacement.

An asset backup is a consistent pair of:

- a SQLite snapshot containing projects, all retained asset revisions,
  governance/audit state, release pins, trash deadlines, and holds; and
- a checksum manifest plus every marked original object.

Derived artifacts may be included for faster recovery but are not required
because they are reproducible from the original and derivative profile.
Temporary uploads, sessions, one-time credentials, and host-specific absolute
paths are excluded. A backup is complete only after every manifest digest is
verified at the destination. Restore writes into temporary storage, verifies
the database and object manifest, then promotes atomically; it never silently
overwrites a healthy newer revision. Backup retention is configured per backup
target and does not shorten the live store's 30-day trash guarantee.

### Installation photo viewpoints

GUI-03 installation photos are installation-owned, revisioned records whose
bytes live in the shared content-addressed object store. JPEG, PNG, and WebP
uploads use server-generated paths, a 25 MB request limit, digest verification,
atomic promotion, authenticated delivery, and audited designer-or-higher
mutations. Deleting a photo record does not directly remove its deduplicated
object; lifecycle garbage collection determines when the digest is unreachable.

Each photo records a display name, named viewpoint, original filename and media
type, orientation in 90-degree increments, an optional normalized crop
rectangle, and an optional positive scale hint. Crop coordinates must be
complete and remain inside the image. Metadata updates and deletion use the
photo's independent optimistic revision. These viewpoints are the durable
background layer for later geometry, surface, and calibration tools.

Installation geometry uses normalized coordinates in a selected photo
viewpoint. Revisioned geometry records support outputs, pixel segments,
fixtures, ordered paths, projector surfaces, masks, audience zones, and
safety/no-output zones. Area objects require a closed polygon with at least
three points, paths and pixel segments require at least two ordered points, and
fixtures require exactly one point. The initial SVG mapper provides
photo selection, click-to-draw points, overlay rendering, selection for edit,
optimistic updates, and audited removal. These photo coordinates remain a view
layer; shared stage coordinates and projector-native transforms are separate
later GUI-03 models.

Projector-surface mappings bind a four-corner projector-surface geometry record
to a logical projector resource, native pixel dimensions, normalized native
crop, and a rectangular extent in shared stage space. Cueverge derives a full
3×3 homography from native pixel corners to the selected photo quadrilateral
and rejects singular planes. The homography, native crop, and surface-to-stage
extent remain independent transforms: photo calibration never changes stage
coordinates, and stage layout never rewrites projector-native pixels. Mapping
metadata has its own optimistic revision and audit events.
The native model also records 0/90/180/270-degree orientation, independent
horizontal/vertical mirroring, and explicit semantic mask IDs validated against
mask geometry in the installation. One native/stage mapping enumerates every
semantic-linked photo instance with its own points and derived homography;
adding or recalibrating a viewpoint does not duplicate or rewrite the native
canvas, crop, masks, orientation, or shared-stage extent.
The SVG mapper exposes projector resource, native resolution, stage extent,
native crop, four-corner creation, and the resulting homography diagnostics.
Corner handles are labeled in native-canvas order and support pointer dragging,
undo, trapezoidal perspective, and mirrored corner order while remaining
clamped to normalized photo bounds.
Mapping content is either a generated calibration grid or a ready managed image
asset. The selected content reference is persisted independently from the
surface geometry and shown as a native-frame preview while the photo overlay
defines its destination quadrilateral.

Projector mappings explicitly select `planar` or `subdivided` surface behavior.
Planar surfaces require exactly four corners and expose a homography.
Subdivided/non-planar surfaces require additional control points, expose no
single homography, and return a publication/simulation diagnostic directing
the designer to define planar sub-surfaces. Arbitrary mask geometry remains a
separate editable layer rather than being folded into the surface transform.

Mapper alignment preview reuses the bounded maintenance-preview API. It is
available only to operators/administrators, requires maintenance mode and idle
playback, expires after 15 seconds, records audit, and remains explicitly
simulation-only. Local corner and crop changes are not persisted until Save.
No development validation sends a grid or other output to physical hardware.

An existing geometry record can be rebound only to another photo owned by the
same installation. The mapper immediately displays its temporary control
points over the newly selected calibration photo; saving preserves the
projector mapping identity, native/stage configuration, and content reference
while recalculating the derived photo homography from the newly marked physical
corners.

Projector fit defaults to preserving the cropped native-canvas aspect ratio.
The mapper compares that ratio with the stage-space surface, reports expected
letterbox/pillarbox behavior, warns when explicit stretch will distort content,
and reports intentional crop and overscan. Stretch and overscan are persisted
only through explicit controls. Photo points remain clamped to the normalized
view, while overscan is the deliberate mechanism for allowing content beyond
the physical surface.

Geometry instances can represent the same semantic installation object across
multiple photo viewpoints. Each instance has its own record/revision and
photo-space coordinates while sharing a stable semantic ID and stable ordered
landmark IDs. New linked instances must belong to the same installation, use
the same geometry kind, and provide the same landmark count. Editing points
preserves existing landmark IDs and assigns new IDs only to newly added points.
Editing any instance synchronizes shared semantic metadata—name, color, label
visibility, lock/hide, layer order, wiring direction, and physical index
range—to all linked instances under their own revisions. Photo assignment,
points, and landmark coordinates remain viewpoint-specific.
Browser acceptance covers one wide viewpoint and two close-ups: a linked
fixture edited in the wide view immediately presents the shared update in both
close-ups while each SVG instance retains a distinct photo-space position.

Easy-mode browser acceptance covers a non-level compound-angle quadrilateral:
one managed still is previewed through the saved mapping, a second managed
still is selected and saved, and the test verifies that the surface points and
3×3 homography remain unchanged while only the content reference changes.

Overlap browser acceptance pairs two mapped projector surfaces, adjusts gamma,
brightness, and black-level compensation, switches between native and mapped
previews, persists the overlap revision, and verifies that neither projector's
managed source-artwork reference changes.

Shared-stage coverage analysis partitions the bounding stage extent at every
projector edge and classifies cells as singly covered, overlapping, or
uncovered. Explicit projector masks are inverse-projected from their calibrated
photo view through the saved homography into stage-space occlusion polygons.
The API retains contributing resources/source IDs, and the mapper renders
covered, overlap, occluded, and uncovered regions in one stage-space SVG
suitable for cross-projector animation and simulation diagnostics.

Semantic geometry may store optional shared-stage points independently from
all photo instances. The runtime mapper export is allowed only for the
runtime-backed installation, rejects unknown projector/lighting resources and
fixtures or paths missing stage geometry, and emits logical resources, native
projector/stage models, lighting surfaces, stage-local pixels, fixtures, and
paths. Photo IDs, raster points, filenames, and viewpoint transforms are
excluded from the runtime document.

Mapper preflight runs before runtime export and project compilation. It reports
and blocks ambiguous semantic instances in one photo, fixtures/paths
disconnected from stage geometry, stage points outside calibrated projector
coverage, duplicate projector-resource mappings, and overlapping physical
pixel index ranges. Diagnostics include stable object IDs and actionable
messages. Accepted projector onset/release delays and lighting onset/release
offsets are included in the runtime mapper document; cancelled or abandoned
temporary sessions never change those exported values.

Geometry layer metadata persists color, label visibility, lock, hidden state,
and signed draw order under the geometry revision. The SVG mapper renders
visible layers in order, blocks editing locked objects, and provides label and
color controls. Editor-only tools include a 1% normalized-coordinate snap grid,
zoom, pan with zoom-aware pointer conversion, point undo, and normalized
point-to-point measurement.

Pixel segments persist forward/reverse wiring direction, start index, and pixel
count. The mapper displays index ranges and direction, diagnoses gaps,
overlapping index ranges, and endpoint discontinuities, and animates ordered
path traversal. Geometry bounding-box overlap diagnostics report the
higher-z-index object that has draw precedence.

Indexed pixel-segment geometry can bind to a logical lighting surface with
local width/height and a nonsingular six-value affine transform into shared
stage space. Cueverge distributes the declared physical pixel count over the
ordered polyline in wiring direction and returns each output index's
surface-local coordinate, stage coordinate, and rectangular footprint. Each
surface retains output resource, fixture, group, ordered-path, strand, and
calibration memberships. The mapper exposes transform/membership controls and
photo-view pixel/index footprints without treating photo pixels as the
authoritative lighting coordinate system.

Lighting surfaces persist their own ordered local control points within local
width/height bounds. Photo-assisted segment points seed these coordinates only
when a surface is first created; subsequent photo edits and alternate
viewpoints do not rewrite the local model. Multiple independent planar
surfaces can therefore map through separate affine transforms into shared
stage space. Arbitrary 3D geometry remains intentionally out of scope.
Semantic-linked pixel-segment instances resolve to the same authoritative
lighting-surface record even when viewed through different photos. The API
returns the requested view's geometry ID while preserving identical physical
indices, local/stage positions, footprints, fixture/group/path/strand
memberships, and calibration. Browser acceptance switches viewpoints,
verifies the photo overlay moves, verifies logical pixels do not, and exercises
the local traversal preview before any output arm or hardware command.

Pixel-layout generators create rows, serpentine grids, rings, rectangular
outlines, individual pixels, or validated imported normalized coordinates.
Open paths include both endpoints; closed paths distribute pixels around the
loop without duplicating the closing point. Generated photo points seed
surface-local coordinates and retain the declared physical count and index
range. The mapper exposes the bounded maintenance-mode pixel-index-chase
preview, which remains simulation-only until protocol dispatch is explicitly
implemented and commissioned.

Calibration values live with the object they affect and share its optimistic
revision/audit boundary. Projector mappings persist a 0–1 brightness limit and
bounded ±5000 ms media start delay. Lighting surfaces persist a 0–1 brightness
limit and bounded ±5000 ms timing offset. These controls sit beside the
maintenance-gated projector-alignment and pixel-identification previews;
preview requests remain simulation-only until physical command paths are
commissioned.

Protocol-v2 capability documents support generic timing calibration for media,
audio, pixels, network lights, smart switches, relays, and future physical
effects. Onset and release adjustments are independent bounded ranges with a
declared resolution and active-adjustment safety flag. Devices also declare
maintenance-reference, armed-trial, or simulation-only testing and optional
A/B support. Topology diagnostics display these constraints without assuming
that undeclared actuators are calibratable.

Timing calibration sessions are temporary, revisioned, audited, and expire
after ten minutes. Starting one requires maintenance mode and idle playback and
cancels any prior active session for that target. Onset and release sessions
retain distinct baselines and proposed values. The UI provides ±100 ms coarse
and ±10 ms fine nudges, explicit earlier/later direction labels, reset, and A/B
baseline/proposed comparison. Accept atomically writes the applicable
projector or lighting offset and advances its mapping revision; Cancel and
expiry leave persisted calibration untouched. Sessions remain simulation-only
until reference playback and actuator dispatch are commissioned.

Projector overlap calibrations pair two mapped projector surfaces in the same
installation with an explicit shared-stage polygon. Revisioned controls persist
blend-ramp width, overlap gamma, independent brightness, RGB white balance,
3×3 color correction matrices, and black-level compensation for both
projectors. The mapper renders a non-destructive dual-gradient/grid pattern for
adjustment; source artwork and each projector's native mapping remain
unchanged.

Overlap diagnostics report narrow-ramp seam risk, combined double-brightness,
non-linear gamma falloff, black-level mismatch, white-balance mismatch, and
color-matrix mismatch. Native preview displays the two calibrated sources
separately; mapped preview combines their ramps in the overlap. Both are local,
non-destructive views derived from the same revisioned calibration record.

Accepting a timing session creates an immutable calibration-evidence record
containing target resource, adjustment kind/value, test asset and version,
assigned device identity, reported software/firmware version, timestamp,
operator, observed variability, notes, and captured hardware, routing, display
mode, media-setting, and environmental context. Review compares current
assignment/firmware/context with that evidence and reports precise stale
reasons. Cancelled or expired sessions never create accepted evidence.

Optional computer-vision or external analysis may submit geometry suggestions
with photo, kind, points, confidence, and human-readable evidence. Suggestions
render as distinct dashed overlays and can be rejected or loaded into the
manual editor. Review changes only suggestion state; it never creates or
updates authoritative geometry. The designer must edit as needed and invoke
the ordinary revisioned Save Geometry workflow.

Physical-output safety arms are resource-specific, require idle maintenance
mode and exact typed resource confirmation, display the assigned device or
controller-owned identity, and expire after 5–30 seconds. Creating a new arm
stops the prior arm for that resource. Every arm exposes an actuator-specific
stop action (`stop media playback` or `stop realtime pixel output`) that remains
available independently of blackout; blackout is never described as a
universal safety mechanism. Arm/stop actions are audited and currently send no
hardware command. Future live test dispatch must require an active matching
arm.

### Timeline foundation

GUI-04 begins with a DOM-based timeline whose width scales from scene duration
and configurable pixels per second. It provides a ruler, horizontally
scrollable tracks, snapped click/keyboard playhead movement, safe-point marker
creation/removal, protected regions, and an editor-only loop range. Optional
quantization supports 1, 10, 100, 250, 500, or 1000 ms increments. Scheduling
changes persist in the runtime-compatible scene scheduling document, while
loop range, quantization, and zoom remain in project editor metadata. Saves use
the existing optimistic draft revision.

Timeline rows are typed and carry stable IDs plus explicit resource bindings.
Scene media and lighting outputs produce built-in read-only identity/binding
tracks. Designers can add revisioned editor tracks for audio references,
fixtures, groups, and paths, select any track, inspect its stable ID/kind, and
edit custom names and bindings in the track property panel. Custom track
metadata remains editor-only until later cue/composition schemas consume it.

Reusable compositions live in revisioned editor metadata with stable UUIDs,
names, durations, child composition IDs, per-composition safe points/protected
regions, loop/zoom/quantization state, and typed tracks. The server validates
all references and rejects cycles. The hierarchy reports parent-composition
and scene usage, supports explicit scene instantiation, and lets the editor
switch the same ruler/playhead into each composition's independent timeline.
Every composition declares one validated logical editing space: projector
native pixels, lighting-surface local coordinates, or shared-stage units. The
record includes an explicit target ID and positive width/height. Hierarchy and
timeline UI show the space, target, and dimensions; server validation rejects
missing/unsupported spaces or nonpositive dimensions.

Opening a shared-stage composition renders the calibrated installation model
behind its timeline: projector covered/overlap/occluded/uncovered regions,
authoritative stage-space lighting pixels, audience zones, and safety/no-output
zones. The mapper runtime export now carries audience and safety polygons in
shared-stage coordinates. Stage composition overlays never consume photo IDs
or raster coordinates.

Timeline navigation keeps one scene-owned global playhead while switching
between the scene root and nested compositions. Breadcrumbs show and navigate
the root-to-current hierarchy without resetting time. A composition may
declare a nonnegative local-time offset; its ruler/playhead displays
`global - offset` clamped to its duration, while keyboard movement still
advances the single global playhead. No composition owns an independent
playback clock.

Scene composition instances are revisioned editor records with globally stable
instance UUIDs, referenced composition IDs, nonnegative scene start offsets,
positive time scale up to 16×, JSON parameter overrides, and explicit x/y
placement in the target composition space. Server validation rejects duplicate
IDs, unknown compositions/scenes, invalid rates/offsets, non-object overrides,
or incomplete placement. Hierarchy usage and instance properties remain
inspectable independently from the reusable composition definition.

Composition dependency controls report parent-composition and scene usage.
Unused definitions may be deleted directly. Used definitions require an
explicit replacement; replacement rewrites parent children, scene roots, and
stable scene-instance references, rejects cycle-producing choices, removes the
old definition, and cleans orphaned timeline-view/track metadata. The server's
reference and cycle validation remains the final write boundary.

Named synchronization markers are separate from scheduling safe points. Each
marker has a globally stable ID, a name unique within its editing space, and a
bounded local timestamp. Typed visual, audio, lighting, switch, and
physical-effect cues either store an independent local timestamp or reference
one marker plus an explicit signed offset. Moving a marker therefore changes
all linked cue evaluations atomically without rewriting cue records; detaching
a cue captures its evaluated timestamp and clears the marker and offset.
Markers with linked cues cannot be deleted, and marker movement is constrained
so every linked cue remains inside the editing-space duration.

Every save deterministically expands synchronization cues into an explicit,
sorted `timeline_sync_schedule`. Scene-root cues map directly to scene time.
Reusable-composition cues map through each hierarchy path using accumulated
local offsets, and scene instances apply `scene start + local time × time
scale`. Each resolved record retains the cue, marker, editing-space, scene, and
context IDs so later package compilation can consume timestamps without
interpreting opaque editor UI state. Server validation rejects duplicate IDs,
unknown editing spaces, cross-space marker links, unsupported cue kinds,
ambiguous linked/absolute timing, and out-of-bounds resolved local times.

Project compilation does not copy or ask the runtime to interpret composition
editor metadata. The compiler independently walks every scene-root and
instance context and writes `runtime/composition-graph.json` into the immutable
package. This versioned document contains concrete scene contexts, accumulated
offsets and time scales, placement, explicit output/resource bindings, and
stable cues sorted by resolved scene timestamp. Scene-root media and lighting
tracks resolve from the runtime scene outputs; custom composition tracks
resolve from their declared resource bindings. Editor-provided resolved
schedules are not trusted as compiler input.

Compilation is lossless-or-fail. A cue resolving outside its scene, a cue whose
track has no runtime binding, or an instance with arbitrary parameter
overrides that the package cannot yet represent blocks compilation. The API
returns structured diagnostics with stable codes and involved object IDs, and
no partially compiled package is installed. Empty or cue-free composition
contexts remain explicit no-op contexts rather than being mistaken for runtime
effects.

Lighting synchronization cues are duration-bearing layers rather than
instantaneous annotations. Their start can be moved directly or remain linked
to a named marker by an explicit offset, and their duration can be resized
within the editing-space boundary. The property editor exposes the runtime
lighting primitive, optional segment/fixture/path target, start and duration,
color and end color, brightness and end brightness, fades, blend mode,
precedence, deterministic seed, period, pixel width, density, and reverse
direction. The timeline renders the resulting duration as the layer width.

The server validates every lighting layer through the same `LightingCue` model
used by runtime scenes and additionally ensures its complete interval fits in
the local editing space. Marker movement accounts for linked lighting
durations, so moving a shared anchor cannot push a linked layer past the
composition boundary. Composition compilation carries the full validated
lighting payload into each explicit runtime cue and scales duration alongside
start time for time-scaled instances.

Timeline media-output tracks resolve the exact asset revision binding already
stored on the scene output, while custom audio-reference tracks bind by stable
asset ID. When processing produced them, the timeline displays the existing
authenticated thumbnail and waveform artifacts as non-authoritative timing
references behind cues and regions. Video tracks may show both thumbnail and
waveform layers; audio-reference tracks show their waveform. Missing assets or
derivatives remain explicit in the track instead of producing a fabricated
preview. The timeline does not create duplicate derivative files or persist
artifact URLs in project metadata.

Image, video, and nested-composition layers can carry ordered 2D transform
keyframes in any scene or composition editing space. A layer has a stable ID,
source ID, output-track binding, start, duration, and at least one keyframe.
Each keyframe stores position, independent X/Y scale, rotation, anchor,
opacity, and normalized left/top/right/bottom crop or reveal bounds. Scale may
be negative for intentional mirroring but cannot be zero; crop bounds cannot
remove the entire source.

Interpolation is explicit per keyframe: hold, linear, ease-in, ease-out, or
Bezier. Bezier records preserve four temporal control values plus spatial
incoming and outgoing handle vectors. Non-Bezier interpolation cannot retain
hidden Bezier controls. The server enforces stable globally unique layer and
keyframe IDs, ordered unique keyframe times, valid source composition
references, bounded layer intervals, normalized opacity/crop, and complete
interpolation data.

Compilation flattens each transform layer through every scene-root and instance
context. Runtime records contain the concrete output binding, scene start and
scaled duration, source identity, editing-space/context provenance, and every
keyframe with its explicit scene timestamp and interpolation controls. Unknown
track bindings and intervals extending beyond a scene fail compilation with
structured diagnostics rather than dropping animation.

Shared-stage transform layers render their ordered positions as an editable
motion path over calibrated coverage. Bezier segments use the stored spatial
incoming/outgoing vectors and display their handles; the selected keyframe is
shown with previous/next position ghosts. Clicking the stage moves the selected
position directly. Optional spatial snapping chooses the nearest candidate
within a bounded screen-space threshold from named fixture/path landmarks,
projector-surface edges, or overlap seams. Keyframe time can independently snap
to a named synchronization marker without changing spatial coordinates.

Motion diagnostics sample every segment instead of checking endpoints only.
They warn when any sample enters an explicit uncovered polygon, an occlusion
mask, or a location with no covered/overlap calibration evidence. These are
editor diagnostics only: they do not change authored coordinates, synthesize
handoffs, or activate projector output.

Every transform layer is authored once in the logical coordinate space of its
containing scene or composition. The editor displays that space, target, and
dimensions beside the layer controls. Compiled transform records repeat this
contract explicitly as `coordinate_space` and declare a calibration policy:
static installation calibration is applied at runtime and
`projector_handoff_keyframes` is false. A stage-space motion therefore remains
one layer and one keyframe sequence while crossing any number of calibrated
projector surfaces; no projector-native duplicates or manual seam fades are
generated or required.

Property animation is allowlisted rather than inferred from arbitrary editor
objects. Runtime transform keyframes support position, independent X/Y scale,
rotation, anchor, opacity, and crop/reveal bounds. Lighting primitive, target,
colors, brightness, blend, precedence, seed, and primitive parameters remain
static for each duration-bearing cue until the runtime schema or GUI-05 baking
pipeline defines deterministic animation for them. The editor states this
boundary explicitly; it does not render preview-only lighting animation.

Validation rejects generic property-keyframe metadata and extra transform or
interpolation fields. Compiled transform records declare
`keyframed_properties` and a `property_animation_policy` of runtime transforms,
static lighting cues, and rejection for unsupported properties. This prevents
packages from silently losing editor-only animation.

The root scene timeline exposes the runtime scheduling and output contract
without requiring JSON edits. In addition to safe points and protected
intervals, creators can edit scene priority, completion policy and target,
request policy and TTL, explicit resource claims, the required media clock
source, per-output required/optional status, media start calibration offsets,
and signed lighting timing offsets. Composition timelines do not duplicate
these scene-owned controls.

Timeline saves write these fields directly into the versioned runtime scene
document, not editor metadata. The existing core scene models remain the final
boundary for valid priority ranges, completion targets, clock-source
requirements, claimed resources, output uniqueness, offset ranges, and
scheduling invariants.

Lighting cue presets are revisioned editor templates with stable IDs, names,
and fully validated lighting payloads. Saving a preset copies the selected
cue's values into the template; applying it copies those values into every
selected lighting cue. Runtime cues cannot carry `preset_id` or other hidden
template references, and compilation remains independent of preset metadata.

Compound selection spans cues and transform layers in the active editing
space. A bounded bulk shift preserves marker links by changing cue offsets,
moves detached cues and layer intervals directly, and is disabled if any
selected interval would leave the editing-space duration. Duplicate creates
new stable cue, layer, and keyframe IDs while copying values; delete removes
the selected records. Presets and compound selection are editor operations,
not runtime dependencies.

Timeline validation runs continuously over the unsaved editor state. It checks
scene priority, completion/request policy, claims, clock-source requirements,
safe points, protected intervals, marker identity/timing, cue bindings and
intervals, lighting fades/targets, transform bindings and intervals, unique
keyframe times, nonzero scale, opacity, and crop bounds. Diagnostics carry
stable codes and object IDs, appear in a global validation panel and beside
the selected cue/layer/keyframe, and mark invalid timeline items.

The timeline save action is disabled while any diagnostic remains. Resolving
the exact field clears the error immediately; server/core-model and compiler
validation remain authoritative defense-in-depth rather than the first place a
creator learns that editor state is invalid.

The integrated GUI-04 multi-output acceptance authors and saves, without JSON
editing, a media output with thumbnail/waveform timing evidence, a separate
audio-reference track, baseline lighting, two overlapping fixture pulses, a
path sweep, one named synchronization marker, and a safe transition point.
Browser assertions inspect the persisted scene and editor records, including
semantic fixture/path targets and marker offsets.

The projector-composition acceptance opens a 1920×1080 projector-native
composition, edits a layer position without rendering any location photo,
saves, returns through the composition breadcrumb to the root scene, and then
opens the installation mapper. The mapper verifies that the same logical
projector mapping exposes independent homographies for wide, left-close, and
right-close calibrated photo views.

Reusable-composition acceptance creates a child definition, links it into a
parent, instantiates it in a scene with placement, time scale, and explicit
overrides, edits the child's timeline, and verifies the instance still points
to the same stable child ID with overrides visible. Separate authenticated API
acceptance rejects graph cycles, while compiler acceptance inspects explicit
contexts, outputs, cues, transforms, and timestamps in the immutable runtime
package.

Dependency acceptance reports every parent-composition edge, root-scene edge,
and scene-instance reference, including the stable instance ID. Unused
definitions can be deleted; any parent-, scene-, or instance-referenced
definition requires an explicit replacement. Replacement rewrites all three
reference classes and preserves linked semantics, so there is no silent
conversion to an independent copy.

GUI-04 closeout adds bounded editor history and deterministic evaluated-state
inspection. Undo and redo restore complete timeline snapshots for compositions,
instances, tracks, markers, cues, transforms, presets, safe/protected timing,
and scene policy. Scrubbing emits a stable, sorted evaluated-state document for
active cues and transform state; repeated evaluation at the same timestamp is
byte-identical.

Final marker acceptance links visual, audio, lighting, switch, and
physical-effect actions to one named marker and verifies their intentional
offsets in the explicit resolved schedule after moving the marker once. Final
motion acceptance uses one semi-transparent curved stage-space layer from left
projector coverage, through calibrated overlap, into right projector coverage,
with no projector-native handoff layer.

Save/reload browser acceptance preserves exact layer/keyframe timing and
ordering after undo and redo. Compiler acceptance independently sorts explicit
runtime cues by timestamp and stable identity. Unsupported lighting-property
keyframes and nested time remapping are disabled with schema-gap explanations;
crafted unsupported metadata is rejected by authenticated API validation
instead of being previewed or compiled lossily.

Topology exposes each enrolled device's complete typed capability document and
active certificate expiry without exposing certificate contents. The UI shows
protocol range, media codecs/containers, simultaneous-output limits,
pause/seek support, declared artifact-cache capacity, software version, and
days until certificate expiry. Missing capabilities remain explicitly not
declared.

Protocol-v2 heartbeats use an NTP-style four-timestamp exchange. Clients report
network round-trip latency with controller processing time removed and their
estimated controller clock offset. Reports are accepted only from the current
certificate-authenticated device session, persisted with presence, and shown
in topology for connected or last-observed on-demand devices.

Operator and administrator users can run a non-controlling connection test for
an enrolled device. The test evaluates enrollment state, fresh authenticated
heartbeat, timing availability, and logical assignment from current controller
evidence, records the result in audit, and sends no media or hardware command.

Media clients asynchronously scan their configured asset root once per
authenticated connection. They report filesystem capacity, deduplicated
SHA-256 digest/size records, aggregate artifact bytes, and bounded scan faults
without exposing filenames. Reports are session-fenced, replace the prior
snapshot atomically, and remain observable while an on-demand device is
offline. Transfer, pinning, and eviction workflows are not implemented yet.

The same inventory report negotiates whether the client supports pinning and
resumable transfer, identifies pinned digests, and reports active or failed
transfer byte progress and faults from local cache-state metadata. This
completes GUI-01 readiness reporting; creating uploads, scheduling transfers,
pin/eviction mutations, and artifact retention policy remain GUI-02 work.

Controllers publish a minimal unauthenticated discovery document containing
only product identity, software version, protocol range, device kind, and
capability kinds. Administrator explicit-address discovery accepts only HTTPS
origins resolving exclusively to private, loopback, or link-local addresses,
rejects credentials/paths/query/fragment/redirects and oversized responses,
and persists compatible or incompatible candidates as untrusted. Discovery
never creates credentials, assignments, or command authority; candidates may
be explicitly rejected.

A compatible discovery record can prepare a candidate-bound pairing token.
The token stores the discovery ID, rejection revokes an unconsumed linked
token, and successful token consumption atomically links the resulting
enrolled device UUID back to the discovery record. Discovery still contributes
no trust by itself; the device must complete the ordinary CSR/mTLS enrollment.

Installation schema now retains a distinct `downstream_controller` resource
kind. Downstream devices enroll and maintain authenticated presence without
registering as direct media agents, and assignment validation accepts only
their `downstream_control` capability. Simulated tests cover enrollment,
presence, topology distinction, compatible binding, and rejection of media
authority conflation.

The GUI-01 read-only topology snapshot combines current runtime observations
with the durable protocol-v2 identity registry: the local controller,
controller-owned pixel outputs, configured legacy media-agent targets, live
legacy connections, enrolled devices, declared capabilities, enrollment state,
and logical-resource assignments. Connection, assignment, compatibility, and
enrollment remain separate dimensions.

Protocol v1 has no authenticated device identity or enrollment flow. Connected
agents are therefore labeled as legacy unauthenticated observations rather
than enrolled or trusted devices. Software version, latency, clock drift,
storage, discovery, and readiness remain explicitly unavailable for those
legacy connections. The topology API is read-only and sends no agent or
hardware commands.

ADR 0015 selects installation-scoped mTLS device certificates bootstrapped by
administrator-created, single-use pairing tokens with a maximum five-minute
expiry. Stable controller-assigned UUIDs—not IP addresses, hostnames, or
caller-supplied agent IDs—identify devices. Protocol-v2 capability documents
separate declared ability from controller authorization and distinguish direct
media agents from downstream controllers.

The implemented enrollment foundation remains disabled by default and requires
explicit confirmation that server-authenticated TLS termination is deployed.
When enabled, an administrator may create a digest-only, single-use pairing
token through the authenticated GUI API. A device generates its private key
locally and submits a CSR and typed capability document to
`/api/device/v2/enroll`. The controller provisions an encrypted,
installation-scoped CA, returns one client certificate, and atomically persists
the enrolled identity, certificate metadata, consumed token, and audit event.
Invalid tokens receive a generic error and enrollment attempts are rate
limited.

The mTLS network listener authenticates enrolled certificates and supports
fenced protocol-v2 hello, heartbeat, media commands, acknowledgements, and
playback telemetry. It runs inside the controller process on its own TLS port
and shares the authoritative runtime directly. Its server certificate is
issued by the installation CA for explicitly configured DNS/IP SANs, and a
real loopback integration test verifies the TLS and WebSocket handshake.

Administrators can create, replace, and remove revisioned logical-resource
assignments through CSRF-protected GUI API mutations. Assignment creation
requires an enrolled device and a capability compatible with the active
installation resource; suspended or revoked devices cannot receive new
assignments. Records may include an optional device-local output identifier
and are audited. They are future protocol-v2 binding inputs only: changing an
assignment does not modify the active show package or send a runtime command.
The Installation workspace presents these durable assignments read-only.
Administrators can also create single-use pairing tokens and change whether an
enrolled device is expected to remain continuously connected or operates
on-demand. These mutations use the existing CSRF, role, audit, and explicit
refresh boundaries; token plaintext is shown only in the creation response.
Logical media resources can be assigned, reassigned, or unassigned through the
same workspace with optimistic revision checks. Assignment mutations require
idle playback and disconnected current and target devices so an existing v2
session can never retain authority under a newly displayed binding.
Administrators can suspend, resume, or permanently revoke enrolled devices.
Suspension preserves identity and assignment while rejecting subsequent v2
messages; resume restores the active certificate. Revocation invalidates all
device certificates, is irreversible, and requires a new enrollment.

Configured DDP/WLED outputs are observed directly through WLED's read-only
HTTP API when topology is requested. Cueverge reports HTTP reachability,
firmware, configured pixel count, Wi-Fi signal, current on/off state, and
pixel-count compatibility. The probe has a bounded timeout and never changes
WLED state; probe failures are returned as explicit diagnostics.

The `cueverge-device-enroll` client command performs the matching native
enrollment flow. It reads a reviewed capability document, prompts without echo
for the single-use token, generates the private key locally, verifies the
issued certificate identity and returned CA chain, and atomically persists the
credential set with restrictive permissions.

An enrolled media agent maintains one native mTLS protocol-v2 media session.
The certificate identifies the device, its durable assignment resolves the
logical output, and the socket carries fenced heartbeat, commands,
acknowledgements, and playback telemetry. The listener durably records
session-scoped connect, heartbeat, and disconnect observations for GUI status.
Stale sessions expire by the controller heartbeat timeout, and old sessions
cannot update or disconnect a replacement session.

`cueverge-device-rotate` performs two-phase credential rotation. The
controller first issues a pending certificate while the current credential
remains active. The client verifies and atomically persists the new keypair,
and the controller activates it only when the device reconnects successfully
with that certificate. Activation supersedes the previous serial and records
separate staged and activated audit events.

## Test strategy and sequence

1. Repository tests for migrations, atomic revision creation, stale
   `If-Match`, and audit records.
2. Compiler round-trip through the existing package loader, including an
   assertion that editor metadata is absent.
3. API tests for unauthenticated denial, login/session/CSRF/origin enforcement,
   role enforcement, revision conflicts, and read-only status.
4. A side-effect test proving GUI reads and compilation do not send agent
   commands or render hardware frames.
5. Frontend component tests for login and navigation, plus TypeScript and
   production-build checks.
6. Full pytest, Ruff, and mypy regression validation.

Recommended next increment: commission real two-projector media assets against
the protocol-v2 command path and add scheduled rotation before certificate
expiry.
