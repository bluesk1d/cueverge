import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError, AssetRecord, getInstallationGeometry, getInstallationPhotos,
  getLightingSurface, InstallationGeometry, InstallationPhoto, installationPhotoUrl,
  previewSceneLighting, ProjectDraftRecord, SceneLightingPreview, LightingFrameView,
} from "../api";
import { geometryForBackground, photoPixelPoints } from "../components/pixel-traversal";
import { nightPreviewColor } from "../components/led-preview";
import { AssetPreview, hasAssetArtifact } from "../components/AssetPreview";
import { ErrorState } from "../components/ui";
import { SceneAuthoringDialog } from "../components/SceneAuthoringDialog";
import { SceneOutputActions } from "../components/SceneOutputActions";
import { SceneMediaOutputs } from "../components/SceneMediaOutputs";

type Document = ProjectDraftRecord["draft"];
type Scene = Document["scenes"][number];
type RecordValue = Record<string, unknown>;

const effects = {
  solid: "Solid color", blackout: "Blackout", envelope: "Fade / envelope",
  pulse: "Pulse", chase: "Chase", path_sweep: "Path sweep", flicker: "Flicker",
  sparkle: "Sparkle", ripple: "Ripple", color_transition: "Color transition",
};

function record(value: unknown): RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as RecordValue : {};
}

