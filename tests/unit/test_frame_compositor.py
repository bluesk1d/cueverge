import wave
from pathlib import Path

import av
import numpy as np
import pytest
from PIL import Image

from cueverge.gui.frame_compositor import (
    AudioLayerBuffer,
    EvaluatedTransform,
    PreparedVisualLayer,
    TimedVideoFrame,
    compose_visual_frame,
    composite_over,
    conform_preview_pixel_format,
    decode_audio_stereo_48k,
    decode_still,
    decode_video_frame,
    evaluate_transform,
    mix_audio_layers,
    overlap_distance_field_masks,
    planar_projector_mesh,
    polygon_mask,
    rasterize_indexed_mesh,
    render_stage_to_projector,
    render_transformed_layer,
    select_video_frame,
    to_working_rgba,
    working_to_srgb8,
)


def test_straight_and_premultiplied_sources_match_in_working_space() -> None:
    straight = np.array([[[255, 0, 0, 128]]], dtype=np.uint8)
    premultiplied = np.array([[[128, 0, 0, 128]]], dtype=np.uint8)

    straight_working = to_working_rgba(
        straight,
        alpha_interpretation="straight",
        source_color_space="srgb",
    )
    premultiplied_working = to_working_rgba(
        premultiplied,
        alpha_interpretation="premultiplied",
        source_color_space="srgb",
    )

    assert np.allclose(straight_working, premultiplied_working, atol=0.005)
    assert straight_working[0, 0, 3] == np.float32(128 / 255)


def test_premultiplied_alpha_composition_round_trips_to_srgb() -> None:
    background = to_working_rgba(
        np.array([[[0, 0, 255, 255]]], dtype=np.uint8),
        alpha_interpretation="opaque",
        source_color_space="srgb",
    )
    foreground = to_working_rgba(
        np.array([[[255, 0, 0, 128]]], dtype=np.uint8),
        alpha_interpretation="straight",
        source_color_space="srgb",
    )

    result = working_to_srgb8(composite_over(background, foreground))

    assert result[0, 0, 3] == 255
    assert 180 <= result[0, 0, 0] <= 190
    assert 180 <= result[0, 0, 2] <= 190


def test_indexed_mesh_rasterization_is_deterministic() -> None:
    source = to_working_rgba(
        np.array(
            [
                [[255, 0, 0, 255], [0, 255, 0, 255]],
                [[0, 0, 255, 255], [255, 255, 255, 255]],
            ],
            dtype=np.uint8,
        ),
        alpha_interpretation="opaque",
        source_color_space="srgb",
    )
    vertices = (
        (0.0, 0.0, 0.0, 0.0),
        (1.0, 0.0, 4.0, 0.0),
        (1.0, 1.0, 4.0, 4.0),
        (0.0, 1.0, 0.0, 4.0),
    )
    triangles = ((0, 1, 2), (0, 2, 3))

    first = rasterize_indexed_mesh(
        source,
        output_width=4,
        output_height=4,
        vertices=vertices,
        triangles=triangles,
        stage_bounds=(0, 0, 4, 4),
    )
    second = rasterize_indexed_mesh(
        source,
        output_width=4,
        output_height=4,
        vertices=vertices,
        triangles=triangles,
        stage_bounds=(0, 0, 4, 4),
    )

    assert np.array_equal(first, second)
    assert np.all(first[..., 3] == 1)
    assert not np.array_equal(first[0, 0], first[-1, -1])


def test_pillow_and_pyav_decode_into_same_working_contract(tmp_path: Path) -> None:
    rgba = np.array([[[64, 128, 192, 128]]], dtype=np.uint8)
    path = tmp_path / "alpha.png"
    Image.fromarray(rgba, mode="RGBA").save(path)
    frame = av.VideoFrame.from_ndarray(rgba, format="rgba")

    still = decode_still(
        str(path),
        alpha_interpretation="straight",
        source_color_space="srgb",
    )
    video = decode_video_frame(
        frame,
        alpha_interpretation="straight",
        source_color_space="srgb",
    )

    assert np.array_equal(still, video)


