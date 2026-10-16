from collections.abc import Sequence
from typing import Literal, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image


class ProjectorSimulationError(RuntimeError):
    pass


def warp_review_proxy(
    source: Image.Image,
    *,
    output_width: int,
    output_height: int,
    native_width: int,
    native_height: int,
    homography: Sequence[float] | None,
    mesh_vertices: Sequence[tuple[float, float, float, float]],
    mesh_triangles: Sequence[tuple[int, int, int]],
    destination_points: Sequence[tuple[float, float]],
    orientation_degrees: Literal[0, 90, 180, 270],
    mirror_x: bool,
    mirror_y: bool,
) -> Image.Image:
    if output_width <= 0 or output_height <= 0:
        raise ProjectorSimulationError("simulation output dimensions must be positive")
    if native_width <= 0 or native_height <= 0:
        raise ProjectorSimulationError("projector native dimensions must be positive")
    rgba = source.convert("RGBA")
    if homography is not None:
        return _warp_planar(
            rgba,
            homography,
            output_width,
            output_height,
            native_width,
            native_height,
        )
    return _warp_mesh(
        rgba,
        output_width=output_width,
        output_height=output_height,
        mesh_vertices=mesh_vertices,
        mesh_triangles=mesh_triangles,
        destination_points=destination_points,
        orientation_degrees=orientation_degrees,
        mirror_x=mirror_x,
        mirror_y=mirror_y,
    )


def _warp_planar(
    source: Image.Image,
    homography: Sequence[float],
    output_width: int,
    output_height: int,
    native_width: int,
    native_height: int,
) -> Image.Image:
    if len(homography) != 9:
        raise ProjectorSimulationError("projector homography must contain nine values")
    source_to_native = (
        (native_width / source.width, 0.0, 0.0),
        (0.0, native_height / source.height, 0.0),
        (0.0, 0.0, 1.0),
    )
    native_to_photo = _matrix(homography)
    normalized_to_output = (
        (float(output_width), 0.0, 0.0),
        (0.0, float(output_height), 0.0),
        (0.0, 0.0, 1.0),
    )
    source_to_output = _multiply(
        normalized_to_output,
        _multiply(native_to_photo, source_to_native),
    )
    output_to_source = _inverse(source_to_output)
    scale = output_to_source[2][2]
    if abs(scale) < 1e-12:
        raise ProjectorSimulationError("projector homography cannot be normalized")
    coefficients = tuple(
        output_to_source[row][column] / scale
        for row, column in (
            (0, 0),
            (0, 1),
            (0, 2),
            (1, 0),
            (1, 1),
            (1, 2),
            (2, 0),
            (2, 1),
        )
    )
    return source.transform(
        (output_width, output_height),
        Image.Transform.PERSPECTIVE,
        coefficients,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )


def _warp_mesh(
    source: Image.Image,
    *,
    output_width: int,
    output_height: int,
    mesh_vertices: Sequence[tuple[float, float, float, float]],
    mesh_triangles: Sequence[tuple[int, int, int]],
    destination_points: Sequence[tuple[float, float]],
    orientation_degrees: Literal[0, 90, 180, 270],
    mirror_x: bool,
    mirror_y: bool,
) -> Image.Image:
    if not mesh_vertices or not mesh_triangles:
        raise ProjectorSimulationError("subdivided projector calibration has no indexed mesh")
    if len(mesh_vertices) != len(destination_points):
        raise ProjectorSimulationError(
            "subdivided projector mesh and photo control-point counts differ"
        )
    source_pixels = np.asarray(source, dtype=np.float64)
    output = np.zeros((output_height, output_width, 4), dtype=np.uint8)
    for triangle in mesh_triangles:
        if len(set(triangle)) != 3 or any(
            index < 0 or index >= len(mesh_vertices) for index in triangle
        ):
            raise ProjectorSimulationError("subdivided projector triangle is invalid")
        destination = np.asarray(
            [
                (
                    destination_points[index][0] * output_width,
                    destination_points[index][1] * output_height,
                )
                for index in triangle
            ],
            dtype=np.float64,
        )
        source_coordinates = np.asarray(
            [
                _oriented_source(
                    mesh_vertices[index][0],
                    mesh_vertices[index][1],
                    source.width,
                    source.height,
                    orientation_degrees,
                    mirror_x,
                    mirror_y,
                )
                for index in triangle
            ],
            dtype=np.float64,
        )
        _rasterize_triangle(output, source_pixels, destination, source_coordinates)
    return Image.fromarray(output, mode="RGBA")


