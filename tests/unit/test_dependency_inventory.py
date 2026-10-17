import json
import subprocess
import sys
from pathlib import Path


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
    assert provided["ffmpeg"] is True
    assert provided["ffprobe"] is True
    assert provided["clamscan"] is False
    assert "freshclam" in provided

    capabilities = inventory["runtime"]["ffmpeg_capabilities"]
    assert {"aac", "libmp3lame", "libx264", "mjpeg", "png"} <= set(capabilities["encoders"])
    assert {"aformat", "ebur128", "scale", "showwavespic", "thumbnail"} <= set(
        capabilities["filters"]
    )

    excluded = {dependency["name"] for dependency in inventory["excluded_from_controller_image"]}
    assert "mpv" in excluded
    assert inventory["frontend_build"]["runtime_image"] is False

    asset_processor = (repository_root / "src/cueverge/gui/asset_upload.py").read_text(
        encoding="utf-8"
    )
    for executable in ("ffmpeg", "ffprobe", "clamscan"):
        assert f'shutil.which("{executable}")' in asset_processor
        assert executable in provided

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
    assert "ffmpeg version" in completed.stdout
    assert "ffprobe version" in completed.stdout


def test_native_dependency_preflight_rejects_missing_declared_capability(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).parents[2]
    inventory = json.loads(
        (repository_root / "deploy/controller-native-dependencies.json").read_text(
            encoding="utf-8"
        )
    )
    inventory["runtime"]["ffmpeg_capabilities"]["encoders"].append(
        "cueverge_missing_encoder"
    )
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
    assert (
        "required FFmpeg encoder is unavailable: cueverge_missing_encoder"
        in completed.stderr
    )
