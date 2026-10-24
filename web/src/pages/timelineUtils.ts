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
