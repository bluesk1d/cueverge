import json
from pathlib import Path
from typing import Any

import pytest

from cueverge.core.loader import ShowPackageError, load_show_package


def test_loads_demo_package_with_bound_resources() -> None:
    catalog = load_show_package(Path("shows/demo"))

    assert {binding.resource for binding in catalog.installation.resources} == {
        "projector.demo",
        "roofline.demo",
    }
    scene = catalog.require("synthetic-sweep")
    assert scene.agent_id == "fake-media-1"
    assert scene.pixel_count == 8
    assert scene.pixel_segments == ()
    assert scene.color == (0, 255, 96)
    assert scene.asset_id is None


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"lighting": {"resource": "missing", "effect": "synthetic_sweep"}}, "claim"),
        ({"lighting": {"resource": "roofline.demo", "effect": "unknown"}}, "literal"),
        ({"media": {"resource": "projector.demo", "asset": "../outside.mp4"}}, "escapes"),
        (
            {"media": {"resource": "projector.demo", "asset": "C:\\outside.mp4"}},
            "escapes",
        ),
    ],
)
def test_rejects_invalid_scene_packages(
    tmp_path: Path,
    mutation: dict[str, Any],
    message: str,
) -> None:
    root = _package(tmp_path)
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene.update(mutation)
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    with pytest.raises(ShowPackageError, match=message):
        load_show_package(root)


def test_normalizes_safe_asset_id_without_requiring_controller_copy(tmp_path: Path) -> None:
    root = _package(tmp_path)
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene["media"]["asset"] = "assets/demo.bin"
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    loaded = load_show_package(root).require("scene")
    assert loaded.asset_id == "assets/demo.bin"


def test_loads_v2_scene_with_multiple_resolved_outputs(tmp_path: Path) -> None:
    root = _package(tmp_path)
    installation_path = root / "installation.json"
    installation = json.loads(installation_path.read_text(encoding="utf-8"))
    installation["resources"].extend(
        [
            {
                "resource": "projector.second",
                "kind": "media_agent",
                "target": "fake-media-2",
            },
            {
                "resource": "porch.demo",
                "kind": "pixel_output",
                "target": "memory-porch",
                "pixel_count": 4,
            },
        ]
    )
    installation_path.write_text(json.dumps(installation), encoding="utf-8")
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene.update(
        {
            "schema_version": 2,
            "resources": [
                "projector.demo",
                "projector.second",
                "roofline.demo",
                "porch.demo",
            ],
            "clock_source": "projector-primary",
            "media": [
                {
                    "id": "projector-primary",
                    "resource": "projector.demo",
                    "asset": "primary.mp4",
                },
                {
                    "id": "projector-secondary",
                    "resource": "projector.second",
                    "asset": "secondary.mp4",
                    "required": False,
                    "start_delay_ms": 25,
                },
            ],
            "lighting": [
                {
                    "id": "roofline",
                    "resource": "roofline.demo",
                    "effect": "synthetic_sweep",
                },
                {
                    "id": "porch",
                    "resource": "porch.demo",
                    "effect": "synthetic_sweep",
                    "brightness_limit": 0.5,
                    "timing_offset_ms": -30,
                },
            ],
        }
    )
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    loaded = load_show_package(root).require("scene")

    assert loaded.clock_source_id == "projector-primary"
    assert [output.agent_id for output in loaded.media_outputs] == [
        "fake-media-1",
        "fake-media-2",
    ]
    assert loaded.media_outputs[1].definition.required is False
    assert loaded.media_outputs[1].definition.start_delay_ms == 25
    assert [output.pixel_count for output in loaded.lighting_outputs] == [8, 4]
    assert loaded.lighting_outputs[1].definition.brightness_limit == 0.5
    assert loaded.lighting_outputs[1].definition.timing_offset_ms == -30
    assert loaded.agent_id == "fake-media-1"
    assert loaded.pixel_count == 8


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"clock_source": "missing"}, "clock_source"),
        (
            {
                "media": [
                    {"id": "duplicate", "resource": "projector.demo"},
                ],
                "lighting": [
                    {
                        "id": "duplicate",
                        "resource": "roofline.demo",
                        "effect": "synthetic_sweep",
                    }
                ],
            },
            "output IDs must be unique",
        ),
    ],
)
def test_rejects_invalid_v2_output_topology(
    tmp_path: Path,
    mutation: dict[str, Any],
    message: str,
) -> None:
    root = _package(tmp_path)
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene.update(
        {
            "schema_version": 2,
            "clock_source": "projector",
            "media": [{"id": "projector", "resource": "projector.demo"}],
            "lighting": [
                {
                    "id": "roofline",
                    "resource": "roofline.demo",
                    "effect": "synthetic_sweep",
                }
            ],
        }
    )
    scene.update(mutation)
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    with pytest.raises(ShowPackageError, match=message):
        load_show_package(root)


