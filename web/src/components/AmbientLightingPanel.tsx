import { useEffect, useMemo, useState } from "react";

import { ApiError, ProjectDraftRecord } from "../api";

type Document = ProjectDraftRecord["draft"];
type RecordValue = Record<string, unknown>;

const looks = {
  solid: "Steady color", pulse: "Pulse", flicker: "Flicker", sparkle: "Sparkle", chase: "Chase",
} as const;
type Look = keyof typeof looks;

function record(value: unknown): RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
}

function objects(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function amount(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function settingsOf(document: Document): RecordValue {
  const current = record(document.editor_metadata.ambient_lighting);
  return {
    ...current,
    schema_version: 1,
    outputs: objects(current.outputs),
    excluded_scene_ids: Array.isArray(current.excluded_scene_ids) ? current.excluded_scene_ids : [],
  };
}

function defaultLook(resource: string): RecordValue {
  return {
    resource, cycle_ms: 10000, brightness_limit: 1,
    cues: [{ id: "ambient", primitive: "solid", start_ms: 0, duration_ms: 10000, color: "#FFB060", brightness: 0.3 }],
  };
}

/**
 * Show-wide LED ambient: one loop per LED output that keeps running under every scene that
 * inherits it. Scene lighting draws over it; LEDs a scene does not light keep the ambient.
 */
export function AmbientLightingPanel({ draft, canEdit, blockedReason = null, onSave }: {
  draft: ProjectDraftRecord;
  canEdit: boolean;
  blockedReason?: string | null;
  onSave: (document: Document) => Promise<void>;
}) {
  const [settings, setSettings] = useState(() => settingsOf(draft.draft));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const saved = useMemo(() => settingsOf(draft.draft), [draft.draft]);
  const dirty = JSON.stringify(settings) !== JSON.stringify(saved);
  useEffect(() => { setSettings(settingsOf(draft.draft)); setError(null); }, [draft.revision, draft.draft]);
  const resources = objects(record(draft.draft.installation).resources)
    .filter((value) => value.kind === "pixel_output").map((value) => text(value.resource));
  const compositions = Object.entries(record(draft.draft.editor_metadata.compositions))
    .map(([id, value]): RecordValue & { id: string } => ({ ...record(value), id }))
    .filter((value) => value.space_kind === "stage" && Array.isArray(value.stage_lighting_surfaces)
      && value.stage_lighting_surfaces.length > 0);
  const outputs = objects(settings.outputs);
  const excluded = (settings.excluded_scene_ids as unknown[]).filter((value): value is string => typeof value === "string");

  function change(resource: string, update: ((value: RecordValue) => RecordValue) | null) {
    setSettings((current) => {
      const list = objects(current.outputs);
      const existing = list.find((value) => value.resource === resource);
      const next = update === null ? list.filter((value) => value.resource !== resource)
        : existing ? list.map((value) => value === existing ? update(value) : value)
          : [...list, update(defaultLook(resource))];
      return { ...current, outputs: next };
    });
  }

  function changeCue(resource: string, patch: RecordValue) {
    change(resource, (value) => {
      const cues = objects(value.cues);
      const first: RecordValue = { id: "ambient", start_ms: 0, primitive: "solid", color: "#FFB060", ...cues[0], ...patch };
      first.duration_ms = amount(value.cycle_ms, 10000) - amount(first.start_ms, 0);
      return { ...value, cues: [first, ...cues.slice(1)] };
    });
  }

  async function save() {
    setSaving(true); setError(null);
    try {
      const metadata = { ...draft.draft.editor_metadata };
      if (objects(settings.outputs).length === 0 && excluded.length === 0) delete metadata.ambient_lighting;
      else metadata.ambient_lighting = settings;
      await onSave({ ...draft.draft, editor_metadata: metadata });
    } catch (failure) {
      setError(failure instanceof ApiError && failure.status === 409
        ? "The show changed since you opened it. Reload it before saving ambient lighting."
        : failure instanceof Error ? failure.message.replace(/^Cueverge API request failed \(\d+\): /, "")
          : "Ambient lighting was not saved.");
    } finally {
      setSaving(false);
    }
  }

  return <details className="show-ambient-lighting">
    <summary>Ambient lighting</summary>
    <p>Ambient lighting loops on each LED output for the whole show. Scenes inherit it by default:
      their lighting draws over it, and LEDs a scene does not light keep the ambient, so it stays
      continuous across scene changes.</p>
    {resources.length === 0 && <p>This show has no LED outputs.</p>}
    <fieldset disabled={!canEdit || saving}>
      {resources.map((resource) => {
        const output = outputs.find((value) => value.resource === resource);
        const cues = objects(output?.cues);
        const cue = cues[0];
        const composition = text(output?.composition_id);
        const primitive = text(cue?.primitive, "solid");
        const known = Object.hasOwn(looks, primitive);
        return <section key={resource} className="ambient-output" aria-label={`Ambient lighting for ${resource}`}>
          <label><input type="checkbox" checked={Boolean(output)}
            onChange={(event) => change(resource, event.target.checked ? (value) => value : null)} />
            Ambient lighting on {resource}</label>
          {output && <>
            <label>Source
              <select aria-label="Source" value={composition ? "composition" : "look"} onChange={(event) => change(resource, (value) => {
                const kept = objects(value.cues).filter((item) => item.id !== "ambient");
                if (event.target.value === "look") {
                  const rest = { ...value };
                  delete rest.composition_id;
                  return { ...rest, cues: [...objects(defaultLook(resource).cues).map((item) => ({
                    ...item, duration_ms: amount(rest.cycle_ms, 10000),
                  })), ...kept] };
                }
                // The simple look gives way to the composition; cues added through MCP stay.
                return { ...value, composition_id: text(compositions[0]?.id), cues: kept };
              })}>
                <option value="look">Simple look</option>
                <option value="composition" disabled={compositions.length === 0}>LED composition loop</option>
              </select>
            </label>
            {composition ? <>
              <label>Composition
                <select aria-label="Composition" value={composition} onChange={(event) => change(resource, (value) => ({ ...value, composition_id: event.target.value }))}>
                  {compositions.map((value) => <option key={value.id} value={value.id}>{text(value.name, value.id)}</option>)}
                </select>
              </label>
              <small>The composition&apos;s LED bake loops for the whole show. Bake it in its timeline
                (Render &amp; bake) after every change; publishing needs a current bake.</small>
            </> : cue && !known ? <p>This look uses the {primitive} effect; edit it with MCP or advanced authoring. It is kept as is.</p>
              : <>
                <label>Look
                  <select aria-label="Look" value={primitive} onChange={(event) => changeCue(resource, { primitive: event.target.value })}>
                    {(Object.keys(looks) as Look[]).map((value) => <option key={value} value={value}>{looks[value]}</option>)}
                  </select>
                </label>
                <label>Color<input type="color" value={text(cue?.color, "#FFB060").toLowerCase()}
                  onChange={(event) => changeCue(resource, { color: event.target.value.toUpperCase() })} /></label>
                <label>Brightness (%)<input type="number" min={0} max={100} step="any"
                  value={Math.round(amount(cue?.brightness, 1) * 1000) / 10}
                  onChange={(event) => changeCue(resource, { brightness: Math.min(1, Math.max(0, event.target.valueAsNumber / 100 || 0)) })} /></label>
                {primitive !== "solid" && <label>Speed: period (seconds)<input type="number" min={0.05} step="any"
                  value={amount(cue?.period_ms, 200) / 1000}
                  onChange={(event) => changeCue(resource, { period_ms: Math.max(1, Math.round(event.target.valueAsNumber * 1000) || 1) })} /></label>}
                <label>Loop length (seconds)<input type="number" min={0.1} max={3600} step="any"
                  value={amount(output.cycle_ms, 10000) / 1000}
                  onChange={(event) => {
                    const cycle = Math.min(3600000, Math.max(100, Math.round(event.target.valueAsNumber * 1000) || 100));
                    change(resource, (value) => ({ ...value, cycle_ms: cycle }));
                    changeCue(resource, {});
                  }} /></label>
                {cues.length > 1 && <small>{cues.length - 1} more effect{cues.length > 2 ? "s" : ""} set through MCP are kept.</small>}
              </>}
            <label>Brightness limit (%)<input type="number" min={1} max={100} step="any"
              value={Math.round(amount(output.brightness_limit, 1) * 1000) / 10}
              onChange={(event) => change(resource, (value) => ({
                ...value, brightness_limit: Math.min(1, Math.max(0.01, event.target.valueAsNumber / 100 || 0.01)),
              }))} /></label>
          </>}
        </section>;
      })}
      {outputs.length > 0 && <fieldset className="ambient-scenes">
        <legend>Scenes that inherit ambient lighting</legend>
        {draft.draft.scenes.map((scene) => <label key={scene.id}>
          <input type="checkbox" checked={!excluded.includes(scene.id)} onChange={(event) => setSettings((current) => ({
            ...current,
            excluded_scene_ids: event.target.checked ? excluded.filter((value) => value !== scene.id) : [...excluded, scene.id],
          }))} />
          {scene.name}
        </label>)}
        <small>Unchecked scenes draw their lighting over black and set other LEDs to their safe state.</small>
      </fieldset>}
    </fieldset>
    {canEdit && <div className="project-card__actions">
      <button disabled={!dirty || saving || blockedReason !== null} onClick={() => void save()}>{saving ? "Saving…" : "Save ambient lighting"}</button>
      <button disabled={!dirty || saving} onClick={() => { setSettings(saved); setError(null); }}>Cancel</button>
    </div>}
    {canEdit && dirty && blockedReason && <small>{blockedReason}</small>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
