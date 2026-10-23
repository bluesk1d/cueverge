"""Pure helpers for deriving shared-stage placement from a reference photo view."""

import math
from collections.abc import Sequence

import numpy as np

# Layout canvas coordinates are normalized against this fixed 1000 x 600 view.
CANVAS_WIDTH = 1000.0
CANVAS_HEIGHT = 600.0
STAGE_MESH_DIVISIONS = 8

Point = tuple[float, float]
Polygon = tuple[Point, ...]
Matrix3 = tuple[float, float, float, float, float, float, float, float, float]
Affine = tuple[float, float, float, float, float, float]


def canvas_to_stage(point: Sequence[float], units_per_canvas: float) -> Point:
    return (
        float(point[0]) * CANVAS_WIDTH * units_per_canvas,
        float(point[1]) * CANVAS_HEIGHT * units_per_canvas,
    )


def stage_size(units_per_canvas: float) -> Point:
    return (CANVAS_WIDTH * units_per_canvas, CANVAS_HEIGHT * units_per_canvas)


def units_from_measurement(
    first: Sequence[float],
    second: Sequence[float],
    distance: float,
) -> float:
    canvas_distance = math.hypot(
        (float(second[0]) - float(first[0])) * CANVAS_WIDTH,
        (float(second[1]) - float(first[1])) * CANVAS_HEIGHT,
    )
    if canvas_distance < 5:
        raise ValueError("scale measurement points must be at least 5 layout pixels apart")
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("scale measurement distance must be positive")
    return distance / canvas_distance


def stage_homography(
    photo_homography: Sequence[float],
    native_width: int,
    native_height: int,
    units_per_canvas: float,
) -> Matrix3:
    """Compose normalized native (u, v) -> native px -> photo -> stage units."""
    photo = np.asarray(photo_homography, dtype=np.float64).reshape(3, 3)
    native = np.diag([float(native_width), float(native_height), 1.0])
    stage = np.diag([CANVAS_WIDTH * units_per_canvas, CANVAS_HEIGHT * units_per_canvas, 1.0])
    combined = stage @ photo @ native
    if abs(combined[2, 2]) < 1e-12:
        raise ValueError("stage homography is singular")
    combined /= combined[2, 2]
    return tuple(float(value) for value in combined.reshape(9))  # type: ignore[return-value]


def apply_homography(matrix: Sequence[float], u: float, v: float) -> Point:
    denominator = matrix[6] * u + matrix[7] * v + matrix[8]
    if abs(denominator) < 1e-12:
        raise ValueError("stage homography maps a point to infinity")
    return (
        (matrix[0] * u + matrix[1] * v + matrix[2]) / denominator,
        (matrix[3] * u + matrix[4] * v + matrix[5]) / denominator,
    )


def homography_footprint(matrix: Sequence[float]) -> Polygon:
    return tuple(apply_homography(matrix, u, v) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)))


def homography_mesh(
    matrix: Sequence[float],
    divisions: int = STAGE_MESH_DIVISIONS,
) -> tuple[
    tuple[tuple[float, float, float, float], ...],
    tuple[tuple[int, int, int], ...],
]:
    """Subdivide so piecewise-affine projector rasterization follows the perspective."""
    vertices: list[tuple[float, float, float, float]] = []
    for row in range(divisions + 1):
        v = row / divisions
        for column in range(divisions + 1):
            u = column / divisions
            x, y = apply_homography(matrix, u, v)
            vertices.append((u, v, x, y))
    triangles: list[tuple[int, int, int]] = []
    stride = divisions + 1
    for row in range(divisions):
        for column in range(divisions):
            top_left = row * stride + column
            top_right = top_left + 1
            bottom_left = top_left + stride
            bottom_right = bottom_left + 1
            triangles.append((top_left, top_right, bottom_right))
            triangles.append((top_left, bottom_right, bottom_left))
    return tuple(vertices), tuple(triangles)


