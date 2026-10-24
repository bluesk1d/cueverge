import { describe, expect, it } from "vitest";

import {
  applyHomography,
  cornerHomography,
  formatTimecode,
  homographyMatrix3d,
  mediaOutputSourceMs,
  sceneMediaOutputs,
  stackLayerRows,
  trimKeyframes,
} from "./timelineUtils";

describe("formatTimecode", () => {
  it("formats minutes, seconds and milliseconds", () => {
    expect(formatTimecode(0)).toBe("0:00.000");
    expect(formatTimecode(100)).toBe("0:00.100");
    expect(formatTimecode(61_250)).toBe("1:01.250");
    expect(formatTimecode(180_000)).toBe("3:00.000");
  });

  it("clamps negative values and rounds fractional milliseconds", () => {
    expect(formatTimecode(-5)).toBe("0:00.000");
    expect(formatTimecode(33.4)).toBe("0:00.033");
  });
});

describe("trimKeyframes", () => {
  it("keeps keyframes inside the range unchanged", () => {
    expect(trimKeyframes([{ time_ms: 0 }, { time_ms: 500 }], 0, 1000)).toEqual([
      { time_ms: 0 },
      { time_ms: 500 },
    ]);
  });

  it("collapses trimmed-off keyframes onto the nearest edge", () => {
    expect(
      trimKeyframes([{ time_ms: -300 }, { time_ms: -100 }, { time_ms: 400 }, { time_ms: 1200 }], 0, 1000),
    ).toEqual([{ time_ms: 0 }, { time_ms: 400 }, { time_ms: 1000 }]);
  });

  it("drops trimmed-off keyframes when one already sits on the edge", () => {
    expect(trimKeyframes([{ time_ms: -100, id: "a" }, { time_ms: 0, id: "b" }], 0, 1000)).toEqual([
      { time_ms: 0, id: "b" },
    ]);
  });
});

describe("stackLayerRows", () => {
  it("puts overlapping layers on separate rows and reuses free rows", () => {
    const rows = stackLayerRows([
      { id: "a", start_ms: 0, duration_ms: 4000, z_index: 0 },
      { id: "b", start_ms: 1600, duration_ms: 2300, z_index: 1 },
      { id: "c", start_ms: 4000, duration_ms: 500, z_index: 2 },
    ]);
    expect(rows.get("a")).toBe(0);
    expect(rows.get("b")).toBe(1);
    expect(rows.get("c")).toBe(0);
  });
});

describe("scene media outputs", () => {
  it("normalizes single and multi-output media", () => {
    expect(sceneMediaOutputs({ resource: "projector.main", asset: "a" })).toEqual([
      { id: "media", resource: "projector.main", asset: "a", startDelayMs: 0, endBehavior: "hold_last_frame" },
    ]);
    expect(
      sceneMediaOutputs([
        { id: "right", resource: "projector.right", asset: "b", start_delay_ms: 500, end_behavior: "loop" },
      ]),
    ).toEqual([{ id: "right", resource: "projector.right", asset: "b", startDelayMs: 500, endBehavior: "loop" }]);
    expect(sceneMediaOutputs(undefined)).toEqual([]);
  });

  it("maps the scene playhead to source time with end behavior", () => {
    expect(mediaOutputSourceMs(200, 500, 1000, "stop")).toBeNull();
    expect(mediaOutputSourceMs(700, 500, 1000, "stop")).toBe(200);
    expect(mediaOutputSourceMs(1600, 500, 1000, "stop")).toBeNull();
    expect(mediaOutputSourceMs(1600, 500, 1000, "hold_last_frame")).toBe(999);
    expect(mediaOutputSourceMs(1700, 500, 1000, "loop")).toBe(200);
    expect(mediaOutputSourceMs(9000, 0, 0, "stop")).toBe(9000);
  });
});

describe("homographies", () => {
  it("maps raster corners onto a drawn quad", () => {
    const corners = [
      { x: 0.7, y: 0.28 },
      { x: 0.78, y: 0.3 },
      { x: 0.77, y: 0.45 },
      { x: 0.71, y: 0.44 },
    ];
    const h = cornerHomography(1920, 1080, corners);
    expect(h).not.toBeNull();
    [[0, 0], [1920, 0], [1920, 1080], [0, 1080]].forEach(([x, y], index) => {
      const point = applyHomography(h!, x, y);
      expect(point.x).toBeCloseTo(corners[index].x, 9);
      expect(point.y).toBeCloseTo(corners[index].y, 9);
    });
    expect(cornerHomography(1920, 1080, corners.slice(0, 3))).toBeNull();
  });

  it("builds a column-major CSS matrix3d scaled to the photo box", () => {
    expect(homographyMatrix3d([1, 2, 3, 4, 5, 6, 7, 8, 9], 10, 100)).toBe(
      "matrix3d(10,400,0,7,20,500,0,8,0,0,1,0,30,600,0,9)",
    );
  });
});