def test_rejects_duplicate_scene_ids(tmp_path: Path) -> None:
    root = _package(tmp_path)
    original = root / "scenes/scene.json"
    (root / "scenes/copy.json").write_text(original.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ShowPackageError, match="duplicate scene id"):
        load_show_package(root)


def test_rejects_unknown_completion_and_failure_scenes(tmp_path: Path) -> None:
    root = _package(tmp_path)
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene["completion"] = {
        "policy": "transition_to",
        "target_scene_id": "missing-next",
    }
    scene["failure_scene_id"] = "missing-fallback"
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    with pytest.raises(ShowPackageError, match=r"missing-fallback.*missing-next"):
        load_show_package(root)


def test_rejects_invalid_completion_configuration(tmp_path: Path) -> None:
    root = _package(tmp_path)
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene["completion"] = {"policy": "transition_to"}
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    with pytest.raises(ShowPackageError, match="requires target_scene_id"):
        load_show_package(root)


def test_rejects_scene_scheduling_outside_duration(tmp_path: Path) -> None:
    root = _package(tmp_path)
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene["scheduling"] = {
        "safe_points_ms": [5000],
        "protected_intervals": [{"start_ms": 1000, "end_ms": 4500}],
    }
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    with pytest.raises(ShowPackageError, match="exceeds scene duration"):
        load_show_package(root)


def test_loads_declarative_show_program(tmp_path: Path) -> None:
    root = _package(tmp_path)
    original = json.loads((root / "scenes/scene.json").read_text(encoding="utf-8"))
    second = {**original, "id": "scene-b", "name": "Scene B"}
    (root / "scenes/scene-b.json").write_text(json.dumps(second), encoding="utf-8")
    program = {
        "schema_version": 1,
        "initial_scene_id": "scene",
        "default_pool_id": "ambient",
        "recent_history_size": 1,
        "random_seed": 42,
        "pools": [
            {
                "id": "ambient",
                "category": "ambient",
                "scenes": [
                    {"scene_id": "scene", "weight": 1, "cooldown_ms": 1000},
                    {
                        "scene_id": "scene-b",
                        "weight": 2,
                        "conditions": {"audience": False},
                    },
                ],
            }
        ],
    }
    (root / "program.json").write_text(json.dumps(program), encoding="utf-8")

    loaded = load_show_package(root)

    assert loaded.program is not None
    assert loaded.program.initial_scene_id == "scene"
    assert loaded.program.pools[0].scenes[1].conditions == {"audience": False}


def test_rejects_unknown_program_scene(tmp_path: Path) -> None:
    root = _package(tmp_path)
    program = {
        "schema_version": 1,
        "initial_scene_id": "missing",
        "default_pool_id": "ambient",
        "pools": [
            {
                "id": "ambient",
                "category": "ambient",
                "scenes": [{"scene_id": "scene"}],
            }
        ],
    }
    (root / "program.json").write_text(json.dumps(program), encoding="utf-8")

    with pytest.raises(ShowPackageError, match=r"unknown scenes.*missing"):
        load_show_package(root)


def test_loads_audience_presence_configuration(tmp_path: Path) -> None:
    root = _package(tmp_path)
    audience = {
        "schema_version": 1,
        "labels": ["person"],
        "cameras": ["doorbell"],
        "zones": ["Porch"],
        "observation_freshness_ms": 5000,
        "arrival_confirmation_ms": 1000,
        "absence_grace_ms": 5000,
        "long_idle_ms": 300000,
    }
    (root / "audience.json").write_text(json.dumps(audience), encoding="utf-8")

    loaded = load_show_package(root)

    assert loaded.audience is not None
    assert loaded.audience.cameras == ("doorbell",)
    assert loaded.audience.zones == ("Porch",)


def test_loads_engagement_opportunities(tmp_path: Path) -> None:
    root = _package(tmp_path)
    opportunities = {
        "schema_version": 1,
        "opportunities": [
            {
                "id": "arrival",
                "scene_id": "scene",
                "trigger_event": "audience.arrived",
                "required_context": {"audience": True},
                "ttl_ms": 10000,
                "min_absence_before_arrival_ms": 120000,
            }
        ],
    }
    (root / "opportunities.json").write_text(json.dumps(opportunities), encoding="utf-8")

    loaded = load_show_package(root)

    assert loaded.opportunities is not None
    assert loaded.opportunities.opportunities[0].id == "arrival"


