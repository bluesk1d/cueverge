import { useCallback, useEffect, useRef, useState } from "react";
import {
  InteractionEdit, InteractionPreview, previewInteractions, ProjectDraftRecord, SimulationStep,
} from "../api";
import { ScenePreview } from "./SceneWorkspace";
import { ErrorState } from "../components/ui";

const reasonNames: Record<string, string> = {
  label_filtered: "This object label is filtered out.",
  no_visitor_rules: "This show has no visitor rules.",
  stale_observation: "This observation is too old to qualify a visitor.",
  camera_not_matched: "The camera does not match the visitor area.",
  waiting_for_matching_zone: "Waiting for entry into the visitor zone.",
  audience_label_filtered: "The label does not match the visitor rules.",
  observation_not_matched: "This repeated or irrelevant observation did not change visitor presence.",
  no_transition: "No state-sequence response matches this event in the current state.",
  event_expired: "The event expired before a response could use it.",
  duplicate: "A repeated event was ignored.", debounced: "The event repetition limit was reached.",
  show_not_automatic: "The show is not in automatic mode.",
  visitor_conditions: "Waiting for matching visitor conditions.",
  presence_duration: "Waiting for the required visit duration.",
  prior_absence: "The display was not quiet long enough before this arrival.",
  repeat_wait: "Waiting for the response's repeat interval.",
  already_waiting: "A response of this kind is already waiting.",
};
const noNames = (_names: Record<string, string>) => {};
function clock(ms: number) { return `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`; }
function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function lightingResources(scene: ProjectDraftRecord["draft"]["scenes"][number]): string[] {
  const outputs: unknown[] = Array.isArray(scene.lighting) ? scene.lighting : [scene.lighting];
  return outputs.filter(isObject).flatMap((value) => typeof value.resource === "string" ? [value.resource] : []);
}

