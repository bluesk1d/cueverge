# Cueverge GUI — Product and Implementation Specification

**Status:** Proposed implementation companion to `docs/gui-roadmap.md`  
**Version:** 1.0 · 2026-10-12  
**Repository baseline:** User-provided `cueverge-main.zip`, reviewed 2026-10-12 (snapshot, not live branch)  
**Intended repository location:** `docs/gui-implementation-spec.md`

## 0. Purpose, authority, and non-goals

This document specifies the product behavior, domain boundaries, contracts, implementation sequencing, and acceptance tests for GUI-00 through GUI-11. **`docs/gui-roadmap.md` remains the milestone/scope authority.** This document adds UX decisions and concrete engineering contracts; if implementation discovers a conflict, propose a documented change rather than silently replacing the roadmap. Existing architecture decision records (ADRs), schema validators, and tests are authoritative for *current* runtime behavior. Future proposals here are not claims of implemented features.

**Non-goals for the first creator release:** full browser NLE/video compositor, DAW, arbitrary 3D mesh authoring, real-time multiuser editing, multiple concurrently active scenes, unauthenticated LAN administration, arbitrary physical-actuator cues, or cloud dependencies.

## 1. Repository capability audit

Evidence is from inspected source and docs, not a full live-device test. “Present” means source implementation and/or tests exist; “validated on hardware” only when the repository documentation explicitly says so. Re-audit on the working branch before coding.

| Capability | Snapshot status | Evidence | GUI consequence |
|---|---|---|---|
| FastAPI controller, status and transport endpoints | Present | `src/cueverge/controller/app.py` `/api/v1/status`, `/scenes`, `/show/start`, `/show/mode`, `/show/reset`, `/stop`, `/pause`, `/resume`, `/seek`, `/blackout`, `/events`, agent WS | Can underpin read-only and local operator shell; not a secure browser management API yet |
| Media clock authority, follower, fencing, ack/heartbeat | Present, tested | `timing/follower.py`, `controller/runtime.py`, `protocol/models.py`, protocol tests; `docs/architecture.md` | Preserve agent-authoritative media position and controller-owned scene selection |
| Multiple media and lighting outputs per scene | Present (scene v2) | `core/models.py` `SceneDefinitionV2`, `tests/unit/test_multi_output_media.py`, `test_multi_output_lighting.py` | Native-output preview and routing can be built; **one scene active at a time** |
| Arbitrary video clip tracks/transitions within one output | **Not present** | v2 `MediaOutputDefinition` has asset, required, start delay; no general clip timeline/compositor | First timeline editor targets existing scene media outputs; richer media sequencing requires explicit engine RFC |
| Timed lighting cue timeline | Present | `lighting/timeline.py`, `LightingCue` and `LightingOutputDefinition` in `core/models.py` | Early usable timeline UI: timed cues, fades, colors, brightness, blending, target selection |
| Lighting effects and ordered pixel semantics | Present | `LightingCue` primitives: solid, blackout, envelope, pulse, chase, path_sweep, flicker, sparkle, ripple, color_transition; `PixelSegment`, `PixelFixture`, `PixelPath` | Expose existing effects; don't confuse timeline `blackout` primitive with operator safety blackout |
| Physical XYZ geometry, surface orientation, photos, projector warp | **Not present** | `InstallationDefinition` v1 stores resource bindings and pixel index partitions, not 3D coordinates | New editor-only spatial model and mapping/calibration runtime extensions |
| Show program pools, weights, cooldowns, seed | Present | `ShowProgramDefinition`, `ScenePool`, `ProgramScene`; `core/director.py` | Structured Director UI can edit supported program JSON |
| Audience presence, encounters, opportunities, arbitration | Present, tested software-only | `observation/presence.py`, `core/encounter.py`, `core/opportunities.py`, `core/arbitration.py`, `simulation.py`; integration tests | Use engine evaluator for scenario simulation; visual trace requires API/structured reasons |
| Deterministic simulation | Present as Python harness | `src/cueverge/simulation.py`, integration tests | Expose simulation API; browser visualization and persisted scenarios are new |
| Windows mpv media agent | Present; hardware validation documented | `media_agent/mpv.py`, `mpv_player.py`, `docs/milestones.md` | Existing agent transport can be reused |
| Linux mpv media agent | Present; target device not validated in snapshot | Unix socket transport; `docs/linux-media-agent.md` | Explicit Linux media-host validation task; no claims of deployed readiness |
| DDP lighting output | Present; Windows/WLED target validation documented | `lighting/ddp.py`, `lighting/sink.py`, `docs/milestones.md` | Preserve opt-in output and current render cadence semantics |
| Secure device enrollment, protocol capabilities, RBAC | **Not present** | `docs/gui-roadmap.md` explicitly notes agent v1 lacks auth/authz/capability negotiation | GUI-00 prerequisite before network-exposed operations |
| Asset library, transcoding, content-addressed cache | **Not present** | `core/loader.py` package-confined resolution; `media_agent/mpv.py` local asset root; no transfer/cache API | Existing local files are **not** a managed distribution system |
| Persistent draft/projects, publish/deploy/rollback | **Not present** | package loader consumes declarative files; no revision DB or deployment lifecycle | New authoring/publishing service; don't mutate active show files |
| Installation schedule / runtime supervisor / latched blackout | **Not present as designed here** | current operational API and scene scheduling metadata are not an installation-level calendar or full supervisor | New lifecycle layer; current `/blackout` must not be assumed latched |
| Browser UI or MCP authoring service | **Not present** | no frontend source or MCP authoring implementation in snapshot | Build incrementally against stable APIs |

