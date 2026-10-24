import json
import subprocess
import sys
from pathlib import Path

from cueverge.gui import media_tools


def test_controller_native_dependency_inventory_covers_runtime_tools() -> None:
    repository_root = Path(__file__).parents[2]
    inventory = json.loads(
        (repository_root / "deploy/controller-native-dependencies.json").read_text(encoding="utf-8")
    )
    packages = inventory["runtime"]["system_packages"]
    required_packages = [package for package in packages if package["required"]]
    assert all(package.get("version") for package in required_packages)
    provided = {
        executable: package["required"]
        for package in packages
        for executable in package.get("provides", [])
    }
    assert "ffmpeg" not in {package["name"] for package in packages}
    assert "ffmpeg" not in provided
    assert "ffprobe" not in provided
    assert provided["clamscan"] is False
    assert "freshclam" in provided

    capabilities = inventory["runtime"]["media_capabilities"]
    assert capabilities["provider"] == "av"
    assert set(media_tools.REQUIRED_ENCODERS) <= set(capabilities["encoders"])
    assert set(media_tools.REQUIRED_FILTERS) <= set(capabilities["filters"])
    assert set(media_tools.REQUIRED_CONTAINER_FORMATS) <= set(capabilities["container_formats"])
    libraries = {library["name"] for library in inventory["runtime"]["python_media_libraries"]}
    assert {"av", "numpy", "pillow"} <= libraries

    excluded = {dependency["name"] for dependency in inventory["excluded_from_controller_image"]}
    assert {"ffmpeg", "mpv"} <= excluded
    assert inventory["frontend_build"]["runtime_image"] is False

    asset_processor = (repository_root / "src/cueverge/gui/asset_upload.py").read_text(
        encoding="utf-8"
    )
    assert 'shutil.which("clamscan")' in asset_processor
    for executable in ("ffmpeg", "ffprobe"):
        assert f'shutil.which("{executable}")' not in asset_processor
    assert "cueverge.gui.media_tools" in asset_processor

    preflight = repository_root / "docker/native_dependency_preflight.py"
    apt_packages = subprocess.check_output(
        [
            sys.executable,
            str(preflight),
            str(repository_root / "deploy/controller-native-dependencies.json"),
            "--apt-packages",
        ],
        text=True,
    ).split()
    assert apt_packages == [
        f"{package['name']}={package['version']}" for package in required_packages
    ]

    dockerfile = (repository_root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY deploy/controller-native-dependencies.json" in dockerfile
    assert "native_dependency_preflight.py" in dockerfile
    assert "--apt-packages" in dockerfile
    assert "ffmpeg" not in dockerfile
    assert "python:3.12-slim-bookworm@sha256:" in dockerfile
    assert "node:22-bookworm-slim@sha256:" in dockerfile

    entrypoint = (repository_root / "docker/entrypoint.sh").read_text(encoding="utf-8")
    assert "native_dependency_preflight.py" in entrypoint


def test_native_dependency_preflight_accepts_declared_host_capabilities() -> None:
    repository_root = Path(__file__).parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "docker/native_dependency_preflight.py"),
            str(repository_root / "deploy/controller-native-dependencies.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("ffmpeg version ")
    assert "(PyAV " in completed.stdout


def test_native_dependency_preflight_rejects_missing_declared_capability(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).parents[2]
    inventory = json.loads(
        (repository_root / "deploy/controller-native-dependencies.json").read_text(encoding="utf-8")
    )
    capabilities = inventory["runtime"]["media_capabilities"]
    capabilities["encoders"].append("cueverge_missing_encoder")
    capabilities["filters"].append("cueverge_missing_filter")
    invalid_inventory = tmp_path / "invalid-inventory.json"
    invalid_inventory.write_text(json.dumps(inventory), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "docker/native_dependency_preflight.py"),
            str(invalid_inventory),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "required media encoder is unavailable: cueverge_missing_encoder" in completed.stderr
    assert "required media filter is unavailable: cueverge_missing_filter" in completed.stderr
