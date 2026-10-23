import { describe, expect, it } from "vitest";
import type { InstallationGeometry } from "../api";
import { mapperDiagnostics } from "./mapper-diagnostics";

function segment(id: string, start: number, count: number, overrides: Partial<InstallationGeometry> = {}): InstallationGeometry {
  return {
    id, installation_id: "original", photo_id: "front", semantic_id: id, kind: "pixel_segment",
    name: id, points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.9 }], closed: false,
    color: "#ffffff", label_visible: true, locked: false, hidden: false, z_index: 0,
    direction: "forward", start_index: start, pixel_count: count, stage_points: null,
    revision: 1, landmark_ids: [], created_at: "", updated_at: "", ...overrides,
  };
}
describe("layout diagnostics", () => {
  it("does not treat photo bounds or separate outputs as physical conflicts", () => {
    const result = mapperDiagnostics([segment("first", 0, 10), segment("second", 0, 10)], { first: "roof", second: "porch" }, "front");
    expect(result.conflicts).toEqual([]);
    expect(result.observations[0]).toContain("not a physical wiring error");
  });
  it("detects nested index overlaps without inventing a gap", () => {
    const result = mapperDiagnostics([segment("outer", 0, 100), segment("inner", 10, 10), segment("later", 30, 10)], { outer: "roof", inner: "roof", later: "roof" }, "front");
    expect(result.conflicts).toHaveLength(2);
    expect(result.observations.some((value) => value.includes("Unmapped index"))).toBe(false);
  });
  it("does not double count linked views and keeps real conflicts visible", () => {
    const first = segment("first", 0, 10);
    const linked = segment("side", 0, 10, { semantic_id: first.semantic_id, photo_id: "side" });
    const overlap = segment("overlap", 5, 10, { hidden: true });
    const result = mapperDiagnostics([first, linked, overlap], { first: "roof", side: "roof", overlap: "roof" }, "front");
    expect(result.conflicts).toHaveLength(1);
    expect(result.conflicts[0]).toContain("overlap");
  });
});