**Important schema constraints:** `SceneDefinitionV2` requires at least one media output and one lighting output, a required media clock source, and **unique resource per scene output**. `InstallationDefinition` schema v1 binds each logical resource uniquely. Proposed shared-device mappings and general media layering **cannot be serialized directly into current v1/v2 schemas** without compilation to compatible runtime resources or a separately versioned schema extension. The loader's installation file is presently *inside the package*; the desired portable show + installation-binding snapshot separation is future work. Maintain backward compatibility for current examples and tests.

## 2. Product model and information architecture

### 2.1 Top-level entities

- **Installation:** durable physical environment, controller identity, devices, logical outputs, 2.5D geometry, photos, calibration, schedules, health, deployed releases.
- **Show Project:** portable authoring entity, with draft revisions, scenes, director, interactions, assets, logical requirements; can be exported/imported to another installation.
- **Show Release:** immutable compiled show and exact asset revision manifest; no editor-only data.
- **Installation Binding Revision:** immutable resolution of logical requirements to enrolled devices, physical surfaces, fixtures, calibration, and safe limits.
- **Deployment:** a show release plus installation binding revision and staged verified client artifacts. State: preparing/staged/active/previous/failed (actual transition contract to be defined).
- **Scene:** deterministic single active unit of presentation with multiple synchronized outputs and lighting cues; director arbitrates *between* scenes.
- **Asset Revision:** immutable original content identity; derived preview proxy and delivery variants are separately tracked.
- **Calibration Session:** temporary explicitly authorized control lease with output scope, expiration, limits, and safe cleanup.

Installation is the primary organizational context. Shows live in its library for convenient editing, but remain portable via logical requirements; import never overwrites destination physical geometry.

### 2.2 Navigation

Global shell: installation switcher; controller/agent health; current operating mode; active show; persistent stop/blackout access; account/permissions.

Top-level workspaces: **Overview**, **Installation**, **Shows**, **Operate**, **Settings**. Installation tabs: Overview, Devices, Mapper, Schedule, Readiness. Show tabs: Overview, Scenes, Director, Interactions, Assets, Simulate, Publish. Show's Simulate and Publish are contextual tabs, **not** top-level global workspaces. Show editor: object tree/library left, central stage/media preview, contextual inspector right, timeline bottom; panels resizable on desktop. Operator mobile UI is glanceable, with large safety controls. Preserve selection, scene time, and navigation context on cross-workspace deep links.

### 2.3 Roles and modes

Roles: viewer, designer, operator, publisher, administrator; enforce on server. Modes: Edit, Preview (no physical output), Rehearse (explicit scoped physical authorization), Publish, Operate, Assist (permissioned proposals). Do not conflate roles with runtime modes. A published show remains immutable; draft changes never alter active execution.

## 3. Installation mapper — 2.5D, not a 3D modeling tool

### 3.1 Geometry contract

Use real measured **XYZ coordinates** in installation units, with a documented axis convention and origin; present mostly **2D orthographic Front/Top/Side views** and calibrated photo views. Optional read-only isometric preview later. No required 3D meshes or free-orbit modeling UI.