def test_rejects_opportunity_with_unknown_scene(tmp_path: Path) -> None:
    root = _package(tmp_path)
    opportunities = {
        "schema_version": 1,
        "opportunities": [
            {
                "id": "arrival",
                "scene_id": "missing",
                "trigger_event": "audience.arrived",
                "ttl_ms": 10000,
            }
        ],
    }
    (root / "opportunities.json").write_text(json.dumps(opportunities), encoding="utf-8")

    with pytest.raises(ShowPackageError, match=r"unknown scenes.*missing"):
        load_show_package(root)


def test_loads_contiguous_pixel_segments(tmp_path: Path) -> None:
    root = _package(tmp_path)
    installation_path = root / "installation.json"
    installation = json.loads(installation_path.read_text(encoding="utf-8"))
    installation["resources"][1]["segments"] = [
        {"id": "right", "start": 0, "end": 3, "strand": "output-1"},
        {"id": "left", "start": 3, "end": 8, "strand": "output-1"},
    ]
    installation_path.write_text(json.dumps(installation), encoding="utf-8")

    loaded = load_show_package(root).require("scene")

    assert [segment.id for segment in loaded.pixel_segments] == ["right", "left"]
    assert [segment.pixel_count for segment in loaded.pixel_segments] == [3, 5]


def test_rejects_non_contiguous_pixel_segments(tmp_path: Path) -> None:
    root = _package(tmp_path)
    installation_path = root / "installation.json"
    installation = json.loads(installation_path.read_text(encoding="utf-8"))
    installation["resources"][1]["segments"] = [
        {"id": "first", "start": 0, "end": 3},
        {"id": "gap", "start": 4, "end": 8},
    ]
    installation_path.write_text(json.dumps(installation), encoding="utf-8")

    with pytest.raises(ShowPackageError, match="contiguous partition"):
        load_show_package(root)


def test_binds_segment_pulse_to_declared_geometry(tmp_path: Path) -> None:
    root = _package(tmp_path)
    installation_path = root / "installation.json"
    installation = json.loads(installation_path.read_text(encoding="utf-8"))
    installation["resources"][1]["segments"] = [
        {"id": "porch", "start": 0, "end": 8},
    ]
    installation_path.write_text(json.dumps(installation), encoding="utf-8")
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene["lighting"] = {
        "resource": "roofline.demo",
        "effect": "segment_pulse",
        "segment": "porch",
        "pulse_times_ms": [1000, 2000],
        "pulse_duration_ms": 120,
        "color": "#FFFFFF",
    }
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    loaded = load_show_package(root).require("scene")

    assert loaded.lighting_segment is not None
    assert loaded.lighting_segment.id == "porch"
    assert loaded.lighting_target_indices == tuple(range(8))


def test_resolves_semantic_fixture_and_reversed_path_targets(tmp_path: Path) -> None:
    root = _package(tmp_path)
    installation_path = root / "installation.json"
    installation = json.loads(installation_path.read_text(encoding="utf-8"))
    installation["resources"][1].update(
        {
            "segments": [
                {"id": "garage", "start": 0, "end": 3},
                {"id": "connector", "start": 3, "end": 5},
                {"id": "mouth-right", "start": 5, "end": 7},
                {"id": "mouth-left", "start": 7, "end": 8},
            ],
            "fixtures": [
                {
                    "id": "mouth",
                    "precedence": 20,
                    "segments": [
                        {"segment": "mouth-right"},
                        {"segment": "mouth-left", "reverse": True},
                    ],
                },
                {
                    "id": "exterior",
                    "segments": [
                        {"segment": "garage"},
                        {"segment": "connector"},
                        {"segment": "mouth-right"},
                    ],
                },
            ],
            "paths": [
                {
                    "id": "garage-to-mouth",
                    "segments": [
                        {"segment": "garage", "reverse": True},
                        {"segment": "connector"},
                        {"segment": "mouth-right"},
                    ],
                }
            ],
        }
    )
    installation_path.write_text(json.dumps(installation), encoding="utf-8")
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene["lighting"] = {
        "resource": "roofline.demo",
        "effect": "segment_pulse",
        "target": {"kind": "path", "id": "garage-to-mouth"},
        "pulse_times_ms": [1000],
    }
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    path_scene = load_show_package(root).require("scene")
    assert path_scene.lighting_target_kind == "path"
    assert path_scene.lighting_target_indices == (2, 1, 0, 3, 4, 5, 6)

    scene["lighting"]["target"] = {"kind": "fixture", "id": "mouth"}
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    fixture_scene = load_show_package(root).require("scene")
    assert fixture_scene.lighting_target_indices == (5, 6, 7)
    assert fixture_scene.lighting_target_precedence == 20


