import { useEffect, useState } from "react";

import {
  addSceneOutput, createScene, getSceneAuthoringOptions,
  type CreateSceneRequest, type ProjectDraftRecord, type SceneAuthoringOptions,
} from "../api";
import { DetailsDialog } from "./DetailsDialog";

export function SceneAuthoringDialog({ kind, draft, sceneId, onClose, onSaved }: {
  kind: "scene" | "output";
  draft: ProjectDraftRecord;
  sceneId: string;
  onClose: () => void;
  onSaved: (updated: ProjectDraftRecord, selectedSceneId: string) => void;
}) {
  const scene = draft.draft.scenes.find((item) => item.id === sceneId);
  const existingOutputs = scene ? [...(Array.isArray(scene.media) ? scene.media : [scene.media]),
    ...(Array.isArray(scene.lighting) ? scene.lighting : [scene.lighting])] : [];
  const usedResources = new Set(existingOutputs.flatMap((output) =>
    output && typeof output === "object" && "resource" in output && typeof output.resource === "string"
      ? [output.resource] : []));
  const usedIds = scene?.schema_version === 1 ? new Set(["media", "lighting"])
    : new Set(existingOutputs.flatMap((output) =>
      output && typeof output === "object" && "id" in output && typeof output.id === "string" ? [output.id] : []));
  const [options, setOptions] = useState<SceneAuthoringOptions | null>(null);
  const [resource, setResource] = useState("");
  const [outputId, setOutputId] = useState("");
  const [name, setName] = useState("");
  const [seconds, setSeconds] = useState("60");
  const [mode, setMode] = useState<CreateSceneRequest["mode"]>("controller_timed");
  const [completion, setCompletion] = useState<CreateSceneRequest["completion_policy"]>("loop");
  const [makeClockSource, setMakeClockSource] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true); setError(null);
    void getSceneAuthoringOptions(draft.project.id).then((value) => {
      if (!current) return;
      setOptions(value);
    }).catch((failure: unknown) => {
      if (current) setError(failure instanceof Error ? failure.message : "Could not load scene resources.");
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [draft.project.id, retry]);
  const resources = options?.resources.filter((item) =>
    kind === "scene" || !usedResources.has(item.resource)) ?? [];
  const binding = resources.find((item) => item.resource === resource);
  const durationMs = Math.round(Number(seconds) * 1000);
  const duplicateId = kind === "output" && usedIds.has(outputId);
  const stale = options !== null && options.revision !== draft.revision;
  const valid = Boolean(binding) && /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(outputId)
    && !duplicateId && !stale
    && (kind === "output" || Boolean(name.trim()) && Number.isSafeInteger(durationMs) && durationMs > 0);
  async function save() {
    if (!valid || saving) return;
    setSaving(true); setError(null);
    try {
      const id = kind === "scene" ? `scene-${crypto.randomUUID()}` : sceneId;
      const output = { output_id: outputId, resource };
      const updated = kind === "scene"
        ? await createScene(draft.project.id, draft.revision, {
          scene_id: id, name: name.trim(), duration_ms: durationMs, mode,
          completion_policy: completion, output,
        })
        : await addSceneOutput(draft.project.id, draft.revision, sceneId, {
          ...output, make_clock_source: makeClockSource,
        });
      onSaved(updated, id);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not save the scene output.");
    } finally {
      setSaving(false);
    }
  }
  const title = kind === "scene" ? "Add scene" : "Add output";
  return <DetailsDialog title={title} onClose={onClose} closeDisabled={saving}>
    <form aria-label={title} onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <p>{kind === "scene" ? "Start with one output; add more as needed."
        : `Add a playback or lighting slot to ${scene?.name ?? sceneId}. Existing content is preserved.`} Saving does not change live playback.</p>
      {loading && <p role="status">Loading available resources...</p>}
      {!loading && !options && <button type="button" onClick={() => setRetry((value) => value + 1)}>Retry resource discovery</button>}
      {stale && <p role="alert">This show has changed. Close this form and reload the show before editing.</p>}
      {!loading && options && resources.length === 0 && <p role="status">
        No unused media or lighting resources are available. Configure resources in this show's installation first.
      </p>}
      <fieldset disabled={loading || saving || !options || stale}>
        {kind === "scene" && <>
          <label>Scene name<input required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>Scene duration (seconds)<input required type="number" min="0.001" step="0.001"
            value={seconds} onChange={(event) => setSeconds(event.target.value)} /></label>
          <label>At the end<select value={completion} onChange={(event) => setCompletion(event.target.value as CreateSceneRequest["completion_policy"])}>
            <option value="loop">Loop</option><option value="hold">Hold</option><option value="stop">Stop</option>
          </select></label>
        </>}
        <label>Installation resource<select required value={resource} onChange={(event) => {
          const value = event.target.value;
          setResource(value);
          setOutputId(value.replace(/[^A-Za-z0-9._-]/g, "-").replace(/^[^A-Za-z0-9]+/, "").slice(0, 100));
          setMakeClockSource(false);
          if (kind === "scene") setMode(resources.find((item) => item.resource === value)?.kind === "media_agent"
            ? "media_synchronized" : "controller_timed");
        }}>
          <option value="">Choose a resource</option>
          {resources.map((item) => <option key={item.resource} value={item.resource}>
            {item.resource} · {item.kind === "media_agent" ? "Video / audio" : "LED lighting"}
            {options?.importable_resources.includes(item.resource) ? " (new installation resource)" : ""}
          </option>)}
        </select></label>
        <label>Output name / ID<input required maxLength={100} pattern="[A-Za-z0-9][A-Za-z0-9._\-]{0,99}"
          value={outputId} onChange={(event) => setOutputId(event.target.value)} /></label>
        <small>Start with a letter or number; use letters, numbers, dots, underscores or hyphens. This name appears when placing assets.</small>
        {duplicateId && <p role="alert">An output with this ID already exists. Choose another name.</p>}
        {kind === "scene" && <label>Scene clock<select value={mode}
          onChange={(event) => setMode(event.target.value as CreateSceneRequest["mode"])}>
          <option value="controller_timed">Controller timer</option>
          {binding?.kind === "media_agent" && <option value="media_synchronized">Media playback</option>}
        </select></label>}
        {kind === "output" && <>
          <p>Current clock: {scene?.mode === "controller_timed" ? "controller timer" : `media playback (${scene?.clock_source ?? "media"})`}. Kept unless explicitly changed.</p>
          {binding?.kind === "media_agent" && <label><input type="checkbox" checked={makeClockSource}
            onChange={(event) => setMakeClockSource(event.target.checked)} />Use this media output as the scene clock</label>}
          {scene?.schema_version === 1 && <p>This legacy scene will be upgraded to multi-output format, preserving its media, lighting, timing and policies.</p>}
        </>}
        {binding?.kind === "media_agent" && <p>After saving, use Assets to place media into this output. A projector surface is geometry, not another playback slot; surface-mapped compositions are configured separately.</p>}
        {binding?.kind === "pixel_output" && <p>The new lighting output starts with a full-duration blackout cue. Edit its effects before publishing.</p>}
        {options?.importable_resources.includes(resource) && <p>Only this selected resource binding will be copied into the show. Existing bindings are kept.</p>}
        <button type="submit" disabled={!valid || saving}>{saving ? "Saving..." : title}</button>
        <button type="button" onClick={onClose}>Cancel</button>
      </fieldset>
      {error && <p role="alert">{error}</p>}
    </form>
  </DetailsDialog>;
}
