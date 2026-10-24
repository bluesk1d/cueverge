/** Keep keyframes inside [start, end]; trimmed-off keys collapse onto the nearest edge. */
export function trimKeyframes<T extends { time_ms: number }>(
  keyframes: T[],
  start: number,
  end: number,
): T[] {
  const inside = keyframes.filter((keyframe) => keyframe.time_ms >= start && keyframe.time_ms <= end);
  const before = keyframes.filter((keyframe) => keyframe.time_ms < start);
  const after = keyframes.filter((keyframe) => keyframe.time_ms > end);
  const result = [...inside];
  if (before.length && !inside.some((keyframe) => keyframe.time_ms === start)) {
    const nearest = before.reduce((best, keyframe) => (keyframe.time_ms > best.time_ms ? keyframe : best));
    result.push({ ...nearest, time_ms: start });
  }
  if (after.length && !inside.some((keyframe) => keyframe.time_ms === end)) {
    const nearest = after.reduce((best, keyframe) => (keyframe.time_ms < best.time_ms ? keyframe : best));
    result.push({ ...nearest, time_ms: end });
  }
  return result.sort((left, right) => left.time_ms - right.time_ms);
}

/** Assign overlapping layers on one track to separate rows so none hide each other. */
export function stackLayerRows(
  layers: { id: string; start_ms: number; duration_ms: number; z_index: number }[],
): Map<string, number> {
  const rows = new Map<string, number>();
  const rowEnds: number[] = [];
  for (const layer of [...layers].sort(
    (left, right) =>
      left.start_ms - right.start_ms || left.z_index - right.z_index || left.id.localeCompare(right.id),
  )) {
    let row = rowEnds.findIndex((end) => end <= layer.start_ms);
    if (row < 0) {
      row = rowEnds.length;
      rowEnds.push(layer.start_ms + layer.duration_ms);
    } else {
      rowEnds[row] = layer.start_ms + layer.duration_ms;
    }
    rows.set(layer.id, row);
  }
  return rows;
}

export function formatTimecode(ms: number): string {
  const safe = Math.max(0, Math.round(ms));
  const minutes = Math.floor(safe / 60000);
  const seconds = Math.floor(safe / 1000) % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(safe % 1000).padStart(3, "0")}`;
}

export type SceneMediaEndBehavior = "stop" | "hold_last_frame" | "loop";

export interface SceneMediaOutputRef {
  id: string;
  resource: string | null;
  asset: string | null;
  startDelayMs: number;
  endBehavior: SceneMediaEndBehavior;
}

/** Normalize a scene's single-output or multi-output `media` field. */
export function sceneMediaOutputs(media: unknown): SceneMediaOutputRef[] {
  const entries = Array.isArray(media) ? media : media && typeof media === "object" ? [media] : [];
  return entries.flatMap((entry, index) => {
    if (!entry || typeof entry !== "object") return [];
    const record = entry as Record<string, unknown>;
    const endBehavior = record.end_behavior;
    return [{
      id: typeof record.id === "string" ? record.id : Array.isArray(media) ? `output-${index + 1}` : "media",
      resource: typeof record.resource === "string" ? record.resource : null,
      asset: typeof record.asset === "string" ? record.asset : null,
      startDelayMs: typeof record.start_delay_ms === "number" ? Math.max(0, record.start_delay_ms) : 0,
      endBehavior:
        endBehavior === "stop" || endBehavior === "loop" ? endBehavior : "hold_last_frame",
    }];
  });
}

/** Source position (ms) a media output shows at the scene playhead, or null when nothing is on screen. */
export function mediaOutputSourceMs(
  playheadMs: number,
  startDelayMs: number,
  durationMs: number,
  endBehavior: SceneMediaEndBehavior,
): number | null {
  const local = playheadMs - startDelayMs;
  if (local < 0) return null;
  if (!(durationMs > 0) || local < durationMs) return local;
  if (endBehavior === "loop") return local % durationMs;
  if (endBehavior === "hold_last_frame") return Math.max(0, durationMs - 1);
  return null;
}

/** Solve the 3×3 homography (row-major, h8 = 1) mapping a width×height raster's corners to `corners`. */
export function cornerHomography(
  width: number,
  height: number,
  corners: { x: number; y: number }[],
): number[] | null {
  if (corners.length !== 4 || !(width > 0) || !(height > 0)) return null;
  const sources: [number, number][] = [[0, 0], [width, 0], [width, height], [0, height]];
  const rows: number[][] = [];
  sources.forEach(([x, y], index) => {
    const { x: u, y: v } = corners[index];
    rows.push([x, y, 1, 0, 0, 0, -u * x, -u * y, u]);
    rows.push([0, 0, 0, x, y, 1, -v * x, -v * y, v]);
  });
  for (let pivot = 0; pivot < 8; pivot += 1) {
    let best = pivot;
    for (let row = pivot + 1; row < 8; row += 1) {
      if (Math.abs(rows[row][pivot]) > Math.abs(rows[best][pivot])) best = row;
    }
    if (Math.abs(rows[best][pivot]) < 1e-12) return null;
    [rows[pivot], rows[best]] = [rows[best], rows[pivot]];
    const divisor = rows[pivot][pivot];
    rows[pivot] = rows[pivot].map((value) => value / divisor);
    for (let row = 0; row < 8; row += 1) {
      if (row === pivot) continue;
      const factor = rows[row][pivot];
      rows[row] = rows[row].map((value, column) => value - factor * rows[pivot][column]);
    }
  }
  return [...rows.map((row) => row[8]), 1];
}

/** CSS matrix3d placing a raster (pixel coords) through a normalized homography into a scaleX×scaleY box. */
export function homographyMatrix3d(h: number[], scaleX: number, scaleY: number): string {
  const [h0, h1, h2, h3, h4, h5, h6, h7, h8] = h;
  return `matrix3d(${[
    h0 * scaleX, h3 * scaleY, 0, h6,
    h1 * scaleX, h4 * scaleY, 0, h7,
    0, 0, 1, 0,
    h2 * scaleX, h5 * scaleY, 0, h8,
  ].join(",")})`;
}

/** Map a raster pixel through a row-major homography. */
export function applyHomography(h: number[], x: number, y: number): { x: number; y: number } {
  const w = h[6] * x + h[7] * y + h[8];
  return { x: (h[0] * x + h[1] * y + h[2]) / w, y: (h[3] * x + h[4] * y + h[5]) / w };
}
