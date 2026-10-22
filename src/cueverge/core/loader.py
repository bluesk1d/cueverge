import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from cueverge.core.models import (
    AudiencePresenceDefinition,
    EncounterDefinition,
    InstallationDefinition,
    LightingCue,
    LightingLookDefinition,
    LightingLooksDefinition,
    LightingOutputDefinition,
    LightingTargetDefinition,
    LoadedLightingCue,
    LoadedLightingLooks,
    LoadedLightingOutput,
    LoadedMediaOutput,
    LoadedScene,
    MediaOutputDefinition,
    MultiLightingLooksDefinition,
    OpportunityProgramDefinition,
    PixelSegment,
    ResourceBinding,
    ResourceKind,
    SceneCatalog,
    SceneDefinition,
    SceneDefinitionV1,
    SceneDefinitionV2,
    SegmentReference,
    ShowProgramDefinition,
)
from cueverge.lighting.sequence import (
    MAX_SEQUENCE_BYTES,
    LightingSequenceReference,
    LoadedLightingSequence,
    load_lighting_sequence,
)


class ShowPackageError(ValueError):
    pass


def _bind_spatial_sequences(root: Path, scenes: dict[str, LoadedScene]) -> None:
    manifest = root / "runtime/lighting-sequences.json"
    if not manifest.exists():
        return
    try:
        records = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("lighting sequence manifest must be a list")
        grouped: dict[tuple[str, str], list[LoadedLightingSequence]] = {}
        loaded: dict[str, LoadedLightingSequence] = {}
        total_bytes = 0
        for record in records:
            reference = LightingSequenceReference.model_validate(record)
            scene = scenes.get(reference.scene_id)
            if scene is None:
                raise ValueError("lighting sequence references an unknown scene")
            output = next(
                (
                    item
                    for item in scene.lighting_outputs
                    if item.definition.resource == reference.resource
                ),
                None,
            )
            if output is None or reference.output_indices[-1] >= output.pixel_count:
                raise ValueError("lighting sequence output mapping is invalid")
            if (
                reference.offset_ms + reference.duration_ms * reference.time_scale
                > scene.definition.duration_ms
            ):
                raise ValueError("lighting sequence extends beyond its scene")
            key = (reference.scene_id, reference.resource)
            for previous in grouped.get(key, []):
                other = previous.reference
                if (
                    set(reference.output_indices).intersection(other.output_indices)
                    and reference.offset_ms < other.offset_ms + other.duration_ms * other.time_scale
                    and other.offset_ms
                    < reference.offset_ms + reference.duration_ms * reference.time_scale
                ):
                    raise ValueError("lighting sequences overlap the same physical pixels")
            cached = loaded.get(reference.path)
            if cached is not None:
                if (
                    cached.reference.digest_sha256 != reference.digest_sha256
                    or cached.reference.output_indices != reference.output_indices
                    or cached.reference.frame_count != reference.frame_count
                    or cached.reference.frame_rate != reference.frame_rate
                    or cached.reference.frame_rate_denominator != reference.frame_rate_denominator
                    or cached.reference.duration_ms != reference.duration_ms
                ):
                    raise ValueError("lighting sequence path has conflicting metadata")
                sequence = LoadedLightingSequence(reference, cached.data)
            else:
                total_bytes += reference.frame_count * len(reference.output_indices) * 3
                if total_bytes > MAX_SEQUENCE_BYTES:
                    raise ValueError("package lighting sequences exceed the 256 MiB budget")
                sequence = load_lighting_sequence(root, reference)
                loaded[reference.path] = sequence
            grouped.setdefault(key, []).append(sequence)
        for scene_id, scene in list(scenes.items()):
            scenes[scene_id] = scene.model_copy(
                update={
                    "lighting_outputs": tuple(
                        output.model_copy(
                            update={
                                "spatial_sequences": tuple(
                                    grouped.get((scene_id, output.definition.resource), [])
                                ),
                            }
                        )
                        for output in scene.lighting_outputs
                    ),
                }
            )
    except (OSError, ValueError, TypeError) as error:
        raise ShowPackageError(f"invalid spatial lighting package: {error}") from error


