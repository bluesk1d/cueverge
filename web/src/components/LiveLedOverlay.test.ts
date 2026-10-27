import { expect, test } from "vitest";

import type { InstallationGeometry, OutputMonitor } from "../api";
import { coverViewBox, mapMonitorLeds } from "./LiveLedOverlay";

function segment(overrides: Partial<InstallationGeometry> = {}): InstallationGeometry {
  return {
    id: "segment-1", installation_id: "home", photo_id: "house", kind: "pixel_segment", name: "Roofline",
    semantic_id: "roofline", landmark_ids: [], color: "#fff", label_visible: true, locked: false, hidden: false,
    z_index: 0, direction: "forward", start_index: 0, pixel_count: 3, stage_points: null,
    points: [{ x: 0.1, y: 0.5 }, { x: 0.3, y: 0.5 }], closed: false, revision: 1,
    created_at: "2026-10-26T00:00:00Z", updated_at: "2026-10-26T00:00:00Z", ...overrides,
  };
}

function output(colors: [number, number, number][], overrides: Partial<OutputMonitor["outputs"][number]> = {}) {
  return {
    output_id: "leds", resource: "wled-001", pixel_count: 3, pixel_offset: 0, output_position_ms: 0,
    colors, next_pixel_offset: null, limitations: [], source: "scene" as const, scene_instance_id: "scene",
    age_ms: 10, stale: false, held: false, error: null, ...overrides,
  };
}

test("cover viewBox crops the letterboxed editor frame exactly like object-fit: cover", () => {
  // 5:3 photo fills the 1000×600 frame; a 2:1 box crops it vertically.
  expect(coverViewBox({ width: 500, height: 300 }, 2)).toEqual([0, 50, 1000, 500]);
  // 16:9 photo is letterboxed in the frame (562.5 tall at y=18.75); a 16:9 box shows just the photo.
  const [x, y, width, height] = coverViewBox({ width: 1600, height: 900 }, 16 / 9)!;
  expect(x).toBeCloseTo(0);
  expect(y).toBeCloseTo(18.75);
  expect(width).toBeCloseTo(1000);
  expect(height).toBeCloseTo(562.5);
  // Portrait photo in a wide box keeps the photo's full width and crops top/bottom.
  const portrait = coverViewBox({ width: 300, height: 600 }, 2)!;
  expect(portrait[2] / portrait[3]).toBeCloseTo(2);
  expect(portrait[0]).toBeCloseTo(350);
  expect(coverViewBox({ width: 0, height: 0 }, 2)).toBeNull();
  expect(coverViewBox({ width: 10, height: 10 }, 0)).toBeNull();
});

test("monitor colors map onto saved segment positions; dark, unsampled and mismatched LEDs are omitted", () => {
  const mapping = { geometry: [segment()], resources: { "segment-1": "wled-001" }, loaded: true, error: null };
  const leds = mapMonitorLeds(mapping, "house", [output([[255, 0, 0], [0, 0, 0], [0, 0, 255]])]);
  expect(leds).toEqual([
    { x: 0.1, y: 0.5, color: "rgb(255,0,0)" },
    { x: 0.3, y: 0.5, color: "rgb(0,0,255)" },
  ]);
  // A later sampled range lands on the matching output indices only.
  expect(mapMonitorLeds(mapping, "house", [output([[255, 0, 0]], { pixel_offset: 2 })]))
    .toEqual([{ x: 0.3, y: 0.5, color: "rgb(255,0,0)" }]);
  expect(mapMonitorLeds(mapping, "house", [output([[255, 0, 0]], { pixel_count: 2 })])).toEqual([]);
  expect(mapMonitorLeds(mapping, "other-photo", [output([[255, 0, 0], [255, 0, 0], [255, 0, 0]])])).toEqual([]);
  expect(mapMonitorLeds(mapping, "house", [output([[9, 9, 9]], { resource: "other" })])).toEqual([]);
});
