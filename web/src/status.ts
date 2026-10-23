import type { RuntimeStatus, TopologyNode } from "./api";
import type { StatusTone } from "./components/ui";

export const STATUS_REFRESH_MS = 2000;
export const STATUS_STALE_MS = 10000;
export const STATUS_TIMEOUT_MS = 8000;

export type SnapshotFreshness = "loading" | "fresh" | "stale" | "unavailable";

export function snapshotFreshness(
  receivedAt: number | null,
  failed: boolean,
  now: number,
): SnapshotFreshness {
  if (failed) return "unavailable";
  if (receivedAt === null) return "loading";
  return now - receivedAt >= STATUS_STALE_MS ? "stale" : "fresh";
}

export function controllerStatus(
  runtime: RuntimeStatus | null,
  freshness: SnapshotFreshness,
): { tone: StatusTone; label: string } {
  if (freshness === "stale") return { tone: "warning", label: "Status out of date" };
  if (freshness === "unavailable") return { tone: "danger", label: "Status unavailable" };
  if (!runtime || freshness === "loading") return { tone: "neutral", label: "Checking status" };
  return runtime.status.degraded
    ? { tone: "warning", label: "Equipment needs attention" }
    : { tone: "success", label: "Controller connected" };
}

export function connectionStatus(node: TopologyNode): { tone: StatusTone; label: string } {
  if (node.connection === "connected") return { tone: "success", label: "Connected" };
  if (node.connection === "disconnected") {
    return node.connection_policy === "on_demand"
      ? { tone: "neutral", label: "Offline · on demand" }
      : { tone: "warning", label: "Disconnected" };
  }
  return { tone: "neutral", label: "Connection not reported" };
}