export function ShowPreviewWorkspace({ draft, candidateEdit, visitorOnly = false, canTestVisitors = true, startWithVisitors = false }: {
  draft: ProjectDraftRecord; candidateEdit?: InteractionEdit; visitorOnly?: boolean; canTestVisitors?: boolean; startWithVisitors?: boolean;
}) {
  const [mode, setMode] = useState<"scene" | "visitors">(visitorOnly || startWithVisitors ? "visitors" : "scene");
  const [sceneId, setSceneId] = useState(draft.draft.scenes[0].id);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [resource, setResource] = useState("");
  const [steps, setSteps] = useState<SimulationStep[]>([]);
  const [selected, setSelected] = useState<number>();
  const [result, setResult] = useState<InteractionPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const area = candidateEdit?.visitors ?? draft.draft.audience;
  const [camera, setCamera] = useState(area?.cameras[0] ?? "porch");
  const [zone, setZone] = useState(area?.zones[0] ?? "");
  const [objectId, setObjectId] = useState("visitor-1");
  const [label, setLabel] = useState(area?.labels[0] ?? "person");
  const [waitSeconds, setWaitSeconds] = useState(1);
  const [ageSeconds, setAgeSeconds] = useState(0);
  const generation = useRef(0);
  const candidateKey = JSON.stringify(candidateEdit ?? null);
  const resourceOptions = [...new Set(draft.draft.scenes.flatMap(lightingResources))];
  useEffect(() => {
    setSteps([]); setResult(null); setSelected(undefined); setError(null);
  }, [draft.revision, candidateKey]);
  const inputKey = JSON.stringify({
    source_revision: draft.revision, proposed_edit: candidateEdit,
    steps, frame_index: selected, resource: resource || undefined,
    pixel_limit: Math.min(1000, Math.floor(4096 / Math.max(1, resourceOptions.length))),
  });
  useEffect(() => {
    if (mode !== "visitors" || !canTestVisitors) return;
    const current = ++generation.current;
    const controller = new AbortController();
    setLoading(true); setError(null);
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    previewInteractions(draft.project.id, JSON.parse(inputKey), controller.signal).then((value) => {
      if (current === generation.current) setResult(value);
    }).catch((failure: unknown) => {
      if (current !== generation.current) return;
      setResult(null);
      setError(controller.signal.aborted ? "Preview timed out. Shorten the test and retry."
        : failure instanceof Error ? failure.message : "Visitor preview failed.");
    }).finally(() => {
      window.clearTimeout(timeout);
      if (current === generation.current) setLoading(false);
    });
    return () => { generation.current++; controller.abort(); window.clearTimeout(timeout); };
  }, [draft.project.id, mode, inputKey, retry, canTestVisitors]);
  const scene = draft.draft.scenes.find((value) => value.id === sceneId) ?? draft.draft.scenes[0];
  const declaredDuration = scene.duration_ms;
  const durationValid = typeof declaredDuration === "number" && Number.isSafeInteger(declaredDuration) && declaredDuration > 0;
  const duration = durationValid ? declaredDuration : 1;
  useEffect(() => {
    if (!playing || mode !== "scene") return;
    const timer = window.setInterval(() => setPosition((previous) => {
      const next = Math.min(duration - 1, previous + 100);
      if (next >= duration - 1) setPlaying(false);
      return next;
    }), 100);
    return () => window.clearInterval(timer);
  }, [playing, mode, duration]);
  const onFrameFailure = useCallback(() => setPlaying(false), []);
  const sceneResource = resource || lightingResources(scene)[0] || "";
  const checkpoint = result?.trace[result.selected_index];
  const frame = result?.frame ?? null;
  function add(step: SimulationStep) {
    if (steps.length >= 200) return;
    setSelected(undefined); setSteps((previous) => [...previous, step]);
  }
  const elapsed = result?.trace.at(-1)?.at_ms ?? 0;
  const canWait = Number.isFinite(waitSeconds) && waitSeconds >= 0 && Number.isSafeInteger(waitSeconds * 1000) && elapsed + waitSeconds * 1000 <= 600000;
  const canObserve = Boolean(camera.trim() && label.trim()) && Number.isFinite(ageSeconds) && ageSeconds >= 0 && ageSeconds <= 600 && Number.isSafeInteger(ageSeconds * 1000);
  const names = new Map(draft.draft.scenes.map((value) => [value.id, value.name]));
  return <section className="show-preview-workspace" aria-label="Preview workspace">
    <header><h2>Preview</h2><p>Try the saved show or pending response edits safely. No real camera events or device commands are sent.</p></header>
    {!visitorOnly && <div className="show-workspace-tabs" role="group" aria-label="Preview type">
      <button aria-pressed={mode === "scene"} onClick={() => { setMode("scene"); setPlaying(false); }}>Scene preview</button>
      <button disabled={!canTestVisitors} aria-pressed={mode === "visitors"} onClick={() => { setMode("visitors"); setPlaying(false); }}>Visitor test</button>
    </div>}
    <label>Preview output<select value={resource} onChange={(event) => setResource(event.target.value)}>
      <option value="">Scene outputs</option>
      {resourceOptions.map((value) => <option key={value}>{value}</option>)}
    </select></label>
    {mode === "scene" ? <>
      <label>Preview scene<select value={scene.id} onChange={(event) => { setSceneId(event.target.value); setPosition(0); setPlaying(false); }}>
        {draft.draft.scenes.map((value) => <option key={value.id} value={value.id}>{value.name}</option>)}
      </select></label>
      <ScenePreview projectId={draft.project.id} revision={draft.revision} document={draft.draft}
        dirty={false} sceneId={scene.id} resource={sceneResource} position={position}
        valid={durationValid} onFailure={onFrameFailure} onTargetNames={noNames} />
      {!durationValid && <ErrorState title="Scene duration unavailable" description="Reopen the saved show before previewing this scene." />}
      <div className="scene-preview-controls">
        <button disabled={!durationValid || !sceneResource} onClick={() => setPlaying(!playing)}>{playing ? "Pause preview" : "Play preview"}</button>
        <label>Scene preview position<input disabled={!durationValid} type="range" min="0" max={duration - 1} step={1} value={position} onChange={(event) => { setPosition(event.target.valueAsNumber); setPlaying(false); }} /></label>
        <span>{clock(position)}</span>
      </div>
    </> : <>
      <div className="visitor-test-controls">
        <label>Test camera<input value={camera} onChange={(event) => setCamera(event.target.value)} /></label>
        <label>Test zone (optional)<input value={zone} onChange={(event) => setZone(event.target.value)} /></label>
        <details><summary>Other test conditions</summary>
          <label>Test object label<input value={label} onChange={(event) => setLabel(event.target.value)} /></label>
          <label>Visitor identifier<input value={objectId} onChange={(event) => setObjectId(event.target.value)} /></label>
          <label>Observation age (seconds)<input type="number" min={0} max={600} step="any" value={Number.isNaN(ageSeconds) ? "" : ageSeconds} onChange={(event) => setAgeSeconds(event.target.valueAsNumber)} /></label>
        </details>
        <div className="show-actions">
          {(["arrive", "stay", "leave"] as const).map((observation) => <button key={observation} disabled={loading || !canObserve || !objectId || steps.length >= 200}
            onClick={() => add({ kind: "visitor", camera, label, zone: zone || undefined, object_id: objectId, observation, age_ms: Math.round(ageSeconds * 1000) })}>
            {observation === "arrive" ? "Visitor arrives" : observation === "stay" ? "Visitor stays" : "Visitor leaves"}
          </button>)}
        </div>
        <div className="show-actions">
          <label>Wait seconds<input type="number" min={0} max={600} step="any" value={Number.isNaN(waitSeconds) ? "" : waitSeconds} onChange={(event) => setWaitSeconds(event.target.valueAsNumber)} /></label>
          <button disabled={loading || !canWait || steps.length >= 200} onClick={() => add({ kind: "advance", delta_ms: Math.round(waitSeconds * 1000) })}>Advance preview time</button>
          <button disabled={loading} onClick={() => { setSteps([]); setSelected(undefined); setResult(null); }}>Reset visitor test</button>
        </div>
        <small>Stay sends a tracking update, not another arrival. Advance time separately to test qualification, expiry and completion. {steps.length}/200 actions · {clock(elapsed)}/10:00.</small>
      </div>
      <p className="scene-preview-status" aria-busy={loading}>{loading ? "Evaluating isolated visitor test…" : result ? `Test clock ${clock(checkpoint?.at_ms ?? 0)} · ${result.unsaved ? "pending response edits" : "saved show"}` : "No evaluated visitor test"}</p>
      {error && <ErrorState title="Visitor preview unavailable" description={error} />}
      {error && <button onClick={() => setRetry((value) => value + 1)}>Retry visitor preview</button>}
      <ScenePreview projectId={draft.project.id} revision={draft.revision} document={draft.draft}
        dirty={Boolean(candidateEdit)} sceneId={frame?.scene_id ?? scene.id}
        resource={frame?.outputs[0]?.resource ?? resource} position={frame?.timestamp_ms ?? 0}
        valid external frameOverride={frame} onFailure={onFrameFailure} onTargetNames={noNames} />
      {result?.frame_unavailable_reason && <p role="status">{result.frame_unavailable_reason}</p>}
      {checkpoint && <section aria-label="What happened">
        <h3>What happened</h3>
        <p><strong>{checkpoint.status.active_scene ? names.get(checkpoint.status.active_scene) ?? checkpoint.status.active_scene : "No scene playing"}</strong>
          {" · "}{checkpoint.status.audience?.state === "present" ? "Visitors qualified" : checkpoint.status.audience?.state === "candidate" ? "Confirming visitor arrival" : checkpoint.status.audience ? "No qualified visitors" : "No visitor rules"}</p>
        {checkpoint.status.completion_policy && checkpoint.status.completion_policy !== "select_next" && <p>This scene's end behavior is {checkpoint.status.completion_policy.replaceAll("_", " ")}; it does not choose waiting visitor responses at its end.</p>}
        {checkpoint.observations.map((value, index) => <p key={index}>{value.reason ? reasonNames[value.reason] ?? value.reason.replaceAll("_", " ") : value.audience_relevant ? "The camera observation matched the visitor rules." : value.accepted ? "The event was accepted." : value.note ?? "The observation did not request an immediate response."}</p>)}
        {(checkpoint.status.opportunities?.pending ?? []).map((value) => <p key={value.id}>Waiting response: {names.get(value.scene_id) ?? value.scene_id} · expires in {Math.round(value.remaining_ms / 1000)}s. A scene must choose what plays next before it can run.</p>)}
        {(checkpoint.status.opportunities?.qualifying ?? []).map((value, index) => <p key={`${value.id}-${index}`}>{names.get(value.scene_id) ?? value.scene_id}: {value.blocked_by.map((reason) => reasonNames[reason] ?? reason).join(" ")}</p>)}
        {checkpoint.status.opportunities && <p>Responses prepared: {checkpoint.status.opportunities.created_count}; selected at a boundary: {checkpoint.status.opportunities.consumed_count}; expired: {checkpoint.status.opportunities.expired_count}; qualification windows ended: {checkpoint.status.opportunities.qualification_expired_count}.</p>}
        {checkpoint.status.encounter && <p>State sequence: {checkpoint.status.encounter.state}. Accepted steps: {checkpoint.status.encounter.accepted_events}; ignored steps: {checkpoint.status.encounter.rejected_events}. This sequence can compete with boundary responses.</p>}
        {checkpoint.status.degraded_reason && <p role="status">Simulation warning: {checkpoint.status.degraded_reason}</p>}
        <p>Scene history: {checkpoint.scene_history.map((id) => names.get(id) ?? id).join(" → ")}</p>
        <label>Inspect a checkpoint<select value={result?.selected_index ?? 0} disabled={loading} onChange={(event) => setSelected(Number(event.target.value))}>
          {result?.trace.map((value) => <option key={value.index} value={value.index}>{clock(value.at_ms)} · {value.index === 0 ? "Start" : `Action ${value.index}: ${value.action}`}</option>)}
        </select></label>
        <details><summary>Scene changes and test limitations</summary>
          <ul>{result?.scene_timeline.map((value, index) => <li key={index}>{clock(value.at_ms)} · {value.scene_id ? names.get(value.scene_id) ?? value.scene_id : "Stopped"}</li>)}</ul>
          {checkpoint.status.director && <ul>{Object.entries(checkpoint.status.director.eligibility).map(([id, eligibility]) => <li key={id}>{names.get(id) ?? id}: {!eligibility.available ? "unavailable" : !eligibility.conditions_match ? "visitor conditions do not match" : eligibility.cooldown_remaining_ms > 0 ? `repeat wait: ${eligibility.cooldown_remaining_ms / 1000}s` : "eligible for normal selection"}</li>)}</ul>}
          {result?.limitations.map((value) => <p key={value}>{value}</p>)}
        </details>
      </section>}
    </>}
  </section>;
}
