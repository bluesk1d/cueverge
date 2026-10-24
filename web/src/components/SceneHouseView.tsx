import { useEffect, useMemo, useRef, useState } from "react";
import {
  assetArtifactUrl,
  getInstallationGeometry,
  getInstallationPhotos,
  getProjectorMapping,
  installationPhotoUrl,
} from "../api";
import type {
  AssetRecord,
  InstallationGeometry,
  InstallationPhoto,
  ProjectorMapping,
} from "../api";
import { hasAssetArtifact } from "./AssetPreview";
import {
  applyHomography,
  cornerHomography,
  formatTimecode,
  homographyMatrix3d,
  mediaOutputSourceMs,
} from "../pages/timelineUtils";
import type { SceneMediaOutputRef } from "../pages/timelineUtils";

const PHOTO_WIDTH = 1000;
const PHOTO_HEIGHT = 600;

interface SurfacePlacement {
  key: string;
  output: SceneMediaOutputRef;
  asset: AssetRecord | undefined;
  surfaceName: string;
  photoId: string;
  homography: number[];
  nativeWidth: number;
  nativeHeight: number;
  fitMode: ProjectorMapping["fit_mode"];
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
  const [photos, setPhotos] = useState<InstallationPhoto[]>([]);
  const [geometry, setGeometry] = useState<InstallationGeometry[]>([]);
  const [mappings, setMappings] = useState<Map<string, ProjectorMapping>>(new Map());
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [selectedPhotoId, setSelectedPhotoId] = useState<string | null>(null);
  const [frameWidth, setFrameWidth] = useState(0);
  const frameRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;
    setLoaded(false);
    Promise.all([getInstallationPhotos(installationId), getInstallationGeometry(installationId)])
      .then(async ([photoResult, geometryResult]) => {
        const surfaces = geometryResult.filter((item) => item.kind === "projector_surface");
        const results = await Promise.allSettled(surfaces.map((item) => getProjectorMapping(item.id)));
        if (!active) return;
        const byGeometry = new Map<string, ProjectorMapping>();
        results.forEach((result, index) => {
          if (result.status === "fulfilled") byGeometry.set(surfaces[index].id, result.value);
        });
        setPhotos(photoResult);
        setGeometry(geometryResult);
        setMappings(byGeometry);
        setLoadError(null);
        setLoaded(true);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setLoadError(error instanceof Error ? error.message : "House view failed to load.");
        setLoaded(true);
      });
    return () => {
      active = false;
    };
  }, [installationId]);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => setFrameWidth(entry.contentRect.width));
    observer.observe(frame);
    setFrameWidth(frame.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, [loaded, photos.length]);

  const placements = useMemo(() => {
    const result: SurfacePlacement[] = [];
    const seen = new Set<string>();
    for (const [geometryId, mapping] of mappings) {
      const output = outputs.find((candidate) => candidate.resource === mapping.resource);
      if (!output) continue;
      const surface = geometry.find((item) => item.id === geometryId);
      const views = mapping.photo_views.length
        ? mapping.photo_views
        : surface
          ? [{ geometry_id: surface.id, photo_id: surface.photo_id, points: surface.points, homography: null }]
          : [];
      for (const view of views) {
        if (!view.photo_id) continue;
        const key = `${output.id}:${view.photo_id}:${view.geometry_id}`;
        if (seen.has(key)) continue;
        const homography =
          view.homography ?? cornerHomography(mapping.native_width, mapping.native_height, view.points);
        if (!homography) continue;
        seen.add(key);
        result.push({
          key,
          output,
          asset: output.asset ? assets.find((candidate) => candidate.id === output.asset) : undefined,
          surfaceName:
            geometry.find((item) => item.id === view.geometry_id)?.name ?? surface?.name ?? mapping.resource,
          photoId: view.photo_id,
          homography,
          nativeWidth: mapping.native_width,
          nativeHeight: mapping.native_height,
          fitMode: mapping.fit_mode,
        });
      }
    }
    return result;
  }, [assets, geometry, mappings, outputs]);

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
      !visible.some((placement) => placement.key.endsWith(`:${item.id}`)),
  );
  const unplaced = outputs.filter(
    (output) => output.asset && !placements.some((placement) => placement.output.id === output.id),
  );
  const frameHeight = (frameWidth * PHOTO_HEIGHT) / PHOTO_WIDTH;

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
          visible.map((placement) => (
            <HouseSurfaceMedia
              key={placement.key}
              placement={placement}
              transform={homographyMatrix3d(placement.homography, frameWidth, frameHeight)}
              playheadMs={playheadMs}
              isPlaying={isPlaying}
            />
          ))}
        <svg viewBox={`0 0 ${PHOTO_WIDTH} ${PHOTO_HEIGHT}`} preserveAspectRatio="none" aria-hidden="true">
          {visible.map((placement) => {
            const corners = [
              [0, 0],
              [placement.nativeWidth, 0],
              [placement.nativeWidth, placement.nativeHeight],
              [0, placement.nativeHeight],
            ].map(([x, y]) => applyHomography(placement.homography, x, y));
            return (
              <polygon
                key={placement.key}
                className="scene-house-view__outline"
                points={corners.map((point) => `${point.x * PHOTO_WIDTH},${point.y * PHOTO_HEIGHT}`).join(" ")}
              />
            );
          })}
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
              );
              return (
                <span key={placement.key}>
                  {placement.surfaceName} ← {placement.asset?.name ?? "No media"}
                  {sourceMs === null ? " · not playing yet" : ` · source ${formatTimecode(sourceMs)}`}
                </span>
              );
            })
          : <span>No scene media is assigned to a projector surface on this photo.</span>}
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

