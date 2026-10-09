import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from cueverge.core.models import (
    AudiencePresenceDefinition,
    EncounterDefinition,
    InstallationDefinition,
    LightingTargetDefinition,
    LoadedLightingCue,
    LoadedScene,
    OpportunityProgramDefinition,
    PixelSegment,
    ResourceBinding,
    ResourceKind,
    SceneCatalog,
    SceneDefinition,
    SegmentReference,
    ShowProgramDefinition,
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
    for loaded_scene in scenes.values():
        lifecycle_references = {
            loaded_scene.definition.completion.target_scene_id,
            loaded_scene.definition.failure_scene_id,
        }
        missing = {
            scene_id
            for scene_id in lifecycle_references
            if scene_id is not None and scene_id not in scenes
        }
        if missing:
            raise ShowPackageError(
                f"scene {loaded_scene.definition.id} references unknown scenes: {sorted(missing)}"
            )
    encounter_path = root / "encounter.json"
    encounter = (
        _validate_json(encounter_path, EncounterDefinition, "encounter")
        if encounter_path.exists()
        else None
    )
    if encounter is not None:
        referenced = {encounter.ambient_scene_id}
        referenced.update(
            transition.scene_id
            for transition in encounter.transitions
            if transition.scene_id is not None
        )
        missing = referenced.difference(scenes)
        if missing:
            raise ShowPackageError(f"encounter references unknown scenes: {sorted(missing)}")
    program_path = root / "program.json"
    program = (
        _validate_json(program_path, ShowProgramDefinition, "show program")
        if program_path.exists()
        else None
    )
    if program is not None:
        program_scene_ids = {program.initial_scene_id}
        program_scene_ids.update(entry.scene_id for pool in program.pools for entry in pool.scenes)
        missing = program_scene_ids.difference(scenes)
        if missing:
            raise ShowPackageError(f"show program references unknown scenes: {sorted(missing)}")
    audience_path = root / "audience.json"
    audience = (
        _validate_json(audience_path, AudiencePresenceDefinition, "audience presence")
        if audience_path.exists()
        else None
    )
    opportunities_path = root / "opportunities.json"
    opportunities = (
        _validate_json(
            opportunities_path,
            OpportunityProgramDefinition,
            "engagement opportunities",
        )
        if opportunities_path.exists()
        else None
    )
    if opportunities is not None:
        missing = {
            opportunity.scene_id
            for opportunity in opportunities.opportunities
            if opportunity.scene_id not in scenes
        }
        if missing:
            raise ShowPackageError(
                f"engagement opportunities reference unknown scenes: {sorted(missing)}"
            )
    return SceneCatalog(
        package_root=root,
        scenes=scenes,
        encounter=encounter,
        program=program,
        audience=audience,
        opportunities=opportunities,
    )


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

    lighting_segment = None
    lighting_target_kind: Literal["segment", "fixture", "path"] | None = None
    lighting_target_id = None
    lighting_target_indices: tuple[int, ...] = ()
    lighting_target_precedence = 0
    if scene.lighting.segment is not None:
        lighting_segment = next(
            (segment for segment in lighting.segments if segment.id == scene.lighting.segment),
            None,
        )
        if lighting_segment is None:
            raise ShowPackageError(
                f"scene {scene.id} uses unknown pixel segment: {scene.lighting.segment}"
            )
        lighting_target_kind = "segment"
        lighting_target_id = lighting_segment.id
        lighting_target_indices = tuple(range(lighting_segment.start, lighting_segment.end))
    elif scene.lighting.target is not None:
        target = scene.lighting.target
        (
            lighting_target_indices,
            lighting_target_precedence,
            lighting_segment,
        ) = _resolve_target(lighting, target, scene.id)
        lighting_target_kind = target.kind
        lighting_target_id = target.id
    loaded_cues = tuple(
        LoadedLightingCue(
            definition=cue,
            target_indices=(
                _resolve_target(lighting, cue.target, scene.id)[0]
                if cue.target is not None
                else tuple(range(lighting.pixel_count))
            ),
            target_precedence=(
                _resolve_target(lighting, cue.target, scene.id)[1] + cue.precedence
                if cue.target is not None
                else cue.precedence
            ),
        )
        for cue in scene.lighting.timeline
    )

    asset_id = _validate_asset_id(scene.media.asset) if scene.media.asset else None
    color = tuple(bytes.fromhex(scene.lighting.color.removeprefix("#")))
    return LoadedScene(
        definition=scene,
        agent_id=media.target,
        pixel_count=lighting.pixel_count,
        pixel_segments=lighting.segments,
        lighting_segment=lighting_segment,
        lighting_target_kind=lighting_target_kind,
        lighting_target_id=lighting_target_id,
        lighting_target_indices=lighting_target_indices,
        lighting_target_precedence=lighting_target_precedence,
        lighting_cues=loaded_cues,
        color=(color[0], color[1], color[2]),
        asset_id=asset_id,
    )


def _resolve_target(
    binding: ResourceBinding,
    target: LightingTargetDefinition,
    scene_id: str,
) -> tuple[tuple[int, ...], int, PixelSegment | None]:
    lighting_segment = None
    precedence = 0
    if target.kind == "segment":
        lighting_segment = next(
            (segment for segment in binding.segments if segment.id == target.id),
            None,
        )
        if lighting_segment is None:
            raise ShowPackageError(f"scene {scene_id} uses unknown pixel segment: {target.id}")
        references: tuple[tuple[PixelSegment, bool], ...] = ((lighting_segment, False),)
    elif target.kind == "fixture":
        fixture = next(
            (fixture for fixture in binding.fixtures if fixture.id == target.id),
            None,
        )
        if fixture is None:
            raise ShowPackageError(f"scene {scene_id} uses unknown pixel fixture: {target.id}")
        precedence = fixture.precedence
        references = _resolve_references(binding, fixture.segments)
    else:
        path = next((path for path in binding.paths if path.id == target.id), None)
        if path is None:
            raise ShowPackageError(f"scene {scene_id} uses unknown pixel path: {target.id}")
        references = _resolve_references(binding, path.segments)
    indices = tuple(
        index
        for segment, reverse in references
        for index in (
            range(segment.end - 1, segment.start - 1, -1)
            if reverse
            else range(segment.start, segment.end)
        )
    )
    return indices, precedence, lighting_segment


def _resolve_references(
    binding: ResourceBinding,
    references: tuple[SegmentReference, ...],
) -> tuple[tuple[PixelSegment, bool], ...]:
    segments = {segment.id: segment for segment in binding.segments}
    return tuple((segments[reference.segment], reference.reverse) for reference in references)


def _validate_asset_id(asset: str) -> str:
    candidate = PurePosixPath(asset)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in asset:
        raise ShowPackageError(f"asset path escapes show package: {asset}")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise ShowPackageError("media asset id cannot be empty")
    return normalized
