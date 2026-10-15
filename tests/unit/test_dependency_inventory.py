import json
from pathlib import Path


def test_controller_native_dependency_inventory_covers_runtime_tools() -> None:
    repository_root = Path(__file__).parents[2]
    inventory = json.loads(
        (repository_root / "deploy/controller-native-dependencies.json").read_text(encoding="utf-8")
    )
    packages = inventory["runtime"]["system_packages"]
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
