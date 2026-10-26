import { useState } from "react";
import { installationPhotoUrl } from "../api";
import type { AssetRecord } from "../api";
import {
  PHOTO_HEIGHT,
  PHOTO_WIDTH,
  SyncedReviewMedia,
  useFrameWidth,
  useProjectorSurfaces,
  WarpedSurface,
} from "./ProjectorSurfaces";
import type { ProjectorSurface } from "./ProjectorSurfaces";
import { formatTimecode, mediaOutputSourceMs } from "../pages/timelineUtils";
import type { SceneMediaOutputRef } from "../pages/timelineUtils";

interface SurfacePlacement extends ProjectorSurface {
  placementKey: string;
  output: SceneMediaOutputRef;
  asset: AssetRecord | undefined;
}

/** Whole-scene house view: each scene media output's review proxy warped onto its projector surface. */
export function SceneHouseView({
  installationId,
  outputs,
  assets,
  playheadMs,
  isPlaying,
}: {
  installationId: string;
  outputs: SceneMediaOutputRef[];
  assets: AssetRecord[];
  playheadMs: number;
  isPlaying: boolean;
}) {
  const { photos, geometry, surfaces, loaded, loadError } = useProjectorSurfaces(installationId);
  const [selectedPhotoId, setSelectedPhotoId] = useState<string | null>(null);
  const [frameRef, frameWidth] = useFrameWidth<HTMLDivElement>(`${loaded}:${photos.length}`);

  const placements: SurfacePlacement[] = surfaces.flatMap((surface) => {
    const output = outputs.find((candidate) => candidate.resource === surface.resource);
    if (!output) return [];
    return [{
      ...surface,
      placementKey: `${output.id}:${surface.photoId}:${surface.geometryId}`,
      output,
      asset: output.asset ? assets.find((candidate) => candidate.id === output.asset) : undefined,
    }];
  });

  const photoId =
    (selectedPhotoId && photos.some((photo) => photo.id === selectedPhotoId) ? selectedPhotoId : null) ??
    [...photos].sort(
      (left, right) =>
        placements.filter((item) => item.photoId === right.id).length -
        placements.filter((item) => item.photoId === left.id).length,
    )[0]?.id ??
    null;
  const photo = photos.find((candidate) => candidate.id === photoId);
  const visible = placements.filter((item) => item.photoId === photoId);
  const otherSurfaces = geometry.filter(
    (item) =>
      item.photo_id === photoId &&
      item.kind === "projector_surface" &&
      !item.hidden &&
      !visible.some((placement) => placement.geometryId === item.id),
  );
  const unplaced = outputs.filter(
    (output) => output.asset && !placements.some((placement) => placement.output.id === output.id),
  );

  if (!loaded) {
    return <div className="timeline-viewer__empty"><p>Loading installation photos…</p></div>;
  }
  if (loadError || !photo) {
    return (
      <div className="timeline-viewer__empty">
        <strong>House view</strong>
        <p>
          {loadError ??
            "Add an installation photo and draw projector surfaces in Installation to see scene media on the house."}
        </p>
      </div>
    );
  }
  return (
    <figure className="scene-house-view" aria-label="Scene house view">
      {photos.length > 1 && (
        <div className="scene-house-view__photos" role="group" aria-label="Installation photo">
          {photos.map((candidate) => (
            <button
              type="button"
              key={candidate.id}
              aria-pressed={candidate.id === photoId}
              onClick={() => setSelectedPhotoId(candidate.id)}
            >
              {candidate.name}
            </button>
          ))}
        </div>
      )}
      <div
        ref={frameRef}
        className="scene-house-view__frame"
        style={{ aspectRatio: `${PHOTO_WIDTH} / ${PHOTO_HEIGHT}` }}
      >
        <img alt={`Installation photo ${photo.name}`} src={installationPhotoUrl(photo.id, photo.revision)} />
        {frameWidth > 0 &&
          visible.map((placement) => {
            const sourceMs = mediaOutputSourceMs(
              playheadMs,
              placement.output.startDelayMs,
              assetDurationMs(placement.asset),
              placement.output.endBehavior,
              placement.output.repeatCount,
            );
            const label = `${placement.asset?.name ?? placement.output.id} on ${placement.surfaceName}`;
            return (
              <WarpedSurface
                key={placement.placementKey}
                surface={placement}
                frameWidth={frameWidth}
                label={label}
                hidden={sourceMs === null}
              >
                <SyncedReviewMedia
                  asset={placement.asset}
                  sourceMs={sourceMs}
                  playing={isPlaying}
                  label={label}
                />
              </WarpedSurface>
            );
          })}
        <svg viewBox={`0 0 ${PHOTO_WIDTH} ${PHOTO_HEIGHT}`} preserveAspectRatio="none" aria-hidden="true">
          {visible.map((placement) => (
            <polygon
              key={placement.placementKey}
              className="scene-house-view__outline"
              points={placement.corners.map((point) => `${point.x * PHOTO_WIDTH},${point.y * PHOTO_HEIGHT}`).join(" ")}
            />
          ))}
          {otherSurfaces.map((item) => (
            <polygon
              key={item.id}
              className="scene-house-view__outline scene-house-view__outline--idle"
              points={item.points.map((point) => `${point.x * PHOTO_WIDTH},${point.y * PHOTO_HEIGHT}`).join(" ")}
            />
          ))}
        </svg>
      </div>
      <figcaption>
        {visible.length
          ? visible.map((placement) => {
              const durationMs = assetDurationMs(placement.asset);
              const sourceMs = mediaOutputSourceMs(
                playheadMs,
                placement.output.startDelayMs,
                durationMs,
                placement.output.endBehavior,
                placement.output.repeatCount,
              );
              return (
                <span key={placement.placementKey}>
                  {placement.surfaceName} ← {placement.asset?.name ?? "No media"}
                  {sourceMs === null ? " · not playing yet" : ` · source ${formatTimecode(sourceMs)}`}
                </span>
              );
            })
          : <span>No scene media is assigned to a projector surface on this photo.</span>}
        {visible.map((placement) => {
          const warning = playbackAspectWarning(placement);
          return warning ? <small key={`${placement.placementKey}:aspect`} role="note">{warning}</small> : null;
        })}
        {unplaced.length > 0 && (
          <small>
            Not shown on the house: {unplaced.map((output) => output.resource ?? output.id).join(", ")} (no
            projector surface with a photo for that output).
          </small>
        )}
        <small>
          Approximate preview: review proxies warped onto the drawn projector surfaces. Bakes are the
          exact output.
        </small>
      </figcaption>
    </figure>
  );
}

function playbackAspectWarning(placement: SurfacePlacement): string | null {
  const width = Number(placement.asset?.metadata?.width);
  const height = Number(placement.asset?.metadata?.height);
  if (!(width > 0 && height > 0)) return null;
  const rotated = placement.orientationDegrees === 90 || placement.orientationDegrees === 270;
  const outputWidth = rotated ? placement.nativeHeight : placement.nativeWidth;
  const outputHeight = rotated ? placement.nativeWidth : placement.nativeHeight;
  const assetAspect = width / height;
  const outputAspect = outputWidth / outputHeight;
  if (Math.abs(assetAspect - outputAspect) / outputAspect <= 0.01) return null;
  return `${placement.asset?.name} is ${width}×${height}, but the ${placement.surfaceName} projector outputs ${outputWidth}×${outputHeight}${
    rotated ? ` (rotated ${placement.orientationDegrees}°)` : ""
  }. The preview fits it to the drawn surface; played directly it will be ${
    assetAspect < outputAspect ? "pillarboxed" : "letterboxed"
  } on the projector. Match the projector's native size and orientation in Installation, or use a projector composition.`;
}

function assetDurationMs(asset: AssetRecord | undefined): number {
  const value = asset?.metadata?.duration_ms;
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}