def test_preview_pixel_format_conforms_rgba_through_yuv420p() -> None:
    rgba = np.full((36, 64, 4), [64, 128, 192, 255], dtype=np.uint8)

    conformed = conform_preview_pixel_format(rgba, "yuv420p")

    assert conformed.shape == rgba.shape
    assert conformed.dtype == np.uint8
    assert np.all(conformed[..., 3] == 255)


def test_audio_mix_applies_order_gain_mute_solo_and_fades() -> None:
    base = np.ones((6, 2), dtype=np.float32)
    layers = (
        AudioLayerBuffer(base, 0, -6.0206, False, False, 2, 2, 2),
        AudioLayerBuffer(base, 0, 0, True, False, 0, 0, 0),
        AudioLayerBuffer(base * 0.25, 2, 0, False, False, 1, 0, 0),
    )

    mixed = mix_audio_layers(layers, total_samples=8)
    repeated = mix_audio_layers(layers, total_samples=8)

    assert np.array_equal(mixed, repeated)
    assert np.allclose(mixed[0], 0)
    assert np.allclose(mixed[1], 0.5, atol=0.001)
    assert np.allclose(mixed[2], 0.75, atol=0.001)
    assert np.allclose(mixed[5], 0.25)
    assert np.allclose(mixed[6:], 0.25)

    soloed = mix_audio_layers(
        (
            layers[0],
            AudioLayerBuffer(base * 0.25, 2, 0, False, True, 1, 0, 0),
        ),
        total_samples=8,
    )
    assert np.allclose(soloed[:2], 0)
    assert np.allclose(soloed[2:8], 0.25)


def test_video_frame_selection_uses_floor_hold_without_blending() -> None:
    frames = tuple(
        TimedVideoFrame(
            pts_ms=pts,
            image=np.full((1, 1, 4), value, dtype=np.float32),
        )
        for pts, value in ((0, 0.1), (40, 0.2), (100, 0.3))
    )

    assert select_video_frame(frames, 0) is frames[0]
    assert select_video_frame(frames, 39) is frames[0]
    assert select_video_frame(frames, 40) is frames[1]
    assert select_video_frame(frames, 99) is frames[1]
    assert select_video_frame(frames, 100) is frames[2]
    assert select_video_frame(frames, 1000) is frames[2]
    with pytest.raises(ValueError, match="precedes"):
        select_video_frame(frames, -1)


def test_transform_evaluation_supports_hold_linear_ease_and_bezier() -> None:
    start = {
        "time_ms": 0,
        "position": [0, 0],
        "scale": [1, 1],
        "rotation_deg": 0,
        "anchor": [0.5, 0.5],
        "opacity": 0,
        "crop": [0, 0, 0, 0],
        "interpolation": {
            "kind": "bezier",
            "temporal_bezier": [0.25, 0.1, 0.25, 1],
            "spatial_in": [0, 0],
            "spatial_out": [2, 0],
        },
    }
    end = {
        "time_ms": 1000,
        "position": [10, 0],
        "scale": [2, 3],
        "rotation_deg": 90,
        "anchor": [0.25, 0.75],
        "opacity": 1,
        "crop": [0.1, 0, 0.1, 0],
        "interpolation": {
            "kind": "linear",
            "temporal_bezier": None,
            "spatial_in": [-2, 0],
            "spatial_out": None,
        },
    }

    evaluated = evaluate_transform((start, end), 500)
    repeated = evaluate_transform((start, end), 500)

    assert evaluated == repeated
    assert 5 < evaluated.position[0] < 10
    assert 1 < evaluated.scale[0] < 2
    assert 0 < evaluated.opacity < 1
    held = {**start, "interpolation": {**start["interpolation"], "kind": "hold"}}
    assert evaluate_transform((held, end), 999).position == (0, 0)


