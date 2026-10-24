from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cueverge.core.models import ResourceBinding, ResourceKind, SceneDefinitionV2
from cueverge.gui.contracts import (
    AddSceneOutputRequest,
    CreateSceneRequest,
    DraftProject,
    RemoveSceneOutputRequest,
    RenameSceneOutputRequest,
    Role,
    SceneAuthoringOptions,
    SceneOutputCreate,
)
from cueverge.gui.repository import RevisionConflictError, StoredRevision

if TYPE_CHECKING:
    from cueverge.gui.service import GuiServices


def scene_authoring_options(services: "GuiServices", project_id: str) -> SceneAuthoringOptions:
    stored = services.repository.get_revision(project_id)
    bindings = {binding.resource: binding for binding in stored.draft.installation.resources}
    saved_resources = set(bindings)
    installation_id = stored.draft.editor_metadata.get("installation_id")
    runtime_installation = services.repository.get_runtime_installation()
    if installation_id == runtime_installation.id:
        for binding in services.runtime.catalog.installation.resources:
            bindings.setdefault(binding.resource, binding)
    resources = tuple(
        binding
        for binding in bindings.values()
        if binding.kind in (ResourceKind.MEDIA_AGENT, ResourceKind.PIXEL_OUTPUT)
    )
    return SceneAuthoringOptions(
        project_id=project_id,
        revision=stored.revision,
        installation_id=installation_id if isinstance(installation_id, str) else None,
        resources=resources,
        importable_resources=tuple(
            binding.resource for binding in resources if binding.resource not in saved_resources
        ),
    )


def _output_document(
    request: SceneOutputCreate, binding: ResourceBinding, duration_ms: int
) -> tuple[str, dict[str, Any]]:
    if binding.kind is ResourceKind.MEDIA_AGENT:
        return "media", {"id": request.output_id, "resource": binding.resource, "asset": None}
    if binding.kind is ResourceKind.PIXEL_OUTPUT:
        return "lighting", {
            "id": request.output_id,
            "resource": binding.resource,
            "timeline": [
                {
                    "id": "blackout",
                    "primitive": "blackout",
                    "start_ms": 0,
                    "duration_ms": duration_ms,
                    "color": "#000000",
                }
            ],
        }
    raise ValueError("Only media-agent and pixel-output resources can be added to a scene.")


def create_scene(
    draft: DraftProject, request: CreateSceneRequest, binding: ResourceBinding
) -> DraftProject:
    if any(scene.id == request.scene_id for scene in draft.scenes):
        raise ValueError("Scene ID already exists.")
    key, output = _output_document(request.output, binding, request.duration_ms)
    if request.mode == "media_synchronized" and key != "media":
        raise ValueError("Media-synchronized scenes require a media output as their clock.")
    document = draft.model_dump(mode="json")
    scene: dict[str, Any] = {
        "schema_version": 2,
        "id": request.scene_id,
        "name": request.name,
        "mode": request.mode,
        "clock_source": request.output.output_id
        if request.mode == "media_synchronized"
        else "controller",
        "duration_ms": request.duration_ms,
        "resources": [binding.resource],
        "completion": {"policy": request.completion_policy},
        "media": [],
        "lighting": [],
    }
    scene[key] = [output]
    document["scenes"].append(scene)
    _import_binding(document, draft, binding)
    return DraftProject.model_validate(document)


