import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  assetArtifactUrl,
  getInstallationGeometry,
  getInstallationPhotos,
  getProjectorMapping,
} from "../api";
import type {
  AssetRecord,
  InstallationGeometry,
  InstallationPhoto,
  ProjectorMapping,
} from "../api";
import { hasAssetArtifact } from "./AssetPreview";
import { cornerHomography, homographyMatrix3d } from "../pages/timelineUtils";

/** Installation photo frame: geometry points are normalized to this letterboxed frame. */
export const PHOTO_WIDTH = 1000;
export const PHOTO_HEIGHT = 600;
const SURFACE_RASTER_WIDTH = 1000;

/** A projector surface as drawn on one installation photo, ready to corner-pin content onto. */
export interface ProjectorSurface {
  key: string;
  geometryId: string;
  resource: string;
  surfaceName: string;
  photoId: string;
  corners: { x: number; y: number }[];
  /** Surface-shaped raster the content is fitted into before the corner warp. */
  rasterWidth: number;
  rasterHeight: number;
  homography: number[];
  nativeWidth: number;
  nativeHeight: number;
  orientationDegrees: ProjectorMapping["orientation_degrees"];
  fitMode: ProjectorMapping["fit_mode"];
}

export interface ProjectorSurfaceData {
  photos: InstallationPhoto[];
  geometry: InstallationGeometry[];
  surfaces: ProjectorSurface[];
  loaded: boolean;
  loadError: string | null;
}

/** Loads installation photos, geometry and projector mappings for corner-pinned previews. */
export function useProjectorSurfaces(installationId: string | null | undefined): ProjectorSurfaceData {
  const [data, setData] = useState<ProjectorSurfaceData>({
    photos: [], geometry: [], surfaces: [], loaded: false, loadError: null,
  });
  useEffect(() => {
    let active = true;
    setData({ photos: [], geometry: [], surfaces: [], loaded: false, loadError: null });
    if (!installationId) {
      setData((current) => ({ ...current, loaded: true }));
      return;
    }
    Promise.all([getInstallationPhotos(installationId), getInstallationGeometry(installationId)])
      .then(async ([photos, geometry]) => {
        const drawn = geometry.filter((item) => item.kind === "projector_surface");
        const results = await Promise.allSettled(drawn.map((item) => getProjectorMapping(item.id)));
        if (!active) return;
        const surfaces: ProjectorSurface[] = [];
        const seen = new Set<string>();
        results.forEach((result, index) => {
          if (result.status !== "fulfilled") return;
          const mapping = result.value;
          const surface = drawn[index];
          const views = mapping.photo_views.length
            ? mapping.photo_views
            : [{ geometry_id: surface.id, photo_id: surface.photo_id, points: surface.points, homography: null }];
          for (const view of views) {
            if (!view.photo_id || view.points.length !== 4) continue;
            const key = `${mapping.resource}:${view.photo_id}:${view.geometry_id}`;
            if (seen.has(key)) continue;
            // The drawn surface is the content area: fit content to its apparent shape, then corner-pin it.
            const rasterWidth = SURFACE_RASTER_WIDTH;
            const rasterHeight = SURFACE_RASTER_WIDTH / surfaceAspect(view.points);
            const homography = cornerHomography(rasterWidth, rasterHeight, view.points);
            if (!homography) continue;
            seen.add(key);
            surfaces.push({
              key,
              geometryId: view.geometry_id,
              resource: mapping.resource,
              surfaceName: geometry.find((item) => item.id === view.geometry_id)?.name ?? surface.name,
              photoId: view.photo_id,
              corners: view.points,
              rasterWidth,
              rasterHeight,
              homography,
              nativeWidth: mapping.native_width,
              nativeHeight: mapping.native_height,
              orientationDegrees: mapping.orientation_degrees,
              fitMode: mapping.fit_mode,
            });
          }
        });
        setData({ photos, geometry, surfaces, loaded: true, loadError: null });
      })
      .catch((error: unknown) => {
        if (!active) return;
        setData({
          photos: [], geometry: [], surfaces: [], loaded: true,
          loadError: error instanceof Error ? error.message : "Projector surfaces failed to load.",
        });
      });
    return () => {
      active = false;
    };
  }, [installationId]);
  return data;
}

/** Apparent width/height of a drawn quad on the 1000×600 photo frame (mean of opposite edges). */
export function surfaceAspect(points: { x: number; y: number }[]): number {
  const length = (a: { x: number; y: number }, b: { x: number; y: number }) =>
    Math.hypot((b.x - a.x) * PHOTO_WIDTH, (b.y - a.y) * PHOTO_HEIGHT);
  const [topLeft, topRight, bottomRight, bottomLeft] = points;
  const width = (length(topLeft, topRight) + length(bottomLeft, bottomRight)) / 2;
  const height = (length(topLeft, bottomLeft) + length(topRight, bottomRight)) / 2;
  return width > 0 && height > 0 ? width / height : 16 / 9;
}

/** Tracks the rendered width of a photo frame so surfaces can be warped in screen pixels. */
export function useFrameWidth<T extends HTMLElement | SVGSVGElement>(dependency: unknown) {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    setWidth(element.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, [dependency]);
  return [ref, width] as const;
}

/** Content fitted to a projector surface (using its content fit) and corner-pinned onto the photo frame. */
export function WarpedSurface({
  surface,
  frameWidth,
  label,
  hidden = false,
  children,
}: {
  surface: ProjectorSurface;
  frameWidth: number;
  label: string;
  hidden?: boolean;
  children: ReactNode;
}) {
  const frameHeight = (frameWidth * PHOTO_HEIGHT) / PHOTO_WIDTH;
  return (
    <div
      className={`scene-house-view__surface scene-house-view__surface--${surface.fitMode}`}
      aria-label={label}
      style={{
        width: `${surface.rasterWidth}px`,
        height: `${surface.rasterHeight}px`,
        transform: homographyMatrix3d(surface.homography, frameWidth, frameHeight),
        visibility: hidden ? "hidden" : "visible",
      }}
    >
      {children}
    </div>
  );
}

/** An asset's review proxy kept in step with a source time; video plays natively while previewing. */
export function SyncedReviewMedia({
  asset,
  sourceMs,
  playing,
  label,
}: {
  asset: AssetRecord | undefined;
  sourceMs: number | null;
  playing: boolean;
  label: string;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const reviewUrl =
    asset && hasAssetArtifact(asset, "review")
      ? assetArtifactUrl(asset.id, "review", asset.current_revision)
      : null;
  const isVideo = Boolean(asset && (asset.media_type?.startsWith("video/") || asset.media_type === "image/gif"));
  const sourceSeconds = (sourceMs ?? 0) / 1000;
  const active = playing && sourceMs !== null;

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !isVideo) return;
    if (Math.abs(video.currentTime - sourceSeconds) > (active ? 0.35 : 0.04)) {
      video.currentTime = sourceSeconds;
    }
    if (active && video.paused) {
      void video.play().catch(() => undefined);
    } else if (!active && !video.paused) {
      video.pause();
    }
  }, [isVideo, active, sourceSeconds, reviewUrl]);

  if (!reviewUrl) {
    return <span>{asset ? `${asset.name}: no review proxy yet` : "No media assigned"}</span>;
  }
  return isVideo ? (
    <video key={reviewUrl} ref={videoRef} aria-label={label} muted playsInline preload="auto" src={reviewUrl} />
  ) : (
    <img alt={label} src={reviewUrl} />
  );
}
