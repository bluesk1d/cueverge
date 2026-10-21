import { expect, test } from "vitest";

import type { InstallationGeometry } from "../api";
import { outputTraversal, photoPixelPoints } from "./pixel-traversal";

function segment(
  id: string,
  start: number,
  count: number,
  overrides: Partial<InstallationGeometry> = {},
): InstallationGeometry {
  return {
    id,
    installation_id: "installation",
    photo_id: "front",
    semantic_id: id,
    landmark_ids: [],
    kind: "pixel_segment",
    name: id,
    points: [{ x: 0, y: 0 }, { x: 1, y: 0 }],
    closed: false,
    color: "#ffffff",
    label_visible: true,
    locked: false,
    hidden: false,
    z_index: 0,
    direction: "forward",
    start_index: start,
    pixel_count: count,
    stage_points: null,
    revision: 1,
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

test("orders all segments by physical index, not geometry list or drawing order", () => {
  const plan = outputTraversal(
    [segment("last", 5, 2), segment("first", 0, 5)],
    { last: "roof", first: "roof" }, "roof", "front", 7,
  );
  expect(plan.segments.map((item) => [item.geometry.id, item.start, item.end]))
    .toEqual([["first", 0, 5], ["last", 5, 7]]);
  expect(plan.segments.map((item) => item.order)).toEqual([1, 2]);
  expect(plan.segments[0].color).not.toBe(plan.segments[1].color);
  expect(plan.diagnostics).toEqual([]);
});

test("samples pixel-count-weighted positions along bends and reverses each segment", () => {
  const geometry = segment("bent", 10, 4, {
    points: [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 2 }],
  });
  expect(photoPixelPoints(geometry)).toEqual([
    { outputIndex: 10, x: 0, y: 0 },
    { outputIndex: 11, x: 1, y: 0 },
    { outputIndex: 12, x: 1, y: 1 },
    { outputIndex: 13, x: 1, y: 2 },
  ]);
  expect(photoPixelPoints({ ...geometry, direction: "reverse" })).toEqual([
    { outputIndex: 10, x: 1, y: 2 },
    { outputIndex: 11, x: 1, y: 1 },
    { outputIndex: 12, x: 1, y: 0 },
    { outputIndex: 13, x: 0, y: 0 },
  ]);
});

test("deduplicates linked photos, prefers current view, and excludes other outputs", () => {
  const plan = outputTraversal([
    segment("side", 0, 3, { semantic_id: "shared", photo_id: "side" }),
    segment("front", 0, 3, { semantic_id: "shared" }),
    segment("other", 0, 3),
    segment("offscreen", 3, 1, { photo_id: "side" }),
  ], { side: "roof", front: "roof", other: "porch", offscreen: "roof" }, "roof", "front", 4);
  expect(plan.segments.map((item) => item.geometry.id)).toEqual(["front", "offscreen"]);
  expect(plan.diagnostics).toEqual([]);
});

test("reports leading, internal and trailing gaps without renumbering pixels", () => {
  const plan = outputTraversal(
    [segment("a", 2, 2), segment("b", 5, 1)],
    { a: "roof", b: "roof" }, "roof", "front", 8,
  );
  expect(plan.diagnostics).toEqual([
    "Unmapped output indices 0-1.",
    "Unmapped output indices 4-4.",
    "Unmapped output indices 6-7.",
  ]);
  expect(plan.segments[1].pixels[0].outputIndex).toBe(5);
});

test("reports overlaps and inconsistent linked views instead of hiding them", () => {
  const plan = outputTraversal([
    segment("a", 0, 4),
    segment("b", 3, 2),
    segment("alternate", 0, 4, { semantic_id: "a", photo_id: "side", direction: "reverse" }),
  ], { a: "roof", b: "roof", alternate: "roof" }, "roof", "front", 5);
  expect(plan.diagnostics).toEqual([
    "a: linked photos disagree on pixel range or direction.",
    "Overlapping pixel allocation at b; multiple locations may pulse.",
  ]);
});

test("invalid ranges are explicit and are not rendered as plausible mappings", () => {
  const plan = outputTraversal(
    [segment("missing", 0, 1, { start_index: null }), segment("outside", 7, 4)],
    { missing: "roof", outside: "roof" }, "roof", "front", 8,
  );
  expect(plan.segments).toEqual([]);
  expect(plan.diagnostics).toContain("missing: invalid or missing pixel range/photo points.");
  expect(plan.diagnostics).toContain("outside: invalid or missing pixel range/photo points.");
  expect(plan.diagnostics).toContain("Unmapped output indices 0-7.");
});

test("one-point fixtures and zero-length strands have finite pixel positions", () => {
  expect(photoPixelPoints(segment("one", 2, 1, { points: [{ x: 0.5, y: 0.6 }] })))
    .toEqual([{ outputIndex: 2, x: 0.5, y: 0.6 }]);
  expect(photoPixelPoints(segment("zero", 0, 2, {
    points: [{ x: 0, y: 0 }, { x: 0, y: 0 }],
  }))).toEqual([{ outputIndex: 0, x: 0, y: 0 }, { outputIndex: 1, x: 0, y: 0 }]);
});

test("closed loops do not duplicate the closing pixel", () => {
  const points = photoPixelPoints(segment("loop", 0, 4, {
    points: [{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 0, y: 0 }],
  }));
  expect(points.map((point) => point.x)).toEqual([0, 0.5, 1, 0.5]);
});
