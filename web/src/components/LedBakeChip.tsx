import { useCallback, useEffect, useState } from "react";

import { createRenderJob, getLightingBakeStatus } from "../api";
import type { LightingBakeStatus } from "../api";

type Tone = "current" | "needed" | "baking" | "failed";

const STATE_LABELS: Record<LightingBakeStatus["state"], string> = {
  current: "Baked",
  queued: "Queued",
  running: "Baking",
  failed: "Bake failed",
  missing: "Needs a bake",
  unavailable: "Cannot bake",
};

function chipTone(items: LightingBakeStatus[]): Tone {
  if (items.some((item) => item.state === "failed" || item.state === "unavailable")) {
    return "failed";
  }
  if (items.some((item) => item.state === "missing")) return "needed";
  if (items.some((item) => item.state === "queued" || item.state === "running")) {
    return "baking";
  }
  return "current";
}

function chipLabel(tone: Tone, items: LightingBakeStatus[]): string {
  const pending = items.filter((item) => item.state !== "current").length;
  if (tone === "current") return "LED bake current";
  if (tone === "baking") {
    const running = items.find((item) => item.state === "running");
    return running?.progress != null
      ? `Baking LEDs · ${Math.round(running.progress * 100)}%`
      : "LED bake queued";
  }
  if (tone === "failed") return pending === 1 ? "LED bake failed" : `${pending} LED bakes failed`;
  return pending === 1 ? "LED bake needed" : `${pending} LED bakes needed`;
}

/** Shows whether the scene's LED compositions are baked for the saved revision. */
export function LedBakeChip({
  projectId,
  revision,
  sceneId,
  canBake,
  dirty,
  onOpenBakeSettings,
}: {
  projectId: string;
  revision: number;
  sceneId: string;
  canBake: boolean;
  dirty: boolean;
  onOpenBakeSettings: (composition: LightingBakeStatus) => void;
}) {
  const [statuses, setStatuses] = useState<LightingBakeStatus[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      const result = await getLightingBakeStatus(projectId, revision);
      setStatuses(result.compositions);
    } catch {
      setStatuses(null);
    }
  }, [projectId, revision]);
  useEffect(() => {
    setStatuses(null);
    setError(null);
    void refresh();
  }, [refresh]);
  const items = (statuses ?? []).filter((item) => item.scene_ids.includes(sceneId));
  const baking = items.some((item) => item.state === "queued" || item.state === "running");
  useEffect(() => {
    if (!baking) return;
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [baking, refresh]);
  useEffect(() => setOpen(false), [sceneId]);
  if (items.length === 0) return null;
  const tone = chipTone(items);
  const restarting = items.filter((item) => item.ambient_scene_timeline_ids?.includes(sceneId));
  const needing = items.filter((item) => item.state === "missing" || item.state === "failed");
  const blocked = !canBake
    ? "You can view bakes but not start them."
    : dirty
      ? "Save or cancel scene edits first; bakes use the saved revision."
      : null;

  async function bake(targets: LightingBakeStatus[]) {
    setBusy(true);
    setError(null);
    try {
      for (const target of targets) await createRenderJob(projectId, target.composition_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The bake could not start.");
    } finally {
      setBusy(false);
      await refresh();
    }
  }

  return (
    <div className="led-bake-chip">
      <button
        type="button"
        className={`led-bake-chip__toggle led-bake-chip__toggle--${tone}`}
        aria-expanded={open}
        aria-label={`${chipLabel(tone, items)}. Show LED bake details`}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true" className="led-bake-chip__dot" />
        {chipLabel(tone, items)}
      </button>
      {restarting.length > 0 && (
        <p className="led-bake-chip__warning" role="note">
          {restarting.map((item) => item.name).join(", ")}{" "}
          {restarting.length === 1 ? "is" : "are"} the show&apos;s ambient lighting but also
          placed on this scene&apos;s timeline, so that copy restarts each time the scene
          repeats. Remove it from the timeline (Edit timeline › Compositions); the ambient
          already runs continuously under this scene.
        </p>
      )}
      {open && (
        <div className="led-bake-chip__panel" role="region" aria-label="LED bakes for this scene">
          <p>
            Scene previews and publishing use LED compositions baked for the saved revision.
            Saving edits that do not change what an LED-only composition draws keeps its bake.
          </p>
          <ul>
            {items.map((item) => (
              <li key={item.composition_id}>
                <span>
                  <strong>{item.name}</strong>
                  {item.ambient && <small> · ambient</small>}
                  <small>
                    {" · "}
                    {STATE_LABELS[item.state]}
                    {item.state === "running" && item.progress != null
                      ? ` ${Math.round(item.progress * 100)}%`
                      : ""}
                    {item.state === "current" && item.baked_revision != null &&
                    item.baked_revision !== revision
                      ? ` (from revision ${item.baked_revision})`
                      : ""}
                  </small>
                  {item.error && <small className="led-bake-chip__error">{item.error}</small>}
                </span>
                <span className="led-bake-chip__actions">
                  {(item.state === "missing" || item.state === "failed") && (
                    <button
                      type="button"
                      disabled={busy || blocked !== null}
                      title={blocked ?? undefined}
                      onClick={() => void bake([item])}
                    >
                      Bake now
                    </button>
                  )}
                  <button type="button" disabled={dirty}
                    title={dirty ? "Save or cancel scene edits before opening the timeline editor." : undefined}
                    onClick={() => onOpenBakeSettings(item)}>
                    Bake settings
                  </button>
                </span>
              </li>
            ))}
          </ul>
          {needing.length > 1 && (
            <button
              type="button"
              disabled={busy || blocked !== null}
              title={blocked ?? undefined}
              onClick={() => void bake(needing)}
            >
              Bake all ({needing.length})
            </button>
          )}
          {blocked && <small>{blocked}</small>}
          {error && <p role="alert">{error}</p>}
        </div>
      )}
    </div>
  );
}
