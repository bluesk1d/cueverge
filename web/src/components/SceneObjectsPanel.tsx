import { useEffect, useState } from "react";
import {
  editShowObject, getSceneObjects, setSceneObjects,
  type InstallationObjectEdit, type ProjectDraftRecord, type SceneObjectEntry, type SceneObjects,
} from "../api";
import { ObjectDefinitionForm, type ObjectOutput } from "./ObjectDefinitionForm";

const keyOf = (item: { resource: string; kind: string; id: string }) => `${item.resource}/${item.kind}/${item.id}`;
const ownerLabel = (owner: string) => owner === "installation" ? "Installation" : "This show";

export function pixelOutputs(draft: ProjectDraftRecord): ObjectOutput[] {
  const resources = (draft.draft.installation as { resources?: unknown[] }).resources ?? [];
  return resources.flatMap((value) => {
    const binding = value as { resource?: string; kind?: string; segments?: ObjectOutput["segments"] };
    return binding.kind === "pixel_output" && binding.resource
      ? [{ resource: binding.resource, segments: binding.segments ?? [] }] : [];
  });
}

/** Objects this scene uses. Adding an installation object never changes the installation. */
export function SceneObjectsPanel({ draft, sceneId, canEdit, disabledReason, onChanged }: {
  draft: ProjectDraftRecord;
  sceneId: string;
  canEdit: boolean;
  disabledReason: string | null;
  onChanged: (updated: ProjectDraftRecord) => void;
}) {
  const [view, setView] = useState<SceneObjects | null>(null);
  const [choice, setChoice] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const projectId = draft.project.id;
  useEffect(() => {
    let current = true;
    setError(null);
    void getSceneObjects(projectId, sceneId).then((value) => {
      if (!Array.isArray(value?.objects)) throw new Error("Scene objects are unavailable.");
      if (current) setView(value);
    })
      .catch((failure: unknown) => { if (current) setError(failure instanceof Error ? failure.message : String(failure)); });
    return () => { current = false; };
  }, [projectId, sceneId, draft.revision]);

  async function save(objects: SceneObjectEntry[]) {
    setBusy(true); setError(null);
    try {
      onChanged(await setSceneObjects(projectId, sceneId, draft.revision,
        objects.map(({ resource, kind, id }) => ({ resource, kind, id }))));
      setChoice("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }
  async function create(value: InstallationObjectEdit) {
    setBusy(true); setError(null);
    try {
      onChanged(await editShowObject(projectId, draft.revision, "create", { ...value, scene_id: sceneId }));
      setCreating(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }
  const blocked = Boolean(disabledReason) || busy || !view;
  const outputs = pixelOutputs(draft);
  const groups = (["installation", "show"] as const).map((owner) =>
    ({ owner, items: view?.available.filter((item) => item.owner === owner) ?? [] }));
  return <details className="scene-advanced scene-objects" open>
    <summary>Objects in this scene ({view?.objects.length ?? 0})</summary>
    <p className="muted">Effects can target only these decorations. Installation objects are shared; show objects belong to this show.</p>
    {view && !view.objects.length && <p>No decorations yet. Effects can still light whole outputs.</p>}
    <ul className="object-rows" aria-label="Scene objects">{view?.objects.map((item) => <li key={keyOf(item)} className="object-row">
      <span className="object-row__name">{item.name}</span>
      <span className="object-row__meta">{item.kind} · {item.resource}</span>
      <span className={`object-badge${item.owner === "installation" ? " object-badge--placed" : ""}`}>{ownerLabel(item.owner)}</span>
      {canEdit && <button type="button" aria-label={`Remove ${item.name} from scene`} disabled={blocked || item.targeted}
        title={item.targeted ? "Effects in this scene target it; change those effects first." : disabledReason ?? undefined}
        onClick={() => void save(view.objects.filter((value) => keyOf(value) !== keyOf(item)))}>Remove</button>}
    </li>)}</ul>
    {canEdit && <div className="object-toolbar">
      <label>Add an object<select value={choice} disabled={blocked} onChange={(event) => setChoice(event.target.value)}>
        <option value="">Choose a decoration</option>
        {groups.map(({ owner, items }) => items.length > 0 && <optgroup key={owner} label={owner === "installation" ? "Installation objects" : "This show's objects"}>
          {items.map((item) => <option key={keyOf(item)} value={keyOf(item)}>{item.name} · {item.kind} · {item.resource}</option>)}
        </optgroup>)}
      </select></label>
      <button type="button" className="primary-button" disabled={blocked || !choice} onClick={() => {
        const item = view?.available.find((value) => keyOf(value) === choice);
        if (item && view) void save([...view.objects, item]);
      }}>Add to scene</button>
      <button type="button" className="primary-button" disabled={blocked || !outputs.some((value) => value.segments.length)}
        onClick={() => setCreating(true)}>New show fixture or path</button>
    </div>}
    {disabledReason && canEdit && <small>{disabledReason}</small>}
    {creating && <section className="installation-object-inspector" aria-label="New show object">
      <h4>New show fixture or path</h4>
      <p className="muted">Only this show can use it. Promote it from the show plan if other shows should reuse it.</p>
      <ObjectDefinitionForm creating canEdit busy={busy} outputs={outputs} saveLabel="Create and add to scene"
        initial={{ resource: outputs.find((value) => value.segments.length)?.resource ?? "", kind: "fixture", id: "", name: "",
          segments: [], precedence: 0, geometry_semantic_ids: [] }}
        onCancel={() => setCreating(false)} onSave={(value) => void create(value)} />
    </section>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
