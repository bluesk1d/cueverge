import { useEffect, useRef, useState } from "react";
import {
  ApiError, deleteInstallationPhoto, describeInstallationPhoto, InstallationPhoto,
  installationPhotoUrl, updateInstallationPhoto, replaceInstallationPhoto,
} from "../api";
import { DetailsDialog } from "./DetailsDialog";
import { ErrorState } from "./ui";

export function InstallationPhotoCard({ photo, canEdit, onRefresh, onLayout, mappedCount, isStageView = false, selected = false }: {
  photo: InstallationPhoto; canEdit: boolean; onRefresh: () => Promise<void>;
  onLayout: () => void; mappedCount: number; isStageView?: boolean; selected?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [missing, setMissing] = useState(false);
  const [retry, setRetry] = useState(0);
  return <article className={`installation-photo-card${selected ? " installation-photo-card--selected" : ""}`}
    aria-current={selected ? "true" : undefined}>
    <div className="installation-photo-card__thumb">
      <img alt={photo.description ?? photo.name} src={`${installationPhotoUrl(photo.id, photo.revision)}&retry=${retry}`}
        style={{ transform: `rotate(${photo.orientation_degrees}deg)` }}
        onError={() => setMissing(true)} onLoad={() => setMissing(false)} />
      {isStageView && <span className="installation-photo-card__badge">Stage view</span>}
    </div>
    <div className="installation-photo-card__body">
      <strong>{photo.description ?? photo.name}</strong>
      <small>{mappedCount ? `${mappedCount} mapped ${mappedCount === 1 ? "object" : "objects"}` : "Nothing arranged yet"}</small>
    </div>
    {missing && <><p role="alert">This photo could not be loaded.</p><button onClick={() => setRetry((value) => value + 1)}>Retry photo</button></>}
    <div className="installation-photo-card__actions">
      <button type="button" className="primary-button" onClick={onLayout}>Open Layout</button>
      {canEdit && <button type="button" className="ghost-button" onClick={() => setOpen(true)}>Replace photo</button>}
      <button type="button" className="ghost-button" onClick={() => setOpen(true)}>{canEdit ? "Photo details" : "Photo information"}</button>
    </div>
    {open && <PhotoEditor key={`${photo.id}:${photo.revision}`} photo={photo} canEdit={canEdit}
      mappedCount={mappedCount} onClose={() => setOpen(false)} onRefresh={onRefresh} />}
  </article>;
}

function PhotoEditor({ photo, canEdit, mappedCount, onClose, onRefresh }: {
  photo: InstallationPhoto; canEdit: boolean; mappedCount: number;
  onClose: () => void; onRefresh: () => Promise<void>;
}) {
  const [description, setDescription] = useState(photo.description ?? photo.name);
  const [orientation, setOrientation] = useState(photo.orientation_degrees);
  const [scale, setScale] = useState(photo.scale_hint?.toString() ?? "");
  const [crop, setCrop] = useState({
    x: photo.crop_x?.toString() ?? "", y: photo.crop_y?.toString() ?? "",
    width: photo.crop_width?.toString() ?? "", height: photo.crop_height?.toString() ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [replacement, setReplacement] = useState<File | null>(null);
  const replacementInput = useRef<HTMLInputElement>(null);
  const [reviewAlignment, setReviewAlignment] = useState(false);
  const metadataChanged = orientation !== photo.orientation_degrees ||
    scale !== (photo.scale_hint?.toString() ?? "") ||
    crop.x !== (photo.crop_x?.toString() ?? "") || crop.y !== (photo.crop_y?.toString() ?? "") ||
    crop.width !== (photo.crop_width?.toString() ?? "") || crop.height !== (photo.crop_height?.toString() ?? "");
  const metadataDirty = description !== (photo.description ?? photo.name) || metadataChanged;
  const dirty = metadataDirty || replacement !== null;
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  function close() {
    if (!busy && (!dirty || window.confirm("Discard unsaved photo changes?"))) onClose();
  }
  async function save() {
    setBusy(true); setError(null);
    try {
      if (metadataChanged) {
        await updateInstallationPhoto(photo, {
          name: description, viewpoint: photo.viewpoint, orientation_degrees: orientation,
          scale_hint: scale ? Number(scale) : null,
          crop_x: crop.x ? Number(crop.x) : null, crop_y: crop.y ? Number(crop.y) : null,
          crop_width: crop.width ? Number(crop.width) : null,
          crop_height: crop.height ? Number(crop.height) : null,
        });
      } else {
        await describeInstallationPhoto(photo, description);
      }
      await onRefresh(); onClose();
    } catch (failure) {
      setError(failure instanceof ApiError && failure.status === 409
        ? "This photo changed elsewhere. Close this editor, refresh photos, then reopen it before saving again."
        : failure instanceof Error ? failure.message : "Photo changes could not be saved.");
    } finally { setBusy(false); }
  }
  async function remove() {
    if (!window.confirm(`Remove the reference photo "${photo.name}"? Its ${mappedCount} layout objects, wiring and calibration will be kept on the installation canvas. Use Replace photo to change only the image.`)) return;
    setBusy(true); setError(null);
    try { await deleteInstallationPhoto(photo); await onRefresh(); onClose(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "The photo could not be removed."); }
    finally { setBusy(false); }
  }
  async function replace() {
    if (!replacement || !reviewAlignment) return;
    setBusy(true); setError(null);
    try {
      await replaceInstallationPhoto(photo, replacement);
      await onRefresh(); onClose();
    } catch (failure) {
      setError(failure instanceof ApiError && failure.status === 409
        ? "This photo changed. Close, refresh photos, then review the replacement again."
        : failure instanceof Error ? failure.message : "Photo replacement failed.");
    } finally { setBusy(false); }
  }
  return <DetailsDialog title="Photo details" onClose={close}>
    <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <fieldset disabled={busy || !canEdit || replacement !== null}>
        <label>Photo description<input autoFocus required maxLength={200} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        <small>Describe what this photo shows or how you use it. Existing view identities and mappings are retained.</small>
        <details><summary>Image and calibration details</summary>
          <p>Legacy view label: {photo.viewpoint}. File: {photo.original_filename} · {(photo.size_bytes / 1_000_000).toFixed(1)} MB.</p>
          <label>Photo orientation<select value={orientation} onChange={(event) => {
            const value = Number(event.target.value);
            if (value === 0 || value === 90 || value === 180 || value === 270) setOrientation(value);
          }}>{[0, 90, 180, 270].map((value) => <option key={value} value={value}>{value} degrees</option>)}</select></label>
          <label>Scale hint (pixels per metre)<input type="number" min={0} step="any" value={scale} onChange={(event) => setScale(event.target.value)} /></label>
          <p>Crop values use the original image's normalized coordinates. Set all four or leave all blank.</p>
          {(["x", "y", "width", "height"] as const).map((field) => <label key={field}>Crop {field}<input type="number" min={0} max={1} step="any" value={crop[field]} onChange={(event) => setCrop({ ...crop, [field]: event.target.value })} /></label>)}
        </details>
        {canEdit && <button className="primary-button" disabled={!metadataDirty || !description.trim()}>{busy ? "Saving…" : "Save photo"}</button>}
      </fieldset>
      {error && <ErrorState title="Photo action failed" description={error} />}
    </form>
    {canEdit && <section aria-label="Replace photo">
      <h3>Replace photo</h3>
      <p>Change only the image. Keep the photo identity and all {mappedCount} layout objects, wiring, calibration and view placements. A different camera angle may need alignment review; nothing is repositioned automatically.</p>
      <fieldset disabled={busy || metadataChanged || description !== (photo.description ?? photo.name)}>
        <label>Replacement photo<input ref={replacementInput} type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => {
          setReplacement(event.target.files?.[0] ?? null); setReviewAlignment(false);
        }} /></label>
        <label><input type="checkbox" checked={reviewAlignment} onChange={(event) => setReviewAlignment(event.target.checked)} />I will review the new background alignment; geometry and calibration are not moved.</label>
        <button type="button" className="primary-button" disabled={!replacement || !reviewAlignment} onClick={() => void replace()}>Replace photo, keep layout</button>
        {replacement && <button type="button" onClick={() => {
          setReplacement(null); setReviewAlignment(false);
          if (replacementInput.current) replacementInput.current.value = "";
        }}>Cancel photo replacement</button>}
      </fieldset>
      {(metadataChanged || description !== (photo.description ?? photo.name)) && <p>Save or discard description/calibration edits before replacing the image.</p>}
    </section>}
    {canEdit && <details><summary>Remove photo</summary>
      <p>Only this reference background is removed. Its {mappedCount} layout objects, wiring and calibration stay available without a photo. Geometry must be deleted explicitly in Layout.</p>
      <button type="button" className="danger-button" disabled={busy} onClick={() => void remove()}>Remove photo</button>
    </details>}
  </DetailsDialog>;
}
