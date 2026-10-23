# Usability pass milestone

**Status:** Complete: UP-00 through UP-09 implemented and qualified  
**Started:** 2026-10-22  
**Review closed:** 2026-10-22; all first-pass recommendation groups approved  
**Companion:** [Visual design system](gui-visual-design-system.md)

## Purpose

Adapt Cueverge for people creating fun seasonal projects with home automation,
not primarily for professional show-control engineers. Make the house,
decorations, and creative choices the focus. Preserve technical capability,
truthful status, authorization, and safe operation without requiring users to
manage implementation details in everyday workflows.

This is the living review record and implementation checklist. The first desktop
review is closed. Work through the consolidated plan below, updating its
checkboxes and verification evidence as each slice is delivered. The later
page-by-page sections retain findings and detailed acceptance scenarios;
historical proposal/deferral language does not reopen approved decisions.
This document describes targets, not functionality already delivered.

## Consolidated implementation plan

### Working agreement

- Implement in small, complete vertical slices, not a single application-wide
  rewrite. Each slice includes underlying behavior, browser workflow, affected
  MCP surfaces, documentation, and focused validation.
- Dynamic interactions are the highest product priority. Address rendering and
  shared contracts first, then prioritize existing LED content and working
  event inputs so interaction work is usable rather than cosmetic.
- Preserve authored data and supported capability, including optional metadata,
  collaboration, history, advanced logic, device identities, and recovery.
  Change everyday presentation without silently dropping internal semantics.
- Keep saved edits, simulated behavior, device-reported state, and actual runtime
  state distinct. No success-shaped substitutes for missing implementation.
- Detailed decisions that change interruption, persistence, authorization, or
  supported capability require an explicit decision before their implementation.
  The gates below are bounded implementation work, not a reason to repeat the
  completed broad review.
- Record implemented changes and verification under the relevant package;
  remaining approved targets are not claims of delivered functionality.

### Delivery order and dependencies

UP-00 through UP-09 are **complete**, with bounded qualification evidence below.
The order is the recommended work sequence;
the dependency column records actual prerequisites, not merely table position.

| Package | Outcome | Depends on |
| --- | --- | --- |
| UP-00 | Fix cross-section rendering and diagram containment | None |
| UP-01 | Establish data-preservation baselines and resolve architecture gates | None |
| UP-02 | Deliver shared navigation, truthful status, and a useful Overview | UP-00, UP-01 |
| UP-03 | Make shows/scenes direct and existing LED animations editable | UP-02 |
| UP-04 | Connect and verify a supported real event source | UP-02 |
| UP-05 | Deliver approachable, explainable dynamic interactions | UP-03, UP-04 |
| UP-06 | Simplify devices, photos, layout, and installation settings | UP-02 |
| UP-07 | Deliver lightweight, verified runtime update and recovery | UP-03, UP-05 |
| UP-08 | Deliver glanceable Operate and the live-output monitor | UP-03, UP-07 |
| UP-09 | Qualify complete hobbyist workflows and close the milestone | UP-00 through UP-08 |

UP-00 and UP-01 can proceed independently. UP-03, UP-04, and UP-06 can proceed
independently after their prerequisites. UP-05 interaction-language/design work
starts in UP-01; the complete implementation requires scene editing and working
inputs. Early validation is required per package, not deferred to UP-09.

### UP-00: rendering containment

- [x] Reproduce and fix the long-timeline sizing leak that expands neighboring
  sections and the encounter SVG. Contain timeline/table overflow locally;
  bound diagrams without clipping their meaningful content.
- [x] Verify the three-minute scene with behavior, timeline, and publication
  sections open together; inspect through the final controls at desktop and
  narrower supported widths.
- [x] Add a focused browser regression for measured workspace containment,
  diagram bounds, and visibility/reachability of lower controls.

**Exit:** the previously approximately 14,364 x 8,141 diagram stays within its
workspace and opening the timeline does not widen adjacent sections. Preserve
diagram content, scrolling, and timeline editing. This is presentation-only
unless investigation proves otherwise; record why MCP requires no change.

#### UP-00 completion evidence: 2026-10-22

- Root cause: the project's implicit grid column used content-based minimum
  sizing, allowing a 14,400-pixel timeline to expand sibling workspaces.
  Constrained it with `minmax(0, 1fr)` rather than clipping the whole project.
- Encounter SVGs retain their view box/content and now have a 420-pixel maximum
  height. Encounter and scene-transition tables have independently scrollable,
  named, keyboard-focusable regions.
- Added `web/tests/browser/workspace-containment.spec.ts`: a three-minute
  timeline, behavior editor, and publication workspace open together at
  1440 x 900, 820 x 1180, and 390 x 844. It checks measured bounds, neighboring
  section widths, end-of-timeline marker editing, keyboard table scrolling, and
  visibility/reachability of lower controls.
- Before the fix, the desktop regression measured a 13,298-pixel increase in
  diagram width when the timeline loaded. After the fix, all 18 selected browser
  tests passed:

  ```bash
  npm --prefix web run test:browser -- workspace-containment.spec.ts workspace-forms.spec.ts visual-shell.spec.ts timeline-foundation.spec.ts --workers=2 --output /home/justin/.copilot/session-state/682e79cb-7f6f-49af-b3de-2a28c152c99f/files/up00-browser-results
  npm --prefix web run build -- --configLoader runner --outDir /home/justin/.local/share/cueverge/local-demo/web
  ```

  TypeScript checking and the Vite production build passed. Used the installed
  Node runtime on PATH; session output avoids pre-existing root-owned test/cache
  directories without changing their permissions or installing dependencies.
- Verified the actual authenticated local application, not only browser fixtures.
  Created a separate synthetic `UP-00 Layout Regression` project with a
  three-minute scene; existing sandbox projects were not overwritten. With two
  project cards present, the diagram measured 491 x 279 pixels within a 563-pixel
  card; timeline content remained 14,429 pixels wide inside a 527-pixel scroll
  region. The final publication control was reachable. No hardware actions ran.
