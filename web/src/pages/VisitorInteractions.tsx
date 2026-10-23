import { useEffect, useMemo, useRef, useState } from "react";
import {
  AudiencePresenceDefinition, getInteractions, GuidedResponse, InteractionCard,
  InteractionEdit, InteractionOverview, ProjectDraftRecord, updateInteraction,
} from "../api";
import { ErrorState } from "../components/ui";
import { ShowPreviewWorkspace } from "./ShowPreviewWorkspace";

const events = [
  ["audience.arrived", "Visitors arrive"], ["audience.present", "Visitors are first confirmed present"],
  ["audience.departed", "Visitors leave"], ["audience.long_idle", "The display stays quiet after visitors leave"],
] as const;
const defaultVisitors: AudiencePresenceDefinition = {
  schema_version: 1, cameras: [], zones: [], labels: ["person"],
  observation_freshness_ms: 5000, arrival_confirmation_ms: 1000,
  absence_grace_ms: 5000, long_idle_ms: 300000,
};

export function VisitorInteractions({ draft, canEdit, canPreview, onUpdated, onDirtyChange, onInputs, onPreview, onAdvanced }: {
  draft: ProjectDraftRecord; canEdit: boolean; canPreview: boolean;
  onUpdated: (draft: ProjectDraftRecord) => void; onDirtyChange: (dirty: boolean) => void;
  onInputs?: () => void; onPreview: () => void; onAdvanced: () => void;
}) {
  const [overview, setOverview] = useState<InteractionOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<"new" | "visitors" | InteractionCard | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getInteractions(draft.project.id, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      if (value.source_revision !== draft.revision) throw new Error("This show changed elsewhere. Reopen the saved show before editing responses.");
      setOverview(value);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Visitor responses could not be loaded.");
    });
    return () => controller.abort();
  }, [draft.project.id, draft.revision, retry]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  const normal = overview?.normal_playback;
  const pool = normal?.pools.find((value) => value.id === normal.default_pool_id);
  const names = new Map(draft.draft.scenes.map((value) => [value.id, value.name]));
  return <section className="visitor-interactions" aria-label="Visitor responses">
    <header><h2>Visitor responses</h2><p>Choose what happens when people arrive, stay, or leave. Save changes here; making them live is a separate step.</p></header>
    <div className="show-actions">
      <button className="primary-button" disabled={!canEdit} onClick={() => setEditor("new")}>Add visitor response</button>
      <button disabled={!canEdit} onClick={() => setEditor("visitors")}>Edit visitor area</button>
      <button disabled={!canPreview} onClick={onPreview}>Try the show safely</button>
      {onInputs && <button onClick={onInputs}>Inputs & connections</button>}
    </div>
    {error && <><ErrorState title="Responses unavailable" description={error} /><button onClick={() => setRetry((value) => value + 1)}>Retry response list</button></>}
    {overview && <>
      <p><strong>Visitor area:</strong> {overview.visitors
        ? `Cameras: ${overview.visitors.cameras.join(", ") || "any"}; zones: ${overview.visitors.zones.join(", ") || "any"}. Arrival confirmation: ${overview.visitors.arrival_confirmation_ms / 1000}s. Shared by all visitor responses.`
        : "Not set up yet. Choose a camera or zone when adding your first response."}</p>
      <p><strong>Normal playback:</strong> {normal?.starting_scene_id ? `Starts with ${names.get(normal.starting_scene_id) ?? normal.starting_scene_id}.` : "No automatic starting scene configured."}
        {pool && ` Then chooses among ${pool.scenes.map((value) => names.get(value.scene_id) ?? value.scene_id).join(", ")} using the saved availability, repeat and weighting rules.`}</p>
      {!overview.cards.length && <p>No visitor responses yet. Start with an arrival response, then try it without connecting cameras or devices.</p>}
      <div className="interaction-cards">
        {overview.cards.map((card) => <article key={card.key} aria-label={`Response ${card.name}`}>
          <h3>{card.name}</h3>
          <p><strong>When:</strong> {card.when}</p>
          <p><strong>Response:</strong> {card.scene_name ?? "Changes state without requesting a scene"}</p>
          <p><strong>Timing:</strong> {card.timing}</p>
          {card.kind === "response" && <p>Wait up to {card.definition.ttl_ms / 1000}s once ready; minimum visit {card.definition.min_presence_ms / 1000}s; repeat interval after selection {card.definition.acknowledgement_cooldown_ms / 1000}s.</p>}
          {card.kind === "response" && card.definition.min_absence_before_arrival_ms > 0 && <p>The display must have been quiet for {card.definition.min_absence_before_arrival_ms / 1000}s before this arrival.</p>}
          <p><strong>Afterward:</strong> {card.afterward}</p>
          <small>{card.note}</small>
          {!card.editable && <p>Additional matching rules are preserved. Use advanced controls for this response.</p>}
          <div className="show-actions">
            <button disabled={!canEdit || !card.editable} onClick={() => setEditor(card)}>{card.kind === "transition" ? "Change response scene" : "Edit response"}</button>
            <button disabled={!canPreview} onClick={onPreview}>Try it</button>
            {!card.editable && <button onClick={onAdvanced}>Open advanced controls</button>}
          </div>
          <details><summary>Matching and sequence details</summary><pre>{JSON.stringify(card.definition, null, 2)}</pre></details>
        </article>)}
      </div>
      {overview.warnings.map((warning) => <p key={warning} role="status">{warning}</p>)}
      <details><summary>Advanced interactions</summary>
        <p>Immediate requests, custom events, overlapping state sequences, weighted groups and extra conditions remain available without conversion.</p>
        <button onClick={onAdvanced}>Open advanced interaction controls</button>
      </details>
    </>}
    {editor !== null && <InteractionEditor key={typeof editor === "string" ? editor : editor.key}
      draft={draft} selection={editor} onDirtyChange={onDirtyChange} onClose={() => setEditor(null)}
      onSaved={(value) => { onUpdated(value); setEditor(null); }} />}
  </section>;
}

