# ADR 0016: Lossless hobbyist authoring and explicit runtime handover

- Status: Accepted direction; implementation is phased under UP-02 through UP-08
- Date: 2026-10-22
- Authority: [Usability implementation plan](../usability-pass.md)
- Related: [Draft compilation](0012-revision-drafts-and-compile-runtime-packages.md),
  [observation, direction, presentation](0009-observation-direction-presentation.md)

## Context and verified baseline

The first usability review found that internal engineering concepts dominate
the creative workflow. A read-only inspection of the reported pre-show found a
three-minute controller-timed scene with 56 ordinary runtime lighting cues,
legacy segment/fixture/path targets, and no editor timeline-cue metadata.
The browser derives its editable synchronized cues from editor metadata, not
from those raw lighting cues. Missing editor blocks are not missing show data.

The existing backend already accepts schema-v1/v2 scenes, legacy sweep/pulse
effects, and ten runtime timeline primitives. It also supports editor-owned
synchronized cues, composition transforms, spatial lighting layers, and baked
sequences. These representations have different responsibilities; not all
are interchangeable or directly editable through the same current UI.

Compilation uses the production loader and leaves the active controller alone.
The preview-evaluation endpoint reports timing/resources/degradation, not a
rendered LED scene frame. Spatial composition preview has its own renderer.
Published artifact activation is not running-catalog replacement.
The runtime constructor derives director/audience/opportunity state from the
loaded catalog; replacing one catalog pointer is not a safe handover.

Frigate/MQTT ingress exists, is configured at startup, and tracks connection and
message counters. The current browser lacks the corresponding setup and
end-to-end input health workflow. Runtime status has a response observation time,
but no authoritative loaded project/revision/package identity contract.

## Decisions

### 1. Lossless authoring is the compatibility boundary

- The persisted typed draft, not a particular browser pane, is authoritative.
  Existing schema-v1/v2 documents and effect forms remain supported.
- Runtime `lighting.timeline` cues are first-class scene content. UP-03 must
  project them into editable UI with stable scene/output/cue identities and
  write edits back to that representation. Do not silently copy them into a
  second authoritative metadata timeline or flatten them into a new format.
- Legacy effects remain inspectable/editable in their supported form. An
  explicit, validated conversion may be offered later; merely opening or saving
  a scene cannot convert it.
- Editor synchronized cues retain their marker-linking/composition semantics.
  Spatial author layers, transforms, composition instances, and baked output
  references retain their own identities and compilation rules.
- When multiple representations participate, describe their contribution and
  precedence. Do not hide, execute twice, or silently overwrite one of them.
  An unsupported simplified edit is an explicit limitation, not permission to
  drop its data.
- No-op editing preserves behavior, policy, bindings, IDs, timing, and unrelated
  metadata. A targeted edit changes only its intended values and necessary
  bookkeeping. Existing server behavior rejecting an identical MCP revision
  remains valid and must leave the head unchanged.
- Backend schemas/validation, compiler/loader, and production evaluators remain
  authoritative. The browser presents their results rather than implement a
  second lighting or show-selection engine.

| Representation | Source of truth | Preservation/inspection obligation |
| --- | --- | --- |
| Legacy v1/v2 effects | Scene lighting definition | Preserve effect parameters, resource, target and limits |
| Runtime timeline primitives | Scene output's `timeline` | Preserve every supported cue field, target order/precedence and deterministic seed |
| Synchronized editor cues | `editor_metadata.timeline_cues` and markers | Preserve marker links and compiled timing/space semantics |
| Spatial composition layers | Compositions, transform layers, lighting author layers | Preserve source assets, mappings, transforms and renderer inputs |
| Baked spatial sequences | Immutable artifact plus compiled graph/reference | Preserve digest, provenance, resource/indices and sampling; never present as editable original source by approximation |
| Optional editor data | Existing metadata namespaces | Preserve annotations, view state and unsupported-but-valid editor information |

UP-01 adds an original synthetic shared fixture, not a copy of production data.
It covers all ten primitive types, three target kinds, schema-v1 pulse,
schema-v2 sweep, timing/policy, limits, seeds, blends, and optional metadata.
Existing spatial/synchronization tests complement it.

### 2. Status contracts separate observation, intent, and readiness

