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

This is a browser foundation, not client enrollment. Agent protocol v1 remains
unauthenticated and must not be exposed as a trusted enrollment channel.

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

## Initial navigation

The first shell contains:

- **Installation**: authenticated summary of the active package and logical
  output resources;
- **Shows**: durable authoring-project summaries and head revisions;
- **Operate**: authenticated, read-only controller status with observation
  timestamp and source.

There are no browser live-control actions in this slice.

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

Recommended next increment: harden the user/account lifecycle and build the
GUI-01 authenticated operator status topology before adding any live command.
