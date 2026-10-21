import { useId } from "react";

type Point = { x: number; y: number };

export function SurfaceMediaOverlay({
  png,
  corners,
}: {
  png: string;
  corners: Point[];
}) {
  const id = useId();
  if (corners.length < 4) return null;
  const [topLeft, topRight, bottomRight, bottomLeft] = corners;
  const project = ([u, v]: [number, number]): [number, number] => [
    ((1 - u) * (1 - v) * topLeft.x + u * (1 - v) * topRight.x +
      u * v * bottomRight.x + (1 - u) * v * bottomLeft.x) * 1000,
    ((1 - u) * (1 - v) * topLeft.y + u * (1 - v) * topRight.y +
      u * v * bottomRight.y + (1 - u) * v * bottomLeft.y) * 600,
  ];
  const triangles: [number, number][][] = [];
  for (let row = 0; row < 8; row += 1) {
    for (let column = 0; column < 8; column += 1) {
      const u = column / 8, v = row / 8;
      triangles.push(
        [[u, v], [u + 1 / 8, v], [u + 1 / 8, v + 1 / 8]],
        [[u, v], [u + 1 / 8, v + 1 / 8], [u, v + 1 / 8]],
      );
    }
  }
  return (
    <g opacity="0.4" aria-label="Spatial media overlay on installation photo" pointerEvents="none">
      <defs>
        <image
          id={`${id}-source`} href={`data:image/png;base64,${png}`}
          width="1" height="1" preserveAspectRatio="none"
        />
      </defs>
      {triangles.map(([p0, p1, p2], index) => {
        const [q0, q1, q2] = [project(p0), project(p1), project(p2)];
        const du1 = p1[0] - p0[0], dv1 = p1[1] - p0[1];
        const du2 = p2[0] - p0[0], dv2 = p2[1] - p0[1];
        const determinant = du1 * dv2 - du2 * dv1;
        const a = ((q1[0] - q0[0]) * dv2 - (q2[0] - q0[0]) * dv1) / determinant;
        const b = ((q1[1] - q0[1]) * dv2 - (q2[1] - q0[1]) * dv1) / determinant;
        const c = ((q2[0] - q0[0]) * du1 - (q1[0] - q0[0]) * du2) / determinant;
        const d = ((q2[1] - q0[1]) * du1 - (q1[1] - q0[1]) * du2) / determinant;
        const e = q0[0] - a * p0[0] - c * p0[1];
        const f = q0[1] - b * p0[0] - d * p0[1];
        return (
          <g key={index} clipPath={`url(#${id}-${index})`}>
            <clipPath id={`${id}-${index}`} clipPathUnits="userSpaceOnUse">
              <polygon points={[q0, q1, q2].map((point) => point.join(",")).join(" ")} />
            </clipPath>
            <use href={`#${id}-source`} transform={`matrix(${a} ${b} ${c} ${d} ${e} ${f})`} />
          </g>
        );
      })}
    </g>
  );
}