These are additive target contracts for subsequent phases, not new endpoints
or capability claims introduced by this ADR.

- **Snapshot:** identify source and observation time; track request success,
  client receive age, and refresh failure separately from playback. UP-02 starts
  with a 2-second visible-page refresh target and marks a snapshot stale after
  10 seconds without a successful update. Use elapsed client time for receipt
  age, not subtraction of unsynchronized host clocks. A stale/error state must
  not retain an unqualified green live claim.
- **Loaded identity:** report installation, loaded project/revision/package
  digest where actually known, load time, and handover generation. Keep unknown
  values explicit for legacy/external packages. Do not derive running identity
  from the currently edited project, active artifact label, or scene name.
- **Readiness:** scope facts to the candidate/scene and requested operation.
  Separate required from optional equipment, connection from capabilities,
  and configured routing from real output health. A missing visitor source
  prevents claiming responsive behavior works, but is not automatically a
  reason to block an unrelated LED scene.
- **Input health:** distinguish disabled, operator-managed, connecting,
  subscribed, last received, accepted/rejected, freshness, audience matching,
  and loaded-rule availability. Broker connectivity and no visitors are not
  equivalent to fresh usable observations.
- **Validation:** identify exact project/candidate revision, check time, scope,
  relevant dependency/configuration identities and blocking/warning/info results.
  Edits or relevant configuration changes invalidate dependent readiness; a
  result must never silently apply to a different candidate.

Authority for device heartbeat/on-demand policy, observation freshness, and
scene timing remains server-side. The 10-second UI snapshot age does not replace
those distinct operational limits.

### 3. Guided Frigate/MQTT configuration with an operator-managed option

User-approved gate: browser-managed supported Frigate/MQTT setup, protected
controller-local credentials, and retained environment-managed configuration.

- Installation-level non-secret settings are durable and independent of show
  drafts/revisions. Store credentials separately from exported show/config data
  in controller-local protected storage, referenced rather than returned.
  Owner-only directory/file permissions or equivalent enforced ACLs are a
  minimum; do not claim encryption at rest merely from those permissions.
- Browser credential writes require administrator authorization and CSRF
  protection. Secrets are write-only and absent from readback, logs, diagnostics,
  MCP results, project exports, and example files.
- Management mode is explicit. Environment-managed sources remain visibly
  read-only in the GUI; do not silently mix GUI credentials with environment
  overrides or pretend a saved UI change updated the active ingress.
- Validate before replacing active settings. Serialize connection reconfiguration,
  stop/reconnect only the affected ingress, and report the actual outcome.
  If restart is required on a supported platform, expose it as pending rather
  than report an applied connection.
- Preserve normal event normalization/qualification and trust boundaries. Setup
  does not require video streaming or opening arbitrary integrations.
- Define secret backup/restore/rotation and permission-failure behavior in UP-04.
  Fail explicitly if protected storage cannot be enforced; do not silently
  persist secrets insecurely or fall back to blank/default credentials.

MCP configuration permissions must not be inherited from ordinary project
`direct_edit`. Any new configuration operation needs explicit administrator/
scope checks; read-only health remains installation-authorized and secret-free.

UP-04 implementation note for the input decision above: guided Frigate/MQTT
configuration, serialized reconnect, distinct input/visitor health and isolated
rule checks are implemented. GUI credential storage currently enforces POSIX
owner-only access, not encryption at rest; unsupported platforms retain
environment management. Live deployment verification remains an authorized
operator action, separate from isolated examples and regression tests.

### 4. Simple explicit Apply now, with a safe stopped-runtime boundary

User-approved gate: Save stays separate; Apply now may briefly interrupt a
running show. Do not add a wait-for-scene-completion workflow in this milestone.

- Prepare/validate the immutable exact candidate and required transfers before
  interrupting playback. Keep existing authorization, applicable approval,
  preflight and target checks, but coordinate them in one backend-owned job.
- The confirmation states the installation, candidate, and interruption effect.
  If stopped, applying leaves it stopped. If running, safely stop, install/load
  the validated catalog, then start the explicit selected or configured starting
  scene from its beginning. Do not promise exact-position continuation.
