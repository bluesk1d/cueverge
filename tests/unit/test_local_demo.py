import json
import os
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from cueverge.config import ControllerConfig
from cueverge.gui.config import GuiConfig
from examples.local_demo import SEGMENTS, create_artwork, initialize, serve, view_point


def test_local_demo_allocations_are_contiguous_and_total_605() -> None:
    next_index = 0
    for _, start, count, points in SEGMENTS:
        assert start == next_index
        next_index += count
        assert all(0 <= coordinate <= 1 for point in points for coordinate in point)
    assert next_index == 605
    assert sum(segment[2] for segment in SEGMENTS[:5]) == 377
    assert sum(segment[2] for segment in SEGMENTS[5:]) == 228


def test_local_demo_credentials_are_private_and_persistent(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    credentials = initialize(root)
    assert credentials["username"] == "demo"
    assert len(credentials["password"]) >= 24
    assert initialize(root) == credentials
    assert stat.S_IMODE((root / "credentials.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_local_demo_rejects_existing_non_demo_data(tmp_path: Path) -> None:
    (tmp_path / "gui.sqlite3").touch()
    with pytest.raises(RuntimeError, match="Refusing non-demo"):
        initialize(tmp_path)


def test_local_demo_reference_artwork_is_valid(tmp_path: Path) -> None:
    for angled in (False, True):
        path = tmp_path / f"view-{angled}.png"
        create_artwork(path, angled)
        with Image.open(path) as image:
            assert image.size == (1280, 720)
            assert image.format == "PNG"
        assert all(
            0 <= coordinate <= 1
            for segment in SEGMENTS
            for point in segment[3]
            for coordinate in view_point(point, angled)
        )


def test_local_demo_launcher_drops_inherited_hardware_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sandbox"
    initialize(root)
    (root / "web").mkdir()
    (root / "web/index.html").write_text("<html></html>")
    (root / "show").mkdir()
    (root / "show/installation.json").write_text(json.dumps({"schema_version": 1}))
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("CUEVERGE_OUTPUT_CONFIG", "/private/output-config.json")
    monkeypatch.setenv("CUEVERGE_DDP_ENABLED", "true")
    monkeypatch.setenv("CUEVERGE_FRIGATE_MQTT_ENABLED", "true")
    monkeypatch.setenv("CUEVERGE_DEVICE_LISTENER_ENABLED", "true")
    run = Mock()
    monkeypatch.setattr("examples.local_demo.uvicorn.run", run)

    serve(root)

    run.assert_called_once_with("cueverge.controller.app:app", host="127.0.0.1", port=7000)
    controller = ControllerConfig.from_environment()
    gui = GuiConfig.from_environment()
    assert controller.show_package == root / "show"
    assert controller.output_deployment is None
    assert controller.output_config_file is None
    assert not controller.ddp.enabled
    assert not controller.frigate_mqtt.enabled
    assert gui is not None
    assert gui.data_root == root / "state"
    assert not gui.device_enrollment_enabled
    assert not gui.device_listener_enabled
