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

The initial shell supports current evergreen Chrome, Edge, Firefox, and Safari,
uses semantic HTML, visible keyboard focus, reduced-motion preferences, and a
responsive navigation layout. Localization infrastructure is deferred until
the first editable product vocabulary stabilizes; user-facing strings remain
centralized in components rather than engine code.

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

Add an opt-in `/api/gui/v1` router, isolated under `cueverge.gui`. Existing
runtime routes remain unchanged and are not treated as the secured browser
management API.

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
runtime documents. Later autosave, undo, and redo operations will append
explicit revisions using the same transaction contract.

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
