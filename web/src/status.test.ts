import { describe, expect, it } from "vitest";

import type { TopologyNode } from "./api";
import { keepAwakeLabel, keepAwakeWarning } from "./status";

function mediaHost(overrides: Partial<TopologyNode> = {}): TopologyNode {
  return {
    id: "device:projector",
    label: "Projector host",
    kind: "direct_media_agent",
    parent_id: "controller",
    configured: true,
    connection: "connected",
    enrollment: "enrolled",
    compatibility: "compatible",
    protocol_version: 2,
    capabilities: [],
    assigned_resources: [],
    last_seen_age_ms: 0,
    software_version: null,
    latency_ms: null,
    clock_drift_ms: null,
    ...overrides,
  };
}

describe("keep awake status", () => {
  it("describes the host option without warning when it is off or granted", () => {
    expect(keepAwakeLabel(mediaHost())).toBe("Off on this host");
    expect(keepAwakeLabel(mediaHost({ keep_awake: { mode: "system", active: true } }))).toBe(
      "Sleep blocked",
    );
    const display = mediaHost({ keep_awake: { mode: "display", active: true } });
    expect(keepAwakeLabel(display)).toBe("Sleep and screen blanking blocked");
    expect(keepAwakeWarning(display)).toBeNull();
    expect(keepAwakeWarning(mediaHost())).toBeNull();
  });

  it("warns when the host refused and ignores stale reports from disconnected hosts", () => {
    const refused = mediaHost({
      keep_awake: { mode: "display", active: false, detail: "no desktop session" },
    });
    expect(keepAwakeLabel(refused)).toBe("Requested (display) but not active");
    expect(keepAwakeWarning(refused)).toBe(
      "This host may sleep or blank its screen: no desktop session",
    );
    const offline = { ...refused, connection: "disconnected" as const };
    expect(keepAwakeLabel(offline)).toBe("Not reported while disconnected");
    expect(keepAwakeWarning(offline)).toBeNull();
  });
});