- **Surface:** stable ID, local 2D coordinate system, 3D origin, orientation/basis, physical dimensions, optional planar polygon, anchors and shared edges.
- **Photo view:** image, landmarks, registration transform, calibration confidence; moving photo pins normally adjusts registration, **not** physical dimensions.
- **Pixel path:** ordered 3D polyline with optional per-vertex fixture/surface attachments. Separate (a) physical geometry, (b) electrical addressing/index order, (c) semantic fixture membership. Explicitly support reverse direction and wiring discontinuities.
- **Projection mapping:** separate surface, projector output, and mapping transform/mask. One projector may illuminate two perpendicular surfaces; geometry alone does not prove optical reachability, focus, or absence of occlusion. Calibration validates coverage.
- **Logical anchor:** stable portable names, e.g. `right_portal_center`, resolved to destination geometry at binding time.
- **Spatial effect modes:** surface-local 2D, path-relative physical distance, or stage-spatial XYZ distance. Distinguish geodesic path travel from straight-line radial propagation.

Model shared edges and “add connected surface at 90°”; permit an LED path to continue across surfaces. Device-to-surface and fixture-to-device relationships may be many-to-many in the **editor model**. Existing runtime's unique output-resource claims require a compiler adapter or versioned extension with explicit conflict validation; do not bypass existing validators.

### 3.2 MVP versus extensions

MVP: measured surfaces, simple photo registration, Front/Top/Side, ordered LED paths, dimensions, logical anchors, output assignments, numeric inspector, undo/redo, static preview. Subsequent: multiple registered photos, perspective-correct projection transforms, masks/warp, edge blending, advanced snapping and spatial propagation. Hardware test patterns only within a calibration session.

## 4. Scene Editor and Spatial Timeline

Layout: scene tree/assets, central preview (Native Outputs / Installation Composite), inspector, multitrack timeline with scrubbing and transport. Selected track/cue drives inspector and stage handles. Timeline times are relative to a single scene clock. Reuse production lighting evaluation for deterministic preview.

**Supported now:** one media asset per media output plus start delay; multiple logical media outputs; lighting cues with start/duration/fades/colors/brightness/blend/precedence; scene duration, completion policy, safe points/protected intervals, priority, resource claims. GUI-04 first slice must compile into existing `SceneDefinitionV2` with no invented media features.

**Requires extension:** arbitrary video/audio clip sequencing, crossfades, independent audio track mixing, nested compositions, keyframe curves beyond current cue parameters, live visual compositing, stage-spatial effects. These may appear as disabled/planned UI affordances, but must not save unsupported semantics as though they were executable. Propose an engine RFC and schema version before enabling.

A **track** is an authoring lane; a **cue** is a scheduled runtime action. Reusable compositions should declare logical inputs/outputs and be expanded or compiled into supported runtime primitives when possible. Scene editor owns *inside-scene* time; Director owns *between-scene* selection. Current runtime plays one scene at a time.

## 5. Autonomous Director

Three coordinated views: **Program** (pools, weights, cooldowns, eligibility), **Simulate** (synthetic events, accelerated time, deterministic seed), **Explain** (candidate list, selected/deferred/rejected reasons). Prefer structured rule forms to a mandatory node graph. Expose existing program/encounter/opportunity fields faithfully; do not invent runtime conditions without a schema change.

Definitions: event = observation; encounter = engagement lifecycle; opportunity = time-bounded chance to play a specific interaction. Distinguish requested, eligible, selected, and started. Scene scheduling safe points/protected intervals and arbitration policy remain engine-owned. Show weighted selection as **relative weights**, not promised percentage of wall-clock runtime. Traces need machine-readable reason codes + user-facing explanations, tied to a reproducible scenario, random seed, and runtime/schema version.

## 6. Scheduling and live operations

Scheduling belongs to **Installation**, not the portable show. Rules select an approved deployed release; support weekly windows, date exceptions, blackout dates, and manual override with expiration. Installation timezone and DST behavior explicit. Reject ambiguous overlapping schedules in MVP.

Proposed runtime supervisor: Stopped → Preparing → Running → Stopping, with orthogonal Degraded health and latched Blackout safety override. Safety > authorized operator override > installation schedule > active director. **These are new runtime contracts, not current engine features.**

Operate UI: active release/scene, scene time, health with verified/stale/unknown distinction, audience state, pending interaction, mode and source of authority, device list, activity trace, request scene, hold auto, graceful stop, immediate stop, blackout. Never claim a command succeeded until controller acknowledgement. Mode changes need confirmation and permission. Stop and blackout remain globally accessible. Blackout must be latched and require explicit reset before schedule may restart; software blackout is not power isolation. Define hardware-appropriate watchdog/fail-safe behavior and bounded retry, with no stale cue replay on reconnection.

