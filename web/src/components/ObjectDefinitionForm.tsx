import { useEffect, useState, type ReactNode } from "react";
import type { InstallationObjectEdit, ObjectSegmentReference } from "../api";

export interface ObjectOutput {
  resource: string;
  segments: { id: string; start: number; end: number }[];
}

/** Absolute pixel order of one member (honouring its optional pixel range). */
export function memberPixels(member: ObjectSegmentReference, segment: { start: number; end: number }) {
  const first = segment.start + (member.start ?? 0);
  const last = segment.start + (member.end ?? segment.end - segment.start) - 1;
  return member.reverse ? { first: last, last: first, count: last - first + 1 } : { first, last, count: last - first + 1 };
}

export function definitionIssues(edit: InstallationObjectEdit, outputs: ObjectOutput[]): string[] {
  const output = outputs.find((value) => value.resource === edit.resource);
  const issues: string[] = [];
  if (!edit.id.trim()) issues.push("Enter a stable object ID.");
  if (!edit.name.trim()) issues.push("Enter an object name.");
  if (!edit.segments.length) issues.push("Add at least one member segment.");
  const covered = new Set<string>();
  for (const [index, member] of edit.segments.entries()) {
    const segment = output?.segments.find((value) => value.id === member.segment);
    if (!segment) { issues.push(`Choose a segment for member ${index + 1}.`); continue; }
    const size = segment.end - segment.start;
    if (member.start !== undefined && (member.end === undefined || member.start < 0 || member.end > size || member.end <= member.start)) {
      issues.push(`Member ${index + 1} range must stay inside ${segment.id} (pixels 1–${size}).`);
      continue;
    }
    const { first, last } = memberPixels(member, segment);
    for (let pixel = Math.min(first, last); pixel <= Math.max(first, last); pixel++) {
      if (covered.has(`${pixel}`)) { issues.push(`Member ${index + 1} repeats pixels of another member.`); break; }
      covered.add(`${pixel}`);
    }
  }
  if (edit.kind === "fixture" && (!Number.isInteger(edit.precedence) || edit.precedence < 0 || edit.precedence > 1000)) {
    issues.push("Fixture precedence must be a whole number from 0 to 1000.");
  }
  return issues;
}