function SecondsField({ label, value, onChange, positive = false }: {
  label: string; value: number; onChange: (value: number) => void; positive?: boolean;
}) {
  return <label>{label}<input required type="number" min={positive ? 0.001 : 0} step={0.001}
    value={Number.isNaN(value) ? "" : value / 1000}
    onChange={(event) => onChange(Math.round(event.target.valueAsNumber * 1000))} /></label>;
}

function InteractionEditor({ draft, selection, onDirtyChange, onClose, onSaved }: {
  draft: ProjectDraftRecord; selection: "new" | "visitors" | InteractionCard;
  onDirtyChange: (dirty: boolean) => void; onClose: () => void;
  onSaved: (draft: ProjectDraftRecord) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const card = typeof selection === "string" ? null : selection;
  const previous = card?.kind === "response" ? card.definition : null;
  const [response, setResponse] = useState<GuidedResponse>({
    ...(previous ? { id: previous.id } : {}),
    name: card?.name ?? "Welcome visitors",
    trigger_event: previous?.trigger_event ?? "audience.arrived",
    scene_id: previous?.scene_id ?? card?.scene_id ?? draft.draft.scenes[0].id,
    ttl_ms: previous?.ttl_ms ?? 30000, min_presence_ms: previous?.min_presence_ms ?? 0,
    acknowledgement_cooldown_ms: previous?.acknowledgement_cooldown_ms ?? 60000,
    priority: previous?.priority ?? 50,
    fallback_scene_id: draft.draft.program?.initial_scene_id ?? draft.draft.scenes[0].id,
    prepare_completion: false,
    qualification_context: previous ? "preserve" : "present",
  });
  const [visitors, setVisitors] = useState<AudiencePresenceDefinition>(draft.draft.audience ?? defaultVisitors);
  const [sceneId, setSceneId] = useState(card?.scene_id ?? "");
  const [step, setStep] = useState(0);
  const [showPreview, setShowPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const initial = useRef(JSON.stringify({ response, visitors, sceneId }));
  const dirty = initial.current !== JSON.stringify({ response, visitors, sceneId });
  const visitorOnly = selection === "visitors";
  const transitionOnly = card?.kind === "transition";
  const edit = useMemo<InteractionEdit>(() => ({
    source_revision: draft.revision,
    operation: visitorOnly ? "visitors" : transitionOnly ? "transition" : "response",
    ...(visitorOnly ? { visitors: { ...visitors, cameras: visitors.cameras.filter(Boolean), zones: visitors.zones.filter(Boolean) } } : transitionOnly ? { transition_index: card.index, scene_id: sceneId || null } : {
      response,
      ...(!draft.draft.audience ? { visitors: { ...visitors, cameras: visitors.cameras.filter(Boolean), zones: visitors.zones.filter(Boolean) } } : {}),
    }),
  }), [draft.revision, draft.draft.audience, visitorOnly, transitionOnly, card, sceneId, response, visitors]);
  const validNumbers = [response.priority, response.ttl_ms, response.min_presence_ms, response.acknowledgement_cooldown_ms, visitors.arrival_confirmation_ms, visitors.absence_grace_ms, visitors.observation_freshness_ms, visitors.long_idle_ms].every((value) => Number.isSafeInteger(value) && value >= 0) && response.priority <= 100 && visitors.observation_freshness_ms > 0 && visitors.long_idle_ms > 0 &&
    [response.min_absence_before_arrival_ms, response.qualification_window_ms, response.event_ttl_ms].every((value) => value === undefined || Number.isSafeInteger(value) && value >= 0) &&
    (response.qualification_window_ms === undefined || response.qualification_window_ms > 0) && (response.event_ttl_ms === undefined || response.event_ttl_ms > 0);
  const areaValid = visitors.cameras.some(Boolean) || visitors.zones.some(Boolean);
  const valid = transitionOnly || (validNumbers && areaValid && (visitorOnly ||
    (Boolean(response.name.trim()) && response.ttl_ms > 0 &&
      (Boolean(draft.draft.program) || Boolean(previous) || Boolean(response.fallback_scene_id && response.prepare_completion)))));
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  useEffect(() => {
    onDirtyChange(dirty);
    const before = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", before);
    return () => window.removeEventListener("beforeunload", before);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  function cancel() {
    if (!saving && (!dirty || window.confirm("Discard unsaved visitor response changes?"))) onClose();
  }
  async function save() {
    if (!valid) return;
    setSaving(true); setError(null);
    try { onSaved(await updateInteraction(draft.project.id, edit)); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "The response could not be saved."); }
    finally { setSaving(false); }
  }
  const areaFields = <>
    <p>Visitor matching is shared by every response in this saved show. This does not reconfigure cameras or change the live show.</p>
    <label>Camera names<input value={visitors.cameras.join(", ")} placeholder="e.g. porch" onChange={(event) => setVisitors({ ...visitors, cameras: event.target.value.split(",").map((value) => value.trim()) })} /></label>
    <label>Zone names (optional)<input value={visitors.zones.join(", ")} onChange={(event) => setVisitors({ ...visitors, zones: event.target.value.split(",").map((value) => value.trim()) })} /></label>
    <small>Use exact Frigate names; separate multiple names with commas. At least one camera or zone is required.</small>
    <SecondsField label="Confirm arrival after (seconds)" value={visitors.arrival_confirmation_ms} onChange={(value) => setVisitors({ ...visitors, arrival_confirmation_ms: value })} />
    <SecondsField label="Allow a tracking gap before departure (seconds)" value={visitors.absence_grace_ms} onChange={(value) => setVisitors({ ...visitors, absence_grace_ms: value })} />
    <details><summary>Visitor timing details</summary>
      <SecondsField label="Observation freshness (seconds)" positive value={visitors.observation_freshness_ms} onChange={(value) => setVisitors({ ...visitors, observation_freshness_ms: value })} />
      <SecondsField label="Quiet-period duration (seconds)" positive value={visitors.long_idle_ms} onChange={(value) => setVisitors({ ...visitors, long_idle_ms: value })} />
      <p>Accepted visitor labels: {visitors.labels.join(", ")}. Extra labels remain editable in advanced controls.</p>
    </details>
  </>;
  const sceneSelect = (value: string, onChange: (value: string) => void, none = false) => <select value={value} onChange={(event) => onChange(event.target.value)}>
    {none && <option value="">No scene request</option>}
    {draft.draft.scenes.map((scene) => <option key={scene.id} value={scene.id}>{scene.name}</option>)}
  </select>;
  return <dialog ref={dialog} className="interaction-dialog" aria-labelledby="interaction-editor-title" onCancel={(event) => { event.preventDefault(); cancel(); }}>
    <h2 id="interaction-editor-title">{visitorOnly ? "Visitor area" : transitionOnly ? "Change response scene" : previous ? "Edit visitor response" : "Add visitor response"}</h2>
    <form ref={form} onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <fieldset disabled={saving}>
        {visitorOnly ? areaFields : transitionOnly ? <>
          <p>{card.note} {card.timing}</p>
          <label>Response scene{sceneSelect(sceneId, setSceneId, true)}</label>
        </> : <>
          <p>Step {step + 1} of 4 · {["When & where", "Response", "Timing & repetition", "Afterward & review"][step]}</p>
          {step === 0 && <>
            <label>Response name<input required maxLength={100} value={response.name} onChange={(event) => setResponse({ ...response, name: event.target.value })} /></label>
            <label>When<select value={response.trigger_event} onChange={(event) => setResponse({ ...response, trigger_event: event.target.value, ...(!previous ? { qualification_context: ["audience.departed", "audience.long_idle"].includes(event.target.value) ? "absent" : "present" } : {}) })}>{events.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            {!draft.draft.audience ? areaFields : <p>Where: cameras {visitors.cameras.join(", ") || "any"}, zones {visitors.zones.join(", ") || "any"}. Use Edit visitor area outside this guide to change shared matching.</p>}
          </>}
          {step === 1 && <>
            <label>Play this scene{sceneSelect(response.scene_id, (value) => setResponse({ ...response, scene_id: value }))}</label>
            <p>The response waits for a scene that chooses what plays next to finish. It does not interrupt scenes immediately. Custom immediate/state-sequence requests remain in advanced controls.</p>
          </>}
          {step === 2 && <>
            <SecondsField label="Wait for an eligible boundary for up to (seconds)" positive value={response.ttl_ms} onChange={(value) => setResponse({ ...response, ttl_ms: value })} />
            <SecondsField label="Require additional qualified visitor time (seconds)" value={response.min_presence_ms} onChange={(value) => setResponse({ ...response, min_presence_ms: value })} />
            <SecondsField label="Repeat interval after this response is selected (seconds)" value={response.acknowledgement_cooldown_ms} onChange={(value) => setResponse({ ...response, acknowledgement_cooldown_ms: value })} />
            <label>Visitor state while qualifying<select value={response.qualification_context} onChange={(event) => {
              const value = event.target.value;
              if (value === "preserve" || value === "present" || value === "absent" || value === "any") setResponse({ ...response, qualification_context: value });
            }}>
              {previous && <option value="preserve">Keep saved requirement ({Object.keys(previous.required_context).length === 0 ? "any state" : previous.required_context.audience === true || previous.required_context.audience_state === "present" ? "visitors present" : "no qualified visitors"})</option>}
              <option value="present">Visitors present</option><option value="absent">No qualified visitors</option><option value="any">Any state</option>
            </select></label>
            <details><summary>Additional qualification timing</summary>
              <SecondsField label="Quiet time required before arrival (seconds)" value={response.min_absence_before_arrival_ms ?? previous?.min_absence_before_arrival_ms ?? 0} onChange={(value) => setResponse({ ...response, min_absence_before_arrival_ms: value })} />
              <SecondsField label="Time allowed to qualify (seconds)" positive value={response.qualification_window_ms ?? previous?.qualification_window_ms ?? 3600000} onChange={(value) => setResponse({ ...response, qualification_window_ms: value })} />
              <SecondsField label="Maximum trigger age (seconds)" positive value={response.event_ttl_ms ?? previous?.event_ttl_ms ?? 3000} onChange={(value) => setResponse({ ...response, event_ttl_ms: value })} />
            </details>
            <p>A waiting visitor may become eligible again after the repeat interval within the saved qualification window. Arrival confirmation, event age and other conditions still apply.</p>
            <p>Once prepared, a waiting response remains available until expiry even if visitors leave. Use a short boundary-wait time when a late response would not make sense.</p>
          </>}
          {step === 3 && <>
            {!draft.draft.program && !previous && <label>Normal-playback scene{sceneSelect(response.fallback_scene_id ?? "", (value) => setResponse({ ...response, fallback_scene_id: value }))}</label>}
            {!draft.draft.program && previous && <p>No automatic normal-playback group is configured. This focused edit preserves that arrangement.</p>}
            {draft.draft.program && <p>Afterward follows the scene's saved end behavior and existing normal-playback groups. Those groups, weights and conditions are preserved.</p>}
            <label><input type="checkbox" checked={response.prepare_completion} onChange={(event) => setResponse({ ...response, prepare_completion: event.target.checked })} />
              Allow the response and normal-playback scenes to choose what plays next when finished (shared scene changes).
            </label>
            <p>{!draft.draft.program && !previous ? "Required to set up normal playback for this first guided response. This explicitly replaces these scenes' loop/hold/stop/fixed-next behavior." : "Optional: otherwise their current end behavior is preserved, including holding, looping or stopping."}</p>
            {response.prepare_completion && <p>Changes end behavior for: {draft.draft.scenes.filter((scene) => {
              const normal = draft.draft.program;
              return scene.id === response.scene_id || (!normal && !previous && scene.id === response.fallback_scene_id) || normal?.initial_scene_id === scene.id || normal?.pools.some((pool) => pool.id === normal.default_pool_id && pool.scenes.some((item) => item.scene_id === scene.id));
            }).map((scene) => `${scene.name} (${scene.completion?.policy ?? "hold"})`).join(", ")}.</p>}
            <details><summary>Response priority</summary><label>Priority<input type="number" min={0} max={100} required value={Number.isNaN(response.priority) ? "" : response.priority} onChange={(event) => setResponse({ ...response, priority: event.target.valueAsNumber })} /></label></details>
            <p>Other responses or state sequences may win; a detection never guarantees playback. Save is not Apply.</p>
            {!valid && <p role="alert">Complete the response name, visitor area and valid timing; confirm normal-playback setup when required.</p>}
            <button type="button" disabled={!valid || saving} onClick={() => setShowPreview(!showPreview)}>{showPreview ? "Hide pending-change preview" : "Try changes before saving"}</button>
          </>}
          <div className="show-actions">
            {step > 0 && <button type="button" onClick={() => { setStep(step - 1); setShowPreview(false); }}>Back</button>}
            {step < 3 && <button type="button" disabled={step === 0 && (!response.name.trim() || (!draft.draft.audience && !areaValid))} onClick={() => { if (form.current?.reportValidity()) setStep(step + 1); }}>Next</button>}
          </div>
        </>}
      </fieldset>
      {error && <ErrorState title="Response not saved" description={error} />}
      <div className="show-actions">
        {(visitorOnly || transitionOnly || step === 3) && <button className="primary-button" disabled={saving || !valid || (dirty === false && selection !== "new")}>{saving ? "Saving…" : visitorOnly ? "Save shared visitor area" : "Save response"}</button>}
        <button type="button" disabled={saving} onClick={cancel}>Cancel</button>
      </div>
    </form>
    {showPreview && valid && <ShowPreviewWorkspace draft={draft} candidateEdit={edit} visitorOnly />}
  </dialog>;
}