## 7. Assets, portability, publication, deployment

### 7.1 Asset pipeline

Shared catalog with show-specific references. Import original → analyze metadata → generate preview proxy/waveform → prepare compatible delivery variant → verify checksum → stage to clients. Immutable revisions identified by digest (e.g. SHA-256); filename is display metadata, not identity. Source dimensions and device output canvas dimensions remain separate. Track provenance/licensing and references; no arbitrary path browsing via public API.

Current media agents use locally provisioned files beneath an asset root. Managed client cache, chunked/resumable transfer, verified artifact inventory, eviction, pinning, disk-space checks, and delivery variant selection are **future protocol and service work**. Do not label locally resolved assets as distributed/verified.

### 7.2 Package and binding

Portable release: manifest, scenes/director, logical capability requirements, immutable asset references. Installation binding: concrete enrolled outputs, fixture/surface mapping, calibration, timing offsets, limits. Deployment: release + binding snapshot + client readiness. Maintain old package loader compatibility until a new loader/compiler is versioned and tested. Full export bundles contain media; thin export explicitly reports missing referenced artifacts.

### 7.3 Publishing workflow

Validate → Resolve bindings → Stage and verify clients → Rehearse (optional or policy-required) → Activate at safe boundary → Observe/rollback. Separate **Publish Version**, **Stage Deployment**, **Activate Deployment**. Block required missing/invalid assets, incompatible mandatory capabilities, or unverified mandatory client readiness. Warnings require acknowledgment; advisories are informational. Show deep-linked fixes and package/binding diffs. Activation should be transaction-like with idempotency keys, explicit failure reporting, and previous known-good retention; do not promise distributed atomicity across disconnected devices. Rollback restores package **and its binding snapshot**. Pin active, staged, in-use, and rollback artifacts against eviction.

## 8. Device discovery, enrollment, calibration

Discovery is untrusted; explicit address entry and local discovery both feed the same pairing flow. States: Discovered → Pairing → Enrolled; Suspended and Revoked. Connectivity, compatibility, and readiness are independent dimensions. Verify identity with time-limited challenge and establish authenticated transport (mTLS is a candidate, final design requires an ADR). Bind authorization to stable device identity, not IP address. Negotiate protocol version, codecs, outputs, clock features, cache/storage, supported operations, and limits. Downstream controllers and direct media agents have distinct authority contracts.

Calibration tests use a time-limited lease, explicit target(s), brightness/output limits, conflict exclusion with live resources, and guaranteed timeout cleanup. Projector test cards, LED pixel chase, and sensor input diagnostics. Readiness levels: Edit-ready, Test-ready, Show-ready **for a specific operation and show**, not one permanent installation-wide boolean. Replacing a client should preserve logical roles and require re-verification of affected physical calibration.

## 9. API and persistence contract (proposed)

### 9.1 Backend modules

Add an authoring service (may share FastAPI process but isolate service/repository layers) for projects, revisions, assets, mapping, simulation, validation, releases, deployments, and audit. Do not make browser code directly mutate `shows/*` or invoke private controller internals. Keep engine pure and independent of frontend framework.

Suggested REST resource groups, **names illustrative until OpenAPI contract is approved**:

- `/api/gui/v1/installations`, `/installations/{id}/geometry`, `/devices`, `/bindings`, `/schedule`, `/readiness`
- `/api/gui/v1/projects`, `/projects/{id}/draft`, `/projects/{id}/revisions`, `/projects/{id}/scenes`, `/director`
- `/api/gui/v1/assets`, `/assets/{id}/revisions`, `/assets/{id}/usages`, `/assets/{id}/proxies`
- `/api/gui/v1/validate`, `/simulate`, `/releases`, `/deployments`, `/deployments/{id}/activate`, `/rollback`
- `/api/gui/v1/operations`, `/audit`, `/events` (bounded authenticated event stream)

Preserve current `/api/v1/*` contracts until intentional versioning; use adapters rather than breaking clients. Use request/response schemas, typed error codes, correlation IDs, idempotency for side-effecting operations, pagination, rate limits, and server-enforced authorization. Readiness/status should state observed timestamp and source.

### 9.2 Persistence and concurrency

