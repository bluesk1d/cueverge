export function nightPreviewColor(color: readonly [number, number, number]): string | null {
  if (color.every((channel) => channel === 0)) return null;
  // Convert linear output levels to sRGB only for the photo visualization.
  const display = color.map((channel) => {
    const linear = channel / 255;
    return Math.round(255 * (linear <= 0.0031308
      ? 12.92 * linear
      : 1.055 * linear ** (1 / 2.4) - 0.055));
  });
  return `rgb(${display.join(",")})`;
}
