# ADR 0013: Use hybrid browser editor rendering

- Status: Accepted
- Date: 2026-10-12

## Context

Cueverge needs a timeline, photo-assisted 2.5D mapper, native-output previews,
and eventually dense lighting and multi-projector visualization. A single DOM,
SVG, or Canvas renderer would force avoidable accessibility, interaction, or
performance compromises. Persisting objects from a rendering library would
also couple the project schema to that library.

## Decision

Build the timeline as a custom hybrid React editor:

- DOM for track controls, toolbars, transport, focus targets, and accessible
  alternatives;
- SVG for cues, markers, handles, motion paths, selection, and snap guides;
- Canvas for waveforms, thumbnails, dense timeline backgrounds, and large cue
  sets;
- optional Web Workers for visual preprocessing and indexes that do not own
  runtime semantics.

Start the spatial mapper with Konva and react-konva for photo overlays, scene
layers, selection, hit testing, and transforms. Treat Konva as a replaceable
view adapter, not a persisted model or public contract. Maintain this boundary:

```text
versioned project geometry
        -> mapper view model
        -> Konva scene objects
```

Keep measured geometry, coordinate spaces, stable IDs, homographies, and other
transform math in framework-independent domain modules. If scale or rendering
requirements outgrow Konva, a PixiJS or custom Canvas/WebGL renderer may
replace the view adapter without a project migration.

Browser editors may convert time and geometry to pixels for display, hit
testing, snapping, and authoring. They do not evaluate authoritative lighting,
director selection, media time, or hardware output. Deterministic preview
comes from backend evaluators.

## Consequences

The editor can remain accessible without sacrificing dense rendering
performance. Canvas views require synchronized semantic DOM or tabular
alternatives. Interaction commands and tests must target Cueverge domain
objects rather than Konva nodes or raw SVG elements.

The renderer abstraction adds some initial structure but prevents project data
from becoming dependent on a third-party scene graph.