function objects(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function lighting(scene: Scene): RecordValue[] {
  return Array.isArray(scene.lighting) ? objects(scene.lighting)
    : scene.lighting && typeof scene.lighting === "object" ? [record(scene.lighting)] : [];
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function numeric(value: unknown, fallback: number): number | "" {
  return typeof value === "number" ? Number.isFinite(value) ? value : "" : fallback;
}

function time(value: number): string {
  return `${Math.floor(value / 60000)}:${String(Math.floor(value / 1000) % 60).padStart(2, "0")}`;
}

export function SceneWorkspace({
  draft, sceneId, canEdit, assets, onSelectScene, onSave, onDirtyChange,
  onOpenTimeline, onDelete, canAdd, onCreated,
}: {
  draft: ProjectDraftRecord;
  sceneId: string;
  canEdit: boolean;
  assets: AssetRecord[];
  onSelectScene: (id: string) => void;
  onSave: (document: Document) => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
  onOpenTimeline: () => void;
  onDelete: (id: string) => Promise<void>;
  canAdd: boolean;
  onCreated: (updated: ProjectDraftRecord, selectedSceneId: string) => void;
}) {
  const [document, setDocument] = useState<Document>(() => structuredClone(draft.draft));
  const [base, setBase] = useState({ revision: draft.revision, sceneId, document: draft.draft });
  const [outputIndex, setOutputIndex] = useState(0);
  const [effectId, setEffectId] = useState("");
  const [filter, setFilter] = useState("");
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [saving, setSaving] = useState(false);
  const [authoring, setAuthoring] = useState<"scene" | "output" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [pulseTimes, setPulseTimes] = useState<string | null>(null);
  const [targetNames, setTargetNames] = useState<Record<string, string>>({});
  const dirty = useMemo(
    () => base.revision === draft.revision && base.sceneId === sceneId &&
      JSON.stringify(document) !== JSON.stringify(base.document),
    [document, base, draft.revision, sceneId],
  );
  useEffect(() => {
    const sameSpace = base.revision === draft.revision && base.sceneId === sceneId;
    if (sameSpace && (dirty || base.document === draft.draft)) return;
    if (sameSpace && JSON.stringify(base.document) === JSON.stringify(draft.draft)) {
      setBase({ revision: draft.revision, sceneId, document: draft.draft });
      return;
    }
    setDocument(structuredClone(draft.draft));
    setBase({ revision: draft.revision, sceneId, document: draft.draft });
    setOutputIndex(0);
    setEffectId("");
    setPosition(0);
    setPlaying(false);
    setError(null);
  }, [draft.draft, draft.revision, sceneId, base, dirty]);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => setPulseTimes(null), [sceneId, outputIndex, draft.revision]);
  useEffect(() => setDeleteError(null), [sceneId]);
  const scene = document.scenes.find((candidate) => candidate.id === sceneId);
  const outputs = scene ? lighting(scene) : [];
  const output = outputs[outputIndex];
  const cues = objects(output?.timeline);
  const cue = cues.find((candidate) => candidate.id === effectId) ?? cues[0];
  const declaredDuration = scene?.duration_ms;
  const durationValid = typeof declaredDuration === "number" && Number.isSafeInteger(declaredDuration) && declaredDuration > 0;
  const duration = durationValid ? declaredDuration : 1000;
  useEffect(() => {
    setPosition((current) => Math.min(current, duration - 1));
    setPlaying(false);
  }, [duration, sceneId]);
  const issues = !durationValid ? ["Enter a positive scene duration in seconds."]
    : !scene?.name.trim() ? ["Enter a scene name."] : outputs.flatMap((value) => {
    const messages: string[] = [];
    const limit = value.brightness_limit;
    if (typeof limit === "number" && (!Number.isFinite(limit) || limit <= 0 || limit > 1)) {
      messages.push("Output brightness limit must be greater than 0 and at most 100%.");
    }
    for (const effect of objects(value.timeline)) {
      const start = Number(effect.start_ms);
      const length = Number(effect.duration_ms);
      if (!Number.isSafeInteger(start) || start < 0 || !Number.isSafeInteger(length) ||
        length <= 0 || start + length > duration) {
        messages.push(`${text(effect.id)}: start and duration must fit inside the scene.`);
      }
      const fades = Number(effect.fade_in_ms ?? 0) + Number(effect.fade_out_ms ?? 0);
      if (!Number.isFinite(fades) || fades < 0 || fades > length) {
        messages.push(`${text(effect.id)}: fades must fit inside the effect duration.`);
      }
      if (Object.values(effect).some((item) => typeof item === "number" && !Number.isFinite(item))) {
        messages.push(`${text(effect.id)}: enter valid numbers in all effect fields.`);
      }
      for (const key of ["brightness", "end_brightness", "density"]) {
        const amount = effect[key];
        if (typeof amount === "number" && (amount < 0 || amount > 1 || (key === "density" && amount === 0))) {
          messages.push(`${text(effect.id)}: ${key.replaceAll("_", " ")} must be within its percentage range.`);
        }
      }
      for (const key of ["seed", "precedence", "width_pixels", "period_ms"]) {
        const amount = effect[key];
        if (typeof amount === "number" && (!Number.isSafeInteger(amount) ||
          (["width_pixels", "period_ms"].includes(key) && amount <= 0))) {
          messages.push(`${text(effect.id)}: ${key.replaceAll("_", " ")} must be a valid whole number.`);
        }
      }
    }
    if (value.effect === "segment_pulse") {
      const times = Array.isArray(value.pulse_times_ms) ? value.pulse_times_ms : [];
      if (!times.length || times.some((item, index) => typeof item !== "number" ||
        !Number.isSafeInteger(item) || item < 0 || item + Number(value.pulse_duration_ms ?? 120) > duration ||
        (index > 0 && Number(times[index - 1]) >= item))) {
        messages.push("Pulse times must be increasing, valid seconds within the scene.");
      }
    }
    return messages;
  });
  const resource = text(output?.resource);
  const binding = objects(record(document.installation).resources)
    .find((candidate) => candidate.resource === resource);
  const objectNames = Object.fromEntries(objects(document.editor_metadata.installation_object_details)
    .filter((item) => item.resource === resource).map((item) => [`${text(item.kind)}:${text(item.id)}`, text(item.name)]));
  const targetName = (kind: string, id: string) => objectNames[`${kind}:${id}`] ?? targetNames[`${kind}:${id}`] ?? id.replaceAll("-", " ");
  const targets = (["segments", "fixtures", "paths"] as const).flatMap((kind) =>
    objects(binding?.[kind]).map((target) => ({
      key: `${kind === "segments" ? "segment" : kind === "fixtures" ? "fixture" : "path"}:${text(target.id)}`,
      label: `${targetName(kind === "segments" ? "segment" : kind === "fixtures" ? "fixture" : "path", text(target.id))} · ${kind.slice(0, -1)}`,
    })),
  );
  useEffect(() => {
    if (!playing) return;
    let previous = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now();
      const delta = now - previous;
      previous = now;
      setPosition((current) => {
        const next = current + delta;
        if (next >= duration) {
          if (scene?.completion?.policy === "loop") return Math.floor(next % duration);
          setPlaying(false);
          return duration - 1;
        }
        return Math.floor(next);
      });
    }, 250);
    return () => window.clearInterval(timer);
  }, [playing, duration, scene?.completion?.policy]);

  if (!scene) return <p>No scene is selected. Open advanced authoring to inspect this show.</p>;

  function changeOutput(update: (value: RecordValue) => RecordValue) {
    setDocument((current) => ({
      ...current,
      scenes: current.scenes.map((candidate) => {
        if (candidate.id !== sceneId) return candidate;
        const values = lighting(candidate).map((value, index) => index === outputIndex ? update(value) : value);
        return { ...candidate, lighting: Array.isArray(candidate.lighting) ? values : values[0] };
      }),
    }));
    setError(null);
  }

  function changeCue(values: RecordValue) {
    changeOutput((value) => ({
      ...value, timeline: objects(value.timeline).map((candidate) =>
        candidate.id === cue?.id ? { ...candidate, ...values } : candidate),
    }));
  }

  async function save() {
    setSaving(true);
    setError(null);
    setPlaying(false);
    try {
      await onSave(document);
    } catch (failure) {
      setError(`${failure instanceof Error ? failure.message : "Scene save failed"}. Your edits remain in this tab; nothing was made live.`);
    } finally {
      setSaving(false);
    }
  }

  function choose(id: string) {
    if (id === sceneId) return;
    if (!dirty || window.confirm("Discard unsaved scene edits and open another scene?")) onSelectScene(id);
  }

  async function removeScene(scene: Scene) {
    if (!window.confirm(`Delete scene "${scene.name}" from the saved show? Its scene-specific editor data will be removed. This does not change live playback.`)) return;
    setSaving(true);
    setDeleteError(null);
    setPlaying(false);
    try {
      await onDelete(sceneId);
    } catch (failure) {
      const message = failure instanceof ApiError && failure.status === 409
        ? "The show changed since you opened it. Reload the saved show before deleting a scene."
        : failure instanceof ApiError && failure.status === 422
          ? failure.message.replace(/^Cueverge API request failed \(422\): /, "")
          : failure instanceof Error ? failure.message : "Scene deletion failed";
      setDeleteError(`"${scene.name}" was not deleted. ${message}`);
    } finally {
      setSaving(false);
    }
  }

  const media = Array.isArray(scene.media) ? scene.media : [scene.media];
  const outputActionBlock = dirty || saving || !canAdd
    ? "Save or cancel pending scene and visitor-response edits before renaming or removing outputs."
    : null;
  const advancedContributions = [
    "timeline_cues", "timeline_transform_layers", "scene_compositions", "composition_instances",
  ].reduce((total, key) => {
    const values = record(document.editor_metadata[key])[sceneId];
    return total + (Array.isArray(values) ? values.length : 0);
  }, 0);
  const target = record(cue?.target);
  const supported = cue && Object.hasOwn(effects, text(cue.primitive));
  const formId = `scene-effect-${encodeURIComponent(sceneId)}`;
  return (
    <section className="scene-workspace" aria-label="Scene workspace">
      <nav className="scene-picker" aria-label="Scenes">
        {draft.draft.scenes.map((candidate) => (
          <button key={candidate.id} aria-pressed={candidate.id === sceneId} onClick={() => choose(candidate.id)}>
            <strong>{candidate.name}</strong>
            <small>{time(candidate.duration_ms ?? 0)} · {Array.isArray(candidate.media) ? candidate.media.length : 1} media · {lighting(candidate).length} lighting outputs</small>
          </button>
        ))}
      </nav>
      {canEdit && <div className="scene-save-controls">
        <button disabled={dirty || saving || !canAdd} onClick={() => setAuthoring("scene")}>Add scene</button>
        <button disabled={dirty || saving || !canAdd} onClick={() => setAuthoring("output")}>Add output</button>
        {(dirty || !canAdd) && <small>Save or cancel pending scene and visitor-response edits before adding scenes or outputs.</small>}
      </div>}
      {authoring && <SceneAuthoringDialog kind={authoring} draft={draft} sceneId={sceneId}
        onClose={() => setAuthoring(null)} onSaved={(updated, id) => {
          setAuthoring(null); onCreated(updated, id);
        }} />}
      <header className="scene-heading">
        <div>
          <h2>{scene.name}</h2>
          <p>{durationValid ? time(duration) : "Duration needs correction"} · {
            scene.completion?.policy === "loop" ? "Loops"
              : scene.completion?.policy === "stop" ? "Stops at the end"
                : scene.completion?.policy === "select_next" ? "Chooses another scene at the end"
                  : scene.completion?.policy === "transition_to" ? `Then: ${document.scenes.find((item) => item.id === scene.completion?.target_scene_id)?.name ?? scene.completion.target_scene_id}`
                    : "Holds at the end"
          }</p>
        </div>
        <div>
          <span role="status">{saving ? "Saving…" : dirty ? "Unsaved edits" : "Saved · not a live update"}</span>
          <button type="button" className="scene-open-timeline" disabled={dirty}
            title={dirty ? "Save or cancel scene edits before opening the timeline editor." : undefined}
            onClick={onOpenTimeline}>Edit timeline</button>
          {canEdit && <div className="scene-save-controls">
            <button form={formId} type="submit" disabled={!dirty || saving || issues.length > 0}>Save scene</button>
            <button type="button" disabled={!dirty || saving} onClick={() => {
              setDocument(structuredClone(draft.draft)); setPlaying(false); setError(null);
            }}>Cancel scene edits</button>
            <button type="button" disabled={dirty || saving || document.scenes.length <= 1}
              onClick={() => void removeScene(scene)}>Delete scene</button>
            {deleteError && <p role="alert" className="scene-delete-error">{deleteError}</p>}
            {dirty && <small>Save or cancel edits before deleting a scene.</small>}
            {document.scenes.length <= 1 && <small>Keep at least one scene in the show.</small>}
          </div>}
        </div>
      </header>
      <div className="scene-editor-grid">
        <div>
          {(outputs.length > 0 || media.length > 0) && <>
          {outputs.length > 0 && <ScenePreview
            projectId={draft.project.id} revision={draft.revision} document={document}
            dirty={dirty} sceneId={sceneId} resource="" position={position}
            valid={issues.length === 0}
            onTargetNames={setTargetNames}
            onFailure={() => setPlaying(false)}
          />}
          {media.length > 0 && <SceneMediaOutputs
            projectId={draft.project.id} revision={draft.revision} document={document}
            dirty={dirty} sceneId={sceneId} position={position} playing={playing}
            valid={issues.length === 0} assets={assets}
          />}
          <div className="scene-preview-controls">
            <button disabled={issues.length > 0} onClick={() => setPlaying(!playing)}>
              {playing ? "Pause scene preview" : "Play scene preview"}
            </button>
            <button onClick={() => { setPlaying(false); setPosition(0); }}>Restart scene preview</button>
            <label>Scene preview time (seconds)
              <input type="range" min="0" max={(duration - 1) / 1000} step="0.001" value={position / 1000}
                onChange={(event) => { setPlaying(false); setPosition(Math.round(Number(event.target.value) * 1000)); }} />
            </label>
            <span>{time(position)} / {time(duration)}</span>
          </div>
          </>}
          {outputs.length > 0 ? <>
          <label>Lighting output
            <select value={outputIndex} onChange={(event) => { setOutputIndex(Number(event.target.value)); setEffectId(""); }}>
              {outputs.map((value, index) => (
                <option key={index} value={index}>{text(value.id, text(value.resource))}</option>
              ))}
            </select>
          </label>
          {canEdit && output && <SceneOutputActions draft={draft} sceneId={sceneId}
            outputId={Array.isArray(scene.lighting) ? text(output.id) : "lighting"} kind="lighting"
            disabledReason={outputActionBlock} onChanged={(updated) => { setOutputIndex(0); onCreated(updated, sceneId); }} />}
          <label>Find an effect
            <input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} />
          </label>
          <div className="scene-effect-list" role="region" aria-label="Runtime lighting effects" tabIndex={0}>
            <p>{cues.length ? `${cues.length} effects · whole scene fitted below` : `Legacy effect: ${text(output?.effect, "unsupported representation")}`}</p>
            {cues.filter((value) => JSON.stringify([value.id, value.target, value.primitive]).toLowerCase().includes(filter.toLowerCase())).map((value) => (
              <button className="scene-effect-row" key={text(value.id)} aria-pressed={value.id === cue?.id}
                aria-label={`Edit effect ${text(value.id)}`} onClick={() => {
                  setEffectId(text(value.id)); setPlaying(false);
                  setPosition(Number(value.start_ms));
                }}>
                <span>{text(value.id).replaceAll("-", " ")}<small>{value.target ? targetName(text(record(value.target).kind), text(record(value.target).id)) : "Whole output"} · {text(value.primitive)}</small></span>
                <span className="scene-effect-track">
                  <span style={{
                    left: `${Number(value.start_ms) / duration * 100}%`,
                    width: `${Number(value.duration_ms) / duration * 100}%`,
                    backgroundColor: text(value.color, "#ffffff"),
                  }} />
                </span>
              </button>
            ))}
          </div>
          {canEdit && cues.length > 0 && <button onClick={() => {
            const id = `cue-${crypto.randomUUID()}`;
            changeOutput((value) => ({ ...value, timeline: [
              ...objects(value.timeline), { id, primitive: "solid", start_ms: 0, duration_ms: duration, color: "#7850B0", brightness: 0.2 },
            ] }));
            setEffectId(id);
          }}>Add light effect</button>}
          </> : <p>This is a media-only scene. Use Add output to include lighting.</p>}
          {advancedContributions > 0 && <div className="scene-advanced">
            <p>This scene also has {advancedContributions} synchronized cues, transform layers, or compositions. They are edited in the timeline editor.</p>
            <button onClick={onOpenTimeline}>Open timeline editor</button>
          </div>}
          {media.length > 0 && <details className="scene-advanced" open={outputs.length === 0}>
            <summary>Media in this scene ({media.length})</summary>
            <button onClick={onOpenTimeline}>Open timeline editor</button>
            {media.map((value, index) => {
              const asset = assets.find((candidate) => candidate.id === value.asset);
              return <div key={index}>
                <strong>{text(record(value).id, "Media output")}</strong>
                <p>{asset?.name ?? value.asset ?? "No managed media assigned"}</p>
                {asset && <SceneMediaPreview key={asset.id} asset={asset} />}
                {canEdit && <SceneOutputActions draft={draft} sceneId={sceneId}
                  outputId={Array.isArray(scene.media) ? text(record(value).id) : "media"} kind="media"
                  disabledReason={outputActionBlock} onChanged={(updated) => onCreated(updated, sceneId)} />}
                <small>Use the timeline editor for media placement and composition preview.</small>
              </div>;
            })}
          </details>}
        </div>
        <form id={formId} className="scene-effect-inspector" aria-label="Selected lighting effect"
          onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <h3>{cue ? text(cue.id).replaceAll("-", " ") : output ? "Legacy lighting" : "Scene settings"}</h3>
          <fieldset disabled={!canEdit || saving}>
            {cue && !supported ? <p role="alert">This effect is not supported by this editor. Its data is preserved; use advanced authoring.</p>
              : cue ? <>
                <label>Effect
                  <select value={text(cue.primitive)} onChange={(event) => changeCue({
                    primitive: event.target.value,
                    ...(event.target.value === "color_transition" && !cue.end_color ? { end_color: text(cue.color, "#ffffff") } : {}),
                  })}>
                    {Object.entries(effects).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select>
                </label>
                <label>Target decorations
                  <select value={cue.target ? `${text(target.kind)}:${text(target.id)}` : ""} onChange={(event) => {
                    const [kind, ...id] = event.target.value.split(":");
                    changeCue({ target: event.target.value ? { kind, id: id.join(":") } : null });
                  }}>
                    <option value="">Whole output</option>
                    {targets.map((value) => <option key={value.key} value={value.key}>{value.label}</option>)}
                  </select>
                </label>
                {cue.primitive !== "blackout" && <div className="scene-field-pair">
                  <label>Effect color<input type="color" value={text(cue.color, "#ffffff")} onChange={(event) => changeCue({ color: event.target.value })} /></label>
                  <NumberField label="Effect brightness (%)" value={numeric(cue.brightness, 1)} scale={100} min={0} max={100} onChange={(value) => changeCue({ brightness: value })} />
                </div>}
                <div className="scene-field-pair">
                <NumberField label="Start (seconds)" value={numeric(cue.start_ms, 0)} scale={0.001} min={0} max={duration / 1000} onChange={(value) => changeCue({ start_ms: Math.round(value) })} />
                <NumberField label="Duration (seconds)" value={numeric(cue.duration_ms, 1000)} scale={0.001} min={0.001} max={duration / 1000} onChange={(value) => changeCue({ duration_ms: Math.round(value) })} />
                </div>
                {["pulse", "flicker", "sparkle"].includes(text(cue.primitive)) &&
                  <NumberField label="Cycle (seconds)" value={numeric(cue.period_ms, 200)} scale={0.001} min={0.001} onChange={(value) => changeCue({ period_ms: Math.round(value) })} />}
                {["chase", "path_sweep", "ripple"].includes(text(cue.primitive)) &&
                  <NumberField label="Width (LEDs)" value={numeric(cue.width_pixels, 1)} min={1} onChange={(value) => changeCue({ width_pixels: value })} />}
                {cue.primitive === "sparkle" && <NumberField label="Sparkle density (%)" value={numeric(cue.density, 0.1)} scale={100} min={0} max={100} onChange={(value) => changeCue({ density: value })} />}
                {cue.primitive === "color_transition" && <label>End color<input type="color" value={text(cue.end_color, "#ffffff")} onChange={(event) => changeCue({ end_color: event.target.value })} /></label>}
                {["chase", "path_sweep", "ripple"].includes(text(cue.primitive)) && <label><input type="checkbox" checked={cue.reverse === true} onChange={(event) => changeCue({ reverse: event.target.checked })} />Reverse direction</label>}
                <details className="scene-advanced"><summary>Advanced effect settings</summary>
                  <NumberField label="Fade in (seconds)" value={numeric(cue.fade_in_ms, 0)} scale={0.001} min={0} onChange={(value) => changeCue({ fade_in_ms: Math.round(value) })} />
                  <NumberField label="Fade out (seconds)" value={numeric(cue.fade_out_ms, 0)} scale={0.001} min={0} onChange={(value) => changeCue({ fade_out_ms: Math.round(value) })} />
                  <label><input type="checkbox" checked={typeof cue.end_brightness === "number"} onChange={(event) => changeCue({ end_brightness: event.target.checked ? cue.brightness ?? 1 : null })} />Different end brightness</label>
                  {typeof cue.end_brightness === "number" && <NumberField label="End brightness (%)" value={numeric(cue.end_brightness, 1)} scale={100} min={0} max={100} onChange={(value) => changeCue({ end_brightness: value })} />}
                  {["flicker", "sparkle"].includes(text(cue.primitive)) && <NumberField label="Deterministic seed" value={numeric(cue.seed, 0)} onChange={(value) => changeCue({ seed: value })} />}
                  <NumberField label="Layer precedence" value={numeric(cue.precedence, 0)} min={0} max={1000} onChange={(value) => changeCue({ precedence: value })} />
                  <label>Blend<select value={text(cue.blend_mode, "replace")} onChange={(event) => changeCue({ blend_mode: event.target.value })}>
                    {["replace", "add", "max", "multiply"].map((value) => <option key={value}>{value}</option>)}
                  </select></label>
                  <small>Stable effect ID: {text(cue.id)}</small>
                </details>
                {canEdit && <button type="button" disabled={cues.length <= 1} onClick={() => {
                  if (window.confirm("Remove this effect from the saved scene when you next save?")) {
                    changeOutput((value) => ({ ...value, timeline: objects(value.timeline).filter((item) => item.id !== cue.id) }));
                  }
                }}>Remove effect</button>}
              </> : output ? <>
                <p>{text(output.effect)} · existing legacy representation retained</p>
                <label>Legacy color<input type="color" value={text(output.color, "#00ff60")} onChange={(event) => changeOutput((value) => ({ ...value, color: event.target.value }))} /></label>
                {output.effect === "segment_pulse" && <>
                  <label>Legacy target
                    <select
                      value={typeof output.segment === "string" ? `segment:${output.segment}` : `${text(record(output.target).kind)}:${text(record(output.target).id)}`}
                      onChange={(event) => {
                        const [kind, ...id] = event.target.value.split(":");
                        changeOutput((item) => typeof item.segment === "string" && kind === "segment"
                          ? { ...item, segment: id.join(":"), target: null }
                          : { ...item, segment: null, target: { kind, id: id.join(":") } });
                      }}
                    >{targets.map((value) => <option key={value.key} value={value.key}>{value.label}</option>)}</select>
                  </label>
                  <NumberField label="Pulse duration (seconds)" value={numeric(output.pulse_duration_ms, 120)} scale={0.001} min={0.001} onChange={(value) => changeOutput((item) => ({ ...item, pulse_duration_ms: Math.round(value) }))} />
                  <label>Pulse times (seconds, comma separated)
                    <input value={pulseTimes ?? (Array.isArray(output.pulse_times_ms) ? output.pulse_times_ms.map((value) => Number(value) / 1000).join(", ") : "")}
                      onChange={(event) => {
                        setPulseTimes(event.target.value);
                        changeOutput((item) => ({ ...item, pulse_times_ms: event.target.value.split(",").map((value) => value.trim() ? Math.round(Number(value) * 1000) : NaN) }));
                      }} />
                  </label>
                </>}
              </> : <p>No lighting outputs are configured in this scene.</p>}
            {output && <details className="scene-advanced"><summary>Output details</summary>
              <p>Resource: {resource}</p>
              {cues.length > 0 && <NumberField label="Output brightness limit (%)" value={numeric(output.brightness_limit, 1)} scale={100} min={0} max={100} onChange={(value) => changeOutput((item) => ({ ...item, brightness_limit: value }))} />}
            </details>}
            <details className="scene-advanced"><summary>Scene details</summary>
              <label>Scene name<input required maxLength={200} value={scene.name} onChange={(event) => {
                const name = event.target.value;
                setDocument((current) => ({ ...current, scenes: current.scenes.map((item) =>
                  item.id === sceneId ? { ...item, name } : item) }));
              }} /></label>
              <NumberField label="Scene duration (seconds)" value={numeric(scene.duration_ms, 1000)} scale={0.001} min={0.001} onChange={(value) => {
                setDocument((current) => ({ ...current, scenes: current.scenes.map((item) =>
                  item.id === sceneId ? { ...item, duration_ms: Math.round(value) } : item) }));
              }} />
              <small>Changing duration does not automatically trim effects, markers, or protected intervals.</small>
            </details>
          </fieldset>
          {error && <p role="alert">{error}</p>}
          {issues.length > 0 && <ul role="alert">{issues.map((message) => <li key={message}>{message}</li>)}</ul>}
          <small>Editing and preview never activate real outputs. Saved changes remain separate from live playback.</small>
        </form>
      </div>
    </section>
  );
}