def _rasterize_triangle(
    output: NDArray[np.uint8],
    source: NDArray[np.float64],
    destination: NDArray[np.float64],
    source_coordinates: NDArray[np.float64],
) -> None:
    minimum_x = max(0, int(np.floor(destination[:, 0].min())))
    maximum_x = min(output.shape[1] - 1, int(np.ceil(destination[:, 0].max())))
    minimum_y = max(0, int(np.floor(destination[:, 1].min())))
    maximum_y = min(output.shape[0] - 1, int(np.ceil(destination[:, 1].max())))
    if minimum_x > maximum_x or minimum_y > maximum_y:
        return
    x_grid, y_grid = np.meshgrid(
        np.arange(minimum_x, maximum_x + 1, dtype=np.float64) + 0.5,
        np.arange(minimum_y, maximum_y + 1, dtype=np.float64) + 0.5,
    )
    first, second, third = destination
    denominator = (second[1] - third[1]) * (first[0] - third[0]) + (
        third[0] - second[0]
    ) * (first[1] - third[1])
    if abs(float(denominator)) < 1e-12:
        raise ProjectorSimulationError("subdivided projector triangle is degenerate")
    first_weight = (
        (second[1] - third[1]) * (x_grid - third[0])
        + (third[0] - second[0]) * (y_grid - third[1])
    ) / denominator
    second_weight = (
        (third[1] - first[1]) * (x_grid - third[0])
        + (first[0] - third[0]) * (y_grid - third[1])
    ) / denominator
    third_weight = 1 - first_weight - second_weight
    inside = (first_weight >= -1e-9) & (second_weight >= -1e-9) & (third_weight >= -1e-9)
    if not inside.any():
        return
    source_x = (
        first_weight * source_coordinates[0, 0]
        + second_weight * source_coordinates[1, 0]
        + third_weight * source_coordinates[2, 0]
    )
    source_y = (
        first_weight * source_coordinates[0, 1]
        + second_weight * source_coordinates[1, 1]
        + third_weight * source_coordinates[2, 1]
    )
    sampled = _bilinear_sample(source, source_x[inside], source_y[inside])
    region = output[minimum_y : maximum_y + 1, minimum_x : maximum_x + 1]
    region[inside] = sampled


def _bilinear_sample(
    source: NDArray[np.float64],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
) -> NDArray[np.uint8]:
    x = np.clip(x, 0, source.shape[1] - 1)
    y = np.clip(y, 0, source.shape[0] - 1)
    left = np.floor(x).astype(np.int64)
    top = np.floor(y).astype(np.int64)
    right = np.minimum(left + 1, source.shape[1] - 1)
    bottom = np.minimum(top + 1, source.shape[0] - 1)
    x_fraction = (x - left)[:, None]
    y_fraction = (y - top)[:, None]
    top_pixels = source[top, left] * (1 - x_fraction) + source[top, right] * x_fraction
    bottom_pixels = (
        source[bottom, left] * (1 - x_fraction) + source[bottom, right] * x_fraction
    )
    return cast(
        NDArray[np.uint8],
        np.clip(
            top_pixels * (1 - y_fraction) + bottom_pixels * y_fraction,
            0,
            255,
        ).astype(np.uint8),
    )


def _oriented_source(
    x: float,
    y: float,
    width: int,
    height: int,
    orientation_degrees: Literal[0, 90, 180, 270],
    mirror_x: bool,
    mirror_y: bool,
) -> tuple[float, float]:
    if mirror_x:
        x = 1 - x
    if mirror_y:
        y = 1 - y
    if orientation_degrees == 90:
        x, y = 1 - y, x
    elif orientation_degrees == 180:
        x, y = 1 - x, 1 - y
    elif orientation_degrees == 270:
        x, y = y, 1 - x
    return x * (width - 1), y * (height - 1)


def _matrix(values: Sequence[float]) -> tuple[tuple[float, float, float], ...]:
    return (
        (float(values[0]), float(values[1]), float(values[2])),
        (float(values[3]), float(values[4]), float(values[5])),
        (float(values[6]), float(values[7]), float(values[8])),
    )


def _multiply(
    left: tuple[tuple[float, float, float], ...],
    right: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    return cast(
        tuple[tuple[float, float, float], ...],
        tuple(
            tuple(
                float(
                    sum(
                        left[row][pivot] * right[pivot][column]
                        for pivot in range(3)
                    )
                )
                for column in range(3)
            )
            for row in range(3)
        )
    )


def _inverse(
    values: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    a, b, c = values[0]
    d, e, f = values[1]
    g, h, i = values[2]
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(determinant) < 1e-12:
        raise ProjectorSimulationError("projector homography is singular")
    return (
        (
            (e * i - f * h) / determinant,
            (c * h - b * i) / determinant,
            (b * f - c * e) / determinant,
        ),
        (
            (f * g - d * i) / determinant,
            (a * i - c * g) / determinant,
            (c * d - a * f) / determinant,
        ),
        (
            (d * h - e * g) / determinant,
            (b * g - a * h) / determinant,
            (a * e - b * d) / determinant,
        ),
    )
