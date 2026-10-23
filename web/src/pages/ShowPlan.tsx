import { ReactNode, useEffect, useState } from "react";
import {
  getShowPlan, ProjectDraftRecord, SceneEnding, ShowPlan as ShowPlanData, ShowPlanEdit,
  ShowPlanScene, updateShowPlan,
} from "../api";
import { Pause, Repeat, RotateCcw, Shuffle, Square, TriangleAlert } from "lucide-react";
import { ErrorState } from "../components/ui";

const weights = [1, 2, 3, 5, 10];
type ShowPlanEditBody = ShowPlanEdit extends infer Edit ? Edit extends ShowPlanEdit ? Omit<Edit, "source_revision"> : never : never;

export function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function ShowPlan({ draft, canEdit, onUpdated, onOpenScene, onVisitorResponses }: {
  draft: ProjectDraftRecord; canEdit: boolean;
  onUpdated: (draft: ProjectDraftRecord) => void;
  onOpenScene: (sceneId: string) => void;
  onVisitorResponses: () => void;
}) {
  const projectId = draft.project.id;
  const [plan, setPlan] = useState<ShowPlanData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [retry, setRetry] = useState(0);
  const [addSceneId, setAddSceneId] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    getShowPlan(projectId, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      if (value.source_revision !== draft.revision) throw new Error("This show changed elsewhere. Reopen the saved show before editing the plan.");
      setPlan(value);
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "The show plan could not be loaded.");
    });
    return () => controller.abort();
  }, [projectId, draft.revision, retry]);

  const scenes = draft.draft.scenes;
  const names = new Map(scenes.map((scene) => [scene.id, scene.name]));
  const editable = canEdit && !busy && plan?.source_revision === draft.revision;

  async function save(edit: ShowPlanEditBody) {
    if (!plan) return;
    setBusy(true); setSaveError(null); setSaved(false);
    try {
      onUpdated(await updateShowPlan(projectId, { ...edit, source_revision: plan.source_revision } as ShowPlanEdit));
      setSaved(true);
    } catch (failure) {
      setSaveError(failure instanceof Error ? failure.message : "The show plan could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  function setEnding(scene: ShowPlanScene, ending: SceneEnding, nextSceneId?: string) {
    if (ending === "play_next") {
      const others = scenes.filter((item) => item.id !== scene.id);
      const index = scenes.findIndex((item) => item.id === scene.id);
      const next = nextSceneId ?? scenes[index + 1]?.id ?? others[0]?.id;
      if (!next) return;
      void save({ operation: "scene_ending", scene_id: scene.id, ending, next_scene_id: next });
    } else {
      void save({ operation: "scene_ending", scene_id: scene.id, ending });
    }
  }

  function saveShuffle(entries: { scene_id: string; weight: number }[]) {
    void save({ operation: "shuffle_list", shuffle: entries });
  }

  function sceneBlock(scene: ShowPlanScene, children?: ReactNode) {
    return <div key={scene.id} className="show-plan-block" style={{ flexGrow: Math.max(1, scene.duration_ms / 30000) }}>
      <button className="show-plan-block__open" onClick={() => onOpenScene(scene.id)}>
        <span className="sr-only">Open scene </span><strong>{scene.name}</strong>
        <small>{formatDuration(scene.duration_ms)} · Open</small>
      </button>
      {children}
    </div>;
  }

  function endingControl(scene: ShowPlanScene) {
    return <div className="show-plan-ending">
      <label>When it ends
        <select aria-label={`When ${scene.name} ends`} disabled={!editable} value={scene.ending.kind}
          onChange={(event) => setEnding(scene, event.target.value as SceneEnding)}>
          {plan?.ending_choices.map((choice) => <option key={choice.kind} value={choice.kind}
            disabled={(choice.kind === "play_next" && scenes.length < 2)
              || (choice.kind === "shuffle" && !plan.starting_scene_id)}>{choice.label}</option>)}
        </select>
      </label>
      {scene.ending.kind === "play_next" && <label>Next scene
        <select aria-label={`Scene after ${scene.name}`} disabled={!editable} value={scene.ending.next_scene_id ?? ""}
          onChange={(event) => setEnding(scene, "play_next", event.target.value)}>
          {scenes.filter((item) => item.id !== scene.id).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>}
    </div>;
  }

  if (error) return <section className="show-plan" aria-label="Show plan">
    <ErrorState title="Show plan unavailable" description={error} />
    <button onClick={() => setRetry((value) => value + 1)}>Retry show plan</button>
  </section>;
  if (!plan) return <section className="show-plan" aria-label="Show plan"><p>Loading show plan…</p></section>;

  const end = plan.main_show.end;
  const endName = end.scene_id ? names.get(end.scene_id) ?? end.scene_id : "";
  const endChip: Partial<Record<typeof end.kind, [typeof Repeat, string]>> = {
    repeat: [Repeat, `Repeats ${endName} forever`],
    back_to: [RotateCcw, `Goes back to ${endName}`],
    shuffle: [Shuffle, "Then picks from the shuffle list (visitor responses can play here)"],
    freeze: [Pause, "Stays on the last moment"],
    stop: [Square, "The show stops"],
    missing: [TriangleAlert, "The next scene is missing"],
  };
  const endEntry = endChip[end.kind];
  const shuffleIds = new Set(plan.shuffle_list.scenes.map((entry) => entry.id));
  const shuffleCandidates = scenes.filter((scene) => !shuffleIds.has(scene.id));
  const currentShuffle = plan.shuffle_list.scenes.map((entry) => ({ scene_id: entry.id, weight: entry.weight }));

  return <section className="show-plan" aria-label="Show plan">
    <header>
      <h2>Show plan</h2>
      <p>Your show is made of scenes. This page shows the order they play in and what happens when visitors walk up. Choose <strong>Open scene</strong> to change a scene&rsquo;s lights and media.</p>
    </header>
    {plan.warnings.length > 0 && <ul className="show-plan-warnings" aria-label="Show plan warnings">
      {plan.warnings.map((warning) => <li key={warning}>{warning}</li>)}
    </ul>}
    {saveError && <p role="alert">{saveError}</p>}
    {busy ? <p role="status">Saving…</p> : saved && <p role="status">Saved. Your live show changes only after you apply saved changes.</p>}

    <section className="show-plan-section" aria-labelledby="show-plan-main">
      <h3 id="show-plan-main">Main show</h3>
      <p>Plays when you press Start, and whenever no visitor response is playing.</p>
      <label className="show-plan-start">Starts with
        <select aria-label="Starting scene" disabled={!editable} value={plan.starting_scene_id ?? ""}
          onChange={(event) => event.target.value && void save({ operation: "starting_scene", scene_id: event.target.value })}>
          {!plan.starting_scene_id && <option value="">Choose a scene…</option>}
          {scenes.map((scene) => <option key={scene.id} value={scene.id}>{scene.name}</option>)}
        </select>
      </label>
      {plan.main_show.scenes.length > 0 && <ol className="show-plan-lane" aria-label="Main show order">
        {plan.main_show.scenes.map((scene, index) => <li key={scene.id} className="show-plan-step">
          {index > 0 && <span className="show-plan-arrow" aria-hidden="true">→</span>}
          {sceneBlock(scene, endingControl(scene))}
        </li>)}
        {endEntry && (() => {
          const [Icon, text] = endEntry;
          return <li className="show-plan-end"><Icon size={16} aria-hidden="true" /> {text}</li>;
        })()}
      </ol>}
    </section>

    <section className="show-plan-section" aria-labelledby="show-plan-shuffle">
      <h3 id="show-plan-shuffle">Shuffle list</h3>
      <p>When a scene ends with &ldquo;Let the show pick next&rdquo;, the show first plays any waiting visitor response, otherwise it picks a scene from this list at random. Choose a higher &ldquo;How often&rdquo; to pick a scene more often.</p>
      {!plan.shuffle_list.pool_id ? <p>Choose a starting scene above to create the shuffle list.</p> : <>
        <ul className="show-plan-shuffle" aria-label="Shuffle list scenes">
          {plan.shuffle_list.scenes.map((entry, index) => <li key={entry.id}>
            {sceneBlock(entry, <div className="show-plan-shuffle__controls">
                <label>How often
                  <select aria-label={`How often ${entry.name} is picked`} disabled={!editable} value={entry.weight}
                    onChange={(event) => saveShuffle(currentShuffle.map((item, itemIndex) => itemIndex === index ? { ...item, weight: Number(event.target.value) } : item))}>
                    {[...new Set([...weights, entry.weight])].sort((a, b) => a - b).map((weight) =>
                      <option key={weight} value={weight}>{weight === 1 ? "Normal" : `${weight}× as often`}</option>)}
                  </select>
                </label>
                <small>{entry.available ? `About ${entry.chance_percent}% of picks` : "Turned off in advanced settings"}</small>
                {entry.has_extra_rules && <small>Has extra rules from advanced settings; they are kept.</small>}
                <small>{entry.ending.label}</small>
                <button disabled={!editable || plan.shuffle_list.scenes.length < 2}
                  onClick={() => saveShuffle(currentShuffle.filter((_, itemIndex) => itemIndex !== index))}
                  aria-label={`Remove ${entry.name} from the shuffle list`}>Remove</button>
              </div>)}
          </li>)}
        </ul>
        {shuffleCandidates.length > 0 && <div className="show-actions">
          <select aria-label="Scene to add to the shuffle list" disabled={!editable} value={addSceneId}
            onChange={(event) => setAddSceneId(event.target.value)}>
            <option value="">Choose a scene…</option>
            {shuffleCandidates.map((scene) => <option key={scene.id} value={scene.id}>{scene.name}</option>)}
          </select>
          <button disabled={!editable || !addSceneId} onClick={() => {
            saveShuffle([...currentShuffle, { scene_id: addSceneId, weight: 1 }]);
            setAddSceneId("");
          }}>Add to shuffle list</button>
        </div>}
      </>}
    </section>

    <section className="show-plan-section" aria-labelledby="show-plan-visitors">
      <h3 id="show-plan-visitors">Visitor responses</h3>
      <p>Scenes that play when someone walks up. They wait for a main-show scene that lets the show pick next.</p>
      {plan.visitor_responses.length === 0 ? <p>No visitor responses yet.</p> : <ul className="show-plan-responses" aria-label="Visitor response lanes">
        {plan.visitor_responses.map((response) => <li key={response.key}>
          <span className="show-plan-trigger">When {response.when.charAt(0).toLowerCase() + response.when.slice(1)}</span>
          <span className="show-plan-arrow" aria-hidden="true">→</span>
          {response.scene_id ? <button className="show-plan-block__open" onClick={() => onOpenScene(response.scene_id!)}>
            <span className="sr-only">Open scene </span><strong>{response.scene_name ?? response.scene_id}</strong><small>Open</small>
          </button> : <span>No scene</span>}
          <span className="show-plan-arrow" aria-hidden="true">→</span>
          <small>{response.afterward}</small>
        </li>)}
      </ul>}
      <div className="show-actions"><button onClick={onVisitorResponses}>Edit visitor responses</button></div>
    </section>

    {plan.unused_scenes.length > 0 && <section className="show-plan-section" aria-labelledby="show-plan-unused">
      <h3 id="show-plan-unused">Scenes not in the show yet</h3>
      <p>These scenes never play. Add one by choosing it as a next scene, adding it to the shuffle list, or using it for a visitor response.</p>
      <ul className="show-plan-unused">
        {plan.unused_scenes.map((scene) => <li key={scene.id}>{sceneBlock(scene)}</li>)}
      </ul>
    </section>}
  </section>;
}
