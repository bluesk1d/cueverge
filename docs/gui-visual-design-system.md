# Cueverge GUI — Visual Design System & Application Shell

**Status:** Approved visual direction; implementation specification, not a claim of existing functionality  
**Version:** 1.0 — 2026-10-12  
**Companion documents:** `docs/gui-roadmap.md` (milestone authority), `docs/gui-implementation-spec.md` (product/API/safety contracts)  
**Reference images:** `docs/assets/gui/installation-overview-reference.png`, `docs/assets/gui/workspace-design-reference.png`

## 1. Design intent

Cueverge is **professional show-control software with the polish of a modern creative workstation**. It should feel at home beside video-editing and lighting-control applications, but remain approachable to an operator running a show from a phone. The approved direction combines a restrained charcoal production-studio foundation with modest blue/cyan accents and strong, semantically meaningful operational feedback. Design for dark mode first; light mode is not required for the initial shell.

**Visual reference hierarchy:** This document's functional and safety rules > `gui-implementation-spec.md` > approved image composition. The generated mockups contain fictional data, inconsistent navigation, impossible runtime affordances, and incidental text. Do **not** reproduce them as screenshots, embed them as backgrounds, or treat their labels as authoritative.

### Non-negotiable product rules

- One active scene per installation at a time under the current runtime model; multiple outputs may participate within that scene.
- Autonomous scene choices are not a fixed upcoming schedule. Installation schedules govern when a show runs, not which scene the director will select at a future timestamp.
- Read-only overview must never expose working transport buttons that bypass auth/preflight. Hardware-affecting operations belong behind explicit authorized controls in Operate.
- Editing and simulation are safe by default; physical tests and live changes require deliberate arming, authorization, preflight, and confirmation. Stop and blackout must be discoverable from Operate. Never imply current blackout is latched if the runtime does not yet implement latching.
- Draft, published, staged, deployed, and active are distinct concepts. Never present a saved draft as running.
- Existing engine schemas and Python timing/director/lighting logic remain authoritative. The browser is not a second show engine.

## 2. Information architecture and navigation

Top-level navigation is exactly: **Overview, Installation, Shows, Operate, Settings**. Use visually quiet section groupings if helpful, but do not add Scenes, Director, Assets, Schedule, or Devices as competing top-level workspaces. Those belong within their owners:

- **Overview:** installation summary, active show and scene, health, recent activity, shortcuts.
- **Installation:** Overview, Devices, Mapper, Calibration, Schedule, and other installation-owned configuration.
- **Shows:** library; inside a show: Overview, Scenes, Director, Interactions, Assets, Simulate, Publish.
- **Operate:** live status, director state, explicit authorized controls, activity, safety.
- **Settings:** controller/application preferences, permissions, maintenance.

Global header: product identity; installation selector; connection/health; operational mode; active show (when present); user/account; navigation to Operate. A collapsed navigation rail is supported on narrow desktops. On mobile, prioritize Operate, Overview, and basic health; complex authoring may be desktop-only initially.

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
2. Page header: `Installation Overview`, installation name, short location/context description; shortcuts to `Open Mapper` and `Manage Installation` (only when routes exist).
3. Optional installation hero image or schematic, sourced from a real installation photo or deliberate placeholder. Avoid generic stock scenes as product data. If no photo, use a useful empty state.
4. Compact health summary: enrolled/online devices, configured media outputs, LED pixels, relevant sensors, active alerts. Counts are derived from actual API fields; do not invent numbers or show fake live green dots.
5. **Active Show** panel: name, release/deployment state, mode, link to show/Operate. Explicit no-active-show state.
6. **Current Scene** panel: actual current scene if running; show position/progress only when the controller reports a trustworthy time. Otherwise show `Position unavailable` or `Stopped`. No decorative pause/skip controls in Overview.
7. **Device Health** panel: actual agent status, last seen, errors/degraded; link to Installation > Devices.
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

The scene editor is a dense creative workspace, not a dashboard: **asset/source browser left; native-output or installation-composite preview center; contextual inspector right; layered timeline bottom**. Adjustable splits, clear selection, track routing, ruler/timecode, and keyboard affordances. Separate editing of scene timelines from show-level Director logic.

For the initial runtime-compatible slice, expose existing media outputs and existing timed lighting cues. Do not imply support for arbitrary multi-clip video composition, crossfades, or video layering until the engine explicitly supports it. Asset references and preview use backend-authoritative data. Timeline colors may distinguish Video, Audio, Lighting, Spatial Effects, Events, and Automation, but unsupported tracks must not appear editable. The mockup's dialogue audio and scripted upcoming scenes are illustrative mistakes, not requirements. The Halloween sample is silent character performance, but Cueverge itself must support audio where the engine permits.

Preview is **simulation by default**; hardware rehearsal requires explicit authorization and arming. Editing must never mutate the active package.

## 7. Screen C — Installation Mapper (design target; later milestone)

Layout: object hierarchy left; dominant editable photo/orthographic canvas center; inspector right; optional diagnostics bottom. Hybrid photo + 2.5D stage representation: true physical XYZ geometry with planar surfaces and ordered LED paths, projected into 2D photo/front/top/side views. A registered photo is a view, not the authoritative geometry. Separate physical surface, logical fixture, device output, pixel addressing, and mapping transform. Support perpendicular surfaces and paths wrapping around corners; no generic aerial landscaping planner or full mesh modeler.

Canvas should support select/pan/measure/surface/path/landmark tools; zoom, snap, selection handles, undo/redo, validation. Hardware test pattern buttons must not be active in edit mode; require an armed, time-limited calibration session with bounded outputs.

## 8. Screen D — Operate (design target; later milestone)

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