def add_scene_output(
    draft: DraftProject, scene_id: str, request: AddSceneOutputRequest, binding: ResourceBinding
) -> DraftProject:
    original = next((scene for scene in draft.scenes if scene.id == scene_id), None)
    if original is None:
        raise ValueError("Scene does not exist.")
    scene = original.model_dump(mode="json")
    if original.schema_version == 1:
        scene.update(
            schema_version=2,
            clock_source="media",
            media=[{"id": "media", **scene["media"]}],
            lighting=[{"id": "lighting", **scene["lighting"]}],
        )
    outputs = [*scene["media"], *scene["lighting"]]
    if any(output["id"] == request.output_id for output in outputs):
        raise ValueError("Output ID already exists in this scene.")
    if any(output["resource"] == binding.resource for output in outputs):
        raise ValueError("This resource already has an output in this scene.")
    if binding.kind is ResourceKind.MEDIA_AGENT:
        saved = {item.resource: item for item in draft.installation.resources}
        for existing_output in scene["media"]:
            existing_binding = saved.get(existing_output["resource"])
            if existing_binding is None:
                raise ValueError("An existing media output has no saved resource binding.")
            if existing_binding.target == binding.target:
                raise ValueError("This media agent already has an output in this scene.")
    key, output = _output_document(request, binding, original.duration_ms)
    scene[key].append(output)
    if binding.resource not in scene["resources"]:
        scene["resources"].append(binding.resource)
    if request.make_clock_source:
        if key != "media":
            raise ValueError("Only a media output can become the scene's media clock.")
        scene.update(mode="media_synchronized", clock_source=request.output_id)
    SceneDefinitionV2.model_validate(scene)
    document = draft.model_dump(mode="json")
    document["scenes"] = [
        scene if existing.id == scene_id else existing.model_dump(mode="json")
        for existing in draft.scenes
    ]
    _import_binding(document, draft, binding)
    return DraftProject.model_validate(document)


def _import_binding(
    document: dict[str, Any], draft: DraftProject, binding: ResourceBinding
) -> None:
    if not any(item.resource == binding.resource for item in draft.installation.resources):
        document["installation"]["resources"].append(binding.model_dump(mode="json"))


def save_scene_edit(
    services: "GuiServices",
    *,
    project_id: str,
    base_revision: int,
    request: CreateSceneRequest | AddSceneOutputRequest,
    actor: str,
    role: Role,
    scene_id: str | None = None,
) -> StoredRevision:
    stored = services.repository.get_revision(project_id)
    if stored.revision != base_revision:
        raise RevisionConflictError(
            requested_revision=base_revision,
            current_revision=stored.revision,
            conflicting_paths=("/scenes",),
        )
    if stored.project.archived_at is not None:
        raise ValueError("Archived shows cannot be edited.")
    resource = (
        request.output.resource if isinstance(request, CreateSceneRequest) else request.resource
    )
    options = scene_authoring_options(services, project_id)
    binding = next((item for item in options.resources if item.resource == resource), None)
    if binding is None:
        raise ValueError("Resource is not available to this show's installation.")
    if isinstance(request, CreateSceneRequest):
        candidate = create_scene(stored.draft, request, binding)
    else:
        if scene_id is None:
            raise ValueError("An existing scene is required when adding an output.")
        candidate = add_scene_output(stored.draft, scene_id, request, binding)
    return services.repository.update_draft(
        project_id=project_id,
        expected_revision=base_revision,
        draft=candidate,
        actor=actor,
        role=role,
        occurred_at=datetime.now(UTC).isoformat(),
    )


class SceneDeletionError(ValueError):
    pass


_TRACK_KEYS = ("timeline_transform_layers", "timeline_cues")


