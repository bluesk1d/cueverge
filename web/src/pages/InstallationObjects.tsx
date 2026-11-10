import { useEffect, useRef, useState } from "react";
import {
  editInstallationObject, getInstallationObjects, setInstallationSegments,
  type InstallationLevelObjects, type InstallationObjectEdit, type InstallationObjectRecord,
  type ObjectLayoutSelection, type Session,
} from "../api";
import { ObjectDefinitionForm } from "../components/ObjectDefinitionForm";
import { EmptyState, Panel } from "../components/ui";

const keyOf = (item: { resource: string; kind: string; id: string }) =>
  JSON.stringify([item.resource, item.kind, item.id]);
const KIND_GROUPS = [
  { kind: "fixture", label: "Fixtures" },
  { kind: "path", label: "Ordered paths" },
  { kind: "segment", label: "Segments (wiring)" },
] as const;

type WiringRow = { id: string; name: string; count: number };

/** Installation-level objects: wiring segments and reusable fixtures/paths every show inherits. */
export function InstallationObjects({ session, installationId, onDirtyChange, onLayout }: {
  session?: Session;
  installationId: string;
  onDirtyChange: (dirty: boolean) => void;
  onLayout: (selection: ObjectLayoutSelection) => void;
}) {
  const [inventory, setInventory] = useState<InstallationLevelObjects | null>(null);
  const [selected, setSelected] = useState<InstallationObjectRecord | null>(null);
  const [editing, setEditing] = useState<InstallationObjectEdit | null>(null);
  const [creating, setCreating] = useState(false);
  const [wiring, setWiring] = useState<{ resource: string; rows: WiringRow[] } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [retry, setRetry] = useState(0);
  const links = useRef<HTMLFieldSetElement>(null);
  const canEdit = session !== undefined && ["designer", "publisher", "administrator"].includes(session.role);
  useEffect(() => {
    let current = true;
    setError(null);
    void getInstallationObjects(installationId).then((value) => {
      if (!Array.isArray(value?.objects)) throw new Error("Installation objects are unavailable.");
      if (current) setInventory(value);
    })
      .catch((failure: unknown) => { if (current) setError(message(failure)); });
    return () => { current = false; };
  }, [installationId, retry]);
  useEffect(() => {
    onDirtyChange(dirty);
    const guard = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const discard = () => !dirty || window.confirm("Discard unsaved object changes?");
  function close() { setSelected(null); setEditing(null); setCreating(false); setWiring(null); setDirty(false); }
  function choose(item: InstallationObjectRecord) {
    if (!discard()) return;
    close(); setSelected(item); setError(null); setNotice(null);
    if (item.kind !== "segment") {
      setEditing({
        resource: item.resource, kind: item.kind, id: item.id, name: item.name,
        segments: item.segments.map((member) => ({ ...member })),
        precedence: item.precedence, geometry_semantic_ids: [...item.geometry_semantic_ids],
      });
    }
  }
  function startWiring(resource: string) {
    if (!discard() || !inventory) return;
    const output = inventory.resources.find((value) => value.resource === resource);
    if (!output) return;
    close();
    const names = Object.fromEntries(inventory.objects.filter((item) => item.kind === "segment" && item.resource === resource)
      .map((item) => [item.id, item.name]));
    setWiring({ resource, rows: output.segments.length
      ? output.segments.map((segment) => ({ id: segment.id, name: names[segment.id] ?? "", count: segment.end - segment.start }))
      : [{ id: "all", name: "Whole output", count: output.pixel_count }] });
  }
  async function run(work: () => Promise<InstallationLevelObjects>, done: string) {
    setBusy(true); setError(null); setNotice(null);
    try {
      setInventory(await work());
      close();
      setNotice(done);
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }
  if (!inventory) {
    return <Panel title="Installation objects" description="Wiring and reusable decorations shared by every show." className="installation-objects-panel">
      {error ? <p role="alert">{error}</p> : <p role="status">Loading installation objects...</p>}
    </Panel>;
  }
  const outputs = inventory.resources;
  const query = search.trim().toLowerCase();
  const wiringOutput = wiring ? outputs.find((value) => value.resource === wiring.resource) : undefined;
  const wiringTotal = wiring?.rows.reduce((sum, row) => sum + (Number.isFinite(row.count) ? row.count : 0), 0) ?? 0;
  const wiringValid = Boolean(wiring && wiringOutput && wiringTotal === wiringOutput.pixel_count &&
    wiring.rows.every((row) => row.id.trim() && Number.isInteger(row.count) && row.count > 0) &&
    new Set(wiring.rows.map((row) => row.id.trim())).size === wiring.rows.length);
  return <Panel title="Installation objects" className="installation-objects-panel"
    description="Wiring segments and reusable fixtures/paths every show of this installation inherits. Show-only decorations are managed inside each show.">
    <div className="object-toolbar">
      <button className="primary-button" disabled={busy} onClick={() => { if (discard()) { close(); setRetry((value) => value + 1); } }}>Refresh objects</button>
      {canEdit && <button className="primary-button" disabled={busy || !outputs.some((value) => value.segments.length)} onClick={() => {
        if (!discard()) return;
        const first = outputs.find((value) => value.segments.length) ?? outputs[0];
        close(); setCreating(true); setDirty(true);
        setEditing({ resource: first.resource, kind: "fixture", id: "", name: "", segments: [], precedence: 0, geometry_semantic_ids: [] });
      }}>Create installation fixture or path</button>}
    </div>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <p>Changes here apply to every show that inherits installation objects as a new working-draft revision. They never publish, apply or change live playback.</p>
    <label>Find an object<input value={search} onChange={(event) => setSearch(event.target.value)} /></label>
    {!outputs.length && <EmptyState title="No pixel outputs" description="Add an LED controller or LIFX bulb under Devices first." />}
    <div className="installation-objects">
      <div className="object-groups" role="group" aria-label="Logical objects">
        {outputs.map((output) => {
          const items = inventory.objects.filter((item) => item.resource === output.resource &&
            `${item.name} ${item.id} ${item.resource} ${item.kind}`.toLowerCase().includes(query));
          return <section key={output.resource} className="object-group" aria-label={`Objects on ${output.resource}`}>
            <h3>{output.resource} <small>{output.pixel_count} pixels</small></h3>
            {canEdit && <div className="object-toolbar">
              <button type="button" disabled={busy} onClick={() => startWiring(output.resource)}>Edit wiring for {output.resource}</button>
            </div>}
            {KIND_GROUPS.map(({ kind, label }) => {
              const group = items.filter((item) => item.kind === kind);
              if (!group.length) return null;
              // Segments mirror wiring and can be numerous; keep them collapsed unless searching.
              return <details key={kind} className="object-kind-group" open={kind !== "segment" || Boolean(query)}>
                <summary>{label} <span className="object-count">{group.length}</span></summary>
                <ul className="object-rows">{group.map((item) => <li key={keyOf(item)} className="object-row"
                  data-selected={selected !== null && keyOf(selected) === keyOf(item)}>
                  <button type="button" className="object-row__name" disabled={busy}
                    aria-pressed={selected !== null && keyOf(selected) === keyOf(item)}
                    onClick={() => choose(item)}><span className="sr-only">Inspect </span>{item.name}</button>
                  <span className="object-row__meta">{item.pixel_count} px</span>
                  <span className={`object-badge${item.geometry_ids.length ? " object-badge--placed" : ""}`}>
                    {item.geometry_ids.length ? "Placed" : "Not placed"}</span>
                  {item.used_by.length > 0 && <span className="object-row__meta">
                    {item.used_by.length} {item.used_by.length === 1 ? "show" : "shows"}</span>}
                </li>)}</ul>
              </details>;
            })}
          </section>;
        })}
      </div>
      {(selected || editing || wiring) && <section className="installation-object-inspector" aria-label="Object properties">
        <h3>{wiring ? `Wiring for ${wiring.resource}` : creating ? "New installation object" : selected?.name}</h3>
        {selected && <>
          <p>ID: {selected.id} · Output: {selected.resource} · {selected.pixel_count} pixels</p>
          {inventory.layout_available && <div className="object-toolbar">
            {selected.geometry_ids.length > 0 && <button type="button" className="primary-button" disabled={busy} onClick={() => {
              if (discard()) onLayout({ name: selected.name, geometryIds: selected.geometry_ids });
            }}>Show in Layout</button>}
            {canEdit && selected.kind !== "segment" && !selected.geometry_ids.length &&
              <button type="button" className="primary-button" disabled={busy}
                onClick={() => links.current?.scrollIntoView({ block: "nearest" })}>Place in Layout</button>}
          </div>}
          <details><summary>Used by ({selected.used_by.length} shows)</summary>
            {selected.used_by.length ? <ul>{selected.used_by.map((usage) =>
              <li key={usage.project_id}>{usage.project_name}: {usage.scene_ids.join(", ")}</li>)}</ul>
              : <p>No show scene uses this object yet. Scenes add installation objects from their Objects panel.</p>}
          </details>
          {selected.missing_geometry_semantic_ids.length > 0 &&
            <p role="alert">Some linked layout geometry was removed. Clear or replace these links before saving.</p>}
        </>}
        {selected?.kind === "segment" && !wiring && <>
          <p>Segments are the physical wiring partition of {selected.resource}. Every show inherits them.</p>
          <ol>{selected.traversal.map((step) => <li key={step.segment}>
            Pixels {step.first_pixel} to {step.last_pixel} · {step.pixel_count} pixels
          </li>)}</ol>
          {canEdit && <button type="button" className="primary-button" onClick={() => startWiring(selected.resource)}>Edit wiring for {selected.resource}</button>}
        </>}
        {wiring && wiringOutput && <form onSubmit={(event) => {
          event.preventDefault();
          let start = 0;
          const segments = wiring.rows.map((row) => { const segment = { id: row.id.trim(), start, end: start + row.count }; start += row.count; return segment; });
          void run(() => setInstallationSegments(installationId, inventory.revision, {
            resource: wiring.resource, segments,
            names: Object.fromEntries(wiring.rows.filter((row) => row.name.trim()).map((row) => [row.id.trim(), row.name.trim()])),
          }), `Wiring for ${wiring.resource} saved. Inheriting shows were updated.`);
        }}>
          <p>Segments run in order from pixel 0 and must cover all {wiringOutput.pixel_count} pixels. Removing a segment that a show uses is refused.</p>
          <ol className="object-member-list">{wiring.rows.map((row, index) => <li key={index}>
            <label>Segment {index + 1} ID<input required value={row.id} onChange={(event) => {
              setDirty(true); setWiring({ ...wiring, rows: wiring.rows.map((value, offset) => offset === index ? { ...value, id: event.target.value } : value) });
            }} /></label>
            <label>Segment {index + 1} name<input value={row.name} onChange={(event) => {
              setDirty(true); setWiring({ ...wiring, rows: wiring.rows.map((value, offset) => offset === index ? { ...value, name: event.target.value } : value) });
            }} /></label>
            <label>Segment {index + 1} pixel count<input required type="number" min={1} value={row.count} onChange={(event) => {
              setDirty(true); setWiring({ ...wiring, rows: wiring.rows.map((value, offset) => offset === index ? { ...value, count: event.target.valueAsNumber } : value) });
            }} /></label>
            <button type="button" disabled={wiring.rows.length <= 1} aria-label={`Remove segment ${index + 1}`} onClick={() => {
              setDirty(true); setWiring({ ...wiring, rows: wiring.rows.filter((_, offset) => offset !== index) });
            }}>Remove segment</button>
          </li>)}</ol>
          <button type="button" onClick={() => { setDirty(true); setWiring({ ...wiring, rows: [...wiring.rows, { id: "", name: "", count: 1 }] }); }}>Add segment</button>
          <p>{wiringTotal} of {wiringOutput.pixel_count} pixels assigned.</p>
          <div className="object-toolbar">
            <button type="submit" className="primary-button" disabled={!wiringValid || busy}>{busy ? "Saving..." : "Save wiring"}</button>
            <button type="button" disabled={busy} onClick={() => { if (discard()) close(); }}>Cancel</button>
          </div>
        </form>}
        {editing && !wiring && <ObjectDefinitionForm key={creating ? "new" : keyOf(editing)} initial={editing} outputs={outputs}
          creating={creating} canEdit={canEdit} busy={busy} onDirtyChange={setDirty}
          onCancel={() => { if (discard()) close(); }}
          deleteBlocked={selected?.used_by.length ? "Remove this object from every scene that uses it before deleting." : null}
          onDelete={() => {
            if (!window.confirm(`Delete ${editing.name} from the installation? Layout geometry is retained.`)) return;
            void run(() => editInstallationObject(installationId, inventory.revision, "delete",
              { resource: editing.resource, kind: editing.kind, id: editing.id }), "Installation object deleted.");
          }}
          onSave={(value) => void run(() => editInstallationObject(installationId, inventory.revision, creating ? "create" : "update", value),
            "Saved. Every inheriting show now uses this definition; live playback is unchanged.")}>
          {(value, change) => <fieldset ref={links} disabled={!inventory.layout_available}>
            <legend>Link existing layout geometry</legend>
            <p>Mapped member segments are highlighted automatically. Optionally link an existing fixture marker, path, or same-output segment.</p>
            {inventory.geometry.filter((shape) => (shape.kind === "pixel_segment" && shape.resource === value.resource) || shape.kind === value.kind ||
              value.geometry_semantic_ids.includes(shape.semantic_id)).map((shape) =>
              <label key={shape.semantic_id}><input type="checkbox" checked={value.geometry_semantic_ids.includes(shape.semantic_id)}
                onChange={(event) => change({ geometry_semantic_ids: event.target.checked
                  ? [...value.geometry_semantic_ids, shape.semantic_id]
                  : value.geometry_semantic_ids.filter((id) => id !== shape.semantic_id) })} />{shape.name} · {shape.kind.replaceAll("_", " ")}</label>)}
            {!inventory.geometry.length && <p>No layout geometry yet. The object remains usable as an effect target without placement.</p>}
          </fieldset>}
        </ObjectDefinitionForm>}
      </section>}
    </div>
  </Panel>;
}

function message(failure: unknown): string {
  return failure instanceof Error ? failure.message : "Could not complete the installation object request.";
}
