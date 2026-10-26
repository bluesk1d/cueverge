# Cueverge GUI — Visual Design System & Application Shell

**Status:** Approved visual direction; implementation specification, not a claim of existing functionality  
**Version:** 1.2 — 2026-10-23\
**Companion documents:** [GUI roadmap](gui-roadmap.md) (implementation history), [usability pass](usability-pass.md) (current UX review and decisions), `docs/gui-implementation-spec.md` (product/API/safety contracts)\
**Reference images:** `docs/assets/gui/installation-overview-reference.png`, `docs/assets/gui/workspace-design-reference.png`

## 1. Design intent

Cueverge is **a creative tool for people making fun seasonal displays with home automation**. Put the house, decorations, lights, media, and creative choices ahead of the machinery that makes them work. Technical sophistication should support an approachable experience, not require users to think like show-control engineers or manage an industrial authoring workflow.

Favor clear tasks, photo-led workspaces, contextual editing, and plain language.
Keep troubleshooting and advanced configuration available through focused details
views rather than permanent telemetry or long forms. More disclosure triangles
alone are not a sufficient usability improvement. Actionable failures, stale
status, and safety controls must remain discoverable.

Dynamic triggers, cues, and visitor-responsive behavior are a core creative
differentiator and require the highest usability attention, not wholesale
removal into advanced settings. Use concrete examples, understandable behavior
summaries, and safe interactive feedback. The
[dedicated interaction sub-pass](usability-pass.md#dedicated-sub-pass-dynamic-triggers-cues-and-interactions)
will establish the detailed experience.

Use guided flows for complex operations where they help; avoid mandatory
multi-step ceremony for simple home-project changes. Provide an optional
lightweight apply experience with truthful saved-versus-live state and
proportionate confirmation, while preserving backend authorization and output
safety. Do not imply unsupported hot reload or silently make saved edits live.
Preview, simulation, and rehearsal must have understandable purposes; consolidate
overlapping experiences unless a distinct useful task justifies separation.

Retain the restrained charcoal foundation, modest blue/cyan accents, accessible
contrast, and semantically meaningful feedback. Design for dark mode first;
light mode is not required for the initial shell. The
[usability pass](usability-pass.md) records the completed first review pass and
its [implementation plan](usability-pass.md#consolidated-implementation-plan).
UP-00 containment, UP-01 preservation/contracts, UP-02 shared status/Overview,
UP-03 scene-first/raw-lighting editing, and UP-04 camera input setup/health are
complete. UP-05 adds discoverable response cards, a focused four-step guide, and
one Preview workspace for scenes and isolated visitor tests. Advanced matching
is preserved rather than flattened into misleading simple controls.
UP-06 separates photo/Layout, device and input tasks, defaults Layout to Select,
and moves metadata/telemetry/administration into contextual dialogs/disclosures.
Photo descriptions are everyday inputs; independent legacy view labels remain
metadata, not a second description field. Photo-space overlap is not a wiring fault.
Connection settings, optional broker login, reception diagnostics and
safe example checks use focused disclosures instead of an always-expanded form.
UP-07 puts explicit **Apply saved changes** beside everyday saved/live guidance.
Keep exact versions, immutable identities and retained recovery available without
turning them into a primary editing model. The review explains change categories,
target, approval and restart consequences; errors/progress are durable.
UP-08 leads Operate with friendly loaded-show/playback and input/equipment issues.
Stop/Blackout remain discoverable when monitoring fails. The opt-in monitor uses
actual successful sink frames on saved photo mappings, stable in-place status,
and explicit age/held/stale/coverage labels. Never substitute a second renderer,
wrapping LED row, artifact label or unobserved projector image for live evidence.
Position, raw counters, wiring tests and maintenance patterns are contextual.
Later revised targets
below are not claims about current application behavior.

**Visual reference hierarchy:** This document's functional and safety rules > `gui-implementation-spec.md` > approved image composition. The generated mockups contain fictional data, inconsistent navigation, impossible runtime affordances, and incidental text. Do **not** reproduce them as screenshots, embed them as backgrounds, or treat their labels as authoritative.

### Non-negotiable product rules

- One active scene per installation at a time under the current runtime model; multiple outputs may participate within that scene.
- Autonomous scene choices are not a fixed upcoming schedule. Installation schedules govern when a show runs, not which scene the director will select at a future timestamp.
- Read-only overview must never expose working transport buttons that bypass auth/preflight. Hardware-affecting operations belong behind explicit authorized controls in Operate.
- Editing and simulation are safe by default; physical tests and live changes require deliberate arming, authorization, preflight, and confirmation. Stop and blackout must be discoverable from Operate. Never imply current blackout is latched if the runtime does not yet implement latching.
- Draft, published, staged, deployed, and active remain distinct internal states. Do not make draft/revision management a prominent everyday workflow; communicate saved changes versus the running show simply and accurately. Never present saved edits as running.
- Existing engine schemas and Python timing/director/lighting logic remain authoritative. The browser is not a second show engine.

## 2. Information architecture and navigation

Top-level navigation is exactly: **Overview, Installation, Shows, Operate, Settings**. Use visually quiet section groupings if helpful, but do not add Scenes, Director, Assets, Schedule, or Devices as competing top-level workspaces. Those belong within their owners:

- **Overview:** installation summary, active show and scene, health, recent activity, shortcuts.
- **Installation:** setup-oriented entry into devices, photo-led layout, and schedule tasks. Calibration and diagnostics belong in the relevant task/device context. Exact grouping and labels remain under usability review.
- **Shows:** show library and a direct scene-editing flow: open show, choose scene, inspect lights/media, edit, preview, explicitly make changes live. Show-level behavior, assets, history, and advanced administration remain available in context; exact grouping remains under review.
- **Operate:** live status, director state, explicit authorized controls, activity, safety.
- **Settings:** controller/application preferences, permissions, maintenance.

Global header: product identity; installation selector; connection/health; operational mode; active show (when present); user/account; navigation to Operate. A collapsed navigation rail is supported on narrow desktops. On mobile, prioritize Operate, Overview, and basic health; complex authoring may be desktop-only initially.

UP-02 uses a static installation label until runtime switching is supported,
and a keyboard-accessible account disclosure rather than repeated role labels.
Status identifies controller connection, not installation readiness. Live reads
refresh at 2 seconds, age out at 10 seconds, and have an 8-second timeout; stale,
failed, and expired-session states are explicit across workspaces.

## 3. Design tokens — initial values to validate in browser

Implement tokens as CSS custom properties, with Tailwind semantic aliases if used. Treat values as **starting design decisions**, not immutable pixel-perfect extraction from AI images.

| Token | Dark value | Usage |
|---|---|---|
| `--cv-bg` | `#0B111B` | App canvas |
| `--cv-sidebar` | `#0E1723` | Navigation rail |
| `--cv-surface` | `#111D2B` | Cards, panes |
| `--cv-surface-raised` | `#172537` | Hover/selected surfaces |
| `--cv-border` | `#26384A` | Subtle separators |
| `--cv-text` | `#EDF4FA` | Primary text |
| `--cv-text-muted` | `#A0B0C2` | Secondary labels |
| `--cv-text-faint` | `#73879C` | Captions and metadata |
| `--cv-primary` | `#287EF0` | Primary actions, selected nav |
| `--cv-primary-hover` | `#4895F5` | Primary hover |
| `--cv-focus` | `#69B6FF` | Visible keyboard focus |
| `--cv-success` | `#28D39B` | Confirmed healthy state |
| `--cv-warning` | `#F2AF45` | Degraded/attention |
| `--cv-danger` | `#F0525D` | Error/destructive/safety |
| `--cv-info` | `#36BED6` | Informational signals |
| `--cv-accent-violet` | `#A57AF0` | Secondary creative accent only |

**Color policy:** Green means positively confirmed healthy, not merely discovered or presumed online. Amber means attention required. Red means error, destructive action, or safety intervention. Never rely on color alone; pair with icons and text. Media/lighting track colors identify content or track categories, not device health.

**Typography:** Inter or a comparable locally bundled/open-licensed sans-serif; system font fallback. Default body 14px, compact metadata 12px, workspace title 24px, major page heading 28px. Maintain readable contrast; no 9px imitation of the reference images. Monospaced numerals for timecodes, counts, and frame-accurate values.

**Spacing:** 4px base grid; 8/12/16/24px common spacing. Card radius 10px, control radius 7px, panel borders 1px. Avoid excessive gradients, glow, heavy shadows, and large dashboard-style icon tiles.

Text inputs, selects, and textareas share the dark control surface, border, radius,
and visible keyboard focus. Stack field labels above controls with a 6px gap;
keep checkbox and radio labels inline. Textareas resize vertically without
widening their panels. Topology cards place connection status below the device
identity and wrap long names and statuses rather than compressing them together.
Workspace layout rules apply in normal and forced-color modes; reserve
forced-color overrides for system colors and contrast.

Wide timelines and technical tables must scroll within their own bounded
workspaces, not expand neighboring sections or their diagrams. SVGs need
container-bounded sizing; a long scene must not turn an encounter diagram into
a giant apparently blank region. Verify entire pages with multiple sections
open, including access to their final controls, not only isolated component
screenshots.

**Layout targets:** Desktop design reference 1440×900 and 2560×1440; sidebar 224px expanded, 64px collapsed; top header 56px; content padding 20–24px. Resizable editor panels have minimum widths and persisted local preferences. Breakpoints should be content-driven rather than simply matching the reference image dimensions.

## 4. Component vocabulary

Build reusable primitives rather than screen-specific styling:

- `AppShell`, `GlobalHeader`, `InstallationSwitcher`, `PrimaryNav`, `WorkspaceHeader`, `Breadcrumbs`.
- `Panel`, `PanelHeader`, `Metric`, `StatusText`, `HealthSummary`, `ActivityRow`, `EmptyState`, `ErrorState`, `Skeleton`.
- `PrimaryButton`, `SecondaryButton`, `GhostButton`, `DestructiveButton`, `IconButton`, `Tabs`, `SegmentedControl`, `SearchField`.
- `InspectorPanel`, `PropertyField`, `MediaThumbnail`, `Timecode`, `TrackHeader`, `TimelineRuler`, `TransportReadout` (later milestones).
- `SafetyControlGroup`, `PreflightDialog`, `ArmSessionBanner`, `BlackoutStateBanner` (later Operate milestone).

Prefer Radix primitives and shadcn-style local component ownership, rather than a large pre-themed dashboard kit. Icons: Lucide. Focus, disabled, loading, stale, offline, unauthorized, and error states must all be visually distinct. Motion should be restrained and honor reduced-motion preferences.

## 5. Screen A — Installation Overview (first implementation target)

**Goal:** A trustworthy glanceable answer to *what installation is selected, what is running, is it healthy, and where do I go next?* It is **not** a transport console.

### Desktop composition

1. Persistent sidebar and global header.
2. Page header: a plain-language setup title, installation name, short location/context description, and clear device/layout destinations (only when routes exist). Do not lead everyday setup with "Network Topology"; exact labels remain under review.
3. Optional installation hero image or schematic, sourced from a real installation photo or deliberate placeholder. Avoid generic stock scenes as product data. If no photo, use a useful empty state.
4. Compact health summary: enrolled/online devices, configured media outputs, LED pixels, relevant sensors, active alerts. Counts are derived from actual API fields; do not invent numbers or show fake live green dots.
5. **Active Show** panel: name, release/deployment state, mode, link to show/Operate. Explicit no-active-show state.
6. **Current Scene** panel: actual current scene if running; show position/progress only when the controller reports a trustworthy time. Otherwise show `Position unavailable` or `Stopped`. No decorative pause/skip controls in Overview.
7. **Device Health** panel: compact friendly identity, factual connection state, and visible relevant warnings; link to Installation > Devices. Technical identifiers remain accessible. Protocol, capability, cache, certificate, and timing details belong in focused device details/diagnostics, not default cards.
8. **Recent Activity** panel: timestamped authoritative events; empty state if unavailable.
9. **Schedule Summary**: next known installation schedule window if scheduling is implemented; otherwise omit or mark `Not configured`. Never predict next autonomous scene.

### Required states

- First run/no installation; installation with no enrolled devices; installation with no show; stopped; preparing; running; degraded; disconnected/stale controller; partial API data; insufficient permissions; loading/error.
- Do not equate controller connection with all devices online.
- Use a `Data freshness` timestamp if status can be stale; a stale green icon must not look live.
- Cards are responsive and reorder to prioritize health and current show on narrow viewports.

### Acceptance

At 1440×900 the user can identify the selected installation, whether a show is running, current scene if available, and whether any required device is degraded without scrolling. With no real show or device data, the page is still informative and honest. Read-only Overview interactions cannot activate outputs.

## 6. Screen B — Scene Editor (design target; later milestone)

The scene editor should make a scene's lights and media immediately understandable,
not default to a dense engineering workspace. Prioritize a visual preview and
contextual controls for the selected content. Asset browsers, inspectors, and a
layered timeline should appear where the task needs them rather than all
competing for attention by default. Detailed layout remains under usability
review. Separate scene editing from show-level autonomous behavior.

MCP-created scenes and LED animations must be as visible, understandable, and
editable as browser-created content. Revision identifiers and draft management
must not obstruct the everyday edit/preview flow; retain history and recovery
without implying that saved changes are live.

For the initial runtime-compatible slice, expose existing media outputs and existing timed lighting cues. Do not imply support for arbitrary multi-clip video composition, crossfades, or video layering until the engine explicitly supports it. Asset references and preview use backend-authoritative data. Timeline colors may distinguish Video, Audio, Lighting, Spatial Effects, Events, and Automation, but unsupported tracks must not appear editable. The mockup's dialogue audio and scripted upcoming scenes are illustrative mistakes, not requirements. A sample may use silent character performance; other shows can include audio where the engine permits.

Preview is **simulation by default**; hardware rehearsal requires explicit authorization and arming. Editing must never mutate the active package.

## 7. Screen C — Installation Mapper (design target; later milestone)

Enter a dedicated layout workspace from the selected installation photo. Present
photos as a gallery, not permanently expanded metadata forms. Ask for one
description of what a photo shows or is used for, rather than separate everyday
photo-name and named-viewpoint inputs. Preserve internal identities and
compatibility as needed. Photo editing should prioritize the image and direct
crop/rotation controls; exact coordinates and scale belong in advanced controls
when needed.

Prioritize a dominant editable photo/orthographic canvas and contextual properties
for the selected decoration or surface. Object hierarchy and diagnostics should
not crowd the everyday placement task. Detailed layout remains under usability
review. The underlying hybrid photo + 2.5D stage representation retains true
physical XYZ geometry with planar surfaces and ordered LED paths projected into
2D photo/front/top/side views. A registered photo is a view, not the authoritative
geometry. Keep physical surface, logical fixture, device output, pixel
addressing, and mapping transform distinct internally without requiring users
to manage them all at once. Support perpendicular surfaces and paths wrapping
around corners; no generic aerial landscaping planner or full mesh modeler.

Canvas should support select/pan/measure/surface/path/landmark tools; zoom, snap, selection handles, undo/redo, validation. Hardware test pattern buttons must not be active in edit mode; require an armed, time-limited calibration session with bounded outputs.

## 8. Screen D — Operate (design target; later milestone)

Evaluate a live-output monitor as part of the
[usability pass](usability-pass.md#refinement-live-output-monitor): a
runtime-driven installation composite with contextual device/output views.
Keep it distinct from editor simulation. Clearly separate controller-intended
output, device-reported state, and verified physical observation; show stale,
disconnected, unmapped, and unsupported outputs honestly. A rendered preview
must not imply confirmed physical output or run on an independent show clock.

Mobile-friendly, glanceable operational console. Show actual running mode, current scene, director state, audience inputs, device health, recent activity, and safe authorized controls. Visually and semantically distinguish:

- `Hold Auto`: stop autonomous selection without implying playback has stopped.
- `Graceful Stop`: stop at permitted boundary.
- `Immediate Stop`: stop as soon as controller can enforce safe output behavior.
- `Blackout`: highest-priority output suppression, with latched state **only after backend implements it**. Software blackout is not electrical isolation.

Require backend authorization, explicit confirmation/preflight as appropriate, controller acknowledgement, and display of pending/failure state. Never wire a frontend-only toggle to a hardware claim. Do not display an invented fixed next-scene queue for an autonomous show. Use a prominent but protected safety section; do not make destructive actions look like ordinary playback transport.

## 9. Responsive behavior

- **Desktop:** full authoring layout; adjustable panels; compact but readable density.
- **Tablet:** collapse sidebar; stack lower-priority panels; keep Overview and Operate functional.
- **Phone:** prioritize live status, alerts, scene identity, and safe operator actions; don't squeeze a full timeline or mapper into a tiny viewport. Offer clear desktop-required affordance for unsupported authoring.
- Use actual browser testing, not the miniature generated mobile mockup as a specification.

## 10. Frontend implementation guidance

Recommended: React + TypeScript + Vite, React Router, Tailwind semantic tokens, Radix/shadcn components, TanStack Query for server state, Zustand only for local editor/UI state, Lucide icons. Keep API clients typed and backend schemas authoritative. Serve built static assets from FastAPI if aligned with the repository's deployment plan. Avoid Next.js, Electron, a separate JavaScript show engine, or premature 3D rendering.

Use mock data **only in explicitly isolated story/demo fixtures**; never silently substitute invented device/show status in production views. Add component states/stories or test fixtures for loading, empty, error, stale, unauthorized, and running. Preserve accessibility, keyboard navigation, and semantic labels.

## 11. Initial implementation handoff and checkpoints

**Checkpoint 1 — Visual foundation:** token file, typography, reusable controls/panels, AppShell, navigation, responsive layout, empty/error/loading states, and screenshot tests. Confirm no regressions to GUI-00 security or backend architecture.

**Checkpoint 2 — Real Overview:** connect read-only status endpoints through typed API adapter, represent unknown/stale states honestly, add health/current show/activity cards only where data exists. Do not add live actions.

**Checkpoint 3 — Review in running browser:** inspect 1440×900, 2560×1440, tablet, and phone; evaluate contrast, density, resizing, focus states, and actual data; refine tokens before applying them to complex workspaces.

**Checkpoint 4 — Next vertical slices:** Scene Editor and Mapper in milestone order, beginning with real runtime-compatible capabilities, not the full aspirational images.

### Definition of done for design-to-code transition

A working local browser build has the Cueverge shell, consistent semantic tokens, functional navigation, accurate Overview states, and tests. The shell does not imply unavailable runtime features or expose unsafe controls. From this checkpoint onward, the running app is the source of truth for visual iteration; the reference images remain inspiration only.
