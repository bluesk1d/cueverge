from typing import cast

from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject
from cueverge.gui.lighting_preview import (
    evaluate_lighting_preview,
    stage_points_to_surface,
)


def test_stage_points_are_mapped_to_normalized_surface_coordinates() -> None:
    assert stage_points_to_surface(
        ((1, 1), (5, 3), (9, 5)),
        stage_matrix=(2, 0, 0, 2, 1, 1),
        local_width=4,
        local_height=2,
    ) == ((0, 0), (0.5, 0.5), (1, 1))


def test_lighting_preview_is_deterministic_and_hardware_free() -> None:
    draft = DraftProject.model_validate(_draft())
    mapper = _mapper_export()
    assets = cast(AssetUploadService, object())

    first = evaluate_lighting_preview(
        project_id="demo-project",
        draft=draft,
        composition_id="lighting",
        timestamp_ms=500,
        assets=assets,
        mapper_export=mapper,
        device_reported_scale=0.5,
    )
    second = evaluate_lighting_preview(
        project_id="demo-project",
        draft=draft,
        composition_id="lighting",
        timestamp_ms=500,
        assets=assets,
        mapper_export=mapper,
        device_reported_scale=0.5,
    )

    assert first == second
    assert first["hardware_command_sent"] is False
    assert first["samples"] == [
        {
            "output_index": 2,
            "requested_color": [48, 0, 207],
            "color": [24, 0, 104],
        },
        {
            "output_index": 8,
            "requested_color": [0, 0, 255],
            "color": [0, 0, 128],
        },
    ]
    assert first["diagnostics"] == {
        "mapping_revision": 3,
        "calibration_id": "calibration-1",
        "mapping_stale": False,
        "unmapped_pixels": 0,
        "duplicate_output_indices": [],
        "device_reported_scale": 0.5,
    }
    assert first["performance_budget"] == {
        "profile": "interactive_30fps",
        "target_fps": 30,
        "maximum_canvas_pixels": 262144,
        "maximum_output_pixels": 4096,
        "maximum_author_layers": 128,
        "qualified": True,
        "exceeded_dimensions": [],
    }


def _draft() -> dict[str, object]:
    return {
        "schema_version": 1,
        "installation": {"schema_version": 1, "resources": []},
        "editor_metadata": {
            "compositions": {
                "lighting": {
                    "id": "lighting",
                    "name": "Facade",
                    "duration_ms": 1000,
                    "space_kind": "lighting_surface",
                    "target_id": "facade",
                    "width": 4,
                    "height": 2,
                    "local_time_offset_ms": 0,
                    "children": [],
                    "safe_points_ms": [],
                    "protected_intervals": [],
                    "lighting_settings": {
                        "canvas_width": 8,
                        "canvas_height": 4,
                        "sampling": "nearest",
                        "gamma": 1,
                        "brightness": 1,
                    },
                }
            },
            "scene_compositions": {"scene": ["lighting"]},
            "timeline_transform_layers": {"lighting": []},
            "lighting_author_layers": {
                "lighting": [
                    {
                        "id": "red",
                        "name": "Red",
                        "mode": "spatial",
                        "start_ms": 0,
                        "duration_ms": 1000,
                        "precedence": 0,
                        "blend_mode": "replace",
                        "opacity": 1,
                        "spatial": {
                            "primitive": "linear_gradient",
                            "color": "#FF0000",
                            "end_color": "#0000FF",
                            "seed": 1,
                        },
                    }
                ]
            },
        },
        "scenes": [
            {
                "schema_version": 2,
                "id": "scene",
                "name": "Scene",
                "mode": "media_synchronized",
                "resources": ["projector.left", "lights"],
                "duration_ms": 1000,
                "clock_source": "media",
                "media": [
                    {
                        "id": "media",
                        "resource": "projector.left",
                        "asset": None,
                        "required": True,
                        "start_delay_ms": 0,
                    }
                ],
                "lighting": [
                    {
                        "id": "lights",
                        "resource": "lights",
                        "required": True,
                        "effect": "synthetic_sweep",
                    }
                ],
            }
        ],
    }


def _mapper_export() -> dict[str, object]:
    return {
        "lighting_surfaces": [
            {
                "semantic_id": "facade",
                "resource": "lights",
                "local_width": 4,
                "local_height": 2,
                "stage_matrix": [2, 0, 0, 2, 1, 1],
                "brightness_limit": 1,
                "mapping_revision": 3,
                "calibration_id": "calibration-1",
                "pixels": [
                    {
                        "output_index": 8,
                        "local_x": 3,
                        "local_y": 1,
                        "footprint_width": 0.1,
                        "footprint_height": 0.1,
                    },
                    {
                        "output_index": 2,
                        "local_x": 1,
                        "local_y": 1,
                        "footprint_width": 0.1,
                        "footprint_height": 0.1,
                    },
                ],
            }
        ],
        "paths": [],
        "fixtures": [],
    }