def _scene_document(draft: DraftProject, scene_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the draft and scene documents, upgrading v1 scenes and their track references."""
    original = next((scene for scene in draft.scenes if scene.id == scene_id), None)
    if original is None:
        raise ValueError("Scene does not exist.")
    document = draft.model_dump(mode="json")
    scene = next(item for item in document["scenes"] if item["id"] == scene_id)
    if original.schema_version == 1:
        scene.update(
            schema_version=2,
            clock_source="media",
            media=[{"id": "media", **scene["media"]}],
            lighting=[{"id": "lighting", **scene["lighting"]}],
        )
        _rewrite_tracks(
            document,
            scene_id,
            {f"media:{scene_id}": "media:media", f"lighting:{scene_id}": "lighting:lighting"},
        )
    return document, scene


def _rewrite_tracks(document: dict[str, Any], scene_id: str, renames: dict[str, str]) -> None:
    metadata = document["editor_metadata"]
    for key in _TRACK_KEYS:
        values = metadata.get(key)
        items = values.get(scene_id) if isinstance(values, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("track_id") in renames:
                item["track_id"] = renames[item["track_id"]]
    schedule = metadata.get("timeline_sync_schedule")
    for entry in schedule if isinstance(schedule, list) else []:
        if (
            isinstance(entry, dict)
            and entry.get("scene_id") == scene_id
            and entry.get("editing_space_id") == scene_id
            and entry.get("track_id") in renames
        ):
            entry["track_id"] = renames[entry["track_id"]]


def _find_output(scene: dict[str, Any], output_id: str) -> tuple[str, dict[str, Any]]:
    for key in ("media", "lighting"):
        for output in scene[key]:
            if output["id"] == output_id:
                return key, output
    raise ValueError(f"Output {output_id} does not exist in this scene.")


def _validated(document: dict[str, Any], scene: dict[str, Any]) -> DraftProject:
    SceneDefinitionV2.model_validate(scene)
    return DraftProject.model_validate(document)


def rename_scene_output(
    draft: DraftProject, scene_id: str, request: RenameSceneOutputRequest
) -> DraftProject:
    document, scene = _scene_document(draft, scene_id)
    key, output = _find_output(scene, request.output_id)
    if request.new_output_id == request.output_id:
        raise ValueError("Enter a different output ID.")
    if any(item["id"] == request.new_output_id for item in [*scene["media"], *scene["lighting"]]):
        raise ValueError("Output ID already exists in this scene.")
    output["id"] = request.new_output_id
    if key == "media" and scene.get("clock_source") == request.output_id:
        scene["clock_source"] = request.new_output_id
    _rewrite_tracks(
        document,
        scene_id,
        {f"{key}:{request.output_id}": f"{key}:{request.new_output_id}"},
    )
    return _validated(document, scene)


def remove_scene_output(
    draft: DraftProject, scene_id: str, request: RemoveSceneOutputRequest
) -> DraftProject:
    document, scene = _scene_document(draft, scene_id)
    key, output = _find_output(scene, request.output_id)
    if len(scene["media"]) + len(scene["lighting"]) == 1:
        raise ValueError("A scene needs at least one output. Delete the scene instead.")
    blockers: list[str] = []
    if (
        key == "media"
        and scene.get("mode") == "media_synchronized"
        and scene.get("clock_source") == request.output_id
    ):
        blockers.append(
            "it is the scene's media clock (choose another clock source or the controller "
            "timer in Scene settings)"
        )
    track_id = f"{key}:{request.output_id}"
    metadata = document["editor_metadata"]
    for metadata_key, label in (
        ("timeline_transform_layers", "media layer"),
        ("timeline_cues", "synchronized cue"),
    ):
        values = metadata.get(metadata_key)
        items = values.get(scene_id) if isinstance(values, dict) else None
        names = [
            str(item.get("name") or item.get("id"))
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict) and item.get("track_id") == track_id
        ]
        if names:
            blockers.append(f"{label}{'s' if len(names) > 1 else ''} {', '.join(names)}")
    if blockers:
        raise ValueError(
            f"Output {request.output_id} cannot be removed while used by: "
            + "; ".join(blockers)
            + ". Update these first."
        )
    scene[key] = [item for item in scene[key] if item["id"] != request.output_id]
    remaining = {item["resource"] for item in [*scene["media"], *scene["lighting"]]}
    if output["resource"] not in remaining:
        scene["resources"] = [item for item in scene["resources"] if item != output["resource"]]
    return _validated(document, scene)


def save_scene_output_change(
    services: "GuiServices",
    *,
    project_id: str,
    scene_id: str,
    base_revision: int,
    request: RenameSceneOutputRequest | RemoveSceneOutputRequest,
    actor: str,
    role: Role,
) -> StoredRevision:
    stored = services.repository.get_revision(project_id)
    if stored.revision != base_revision:
        raise RevisionConflictError(
            requested_revision=base_revision,
            current_revision=stored.revision,
            conflicting_paths=("/scenes",),
        )
    if stored.project.archived_at is not None:
        raise ValueError("Archived shows cannot be edited.")
    candidate = (
        rename_scene_output(stored.draft, scene_id, request)
        if isinstance(request, RenameSceneOutputRequest)
        else remove_scene_output(stored.draft, scene_id, request)
    )
    return services.repository.update_draft(
        project_id=project_id,
        expected_revision=base_revision,
        draft=candidate,
        actor=actor,
        role=role,
        occurred_at=datetime.now(UTC).isoformat(),
    )


def delete_scene(draft: DraftProject, scene_id: str) -> DraftProject:
    if not any(scene.id == scene_id for scene in draft.scenes):
        raise SceneDeletionError("Scene does not exist.")
    if len(draft.scenes) == 1:
        raise SceneDeletionError("The last scene cannot be deleted. Keep at least one scene.")
    references: list[str] = []
    for scene in draft.scenes:
        if scene.id == scene_id:
            continue
        if scene.failure_scene_id == scene_id:
            references.append(f"fallback for {scene.name} ({scene.id})")
        if scene.completion.target_scene_id == scene_id:
            references.append(f"completion transition from {scene.name} ({scene.id})")
    if draft.program is not None:
        if draft.program.initial_scene_id == scene_id:
            references.append("automatic program starting scene")
        for pool in draft.program.pools:
            if any(entry.scene_id == scene_id for entry in pool.scenes):
                references.append(f"automatic program pool {pool.id}")
    if draft.opportunities is not None:
        for opportunity in draft.opportunities.opportunities:
            if opportunity.scene_id == scene_id:
                references.append(f"visitor response {opportunity.id}")
    if draft.encounter is not None:
        if draft.encounter.ambient_scene_id == scene_id:
            references.append("encounter ambient scene")
        for transition in draft.encounter.transitions:
            if transition.scene_id == scene_id:
                references.append(
                    f"encounter transition {transition.event_type} to {transition.to_state}"
                )
    if references:
        raise SceneDeletionError(
            "Scene is still used by: " + "; ".join(references) + ". Update these references first."
        )
    document = draft.model_dump(mode="json")
    document["scenes"] = [
        scene.model_dump(mode="json") for scene in draft.scenes if scene.id != scene_id
    ]
    metadata = document["editor_metadata"]
    compositions = metadata.get("compositions", {})
    for key in (
        "scene_organization",
        "scene_preset_usage",
        "scene_compositions",
        "composition_instances",
        "timeline_views",
        "timeline_tracks",
        "timeline_markers",
        "timeline_cues",
        "timeline_transform_layers",
        "timeline_property_keyframes",
        "lighting_author_layers",
    ):
        values = metadata.get(key)
        if values is None:
            continue
        if not isinstance(values, dict):
            raise SceneDeletionError(f"Scene metadata {key} must be an object.")
        if (
            (key.startswith("timeline_") or key == "lighting_author_layers")
            and isinstance(compositions, dict)
            and scene_id in compositions
        ):
            continue
        values.pop(scene_id, None)
    schedule = metadata.get("timeline_sync_schedule")
    if schedule is not None:
        if not isinstance(schedule, list):
            raise SceneDeletionError("Scene metadata timeline_sync_schedule must be a list.")
        metadata["timeline_sync_schedule"] = [
            entry
            for entry in schedule
            if not isinstance(entry, dict) or entry.get("scene_id") != scene_id
        ]
    return DraftProject.model_validate(document)