function SceneMediaPreview({ asset }: { asset: AssetRecord }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <ErrorState title="Media preview unavailable" description="A managed review artifact could not be loaded. Inspect the asset details before relying on this preview." />;
  if (!hasAssetArtifact(asset, "review")) return <p>No managed review proxy is available for this media reference.</p>;
  return <div onErrorCapture={() => setFailed(true)}>
    <small>Media reference preview; independent of the LED scene clock and venue playback.</small>
    <AssetPreview asset={asset} />
  </div>;
}

function NumberField({ label, value, onChange, scale = 1, min, max }: {
  label: string; value: number | ""; onChange: (value: number) => void;
  scale?: number; min?: number; max?: number;
}) {
  const places = scale === 100 ? 2 : scale === 0.001 ? -3 : 0;
  return <label>{label}<input required type="number" step="any" min={min} max={max}
    value={value === "" ? "" : scaleDecimal(value, places)}
    onChange={(event) => onChange(scaleDecimal(event.target.valueAsNumber, -places))} /></label>;
}

function scaleDecimal(value: number, places: number): number {
  if (!places) return value;
  const [coefficient, exponent = "0"] = String(value).split("e");
  return Number(`${coefficient}e${Number(exponent) + places}`);
}

export function ScenePreview({ projectId, revision, document, dirty, sceneId, resource, position, onFailure, valid, onTargetNames, external = false, frameOverride = null, intent = false, installationIdOverride }: {
  projectId: string; revision: number; document?: Document; dirty: boolean; sceneId: string;
  resource: string; position: number; onFailure: () => void;
  valid: boolean;
  onTargetNames: (names: Record<string, string>) => void;
  external?: boolean; frameOverride?: LightingFrameView | null;
  intent?: boolean; installationIdOverride?: string;
}) {
  const [requestedFrame, setFrame] = useState<SceneLightingPreview | null>(null);
  const frame = external ? frameOverride : requestedFrame;
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [photos, setPhotos] = useState<InstallationPhoto[]>([]);
  const [geometry, setGeometry] = useState<InstallationGeometry[]>([]);
  const [resources, setResources] = useState<Record<string, string>>({});
  const [mapError, setMapError] = useState<string | null>(null);
  const [photoId, setPhotoId] = useState("");
  const [photoFailed, setPhotoFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const latestPosition = useRef(position);
  latestPosition.current = position;
  useEffect(() => setPhotoFailed(false), [photoId, retry]);
  const installationId = installationIdOverride ?? document?.editor_metadata.installation_id;
  useEffect(() => {
    setFrame(null);
    setLoading(false);
  }, [document, resource, sceneId]);
  useEffect(() => {
    if (external) {
      setLoading(false); setError(null);
      return;
    }
    if (!valid) {
      setFrame(null);
      setLoading(false);
      setError("Correct the scene fields to preview these edits.");
      return;
    }
    const scene = document?.scenes.find((value) => value.id === sceneId);
    const outputCount = Math.max(1, scene ? lighting(scene).length : 1);
    const pixelLimit = resource ? 4096 : Math.max(1, Math.floor(4096 / outputCount));
    let current = true;
    let abort = new AbortController();
    let watchdog: number | undefined;
    let pending = false;
    let previousPosition: number | null = null;
    const evaluate = () => {
      if (!current || pending || previousPosition === latestPosition.current) return;
      pending = true;
      const requestedPosition = latestPosition.current;
      previousPosition = requestedPosition;
      abort = new AbortController();
      setLoading(true);
      setError(null);
      watchdog = window.setTimeout(() => abort.abort(), 8000);
      previewSceneLighting(projectId, revision, sceneId, requestedPosition, resource || null, dirty ? document : undefined, abort.signal, pixelLimit)
        .then((value) => { if (current) setFrame(value); })
        .catch((failure: unknown) => {
          if (!current) return;
          setFrame(null);
          setError(abort.signal.aborted ? "Preview timed out. Try again after checking the controller."
            : failure instanceof Error ? failure.message : "Scene preview failed.");
          previousPosition = latestPosition.current;
          onFailure();
        }).finally(() => {
          window.clearTimeout(watchdog);
          pending = false;
          if (current) setLoading(false);
        });
    };
    const timer = window.setTimeout(evaluate, 120);
    const poll = window.setInterval(evaluate, 250);
    return () => { current = false; window.clearTimeout(timer); window.clearInterval(poll); window.clearTimeout(watchdog); abort.abort(); };
  }, [projectId, revision, sceneId, resource, document, dirty, valid, retry, external]);
  useEffect(() => {
    let current = true;
    setPhotos([]); setGeometry([]); setResources({}); setMapError(null);
    onTargetNames({});
    if (typeof installationId !== "string") return;
    Promise.all([getInstallationPhotos(installationId), getInstallationGeometry(installationId)])
      .then(async ([images, shapes]) => {
        const segments = shapes.filter((value) => value.kind === "pixel_segment");
        const surfaces = await Promise.allSettled(segments.map((value) => getLightingSurface(value.id)));
        if (!current) return;
        setPhotos(images); setPhotoId(images[0]?.id ?? ""); setGeometry(shapes);
        onTargetNames(Object.fromEntries(shapes.filter((value) =>
          ["fixture", "path", "pixel_segment"].includes(value.kind)).map((value) =>
          [`${value.kind === "pixel_segment" ? "segment" : value.kind}:${value.semantic_id}`, value.name])));
        setResources(Object.fromEntries(surfaces.flatMap((value, index) =>
          value.status === "fulfilled" ? [[segments[index].id, value.value.resource]] : [])));
        if (surfaces.some((value) => value.status === "rejected")) setMapError("Some saved mappings could not be read; photo coverage is incomplete.");
      }).catch(() => { if (current) setMapError("Installation mapping is unavailable. Output colors below are still evaluated independently."); });
    return () => { current = false; };
  }, [installationId, retry, onTargetNames]);
  const photo = photos.find((value) => value.id === photoId);
  const frameOutputs = frame?.outputs ?? [];
  const outputsByResource = new Map(frameOutputs.map((value) => [value.resource, value]));
  const photoGeometry = geometryForBackground(geometry, photoId || null)
    .filter((value) => resource ? resources[value.id] === resource : outputsByResource.has(resources[value.id]));
  const allocationMismatch = photoGeometry.some((value) => {
    const evaluated = outputsByResource.get(resources[value.id]);
    return evaluated !== undefined &&
      (value.start_index ?? 0) + (value.pixel_count ?? 0) > evaluated.pixel_count;
  });
  const mapped = (allocationMismatch ? [] : photoGeometry).flatMap((value) => {
    const evaluated = outputsByResource.get(resources[value.id]);
    if (!evaluated) return [];
    return photoPixelPoints(value)
      .filter((pixel) => pixel.outputIndex >= evaluated.pixel_offset &&
        pixel.outputIndex < evaluated.pixel_offset + evaluated.colors.length)
      .map((pixel) => ({ ...pixel, color: evaluated.colors[pixel.outputIndex - evaluated.pixel_offset] }));
  });
  const sampledCount = frameOutputs.reduce((total, value) => total + value.colors.length, 0);
  const outputLimitations = frameOutputs.flatMap((value) => value.limitations ?? []);
  const sceneLimitations = frame && "limitations" in frame && Array.isArray(frame.limitations)
    ? frame.limitations as string[] : [];
  return <section className="scene-preview" aria-label={intent ? "Controller intended LED output" : "Scene lighting preview"}>
    <header><strong>{intent ? "Last sent LED colors" : "Scene preview"}</strong><span>{intent ? "Read-only observation · not physical feedback" : "Simulation only · no hardware commands"}</span></header>
    {photos.length > 0 && <label>Preview photo<select value={photoId} onChange={(event) => setPhotoId(event.target.value)}>
      <option value="">Installation canvas (no photo)</option>
      {photos.map((value) => <option key={value.id} value={value.id}>{value.name}</option>)}
    </select></label>}
    {photoFailed && <ErrorState title="Photo unavailable" description="The photo could not be loaded; evaluated colors remain available below." />}
    {(!photoFailed && (photo || mapped.length > 0)) && <div className="scene-photo">
      <svg viewBox="0 0 1000 600" role="img" aria-label={intent ? "Last sent LED colors on installation photo" : "Evaluated LED colors on installation photo"}>
        <title>{photo?.name ?? "Installation layout"}</title>
        {photo && <image href={installationPhotoUrl(photo.id, photo.revision)} width="1000" height="600"
          preserveAspectRatio="xMidYMid meet" onError={() => setPhotoFailed(true)} />}
        <g className="scene-led-overlay">
          {mapped.map((pixel, index) => {
            const display = nightPreviewColor(pixel.color);
            return display === null ? null : <circle className="scene-led" key={index}
              cx={pixel.x * 1000} cy={pixel.y * 600} r="3"
              fill={display} style={{ color: display }} aria-hidden="true" />;
          })}
        </g>
      </svg>
    </div>}
    {!photoFailed && mapped.length > 0 && <small>Nighttime visualization · enhanced LED glow; output brightness is unchanged.</small>}
    {mapError && <p role="status">{mapError}</p>}
    {allocationMismatch && <p role="status">The saved photo uses a different LED allocation. The overlay is withheld; evaluated output colors remain available below.</p>}
    {error && <ErrorState title="Scene preview unavailable" description={error} />}
    {(error || mapError || photoFailed) && <button onClick={() => setRetry((value) => value + 1)}>Retry scene preview</button>}
    <p className="scene-preview-status" aria-busy={loading}>
      {frame
        ? <>{intent ? "Last sent position" : "Evaluated"} {time(frame.timestamp_ms)}.{String(frame.timestamp_ms % 1000).padStart(3, "0")}{!intent && <> · {frame.unsaved ? "unsaved edits" : "saved scene"}</>}</>
        : loading ? "Preparing scene preview…" : "No evaluated frame"}
    </p>
    {frame && <div>
      {!mapped.length && <p>No matching layout mapping. Map your lights in Installation to preview their placement; a photo is optional.</p>}
      <details>
        <summary>LED output details</summary>
        <p>Pixel address order, not physical placement. Scroll horizontally to inspect LED colors.</p>
        <div className="scene-pixel-strip" role="region" aria-label="LED colors in pixel address order" tabIndex={0}>
          <div role="img" aria-label="Evaluated output colors">
            {frame.outputs.flatMap((value) => value.colors.map((color, index) => (
              <span key={`${value.output_id}:${index}`} style={{ backgroundColor: `rgb(${color.join(",")})` }} title={`LED ${value.pixel_offset + index}`} />
            )))}
          </div>
        </div>
        <small>{mapped.length} photo locations · {sampledCount} sampled LEDs{frameOutputs.length > 1 ? ` across ${frameOutputs.length} outputs` : ""}.</small>
        {intent && outputLimitations.map((message) => <p role="status" key={message}>{message}</p>)}
      </details>
      <small>
        {frameOutputs.some((value) => value.next_pixel_offset !== null) && " Output is paged; only the sampled range is shown."}
        {mapped.length > 0 && mapped.length < sampledCount && " The photo does not cover every sampled LED."}
      </small>
      {!intent && outputLimitations.map((message) => <p role="status" key={message}>{message}</p>)}
      {sceneLimitations.map((message) => <p role="status" key={message}>{message}</p>)}
    </div>}
  </section>;
}
