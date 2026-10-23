import { useEffect, useRef, useState } from "react";
import {
  editInstallationObject, getInstallationObjects, getProjectMembers, getProjects,
  type InstallationObjectEdit, type InstallationObjectInventory, type InstallationObjectRecord,
  type ObjectLayoutSelection, type ProjectSummary, type Session,
} from "../api";
import { EmptyState, Panel } from "../components/ui";

const keyOf = (item: { resource: string; kind: string; id: string }) =>
  JSON.stringify([item.resource, item.kind, item.id]);

export function InstallationObjects({ session, projectId, onProjectChange, onDirtyChange, onLayout }: {
  session?: Session;
  projectId: string;
  onProjectChange: (id: string) => void;
  onDirtyChange: (dirty: boolean) => void;
  onLayout: (selection: ObjectLayoutSelection) => void;
}) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [inventory, setInventory] = useState<InstallationObjectInventory | null>(null);
  const [canEdit, setCanEdit] = useState(false);
  const [selected, setSelected] = useState<InstallationObjectRecord | null>(null);
  const [editing, setEditing] = useState<InstallationObjectEdit | null>(null);
  const [creating, setCreating] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [retry, setRetry] = useState(0);
  const links = useRef<HTMLFieldSetElement>(null);
  useEffect(() => {
    let current = true;
    void getProjects().then((value) => { if (current) setProjects(value); })
      .catch((failure: unknown) => { if (current) setError(message(failure)); });
    return () => { current = false; };
  }, [retry]);
  useEffect(() => {
    let current = true;
    setInventory(null); setCanEdit(false); setSelected(null); setEditing(null);
    setDirty(false); setNotice(null); setError(null);
    if (!projectId) return;
    setLoading(true);
    void Promise.all([
      getInstallationObjects(projectId),
      session?.role === "administrator" ? Promise.resolve(true) :
        getProjectMembers(projectId).then((members) => members.some((member) =>
          member.username === session?.username &&
          ["designer", "publisher", "administrator"].includes(member.role))),
    ]).then(([value, permitted]) => {
      if (current) { setInventory(value); setCanEdit(permitted && !value.archived); }
    }).catch((failure: unknown) => { if (current) setError(message(failure)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [projectId, retry, session?.role, session?.username]);
  useEffect(() => {
    onDirtyChange(dirty);
    const guard = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  function discard() {
    return !dirty || window.confirm("Discard unsaved object changes?");
  }
  function choose(item: InstallationObjectRecord, place = false) {
    if (!discard()) return;
    setSelected(item); setCreating(false); setDirty(false); setError(null); setNotice(null);
    setEditing(item.kind === "segment" ? null : {
      resource: item.resource, kind: item.kind, id: item.id, name: item.name,
      segments: item.segments.map((member) => ({ ...member })),
      precedence: item.precedence, geometry_semantic_ids: [...item.geometry_semantic_ids],
    });
    if (place) window.setTimeout(() => links.current?.scrollIntoView({ block: "nearest" }), 0);
  }
  function start() {
    if (!discard() || !inventory?.resources.length) return;
    setSelected(null); setCreating(true); setDirty(true); setError(null); setNotice(null);
    setEditing({
      resource: inventory.resources[0].resource, kind: "fixture", id: "", name: "",
      segments: [], precedence: 0, geometry_semantic_ids: [],
    });
  }
  function change(patch: Partial<InstallationObjectEdit>) {
    setEditing((value) => value ? { ...value, ...patch } : null);
    setDirty(true);
  }
  async function save(action: "create" | "update" | "delete") {
    if (!inventory || !editing) return;
    if (action === "delete" && !window.confirm(`Delete ${editing.name}? Installation geometry is retained.`)) return;
    setBusy(true); setError(null); setNotice(null);
    let saved = false;
    try {
      await editInstallationObject(projectId, inventory.revision, action, action === "delete"
        ? { resource: editing.resource, kind: editing.kind, id: editing.id } : editing);
      saved = true;
      setDirty(false); setSelected(null); setEditing(null); setCreating(false);
      setInventory(null);
      setNotice("Saved to the working draft. Live playback is unchanged.");
      setInventory(await getInstallationObjects(projectId));
    } catch (failure) {
      setError(saved ? `Object saved, but inventory refresh failed. Refresh objects before editing again. ${message(failure)}` : message(failure));
    } finally {
      setBusy(false);
    }
  }
  const binding = inventory?.resources.find((value) => value.resource === editing?.resource);
  const members = editing?.segments ?? [];
  const valid = Boolean(editing?.id.trim() && editing.name.trim() && members.length &&
    new Set(members.map((member) => member.segment)).size === members.length &&
    members.every((member) => binding?.segments.some((segment) => segment.id === member.segment)) &&
    Number.isInteger(editing.precedence) && editing.precedence >= 0 && editing.precedence <= 1000);
  const traversal = members.flatMap((member) => {
    const segment = binding?.segments.find((value) => value.id === member.segment);
    return segment ? [{
      segment: member.segment,
      first: member.reverse ? segment.end - 1 : segment.start,
      last: member.reverse ? segment.start : segment.end - 1,
      count: segment.end - segment.start,
    }] : [];
  });
  return <Panel title="Installation objects"
    description="Logical fixtures, segments and ordered paths, including objects without a layout placement.">
    <label>Definitions from show<select value={projectId} disabled={busy} onChange={(event) => {
      if (discard()) onProjectChange(event.target.value);
    }}>
      <option value="">Choose a show explicitly</option>
      {projects.map((project) => <option key={project.id} value={project.id}>
        {project.name}{project.archived_at ? " (archived)" : ""}
      </option>)}
    </select></label>
    {loading && <p role="status">Loading object definitions...</p>}
    {error && <p role="alert">{error}</p>}
    <button disabled={busy || loading} onClick={() => { if (discard()) setRetry((value) => value + 1); }}>Refresh objects</button>
    {notice && <p role="status">{notice}</p>}
    {!projectId && <p>Select a show to inspect its working draft. No show is chosen automatically from live playback.</p>}
    {inventory && <>
      <p>Definitions: <strong>{inventory.project_name}</strong> · Working draft revision {inventory.revision}
        {inventory.archived && " · Archived (read only)"}</p>
      <p>Layout: {inventory.layout_available ? `installation ${inventory.installation_id}` : "not bound to this runtime installation"}.
        Saving definitions does not publish, apply, or change live playback.</p>
      <label>Find an object<input value={search} onChange={(event) => setSearch(event.target.value)} /></label>
      {canEdit && <button disabled={busy || !inventory.resources.length} onClick={start}>Create fixture or path</button>}
      {!inventory.objects.length && <EmptyState title="No logical objects"
        description="This show has no saved LED segment, fixture or path definitions. Unplaced objects will appear here too." />}
      <div className="installation-objects">
        <ul className="resource-list" aria-label="Logical objects">
          {inventory.objects.filter((item) =>
            `${item.name} ${item.id} ${item.resource} ${item.kind}`.toLowerCase().includes(search.toLowerCase()))
            .map((item) => <li key={keyOf(item)}>
              <div><strong>{item.name}</strong><small>{item.kind} · {item.resource} · {item.pixel_count} pixels</small>
                <small>{item.geometry_ids.length ? "Placed / member segments mapped" : "Not placed"} · {item.references.length} references</small>
              </div>
              <div className="installation-object-actions">
                <button disabled={busy} aria-pressed={selected !== null && keyOf(selected) === keyOf(item)}
                  onClick={() => choose(item)}>Inspect {item.name}</button>
                {inventory.layout_available && item.geometry_ids.length > 0 && <button disabled={busy} onClick={() => {
                  if (discard()) onLayout({ name: item.name, geometryIds: item.geometry_ids });
                }}>Show in Layout</button>}
                {canEdit && inventory.layout_available && item.kind !== "segment" && !item.geometry_ids.length &&
                  <button disabled={busy} onClick={() => choose(item, true)}>Place in Layout</button>}
              </div>
            </li>)}
        </ul>
        {(selected || editing) && <section className="installation-object-inspector" aria-label="Object properties">
          <h3>{creating ? "New object" : selected?.name}</h3>
          {selected && <>
            <p>ID: {selected.id} · Output: {selected.resource}</p>
            <details><summary>References ({selected.references.length})</summary>
              {selected.references.length ? <ul>{selected.references.map((path) => <li key={path}><code>{path}</code></li>)}</ul>
                : <p>No effect or authoring references.</p>}
            </details>
            {selected.missing_geometry_semantic_ids.length > 0 &&
              <p role="alert">Some linked layout geometry was removed. Clear or replace these links before saving.</p>}
          </>}
          {selected?.kind === "segment" && <>
            <p>Segments are read-only here. Their contiguous pixel partition is preserved; fixture edits never change wiring.</p>
            <ol>{selected.traversal.map((step) => <li key={step.segment}>
              Pixels {step.first_pixel} to {step.last_pixel} · {step.pixel_count} pixels
            </li>)}</ol>
          </>}
          {editing && <form onSubmit={(event) => { event.preventDefault(); void save(creating ? "create" : "update"); }}>
            <fieldset disabled={!canEdit || busy}>
              <label>Object type<select value={editing.kind} disabled={!creating} onChange={(event) =>
                change({ kind: event.target.value === "path" ? "path" : "fixture", precedence: 0 })}>
                <option value="fixture">Fixture</option><option value="path">Ordered path</option>
              </select></label>
              <label>Output resource<select value={editing.resource} disabled={!creating} onChange={(event) =>
                change({ resource: event.target.value, segments: [], geometry_semantic_ids: [] })}>
                {inventory.resources.map((resource) => <option key={resource.resource}>{resource.resource}</option>)}
              </select></label>
              <label>Stable object ID<input required maxLength={200} value={editing.id} disabled={!creating}
                onChange={(event) => change({ id: event.target.value })} /></label>
              <label>Object name<input required maxLength={200} value={editing.name} onChange={(event) => change({ name: event.target.value })} /></label>
              <p>Membership order and reversal determine traversal. A fixture can group several segments; IDs remain stable during edits.</p>
              <ol className="object-member-list">
                {members.map((member, index) => <li key={`${index}:${member.segment}`}>
                  <label>Member segment {index + 1}<select required value={member.segment} onChange={(event) =>
                    change({ segments: members.map((value, offset) => offset === index ? { ...value, segment: event.target.value } : value) })}>
                    <option value="">Choose a segment</option>
                    {binding?.segments.filter((value) => value.id === member.segment || !members.some((item) => item.segment === value.id))
                      .map((segment) => <option key={segment.id} value={segment.id}>{segment.id} · pixels {segment.start}–{segment.end - 1}</option>)}
                  </select></label>
                  <label><input type="checkbox" checked={member.reverse} onChange={(event) =>
                    change({ segments: members.map((value, offset) => offset === index ? { ...value, reverse: event.target.checked } : value) })} />Reverse member {index + 1}</label>
                  <button type="button" disabled={index === 0} aria-label={`Move member ${index + 1} up`} onClick={() => {
                    const reordered = [...members]; [reordered[index - 1], reordered[index]] = [reordered[index], reordered[index - 1]];
                    change({ segments: reordered });
                  }}>Up</button>
                  <button type="button" disabled={index === members.length - 1} aria-label={`Move member ${index + 1} down`} onClick={() => {
                    const reordered = [...members]; [reordered[index], reordered[index + 1]] = [reordered[index + 1], reordered[index]];
                    change({ segments: reordered });
                  }}>Down</button>
                  <button type="button" aria-label={`Remove member ${index + 1}`} onClick={() => change({ segments: members.filter((_, offset) => offset !== index) })}>Remove member</button>
                </li>)}
              </ol>
              <button type="button" disabled={members.some((member) => !member.segment) || !binding?.segments.some((value) => !members.some((member) => member.segment === value.id))}
                onClick={() => change({ segments: [...members, { segment: "", reverse: false }] })}>Add member segment</button>
              {editing.kind === "fixture" && <label>Fixture precedence<input required type="number" min="0" max="1000" step="1" value={editing.precedence}
                onChange={(event) => change({ precedence: event.target.valueAsNumber })} />
                <small>Added to cue precedence when resolving overlapping effects. Higher precedence is evaluated later.</small>
              </label>}
              <h4>Pixel traversal preview</h4>
              <ol aria-label="Pixel traversal preview">{traversal.map((step, index) =>
                <li key={index}>{step.segment}: {step.first} → {step.last} ({step.count} pixels)</li>)}</ol>
              <p>{traversal.reduce((total, step) => total + step.count, 0)} total pixels</p>
              <fieldset ref={links} disabled={!inventory.layout_available}>
                <legend>Link existing layout geometry</legend>
                <p>Mapped member segments are highlighted automatically. Optionally link an existing fixture marker, path, or same-output segment.
                  Draw new geometry in Photos &amp; Layout first. Links never create duplicate fixture definitions.</p>
                {inventory.geometry.filter((shape) => (shape.kind === "pixel_segment" && shape.resource === editing.resource) || shape.kind === editing.kind ||
                  editing.geometry_semantic_ids.includes(shape.semantic_id)).map((shape) =>
                  <label key={shape.semantic_id}><input type="checkbox" checked={editing.geometry_semantic_ids.includes(shape.semantic_id)}
                    onChange={(event) => change({ geometry_semantic_ids: event.target.checked
                      ? [...editing.geometry_semantic_ids, shape.semantic_id]
                      : editing.geometry_semantic_ids.filter((id) => id !== shape.semantic_id) })} />{shape.name} · {shape.kind.replaceAll("_", " ")}</label>)}
                {selected?.missing_geometry_semantic_ids.map((id) => <label key={id}><input type="checkbox"
                  checked={editing.geometry_semantic_ids.includes(id)} onChange={() =>
                    change({ geometry_semantic_ids: editing.geometry_semantic_ids.filter((value) => value !== id) })} />Missing geometry: {id}</label>)}
                {!inventory.geometry.length && <p>No layout geometry yet. The object remains usable as an effect target without placement.</p>}
                <small>Unlinking geometry only removes the show-owned association. It does not remove geometry or effect references.</small>
              </fieldset>
              {canEdit && <button type="submit" className="primary-button" disabled={!valid || !dirty || busy}>{busy ? "Saving..." : "Save object"}</button>}
            </fieldset>
            <button type="button" disabled={busy} onClick={() => { if (discard()) { setSelected(null); setEditing(null); setDirty(false); } }}>Close properties</button>
            {!creating && canEdit && <button type="button" className="danger-button" disabled={busy || Boolean(selected?.references.length)}
              onClick={() => void save("delete")}>Delete definition</button>}
            {Boolean(selected?.references.length) && <p>Remove references before deleting this definition. Placement removal is separate.</p>}
          </form>}
        </section>}
      </div>
    </>}
  </Panel>;
}

function message(failure: unknown): string {
  return failure instanceof Error ? failure.message : "Could not complete the installation object request.";
}
