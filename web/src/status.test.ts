import { describe, expect, it } from "vitest";

import type { TopologyNode } from "./api";
import type { DeviceAgentUpdate } from "./api";
import { agentSoftwareLabel, agentUpdateLabel, keepAwakeLabel, keepAwakeWarning } from "./status";

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

describe("agent update labels", () => {
  const update: DeviceAgentUpdate = {
    device_id: "left",
    display_name: "Left",
    connected: true,
    state: "available",
    managed: true,
    running_version: "0.1.0",
    running_digest: "a".repeat(64),
    available: { platform: "linux_x86_64", version: "0.2.0", build: "rev42", digest_sha256: "b".repeat(64), size_bytes: 1 },
  };

  it("names the running build and what is offered", () => {
    expect(agentSoftwareLabel(update)).toBe(`0.1.0 (${"a".repeat(12)})`);
    expect(agentSoftwareLabel({ ...update, running_digest: null })).toBe("0.1.0 (source checkout)");
    expect(agentSoftwareLabel(null)).toBe("Not reported");
    expect(agentUpdateLabel(update)).toBe("Update available (0.2.0 · rev42)");
  });

  it("explains failures and hosts that need a one-time install", () => {
    expect(agentUpdateLabel({ ...update, state: "rolled_back", detail: "Exited with code 1." })).toBe(
      "Update rolled back: Exited with code 1.",
    );
    expect(agentUpdateLabel({ ...update, state: "unmanaged", managed: false })).toBe("Manual updates only");
    expect(agentUpdateLabel(undefined)).toBe("Not reported");
  });
});
