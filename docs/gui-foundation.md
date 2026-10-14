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
