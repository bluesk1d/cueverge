import json
from pathlib import Path
from typing import Any

import pytest

from cueverge.core.loader import ShowPackageError, load_show_package


def test_loads_demo_package_with_bound_resources() -> None:
    catalog = load_show_package(Path("shows/demo"))

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
