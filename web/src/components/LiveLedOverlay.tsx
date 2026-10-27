import { useEffect, useRef, useState } from "react";

import { getInstallationGeometry, getLightingSurface, InstallationGeometry, OutputMonitor } from "../api";
import { nightPreviewColor } from "./led-preview";
import { geometryForBackground, photoPixelPoints } from "./pixel-traversal";

// Saved geometry is normalized to the editor's 1000×600 frame with the photo letterboxed ("meet") inside.
const FRAME_WIDTH = 1000;
const FRAME_HEIGHT = 600;

export interface LedPhotoMapping {
  geometry: InstallationGeometry[];
  resources: Record<string, string>;
  loaded: boolean;
  error: string | null;
}

/** Loads saved LED segment placements and their output resources while enabled. */
export function useLedPhotoMapping(installationId: string | undefined, enabled: boolean, refreshKey = 0): LedPhotoMapping {
  const [mapping, setMapping] = useState<LedPhotoMapping>({ geometry: [], resources: {}, loaded: false, error: null });
  useEffect(() => {
    if (!enabled || !installationId) return;
    let current = true;
    getInstallationGeometry(installationId).then(async (shapes) => {
      const segments = shapes.filter((value) => value.kind === "pixel_segment");
      const surfaces = await Promise.allSettled(segments.map((value) => getLightingSurface(value.id)));
      if (!current) return;
      setMapping({
        geometry: shapes,
        resources: Object.fromEntries(surfaces.flatMap((value, index) =>
          value.status === "fulfilled" ? [[segments[index].id, value.value.resource]] : [])),
        loaded: true,
        error: surfaces.some((value) => value.status === "rejected")
          ? "Some saved LED mappings could not be read." : null,
      });
    }).catch(() => {
      if (current) setMapping({ geometry: [], resources: {}, loaded: true, error: "Saved LED mappings are unavailable." });
    });
    return () => { current = false; };
  }, [installationId, enabled, refreshKey]);
  return mapping;
}

export interface MappedLed { x: number; y: number; color: string }

/** Places sampled monitor colors on a photo using saved segment geometry; mismatched allocations are withheld. */
export function mapMonitorLeds(
  mapping: LedPhotoMapping, photoId: string, outputs: OutputMonitor["outputs"],
): MappedLed[] {
  const byResource = new Map(outputs.map((value) => [value.resource, value]));
  return geometryForBackground(mapping.geometry, photoId).flatMap((segment) => {
    const output = byResource.get(mapping.resources[segment.id]);
    if (!output || (segment.start_index ?? 0) + (segment.pixel_count ?? 0) > output.pixel_count) return [];
    return photoPixelPoints(segment).flatMap((pixel) => {
      const index = pixel.outputIndex - output.pixel_offset;
      const color = index >= 0 && index < output.colors.length ? nightPreviewColor(output.colors[index]) : null;
      return color === null ? [] : [{ x: pixel.x, y: pixel.y, color }];
    });
  });
}

/** viewBox (in frame units) showing exactly the part of the frame visible in an object-fit: cover box. */
export function coverViewBox(
  natural: { width: number; height: number }, aspect: number,
): [number, number, number, number] | null {
  const { width, height } = natural;
  if (!(width > 0 && height > 0 && aspect > 0)) return null;
  const scale = Math.min(FRAME_WIDTH / width, FRAME_HEIGHT / height);
  const offsetX = (FRAME_WIDTH - width * scale) / 2;
  const offsetY = (FRAME_HEIGHT - height * scale) / 2;
  const visibleWidth = width / height > aspect ? height * aspect : width;
  const visibleHeight = width / height > aspect ? height : width / aspect;
  return [
    offsetX + ((width - visibleWidth) / 2) * scale,
    offsetY + ((height - visibleHeight) / 2) * scale,
    visibleWidth * scale,
    visibleHeight * scale,
  ];
}

/** Last-sent LED colors drawn over a cover-fitted installation photo. */
export function LiveLedOverlay({ leds, natural }: {
  leds: MappedLed[]; natural: { width: number; height: number } | null;
}) {
  const ref = useRef<SVGSVGElement>(null);
  const [aspect, setAspect] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => {
      const box = element.getBoundingClientRect();
      setAspect(box.height > 0 ? box.width / box.height : 0);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const viewBox = natural ? coverViewBox(natural, aspect) : null;
  return (
    <svg ref={ref} className="overview-hero__leds" role="img" aria-label="Live LED colors on installation photo"
      viewBox={viewBox?.join(" ") ?? `0 0 ${FRAME_WIDTH} ${FRAME_HEIGHT}`} preserveAspectRatio="none">
      {viewBox && <g className="scene-led-overlay">
        {leds.map((led, index) => (
          <circle className="scene-led" key={index} cx={led.x * FRAME_WIDTH} cy={led.y * FRAME_HEIGHT} r="3"
            fill={led.color} style={{ color: led.color }} aria-hidden="true" />
        ))}
      </g>}
    </svg>
  );
}
