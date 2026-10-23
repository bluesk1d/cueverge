import type { InstallationGeometry } from "../api";

export function mapperDiagnostics(
  geometry: InstallationGeometry[], resources: Record<string, string>, photoId: string | undefined,
): { conflicts: string[]; observations: string[] } {
  const conflicts: string[] = [];
  const observations: string[] = [];
  const physical = geometry.filter((item, index, all) =>
    item.kind === "pixel_segment" && item.start_index !== null && item.pixel_count !== null &&
    Boolean(resources[item.id]) &&
    all.findIndex((candidate) => candidate.semantic_id === item.semantic_id) === index,
  );
  for (const resource of new Set(physical.map((item) => resources[item.id]))) {
    const segments = physical.filter((item) => resources[item.id] === resource)
      .sort((left, right) => left.start_index! - right.start_index!);
    let end = 0;
    segments.forEach((current, index) => {
      for (const previous of segments.slice(0, index)) {
        if (current.start_index! < previous.start_index! + previous.pixel_count!) {
          conflicts.push(`Pixel index overlap on ${resource}: ${previous.name} and ${current.name}.`);
        }
      }
      if (index > 0 && current.start_index! > end) observations.push(`Unmapped index range on ${resource} before ${current.name}.`);
      end = Math.max(end, current.start_index! + current.pixel_count!);
    });
  }
  const visible = geometry.filter((item) => item.photo_id === photoId && !item.hidden);
  const bounds = (item: InstallationGeometry) => ({
    x0: Math.min(...item.points.map((point) => point.x)),
    x1: Math.max(...item.points.map((point) => point.x)),
    y0: Math.min(...item.points.map((point) => point.y)),
    y1: Math.max(...item.points.map((point) => point.y)),
  });
  visible.forEach((left, index) => {
    if (left.points.length < 2) return;
    for (const right of visible.slice(index + 1)) {
      if (right.points.length < 2) continue;
      const a = bounds(left), b = bounds(right);
      if (a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0) {
        const top = left.z_index >= right.z_index ? left : right;
        observations.push(`Photo bounds overlap: ${top.name} is drawn on top. This is not a physical wiring error.`);
      }
    }
  });
  return { conflicts: [...new Set(conflicts)], observations: [...new Set(observations)] };
}
