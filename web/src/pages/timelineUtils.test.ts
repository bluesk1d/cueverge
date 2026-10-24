import { describe, expect, it } from "vitest";

import { formatTimecode, stackLayerRows, trimKeyframes } from "./timelineUtils";

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
