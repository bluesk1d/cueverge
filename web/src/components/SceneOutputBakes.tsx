import { useEffect, useState } from "react";

import { BakeRequirement, RenderJob, createRenderJob, getBakeRequirements } from "../api";

const stateLabels: Record<BakeRequirement["state"], string> = {
  current: "Baked for this saved version",
  missing: "Needs a bake",
  queued: "Queued",
  running: "Baking",
  failed: "Bake failed",
  unavailable: "Can't be baked",
};

/** Bake status for media layers placed on this scene's output tracks (whole-scene timeline). */
export function SceneOutputBakes({
  projectId,
  sceneId,
  revision,
  canEdit,
  dirty,
  renderJobs,
  onJobCreated,
}: {
  projectId: string;
  sceneId: string;
  revision: number;
  canEdit: boolean;
  dirty: boolean;
  renderJobs: RenderJob[];
  onJobCreated: (job: RenderJob) => void;
}) {
  const [bakes, setBakes] = useState<BakeRequirement[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  const [refreshes, setRefreshes] = useState(0);
  const pending = bakes?.some((item) => item.state === "queued" || item.state === "running") ?? false;

  useEffect(() => {
    let active = true;
    getBakeRequirements(projectId, revision)
      .then((value) => {
        if (active) {
          setBakes(value.bakes.filter((item) => item.kind === "scene_output" && item.scene_id === sceneId));
          setError(null);
        }
      })
      .catch((failure) => {
        if (active) setError(failure instanceof Error ? failure.message : "Bake status is unavailable.");
      });
    return () => {
      active = false;
    };
  }, [projectId, sceneId, revision, renderJobs, refreshes]);

  useEffect(() => {
    if (!pending) return undefined;
    const timer = window.setTimeout(() => setRefreshes((value) => value + 1), 1000);
    return () => window.clearTimeout(timer);
  }, [pending, bakes]);

  async function bake(item: BakeRequirement) {
    setStarting(item.id);
    setError(null);
    try {
      onJobCreated(await createRenderJob(projectId, item.id));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "The bake could not start.");
    } finally {
      setStarting(null);
    }
  }

  return (
    <section className="timeline-scene-output-bakes" aria-label="Whole-scene output bakes">
      <strong>Whole-scene audio</strong>
      <small>
        Audio layers on an output track are mixed with that output&apos;s media when baked; the
        video is copied unchanged. Apply bakes anything missing automatically. Bakes use the
        saved version{dirty ? ", so save your edits first" : ""}.
      </small>
      {bakes?.length === 0 && <small>This scene has no layers on its output tracks.</small>}
      {bakes?.map((item) => (
        <div key={item.id} className="timeline-scene-output-bakes__row">
          <span>
            {item.label} · {stateLabels[item.state]}
            {item.state === "running" && item.progress != null ? ` ${Math.round(item.progress * 100)}%` : ""}
          </span>
          {item.error && <small role={item.state === "unavailable" || item.state === "failed" ? "alert" : undefined}>{item.error}</small>}
          {canEdit && (item.state === "missing" || item.state === "failed") && (
            <button
              type="button"
              disabled={dirty || starting !== null}
              onClick={() => void bake(item)}
            >
              {starting === item.id ? "Starting…" : `Bake ${item.label}`}
            </button>
          )}
        </div>
      ))}
      {error && <small role="alert">{error}</small>}
    </section>
  );
}