Store draft revisions durably, with monotonic revision ID or ETag, optimistic concurrency (`If-Match` or equivalent), autosave, undo/redo as explicit transactions, atomic commit and crash recovery. Conflicts return structured diff; never silently last-write-wins. Store editor-only geometry/photo/proxy/layout metadata separately from runtime package. Version schemas and provide migrations; test import/export of old package fixtures. Retain audit records for actor, action, target, requested revision, result, timestamp, and authorization context; keep credentials and secrets out of audit payloads.

### 9.3 Deterministic preview

Expose pure backend evaluation of lighting frames and director decisions using existing renderer/simulation. Browser renders backend-evaluated state rather than reimplementing timing semantics. Media previews may use browser proxy playback, labeled approximate; controller/agent media time remains authoritative in live mode. Define reproducibility with input scenario, seed, package hash, and engine version.

## 10. Security and safety invariants

1. No unauthenticated non-loopback browser access to topology, assets, editing, or hardware operations.
2. Unenrolled agents cannot receive operational commands; current v1 agent WS is not an enrollment mechanism.
3. Simulate and Preview cannot activate hardware; Rehearse/Operate require explicit scoped authorization.
4. Draft mutation cannot modify active package or active binding snapshot.
5. Stop/blackout always accessible and server-enforced; hardware command success never inferred from UI intent.
6. Blackout is latched until authorized reset; no schedule-triggered reactivation during blackout.
7. Physical effects (fog/motors/relays) require separate adapter-specific interlocks and cannot be generic seekable cues.
8. Asset upload validates type/size, enforces quota and access, and cannot escape asset root.
9. Device permissions and MCP scopes are server-enforced and audited.
10. Cache cleanup cannot evict active, rollback, or currently playing content.

## 11. Screen inventory and primary flows

| Screen | Key elements | Main flow |
|---|---|---|
| Installation directory | create/import/archive, health summary | choose installation |
| Installation Overview | health, active show, map thumbnail, drafts | navigate to work |
| Devices | discovered/enrolled topology, trust, capability, diagnostics | discover → verify → enroll → assign |
| Mapper | photo/stage, Front/Top/Side, object tree, inspector | draw/measure → bind → validate → calibrate |
| Installation Schedule | calendar windows, release, exceptions | choose approved deployment → validate conflicts |
| Shows Library | show cards, revisions, import/export | create/copy/enter show |
| Scene Editor | native/stage preview, tracks, timeline, inspector | place asset → edit cue → preview → save |
| Director | pools, structured rules, explanation | configure → simulate → inspect decisions |
| Interactions | event/encounter/opportunity policies | qualify event → request scene → test |
| Assets | search, metadata, proxy, usage, revisions | import → process → reference |
| Simulate | event script, clock, output preview, trace | scenario → run → inspect |
| Publish | validation, binding, staging, activation, rollback | compile → verify → activate |
| Operate | current scene, health, mode, intervention, safety | observe → request/stop/blackout |
| Settings | identities, permissions, audit, retention | administer safely |

## 12. Milestone alignment and dependency graph

The existing roadmap remains authoritative. This table identifies a **minimum implementable vertical slice** for each milestone, not a scope reduction:

| Roadmap | First demonstrable slice | Engine prerequisite / warning |
|---|---|---|
| GUI-00 | API contracts, durable draft store, auth/session/RBAC, audit, schema compiler fixture | Secure browser boundary and agent protocol design; avoid exposed unsafe existing endpoints |
| GUI-01 | authenticated read-only status + controlled local operations; device identity registry | Secure enrollment and capability negotiation are new; do not ship discovery-as-trust |
| GUI-02 | asset import, metadata, reference, proxy, revision | storage/processing jobs new |
| GUI-03 | measured 2.5D surface + pixel path, photo registration, binding inspector | spatial geometry new; compiler must respect existing unique resources |
| GUI-04 | editor for v2 media outputs and **existing lighting timeline** | arbitrary video tracks deferred pending runtime extension |
| GUI-05 | projector surface composition and calibration UI | mapping transforms and output composition new |
| GUI-06 | path/stage-spatial effect editing | XYZ geometry and distance evaluation new |
| GUI-07 | deterministic lighting preview + synthetic director scenario | reuse `simulation.py` and renderer; browser compositor new |
| GUI-08 | program pools, encounters, opportunities, trace | existing director logic; explainable decision records new |
| GUI-09 | immutable release, asset staging, readiness, activation, rollback | managed cache, supervisor, distribution new |
| GUI-10 | reviews, role hardening, accessibility, performance, migration tests | stable revisions/authorization required |
| GUI-11 | scoped MCP read/propose/validate with approval | reuse authoring APIs; no privileged parallel path |

