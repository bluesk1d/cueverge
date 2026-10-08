import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from cueverge.core.models import (
    InstallationDefinition,
    LoadedScene,
    ResourceBinding,
    ResourceKind,
    SceneCatalog,
    SceneDefinition,
)


class ShowPackageError(ValueError):
    pass


def load_show_package(package_root: Path) -> SceneCatalog:
    root = package_root.resolve()
    if not root.is_dir():
        raise ShowPackageError(f"show package directory does not exist: {root}")

    installation = _validate_json(
        root / "installation.json",
        InstallationDefinition,
        "installation",
    )
    bindings = {binding.resource: binding for binding in installation.resources}
    scene_files = sorted(root.glob("scenes/*.json"))
    if not scene_files:
        raise ShowPackageError(f"show package has no scene files: {root / 'scenes'}")

    scenes: dict[str, LoadedScene] = {}
    for scene_file in scene_files:
        scene = _validate_json(scene_file, SceneDefinition, "scene")
        if scene.id in scenes:
            raise ShowPackageError(f"duplicate scene id: {scene.id}")
        scenes[scene.id] = _bind_scene(root, scene, bindings)
    return SceneCatalog(package_root=root, scenes=scenes)


def _validate_json[ModelT: BaseModel](
    path: Path,
    model_type: type[ModelT],
    description: str,
) -> ModelT:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ShowPackageError(f"missing {description} file: {path}") from error
    except json.JSONDecodeError as error:
        raise ShowPackageError(f"invalid JSON in {path}: {error}") from error
    try:
        return model_type.model_validate(payload)
    except ValidationError as error:
        raise ShowPackageError(f"invalid {description} in {path}: {error}") from error


def _bind_scene(
    root: Path,
    scene: SceneDefinition,
    bindings: dict[str, ResourceBinding],
) -> LoadedScene:
    claimed = set(scene.resources)
    undefined = claimed.difference(bindings)
    if undefined:
        raise ShowPackageError(f"scene {scene.id} uses unbound resources: {sorted(undefined)}")

    media = bindings[scene.media.resource]
    if media.kind is not ResourceKind.MEDIA_AGENT:
        raise ShowPackageError(f"scene {scene.id} media resource is not a media_agent")
    lighting = bindings[scene.lighting.resource]
    if lighting.kind is not ResourceKind.PIXEL_OUTPUT or lighting.pixel_count is None:
        raise ShowPackageError(f"scene {scene.id} lighting resource is not a pixel_output")

    asset_path = _resolve_asset(root, scene.media.asset) if scene.media.asset else None
    color = tuple(bytes.fromhex(scene.lighting.color.removeprefix("#")))
    return LoadedScene(
        definition=scene,
        agent_id=media.target,
        pixel_count=lighting.pixel_count,
        color=(color[0], color[1], color[2]),
        asset_path=asset_path,
    )


def _resolve_asset(root: Path, asset: str) -> Path:
    candidate = (root / asset).resolve()
    if not candidate.is_relative_to(root):
        raise ShowPackageError(f"asset path escapes show package: {asset}")
    if not candidate.is_file():
        raise ShowPackageError(f"media asset does not exist: {asset}")
    return candidate