def test_visual_layer_render_applies_transform_crop_opacity_and_is_deterministic() -> None:
    source = to_working_rgba(
        np.full((4, 4, 4), [255, 0, 0, 255], dtype=np.uint8),
        alpha_interpretation="straight",
        source_color_space="srgb",
    )
    transform = EvaluatedTransform(
        position=(4, 4),
        scale=(1, 1),
        rotation_degrees=0,
        anchor=(0.5, 0.5),
        opacity=0.5,
        crop=(0.25, 0, 0.25, 0),
    )

    first = render_transformed_layer(
        source,
        output_width=8,
        output_height=8,
        transform=transform,
        fade_opacity=0.5,
    )
    second = render_transformed_layer(
        source,
        output_width=8,
        output_height=8,
        transform=transform,
        fade_opacity=0.5,
    )

    assert np.array_equal(first, second)
    assert np.max(first[..., 3]) == pytest.approx(0.25)
    assert np.count_nonzero(first[..., 3]) == 8


def test_audio_decode_resamples_mono_pcm_to_48k_stereo(tmp_path: Path) -> None:
    path = tmp_path / "tone.wav"
    samples = (np.sin(np.linspace(0, np.pi * 2, 240, endpoint=False)) * 16000).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(samples.tobytes())

    decoded = decode_audio_stereo_48k(str(path))

    assert decoded.shape[1] == 2
    assert 470 <= decoded.shape[0] <= 490
    assert np.allclose(decoded[:, 0], decoded[:, 1])


def test_visual_composition_selects_video_frame_orders_layers_and_resolves_alpha() -> None:
    red = to_working_rgba(
        np.full((2, 2, 4), [255, 0, 0, 128], dtype=np.uint8),
        alpha_interpretation="straight",
        source_color_space="srgb",
    )
    green = to_working_rgba(
        np.full((2, 2, 4), [0, 255, 0, 128], dtype=np.uint8),
        alpha_interpretation="straight",
        source_color_space="srgb",
    )
    keyframe = {
        "time_ms": 0,
        "position": [1, 1],
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
    }
    layers = (
        PreparedVisualLayer(
            id="video",
            source=(
                TimedVideoFrame(0, red),
                TimedVideoFrame(100, green),
            ),
            source_kind="video",
            start_ms=0,
            duration_ms=200,
            in_point_ms=0,
            z_index=0,
            fade_in_ms=0,
            fade_out_ms=0,
            keyframes=(keyframe,),
        ),
        PreparedVisualLayer(
            id="top",
            source=green,
            source_kind="image",
            start_ms=0,
            duration_ms=200,
            in_point_ms=0,
            z_index=1,
            fade_in_ms=100,
            fade_out_ms=0,
            keyframes=(keyframe,),
        ),
    )

    working, output = compose_visual_frame(
        layers,
        timestamp_ms=50,
        output_width=2,
        output_height=2,
        background_rgb=(0, 0, 255),
    )
    repeated_working, repeated_output = compose_visual_frame(
        layers,
        timestamp_ms=50,
        output_width=2,
        output_height=2,
        background_rgb=(0, 0, 255),
    )

    assert np.array_equal(working, repeated_working)
    assert np.array_equal(output, repeated_output)
    assert np.all(output[..., 3] == 255)
    assert np.all(output[..., 0] > output[..., 1])
    assert np.all(output[..., 1] > 0)
    assert np.all(output[..., 2] > 0)


def test_stage_frame_reconstructs_two_projector_contributions() -> None:
    stage = to_working_rgba(
        np.full((2, 4, 4), [255, 255, 255, 255], dtype=np.uint8),
        alpha_interpretation="opaque",
        source_color_space="srgb",
    )
    left_mesh, triangles = planar_projector_mesh((0, 0, 2, 2))
    right_mesh, _ = planar_projector_mesh((2, 0, 2, 2))
    blend = np.array([[1, 0.5], [1, 0.5]], dtype=np.float32)

    left = render_stage_to_projector(
        stage,
        stage_bounds=(0, 0, 4, 2),
        projector_width=2,
        projector_height=2,
        mesh_vertices=left_mesh,
        mesh_triangles=triangles,
        blend_alpha=blend,
    )
    right = render_stage_to_projector(
        stage,
        stage_bounds=(0, 0, 4, 2),
        projector_width=2,
        projector_height=2,
        mesh_vertices=right_mesh,
        mesh_triangles=triangles,
    )

    assert np.all(left[:, 0, 3] == 1)
    assert np.all(left[:, 1, 3] == 0.5)
    assert np.all(right[..., 3] == 1)