def bounds(points: Sequence[Point]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def fit_affine(local: Sequence[Point], stage: Sequence[Point]) -> Affine:
    """Fit stage = (a*x + c*y + tx, b*x + d*y + ty) for a lighting surface.

    Uses an exact similarity for two points and least squares for three or more
    non-collinear points. Collinear inputs fall back to a similarity.
    """
    if not local or len(local) != len(stage):
        raise ValueError("affine fit requires matching point lists")
    if len(local) == 1:
        return (1.0, 0.0, 0.0, 1.0, stage[0][0] - local[0][0], stage[0][1] - local[0][1])
    if len(local) >= 3:
        source = np.asarray([[x, y, 1.0] for x, y in local], dtype=np.float64)
        if np.linalg.matrix_rank(source, tol=1e-9) == 3:
            target = np.asarray(stage, dtype=np.float64)
            solution, *_ = np.linalg.lstsq(source, target, rcond=None)
            a, b = float(solution[0, 0]), float(solution[0, 1])
            c, d = float(solution[1, 0]), float(solution[1, 1])
            tx, ty = float(solution[2, 0]), float(solution[2, 1])
            if abs(a * d - b * c) > 1e-12:
                return (a, b, c, d, tx, ty)
    return _similarity(local[0], local[-1], stage[0], stage[-1])


def _similarity(p0: Point, p1: Point, q0: Point, q1: Point) -> Affine:
    source = complex(p1[0] - p0[0], p1[1] - p0[1])
    if abs(source) < 1e-12:
        return (1.0, 0.0, 0.0, 1.0, q0[0] - p0[0], q0[1] - p0[1])
    ratio = complex(q1[0] - q0[0], q1[1] - q0[1]) / source
    if abs(ratio) < 1e-12:
        ratio = complex(1, 0)
    a, b = ratio.real, ratio.imag
    c, d = -b, a
    return (a, b, c, d, q0[0] - (a * p0[0] + c * p0[1]), q0[1] - (b * p0[0] + d * p0[1]))


def bbox_affine(local: Sequence[Point], stage: Sequence[Point]) -> Affine:
    """Axis-aligned fallback when local points do not correspond one-to-one."""
    lx0, ly0, lx1, ly1 = bounds(local)
    sx0, sy0, sx1, sy1 = bounds(stage)
    local_w, local_h = lx1 - lx0, ly1 - ly0
    stage_w, stage_h = sx1 - sx0, sy1 - sy0
    scale_x = stage_w / local_w if local_w > 1e-12 else None
    scale_y = stage_h / local_h if local_h > 1e-12 else None
    uniform = scale_x or scale_y or 1.0
    a = scale_x if scale_x else uniform
    d = scale_y if scale_y else uniform
    return (a, 0.0, 0.0, d, sx0 - a * lx0, sy0 - d * ly0)


def signed_area(polygon: Sequence[Point]) -> float:
    total = 0.0
    for index, (x0, y0) in enumerate(polygon):
        x1, y1 = polygon[(index + 1) % len(polygon)]
        total += x0 * y1 - x1 * y0
    return total / 2


def area(polygon: Sequence[Point]) -> float:
    return abs(signed_area(polygon)) if len(polygon) >= 3 else 0.0


def oriented(polygon: Sequence[Point]) -> Polygon:
    return tuple(polygon) if signed_area(polygon) >= 0 else tuple(reversed(polygon))


def is_convex(polygon: Sequence[Point]) -> bool:
    if len(polygon) < 3:
        return False
    sign = 0
    for index in range(len(polygon)):
        x0, y0 = polygon[index]
        x1, y1 = polygon[(index + 1) % len(polygon)]
        x2, y2 = polygon[(index + 2) % len(polygon)]
        cross = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
        if abs(cross) < 1e-12:
            continue
        current = 1 if cross > 0 else -1
        if sign and current != sign:
            return False
        sign = current
    return sign != 0


def _clip_half_plane(
    polygon: Sequence[Point],
    start: Point,
    end: Point,
    keep_inside: bool,
) -> Polygon:
    def side(point: Point) -> float:
        return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (
            point[0] - start[0]
        )

    def keep(value: float) -> bool:
        return value >= -1e-12 if keep_inside else value <= 1e-12

    output: list[Point] = []
    count = len(polygon)
    for index in range(count):
        current = polygon[index]
        following = polygon[(index + 1) % count]
        current_side = side(current)
        following_side = side(following)
        if keep(current_side):
            output.append(current)
        if (current_side > 1e-12 and following_side < -1e-12) or (
            current_side < -1e-12 and following_side > 1e-12
        ):
            ratio = current_side / (current_side - following_side)
            output.append(
                (
                    current[0] + (following[0] - current[0]) * ratio,
                    current[1] + (following[1] - current[1]) * ratio,
                )
            )
    return tuple(output)


def intersect_convex(subject: Sequence[Point], clip: Sequence[Point]) -> Polygon:
    """Clip a polygon by a convex polygon; both should be counter-clockwise."""
    output: Polygon = tuple(subject)
    for index in range(len(clip)):
        if len(output) < 3:
            return ()
        output = _clip_half_plane(output, clip[index], clip[(index + 1) % len(clip)], True)
    return output if len(output) >= 3 else ()


def subtract_convex(subject: Sequence[Point], clip: Sequence[Point]) -> tuple[Polygon, ...]:
    """Return disjoint convex pieces of subject outside a convex clip polygon."""
    pieces: list[Polygon] = []
    remaining: Polygon = tuple(subject)
    for index in range(len(clip)):
        if len(remaining) < 3:
            break
        start = clip[index]
        end = clip[(index + 1) % len(clip)]
        outside = _clip_half_plane(remaining, start, end, False)
        if len(outside) >= 3 and area(outside) > 1e-9:
            pieces.append(outside)
        remaining = _clip_half_plane(remaining, start, end, True)
    return tuple(pieces)


def overlap_fraction(first: Sequence[Point], second: Sequence[Point]) -> float:
    a, b = oriented(first), oriented(second)
    if not (is_convex(a) and is_convex(b)):
        return 0.0
    shared = area(intersect_convex(a, b))
    smaller = min(area(a), area(b))
    return shared / smaller if smaller > 1e-12 else 0.0
