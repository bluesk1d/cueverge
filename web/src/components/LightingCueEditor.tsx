import type { ReactNode } from "react";

/**
 * Procedural lighting cue (LightingCue) editing shared by scene lighting and the show-wide
 * ambient, so effects authored through MCP are listed and editable in both places.
 */

type RecordValue = Record<string, unknown>;

export const LIGHTING_EFFECTS = {
  solid: "Solid color", blackout: "Blackout", envelope: "Fade / envelope",
  pulse: "Pulse", chase: "Chase", path_sweep: "Path sweep", flicker: "Flicker",
  sparkle: "Sparkle", ripple: "Ripple", color_transition: "Color transition",
};

export interface LightingTarget { key: string; label: string }

function record(value: unknown): RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as RecordValue : {};
}

function objects(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function numeric(value: unknown, fallback: number): number | "" {
  return typeof value === "number" ? Number.isFinite(value) ? value : "" : fallback;
}

/** Decorations an LED output's cues can target, named from installation objects when known. */
export function lightingTargets(
  installation: unknown,
  editorMetadata: RecordValue,
  resource: string,
  knownNames: Record<string, string> = {},
): { targets: LightingTarget[]; targetName: (kind: string, id: string) => string } {
  const binding = objects(record(installation).resources)
    .find((candidate) => candidate.resource === resource);
  const objectNames = Object.fromEntries(objects(editorMetadata.installation_object_details)
    .filter((item) => item.resource === resource)
    .map((item) => [`${text(item.kind)}:${text(item.id)}`, text(item.name)]));
  const targetName = (kind: string, id: string) =>
    objectNames[`${kind}:${id}`] ?? knownNames[`${kind}:${id}`] ?? id.replaceAll("-", " ");
  const targets = (["segments", "fixtures", "paths"] as const).flatMap((kind) => {
    const single = kind === "segments" ? "segment" : kind === "fixtures" ? "fixture" : "path";
    return objects(binding?.[kind]).map((target) => ({
      key: `${single}:${text(target.id)}`,
      label: `${targetName(single, text(target.id))} · ${kind.slice(0, -1)}`,
    }));
  });
  return { targets, targetName };
}

/** Problems that would make the saved cues invalid; `span` is "scene" or "loop". */
export function lightingCueIssues(cues: RecordValue[], duration: number, span = "scene"): string[] {
  const messages: string[] = [];
  for (const effect of cues) {
    const start = Number(effect.start_ms);
    const length = Number(effect.duration_ms);
    if (!Number.isSafeInteger(start) || start < 0 || !Number.isSafeInteger(length) ||
      length <= 0 || start + length > duration) {
      messages.push(`${text(effect.id)}: start and duration must fit inside the ${span}.`);
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
  return messages;
}

/** Searchable effect rows with each effect's span drawn across the whole scene or loop. */
export function LightingCueList({
  cues, duration, selectedId, filter, onFilter, onSelect, targetName, summary,
  label = "Runtime lighting effects",
}: {
  cues: RecordValue[];
  duration: number;
  selectedId: string | undefined;
  filter: string;
  onFilter: (value: string) => void;
  onSelect: (cue: RecordValue) => void;
  targetName: (kind: string, id: string) => string;
  summary: ReactNode;
  label?: string;
}) {
  return <>
    <label>Find an effect
      <input type="search" value={filter} onChange={(event) => onFilter(event.target.value)} />
    </label>
    <div className="scene-effect-list" role="region" aria-label={label} tabIndex={0}>
      <p>{summary}</p>
      {cues.filter((value) => JSON.stringify([value.id, value.target, value.primitive, value.target ?
        targetName(text(record(value.target).kind), text(record(value.target).id)) : ""])
        .toLowerCase().includes(filter.toLowerCase())).map((value) => (
        <button type="button" className="scene-effect-row" key={text(value.id)}
          aria-pressed={value.id === selectedId}
          aria-label={`Edit effect ${text(value.id)}`} onClick={() => onSelect(value)}>
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
  </>;
}

/** Inspector fields for one cue; unsupported primitives are preserved untouched. */
export function LightingCueFields({ cue, duration, targets, onChange }: {
  cue: RecordValue;
  duration: number;
  targets: LightingTarget[];
  onChange: (values: RecordValue) => void;
}) {
  const target = record(cue.target);
  if (!Object.hasOwn(LIGHTING_EFFECTS, text(cue.primitive))) {
    return <p role="alert">This effect is not supported by this editor. Its data is preserved; use advanced authoring.</p>;
  }
  return <>
    <label>Effect
      <select value={text(cue.primitive)} onChange={(event) => onChange({
        primitive: event.target.value,
        ...(event.target.value === "color_transition" && !cue.end_color ? { end_color: text(cue.color, "#ffffff") } : {}),
      })}>
        {Object.entries(LIGHTING_EFFECTS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
    </label>
    <label>Target decorations
      <select value={cue.target ? `${text(target.kind)}:${text(target.id)}` : ""} onChange={(event) => {
        const [kind, ...id] = event.target.value.split(":");
        onChange({ target: event.target.value ? { kind, id: id.join(":") } : null });
      }}>
        <option value="">Whole output</option>
        {targets.map((value) => <option key={value.key} value={value.key}>{value.label}</option>)}
        {cue.target && !targets.some((value) => value.key === `${text(target.kind)}:${text(target.id)}`) ?
          <option value={`${text(target.kind)}:${text(target.id)}`}>{text(target.id)} · {text(target.kind)}</option> : null}
      </select>
    </label>
    {cue.primitive !== "blackout" && <div className="scene-field-pair">
      <label>Effect color<input type="color" value={text(cue.color, "#ffffff").toLowerCase()} onChange={(event) => onChange({ color: event.target.value })} /></label>
      <NumberField label="Effect brightness (%)" value={numeric(cue.brightness, 1)} scale={100} min={0} max={100} onChange={(value) => onChange({ brightness: value })} />
    </div>}
    <div className="scene-field-pair">
      <NumberField label="Start (seconds)" value={numeric(cue.start_ms, 0)} scale={0.001} min={0} max={duration / 1000} onChange={(value) => onChange({ start_ms: Math.round(value) })} />
      <NumberField label="Duration (seconds)" value={numeric(cue.duration_ms, 1000)} scale={0.001} min={0.001} max={duration / 1000} onChange={(value) => onChange({ duration_ms: Math.round(value) })} />
    </div>
    {["pulse", "flicker", "sparkle"].includes(text(cue.primitive)) &&
      <NumberField label="Cycle (seconds)" value={numeric(cue.period_ms, 200)} scale={0.001} min={0.001} onChange={(value) => onChange({ period_ms: Math.round(value) })} />}
    {["chase", "path_sweep", "ripple"].includes(text(cue.primitive)) &&
      <NumberField label="Width (LEDs)" value={numeric(cue.width_pixels, 1)} min={1} onChange={(value) => onChange({ width_pixels: value })} />}
    {cue.primitive === "sparkle" && <NumberField label="Sparkle density (%)" value={numeric(cue.density, 0.1)} scale={100} min={0} max={100} onChange={(value) => onChange({ density: value })} />}
    {cue.primitive === "color_transition" && <label>End color<input type="color" value={text(cue.end_color, "#ffffff").toLowerCase()} onChange={(event) => onChange({ end_color: event.target.value })} /></label>}
    {["chase", "path_sweep", "ripple"].includes(text(cue.primitive)) && <label><input type="checkbox" checked={cue.reverse === true} onChange={(event) => onChange({ reverse: event.target.checked })} />Reverse direction</label>}
    <details className="scene-advanced"><summary>Advanced effect settings</summary>
      <NumberField label="Fade in (seconds)" value={numeric(cue.fade_in_ms, 0)} scale={0.001} min={0} onChange={(value) => onChange({ fade_in_ms: Math.round(value) })} />
      <NumberField label="Fade out (seconds)" value={numeric(cue.fade_out_ms, 0)} scale={0.001} min={0} onChange={(value) => onChange({ fade_out_ms: Math.round(value) })} />
      <label><input type="checkbox" checked={typeof cue.end_brightness === "number"} onChange={(event) => onChange({ end_brightness: event.target.checked ? cue.brightness ?? 1 : null })} />Different end brightness</label>
      {typeof cue.end_brightness === "number" && <NumberField label="End brightness (%)" value={numeric(cue.end_brightness, 1)} scale={100} min={0} max={100} onChange={(value) => onChange({ end_brightness: value })} />}
      {["flicker", "sparkle"].includes(text(cue.primitive)) && <NumberField label="Deterministic seed" value={numeric(cue.seed, 0)} onChange={(value) => onChange({ seed: value })} />}
      <NumberField label="Layer precedence" value={numeric(cue.precedence, 0)} min={0} max={1000} onChange={(value) => onChange({ precedence: value })} />
      <label>Blend<select value={text(cue.blend_mode, "replace")} onChange={(event) => onChange({ blend_mode: event.target.value })}>
        {["replace", "add", "max", "multiply"].map((value) => <option key={value}>{value}</option>)}
      </select></label>
      <small>Stable effect ID: {text(cue.id)}</small>
    </details>
  </>;
}

export function NumberField({ label, value, onChange, scale = 1, min, max }: {
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