def test_projector_color_correction_brightness_and_black_level() -> None:
    stage = to_working_rgba(
        np.full((1, 1, 4), [255, 0, 0, 255], dtype=np.uint8),
        alpha_interpretation="opaque",
        source_color_space="linear_srgb",
    )
    mesh, triangles = planar_projector_mesh((0, 0, 1, 1))

    output = render_stage_to_projector(
        stage,
        stage_bounds=(0, 0, 1, 1),
        projector_width=1,
        projector_height=1,
        mesh_vertices=mesh,
        mesh_triangles=triangles,
        brightness=0.5,
        black_level=0.1,
        color_matrix=(0, 1, 0, 1, 0, 0, 0, 0, 1),
    )

    assert output[0, 0, 0] == pytest.approx(0.1)
    assert output[0, 0, 1] == pytest.approx(0.6)
    assert output[0, 0, 2] == pytest.approx(0.1)
    assert output[0, 0, 3] == 1


def test_overlap_distance_fields_partition_contribution_and_apply_gamma() -> None:
    overlap = ((4, 0), (6, 0), (6, 2), (4, 2))
    first_exclusive = (((0, 0), (4, 0), (4, 2), (0, 2)),)
    second_exclusive = (((6, 0), (10, 0), (10, 2), (6, 2)),)

    first, second = overlap_distance_field_masks(
        output_width=10,
        output_height=2,
        stage_bounds=(0, 0, 10, 2),
        overlap_polygon=overlap,
        first_exclusive_polygons=first_exclusive,
        second_exclusive_polygons=second_exclusive,
        ramp_width=1,
        gamma=1,
    )
    repeated = overlap_distance_field_masks(
        output_width=10,
        output_height=2,
        stage_bounds=(0, 0, 10, 2),
        overlap_polygon=overlap,
        first_exclusive_polygons=first_exclusive,
        second_exclusive_polygons=second_exclusive,
        ramp_width=1,
        gamma=1,
    )

    assert np.array_equal(first, repeated[0])
    assert np.array_equal(second, repeated[1])
    assert np.allclose(first[:, 4:6] + second[:, 4:6], 1)
    assert np.all(first[:, 4] > second[:, 4])
    assert np.all(first[:, 5] < second[:, 5])

    narrow_first, narrow_second = overlap_distance_field_masks(
        output_width=10,
        output_height=2,
        stage_bounds=(0, 0, 10, 2),
        overlap_polygon=overlap,
        first_exclusive_polygons=first_exclusive,
        second_exclusive_polygons=second_exclusive,
        ramp_width=0.25,
        gamma=2,
    )
    assert np.allclose(narrow_first[:, 4:6] + narrow_second[:, 4:6], 1)
    assert np.all(narrow_first[:, 4] >= first[:, 4])


def test_stage_polygon_mask_is_deterministic_and_bounded() -> None:
    first = polygon_mask(
        output_width=4,
        output_height=4,
        stage_bounds=(0, 0, 4, 4),
        polygon=((1, 1), (3, 1), (3, 3), (1, 3)),
    )
    second = polygon_mask(
        output_width=4,
        output_height=4,
        stage_bounds=(0, 0, 4, 4),
        polygon=((1, 1), (3, 1), (3, 3), (1, 3)),
    )

    assert np.array_equal(first, second)
    assert np.count_nonzero(first) == 4
    assert np.all(first[1:3, 1:3] == 1)
