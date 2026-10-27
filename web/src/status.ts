import type { DeviceAgentUpdate, RuntimeStatus, TopologyNode } from "./api";
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

/** Loaded-catalog scene name for a scene ID, falling back to the ID when unreported. */
export function sceneLabel(runtime: RuntimeStatus | null, sceneId: string | null | undefined): string | null {
  if (!sceneId) return null;
  return runtime?.status.operations.scene_options?.find((value) => value.id === sceneId)?.name || sceneId;
}

/** Name of the verified loaded show, or null for legacy/unreported catalogs. */
export function loadedShowName(runtime: RuntimeStatus | null): string | null {
  const loaded = runtime?.loaded_catalog;
  return loaded?.state === "loaded" ? loaded.project_name || null : null;
}

/** Short version label: "0.1.0 · build 20261027 (3f2a1b9c0d1e)". */
export function agentSoftwareLabel(update: DeviceAgentUpdate | null | undefined): string {
  if (!update?.running_version) return "Not reported";
  const digest = update.running_digest ? ` (${update.running_digest.slice(0, 12)})` : " (source checkout)";
  return `${update.running_version}${digest}`;
}

/** Plain-language agent update state with its reason when there is one. */
export function agentUpdateLabel(update: DeviceAgentUpdate | null | undefined): string {
  if (!update) return "Not reported";
  const label = {
    current: "Up to date",
    available: `Update available${update.available ? ` (${update.available.version} · ${update.available.build})` : ""}`,
    updating: "Updating",
    failed: "Update failed",
    rolled_back: "Update rolled back",
    unmanaged: "Manual updates only",
    unsupported_platform: "No updates for this platform",
    no_bundle: "No update bundle for this platform",
    disconnected: "Not reported while disconnected",
  }[update.state];
  return update.detail ? `${label}: ${update.detail}` : label;
}

/** Device-details text for a media host's keep-awake option. */
export function keepAwakeLabel(node: TopologyNode): string {
  if (node.connection !== "connected") return "Not reported while disconnected";
  const keepAwake = node.keep_awake;
  if (!keepAwake) return "Off on this host";
  const scope = keepAwake.mode === "display" ? "Sleep and screen blanking blocked" : "Sleep blocked";
  return keepAwake.active ? scope : `Requested (${keepAwake.mode}) but not active`;
}

/** Warning when a media host asked to stay awake but its operating system refused. */
export function keepAwakeWarning(node: TopologyNode): string | null {
  const keepAwake = node.keep_awake;
  if (node.connection !== "connected" || !keepAwake || keepAwake.active) return null;
  return `This host may sleep or blank its screen: ${keepAwake.detail ?? "keep awake is not active."}`;
}

export function connectionStatus(node: TopologyNode): { tone: StatusTone; label: string } {
  if (node.connection === "connected") {
    if (node.kind === "direct_media_agent" && node.player_control && node.player_state !== "ready") {
      return node.player_state === "failed"
        ? { tone: "danger", label: "Player failed" }
        : node.player_state
          ? { tone: "neutral", label: `Player ${node.player_state}` }
          : { tone: "neutral", label: "Player state unknown" };
    }
    return node.kind === "direct_media_agent" && node.diagnostic
      ? { tone: "success", label: "Connected · idle" }
      : { tone: "success", label: "Connected" };
  }
  if (node.connection === "disconnected") {
    return node.connection_policy === "on_demand"
      ? { tone: "neutral", label: "Offline · on demand" }
      : { tone: "warning", label: "Disconnected" };
  }
  return { tone: "neutral", label: "Connection not reported" };
}