- Persistent local controller: `http://127.0.0.1:7000/`; hot-reload frontend:
  `http://127.0.0.1:4173/`. The local page is open in the shared browser at sign-in.
  Account `demo`; password remains in the owner-only sandbox credentials file,
  not this document. See [local development](local-demo.md#live-source-development).
- MCP applicability: no MCP tools, discovery, schemas, authorization, simulation,
  or authored/runtime data semantics changed. The fix is CSS containment and
  accessible table wrappers, so no MCP interface change or MCP regression is
  required for UP-00. Browser regressions cover the actual affected behavior.
- Updated the operator guide's table navigation and local-development guidance.
  Remaining terminology, graph explanations, scene-first layout, and broader
  usability changes belong to subsequent packages.

### UP-01: evidence baselines and architecture decisions

- [x] Inspect the pre-show's real lighting representation and create safe
  regression fixtures for its effects, targets, timings, policy, and metadata.
  Use isolated/authorized data; do not copy private media into committed fixtures.
- [x] Define canonical browser/MCP authoring and evaluation contracts for all
  supported existing effect representations. Explicitly preserve data that a
  simplified editor cannot yet represent.
- [x] Establish contracts for fresh/stale runtime status, loaded catalog identity,
  required-resource readiness, input health, and revision-scoped validation.
- [x] Design the dynamic-interaction mental model and concrete example flow,
  including opportunity-versus-encounter responsibilities, qualification,
  boundary waiting/interruption, expiry, repetition, and recovery.
- [x] Resolve the four gates below and record decisions here or in a focused ADR
  if they alter architecture. Obtain user decisions for material behavior choices.

| Gate | Required decision/evidence before implementation |
| --- | --- |
| Input configuration | Reuse Frigate/MQTT first; decide durable configuration, protected credentials, operator-managed compatibility, reconnect/restart requirements, and supported health reporting |
| Lightweight apply | Define optional immediate versus deferred application, behavior while running, interruption/restart confirmation, backend-owned catalog handover, acknowledgement, failure handling, and recovery; do not assume hot reload |
| Preview/testing | Define one coherent scene/interaction simulation experience; retain separate rehearsal only for a distinct useful result; separate physical tests |
| Live monitoring | Establish which outputs can be evaluated/previewed from authoritative time, which have device feedback, unsupported/unmapped coverage, sampling budgets, and permission boundaries |

**Exit:** named decisions and regression baselines replace speculation. No new
parallel show engine, fabricated telemetry, silent data migration, or
frontend-managed controller restart.

#### UP-01 completion evidence: 2026-10-22

- Accepted [ADR 0016](decisions/0016-lossless-hobbyist-authoring-and-runtime-handover.md)
  records verified current boundaries, representation compatibility, measurable
  snapshot/monitoring targets, readiness/identity/input/validation contracts,
  and the four user-approved implementation gates:
  - Browser-managed Frigate/MQTT with protected controller-local credentials and
    an explicit operator/environment-managed option.
  - Save remains separate; simple Apply now uses a safe stopped-runtime handover,
    may interrupt playback, restarts a selected/starting scene if previously
    running, and leaves a stopped installation stopped. No promise of resuming
    the exact position or waiting for scene completion.
  - One Preview workspace for scene playback and visitor simulation, advanced
    scenarios inside it, physical tests separate.
  - On-demand runtime-driven monitoring, initially 2 Hz and at most 5 Hz,
    with explicit evidence/coverage and no camera streaming or guaranteed
    physical feedback.
- Read-only production inspection found 56 ordinary runtime cues on the
  three-minute controller-timed pre-show and no editor cue metadata. Nothing
  was saved or executed there. The UI's metadata-only cue initialization and
  MCP's editor-only timeline range explain why raw effects can be overlooked.
  Their actual editable UI remains UP-03 work.
- Added original `tests/fixtures/usability-authoring.json`, shared by Python/MCP
  and browser tests. It contains all ten runtime primitives, segment/fixture/
  path targets with ordering, legacy schema-v1/v2 effects, limits, policy,
  deterministic seeds, timing and optional metadata. No private production
  document/media was copied into the fixture.
- Browser baselines prove no-op save preservation across the three runtime
  representations and isolation of a timing-offset edit. The first run exposed
  view-state loss: saved zoom 100/snap 250 became defaults 80/100.
  Fixed scene/space entry to restore saved zoom and snap; the regression now
  passes. This is a narrow preservation fix, not the scene-editor redesign.
- MCP discovery now describes `authoring_representations`, including raw scene
  reads via `get_project_draft` and editor-only `get_timeline_range` coverage.
  Clarified the range tool's description. Existing schemas/scopes/results and
  contract version 2 remain compatible; no unsupported new workflow is claimed.
- Python/MCP baseline verifies schema discovery, full draft read/direct edit,
  identical-write rejection without head change, compiler/loader definition
  preservation, deterministic frames, isolated proposal validation/simulation,
  metadata preservation, and no live publication/playback.
- Focused validation passed:

  ```bash
  .venv/bin/python -m pytest tests/unit/test_gui_foundation.py tests/unit/test_mqtt.py tests/unit/test_frigate.py tests/unit/test_lighting_timeline.py -k 'usability_baseline or mcp_controller_timed or compilation_round_trips or mcp_shared_stage or mcp_spatial_context or mcp_endpoint_requires_project_token or mcp_existing_scopes or mcp_direct_edit or mqtt or frigate or timeline'
  npm --prefix web run test:browser -- authoring-preservation.spec.ts workspace-containment.spec.ts timeline-foundation.spec.ts timeline-lighting-surface.spec.ts timeline-sync-markers.spec.ts timeline-multi-output-scene.spec.ts --workers=2 --output /home/justin/.copilot/session-state/682e79cb-7f6f-49af-b3de-2a28c152c99f/files/up01-browser-results
  .venv/bin/ruff check src/cueverge/gui/mcp_server.py tests/unit/test_gui_foundation.py
  npm --prefix web run build -- --configLoader runner --outDir /home/justin/.local/share/cueverge/local-demo/web
  ```

  Results: 22 Python tests and 17 browser tests passed; lint, frontend type-check
  and build passed. Pylance found no syntax errors or error/warning diagnostics
  in changed Python; the existing test file retains unrelated unused-name hints.
- Created a separate persistent local `UP-01 Preservation Baseline` project.
  The real authenticated browser save preserved ten raw cues, both legacy scenes,
  zoom 100 and snap 250. A scoped MCP read/proposal/validation/simulation passed
  without merging or changing the saved head; hardware-command flags were false
  and the temporary token was revoked. Shared-browser inspection also confirmed
  restored zoom/snap.
- Local services remain at ports 4173/7000. Restarted only the isolated local
  controller to load the additive MCP discovery clarification; production and
  enrolled hardware were not changed.
- Updated operations/MCP, operator, creator, architecture and local-demo
  documentation. Actual input configuration, runtime handover and monitoring
  APIs/UI remain later packages; these checked gates are decisions, not delivery
  claims for those features.

### UP-02: shared UX foundation, Overview, and shell

- [x] Keep five clear top-level destinations with task-focused workspaces and
  consistent contextual dialogs/advanced controls. Quiet account/role chrome and
  make unsupported installation switching honest.
- [x] Implement shared status/freshness and readiness presentation using UP-01
  contracts; reconcile conflicting sources and handle refresh failure/staleness.
- [x] Replace the hardcoded image placeholder with the real installation photo;
  lead Overview with actual playback/identity, actionable issues, and next steps.
- [x] Correct metric/activity colors and audit-result semantics; summarize
  repeated activity without losing evidence. Label unsupported scheduling
  unavailable rather than unconfigured.
- [x] Validate whole-page layout, keyboard/focus behavior, and fresh/stale/error/
  empty/unauthorized states; update relevant guides and MCP status descriptions/
  results where underlying semantics change.

**Exit:** users can identify what is running, what needs attention, and where to
go next without reading protocol/schema terms. Unknown identity stays unknown;
green status cannot conceal an old snapshot.

#### UP-02 completion evidence: 2026-10-22

- Replaced Overview's permanent image/schema/metric/unknown-show panels with a
  saved installation photo, actual playback or explicitly last-known state,
  controller connectivity, display shortcuts, connection facts, and compact
  activity. Setup/checklist and system details are contextual disclosures.
  Unsupported scheduling and guided input setup are stated as unavailable.
- Kept the five top-level workspaces. Removed the nonfunctional installation
  dropdown and duplicate authentication/role chrome; account details/sign-out
  are a focusable disclosure with outside dismissal and Escape focus return.
  The new primary shortcut uses a darker primary-color mix to meet contrast.
- Added independent runtime/topology reads at 2-second visible intervals,
  monotonic 10-second freshness, 8-second abort timeouts, non-overlapping live
  requests, hidden-tab pause and immediate visibility/manual recovery.
  Metadata/activity refresh at 30 seconds with explicit failure handling.
  Freshness aging does not rerender the whole workspace every second when its
  classification is unchanged.
- Overview and Installation share connection labels sourced from topology,
  including Offline/on-demand policy. Controller connected is not a readiness
  claim. Old values are visibly last-known, cached success coloring is muted,
  ordinary stale playback/mode actions are guarded, and authorized Stop/
  Blackout remain usable subject to real server/network delivery.
- Session expiry pauses automatic live reads without silently navigating away
  from unsaved editor state. Returning to sign-in is explicit and warns about
  unsaved changes; a manual retry can recover a renewed session.
- Activity no longer treats every non-`success` audit value as failure or
  paints disconnect recording green. Connection churn is grouped, full audit
  records remain reachable, and routine security/token/tool administration is
  contextual rather than dominating normal display activity.
- Added an additive REST snapshot identity envelope and `get_runtime_status`
  MCP tool. Read requires a matching runtime-installation project binding;
  unrelated/unbound projects are denied, grants are unchanged, and oversized
  results fail the existing 200 KiB boundary. `loaded_catalog` remains explicitly
  unreported/null, not guessed from an editor project or artifact state.
  Actual candidate readiness/handover remain UP-06/UP-07/UP-08 work, not implied
  by connectivity.
- Verification passed:

  ```bash
  npm --prefix web test -- --configLoader runner --no-cache src/App.test.tsx src/useLiveStatus.test.tsx
  npm --prefix web run test:browser -- overview-status.spec.ts visual-shell.spec.ts accessibility.spec.ts workspace-forms.spec.ts workspace-containment.spec.ts authoring-preservation.spec.ts timeline-foundation.spec.ts --workers=2 --output /home/justin/.copilot/session-state/682e79cb-7f6f-49af-b3de-2a28c152c99f/files/up02-browser-results
  .venv/bin/python -m pytest tests/unit/test_gui_foundation.py -k 'runtime_snapshot or gui_resources_require_authentication or mcp_endpoint_requires_project_token or mcp_existing_scopes or usability_baseline or mcp_controller_timed or compilation_round_trips'
  .venv/bin/ruff check src/cueverge/gui/contracts.py src/cueverge/gui/router.py src/cueverge/gui/mcp_server.py tests/unit/test_gui_foundation.py
  npm --prefix web run build -- --configLoader runner --outDir /home/justin/.local/share/cueverge/local-demo/web
  ```

  Results: 13 frontend unit tests, 30 browser regressions and 12 Python/MCP tests
  passed, including exact freshness/timeout boundaries, failure recovery,
  no-overlap, session expiry, inaccessible photos, narrow layouts, keyboard
  account controls, accessibility, prior containment and preservation.
  Lint, Python diagnostics/syntax, frontend type-check and build passed.
- Verified a saved photo and approximately 2-second automatic timestamp updates
  in the shared local browser. A structural development hot reload briefly left
  stale React hook state; a clean reload was checked with no page errors.
  Existing development-error audit records were retained, not erased.
- Restarted only the isolated local backend. A real read-only MCP token verified
  capability discovery, matching REST installation/runtime facts, and unreported
  loaded identity; the temporary token was revoked. Both local services remain
  running. No production state, playback, or physical output was changed.

### UP-03: show library, scene editing, and existing LEDs

- [x] Deliver compact Open show/New show flows and a scene-first workspace;
  keep one selected scene consistent across editing and preview.
- [x] Make the existing MCP-created pre-show's lighting effects visible and
  editable with contextual supported parameters and named mapped targets.
- [x] Use a dominant authoritative house/output preview and fit the initial
  timeline to the whole scene. Move metadata, history, presets, collaboration,
  composition internals, and advanced controls out of the normal path while
  retaining deliberate access and necessary conflict/permission feedback.
- [x] Prove no-op save preservation and single-parameter edit isolation across
  effects, markers, mappings, timings, policy, and metadata.
- [x] Exercise MCP-created content in the browser, read it back through MCP,
  compare authoritative evaluation before/after, and update affected authoring
  schemas/discovery/descriptions and creator/operator examples.

**Exit:** the pre-show can be found, understood, previewed, and edited without
JSON, and without dropping authored behavior. Saving does not make it live.

#### UP-03 completion evidence: 2026-10-22

- Shows is now a compact library with explicit Open show, a focused New show
  flow, and scene-first editing. Version/name/export/archive controls are in
  Show options, assets in their own disclosure, and advanced timeline/logic/
  MCP/publication/history remain accessible without dominating the main flow.
- Collaboration is opt-in under Show options > Share & review. Member roles
  are checked independently; no default collaboration presence/comment traffic
  is required to enable authorized editing. Read-only project membership still
  disables editing even when the user's global role is designer.
- Scene-native runtime lighting is edited in place, including all ten
  primitives, targets, color/brightness, timing, fades, blend, seed/precedence
  and output/scene details. Legacy v1/v2 effects retain their original forms.
  No second authoritative cue array is generated in editor metadata.
- Effects are searchable and their bars fit the whole scene. Main/advanced
  scene selection is shared; advanced compositor contributions are explicitly
  identified and linked. Existing media review components are reused with
  independent-media-clock labeling. The sticky scene heading keeps Save/Cancel
  near the user while editing contextual fields.
- Typed saved data, IDs, policy, bindings, other effects, metadata and view state
  survive edits. Dirty state is based on the editing baseline, not an incoming
  metadata refresh; the full advanced bake workflow caught and now covers this
  regression. Local edits guard scene/library/workspace/sign-out/unload changes,
  and background asset refreshes cannot overwrite them.
- Extracted the existing production lighting evaluation into one shared pure
  function used by the controller and the new REST/MCP scene preview. Existing
  persistent-baseline/spatial runtime behavior remains covered by frame tests.
  Preview includes output timing offsets and exact evaluated time, never starts
  a controller/hardware adapter, and compiles in cleaned-up temporary storage.
- Added `preview_scene_lighting` with simulation authorization and discovery,
  source revision/optional unsaved candidate, immutable installation binding,
  bounded RGB pages, actual coverage, explicit limitations and no-hardware flag.
  REST uses project membership and CSRF. Cost bounds and nonblocking two-slot
  concurrency fail explicitly, not as truncated/partial successful output.
- Normal scene preview covers raw and compiled synchronized lighting; spatial
  compositions use the existing advanced preview instead of being silently
  omitted. Existing legacy brightness-limit behavior is preserved and reported.
  Media reference proxies do not claim scene-clock or venue synchronization.
- Browser verification covers 56 source cues, complete single-edit preservation,
  invalid timing/cancel, shared selection, role checks without collaboration,
  New show opening, keyboard/accessible authoring, and slow-preview serialization
  without frame starvation. Existing advanced tests now explicitly navigate
  through Open show and the relevant disclosures rather than testing a fixture-
  only bypass of the new library.
- Final checks passed:

  ```bash
  npm --prefix web run test:browser -- --workers=2 --output /home/justin/.copilot/session-state/682e79cb-7f6f-49af-b3de-2a28c152c99f/files/up03-browser-results
  .venv/bin/python -m pytest tests/unit/test_gui_foundation.py tests/unit/test_lighting_timeline.py tests/unit/test_lighting_preview.py tests/unit/test_stage_renderer.py tests/unit/test_multi_output_lighting.py -k 'scene_lighting_preview or usability_baseline or mcp_controller_timed or compilation_round_trips or mcp_shared_stage or mcp_spatial_context or mcp_existing_scopes or lighting or stage'
  npm --prefix web test -- --configLoader runner --no-cache src/App.test.tsx src/useLiveStatus.test.tsx
  npm --prefix web run build -- --configLoader runner --outDir /home/justin/.local/share/cueverge/local-demo/web
  ```

  Results: all 63 browser tests, 33 selected Python tests, and 13 frontend units
  passed. Lint, Python syntax/diagnostics, frontend type-check and build passed.
  Boundary coverage preserves tiny positive density/output-limit values during
  unrelated edits, rejects a zero output limit, and displays ordinary percentages
  without binary scaling artifacts.
- Real local end-to-end verification created an original synthetic MCP-authored
  56-cue project, then saved one browser brightness edit. Exact MCP readback
  matched the expected full draft with no unrelated changes; canonical frames
  differed as intended. All 605 output samples and 605 photo locations were
  available. The project remains as UP-03 Scene Editor Demo; its temporary token
  was revoked. Saved head is revision 3, no package was published, and hardware
  command flags were false.
- Shared local browser is open to the saved demo with Sparkle selected and 605
  mapped locations. Both local services remain running; only the isolated local
  backend was restarted. Production data and physical output were untouched.
- Preview usability follow-up: removed the per-frame loading message that
  repeatedly inserted a line and shifted the page. The previous evaluated frame
  and a stable status line remain visible during refresh. Pixel-address colors
  now live under collapsed **LED output details**, explicitly labeled as
  non-spatial, in one horizontally scrollable, keyboard-accessible row.
  Nine focused browser tests and 13 frontend units passed, along with type-check,
  build and whitespace checks. Desktop/phone tests verify 600 pixels never wrap
  or widen the page; delayed playback verifies stable details positioning.
  Shared-browser playback also verified ten successive frame states with
  identical details positioning and diagnostics closed. This is presentation-only:
  the existing REST/MCP evaluator, permissions, schemas and result data are
  unchanged, so no MCP interface change is required.
- Updated operator/creator, operations/MCP, scale budgets, local verification,
  visual specification and milestone documentation. Event input setup remains
  UP-04 and guided interactive behavior remains UP-05; this does not claim a
  completed live apply, full-show rehearsal, or live-output monitor.

### UP-04: event-source setup and observability

- [x] Deliver discoverable installation-owned Inputs/Connections, linked from
  interaction authoring. Guide configuration of the supported Frigate/MQTT path
  using UP-01 decisions; do not advertise unimplemented integrations.
- [x] Show distinct enabled/connected/event-received/accepted/fresh/matched states,
  useful rejection reasons, source/zone identity, and concrete recovery steps.
- [x] Implement necessary backend configuration/health and protected secret
  handling, or accurately expose operator-managed steps where retained. A
  populated form or broker connection alone is not end-to-end success.
- [x] Verify input normalization through audience qualification in an isolated
  workflow, including reconnect, invalid payload, filtering, stale input, and
  no-input cases. Add MCP configuration/inspection/validation support as
  appropriate and update deployment/configuration examples without secrets.

**Exit:** a user can establish that real supported events reach the installation
without SSH or raw JSON. Deployment-specific live verification remains an
explicit authorized step, not a claim based on simulated events.

#### UP-04 completion evidence: 2026-10-22

- **Installation > Inputs & connections** exposes supported Frigate/MQTT setup,
  separate reception/visitor health, and a safe isolated example checker.
  Host/port share a compact row; optional login and advanced topic/labels are
  collapsed. Visitor-enabled shows and the interaction designer link to setup.
  Real connection enablement requires explicit confirmation; saving a form is
  never presented as proof of connection or reception.
- Settings are installation-owned and independent of show drafts. Protected
  POSIX storage separates nonsecret settings from immutable, write-only
  credentials; supports retention, rotation and explicit removal; rejects
  insecure/missing storage without anonymous fallback. Revision checks prevent
  accidental overwrite. Environment management remains explicitly read-only;
  mode precedence, unsupported-platform recovery, TLS and backup/restore are
  documented. Only the affected ingress reconnects; an in-flight controller
  delivery completes before disconnection.
- Health separates subscription, no input, received/filtered/invalid messages,
  delivery, controller acceptance, source freshness, visitor matching and
  qualification. Rejection reasons name camera/zone/label/timing issues.
  Polling preserves pending edits and exposes last-known data on failure.
  Payloads are bounded at 256 KiB and diagnostics at 20 entries; exact boundary
  tests cover both. Nonfinite timestamps are invalid, and stale observations
  cannot qualify visitors through the managed source.
- MCP adds installation-bound `get_input_source`, global-administrator-only
  `configure_input_source` with explicit `input_config`, and simulation-scoped
  `validate_input_source_payload`. Capability and authoring schemas document
  grants, fields and bounds. Isolated validation shares the normalizer and
  presence estimator, never injects live events, and carries a context digest
  invalidated by changed settings or loaded visitor rules.
- Verification: **100 focused Python tests**, **71 browser tests**, and **13
  frontend unit tests** passed, plus lint, Pylance syntax/diagnostics and
  frontend type-check/build. Tests include reconnect after initial failure
  and stream loss, filtering, stale/no input, qualification, safe delivery
  draining, explicit TLS, private credential lifecycle, authorization/CSRF,
  binding, operator compatibility and phone containment/accessibility.
  Visitor-match evidence comes directly from each controller ingestion result,
  not differences in global counters; interleaved unrelated traffic cannot be
  misattributed to this source. Legacy event outcomes and encounter/opportunity/
  controller-clock behavior remain covered.
- Real authenticated shared-browser configuration save was read back through
  MCP; isolated browser and MCP checks normalized the same two example events
  and agreed on validation context. The stock loaded sandbox honestly reports
  no visitor rules, not a matched visitor. Browser mutation fixtures enforce
  JSON/CSRF headers. The local configuration was restored to empty host, off,
  no credentials and zero real messages (configuration revision 4); the show
  remains revision 3. Temporary tokens were revoked. The local controller and
  frontend remain available with installation setup ready for review.
- No production broker, camera or physical hardware was contacted. Isolated
  connector/runtime tests prove receiver-to-qualification behavior, not
  deployment-specific live reception. Guided creative interaction authoring
  remains UP-05; verified runtime handover remains UP-07.

### UP-05: dynamic interactions, highest-priority creative workflow

- [x] Deliver discoverable interaction cards, plain-language summaries, and
  guided When/Where/Response/Timing/Repetition/Afterward authoring with direct
  focused editing for existing interactions.
- [x] Faithfully represent overlapping opportunity/encounter/pool behavior;
  keep advanced choreography intact and flag unsupported simplified editing.
- [x] Deliver safe Try it controls for supported visitor events and time
  advancement, house preview, and an authoritative explanation trace.
- [x] Cover arrival qualification, sustained presence, repeated arrivals,
  departure, expiry before a boundary, cooldown, competing requests, stale/
  missing input, completion, and return/fallback.
- [x] Share regression scenarios with MCP authoring/validation/simulation;
  verify browser/MCP round trips and confirm synthetic input cannot reach
  production automation or physical output.

**Exit:** a hobbyist can create, explain, test, and adjust a visitor response
without learning internal arbitration vocabulary. A detected event is never
misreported as guaranteed scene playback.

#### UP-05 completion evidence: 2026-10-22

- Shows now exposes **Scenes / Visitor responses / Preview**. Response cards
  describe event, scene, boundary/request timing, repeats and afterward behavior.
  Shared visitor settings and actual input setup are linked, while advanced
  definitions stay collapsed.
- The four-step guide supports boundary-based response creation/editing and
  pending-change preview. It preserves IDs, extra qualification timing,
  context, metadata, group weights and unedited scene policies. Shared completion
  changes explicitly list affected scenes and require consent; existing responses
  without a normal program retain that arrangement. State-sequence scene edits
  preserve all other transition fields. Immediate/custom/state-dependent creation
  remains advanced and is not silently converted.
- The shared Preview reuses the existing photo/color view and production
  simulation/runtime. Visitor arrival, tracking, departure, observation age and
  time advancement produce checkpoint evidence, actual scene history, waiting/
  qualifying/expired response reasons, competing priority/repeat constraints and
  normal-scene eligibility. No second browser decision or lighting engine exists.
  Qualification ticks every 100 ms, and actual scene-clock/output-offset times
  remain distinct. Rendering/step/time/media budgets are bounded and discoverable.
- Fixed prior-absence calculation clearing the departure timestamp too early;
  a regression proves quiet-time requirements now receive the actual prior gap.
  Deterministic preview IDs, normalization history, legacy event outcomes and
  saved visitor scenarios are covered. Saved scenarios
  reuse the same visitor delivery/clock path.
- MCP adds `get_interactions`, `update_interaction` (`direct_edit`) and
  `preview_interaction_workflow` (`simulation`), with shared schemas, project
  authorization, revision checks, binding validation and no-hardware flags.
  Read-only users cannot edit or run visitor tests. Browser mutations enforce
  existing origin/JSON/CSRF boundaries. Advanced controls preserve boolean visitor
  requirements and unrelated custom conditions.
- Verification: **100 focused Python tests**, **77 browser tests**, **13 frontend
  units**, frontend type-check/build, lint and Pylance signature/diagnostic checks.
  Tests cover preservation, explicit completion consent, pending-edit isolation,
  confirmation/sustained presence, repeated arrivals/cooldown, priority competition,
  expiry before a boundary, missing/stale input, completion/return, saved scenario
  parity, permissions, phone containment and modal accessibility.
- Real shared-browser authoring created **Welcome porch visitors** in the
  original **UP-05 Visitor Response Demo**. Before Save, an unsaved preview
  qualified a visitor after one second and selected welcome at five seconds.
  MCP full-draft comparison afterward verified only the requested changes;
  a saved test returned to ambient at eight seconds and sampled all 605 pixels.
  Saved head is revision 3, temporary tokens were revoked, live ingress stayed
  disabled and live playback stayed stopped. No production broker, project or
  physical output was touched. Local services remain available for review.

### UP-06: installation, devices, photos, and mapper

- [x] Deliver compact device cards/details, guided enrollment without routine
  capability JSON, contextual connection expectations, deliberate assignment,
  and protected advanced suspend/revoke actions.
- [x] Deliver photo-first gallery/upload/edit flows with one description;
  preserve photo/view identities and existing metadata. Enter Layout from the
  selected photo.
- [x] Default Layout to Select; link canvas/list selection and contextual
  properties; use explicit Add and visual LED/projector setup.
- [x] Move Strand ID, optional associations, exact coordinates, and advanced
  calibration into focused controls. Use the main canvas for scoped wiring
  checks, distinct from authored animation preview.
- [x] Reclassify 2D bounds/draw-precedence observations as diagnostics without
  suppressing genuine wiring/addressing/output conflicts.
- [x] Simplify installation name/status; make folder/tag/favorite organization,
  local users, and multi-installation administration contextual. Preserve trust,
  provisioning truth, permissions, and existing data.
- [x] Verify device enrollment/assignment and geometry/photo authoring through
  affected MCP workflows, with focused tests and updated setup/schema guides.

**Exit:** users can connect equipment and arrange decorations on their house
without navigating permanent telemetry/forms; hidden diagnostics cannot hide
actionable failures, and wiring tests remain explicitly hardware-free or armed.

#### UP-06 completion evidence: 2026-10-22

- Installation defaults to **Photos & Layout**, with separate device/input
  sections and correctly targeted input/device shortcuts. The photo gallery
  asks one description and submits explicitly after choosing a file. Native
  contextual dialogs retain optional image metadata, while description-only
  changes preserve legacy view labels, image bytes, crop, scale and linked IDs.
  Unicode metadata is safely encoded; legacy upload headers/PUT remain supported.
- Layout opens from the selected card, starts in Select, and enables point
  creation only through Add/Draw. Canvas hit targets and list selection share
  contextual properties. Empty-space selection cannot add geometry; point handles,
  lock behavior, zoom/pan/touch, linking and generators retain existing behavior.
  Pending layout edits are guarded. A partial geometry/mapping failure retains
  the saved object ID for retry rather than creating duplicates.
- Strand labels, associations, measurements, precise projector mapping,
  calibration, multi-projector overlap and shared stage coverage are contextual.
  Actual output/index conflicts remain visible, including nested overlaps and
  hidden objects. Different outputs and linked views are not falsely compared.
  Photo bounds/gaps/draw precedence are optional observations, not physical
  wiring errors for a three-dimensional display.
- Device cards retain clear status/role and actionable faults, with verbose
  telemetry in dialogs. Enrollment guides declaration review without requiring
  routine JSON editing; compatibility is not presented as connection/readiness.
  Assignment, connection policy/checks and protected suspend/revoke remain
  explicit. Settings leads with the connected installation's name; optional
  organization, extra planning records and people/access are contextual.
- MCP adds installation-bound, paginated `get_installation_setup` and
  `describe_installation_photo` with explicit `installation_edit`, global and
  project author checks, binding and photo revision validation. Project
  `direct_edit` alone cannot write installation descriptions. Device enrollment/
  assignment and geometry writes remain GUI-only with original authorization/
  validation; discovery explicitly states that boundary rather than inventing
  broader MCP control grants.
- Validation includes **57 focused Python tests**, **83 browser regressions**,
  **16 frontend units**, type-check/build, lint and Pylance checks. Coverage
  includes legacy/Unicode upload, exact metadata/geometry preservation, conflict/
  CSRF/role boundaries, capability/lifecycle/assignment safeguards, nested index
  conflicts, selection, partial-save recovery, phone layout and modal accessibility.
- Real shared-browser upload, Select/Add mapping and description edit created
  the original **UP-06 Porch setup · mapped LEDs** fixture with a three-pixel
  memory-output segment. MCP readback and separately scoped description
  write/restore verified stable photo/view IDs and all geometry unchanged.
  The saved photo is revision 4; temporary tokens were revoked. No production
  image/device, real enrollment, broker or physical output was used. Local
  services remain available; live input is off and playback remains stopped.

### UP-07: lightweight apply and real runtime handover

- [x] Implement the UP-01 apply decision as a simple home-owner workflow with
  guidance for complex cases, meaningful change summary, and proportionate
  target/interruption confirmation.
- [x] Orchestrate validation, approval where applicable, publication, transfer,
  and actual runtime catalog handover in the backend; expose durable progress
  and acknowledgement rather than require manual package-state coordination.
- [x] Preserve single-owner simplicity and shared-project authorization/approval
  safeguards; saved edits never silently activate physical output.
- [x] Implement actual retained-version recovery and test failure, disconnect,
  stale candidate, cancellation, partial handover, and restart/recovery cases.
- [x] Verify loaded catalog identity and resulting runtime behavior through
  browser/MCP workflows. Update release, service, operations, and recovery guides.

**Exit:** scene/interaction changes genuinely reach the runtime, with truthful
saved/prepared/loaded/running states. An artifact activation label is not success.
If a platform cannot complete handover, explicitly report the limitation and
remaining work; do not mark end-to-end apply complete.

#### UP-07 delivered behavior and qualification

- Everyday Apply reviews the exact saved head, meaningful change categories,
  target, approval and interruption; revisions/history remain contextual.
  Saved work never applies automatically. Durable jobs survive closing the tab;
  failures offer refresh/recovery rather than claiming success.
- Real runtime handover preserves agent channels, verifies required stop and
  prepare/play acknowledgements (timeouts/abandoned commands are not ACKs),
  replaces the catalog and visitor/scheduling components, fences in-flight
  starts, and publishes actual loaded identity/generation. Paused/stopped and
  restart recovery remain stopped with automatic input disabled. Background
  visitor timers cannot undo Stop.
- Media source IDs remain unchanged in authored data. Immutable runtime packages
  use verified original/baked digest filenames, protected cache members and
  installation-assigned mTLS downloads. Tests cover exact transfer ACKs,
  authentication/assignment, ranges (including byte zero), original-media binding,
  optional unavailable media, mixed native bakes and safe lighting preservation.
- Changed head/dependencies, live grants, token revocation, cancellation,
  partial metadata commit, rejected/lost stop/start ACKs, retained restore and
  corrupt startup recovery are exercised. Startup derives identity from the
  verified package, not an untrusted stale label. Corrupt catalog records
  require consistent-backup recovery, not silent reset.
- MCP discovers Apply/recovery schemas and exposes `apply_saved_show`,
  `get_apply_jobs`, `cancel_apply` and observed runtime identity. Normal Apply
  requires publish **and** activate; retained restore requires activate.
  Existing project/live/installation/approval boundaries remain enforced.
- Shared local browser: applied saved version 3 of the original visitor-response
  demo; the controller reported its real package digest/generation and stayed
  stopped. Live checks use only the isolated memory output.

### UP-08: Operate and live-output monitoring

- [x] Lead Operate with actual loaded identity/playback, relevant equipment/
  input problems, and discoverable protected controls; make detailed telemetry,
  millisecond seek, counters, and troubleshooting contextual.
- [x] Deliver a runtime-driven house composite and compact output/device coverage
  view using UP-01 monitoring decisions and reusable UP-03 preview components.
- [x] Distinguish intended/rendered, device-reported, and physically observed
  output; show stale/disconnected/unmapped/unsupported states honestly.
- [x] Move physical tests to a guided authorized bounded workflow; consolidate
  hardware-free patterns per UP-01 and preserve real stop/timeout semantics.
- [x] Test LED-only and mixed-output readiness, monitoring loss/performance,
  mobile operation, and relevant MCP inspection/control workflows.

**Exit:** users can see what the running installation is doing without confusing
simulation with physical verification. Monitor failure does not hide status or
safety controls.

#### UP-08 delivered behavior and qualification

- Operate leads with loaded show/playback and actionable input/equipment issues.
  Start selects an actually loaded scene, including LED-only catalogs without
  a program; unused/optional media does not block their readiness. Seek,
  modes/recovery, physical tests, simulation patterns and counters are contextual.
- The opt-in monitor reuses the photo overlay with last successful sink frames,
  including safe/blackout/physical-test sends. No second show engine or hardware
  commands are introduced. Device-reported media is separate; decoded video,
  electrical LED feedback and independent audio/physical observation are not
  claimed.
- At most 2 GUI requests/second, no overlapping polls, hidden-tab suspension,
  hard 5 Hz capture, latest-frame retention for 32 outputs and at most 4096
  samples/request (1000/output). Range/coverage, age, held/stale frames, failed
  sends, unavailable mapping and identity mismatch are explicit. Default live
  memory history is 120 frames; explicit simulation sinks retain full history.
- MCP adds bounded project-matched `get_output_monitor` and separately opted-in
  `operate_loaded_show`, requiring live_operation, current operator/admin live
  permission and explicit physical confirmation. Schema/discovery and guides
  expose the same boundaries; read/activate alone cannot transport.
- Shared browser displayed 605 actual last-sent LED colors on their saved photo
  positions. Three successive observations kept the footer at exactly the same
  Y position. Browser tests measure nonoverlap/rate and hidden-tab suspension.
  Desktop, wide desktop, tablet and phone controls are reachable; the phone
  fixture has explicit bottom clearance for its fixed demonstration warning.

### UP-09: qualification and completion

- [x] Run the end-to-end hobbyist journey: connect equipment/input, add a photo,
  map decorations, open/edit existing LEDs, author/test a visitor response, apply,
  monitor, and restore. Use isolated outputs unless live testing is authorized.
- [x] Verify existing-data compatibility, no-op/targeted-edit preservation,
  single-owner and shared-project permissions, invalid input, stale state,
  interrupted operations, restart persistence, and recovery.
- [x] Validate desktop/tablet/mobile scopes, keyboard navigation, focus return,
  readable labels/colors, and complete-page containment with all relevant panels
  open, including the long-scene regression.
- [x] Run relevant underlying, frontend, MCP, build/type, and integration checks;
  record exact commands/results and any target-hardware limitations.
- [x] Update final operator/creator/service guides, schema/configuration examples,
  visual specification, and roadmap completion evidence to match shipped behavior.

**Exit:** all package checkboxes have reproducible evidence; unmet acceptance
remains open with specific limitations. No broad "done" based on screenshots alone.

#### UP-09 completion evidence: 2026-10-23

- Complete Python suite: **424 passed**. CI-equivalent Ruff format/check and
  mypy passed; Pylance reported no diagnostics for Apply/runtime. The two
  strengthened MCP cross-project/independent-scope regressions also passed
  after the full run. No dependencies were added.
- Complete frontend units: **43 passed** across 11 files. Full Playwright:
  **85 passed**. Production frontend build/type-check passed. Browser coverage
  includes 1440 x 900 desktop, 2560 x 1440 wide desktop, 820 x 1180 tablet,
  390 x 844 phone, keyboard/accessibility, full-page containment, original
  authoring preservation and the new Apply/monitor workflows.
- Real isolated local workflow: browser Apply loaded saved visitor demo
  version 3 and stayed stopped. Controller restart recovered that verified
  package stopped before input. A temporary MCP token with read/activate/
  live_operation **without publish** inspected capabilities/runtime, started
  the loaded LED scene with explicit confirmation, read **605** last-sent mapped
  LED samples, restored the retained version while playing, observed a new
  loaded generation, and stopped playback. The saved head stayed version 3
  and the complete draft matched byte-for-byte after normalized JSON encoding:
  SHA-256 `d447ebe403fa3f867c533e0b22ab6b248251d6cbc947a981e8a6aa50c6b12291`.
  Temporary tokens were revoked; camera ingress remains disabled.
- Related operator, creator, operations/MCP, local-demo, service/recovery,
  visual specification, roadmap and schema examples are synchronized.
  The local frontend/controller remain available with playback stopped.
- Reproducible validation:

  ```bash
  .venv/bin/python -m ruff format --check .
  .venv/bin/python -m ruff check .
  .venv/bin/python -m mypy src
  .venv/bin/python -m pytest -ra
  npm --prefix web test -- --configLoader runner --no-cache
  npm --prefix web run test:browser -- --workers=2 --output /tmp/cueverge-up09-browser-results
  npm --prefix web run build -- --configLoader runner --outDir "$HOME/.local/share/cueverge/local-demo/web"
  ```

**Qualification boundary:** fixtures, original synthetic assets, protocol peers
and isolated memory/loopback outputs were used. No production deployment,
real media/LED devices, live site Frigate broker, graphics driver or physical
video/audio/light result was commissioned. Those require installation-specific
supervision. The intended-output monitor deliberately does not capture decoded
projector video or independent physical feedback. Routing changes, ambiguous
multi-root media and corrupt private-state records fail explicitly rather than
being flattened or silently reset; see the operations/service guides.

### Per-package completion record

For each package, append or link: delivered changes; decisions made; tests and
exact outcomes; browser workflow/viewport evidence; MCP tools/workflows exercised
or a reason no MCP change applies; related guide/schema/example updates; remaining
limitations. Check off only implemented and verified items.

### Scope boundaries

- No new wall-clock scheduler in this milestone; correct availability messaging.
- No removal of collaboration/history/revisions internally, no generic CAD/3D
  expansion, no unrequested media compositor, and no parallel browser show engine.
- No promise of arbitrary camera providers, camera streaming, physical feedback,
  universal hot reload, or a fully operational multi-runtime installation switcher.
- Backend work needed for usable supported inputs, canonical effect authoring,
  runtime handover, and truthful monitoring is part of the plan, with UP-01 gates;
  do not substitute cosmetic controls for those capabilities.

## Agreed direction

- Favor task-focused workspaces and contextual controls over one long page of
  forms, technical facts, and expanded sections. More disclosure triangles alone
  will not solve the density problem.
- Keep device diagnostics mostly hidden by default, accessible through a focused
  details dialog or equivalent. Relevant failures and actionable warnings remain
  visible without opening diagnostics.
- Ask for one description of what an installation photo shows or is used for,
  rather than separate everyday photo-name and named-viewpoint inputs.
  Review the existing internal fields and compatibility before implementation;
  this decision does not require deleting distinct internal identities.
- Make the photo the entrance to a dedicated layout/mapping workspace.
- Open layouts in Select mode, with linked canvas/list selection and contextual
  properties. Begin drawing only through an explicit Add action.
- Use a focused visual LED setup flow and separate everyday projector placement
  from advanced calibration.
- Keep optional fields such as Strand ID out of the primary inspector; expose
  them in focused pop-up/advanced menus when needed.
- Use the main canvas for clearly scoped wiring checks, separate from creative
  scene animation preview.
- Replace diagnostic noise with actionable guidance. Overlap in a 2D photo is
  not, by itself, a placement error: correctly mapped objects can represent
  different positions or depths in a 3D installation. Keep technical
  bounds/draw-precedence observations in diagnostics rather than routine warnings;
  preserve warnings for genuine addressing, wiring, or output conflicts.
- Consolidate management under each device, with guided enrollment, understandable
  capabilities, contextual troubleshooting, deliberate assignments, and advanced
  administration. Keep raw capability JSON out of routine setup without weakening
  trust or validation.
- Explain continuous/on-demand connection expectations in plain language and
  distinguish idle, stale, disconnected, and confirmed-connected states.
- Describe scheduling as unavailable where unsupported. A new scheduler is not
  part of the approved usability scope.
- Use the creative flow: open show, choose scene, see its lights/media, edit,
  preview, then explicitly make changes live.
- Do not make drafts and revision management prominent everyday UI concepts.
  Preserve the distinction between saved edits and the running show, and retain
  useful history/recovery safeguards behind a simpler experience.
- An MCP-created scene or LED animation must be visible, understandable, and
  editable through the same browser authoring surfaces as browser-created work.
- Use a compact show library with explicit Open show/New show actions and a
  scene-first workspace. Keep selection consistent between editing and preview.
- Make existing LED effects understandable and editable through contextual
  controls, with a dominant authoritative house preview and a timeline initially
  fitted to the whole scene.
- Keep routine scene behavior understandable while moving engineering controls
  and simulation scenario authoring into relevant advanced workflows.
- Preserve collaboration capability, but make it almost never visible in a
  normal hobby installation. Sharing, review, comments, change requests, presence,
  and member/permission administration are contextual opt-in workflows, not
  permanently expanded parts of creating a show. This presentation change does
  not remove or bypass existing authorization or shared-project safeguards.
- Give dynamic triggers, cues, and visitor-responsive behavior the highest
  usability attention: this is a core differentiator, not an advanced capability
  to hide wholesale. Run a dedicated interaction-design sub-pass for hobbyists.
- Use guided flows where they help users complete complex operations, without
  turning simple edits into mandatory multi-step procedures.
- Support an optional lightweight way to apply changes for a home installation.
  Avoid amusement-park-style release ceremony; retain truthful live state,
  authorization, and proportionate confirmation of interruption/output effects.
  Exact quick-apply behavior remains to be designed; this is not approval for
  saving to silently activate hardware.
- Explain any distinct benefit of rehearsal over preview/simulation. Consolidate
  overlapping experiences unless a separate task and outcome justify them.
- Make interaction terminology understandable without knowledge of the
  implementation. "Opportunity" and "encounter" are not self-explanatory even to
  the app's designer; explain their actual responsibilities and prefer
  intent-focused everyday labels rather than merely renaming ambiguous objects.
- Treat camera/event-source connectivity as an end-to-end usability requirement,
  not a future detail behind otherwise complete interaction forms. The user
  reports that receiving-camera-event plumbing is missing or hidden; investigate
  what is implemented, exposed, configured, and working before claiming readiness.
- Fix the verified cross-section rendering defect: long timelines must not
  enlarge encounter diagrams into giant apparently empty spaces or clip content.
- Provide a runtime-driven live-output monitor in Operate, clearly distinguishing
  intended/rendered output, device-reported state, and physical observation.
  Exact device coverage and telemetry support require implementation investigation.
- Simplify installation identity to a clearly labeled name and understandable
  operational status; move optional folder/tag/favorite organization into
  contextual controls while preserving existing metadata.

## Review tracker

| Area | Review status | Next focus |
| --- | --- | --- |
| Global shell and Overview | First-pass recommendations approved | UP-02 |
| Installation: landing and devices | First-pass recommendations approved | UP-06 |
| Installation: photos | First-pass recommendations approved | UP-06 |
| Installation: layout/mapper | Initial review complete; six recommendation groups approved | Resolve detailed interactions and diagnostic classification during implementation planning |
| Installation: setup and schedule | Initial review complete; recommendations approved | Resolve enrollment feasibility and detailed interactions during implementation planning |
| Shows | Library/scene/LED and six behavior/handover recommendation groups approved | Design lightweight apply flow and resolve preview/simulation/rehearsal overlap |
| Dynamic triggers and cues | Eight user-facing recommendation groups approved; highest priority | Discoverable working camera/event input setup; validate concrete end-to-end scenarios later |
| Operate | Initial review complete; recommendations and live-output monitor direction approved | Resolve device coverage and runtime/output evidence during implementation planning |
| Settings | Initial review complete; recommendations and installation-field refinements approved | Resolve contextual organization and input configuration implementation |
| Cross-cutting architecture and MCP | UP-01 contracts/gates accepted and preservation baselines verified | Implement additive truthful contracts in UP-02 through UP-08 |

## Installation: initial findings and proposals

Observed in the shared running app on 2026-10-22. These proposals remain open
unless explicitly included in the agreed direction above.

| UI element | Current friction | Proposed change |
| --- | --- | --- |
| Page title | Installation opens as "Network Topology" and leads with engineering concepts | Use a setup-oriented landing page, provisionally "Your setup" |
| Page organization | Device telemetry, photos, mapping, pairing, and administration share a long page | Separate everyday Devices, Layout, and Schedule tasks; keep diagnostics/calibration contextual |
| Media-agent cards | Roughly twenty technical facts compete with identity and connection state | Compact friendly name, secondary technical identifier, connection state, relevant warnings, and Details action |
| Device details | Protocols, capabilities, cache, certificates, and timing dominate the default view | Focused details dialog with basic information/settings and separate diagnostics |
| Status language | "Observed", protocol assignments, and standby/stale information require interpretation | Plain, factual connection and attention labels; do not equate connection with readiness |
| Photo collection | A small image is followed by full-width permanent edit fields | Photo-first gallery with description, Open layout, Edit, and overflow actions |
| Add photo | Two text inputs precede file selection | Select/upload the image first, then ask for its description |
| Photo editing | Crop coordinates, scale, orientation, save, and remove are always exposed | Focused editor; direct crop/rotation; exact coordinates and scale only when needed |
| Photo removal | Remove is a permanent full-width action beside routine editing | Overflow action with confirmation |
| Photo-to-layout transition | Photos and Visual Mapper appear as separate substantial sections | Open the selected photo directly in its layout workspace |

Warnings must remain visible when details are hidden. Stale observations must
not look like live readiness. Friendly names must not obscure actual device
identity during assignment or troubleshooting.

## Installation: layout/mapper review

### Browser evidence

Inspected the existing house photo, roofline LED segments, window projection
surfaces, default projector-surface form, existing garage-eave segment editor,
and browser-only whole-output wiring traversal preview on 2026-10-22.
Opened the existing segment editor without changing fields or saving.
Started and closed the explicitly browser-only traversal preview; no physical
test, alignment grid, save, update, or removal action was invoked.

- The house image with roofline and window outlines provides an understandable
  visual foundation worth retaining.
- The initial mapper state presents a projector-surface creation form and Draw
  mode, even though the installation already contains mapped objects.
- The projector inspector includes resource assignment, calibration content,
  surface model/source/revision, fit, orientation, mirrors, masks, overscan,
  native dimensions, stage dimensions, crop, brightness, delay, and control
  points. The inspector scrolls separately from the page.
- Stage coverage and overlap information appear above the editing canvas.
  Wiring discontinuity and repeated bounds/draw-precedence messages appear
  below it, followed by a separate list of objects with Edit/Remove buttons.
- The object list exposes semantic identifiers and point counts alongside
  names. Canvas selection behavior was not tested by clicking the drawing
  surface, to avoid adding points to the active creation form.
- Editing garage-eave exposes a 162-LED segment on output wled-001, first pixel
  0, strand ID, wiring direction, creation method, and additional property
  groups. Basic output assignment and LED count are meaningful setup facts;
  not every technical field should simply disappear.
- "Preview traversal" in a segment inspector actually previews all saved
  segments on the selected output, not just the selected segment or unsaved
  edits. The UI discloses this in explanatory text.
- Traversal adds another large photo view, playback controls, pixel-index
  slider, segment-order list, and a separate physical-test disclosure above
  the editing canvas. Some preview labels overlap or clip at image edges.
- The traversal preview is a wiring diagnostic, not evidence that authored
  scene animations are understandable or editable.

### Approved direction and interaction proposals

The six mapper recommendation groups were approved on 2026-10-22: Select-mode
entry; linked canvas/list/contextual properties; visual LED setup; simpler
projector placement; same-canvas wiring checks; and actionable diagnostics.
The table records that direction and supporting interaction proposals. Exact
controls, Add choices, and optional address suggestions remain implementation
design details, not separately approved requirements.

| UI element/task | Proposed experience |
| --- | --- |
| Entry and default mode | Open the selected photo in a dedicated Layout workspace, initially in Select mode with no creation form active |
| Canvas and object list | Keep one dominant photo canvas with a compact companion list of lights, projection areas, and decorations; selecting either highlights the same object and opens its contextual properties |
| Existing object editing | Select an object, see its name and useful summary, adjust its handles/properties, then explicitly save or cancel; move removal to an overflow action with confirmation |
| Adding objects | An explicit Add action offers understandable tasks such as LED lights, projection area, or decoration; guide drawing only after the user chooses a task |
| Terminology | Prefer "Name", "Light controller", "LED count", "Direction", and "Projection area"; retain exact technical identities in details where necessary |
| LED setup | Put tracing, controller assignment, LED count, start address, and a visual direction arrow in a focused setup flow; keep addressing conventions explicit and compatible with current data |
| Optional metadata | Move Strand ID and similarly optional fields into focused pop-up/advanced menus rather than consuming primary inspector space |
| Repeated LED setup | Investigate suggesting the next available start address, but never silently renumber existing segments or assume wiring order from visual proximity |
| Projector setup | Lead with projector choice and corner adjustment; keep fit/rotation handy when relevant; put advanced warp, crop, masks, timing, and exact coordinates in focused settings/calibration |
| Calibration provenance | Show verified/estimated status honestly; investigate recording calibration revision automatically rather than asking users to manage it as routine input |
| Preview modes | Use clearly labeled Layout and Wiring check modes on the same canvas; keep authored animation preview in the scene workflow, distinct from wiring diagnostics |
| Wiring check scope | Clearly identify whether checking a segment or the whole controller output, and whether the preview uses saved mappings or current edits; do not require explanatory paragraphs to establish scope |
| Labels and overlays | Prioritize selected/hovered labels, readable direction/start/end indicators, and optional address overlays instead of always drawing every identifier |
| Diagnostics | Show a compact actionable issue summary; selecting an issue highlights the relevant objects and explains the consequence and next step; move technical bounds/draw-precedence reports into diagnostics; 2D overlap alone is not an error for a 3D installation |
| Editing versus live tests | Keep simulation visibly hardware-free; make physical testing a separate explicit authorized workflow with bounded output and confirmation |
| Save feedback | Keep the edited object's name and saved/unsaved state visible; avoid requiring users to manage geometry revisions or infer which form a generic save action applies to |

The exact Add choices and settings groupings require review of supported object
semantics. Friendly presentation must not collapse distinct data types or imply
unsupported editing capabilities. Classify existing warnings before deciding
which are actionable problems versus diagnostic observations.
In particular, do not infer an invalid physical placement, wiring connection,
or output conflict solely from intersecting bounds in the photo. Preserve
genuine conflicts established by authoritative addressing, wiring, or output
data.

### Proposed acceptance scenarios

- Opening an existing photo presents the house and mapped objects, not an active
  drawing operation. A user can find garage-eave from the photo or companion list
  and identify its controller, LED count, and direction without reading IDs.
- A user can explicitly add and trace lights, enter required setup facts, and
  correct the shape before saving. Cancellation leaves saved mappings unchanged.
- Selecting a window projection area exposes relevant projector/corner controls
  without requiring a tour through unrelated stage, timing, or calibration fields.
- A wiring check clearly states its scope and data source, animates on the main
  canvas, and can be closed without losing the selection or edit state. It sends
  no hardware commands.
- Relevant diagnostics identify the affected objects and provide plain-language
  consequences; technical details remain reachable without flooding the canvas.
- Optional Strand ID metadata remains editable through advanced controls without
  occupying primary inspector space.
- Correctly mapped objects representing a 3D installation do not produce routine
  placement warnings solely because their 2D photo bounds overlap. Genuine
  addressing, wiring, and output conflicts remain visible.
- Descriptions, bindings, pixel ranges/directions, geometry identities, and
  calibration provenance survive edits and remain discoverable through MCP.
- Existing mapped objects created through MCP can be selected and edited in the
  same layout workflow. Live tests still enforce authorization and safety checks.

## Installation: device setup and scheduling review

### Browser evidence

Inspected the administrator Device Management area and Overview Schedule panel
on 2026-10-22. Did not probe endpoints, review/submit capabilities, generate
tokens, pair devices, change connection policies or assignments, suspend/revoke
devices, or run connection/hardware tests.

- Device management is below the mapper, separated into controller discovery,
  pairing, connection expectations, lifecycle, assignments, and connection tests.
  The same devices recur in multiple panels.
- Discovery asks for an "HTTPS controller origin" and offers "Probe endpoint".
  This is explicit controller discovery, not evidence of automatic LAN discovery
  or discovery of every supported device type.
- Pairing defaults to a raw capability JSON document, "Review capabilities", and
  "Create one-time token". Device choices are direct media agent and downstream
  controller; controller-owned outputs must not be assumed to use that same flow.
- Connection policy is labeled Continuous or On demand. The on-demand device
  appears as Standby, making last-seen/expected-idle information important.
- Suspend and Revoke are prominent permanent buttons in a lifecycle panel.
- Assignments use technical resource names such as projector.left. The current
  UI explains that changes apply on the next authenticated connection and
  prevents changing an assignment while its current device is connected.
- Overview's Schedule panel says "Not configured", then explains that the runtime
  does not expose an installation schedule. The frontend's Overview and show
  logic surfaces explicitly describe scheduling as unavailable; the show logic
  surface also notes that a scheduled template cannot author a clock trigger.
  No scheduling editor was available in the reviewed Installation surface.

### Approved recommendations

The device-setup recommendations and truthful scheduling-availability messaging
were approved on 2026-10-22. New scheduler functionality remains outside this
usability scope. Detailed enrollment mechanisms still require feasibility and
authorization review.

| UI element/task | Proposed experience |
| --- | --- |
| Devices landing | One compact device list with friendly identity, assignment, factual status, and relevant warnings; an explicit Add device action |
| Add device | Guided setup for the selected supported device type, explaining where its software runs, what to do there, and how to confirm connection; preserve explicit trust approval |
| Capability review | Normal setup should present supported capability facts in plain language; move raw JSON to advanced/import details; investigate a trustworthy source rather than inventing capabilities or trusting discovery alone |
| Pairing handoff | Explain the one-time code/token's purpose and where it is used; present copyable instructions at the relevant step, preserve expiration/security boundaries, and confirm success only after authenticated enrollment/connection |
| Advanced controller discovery | Move multi-controller address/probe workflow out of the default setup view; use plain labels such as Controller address and Check controller, with exact origin requirements available as help |
| Per-device details | Consolidate assignment, connection expectations, troubleshooting, and administration under the selected device instead of repeating device rows in separate page-wide panels |
| Connection policy | Explain whether the device is expected to stay connected or may be offline when unused; an expected on-demand idle state must not be represented as a verified live connection, fault, or readiness |
| Assignments | Use a clear "Used for" summary and a deliberate reassignment flow; preserve active-connection restrictions and explain when the change takes effect |
| Connection tests | Keep Check connection in device troubleshooting with progress and factual results; distinguish it from displaying a physical test pattern or waking/starting equipment |
| Suspend/revoke | Move into device management/advanced actions with plain explanations of temporary suspension versus revoking trust; retain precise backend semantics and appropriate confirmation |
| Schedule availability | Say scheduling is not yet available, not merely unconfigured; do not show an actionable setup affordance or a next-start prediction the runtime cannot support |
| Future scheduling | If added separately, begin with simple operating days/times and clear timezone, active dates, and boundary behavior; installation operating windows are not a predicted autonomous scene sequence |

### Scope and follow-up

Making existing setup usable is part of this review. Building a scheduler is a
new functional capability, not a cosmetic fix, and requires a separate explicit
scope decision. A new pairing handoff/capability-exchange mechanism may also
require backend work; review feasibility and authorization boundaries before
promising a code-only or fully automatic setup experience.

Scheduling would need authoritative timezone/DST, overnight-window,
restart/missed-window, manual-override, and start/stop behavior decisions,
alongside MCP discovery/authoring/simulation support. Do not add a browser timer
or imply that an external home-automation scheduler is configured.

### Proposed acceptance scenarios

- A user can find an existing device and its assignment/status in one place,
  then reach advanced metadata without it occupying the default view.
- A supported enrollment walkthrough requires no routine JSON editing, retains
  explicit trust/capability validation, and never reports success merely because
  a token was generated or an endpoint discovered.
- Assignment changes retain current connection restrictions, state when they
  take effect, and do not silently alter a running installation.
- On-demand idle, unexpected disconnection, stale observations, and confirmed
  connection remain distinguishable in plain language.
- Revoking trust cannot be confused with simply disabling or removing a layout
  decoration. Troubleshooting cannot accidentally start physical output.
- An installation without scheduling support is labeled unavailable, not
  unconfigured, and does not advertise a fictitious next run.

### Incidental Overview findings for later review

The same selected installation's Overview says no image is configured despite
the photo already visible in Installation. Its device summary also differs from
the Installation topology statuses observed during this review. Investigate data
sources and freshness during the Overview review; do not yet assume a root cause
or that any one display is authoritative.

## Shows: library, scene discovery, and LED editing review

### Browser evidence

Reviewed Halloween2026 and located "Halloween Pre-show 2026 — Klown Ship" on
2026-10-22. Expanded Scenes & reusable presets and Timeline editor, selected the
pre-show in the editor's scene dropdown, and selected its lighting output track.
No authored fields were changed or saved; no scene was activated, published,
or sent to hardware. Opening editor surfaces can initiate normal application
presence/evaluation requests; this was not a backend-isolated session.

- The Shows page leads with revisioned-draft isolation, a permanent create/import
  form, and "Draft projects". The existing show's title is an editable name field,
  with Export/Rename/Duplicate/Archive actions rather than a clear Open show action.
- Assets and a large expanded collaboration workspace precede scene editing.
  Comments, change requests, project roles, live-output permission, and review
  links consume space before the creative workflow.
- Scenes & reusable presets is a disclosure section. Each scene entry offers
  folder/tags/favorite and Save scene organization, not an Open/Edit scene action.
  The pre-show entry also says "No incoming references"; its precise coverage
  requires verification before using that statement as creative guidance.
- Six synthetic scenes appear alongside the pre-show. Their origin and intended
  role in this project are not yet established; do not delete or hide saved
  content automatically.
- To reach the pre-show editor, a user must expand a separate Timeline editor
  and select the scene again from a dropdown. The default was Synthetic Ambient.
- The pre-show has a lighting output track, preshow-leds, bound to wled-001,
  a 180000 ms loop end, and several timeline markers. The viewed track lane has
  no visible selectable effect blocks; selecting its header exposes only stable
  ID, kind, and disabled name/resource-binding fields.
- The editor exposes evaluated-state JSON and backend timing/resource diagnostics
  rather than an immediately understandable house/LED animation view in the
  inspected preview area.
- Repeatable simulation scenario inputs, synthetic event JSON, and failure
  injection controls appear before the timeline. Scene policies, synchronization,
  presets, transform layers, and composition controls add substantial complexity.
- The timeline uses wide horizontal scrolling with one-second labels; at the
  reviewed zoom the viewport shows only the beginning of the three-minute scene.
- The inspected UI confirms a discovery/editing gap, not its underlying data
  cause. An HTTP 422 was reported when opening the editor; do not attribute it to
  the pre-show or missing LED controls without examining the failed request.

### Approved recommendations and interaction proposals

All seven recommendation groups were approved on 2026-10-22: a compact show
library; scene-first navigation; visible/editable LED effects; a dominant house
preview; a whole-scene initial timeline view; understandable scene behavior with
contextual engineering controls; and collaboration/advanced testing outside the
normal creative path.

Collaboration is retained, but should be almost never seen in the intended
single-household hobby workflow. Preserve explicit access to shared-work
capabilities and required permission/conflict information when applicable
without showing collaboration forms or administrative panels by default.
The supporting interaction proposals below guide implementation planning;
details such as handling existing demo scenes still require investigation.

| UI element/task | Proposed experience |
| --- | --- |
| Show library | Compact show cards/rows with a clear Open show action, meaningful status, last edited time, and optional image; New show is an action, not a permanent large form |
| Terminology | Present shows and scenes as the everyday model; keep revision IDs, metadata revisions, export/import, rename, archive, and recovery history contextual |
| Show workspace | Open into the show's scenes and a useful summary; selected scene remains consistent across editing and preview instead of unrelated dropdown selections |
| Scene entries | Name, concise content/duration summary, and explicit Edit/Preview actions; move folder/tag/favorite organization into contextual controls |
| LED effect visibility | Existing supported animations appear as understandable effects/layers or timed blocks with target decorations, colors, timing, and effect summaries; do not show a misleading empty lane for existing behavior |
| LED effect editing | Selecting an effect exposes only relevant supported parameters, such as target, colors, brightness, speed/direction, and timing; advanced blending/seeds/coordinates remain available in context |
| Target selection | Select named mapped decorations from the house or list, with clear scope such as whole roofline versus one segment; preserve exact bindings and target semantics |
| Visual preview | Dominant house/LED preview using authoritative evaluation and mapping, with play/pause/scrub and clear no-hardware labeling; diagnostic JSON is optional details, not the preview experience |
| Timeline | Fit the whole scene initially, with purposeful zoom and readable time; expose detailed sequencing when needed rather than requiring users to navigate a wide technical canvas first |
| Scene behavior | Offer concise user-facing explanations of looping/completion and when a scene can run; move priorities, TTL, clock authority, protected ranges, and synchronization internals into relevant advanced workflows |
| Collaboration and permissions | Retain the capability in opt-in contextual workflows; make it almost never visible in normal hobby use, with no permanent member administration, presence, comments, or change-request workspace; preserve applicable permission/conflict safeguards |
| Simulation tooling | Keep routine visual scene preview direct; move synthetic observations, failure injection, expected-outcome JSON, and repeatable scenario authoring into an advanced testing workflow |
| Demo content | Explain any starter/example scenes and provide deliberate organization/removal; never automatically discard existing scenes because their names look synthetic |

### Required investigation and proposed acceptance

- Read and preserve the pre-show's actual authored lighting data before designing
  its editable representation. Determine whether effects originate in runtime
  lighting definitions, authoring layers, synchronized cues, or another supported
  representation; do not assume absent timeline blocks mean absent animation.
- Opening the show and choosing the pre-show must reveal what its lights do,
  which decorations they affect, its duration/loop behavior, and a safe preview
  without JSON or a second scene-selection workflow.
- Every supported existing effect must have meaningful browser inspection and
  editing. Unsupported/unrepresentable data must be explicitly identified and
  preserved, not dropped, replaced by defaults, or disguised as an empty scene.
- Inspect/save-without-changes must preserve lighting behavior. Changing one
  exposed effect parameter must preserve unrelated effects, markers, bindings,
  operational policy, and metadata.
- Verify MCP-created content through the browser and read it back through MCP;
  validate authoritative simulation before/after the intended edit. No browser
  authoring adapter may silently diverge from runtime/MCP semantics.
- Saving changes remains separate from making them live. History and recovery
  remain available without requiring routine revision management.
- A normal single-household installation can open a show, edit scenes, and
  preview without encountering collaboration panels/forms. Deliberately opening
  sharing/review still exposes the existing authorized capabilities, and shared
  editing retains required permission and conflict feedback.
- Detailed show-level behavior and publication/rehearsal/rollback flows still
  need their own browser review; these initial findings do not complete Shows.

### 2026-10-22: initial Shows direction approved

- Approved all seven library/scene/LED editing recommendation groups.
- Clarified that collaboration must remain available but be almost never seen in
  normal hobby installations; existing capability is not being removed.
- Detailed autonomous behavior and preview-to-live handover remain to be reviewed.
- Implementation remains deferred until the usability review is complete.

## Shows: autonomous behavior and preview-to-live review

### Browser and implementation evidence

Inspected Autonomous show logic designer and Rehearsal, publication, and rollback
on 2026-10-22. No templates were applied, fields changed, logic saved, analyses
or rehearsals run, or publication/staging/activation/rollback actions invoked.
Read the relevant publication component and operator-guide handover contract to
avoid proposing a misleading simplification of runtime behavior.

- The behavior designer combines program pools, audience presence, engagement
  opportunities, encounter state flow, completion/fallback/resource analysis,
  validation, and accelerated simulations in one expanded workspace.
- The pre-show is selected as initial scene and as an entry in the preshow pool.
  Pool IDs/categories, weights, cooldown milliseconds, availability, semantic
  eligibility, history exclusion, and random seed are exposed together.
- Visitor configuration mixes cameras/zones with observation freshness, arrival
  confirmation, absence grace, opportunity lifetime, event lifetime,
  qualification windows, prior absence, acknowledgement cooldown, and priority.
- Encounter state transitions and scene arbitration add another distinct model
  to the same page. Do not assume they can be removed or collapsed into one
  generic rule without preserving their different responsibilities.
- A "Scheduled main show template" is offered while explanatory text states
  that clock scheduling is unavailable.
- A panel titled "Publication-blocking logic analysis" lists fallback,
  unreachable-scene, and scheduling diagnostics. The release panel separately
  reports "Preflight passed". Severity, scope, revision, and freshness are not
  reconciled in a clear user-facing readiness summary.
- Release preparation begins with revision name, change summary, approval state,
  Save release metadata, prior-revision comparison, and preflight. The current
  Publish action is disabled; the component gates it on publisher permission,
  approved metadata, successful validation, and busy state. No publication was
  attempted.
- Rehearsal lists all scenes, including synthetic scenes, with Start, Stop, and
  Blackout rehearsal controls. The component's blackout handler sets a status
  message; the stop handler invalidates local rehearsal generation/countdown.
  Review actual simulation cancellation/control semantics before presenting
  either as evidence of a simulator state change.
- The operator guide explicitly distinguishes publication, device-artifact
  staging/activation, and running scene catalog handover. Changed scene
  definitions/autonomous logic currently require controlled package
  configuration and controller restart/recreation. An active artifact package is
  not proof the running controller has loaded it.

### Approved direction and interaction proposals

All six recommendation groups were approved on 2026-10-22: understandable
behavior summaries; approachable visitor interactions; coherent readiness;
guided updates; real runtime handover; and truthful rehearsal/recovery.
The refinements below qualify the earlier proposed flow:

- Dynamic behavior needs the most usability attention and a dedicated sub-pass.
- Guided flows should support complex tasks throughout the application.
- Home users should optionally be able to apply changes with little ceremony;
  do not force every minor edit through a professional release workflow.
- A separate rehearsal experience has no established user-facing justification
  yet. Explain its unique value or consolidate it with preview/simulation.

The table retains supporting proposals; it does not mandate separate preview,
rehearsal, preparation, or confirmation screens for every update.

| UI element/task | Proposed experience |
| --- | --- |
| Behavior entry | A concise "How this show runs" summary showing the starting scene, normal/background choices, visitor responses, and what happens when a scene finishes; accurately summarize existing logic rather than replace it |
| Ordinary behavior editing | Task-focused controls for those choices; use scenes' friendly names and readable durations; offer complex eligibility, pools, encounters, priorities, and deterministic tuning in contextual advanced editors |
| Visitor interactions | Start from a readable trigger/response summary, with source/zone and relevant cooldown visible when editing; keep detector freshness and timing internals advanced while preserving their distinct semantics |
| Templates | Show what a template will change and require explicit application; do not offer scheduled behavior as if a clock trigger exists |
| Validation | One reconciled, revision-scoped readiness summary with blocking issues, warnings, and informational limitations; link each actionable issue to the relevant setting without requiring users to interpret diagnostic codes |
| Scene reachability | Distinguish scenes unused by automatic behavior from scenes that are invalid; manual-only or retained scenes are not automatically mistakes; preserve genuine runtime validation rules |
| Save versus live | Always distinguish saved edits, prepared changes, and what the controller has actually loaded/runs; do not require users to manage those as prominent revision/package objects |
| Update workflow | Offer a lightweight apply path for ordinary home edits and guidance where complexity warrants it; perform/report required checks and real runtime handover without requiring users to coordinate publication/staging; explain relevant live/interruption effects |
| Approval/history | Keep history, named versions, audit attribution, and restore capabilities contextual; streamline single-owner confirmation without bypassing existing approval/authorization requirements or shared-project policies |
| Handover status | Report preparation, transfer, handover required, loaded, and running only from authoritative evidence; name the installation/targets and explain interruption or restart requirements before confirmation |
| Current handover limitation | Until backend orchestration exists, clearly report the required technical handover and its next steps; do not label artifact activation as applying scene/behavior edits to the running controller |
| Recovery | Contextual "Restore previous working version" flow backed by actual retained packages and runtime handover; no claim of restoration based solely on an artifact/release-state change |
| Preview and rehearsal | Explain the purpose and outcome of each experience, consolidating duplicate concepts; scene appearance and interactive behavior can be tasks within one simulation workspace; keep failure injection advanced and physical tests explicitly separate |

### Architecture and scope decisions still needed

- A hobby-friendly end-to-end live update requires investigating backend-owned
  package/catalog handover, acknowledgement, recovery, interruption boundaries,
  and durable progress, not just relabeling Activate. Decide that implementation
  scope explicitly after review; do not introduce an unsafe browser-managed
  restart or bypass device/session/permission protections.
- Design the optional lightweight apply interaction, including how users choose
  it, which changes require interruption, and what happens if a show is running.
  Preserve saved-versus-live clarity and deliberate authorization without
  imposing unnecessary review/revision screens. Do not assume automatic live
  saving, universal hot reload, or a particular restart policy.
- Investigate whether current single-owner release approval can be incorporated
  into explicit confirmation while retaining server validation, audit, and
  collaborative approval policy. This is not approval to remove backend gates.
- Establish one authoritative revision-scoped readiness result across logic,
  resources, assets, bakes, caches, permissions, and handover requirements.
  A visual preview or one passed check must not imply complete live readiness.
- Test whether generic failure-fallback diagnostics apply to an LED-only scene,
  and distinguish warning/info from true blockers; do not automatically silence
  validation because this project primarily uses lights.

### Proposed acceptance scenarios

- A user can explain the current show's normal behavior and visitor responses
  from a short summary, then edit a specific choice without navigating every
  underlying state-machine/pool parameter.
- Opening a complex existing configuration preserves all conditions,
  priorities, timings, and transitions. Simplified controls explicitly identify
  configurations they cannot represent; save does not flatten or discard them.
- Readable seconds/minutes round-trip exactly to authoritative timing values;
  unsupported scheduling is never presented as an available clock action.
- Diagnostics identify the candidate and check time/scope; edits invalidate or
  clearly mark older results, and conflicting readiness signals are explained.
- A normal owner can prepare and confirm an update without manually naming
  revisions or coordinating package states; applicable authorization, approval,
  preflight, and target confirmation still apply.
- Changed scene/logic handover is either genuinely completed and verified by the
  runtime or explicitly reported incomplete with next steps. Artifact activation,
  simulated success, and controller restart alone are not evidence of playback.
- Preparation failure, lost connections, handover failure, cancellation, and
  restore produce truthful actionable states; current live state remains visible.
- Rehearsal controls and completion claims match actual simulator behavior.
  Simulation cannot activate real devices or be confused with live stop/blackout.
- Users can explain any separate rehearsal task's advantage over preview or
  simulation. If there is no distinct useful outcome, the experience is
  consolidated rather than requiring users to learn another lifecycle.
- Exercise the relevant MCP inspection, validation, simulation, preparation,
  handover, and recovery workflows alongside browser tests for any implemented
  changes. Unsupported handover remains explicitly discoverable as a limitation.

## Dedicated sub-pass: dynamic triggers, cues, and interactions

**Priority:** Highest usability attention; initial review recorded, design and
scenario validation pending.

Dynamic responses distinguish Cueverge from a simple looping display. The goal
is to make that power approachable, not to demote the whole feature to an
advanced menu. Keep technical internals contextual while making the creative
possibilities easy to discover.

Review with concrete hobbyist tasks, such as continuing a background scene,
responding when visitors arrive, avoiding excessive repetition, and returning
to normal behavior afterward. These are design scenarios, not claims about a
particular project's configured rules.

### Initial dedicated review: evidence and semantic constraints

On 2026-10-22 inspected the configured visitor opportunity, audience input
fields, encounter graph/table, and available simulation controls. No interactions
were created or changed, no events injected, and no simulation/live actions run.
Read the creator guide's engagement-opportunity contract to distinguish delayed
selection from interruption.

- Input configuration is separated from the response: labels/camera/zone and
  detection timing are in Audience presence, the visitor response is in
  Engagement opportunities, and encounter state transitions live in another
  graph/table.
- The current visitor opportunity uses audience.arrived and Synthetic Ambient,
  with separate opportunity/event lifetimes, qualification window, minimum
  presence/prior absence, acknowledgement cooldown, priority, and audience state.
- The encounter separately maps audience.arrived from idle to engaged with a
  Synthetic Ambient scene request, then audience.departed to cooldown. Both
  models reference the same event; inspect their combined arbitration rather
  than assuming one replaces the other or that they necessarily fire twice.
- The documented engagement opportunity is consumed once at an eligible
  completion boundary, does not interrupt the active scene, and coalesces repeat
  observations rather than building a backlog. This behavior must not be
  summarized as immediate playback or a guaranteed queued scene.
- Expiry before an eligible boundary is a meaningful outcome. A successful
  detector event is not proof that an interaction qualified or a scene played.
- The current simulation entry points emphasize thirty-minute analysis,
  interruption rehearsal, or scenario construction with synthetic attributes,
  time advances, and expected-outcome JSON. No simple visitor-event test with a
  linked plain-language explanation was evident in the inspected surfaces.
- Camera/zone text fields are configuration, not proof that the live input
  integration is connected and producing fresh observations.

### Approved interaction direction and supporting proposals

The eight user-facing recommendation groups from the dedicated review were
approved on 2026-10-22. The supporting proposals below are approved direction;
exact labels and interaction controls still require design/feasibility work.
The user also explicitly approved addressing the oversized diagram/page-rendering
defect. Camera-event reception is a reported missing/hidden prerequisite, not
verified working functionality.

1. **Give interactions a discoverable home inside the show.** Use an
   "Interactions" area with readable cards describing each response and an Add
   interaction action. Do not make ordinary visitor behavior an advanced-only
   feature or require users to choose a pool/opportunity/encounter architecture
   before expressing their intent.
2. **Separate triggers from scene cues.** Explain a trigger as something that
   can cause a response, an interaction as the configured response behavior, and
   a cue as a timed action within a scene. Link the interaction to the scene and
   its actual light/media content without treating these as interchangeable
   runtime objects.
3. **Guide authoring through intent.** Start with When, Where/from which input,
   What scene/response, When it may run, Repeat behavior, and What happens
   afterward. Offer only supported triggers/conditions/actions and explain
   unavailable inputs. Existing interactions open directly for focused edits
   rather than forcing a complete wizard every time.
4. **Make response timing explicit.** Show whether behavior waits for an
   eligible boundary, can interrupt under supported policy, or expires before
   it can run. Never invent an immediate/wait choice that the selected runtime
   mechanism cannot provide. Explain configured expiry and cooldown in readable
   time units without merging their different meanings.
5. **Summarize the entire response, not just its trigger.** Include qualification,
   repeat suppression, current-scene behavior, and actual completion/fallback
   path. Do not promise to resume a paused position or return to background
   unless the saved runtime configuration implements that behavior.
6. **Provide a safe "Try it" interaction simulator.** Within the simulation
   workspace, offer supported visitor arrival/presence/departure actions and time
   advancement, alongside the house preview. Tie the result to the selected
   interaction and saved/edited candidate. Keep raw payloads and failure
   injection available in advanced testing, not as prerequisites.
7. **Explain decisions in a short trace.** Distinguish input received,
   qualification, waiting for a boundary, selected/started, cooldown, expiry,
   preemption, and input unavailable/stale. Show concrete reasons and relevant
   remaining time from authoritative results; do not label a pending opportunity
   as a scene that is guaranteed to play.
8. **Configure sources once and show their actual health.** Reuse installation
   inputs across interactions, offer friendly source/zone selection where
   discoverable, and distinguish configured/simulated/connected/fresh. A live
   observation monitor, if offered, must remain clearly separate from injected
   simulation and preserve authorization/privacy.
9. **Keep advanced choreography intact.** Provide contextual access to state
   flows, eligibility, priorities, and source details for complex existing
   configurations. A readable summary must disclose overlapping mechanisms or
   unsupported simplifications rather than silently rewriting them.

Additional approval refinements:

- Explain the meanings and differences of opportunities and encounters in
   context; do not require hobbyists to infer them from implementation terms.
- Establish a discoverable route for configuring and verifying real camera
   event reception. Camera/zone text fields and simulated events are not a
   substitute for a working input integration.
- Preserve the whole-page layout/containment requirements recorded below as
   explicit implementation work, not merely a cosmetic preference.

### Proposed acceptance walkthrough

For a supported visitor-responsive example, a hobbyist can:

1. Find the interaction and understand its trigger, target scene, response
   timing, repetition limits, and actual return/fallback behavior.
2. Identify whether its source is configured, receiving fresh live observations,
   or being simulated, without reading technical event identifiers.
3. Safely simulate arrival and see the relevant house preview plus a decision
   explanation. Advance simulated time through qualification and scene
   boundaries rather than relying on invented immediate playback.
4. Simulate repeated arrival, departure, stale/missing input, expiry, competing
   interactions, and fallback, then understand why each response did or did not
   occur.
5. Change a supported parameter, repeat the scenario, and verify the intended
   difference without losing underlying timings, transitions, targets, or policy.
6. Read back the interaction through MCP and obtain matching authoritative
   simulation outcomes. No synthetic visitor input reaches live automation or
   physical devices.

These are design/implementation acceptance scenarios, not scenarios already
executed during the review.

### Whole-page follow-up: oversized diagram and obscured lower content

The user identified a massive apparently empty box with more content below it.
The earlier review had read lower content through the accessibility snapshot,
but did not adequately investigate the blank visual area. On 2026-10-22,
followed through the diagram and lower page visually and measured its actual
browser layout:

- With the three-minute Timeline editor open, the encounter SVG measured
  approximately 14,364 x 8,141 CSS pixels in a 1,534 x 1,142 viewport.
- Its behavior-designer ancestor was approximately 14,402 pixels wide inside
  a project card only 1,217 pixels wide. An outer panel clips horizontal overflow.
  This is a layout/containment defect, not an intentionally empty workspace.
- Collapsing only Timeline editor reduced the graph to approximately
  1,146 x 650 pixels and revealed its idle/engaged/cooldown nodes and event links.
  This demonstrates cross-section sizing coupling; the precise CSS fix still
  needs implementation investigation.
- Visually inspected the transition table below the graph, the complete
  scene completion/fallback/arbitration/resources table, the bottom accelerated
  analysis controls, and the final publication section. Lower content was not
  absent; the oversized/clipped layout made it difficult to reach and interpret.
- The lower table shows the pre-show as looping, with queue interruption policy,
  explicit safe-point values, and a fallback selector. Those facts are relevant
  to explaining responses and should not be separated from interaction summaries.
- The graph uses links labeled with technical event names but does not visually
  explain direction or automatic timeout/cooldown recovery paths. A diagram
  should not be treated as a complete explanation of effective behavior unless
  it includes or explicitly accounts for implicit transitions.
- Left Timeline editor collapsed for legibility; only disclosure/scroll state
  changed. No authored configuration or hardware state was changed.

Implementation requirements added by this finding:

- [ ] Contain wide timeline content within its own scroll/zoom region. Opening a
  long scene must not widen neighboring behavior, collaboration, or release
  sections, enlarge their SVGs, or push controls outside a clipped panel.
- [ ] Bound diagram size to its workspace, provide readable labels/direction and
  a purposeful fit/view affordance where needed, and avoid giant blank regions
  before essential controls.
- [ ] Verify the whole Shows page with multiple sections open simultaneously,
  including this three-minute scene, at desktop and narrower supported widths.
  Check both vertical reachability and horizontal visibility of lower controls.
- [ ] Explain completion, queue/interruption, fallback, and implicit
  timeout/cooldown recovery near the interaction being edited, with advanced
  tables/graphs available for detailed inspection.
- [ ] Future review coverage must include a visual pass through the last
  section of the page, not only accessibility snapshots or isolated top panels.

- [x] Establish a plain-language mental model for triggers, conditions, cues,
  scenes, interruption, and return-to-normal behavior.
- [ ] Walk through an existing interaction from its input source to its visible
  response, including why it is eligible, delayed, ignored, or repeated.
- [ ] Design guided creation/editing where needed, with understandable examples
  and direct visual feedback rather than a page of state-machine parameters.
- [ ] Make a safe simulation of visitor events easy to discover and show what
  happens next and why; do not invent a fixed future sequence for autonomous logic.
- [ ] Cover no visitors, repeated arrivals, stale/missing inputs, competing
  triggers, cooldowns, scene completion, and return/fallback behavior.
- [ ] Preserve sophisticated existing configurations and expose when simplified
  controls cannot represent them without loss.
- [ ] Test whether a hobbyist can author, explain, and adjust an interaction
  without knowing pools, opportunity lifetimes, arbitration, or semantic event IDs.
- [ ] Carry the resulting semantics through MCP discovery, authoring,
  validation, and simulation, with regression scenarios shared with the browser.

### 2026-10-22: behavior and handover direction approved

- Approved all six behavior/update recommendation groups.
- Elevated dynamic triggers/cues to the highest-priority dedicated usability
  sub-pass because they are central to Cueverge's value and currently unintuitive.
- Extended guided-flow guidance to complex operations across the app.
- Requested optional easy changes appropriate to a home installation, rather
  than mandatory professional release ceremony.
- Challenged the separate rehearsal concept: establish a concrete advantage over
  preview/simulation or consolidate it. No distinct advantage was assumed.
- Detailed apply/interruption semantics and rehearsal consolidation remain open;
  implementation is still deferred.

## Shows and architecture: questions to resolve

- The pre-show's library/editor path has now been inspected in the browser.
  Effect-level inspection/editing was not available through the reviewed scene
  entry or selected track; diagnose its data representation before implementation.
- Determine which scene, project, draft, revision, and publication concepts users
  actually need to encounter. Avoid merely renaming an unchanged complex flow.
- Prefer internal revision/history safeguards over asking users to manage
  revision identifiers. Do not remove persistence or recovery mechanisms without
  investigating their responsibilities.
- Establish how saved edits, preview, and explicit live handover are communicated
  simply and accurately. Saving must never imply that a show is running.
- Identify all supported LED effect parameters and ensure existing authored
  content can be inspected and edited without requiring JSON or MCP.
- Review collaboration, permissions, assets, and advanced automation in context;
  decide which deserve dedicated workflows rather than permanent expanded UI.

## Event inputs, Settings, and Operate review

### Evidence and review coverage

On 2026-10-22 inspected Settings from its header through its final installation
record, and Operate from controller status through the final Automation state
panel. Read all lower-page output, counter, and failure sections as well as
the maintenance forms. No users/installations were created or changed, no mode
changed, and no connection, output, playback, or simulation test was invoked.

- Settings contains local-user creation and installation records, with roles,
  revision/runtime-backed labels, folders, tags, favorites, and permanent forms.
  No camera/event-source connection setup was present in the reviewed page.
- Operate leads with controller facts and technical timing/connection fields,
  then playback, numeric millisecond seek, mode/reset/blackout controls, physical
  LED tests, maintenance simulation, output telemetry, runtime counters,
  recent failures, and automation state.
- Start is disabled with a message requiring all assigned media clients to
  connect. Investigate whether that readiness scope matches the intended
  selected/running show, especially for LED-only scenes; this observation does
  not establish that the underlying gate is wrong.
- Physical LED tests and simulated maintenance patterns are permanently expanded.
  The simulated-pattern form asks for maintenance mode and arming even though
  its description says browser-only/no hardware commands.
- Runtime counters occupy significant space, while the Automation state panel is
  at the bottom. It reports ambient/dual-window-sweep runtime facts and audience,
  encounter, and opportunities as Not configured. Those facts differ from the
  pre-show/dynamic rules in the edited project; explain saved versus loaded
  configuration and investigate freshness before assigning a root cause.
- Neither reviewed page offered an event-source connection/last-event monitor
  that would let a hobbyist establish why a real camera response is not working.
- The repository does contain a Frigate MQTT ingress adapter, conditionally wired
  to runtime event ingestion and configured through environment-based settings.
  It tracks connection and received/invalid/emitted counts. This establishes
  backend capability, not that the deployed installation has it enabled, that
  its source is connected, or that its facts are available to the browser.

### Approved recommendations

All seven input/Settings/Operate recommendation groups were approved on
2026-10-22. The user additionally called out the unclear installation fields
"Home", "Home", and "Primary" and requested a live view across output devices.
The live-monitor and installation-field refinements below were subsequently
approved as direction. Detailed feasibility and controls remain implementation
design work, not delivered functionality.

1. **Make event sources first-class setup.** Add a discoverable installation-owned
   Inputs/Connections destination, linked from interaction authoring and relevant
   settings. Begin with supported integrations rather than imply generic camera,
   Home Assistant, webhook, or automatic discovery support.
2. **Guide connection and verification.** Explain prerequisites and securely
   configure the supported source; confirm connection, event arrival, camera/zone
   matching, and usable observations in separate steps. Distinguish browser-managed
   setup from operator-managed environment configuration and restart requirements.
   Preserve credential boundaries and do not put secrets into logs or show files.
3. **Expose an understandable input-to-response health path.** Show source
   enabled/connected, last fresh event, accepted/rejected input with reason,
   audience qualification, loaded interaction, and resulting response decision.
   Distinguish no visitors from disconnected or stale inputs and from saved
   rules not loaded in the runtime. Keep raw payloads/counters contextual.
4. **Make Operate glanceable.** Lead with the installation, actual loaded show/
   scene where known, stopped/running state, and actionable problems. Keep primary
   playback and protected stop/blackout discoverable. Move millisecond seek,
   clock internals, counters, and detailed telemetry into focused controls/details.
   Never replace unknown runtime identity with the currently edited show name.
5. **Separate everyday operation from testing.** Put physical output checks into
   a deliberate guided test workflow rather than a permanent large form.
   Consolidate hardware-free patterns with the simulation experience where
   appropriate; investigate whether their maintenance/arming requirements serve
   a real purpose before removing them. Physical tests retain bounded output,
   authorized mode/arming, target confirmation, and clear stop/timeout behavior.
6. **Explain readiness for the operation being attempted.** Identify which show/
   scene and required equipment block Start, with links to resolve issues.
   Investigate LED-only readiness without simply bypassing media validation.
   "Not degraded" must not be presented as proof that a stopped/unconfigured
   installation is ready to run.
7. **Keep Settings routine and administration contextual.** Move user creation,
   roles, and advanced multi-installation management behind deliberate
   administration actions. Replace permanently expanded edit/create forms with
   summaries and focused editing. Explain that an unprovisioned installation
   record is not an operational controller, and guide the next step or state the
   limitation rather than imply Create installation completes setup.

### Refinement: live-output monitor

Recommend a live visual monitor in Operate, distinct from editing simulation.
It should answer what the controller is currently driving across the selected
installation, not what an independently running browser preview predicts.

- Use the installation photo as a composite view of mapped lights/projections,
  with an optional compact device/output list or mosaic for outputs that cannot
  be meaningfully represented on that photo.
- Drive visualization from authoritative runtime scene identity, time, routing,
  and evaluated output state where available. Do not run an independent browser
  show clock or substitute the current unsaved/saved editor candidate.
- Distinguish controller-intended/rendered output, device-reported playback, and
  genuinely observed physical output. A simulated glow or projector frame cannot
  prove that a light/projector is visibly producing it.
- Identify disconnected, stale, unmapped, unsupported, or unobservable devices
  explicitly. "Across all devices" requires an inventory with honest coverage,
  not invented previews for missing device data.
- Keep status/safety controls readable when the monitor is loading or fails.
  Provide a lightweight/collapsible presentation, including on mobile, rather
  than recreate a dense permanent dashboard.
- Label live monitoring versus hypothetical simulation clearly. Monitoring must
  not start playback, activate devices, or expose live test controls implicitly.
- Assess existing telemetry/evaluation support, sampling/performance budgets,
  media-preview availability, permissions, and MCP inspection/monitoring parity.
  Do not imply that camera streaming or pixel-perfect physical verification is
  already supported or required.

### Refinement: installation identity and organization

The inspected row contains Name = Home, Folder = Home, Tags = Primary, plus a
Favorite checkbox, revision/runtime-backed metadata, and Save/Delete actions.
The user correctly flagged the visible "Home", "Home", "Primary" inputs as
unintelligible without knowing the data model.

- Lead with one clearly labeled installation name, optionally a short
  description/location if a real user need and compatible data model justify it.
- Move folder, tags, and favorite organization into optional contextual controls;
  do not present them as required setup facts for a single-home installation.
- Do not use the tag "Primary" to imply a default/active installation setting.
  If a real default selection exists, expose it as a separate explicit concept.
- Show runtime provisioning/availability in plain language and separate it from
  editable identity. Keep revision identifiers in history/details.
- Preserve existing names, folders, tags, references, and favorites during the
  redesign; do not infer that duplicate text is duplicate data to delete.
- A user must be able to identify what each exposed field changes without
  relying on placeholders or the order of adjacent unlabeled inputs.

### Proposed acceptance and implementation boundaries

- A user can reach event-source setup from an interaction that needs it and
  determine whether supported real events are arriving without SSH, raw JSON,
  or guessing from camera-name fields. If setup remains operator-managed, the
  UI accurately exposes the limitation and concrete next steps.
- Verify the whole chain for at least one supported integration: source setup,
  authenticated/configured ingress, observation normalization, audience state,
  loaded rule eligibility, and response. Use safe simulation/isolated fixtures
  for output-affecting validation; do not inject tests into production automation.
- Browser-managed integration configuration may require new backend APIs,
  durable configuration, secret storage, restart/reconnect behavior, and MCP
  surfaces. Define that work explicitly; a frontend-only form is not completion.
- Source diagnostics must distinguish adapter connection from successful
  normalization/acceptance and from actual scene selection/playback.
- Operate remains usable on supported narrow screens; diagnostics/tests cannot
  displace primary state or protected safety actions. Review actual mobile
  behavior during implementation validation.
- Whole-page Settings/Operate tests include their final sections, empty/error/
  stale states, authorization, and saved-versus-loaded mismatches.

## Overview and global shell: final first-pass review

### Evidence and coverage

On 2026-10-22 inspected Overview from header to its final Schedule panel, the
sidebar and global header, and the relevant shell/Overview rendering. Used only
the read-only Refresh status control; no account, installation, mode, playback,
or hardware-affecting control was changed.

- The installation hero always renders an image placeholder in the current
  component, despite the real house photo available in Installation. It leads
  with package compatibility and logical-resource language.
- Large metrics show configured media outputs, LED pixels, connected agents,
  and controller observation rather than a concise practical next step.
  Configured LED count has warning styling; connected-agent count gets success
  styling when runtime data exists, including the observed zero count.
- Active Show is a permanent identity-unavailable state in this component;
  Current Scene separately reports Stopped. Unknown release identity is not
  proof no show exists and should not be conflated with playback state.
- Device health uses technical resource identities. Its earlier differences from
  Installation require data-source/freshness investigation, not selection of
  whichever status looks more reassuring.
- Recent Activity lists connection/disconnection events with repeated
  target/actor names and green "Success" badges, including disconnects. Successful
  audit recording is not the same concept as healthy equipment.
- The initial snapshot timestamp remained 10:17:28 while the header displayed
  green Controller observed. Read-only Refresh advanced it to 11:49:19.
  This demonstrates old data can remain displayed during a long session; it does
  not by itself identify whether polling or backend observation was responsible.
- Schedule repeats the already-recorded unsupported-versus-unconfigured problem.
- The header repeats installation identity, controller observation, Automatic
  mode, refresh, Open Operate, account/role, and sign-out. The sidebar separately
  advertises authenticated role. Automatic mode does not mean a show is playing.
- The installation selector's current change handler is a no-op; unprovisioned
  installations are disabled. Do not present it as a fully working multi-runtime
  switcher.

### Approved final first-pass recommendations

All seven Overview/header/navigation recommendations were approved on
2026-10-22, closing the first review pass.

1. **Make Overview the home summary, not a technical dashboard.** Lead with the
   actual house photo, selected installation, running/stopped/unknown playback,
   scene/show identity where known, and actionable attention items. Move schema
   compatibility and raw resource totals into details/setup context.
2. **Give Overview and Operate different jobs.** Overview answers what is going
   on and where to go next, with clear Open show, Edit layout, Manage devices,
   Connect input, and Open controls destinations when relevant/available.
   Operate owns detailed live monitoring and authorized output controls; do not
   duplicate another large control console on the landing page.
3. **Make status and freshness truthful across pages.** Use shared authoritative
   definitions and visible stale/unknown states. Consider automatic refresh with
   explicit failure/staleness handling; manual refresh is recovery, not a
   prerequisite for trustworthy status. Separate controller reachable, equipment
   readiness, mode, loaded show, and actual playback.
4. **Use semantic color for actual meaning.** Configured LED count is not a
   warning, zero connections is not automatically healthy, and recording a
   disconnect successfully should not produce a reassuring equipment-success
   badge. Interpret whether devices are required/on-demand before raising alarms.
5. **Keep activity useful and compact.** Summarize notable changes in plain
   language with friendly names, useful relative timestamps and exact details
   available, and links to affected equipment. Keep repeated reconnect churn
   expandable and full audit results intact in details; avoid silently hiding a
   recurring problem or conflating audit result with event severity.
6. **Quiet the global header.** Keep installation context and concise meaningful
   status; expose mode without implying playback. Use a simple installation label
   for single-runtime setups until switching is actually supported. Put account,
   role, and sign-out in an accessible account menu rather than repeated
   administrative labels; keep quick access to Operate where useful.
7. **Retain clear navigation, add contextual next steps.** Keep the five familiar
   top-level destinations, distinguish setup from operation through page
   headings/shortcuts, and use an optional task-focused first-use checklist for
   missing equipment, input connectivity, layout, or runtime handover. Link to
   actual supported tasks without implying setup is complete from saved records.

### Proposed acceptance and first-pass completion boundary

- Overview shows the existing installation photo and does not invent a loaded
  release or claim that a stopped show is running.
- A stale successful snapshot cannot remain visually indistinguishable from
  fresh status. Page changes do not produce unexplained conflicting health.
- Within the initial desktop viewport, users can identify playback state,
  important issues, and the next relevant destination without interpreting
  schema names, protocol terms, or audit-result codes.
- Recent activity preserves audit evidence while communicating disconnection/
  reconnection and recurring faults accurately.
- Header/navigation remain keyboard-accessible and comprehensible at supported
  narrow widths; hidden account controls retain accessible names/focus behavior.
- This completes the first desktop discovery sweep of all main pages and the
  identified high-friction workflows. All recommendation groups are approved;
  the consolidated plan governs implementation.
- The sweep is not exhaustive browser-state/accessibility/mobile validation,
  proof of deployed integration connectivity, or end-to-end feature qualification.
  Those checks belong to implementation acceptance. Runtime handover, source
  configuration, effect editing, and monitoring still have recorded feasibility
  questions; do not imply those architectural questions are resolved.

## Milestone scope and acceptance

### Review and decisions

- [x] Establish the audience and usability direction.
- [x] Create this living milestone and revise the visual design intent.
- [x] Record the single photo-description decision and photo-to-layout direction.
- [x] Complete the initial desktop sweep of all main pages and identified
  high-friction workflows; exhaustive feature/state validation remains in acceptance.
- [x] Approve first-pass recommendations and consolidate implementation scope,
  package dependencies, and architecture decision gates.
- [x] Record concrete acceptance scenarios and package exit criteria.
- [x] Resolve UP-01 architecture gates before dependent behavior is implemented.

### Implementation, after review

- [ ] Implement the agreed page/feature changes without unrelated redesign.
- [ ] Preserve saved data, authorization, validation, safe editing, and explicit
  hardware activation boundaries.
- [ ] Update affected MCP tools, discovery, authoring schemas/descriptions, and
  simulation results alongside functional changes; explain changes requiring no
  MCP modification.
- [ ] Update relevant user/operator guides, configuration/schema documentation,
  and examples to describe delivered behavior.
- [ ] Add focused underlying and MCP regression coverage and exercise relevant
  MCP workflows, including MCP-created content edited in the browser.
- [ ] Validate actual browser workflows with real supported content, keyboard
  access, relevant viewport sizes, and empty/error/stale/unauthorized states.
- [ ] Verify that hidden diagnostics do not hide actionable failures and that
  saved edits cannot be mistaken for live changes.

## Decision log

### 2026-10-22: first pass closed and implementation plan consolidated

- Approved all seven final Overview/shell recommendations and closed the first
  desktop review pass.
- Consolidated UP-00 through UP-09 with explicit prerequisites, checklists,
  acceptance criteria, scope boundaries, and per-package completion evidence.
- Prioritized rendering containment, preservation/contracts, existing LED editing,
  working inputs, and approachable dynamic interactions before secondary polish.
- Architecture gates are explicit upcoming implementation work. No application
  implementation, live integration test, or hardware activation was performed.

### 2026-10-22: review begins

- Agreed to review and maintain this milestone before implementing.
- Agreed to revise the professional-workstation framing toward seasonal home
  creativity.
- Agreed to one photo description and photo-driven layout entry.
- Accepted the proposed show-edit-preview-live flow as a reasonable direction;
  prominent drafts/revisions are too onerous for the intended audience.
- Application behavior is unchanged by this documentation update.

### 2026-10-22: mapper review

- Recorded browser evidence and proposals for a selection-first dedicated layout,
  contextual object setup, same-canvas wiring checks, and quieter diagnostics.
- These detailed mapper proposals await user agreement; they are not yet approved
  scope or implemented behavior.
- Kept creative scene animation preview separate from the verified wiring check.
- No persistent application data or hardware state was changed.

### 2026-10-22: device setup direction approved

- Approved the device-setup recommendations, including consolidated management,
  guided pairing, plain connection expectations, deliberate assignments, and
  contextual advanced/destructive actions.
- Approved truthful scheduling-unavailable messaging, not implementation of a
  scheduler as part of this pass.
- Proceeding to the Shows browser review; implementation remains deferred.

### 2026-10-22: mapper direction approved

- Approved all six mapper recommendation groups.
- Explicitly requested that optional metadata such as Strand ID move into
  pop-up/advanced menus to conserve workspace.
- Confirmed that the current bounds-overlap message is not useful for the
  reviewed installation: accurate 2D placement represents a 3D space.
  Treat photo-bounds overlap as diagnostic information, not sufficient evidence
  of a placement problem; preserve genuine wiring/addressing/output warnings.
- Implementation remains deferred until the usability review is complete.

### 2026-10-22: device setup and scheduling review

- Recorded guided enrollment, consolidated device management, contextual
  administration, and clearer connection/assignment proposals for approval.
- Confirmed that the reviewed scheduling UI describes an unavailable capability
  as "Not configured"; proposed truthful availability wording.
- Kept implementation of a scheduler out of assumed usability scope pending an
  explicit decision.
- Flagged Overview photo/status inconsistencies for its later review without
  assigning an unverified cause.
- No persistent application data or hardware state was changed.