function assetDurationMs(asset: AssetRecord | undefined): number {
  const value = asset?.metadata?.duration_ms;
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function HouseSurfaceMedia({
  placement,
  transform,
  playheadMs,
  isPlaying,
}: {
  placement: SurfacePlacement;
  transform: string;
  playheadMs: number;
  isPlaying: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const { asset, output } = placement;
  const reviewUrl = asset && hasAssetArtifact(asset, "review") ? assetArtifactUrl(asset.id, "review") : null;
  const isVideo = Boolean(asset && (asset.media_type?.startsWith("video/") || asset.media_type === "image/gif"));
  const sourceMs = mediaOutputSourceMs(
    playheadMs,
    output.startDelayMs,
    assetDurationMs(asset),
    output.endBehavior,
  );
  const sourceSeconds = (sourceMs ?? 0) / 1000;
  const playing = isPlaying && sourceMs !== null;

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !isVideo) return;
    if (Math.abs(video.currentTime - sourceSeconds) > (playing ? 0.35 : 0.04)) {
      video.currentTime = sourceSeconds;
    }
    if (playing && video.paused) {
      void video.play().catch(() => undefined);
    } else if (!playing && !video.paused) {
      video.pause();
    }
  }, [isVideo, playing, sourceSeconds, reviewUrl]);

  const label = `${asset?.name ?? output.id} on ${placement.surfaceName}`;
  return (
    <div
      className={`scene-house-view__surface scene-house-view__surface--${placement.fitMode}`}
      aria-label={label}
      style={{
        width: `${placement.nativeWidth}px`,
        height: `${placement.nativeHeight}px`,
        transform,
        visibility: sourceMs === null ? "hidden" : "visible",
      }}
    >
      {!reviewUrl ? (
        <span>{asset ? `${asset.name}: no review proxy yet` : "No media assigned"}</span>
      ) : isVideo ? (
        <video
          key={reviewUrl}
          ref={videoRef}
          aria-label={label}
          muted
          playsInline
          preload="auto"
          src={reviewUrl}
        />
      ) : (
        <img alt={label} src={reviewUrl} />
      )}
    </div>
  );
}
