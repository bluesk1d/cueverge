import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, previewAmbientLighting, ProjectDraftRecord } from "../api";
import type { AmbientLightingPreview } from "../api";
import { ScenePreview } from "../pages/SceneWorkspace";
import { LightingCueFields, LightingCueList, lightingCueIssues, lightingTargets } from "./LightingCueEditor";

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

function time(value: number): string {
  return `${Math.floor(value / 60000)}:${String(Math.floor(value / 1000) % 60).padStart(2, "0")}.${String(Math.floor(value) % 1000).padStart(3, "0")}`;
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

/** One whole-output effect with a simple look: edited with the quick look controls. */
function isSimpleLook(output: RecordValue): boolean {
  const cues = objects(output.cues);
  return !text(output.composition_id) && cues.length === 1 && !cues[0].target &&
    Object.hasOwn(looks, text(cues[0].primitive, "solid"));
}

function newCueId(): string {
  return `ambient-${crypto.randomUUID().slice(0, 8)}`;
}

/**
 * Show-wide LED ambient: one loop per LED output that keeps running under every scene that
 * inherits it. Scene lighting draws over it; LEDs a scene does not light keep the ambient.
 * Every effect in the loop (including ones written through MCP) is listed and editable.
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
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [targetNames, setTargetNames] = useState<Record<string, string>>({});
  const [position, setPosition] = useState(0);
  const [open, setOpen] = useState(false);
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
  const issues = outputs.flatMap((output) => {
    const cycle = amount(output.cycle_ms, 0);
    const messages = cycle <= 0 ? [`${text(output.resource)}: enter a loop length.`] : lightingCueIssues(objects(output.cues), cycle, "loop")
      .map((message) => `${text(output.resource)} · ${message}`);
    if (!text(output.composition_id) && objects(output.cues).length === 0) {
      messages.push(`${text(output.resource)}: add an effect or choose an LED composition loop.`);
    }
    return messages;
  });
  const document = useMemo(() => {
    const metadata = { ...draft.draft.editor_metadata };
    if (outputs.length === 0 && excluded.length === 0) delete metadata.ambient_lighting;
    else metadata.ambient_lighting = settings;
    return { ...draft.draft, editor_metadata: metadata };
  }, [draft.draft, settings, outputs.length, excluded.length]);
  const onTargetNames = useCallback((names: Record<string, string>) => setTargetNames(names), []);

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

  function changeSimpleLook(resource: string, patch: RecordValue) {
    change(resource, (value) => {
      const cues = objects(value.cues);
      const first: RecordValue = { id: "ambient", start_ms: 0, primitive: "solid", color: "#FFB060", ...cues[0], ...patch };
      first.duration_ms = amount(value.cycle_ms, 10000) - amount(first.start_ms, 0);
      return { ...value, cues: [first, ...cues.slice(1)] };
    });
  }

  function changeCue(resource: string, id: string, patch: RecordValue) {
    change(resource, (value) => ({
      ...value, cues: objects(value.cues).map((cue) => cue.id === id ? { ...cue, ...patch } : cue),
    }));
  }

  function addCue(resource: string, source?: RecordValue) {
    const id = newCueId();
    change(resource, (value) => {
      const cycle = amount(value.cycle_ms, 10000);
      const cue = source ? { ...source, id } : {
        id, primitive: "solid", start_ms: 0, duration_ms: cycle, color: "#7850B0", brightness: 0.2,
      };
      return { ...value, cues: [...objects(value.cues), cue] };
    });
    setSelected((current) => ({ ...current, [resource]: id }));
  }

  function removeCue(resource: string, id: string) {
    if (!window.confirm("Remove this effect from the ambient loop when you next save?")) return;
    change(resource, (value) => ({ ...value, cues: objects(value.cues).filter((cue) => cue.id !== id) }));
  }

  async function save() {
    setSaving(true); setError(null);
    try {
      await onSave(document);
    } catch (failure) {
      setError(failure instanceof ApiError && failure.status === 409
        ? "The show changed since you opened it. Reload it before saving ambient lighting."
        : failure instanceof Error ? failure.message.replace(/^Cueverge API request failed \(\d+\): /, "")
          : "Ambient lighting was not saved.");
    } finally {
      setSaving(false);
    }
  }

  const longestCycle = Math.max(1000, ...outputs.map((value) => amount(value.cycle_ms, 0)));
  const effectCount = outputs.reduce((total, value) => total + objects(value.cues).length, 0);
  return <details className="show-ambient-lighting" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><span>Ambient lighting</span>{outputs.length > 0 && <small> · {effectCount} effect{effectCount === 1 ? "" : "s"} on {outputs.length} output{outputs.length === 1 ? "" : "s"}</small>}</summary>
    <p>Ambient lighting loops on each LED output for the whole show. Scenes inherit it by default:
      their lighting draws over it, and LEDs a scene does not light keep the ambient, so it stays
      continuous across scene changes.</p>
    {resources.length === 0 && <p>This show has no LED outputs.</p>}
    {resources.map((resource) => {
      const output = outputs.find((value) => value.resource === resource);
      const cues = objects(output?.cues);
      const composition = text(output?.composition_id);
      const cycle = amount(output?.cycle_ms, 10000);
      const simple = output !== undefined && isSimpleLook(output);
      const cue = cues[0];
      const primitive = text(cue?.primitive, "solid");
      const { targets, targetName } = lightingTargets(draft.draft.installation, draft.draft.editor_metadata, resource, targetNames);
      const active = cues.find((value) => value.id === selected[resource]) ?? cues[0];
      return <section key={resource} className="ambient-output" aria-label={`Ambient lighting for ${resource}`}>
        <fieldset disabled={!canEdit || saving}>
          <label><input type="checkbox" checked={Boolean(output)}
            onChange={(event) => change(resource, event.target.checked ? (value) => value : null)} />
            Ambient lighting on {resource}</label>
          {output && <>
            <label>Source
              <select aria-label="Source" value={composition ? "composition" : "look"} onChange={(event) => change(resource, (value) => {
                if (event.target.value === "look") {
                  const rest = { ...value };
                  delete rest.composition_id;
                  return objects(rest.cues).length ? rest : { ...rest, cues: objects(defaultLook(resource).cues).map((item) => ({
                    ...item, duration_ms: amount(rest.cycle_ms, 10000),
                  })) };
                }
                // The composition loops over any effects; a lone default look gives way to it.
                const kept = objects(value.cues).filter((item) => item.id !== "ambient");
                return { ...value, composition_id: text(compositions[0]?.id), cues: kept };
              })}>
                <option value="look">Effects</option>
                <option value="composition" disabled={compositions.length === 0}>LED composition loop</option>
              </select>
            </label>
            {composition && <>
              <label>Composition
                <select aria-label="Composition" value={composition} onChange={(event) => change(resource, (value) => ({ ...value, composition_id: event.target.value }))}>
                  {compositions.map((value) => <option key={value.id} value={value.id}>{text(value.name, value.id)}</option>)}
                </select>
              </label>
              <small>The composition&apos;s LED bake loops for the whole show over any effects below. Bake it in its timeline
                (Render &amp; bake) or from a scene&apos;s LED bake chip after changing what it draws;
                publishing needs a current bake.</small>
            </>}
            {simple && <>
              <label>Look
                <select aria-label="Look" value={primitive} onChange={(event) => changeSimpleLook(resource, { primitive: event.target.value })}>
                  {(Object.keys(looks) as Look[]).map((value) => <option key={value} value={value}>{looks[value]}</option>)}
                </select>
              </label>
              <label>Color<input type="color" value={text(cue?.color, "#FFB060").toLowerCase()}
                onChange={(event) => changeSimpleLook(resource, { color: event.target.value.toUpperCase() })} /></label>
              <label>Brightness (%)<input type="number" min={0} max={100} step="any"
                value={Math.round(amount(cue?.brightness, 1) * 1000) / 10}
                onChange={(event) => changeSimpleLook(resource, { brightness: Math.min(1, Math.max(0, event.target.valueAsNumber / 100 || 0)) })} /></label>
              {primitive !== "solid" && <label>Speed: period (seconds)<input type="number" min={0.05} step="any"
                value={amount(cue?.period_ms, 200) / 1000}
                onChange={(event) => changeSimpleLook(resource, { period_ms: Math.max(1, Math.round(event.target.valueAsNumber * 1000) || 1) })} /></label>}
            </>}
            <label>Loop length (seconds)<input type="number" min={0.1} max={3600} step="any"
              value={cycle / 1000}
              onChange={(event) => {
                const next = Math.min(3600000, Math.max(100, Math.round(event.target.valueAsNumber * 1000) || 100));
                change(resource, (value) => ({ ...value, cycle_ms: next }));
                if (simple) changeSimpleLook(resource, {});
              }} /></label>
            {!simple && cues.some((value) => amount(value.start_ms, 0) + amount(value.duration_ms, 0) > cycle) &&
              <small role="status">Some effects run past the loop. Shorten or move them, or lengthen the loop.</small>}
            <label>Brightness limit (%)<input type="number" min={1} max={100} step="any"
              value={Math.round(amount(output.brightness_limit, 1) * 1000) / 10}
              onChange={(event) => change(resource, (value) => ({
                ...value, brightness_limit: Math.min(1, Math.max(0.01, event.target.valueAsNumber / 100 || 0.01)),
              }))} /></label>
          </>}
        </fieldset>
        {output && !simple && <div className="ambient-effects">
          {cues.length > 0 ? <>
            <LightingCueList cues={cues} duration={cycle} selectedId={text(active?.id) || undefined}
              filter={filters[resource] ?? ""} label={`Ambient effects on ${resource}`}
              onFilter={(value) => setFilters((current) => ({ ...current, [resource]: value }))}
              targetName={targetName}
              summary={`${cues.length} effect${cues.length === 1 ? "" : "s"} · ${cycle / 1000} s loop fitted below${composition ? " · over the composition loop" : ""}`}
              onSelect={(value) => {
                setSelected((current) => ({ ...current, [resource]: text(value.id) }));
                setPosition(amount(value.start_ms, 0));
              }} />
            {active && <fieldset className="ambient-effect-inspector" disabled={!canEdit || saving}
              aria-label={`Selected ambient effect on ${resource}`}>
              <legend>{text(active.id).replaceAll("-", " ")}</legend>
              <LightingCueFields cue={active} duration={cycle} targets={targets}
                onChange={(patch) => changeCue(resource, text(active.id), patch)} />
              {canEdit && <div className="project-card__actions">
                <button type="button" onClick={() => addCue(resource, active)}>Duplicate effect</button>
                <button type="button" disabled={cues.length <= 1 && !composition}
                  title={cues.length <= 1 && !composition ? "Keep at least one effect, or choose an LED composition loop." : undefined}
                  onClick={() => removeCue(resource, text(active.id))}>Remove effect</button>
              </div>}
            </fieldset>}
          </> : <p>No effects run under the composition loop.</p>}
        </div>}
        {output && canEdit && <button type="button" disabled={saving} onClick={() => addCue(resource)}>Add ambient effect</button>}
      </section>;
    })}
    {outputs.length > 0 && <fieldset className="ambient-scenes" disabled={!canEdit || saving}>
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
    {open && outputs.length > 0 && <AmbientLoopPreview projectId={draft.project.id} revision={draft.revision}
      document={document} dirty={dirty} valid={issues.length === 0} cycleMs={longestCycle}
      position={position} onPosition={setPosition} onTargetNames={onTargetNames} />}
    {issues.length > 0 && <ul role="alert">
      {issues.slice(0, 5).map((message) => <li key={message}>{message}</li>)}
      {issues.length > 5 && <li>and {issues.length - 5} more</li>}
    </ul>}
    {canEdit && <div className="project-card__actions">
      <button disabled={!dirty || saving || blockedReason !== null || issues.length > 0} onClick={() => void save()}>{saving ? "Saving…" : "Save ambient lighting"}</button>
      <button disabled={!dirty || saving} onClick={() => { setSettings(saved); setError(null); }}>Cancel</button>
    </div>}
    {canEdit && dirty && blockedReason && <small>{blockedReason}</small>}
    {error && <p role="alert">{error}</p>}
  </details>;
}

/** The ambient alone on the installation, evaluated by the controller's lighting renderer. */
function AmbientLoopPreview({ projectId, revision, document, dirty, valid, cycleMs, position, onPosition, onTargetNames }: {
  projectId: string; revision: number; document: Document; dirty: boolean; valid: boolean;
  cycleMs: number; position: number; onPosition: (value: number) => void;
  onTargetNames: (names: Record<string, string>) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const [frame, setFrame] = useState<AmbientLightingPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(position);
  latest.current = position;
  useEffect(() => {
    if (!playing) return;
    let previous = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now();
      const next = latest.current + now - previous;
      previous = now;
      onPosition(Math.floor(next % cycleMs));
    }, 250);
    return () => window.clearInterval(timer);
  }, [playing, cycleMs, onPosition]);
  useEffect(() => {
    if (!valid) {
      setFrame(null);
      setError("Correct the ambient effects to preview these edits.");
      return;
    }
    let current = true;
    let pending = false;
    let previous: number | null = null;
    let abort = new AbortController();
    const evaluate = () => {
      if (!current || pending || previous === latest.current) return;
      pending = true;
      previous = latest.current;
      abort = new AbortController();
      previewAmbientLighting(projectId, revision, latest.current, null, dirty ? document : undefined, abort.signal)
        .then((value) => { if (current) { setFrame(value); setError(null); } })
        .catch((failure: unknown) => {
          if (!current || abort.signal.aborted) return;
          setFrame(null);
          setPlaying(false);
          setError(failure instanceof Error ? failure.message.replace(/^Cueverge API request failed \(\d+\): /, "") : "Ambient preview failed.");
        })
        .finally(() => { pending = false; });
    };
    const timer = window.setTimeout(evaluate, 120);
    const poll = window.setInterval(evaluate, 250);
    return () => { current = false; window.clearTimeout(timer); window.clearInterval(poll); abort.abort(); };
  }, [projectId, revision, document, dirty, valid]);
  const noop = useCallback(() => setPlaying(false), []);
  return <section className="ambient-preview" aria-label="Ambient lighting preview">
    <div className="scene-preview-controls">
      <button type="button" disabled={!valid} onClick={() => setPlaying(!playing)}>
        {playing ? "Pause ambient preview" : "Play ambient preview"}
      </button>
      <label>Ambient preview time (seconds)
        <input type="range" min="0" max={(cycleMs - 1) / 1000} step="0.001" value={Math.min(position, cycleMs - 1) / 1000}
          onChange={(event) => { setPlaying(false); onPosition(Math.round(Number(event.target.value) * 1000)); }} />
      </label>
      <span>{time(position)} / {time(cycleMs)}</span>
    </div>
    {error && <p role="alert">{error}</p>}
    <ScenePreview projectId={projectId} revision={revision} document={document} dirty={dirty}
      sceneId="ambient-preview" resource="" position={position} valid external subject="ambient"
      frameOverride={frame} onFailure={noop} onTargetNames={onTargetNames} />
    {frame?.limitations.map((message) => <small key={message}>{message}</small>)}
  </section>;
}
