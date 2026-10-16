import numpy as np
import pytest
from PIL import Image

from cueverge.gui.projector_simulation import (
    ProjectorSimulationError,
    warp_review_proxy,
)


def test_warp_review_proxy_projects_planar_content_with_transparency() -> None:
    source = Image.new("RGBA", (20, 10), (220, 30, 40, 255))

    overlay = warp_review_proxy(
        source,
        output_width=100,
        output_height=100,
        native_width=20,
        native_height=10,
        homography=(0.025, 0, 0.25, 0, 0.05, 0.25, 0, 0, 1),
        mesh_vertices=(),
        mesh_triangles=(),
        destination_points=(),
        orientation_degrees=0,
        mirror_x=False,
        mirror_y=False,
    )

    pixels = np.asarray(overlay)
    assert pixels[50, 50, :3].tolist() == [220, 30, 40]
    assert pixels[50, 50, 3] == 255
    assert pixels[5, 5, 3] == 0


def test_warp_review_proxy_rasterizes_subdivided_mesh() -> None:
    source = Image.new("RGBA", (20, 20), (20, 180, 90, 255))

    overlay = warp_review_proxy(
        source,
        output_width=100,
        output_height=100,
        native_width=20,
        native_height=20,
        homography=None,
        mesh_vertices=(
            (0, 0, 0, 0),
            (1, 0, 1, 0),
            (1, 1, 1, 1),
            (0, 1, 0, 1),
        ),
        mesh_triangles=((0, 1, 2), (0, 2, 3)),
        destination_points=((0.1, 0.2), (0.9, 0.1), (0.8, 0.9), (0.2, 0.8)),
        orientation_degrees=0,
        mirror_x=False,
        mirror_y=False,
    )

    pixels = np.asarray(overlay)
    assert pixels[50, 50, :3].tolist() == [20, 180, 90]
    assert pixels[50, 50, 3] == 255
    assert pixels[5, 5, 3] == 0


def test_warp_review_proxy_rejects_incomplete_subdivided_calibration() -> None:
    with pytest.raises(
        ProjectorSimulationError,
        match="mesh and photo control-point counts differ",
    ):
        warp_review_proxy(
            Image.new("RGBA", (20, 20), (0, 0, 0, 255)),
            output_width=100,
            output_height=100,
            native_width=20,
            native_height=20,
            homography=None,
            mesh_vertices=(
                (0, 0, 0, 0),
                (1, 0, 1, 0),
                (1, 1, 1, 1),
                (0, 1, 0, 1),
            ),
            mesh_triangles=((0, 1, 2), (0, 2, 3)),
            destination_points=((0.1, 0.2), (0.9, 0.1), (0.8, 0.9)),
            orientation_degrees=0,
            mirror_x=False,
            mirror_y=False,
        )
