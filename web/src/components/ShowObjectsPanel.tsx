import { useEffect, useState } from "react";
import {
  editShowObject, getShowObjectSummary, getShowObjects, showObjectAction,
  type InstallationObjectEdit, type InstallationObjectInventory, type InstallationObjectRecord,
  type ProjectDraftRecord, type ShowObjectSummary,
} from "../api";
import { ObjectDefinitionForm } from "./ObjectDefinitionForm";
import { pixelOutputs } from "./SceneObjectsPanel";

const keyOf = (item: { resource: string; kind: string; id: string }) => `${item.resource}/${item.kind}/${item.id}`;

/** Show-level roll-up (read-only) plus the fixtures/paths this show owns. */
export function ShowObjectsPanel({ draft, canEdit, canEditInstallation, onChanged, onOpenScene }: {
  draft: ProjectDraftRecord;
  canEdit: boolean;
  canEditInstallation: boolean;
  onChanged: (updated: ProjectDraftRecord) => void;
  onOpenScene: (sceneId: string) => void;
}) {
  const [summary, setSummary] = useState<ShowObjectSummary | null>(null);
  const [inventory, setInventory] = useState<InstallationObjectInventory | null>(null);
  const [editing, setEditing] = useState<InstallationObjectRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const projectId = draft.project.id;
  useEffect(() => {
    let current = true;
    void Promise.all([getShowObjectSummary(projectId), getShowObjects(projectId)]).then(([rollup, objects]) => {
      if (!Array.isArray(rollup?.scenes) || !Array.isArray(objects?.objects)) throw new Error("Show objects are unavailable.");
      if (current) { setSummary(rollup); setInventory(objects); }
    }).catch((failure: unknown) => { if (current) setError(failure instanceof Error ? failure.message : String(failure)); });
    return () => { current = false; };
  }, [projectId, draft.revision]);

  async function run(work: () => Promise<ProjectDraftRecord>, done: string) {
    setBusy(true); setError(null); setNotice(null);
    try {
      onChanged(await work());
      setEditing(null);
      setNotice(done);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }
  const names = new Map(summary?.objects.map((item) => [keyOf(item), item.name]) ?? []);
  const own = inventory?.objects.filter((item) => item.owner === "show" && item.kind !== "segment") ?? [];
  const unused = own.filter((item) => !item.scene_ids.length);
  const installationCount = summary?.objects.filter((item) => item.owner === "installation").length ?? 0;
  return <section className="show-objects" aria-label="Devices and objects">
    <h3>Devices and objects used by this show</h3>
    <p className="muted">A roll-up of what each scene uses. Add or remove decorations inside each scene.</p>
    {summary && <p>{summary.devices.length} devices · {summary.objects.length} objects
      ({installationCount} from the installation, {summary.objects.length - installationCount} show-only)</p>}
    {summary && <ul className="show-object-scenes">{summary.scenes.map((scene) => <li key={scene.scene_id}>
      <button type="button" className="object-row__name" onClick={() => onOpenScene(scene.scene_id)}>
        <span className="sr-only">Open scene </span>{scene.name}</button>
      <small>Devices: {scene.devices.join(", ") || "none"}</small>
      <small>Objects: {scene.objects.map((item) => names.get(keyOf(item)) ?? item.id).join(", ") || "none"}</small>
    </li>)}</ul>}
    {inventory && !inventory.inherits && <div className="object-toolbar">
      <p role="status">This show keeps its own copy of wiring and decorations from before installation objects existed.</p>
      {canEdit && <button type="button" className="primary-button" disabled={busy}
        onClick={() => void run(() => showObjectAction(projectId, draft.revision, "adopt"), "This show now inherits installation wiring and objects.")}>
        Use installation objects</button>}
    </div>}
    <details className="scene-advanced">
      <summary>This show's own fixtures and paths ({own.length})</summary>
      <p className="muted">Visible only in this show. Promote one to reuse it in every show of the installation.</p>
      <ul className="object-rows" aria-label="Show objects">{own.map((item) => <li key={keyOf(item)} className="object-row">
        <span className="object-row__name">{item.name}</span>
        <span className="object-row__meta">{item.kind} · {item.pixel_count} px · {item.scene_ids.length ? `${item.scene_ids.length} scenes` : "unused"}</span>
        {canEdit && <button type="button" aria-label={`Edit ${item.name}`} disabled={busy} onClick={() => setEditing(item)}>Edit</button>}
        {canEdit && canEditInstallation && inventory?.inherits && <button type="button" aria-label={`Promote ${item.name} to installation`} disabled={busy}
          onClick={() => {
            if (!window.confirm(`Make ${item.name} an installation object that every show can use?`)) return;
            void run(() => showObjectAction(projectId, draft.revision, "promote", { resource: item.resource, kind: item.kind as "fixture" | "path", id: item.id }),
              `${item.name} is now an installation object.`);
          }}>Promote</button>}
      </li>)}</ul>
      {canEdit && <div className="object-toolbar">
        <button type="button" className="primary-button" disabled={busy || !unused.length} onClick={() => {
          if (!window.confirm(`Delete ${unused.length} show fixtures/paths no scene uses?`)) return;
          void run(() => showObjectAction(projectId, draft.revision, "remove-unused"), `Removed ${unused.length} unused show objects.`);
        }}>Remove unused show objects ({unused.length})</button>
      </div>}
      {editing && <section className="installation-object-inspector" aria-label="Show object properties">
        <ObjectDefinitionForm key={keyOf(editing)} creating={false} canEdit={canEdit} busy={busy} outputs={pixelOutputs(draft)}
          initial={{ resource: editing.resource, kind: editing.kind as "fixture" | "path", id: editing.id, name: editing.name,
            segments: editing.segments.map((member) => ({ ...member })), precedence: editing.precedence,
            geometry_semantic_ids: [...editing.geometry_semantic_ids] }}
          deleteBlocked={editing.references.length ? "Effects still target this object; change them before deleting." : null}
          onCancel={() => setEditing(null)}
          onDelete={() => {
            if (!window.confirm(`Delete ${editing.name} from this show?`)) return;
            void run(() => editShowObject(projectId, draft.revision, "delete",
              { resource: editing.resource, kind: editing.kind as "fixture" | "path", id: editing.id }), "Show object deleted.");
          }}
          onSave={(value: InstallationObjectEdit) => void run(() => editShowObject(projectId, draft.revision, "update", value), "Show object saved.")} />
      </section>}
    </details>
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