def test_rejects_unknown_semantic_pixel_target(tmp_path: Path) -> None:
    root = _package(tmp_path)
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene["lighting"] = {
        "resource": "roofline.demo",
        "effect": "segment_pulse",
        "target": {"kind": "fixture", "id": "missing"},
        "pulse_times_ms": [1000],
    }
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    with pytest.raises(ShowPackageError, match="unknown pixel fixture"):
        load_show_package(root)


def test_binds_multi_cue_lighting_timeline_targets(tmp_path: Path) -> None:
    root = _package(tmp_path)
    installation_path = root / "installation.json"
    installation = json.loads(installation_path.read_text(encoding="utf-8"))
    installation["resources"][1].update(
        {
            "segments": [
                {"id": "left", "start": 0, "end": 4},
                {"id": "right", "start": 4, "end": 8},
            ],
            "fixtures": [
                {
                    "id": "both",
                    "precedence": 20,
                    "segments": [
                        {"segment": "left"},
                        {"segment": "right", "reverse": True},
                    ],
                }
            ],
        }
    )
    installation_path.write_text(json.dumps(installation), encoding="utf-8")
    scene_path = root / "scenes/scene.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    scene["lighting"] = {
        "resource": "roofline.demo",
        "brightness_limit": 0.8,
        "timeline": [
            {
                "id": "baseline",
                "primitive": "solid",
                "start_ms": 0,
                "duration_ms": 4000,
                "color": "#100020",
            },
            {
                "id": "fixture-pulse",
                "primitive": "pulse",
                "start_ms": 1000,
                "duration_ms": 1000,
                "target": {"kind": "fixture", "id": "both"},
                "precedence": 5,
            },
        ],
    }
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    loaded = load_show_package(root).require("scene")

    assert len(loaded.lighting_cues) == 2
    assert loaded.lighting_cues[0].target_indices == tuple(range(8))
    assert loaded.lighting_cues[1].target_indices == (0, 1, 2, 3, 7, 6, 5, 4)
    assert loaded.lighting_cues[1].target_precedence == 25


def test_loads_persistent_baseline_and_safe_state(tmp_path: Path) -> None:
    root = _package(tmp_path)
    looks = {
        "schema_version": 1,
        "resource": "roofline.demo",
        "cycle_ms": 4000,
        "brightness_limit": 0.8,
        "baseline": [
            {
                "id": "baseline",
                "primitive": "pulse",
                "start_ms": 0,
                "duration_ms": 4000,
                "period_ms": 2000,
            }
        ],
        "safe_state": [
            {
                "id": "safe",
                "primitive": "blackout",
                "start_ms": 0,
                "duration_ms": 1,
            }
        ],
    }
    (root / "looks.json").write_text(json.dumps(looks), encoding="utf-8")

    loaded = load_show_package(root)

    assert loaded.lighting_looks is not None
    assert loaded.lighting_looks.pixel_count == 8
    assert loaded.lighting_looks.definition.brightness_limit == 0.8
    assert loaded.lighting_looks.baseline_cues[0].target_indices == tuple(range(8))


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "show"
    scenes = root / "scenes"
    scenes.mkdir(parents=True)
    installation = {
        "schema_version": 1,
        "resources": [
            {
                "resource": "projector.demo",
                "kind": "media_agent",
                "target": "fake-media-1",
            },
            {
                "resource": "roofline.demo",
                "kind": "pixel_output",
                "target": "memory",
                "pixel_count": 8,
            },
        ],
    }
    scene = {
        "schema_version": 1,
        "id": "scene",
        "name": "Scene",
        "mode": "media_synchronized",
        "resources": ["projector.demo", "roofline.demo"],
        "duration_ms": 4000,
        "media": {"resource": "projector.demo"},
        "lighting": {
            "resource": "roofline.demo",
            "effect": "synthetic_sweep",
        },
    }
    (root / "installation.json").write_text(json.dumps(installation), encoding="utf-8")
    (scenes / "scene.json").write_text(json.dumps(scene), encoding="utf-8")
    return root
