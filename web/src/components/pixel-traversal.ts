import type { InstallationGeometry } from "../api";

export function photoPixelPoints(
  geometry: InstallationGeometry,
): { outputIndex: number; x: number; y: number }[] {
  if (
    geometry.kind !== "pixel_segment" ||
    geometry.start_index === null ||
    geometry.pixel_count === null ||
    geometry.points.length === 0
  ) {
    return [];
  }
  const ordered =
    geometry.direction === "forward" ? geometry.points : [...geometry.points].reverse();
  const lengths = ordered.slice(1).map((point, index) =>
    Math.hypot(point.x - ordered[index].x, point.y - ordered[index].y),
  );
  const total = lengths.reduce((sum, value) => sum + value, 0);
  const closed =
    ordered.length > 1 &&
    ordered[0].x === ordered.at(-1)?.x && ordered[0].y === ordered.at(-1)?.y;
  return Array.from({ length: geometry.pixel_count }, (_, offset) => {
    const denominator = closed
      ? geometry.pixel_count!
      : Math.max(geometry.pixel_count! - 1, 1);
    const target = total * (offset / denominator);
    let traversed = 0;
    for (let index = 0; index < lengths.length; index += 1) {
      const length = lengths[index];
      if (traversed + length >= target || index === lengths.length - 1) {
        const ratio = length ? (target - traversed) / length : 0;
        return {
          outputIndex: geometry.start_index! + offset,
          x: ordered[index].x + (ordered[index + 1].x - ordered[index].x) * ratio,
          y: ordered[index].y + (ordered[index + 1].y - ordered[index].y) * ratio,
        };
      }
      traversed += length;
    }
    return { outputIndex: geometry.start_index! + offset, ...ordered[0] };
  });
}

export function outputTraversal(
  geometry: InstallationGeometry[],
  resources: Record<string, string>,
  resource: string,
  photoId: string,
  pixelCount: number,
) {
  const linked = new Map<string, InstallationGeometry[]>();
  for (const item of geometry) {
    if (item.kind !== "pixel_segment" || resources[item.id] !== resource) {
      continue;
    }
    const views = linked.get(item.semantic_id) ?? [];
    views.push(item);
    linked.set(item.semantic_id, views);
  }
  const diagnostics: string[] = [];
  for (const views of linked.values()) {
    if (views.some((view) =>
      view.start_index !== views[0].start_index ||
      view.pixel_count !== views[0].pixel_count ||
      view.direction !== views[0].direction,
    )) {
      diagnostics.push(`${views[0].name}: linked photos disagree on pixel range or direction.`);
    }
  }
  const segments = [...linked.values()]
    .map((views) => views.find((item) => item.photo_id === photoId) ?? views[0])
    .sort((left, right) =>
      (left.start_index ?? Infinity) - (right.start_index ?? Infinity) ||
      left.semantic_id.localeCompare(right.semantic_id),
    )
    .flatMap((item, index) => {
      if (
        item.start_index === null || item.pixel_count === null ||
        !Number.isSafeInteger(item.start_index) || item.start_index < 0 ||
        !Number.isSafeInteger(item.pixel_count) || item.pixel_count < 1 ||
        item.start_index + item.pixel_count > pixelCount ||
        item.points.length === 0
      ) {
        diagnostics.push(`${item.name}: invalid or missing pixel range/photo points.`);
        return [];
      }
      return [{
        geometry: item,
        order: index + 1,
        color: `hsl(${Math.round((index * 137.508 + 190) % 360)} 85% 65%)`,
        start: item.start_index,
        end: item.start_index + item.pixel_count,
        pixels: photoPixelPoints(item),
      }];
    });
  let end = 0;
  for (const segment of segments) {
    if (segment.start > end) {
      diagnostics.push(`Unmapped output indices ${end}-${segment.start - 1}.`);
    } else if (segment.start < end) {
      diagnostics.push(`Overlapping pixel allocation at ${segment.geometry.name}; multiple locations may pulse.`);
    }
    end = Math.max(end, segment.end);
  }
  if (end < pixelCount) {
    diagnostics.push(`Unmapped output indices ${end}-${pixelCount - 1}.`);
  }
  return { segments, diagnostics };
}