def load_show_package(package_root: Path) -> SceneCatalog:
    root = package_root.resolve()
    if not root.is_dir():
        raise ShowPackageError(f"show package directory does not exist: {root}")
    if (root / "runtime/lighting-bake-required.json").exists():
        raise ShowPackageError("spatial lighting requires current bakes before runtime playback")
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
        scene = _validate_scene(scene_file)
        if scene.id in scenes:
            raise ShowPackageError(f"duplicate scene id: {scene.id}")
        scenes[scene.id] = _bind_scene(scene, bindings)
    _bind_spatial_sequences(root, scenes)
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
    looks_path = root / "looks.json"
    looks_definition = _validate_lighting_looks(looks_path) if looks_path.exists() else None
    look_definitions: tuple[LightingLookDefinition, ...]
    if isinstance(looks_definition, LightingLooksDefinition):
        look_definitions = (looks_definition,)
    elif looks_definition is not None:
        look_definitions = looks_definition.outputs
    else:
        look_definitions = ()
    lighting_looks_by_resource: dict[str, LoadedLightingLooks] = {}
    for look_definition in look_definitions:
        binding = bindings.get(look_definition.resource)
        if (
            binding is None
            or binding.kind is not ResourceKind.PIXEL_OUTPUT
            or binding.pixel_count is None
        ):
            raise ShowPackageError(
                f"persistent lighting looks use invalid pixel resource: {look_definition.resource}"
            )
        lighting_looks_by_resource[look_definition.resource] = LoadedLightingLooks(
            definition=look_definition,
            pixel_count=binding.pixel_count,
            baseline_cues=_load_cues(binding, look_definition.baseline, "persistent baseline"),
            safe_state_cues=_load_cues(binding, look_definition.safe_state, "safe state"),
        )
    lighting_looks = next(iter(lighting_looks_by_resource.values()), None)
    if isinstance(looks_definition, LightingLooksDefinition):
        mismatched = sorted(
            scene.definition.id
            for scene in scenes.values()
            if looks_definition.resource
            not in {output.definition.resource for output in scene.lighting_outputs}
        )
        if mismatched:
            raise ShowPackageError(
                f"persistent lighting resource does not match scenes: {mismatched}"
            )
    return SceneCatalog(
        package_root=root,
        installation=installation,
        scenes=scenes,
        encounter=encounter,
        program=program,
        audience=audience,
        opportunities=opportunities,
        lighting_looks=lighting_looks,
        lighting_looks_by_resource=lighting_looks_by_resource,
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


def _validate_scene(path: Path) -> SceneDefinition:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ShowPackageError(f"missing scene file: {path}") from error
    except json.JSONDecodeError as error:
        raise ShowPackageError(f"invalid scene in {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ShowPackageError(f"invalid scene in {path}: scene must be an object")
    version = payload.get("schema_version")
    model_type: type[SceneDefinitionV1] | type[SceneDefinitionV2]
    if version == 1:
        model_type = SceneDefinitionV1
    elif version == 2:
        model_type = SceneDefinitionV2
    else:
        raise ShowPackageError(f"invalid scene in {path}: unsupported schema_version {version!r}")
    try:
        return model_type.model_validate(payload)
    except ValidationError as error:
        raise ShowPackageError(f"invalid scene in {path}: {error}") from error


def _validate_lighting_looks(
    path: Path,
) -> LightingLooksDefinition | MultiLightingLooksDefinition:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ShowPackageError(f"missing persistent lighting looks file: {path}") from error
    except json.JSONDecodeError as error:
        raise ShowPackageError(f"invalid persistent lighting looks in {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ShowPackageError(
            f"invalid persistent lighting looks in {path}: document must be an object"
        )
    version = payload.get("schema_version")
    model_type: type[LightingLooksDefinition] | type[MultiLightingLooksDefinition]
    if version == 1:
        model_type = LightingLooksDefinition
    elif version == 2:
        model_type = MultiLightingLooksDefinition
    else:
        raise ShowPackageError(
            f"invalid persistent lighting looks in {path}: unsupported schema_version {version!r}"
        )
    try:
        return model_type.model_validate(payload)
    except ValidationError as error:
        raise ShowPackageError(f"invalid persistent lighting looks in {path}: {error}") from error


def _bind_scene(
    scene: SceneDefinition,
    bindings: dict[str, ResourceBinding],
) -> LoadedScene:
    claimed = set(scene.resources)
    undefined = claimed.difference(bindings)
    if undefined:
        raise ShowPackageError(f"scene {scene.id} uses unbound resources: {sorted(undefined)}")

    media_definitions: tuple[MediaOutputDefinition, ...]
    lighting_definitions: tuple[LightingOutputDefinition, ...]
    if isinstance(scene, SceneDefinitionV1):
        media_definitions = (
            MediaOutputDefinition(
                id="media",
                resource=scene.media.resource,
                asset=scene.media.asset,
            ),
        )
        lighting_definitions = (
            LightingOutputDefinition(
                id="lighting",
                **scene.lighting.model_dump(),
            ),
        )
        clock_source_id = "media"
    else:
        media_definitions = scene.media
        lighting_definitions = scene.lighting
        clock_source_id = scene.clock_source

    media_outputs = tuple(
        _bind_media_output(scene.id, definition, bindings) for definition in media_definitions
    )
    agent_ids = [output.agent_id for output in media_outputs]
    if len(agent_ids) != len(set(agent_ids)):
        raise ShowPackageError(f"scene {scene.id} media outputs must use unique agents")
    lighting_outputs = tuple(
        _bind_lighting_output(scene.id, definition, bindings) for definition in lighting_definitions
    )
    primary_media = media_outputs[0] if media_outputs else None
    primary_lighting = lighting_outputs[0]
    return LoadedScene(
        definition=scene,
        media_outputs=media_outputs,
        lighting_outputs=lighting_outputs,
        clock_source_id=clock_source_id,
        agent_id=primary_media.agent_id if primary_media is not None else None,
        pixel_count=primary_lighting.pixel_count,
        pixel_segments=primary_lighting.pixel_segments,
        lighting_segment=primary_lighting.lighting_segment,
        lighting_target_kind=primary_lighting.lighting_target_kind,
        lighting_target_id=primary_lighting.lighting_target_id,
        lighting_target_indices=primary_lighting.lighting_target_indices,
        lighting_target_precedence=primary_lighting.lighting_target_precedence,
        lighting_cues=primary_lighting.lighting_cues,
        color=primary_lighting.color,
        asset_id=primary_media.asset_id if primary_media is not None else None,
    )


def _bind_media_output(
    scene_id: str,
    definition: MediaOutputDefinition,
    bindings: dict[str, ResourceBinding],
) -> LoadedMediaOutput:
    binding = bindings[definition.resource]
    if binding.kind is not ResourceKind.MEDIA_AGENT:
        raise ShowPackageError(
            f"scene {scene_id} media output {definition.id} resource is not a media_agent"
        )
    asset_id = _validate_asset_id(definition.asset) if definition.asset else None
    return LoadedMediaOutput(
        definition=definition,
        agent_id=binding.target,
        asset_id=asset_id,
    )


def _bind_lighting_output(
    scene_id: str,
    definition: LightingOutputDefinition,
    bindings: dict[str, ResourceBinding],
) -> LoadedLightingOutput:
    binding = bindings[definition.resource]
    if binding.kind is not ResourceKind.PIXEL_OUTPUT or binding.pixel_count is None:
        raise ShowPackageError(
            f"scene {scene_id} lighting output {definition.id} resource is not a pixel_output"
        )
    lighting_segment = None
    lighting_target_kind: Literal["segment", "fixture", "path"] | None = None
    lighting_target_id = None
    lighting_target_indices: tuple[int, ...] = ()
    lighting_target_precedence = 0
    if definition.segment is not None:
        lighting_segment = next(
            (segment for segment in binding.segments if segment.id == definition.segment),
            None,
        )
        if lighting_segment is None:
            raise ShowPackageError(
                f"scene {scene_id} uses unknown pixel segment: {definition.segment}"
            )
        lighting_target_kind = "segment"
        lighting_target_id = lighting_segment.id
        lighting_target_indices = tuple(range(lighting_segment.start, lighting_segment.end))
    elif definition.target is not None:
        target = definition.target
        (
            lighting_target_indices,
            lighting_target_precedence,
            lighting_segment,
        ) = _resolve_target(binding, target, scene_id)
        lighting_target_kind = target.kind
        lighting_target_id = target.id
    loaded_cues = _load_cues(binding, definition.timeline, scene_id)
    color = tuple(bytes.fromhex(definition.color.removeprefix("#")))
    return LoadedLightingOutput(
        definition=definition,
        pixel_count=binding.pixel_count,
        pixel_segments=binding.segments,
        lighting_segment=lighting_segment,
        lighting_target_kind=lighting_target_kind,
        lighting_target_id=lighting_target_id,
        lighting_target_indices=lighting_target_indices,
        lighting_target_precedence=lighting_target_precedence,
        lighting_cues=loaded_cues,
        color=(color[0], color[1], color[2]),
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


def _load_cues(
    binding: ResourceBinding,
    cues: tuple[LightingCue, ...],
    owner_id: str,
) -> tuple[LoadedLightingCue, ...]:
    loaded: list[LoadedLightingCue] = []
    if binding.pixel_count is None:
        raise ShowPackageError(f"lighting cue owner {owner_id} has no pixel count")
    for cue in cues:
        if cue.target is None:
            indices = tuple(range(binding.pixel_count))
            target_precedence = cue.precedence
        else:
            indices, fixture_precedence, _ = _resolve_target(binding, cue.target, owner_id)
            target_precedence = fixture_precedence + cue.precedence
        loaded.append(
            LoadedLightingCue(
                definition=cue,
                target_indices=indices,
                target_precedence=target_precedence,
            )
        )
    return tuple(loaded)


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