- Add a deliberate controller-owned stopped-catalog handover operation in UP-07;
  there is no existing hot-reload API to relabel. Quiesce rendering, reinitialize
  catalog-derived state, preserve authorized device identities, fence obsolete
  scene/session commands and verify the loaded candidate. Do not naively assign
  `runtime.catalog`, mutate the active package in place, or require browser-owned
  service/process manipulation.
- Persist candidate identity, operation identity, phase, progress, outcomes and
  recovery information. Resolve retries and stale/concurrent operations against
  that identity, not the latest draft by accident.
- Stop/blackout intent and authorization changes dominate queued restart work.
  Never resume output after an intervening safety action merely because apply
  originally began while playing. No new blackout-latching claim is introduced.
- Preparation failure leaves the original catalog untouched. Handover/restart
  failure reports actual stopped/partial state and offers recovery; restoration
  is not successful until the prior catalog is loaded and reported correctly.
  Do not claim that applying a catalog verifies visible physical playback.

The backend implementation and failure/restart tests belong to UP-07. If a
platform cannot complete that handover, report the limitation and keep the
end-to-end package incomplete instead of returning artifact-only success.

### 5. One Preview workspace, with safe interaction simulation

User-approved gate: combine scene playback and visitor simulation in Preview,
with advanced repeatable scenarios inside it. Physical tests remain separate.

- Scene appearance and show decisions are related tasks, not synonymous
  evaluations. Report the selected candidate, time, mapping/source context,
  limitations and actual renderer/evaluator used.
- Simulated visitor input runs in the isolated harness with fake media and
  in-memory lighting; it cannot publish live events or open real adapters.
- The everyday model is trigger, interaction, scene and timed cue. Present
  opportunities as time-limited chances to respond at an eligible boundary,
  not immediate interruption. Encounter state tracks ongoing interaction context
  and transitions. Explain their combined effect without erasing either.
- Summaries and decision traces must account for qualification, repeated-input
  coalescing, boundary waiting, expiry, cooldown, arbitration and recovery.
  Link explicit state diagrams to implicit timeout/cooldown behavior.
- Preserve existing scenario/rehearsal APIs and stored results during UI
  consolidation. Keep a separately named rehearsal only if future evidence
  establishes a distinct useful result; no duplicate lifecycle is required now.
- Raw payloads/failure injection and expected-outcome authoring remain available
  in advanced testing. Stop/blackout controls report actual simulator changes,
  not just a status message.

### 6. On-demand live monitoring, not speculative replay

User-approved gate: runtime-driven monitor with honest output coverage; no
camera streaming or guaranteed physical feedback.

- Observe the actual running catalog, scene session, time, routing and rendered
  frames. Prefer sampling the runtime's latest rendered LED state rather than
  executing a second renderer with independent time or editor data.
- Start with 2 updates/second while visible, at most 5/second for the initial
  monitor. Pause unnecessary work when hidden/off and retain bounded payloads;
  do not alter the engine's render cadence to match the monitor.
- Identify each output's evidence: controller-rendered intent, device-reported
  playback/telemetry, or genuinely measured physical feedback. Do not invent
  frames for unsupported media previews or infer physical light from UDP send.
- Report sample time/age, scene/session identity and disconnected/stale/
  unmapped/unsupported coverage. Preserve status and safety controls if rendering
  or monitoring fails.
- Authorization remains read-only and installation-scoped. Opening the monitor
  cannot start playback, arm output, or reveal private assets to an unauthorized
  client. MCP must expose equivalent supported facts, not misleadingly advertise
  a full live-rendering capability before it exists.

## Verification and consequences

UP-01's shared synthetic fixture is exercised through browser saves, MCP
read/direct-edit/proposal/validation/simulation, typed draft parsing, compilation,
loader target resolution and deterministic lighting evaluation. It verifies
preservation, not completion of the later editing/input/handover/monitor UIs.

The first baseline exposed browser zoom/snap view settings being overwritten by
defaults on save. Restore those settings on scene/space entry; retain the
regression. That is a narrow preservation fix, not early implementation of the
UP-03 redesign.

Future contract/schema/tool changes must be additive or explicitly versioned,
describe actual support, preserve existing grants and validation boundaries, and
ship with focused browser/MCP regression evidence and user/operator guidance.
UP-01 does not advertise new tools or mark future features available.
