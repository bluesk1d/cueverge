import numpy as np

from cueverge.gui.frame_compositor import (
    PreparedVisualLayer,
    planar_projector_mesh,
    render_stage_to_projector,
    to_working_rgba,
)
from cueverge.gui.lighting_renderer import (
    LightingFrame,
    LightingPixel,
    assess_lighting_performance_budget,
    dispatch_lighting_frame,
    estimate_device_delivered_colors,
    evaluate_composed_lighting_frame,
    evaluate_lighting_canvas,
    map_stage_canvas_to_surface,
    render_lighting_layers,
)
from cueverge.lighting.sink import FrameSinkRouter, MemoryFrameSink


def test_sampling_modes_are_deterministic_and_use_logical_coordinates() -> None:
    canvas = np.array(
        [
            [[1, 0, 0], [0, 1, 0]],
            [[0, 0, 1], [1, 1, 1]],
        ],
        dtype=np.float32,
    )
    pixel = LightingPixel(7, 0.5, 0.5, 1, 1)

    nearest = evaluate_lighting_canvas(
        canvas,
        local_width=1,
        local_height=1,
        pixels=(pixel,),
        sampling="nearest",
        gamma=1,
        brightness=1,
    )
    bilinear = evaluate_lighting_canvas(
        canvas,
        local_width=1,
        local_height=1,
        pixels=(pixel,),
        sampling="bilinear",
        gamma=1,
        brightness=1,
    )
    area = evaluate_lighting_canvas(
        canvas,
        local_width=1,
        local_height=1,
        pixels=(pixel,),
        sampling="area",
        gamma=1,
        brightness=1,
    )

    assert nearest.colors.tolist() == [[255, 0, 0]]
    assert bilinear.colors.tolist() == [[128, 128, 128]]
    assert area.colors.tolist() == [[128, 128, 128]]
    assert np.array_equal(
        bilinear.colors,
        evaluate_lighting_canvas(
            canvas,
            local_width=1,
            local_height=1,
            pixels=(pixel,),
            sampling="bilinear",
            gamma=1,
            brightness=1,
        ).colors,
    )


def test_calibration_load_diagnostics_and_physical_order_apply_after_sampling() -> None:
    canvas = np.ones((2, 2, 3), dtype=np.float32)
    pixels = (
        LightingPixel(
            8,
            0,
            0,
            0.1,
            0.1,
            color_matrix=(0, 1, 0, 1, 0, 0, 0, 0, 1),
            gain=(1, 0.5, 1),
        ),
        LightingPixel(2, 1, 1, 0.1, 0.1),
        LightingPixel(5, 0.5, 0.5, 0.1, 0.1),
    )

    frame = evaluate_lighting_canvas(
        canvas,
        local_width=1,
        local_height=1,
        pixels=pixels,
        sampling="nearest",
        gamma=2,
        brightness=0.5,
    )

    assert frame.output_indices == (2, 5, 8)
    assert frame.normalized_channel_load > 0
    assert frame.colors.shape == (3, 3)


def test_final_frame_dispatch_uses_configured_resource_adapter() -> None:
    canvas = np.array(
        [[[1, 0, 0], [0, 1, 0], [0, 0, 1]]],
        dtype=np.float32,
    )
    frame = evaluate_lighting_canvas(
        canvas,
        local_width=1,
        local_height=1,
        pixels=(
            LightingPixel(9, 1, 0, 0.1, 0.1),
            LightingPixel(2, 0, 0, 0.1, 0.1),
            LightingPixel(5, 0.5, 0, 0.1, 0.1),
        ),
        sampling="nearest",
        gamma=1,
        brightness=1,
    )
    selected = MemoryFrameSink()
    unrelated = MemoryFrameSink()
    adapter = FrameSinkRouter({"lights.facade": selected, "lights.porch": unrelated})

    dispatch_lighting_frame(
        frame,
        sink=adapter,
        resource="lights.facade",
        scene_instance_id="simulation-1",
        timestamp_ms=250,
    )

    assert frame.output_indices == (2, 5, 9)
    assert selected.frames[0].pixels == (
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
    )
    assert selected.frames[0].position_ms == 250
    assert unrelated.frames == []


def test_lighting_dispatch_surfaces_missing_adapter_explicitly() -> None:
    frame = LightingFrame(
        colors=np.zeros((1, 3), dtype=np.uint8),
        output_indices=(0,),
        normalized_channel_load=0,
        clipped_pixels=0,
    )

    try:
        dispatch_lighting_frame(
            frame,
            sink=FrameSinkRouter({}),
            resource="lights.missing",
            scene_instance_id="simulation-1",
            timestamp_ms=0,
        )
    except ValueError as error:
        assert "configured sink" in str(error)
    else:
        raise AssertionError("missing lighting adapter was accepted")