**Parallelism:** GUI-01 and GUI-02 may proceed after GUI-00 contracts. GUI-03 depends on installation resource and draft schema. GUI-04 can start with existing lighting/media schema independent of advanced projector warping. GUI-05/06 must not block basic scene authoring. GUI-07 should reuse backend evaluators. GUI-08 can expose existing engine logic while visualization matures. GUI-09 needs trustworthy agent identities and artifact transport before real activation.

## 13. Recommended first Copilot implementation increment

Do **not** attempt all milestones in one pass. Begin with a GUI-00 vertical slice that leaves the engine unchanged:

1. Re-read `docs/gui-roadmap.md`, this document, ADRs, relevant schemas and tests; produce a short gap/implementation plan with proposed API contracts.
2. Choose and document frontend stack and deployment boundary in an ADR. Keep frontend isolated from `src/cueverge/core` and package runtime schemas.
3. Add durable draft project/revision persistence, minimal schema, migration mechanism, optimistic concurrency and tests. Include a compiler for one minimal draft that emits a package accepted by `load_show_package` without editor metadata.
4. Add an authenticated, **read-only** GUI API for installation/runtime status, plus a minimal frontend shell (installation selector, Overview, Shows, Operate status). Do not expose new write/live endpoints before auth/RBAC/CSRF/audit are in place.
5. Add contract/integration tests for unauthorized access, concurrent revision conflict, package roundtrip, and no hardware side effects in preview.
6. Run existing tests, Ruff, mypy; document limitations and next incremental PR. If required infrastructure is too large for one PR, break into coherent tested commits and stop at a stable boundary.

**Explicit deferments:** no automatic client enrollment, no live hardware activation from the new GUI, no video clip compositing, no geometry schema mutation, no migration of private installation assets in this first increment.

## 14. Acceptance scenarios for eventual product

- **Draft isolation:** edit a scene while v1 is live; running package hash unchanged; publish v2 and stage without activating; rollback to v1 including matching binding.
- **Two media clients:** both output agents are enrolled and report capabilities; one is missing a required artifact; activation blocked, current show unaffected.
- **Perpendicular surfaces:** model two planar surfaces at 90°, one projector mapped to both; calibration warns when optical coverage is unverified; scene output transforms are deterministic.
- **Corner LED:** one electrical pixel run traverses two perpendicular surfaces; chase preserves address order and can alternatively propagate by measured physical distance.
- **Audience scenario:** a visitor arrives during a protected interval; interaction deferred until safe point; trace identifies rejected/eligible candidates and reason.
- **Disconnected agent:** live status becomes stale/degraded; controller does not replay missed irreversible actions; reconnect reconciles safely.
- **Safety:** browser preview never sends hardware output; blackout blocks scheduled restart until authorized reset.
- **Portability:** export show, import to a second installation, resolve logical capabilities without copying first installation's IPs, credentials, or measured geometry.
- **Concurrency:** two editors save from same revision; one receives a conflict and must reconcile; no silent overwrite.
- **Legacy compatibility:** all current `shows/demo` and multi-output fixtures still load and existing tests pass.

## 15. Engineering review questions / ADR queue

These are intentionally **not** silently settled by UX design:

1. Frontend stack, hosting model, authenticated session store, and deployment topology.
2. Durable database/storage choice, asset storage roots, backup and migration policy.
3. Secure enrollment credential provisioning, mTLS or equivalent, certificate rotation/revocation, and legacy agent migration.
4. Versioned spatial schema and projector mapping transforms; compiler behavior for many-to-many output bindings.
5. Media composition extension (if/when needed), scene clock semantics, and independent audio routing.
6. Artifact distribution protocol, client pin/eviction policy, and deployment supervisor transaction semantics.
7. Explicit stop/blackout state machine, hardware watchdogs, recovery and operator override precedence.
8. Installation schedule engine and timezone/DST handling.
9. Trace reason-code schema and deterministic simulation service boundaries.
10. Package portability split: current package-embedded `installation.json` versus future installation binding snapshot.

**Implementation rule:** When a proposed feature conflicts with a current schema invariant, preserve the invariant, document the incompatibility, add an ADR and versioned migration/compilation plan, and write tests before changing runtime behavior.