/** Fixture / ordered path definition: members are whole segments or pixel ranges inside one. */
export function ObjectDefinitionForm({ initial, outputs, creating, canEdit, busy, saveLabel, onSave, onCancel, onDelete, deleteBlocked, onDirtyChange, children }: {
  initial: InstallationObjectEdit;
  outputs: ObjectOutput[];
  creating: boolean;
  canEdit: boolean;
  busy: boolean;
  saveLabel?: string;
  onSave: (value: InstallationObjectEdit) => void;
  onCancel: () => void;
  onDelete?: () => void;
  deleteBlocked?: string | null;
  onDirtyChange?: (dirty: boolean) => void;
  children?: (value: InstallationObjectEdit, change: (patch: Partial<InstallationObjectEdit>) => void) => ReactNode;
}) {
  const [edit, setEdit] = useState<InstallationObjectEdit>(initial);
  const dirty = creating || JSON.stringify(edit) !== JSON.stringify(initial);
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);
  const output = outputs.find((value) => value.resource === edit.resource);
  const members = edit.segments;
  const issues = definitionIssues(edit, outputs);
  const change = (patch: Partial<InstallationObjectEdit>) => setEdit((value) => ({ ...value, ...patch }));
  const setMember = (index: number, patch: Partial<ObjectSegmentReference>) =>
    change({ segments: members.map((value, offset) => offset === index ? { ...value, ...patch } : value) });
  const total = members.reduce((sum, member) => {
    const segment = output?.segments.find((value) => value.id === member.segment);
    return segment ? sum + memberPixels(member, segment).count : sum;
  }, 0);
  return <form className="object-definition-form" onSubmit={(event) => { event.preventDefault(); if (!issues.length) onSave(edit); }}>
    <fieldset disabled={!canEdit || busy}>
      <label>Object type<select value={edit.kind} disabled={!creating} onChange={(event) =>
        change({ kind: event.target.value === "path" ? "path" : "fixture", precedence: 0 })}>
        <option value="fixture">Fixture</option><option value="path">Ordered path</option>
      </select></label>
      <label>Output resource<select value={edit.resource} disabled={!creating} onChange={(event) =>
        change({ resource: event.target.value, segments: [], geometry_semantic_ids: [] })}>
        {outputs.map((value) => <option key={value.resource}>{value.resource}</option>)}
      </select></label>
      <label>Stable object ID<input required maxLength={200} value={edit.id} disabled={!creating}
        onChange={(event) => change({ id: event.target.value })} /></label>
      <label>Object name<input required maxLength={200} value={edit.name} onChange={(event) => change({ name: event.target.value })} /></label>
      <p>Members are traversed in order. Use a part of a segment to address individual pixels without changing the installation wiring.</p>
      <ol className="object-member-list">
        {members.map((member, index) => {
          const segment = output?.segments.find((value) => value.id === member.segment);
          const size = segment ? segment.end - segment.start : 0;
          const partial = member.start !== undefined;
          return <li key={index}>
            <label>Member segment {index + 1}<select required value={member.segment} onChange={(event) =>
              setMember(index, { segment: event.target.value, start: undefined, end: undefined })}>
              <option value="">Choose a segment</option>
              {output?.segments.map((value) => <option key={value.id} value={value.id}>{value.id} · pixels {value.start}–{value.end - 1}</option>)}
            </select></label>
            <label><input type="checkbox" checked={member.reverse} onChange={(event) => setMember(index, { reverse: event.target.checked })} />Reverse member {index + 1}</label>
            {segment && <label><input type="checkbox" checked={partial} onChange={(event) =>
              setMember(index, event.target.checked ? { start: 0, end: size } : { start: undefined, end: undefined })} />Only part of {segment.id}</label>}
            {segment && partial && <div className="object-member-range">
              <label>First pixel of member {index + 1}<input type="number" min={1} max={size} value={(member.start ?? 0) + 1}
                onChange={(event) => setMember(index, { start: event.target.valueAsNumber - 1 })} /></label>
              <label>Last pixel of member {index + 1}<input type="number" min={1} max={size} value={member.end ?? size}
                onChange={(event) => setMember(index, { end: event.target.valueAsNumber })} /></label>
              <small>Counted within {segment.id} (1–{size}).</small>
            </div>}
            <button type="button" disabled={index === 0} aria-label={`Move member ${index + 1} up`} onClick={() => {
              const reordered = [...members]; [reordered[index - 1], reordered[index]] = [reordered[index], reordered[index - 1]];
              change({ segments: reordered });
            }}>Up</button>
            <button type="button" disabled={index === members.length - 1} aria-label={`Move member ${index + 1} down`} onClick={() => {
              const reordered = [...members]; [reordered[index], reordered[index + 1]] = [reordered[index + 1], reordered[index]];
              change({ segments: reordered });
            }}>Down</button>
            <button type="button" aria-label={`Remove member ${index + 1}`} onClick={() => change({ segments: members.filter((_, offset) => offset !== index) })}>Remove member</button>
          </li>;
        })}
      </ol>
      <button type="button" disabled={!output?.segments.length || members.some((member) => !member.segment)}
        onClick={() => change({ segments: [...members, { segment: "", reverse: false }] })}>Add member segment</button>
      {!output?.segments.length && <p className="muted">This output has no wiring segments yet. Define them under Installation → Objects.</p>}
      {edit.kind === "fixture" && <label>Fixture precedence<input required type="number" min="0" max="1000" step="1" value={edit.precedence}
        onChange={(event) => change({ precedence: event.target.valueAsNumber })} />
        <small>Added to cue precedence when resolving overlapping effects. Higher precedence is evaluated later.</small>
      </label>}
      <h4>Pixel traversal preview</h4>
      <ol aria-label="Pixel traversal preview">{members.map((member, index) => {
        const segment = output?.segments.find((value) => value.id === member.segment);
        if (!segment) return null;
        const pixels = memberPixels(member, segment);
        return <li key={index}>{member.segment}: {pixels.first} → {pixels.last} ({pixels.count} pixels)</li>;
      })}</ol>
      <p>{total} total pixels</p>
      {children?.(edit, change)}
      {issues.length > 0 && <ul className="object-issues">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
      {canEdit && <div className="object-toolbar">
        <button type="submit" className="primary-button" disabled={issues.length > 0 || busy}>{busy ? "Saving..." : saveLabel ?? "Save object"}</button>
      </div>}
    </fieldset>
    <div className="object-toolbar">
      <button type="button" disabled={busy} onClick={onCancel}>Close properties</button>
      {!creating && canEdit && onDelete && <button type="button" className="danger-button" disabled={busy || Boolean(deleteBlocked)}
        title={deleteBlocked ?? undefined} onClick={onDelete}>Delete definition</button>}
    </div>
    {deleteBlocked && <p>{deleteBlocked}</p>}
  </form>;
}