def test_duplicate_indices_and_invalid_matrices_fail_explicitly() -> None:
    canvas = np.zeros((2, 2, 3), dtype=np.float32)
    duplicate = (
        LightingPixel(1, 0, 0, 0.1, 0.1),
        LightingPixel(1, 1, 1, 0.1, 0.1),
    )
    try:
        evaluate_lighting_canvas(
            canvas,
            local_width=1,
            local_height=1,
            pixels=duplicate,
            sampling="nearest",
            gamma=1,
            brightness=1,
        )
    except ValueError as error:
        assert "unique" in str(error)
    else:
        raise AssertionError("duplicate output indices were accepted")


def test_spatial_path_and_fixture_layers_blend_deterministically() -> None:
    layers = (
        {
            "id": "wash",
            "name": "Wash",
            "mode": "spatial",
            "start_ms": 0,
            "duration_ms": 1000,
            "precedence": 0,
            "blend_mode": "replace",
            "opacity": 1,
            "spatial": {
                "primitive": "linear_gradient",
                "color": "#000000",
                "end_color": "#0000FF",
                "seed": 1,
            },
        },
        {
            "id": "chase",
            "name": "Chase",
            "mode": "path",
            "start_ms": 0,
            "duration_ms": 1000,
            "precedence": 10,
            "blend_mode": "add",
            "opacity": 1,
            "path": {
                "path_id": "roof",
                "primitive": "chase",
                "position": 0,
                "width": 0.5,
                "direction": "forward",
                "fill": 1,
                "color": "#FF0000",
            },
        },
        {
            "id": "fixture",
            "name": "Fixture",
            "mode": "fixture",
            "start_ms": 0,
            "duration_ms": 1000,
            "precedence": 20,
            "blend_mode": "max",
            "opacity": 1,
            "fixture": {
                "target_kind": "fixture",
                "target_id": "window",
                "primitive": "pulse",
                "color": "#00FF00",
                "brightness": 1,
            },
        },
    )
    base = np.full((4, 8, 3), [0.1, 0.1, 0.1], dtype=np.float32)
    arguments = {
        "layers": layers,
        "timestamp_ms": 500,
        "canvas_width": 8,
        "canvas_height": 4,
        "paths": {"roof": ((0, 0.5), (1, 0.5))},
        "fixtures": {"window": ((0.5, 0.5),)},
        "base_canvas": base,
    }

    first = render_lighting_layers(**arguments)
    second = render_lighting_layers(**arguments)

    assert np.array_equal(first, second)
    assert np.any(first[..., 0] > 0)
    assert np.any(first[..., 1] > 0)
    assert np.any(first[..., 2] > 0)
    assert not np.array_equal(first, base)


def test_spatial_shape_is_seekable_paused_and_reconnect_deterministic() -> None:
    layer = {
        "id": "moving-shape",
        "name": "Moving shape",
        "mode": "spatial",
        "start_ms": 0,
        "duration_ms": 1000,
        "precedence": 0,
        "blend_mode": "replace",
        "opacity": 1,
        "spatial": {
            "primitive": "shape",
            "color": "#FFFFFF",
            "end_color": "#FFFFFF",
            "position": [0.1, 0.5],
            "size": [0.25, 0.5],
            "seed": 1,
        },
    }
    paused = render_lighting_layers(
        layers=(layer,),
        timestamp_ms=100,
        canvas_width=16,
        canvas_height=8,
    )
    sought_forward = render_lighting_layers(
        layers=(layer,),
        timestamp_ms=700,
        canvas_width=16,
        canvas_height=8,
    )
    sought_backward = render_lighting_layers(
        layers=(layer,),
        timestamp_ms=100,
        canvas_width=16,
        canvas_height=8,
    )
    reconnect_evaluation = render_lighting_layers(
        layers=(dict(layer),),
        timestamp_ms=700,
        canvas_width=16,
        canvas_height=8,
    )

    assert not np.array_equal(paused, sought_forward)
    assert np.array_equal(paused, sought_backward)
    assert np.array_equal(sought_forward, reconnect_evaluation)


