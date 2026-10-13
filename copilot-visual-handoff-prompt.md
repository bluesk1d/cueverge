# Copilot implementation prompt — Cueverge visual foundation

Read these repository documents before editing code:

- `docs/gui-roadmap.md` (milestone authority)
- `docs/gui-implementation-spec.md` (product, runtime, API, and safety contracts)
- `docs/gui-visual-design-system.md` (approved visual direction)
- `docs/architecture.md` and relevant ADRs

Also inspect `docs/assets/gui/installation-overview-reference.png` and `docs/assets/gui/workspace-design-reference.png`. These are *visual inspiration*, not functional specifications or authoritative data. The documents override visual artifacts where they disagree.

**Context:** GUI-00 implementation is already underway. First inspect the current working tree, uncommitted changes, installed frontend framework, current tests, and completed GUI-00 contracts. Do not overwrite, duplicate, or replace existing work. Adapt the design to the stack already established if reasonable; propose any major stack change before implementing it.

**Goal:** Implement the first visual vertical slice: shared Cueverge application shell and a truthful, responsive, read-only Installation Overview. Use React/TypeScript/Vite and the existing FastAPI backend if consistent with the current branch. Prefer semantic CSS tokens, reusable local UI components, and a typed API boundary.

**Tasks:**

1. Summarize the current frontend/backend baseline and identify the smallest safe implementation slice.
2. Add semantic dark-mode design tokens and a coherent typography/spacing system from `gui-visual-design-system.md`.
3. Build or refine `AppShell`, global header, installation selector, sidebar with exactly Overview / Installation / Shows / Operate / Settings, page header, panels, status indicators, empty/loading/error states, and responsive layout.
4. Implement the Installation Overview using existing **read-only** status endpoints. Only show facts the API supports; when missing, show honest placeholders, not invented online counts or fictional show metadata. Do not introduce fake active-show data into production paths.
5. Include isolated demo fixtures or component stories for reviewing rich states without real devices, clearly separated from production.
6. Ensure accessibility (focus rings, labels, keyboard navigation, contrast, reduced motion) and browser behavior at 1440×900, 2560×1440, tablet, and phone widths.
7. Add appropriate unit/component/browser tests, run available repository lint/type/test commands, and report actual results.

**Safety and architecture constraints:** No new hardware-control shortcuts on Overview; no unauthenticated network control; no assumption of a fixed autonomous next-scene schedule; no frontend director/lighting timing engine; no modification of active show packages; do not claim blackout is latched unless the backend actually enforces it. Preserve GUI-00 work, current schemas, and agent protocol compatibility.

**Deliverables:** Changed files, screenshots of the real browser UI (or exact instructions to capture them if unavailable), test results, known gaps, and a brief proposed next visual iteration. Stop after the shell + Overview slice; do not build the full Scene Editor or Mapper in this pass.
