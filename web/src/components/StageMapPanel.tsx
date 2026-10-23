import { useEffect, useState } from "react";
import {
  ApiError,
  applyStageMap,
  InstallationPhoto,
  saveStageReference,
  StageMap,
  StageMapItem,
  StageUnit,
} from "../api";
import { ErrorState, Panel } from "./ui";

const UNITS: { value: StageUnit; label: string }[] = [
  { value: "ft", label: "feet" },
  { value: "m", label: "metres" },
  { value: "in", label: "inches" },
  { value: "cm", label: "centimetres" },
  { value: "units", label: "stage units" },
];

const KIND_LABELS: Record<string, string> = {
  pixel_segment: "LED segment",
  projector_surface: "Projector area",
  fixture: "Fixture",
  path: "Path",
  output: "Output",
  mask: "Mask",
  audience_zone: "Audience zone",
  safety_zone: "Safety zone",
};

export function formatStageSize(width: number, height: number, unit: StageUnit): string {
  const round = (value: number) => (value >= 100 ? Math.round(value) : Math.round(value * 10) / 10);
  return `${round(width)} × ${round(height)} ${unit === "units" ? "stage units" : unit}`;
}

export function StageMapPanel({
  installationId,
  photos,
  stageMap,
  loadError,
  canEdit,
  onChanged,
}: {
  installationId: string;
  photos: InstallationPhoto[];
  stageMap: StageMap | null;
  loadError: boolean;
  canEdit: boolean;
  onChanged: () => Promise<void>;
}) {
  const reference = stageMap?.reference ?? null;
  const [photoId, setPhotoId] = useState(reference?.photo_id ?? photos[0]?.id ?? "");
  const [width, setWidth] = useState(String(reference?.stage_width ?? 40));
  const [unit, setUnit] = useState<StageUnit>(reference?.unit_label ?? "ft");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setPhotoId(reference?.photo_id ?? photos[0]?.id ?? "");
    if (reference) {
      setWidth(String(Math.round(reference.stage_width * 1000) / 1000));
      setUnit(reference.unit_label);
    }
  }, [reference?.revision, reference?.photo_id, photos.length]);

  const referencePhoto = photos.find((photo) => photo.id === reference?.photo_id);
  const settingsChanged =
    !reference ||
    reference.photo_id !== photoId ||
    reference.unit_label !== unit ||
    Math.abs(reference.stage_width - Number(width)) > 1e-6;
  const widthValid = Number.isFinite(Number(width)) && Number(width) > 0;
  const notPlaced = (stageMap?.items ?? []).filter(
    (item) => item.status === "manual" || item.status === "unplaced",
  );
  const placed = stageMap?.counts?.placed ?? 0;
  const pending = stageMap?.counts?.needs_update ?? 0;

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 409
          ? "The stage map changed elsewhere. It has been reloaded; review it and try again."
          : failure instanceof Error
            ? failure.message
            : "The stage map could not be saved.",
      );
      if (failure instanceof ApiError && failure.status === 409) await onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    await run(async () => {
      const saved = await saveStageReference(
        installationId,
        { photo_id: photoId, unit_label: unit, stage_width: Number(width) },
        reference?.revision,
      );
      if (saved.reference) await applyStageMap(installationId, saved.reference.revision);
    });
  }

  return (
    <Panel
      title="Stage map"
      className="stage-map-panel"
      description="Effects that travel across several devices, like a flag rippling over projectors and LEDs, are drawn on one shared stage. Choose the photo taken from where visitors watch; everything drawn on it is placed on the stage automatically."
    >
      {loadError && <ErrorState title="Stage map unavailable" description="Reload the installation to see stage placement." />}
      <div className="stage-map-summary" role="status" aria-label="Stage map status">
        {reference && reference.applied_at ? (
          <>
            <strong>
              Stage view: {referencePhoto ? referencePhoto.description ?? referencePhoto.name : "removed photo (placement kept)"} ·{" "}
              {formatStageSize(reference.stage_width, reference.stage_height, reference.unit_label)}
            </strong>
            <span>
              {placed} placed
              {pending > 0 && ` · ${pending} changed since the last update`}
              {notPlaced.length > 0 && ` · ${notPlaced.length} not drawn on the stage view`}
            </span>
          </>
        ) : (
          <>
            <strong>No stage view yet</strong>
            <span>
              {photos.length
                ? "Pick the photo that shows the whole display, enter how wide it is, then choose Use as stage."
                : "Add a photo of the whole display first."}
            </span>
          </>
        )}
      </div>
      {pending > 0 && reference && (
        <div className="stage-map-callout" role="alert">
          <span>
            {pending === 1 ? "1 object was" : `${pending} objects were`} moved or added on the stage view.
            Shows use the old placement until you update the stage map.
          </span>
          {canEdit && (
            <button
              type="button"
              className="primary-button"
              disabled={busy}
              onClick={() => void run(() => applyStageMap(installationId, reference.revision))}
            >
              Update stage map
            </button>
          )}
        </div>
      )}
      {(stageMap?.projector_overlaps ?? []).map((overlap) => (
        <p
          className={`stage-map-overlap${overlap.fraction >= 0.95 ? " stage-map-overlap--full" : ""}`}
          key={overlap.resources.join("|")}
        >
          {overlap.fraction >= 0.95
            ? `${overlap.resources[0]} and ${overlap.resources[1]} cover the same area. If they should sit side by side, check their corners on the stage view; if stacking is intentional, set blending in the projector's overlap settings.`
            : `${overlap.resources[0]} and ${overlap.resources[1]} overlap ${Math.round(overlap.fraction * 100)}%. Blend the seam in the projector's overlap settings.`}
        </p>
      ))}
      {canEdit && photos.length > 0 && (
        <form
          className="stage-map-form"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <label>
            Stage view
            <select value={photoId} onChange={(event) => setPhotoId(event.target.value)} disabled={busy}>
              {photos.map((photo) => (
                <option key={photo.id} value={photo.id}>
                  {photo.description ?? photo.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Width of the whole photo view
            <input
              type="number"
              min="0.01"
              step="any"
              required
              value={width}
              disabled={busy}
              onChange={(event) => setWidth(event.target.value)}
            />
          </label>
          <label>
            Unit
            <select value={unit} disabled={busy} onChange={(event) => setUnit(event.target.value as StageUnit)}>
              {UNITS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            className="primary-button"
            disabled={busy || !photoId || !widthValid || (!settingsChanged && Boolean(reference?.applied_at))}
          >
            {busy ? "Saving…" : reference?.applied_at ? "Save stage settings" : "Use as stage"}
          </button>
          <small>
            An estimate is fine: the width sets the size of effects in real units. Photo
            calibration, wiring and projector pixels are not changed.
          </small>
        </form>
      )}
      {error && <ErrorState title="Stage map not saved" description={error} />}
      {reference && notPlaced.length > 0 && (
        <details className="stage-map-unplaced">
          <summary>Not drawn on the stage view ({notPlaced.length})</summary>
          <ul>
            {notPlaced.map((item: StageMapItem) => (
              <li key={`${item.kind}:${item.semantic_id}`}>
                <strong>{item.name}</strong> · {KIND_LABELS[item.kind] ?? item.kind}
                {item.status === "manual" ? " · keeps its manually entered stage position" : " · not on the stage"}
              </li>
            ))}
          </ul>
          <small>Open Layout on the stage view and draw these objects to include them in shared effects.</small>
        </details>
      )}
    </Panel>
  );
}