def test_lighting_performance_budgets_have_explicit_boundaries() -> None:
    interactive = assess_lighting_performance_budget(
        canvas_width=512,
        canvas_height=512,
        output_pixel_count=4096,
        author_layer_count=128,
        sampling="bilinear",
    )
    exceeded = assess_lighting_performance_budget(
        canvas_width=513,
        canvas_height=512,
        output_pixel_count=4097,
        author_layer_count=129,
        sampling="nearest",
    )
    area = assess_lighting_performance_budget(
        canvas_width=256,
        canvas_height=256,
        output_pixel_count=2048,
        author_layer_count=64,
        sampling="area",
    )

    assert interactive.qualified is True
    assert interactive.target_fps == 30
    assert area.qualified is True
    assert area.target_fps == 10
    assert exceeded.exceeded_dimensions == (
        "canvas_pixels",
        "output_pixels",
        "author_layers",
    )


def test_raster_and_semantic_layers_share_one_sampling_pipeline() -> None:
    source = to_working_rgba(
        np.full((4, 4, 4), [0, 0, 255, 255], dtype=np.uint8),
        alpha_interpretation="opaque",
        source_color_space="srgb",
    )
    raster = PreparedVisualLayer(
        id="raster",
        source=source,
        source_kind="image",
        start_ms=0,
        duration_ms=1000,
        in_point_ms=0,
        z_index=0,
        fade_in_ms=0,
        fade_out_ms=0,
        keyframes=(
            {
                "time_ms": 0,
                "position": [2, 2],
                "scale": [1, 1],
                "rotation_deg": 0,
                "anchor": [0.5, 0.5],
                "opacity": 1,
                "crop": [0, 0, 0, 0],
                "interpolation": {
                    "kind": "linear",
                    "temporal_bezier": None,
                    "spatial_in": None,
                    "spatial_out": None,
                },
            },
        ),
    )
    semantic = (
        {
            "id": "red-add",
            "name": "Red",
            "mode": "spatial",
            "start_ms": 0,
            "duration_ms": 1000,
            "precedence": 10,
            "blend_mode": "add",
            "opacity": 1,
            "spatial": {
                "primitive": "solid",
                "color": "#FF0000",
                "end_color": "#FF0000",
                "seed": 0,
            },
        },
    )

    frame = evaluate_composed_lighting_frame(
        raster_layers=(raster,),
        author_layers=semantic,
        timestamp_ms=500,
        canvas_width=4,
        canvas_height=4,
        local_width=1,
        local_height=1,
        pixels=(LightingPixel(0, 0.5, 0.5, 0.1, 0.1),),
        paths={},
        fixtures={},
        sampling="nearest",
        gamma=1,
        brightness=1,
    )

    assert frame.colors.tolist() == [[255, 0, 255]]


def test_mixed_lighting_show_blends_persistent_and_timed_layers() -> None:
    source = to_working_rgba(
        np.full((4, 8, 4), [0, 0, 32, 255], dtype=np.uint8),
        alpha_interpretation="opaque",
        source_color_space="srgb",
    )
    raster = PreparedVisualLayer(
        id="raster-wash",
        source=source,
        source_kind="image",
        start_ms=0,
        duration_ms=1000,
        in_point_ms=0,
        z_index=0,
        fade_in_ms=0,
        fade_out_ms=0,
        keyframes=(
            {
                "time_ms": 0,
                "position": [4, 2],
                "scale": [1, 1],
                "rotation_deg": 0,
                "anchor": [0.5, 0.5],
                "opacity": 1,
                "crop": [0, 0, 0, 0],
                "interpolation": {
                    "kind": "linear",
                    "temporal_bezier": None,
                    "spatial_in": None,
                    "spatial_out": None,
                },
            },
        ),
    )
    layers = (
        {
            "id": "spatial-wash",
            "name": "Spatial wash",
            "mode": "spatial",
            "start_ms": 0,
            "duration_ms": 1000,
            "persistent": False,
            "precedence": 10,
            "blend_mode": "add",
            "opacity": 1,
            "spatial": {
                "primitive": "solid",
                "color": "#200000",
                "end_color": "#200000",
                "seed": 0,
            },
        },
        {
            "id": "path-chase",
            "name": "Path chase",
            "mode": "path",
            "start_ms": 0,
            "duration_ms": 1000,
            "persistent": False,
            "precedence": 20,
            "blend_mode": "add",
            "opacity": 1,
            "path": {
                "path_id": "roof",
                "primitive": "chase",
                "position": 0,
                "width": 0.3,
                "direction": "forward",
                "fill": 1,
                "color": "#002000",
            },
        },
        {
            "id": "fixture-pulse",
            "name": "Fixture pulse",
            "mode": "fixture",
            "start_ms": 0,
            "duration_ms": 1000,
            "persistent": False,
            "precedence": 30,
            "blend_mode": "add",
            "opacity": 1,
            "fixture": {
                "target_kind": "fixture",
                "target_id": "left-window",
                "primitive": "pulse",
                "color": "#200000",
                "brightness": 1,
            },
        },
        {
            "id": "persistent-look",
            "name": "Persistent look",
            "mode": "fixture",
            "start_ms": 0,
            "duration_ms": 100,
            "persistent": True,
            "precedence": 40,
            "blend_mode": "add",
            "opacity": 1,
            "fixture": {
                "target_kind": "fixture",
                "target_id": "right-window",
                "primitive": "solid",
                "color": "#002000",
                "brightness": 1,
            },
        },
    )
    arguments = {
        "raster_layers": (raster,),
        "author_layers": layers,
        "timestamp_ms": 500,
        "canvas_width": 8,
        "canvas_height": 4,
        "local_width": 1,
        "local_height": 1,
        "pixels": (
            LightingPixel(0, 0.1, 0.5, 0.05, 0.05),
            LightingPixel(1, 0.5, 0.5, 0.05, 0.05),
            LightingPixel(2, 0.9, 0.5, 0.05, 0.05),
        ),
        "paths": {"roof": ((0, 0.5), (1, 0.5))},
        "fixtures": {
            "left-window": ((0.1, 0.5),),
            "right-window": ((0.9, 0.5),),
        },
        "sampling": "nearest",
        "gamma": 1,
        "brightness": 1,
    }

    first = evaluate_composed_lighting_frame(**arguments)
    second = evaluate_composed_lighting_frame(**arguments)

    assert np.array_equal(first.colors, second.colors)
    assert first.colors[0, 0] > first.colors[1, 0]
    assert first.colors[1, 1] > first.colors[0, 1]
    assert first.colors[2, 1] > first.colors[0, 1]
    delivered = estimate_device_delivered_colors(first.colors, 0.5)
    assert np.array_equal(delivered, np.rint(first.colors * 0.5).astype(np.uint8))
    assert np.all(delivered <= first.colors)
    adapter = MemoryFrameSink()
    dispatch_lighting_frame(
        first,
        sink=adapter,
        resource="lights.facade",
        scene_instance_id="simulation-mixed-show",
        timestamp_ms=500,
    )
    assert adapter.frames[0].pixels == tuple(
        tuple(int(channel) for channel in color) for color in first.colors
    )


def test_stage_canvas_maps_through_calibrated_surface_transform() -> None:
    stage = np.zeros((2, 4, 3), dtype=np.float32)
    stage[:, :2, 0] = 1
    stage[:, 2:, 2] = 1

    local = map_stage_canvas_to_surface(
        stage,
        stage_bounds=(0, 0, 4, 2),
        stage_matrix=(1, 0, 0, 1, 2, 0),
        local_width=2,
        local_height=2,
        output_width=2,
        output_height=2,
    )
    repeated = map_stage_canvas_to_surface(
        stage,
        stage_bounds=(0, 0, 4, 2),
        stage_matrix=(1, 0, 0, 1, 2, 0),
        local_width=2,
        local_height=2,
        output_width=2,
        output_height=2,
    )

    assert np.array_equal(local, repeated)
    assert np.all(local[..., 2] > local[..., 0])


def test_one_stage_canvas_aligns_projector_and_lighting_samples() -> None:
    stage = np.zeros((4, 4, 3), dtype=np.float32)
    stage[:, 1:3, 0] = 1
    lighting = evaluate_composed_lighting_frame(
        raster_layers=(),
        author_layers=(),
        timestamp_ms=0,
        canvas_width=2,
        canvas_height=4,
        local_width=2,
        local_height=4,
        pixels=(
            LightingPixel(0, 0.5, 2, 0.1, 0.1),
            LightingPixel(1, 1.5, 2, 0.1, 0.1),
        ),
        paths={},
        fixtures={},
        stage_canvas=stage,
        stage_bounds=(0, 0, 4, 4),
        stage_matrix=(1, 0, 0, 1, 1, 0),
        sampling="nearest",
        gamma=1,
        brightness=1,
    )
    stage_rgba = np.concatenate(
        (stage, np.ones((4, 4, 1), dtype=np.float32)),
        axis=2,
    )
    mesh, triangles = planar_projector_mesh((0, 0, 4, 4))
    projector = render_stage_to_projector(
        stage_rgba,
        stage_bounds=(0, 0, 4, 4),
        projector_width=4,
        projector_height=4,
        mesh_vertices=mesh,
        mesh_triangles=triangles,
    )

    assert np.all(lighting.colors[:, 0] == 255)
    assert np.all(lighting.colors[:, 1:] == 0)
    assert np.all(projector[:, 1:3, 0] == 1)
