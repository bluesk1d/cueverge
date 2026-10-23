from cueverge.gui.contracts import DraftProject


class SceneDeletionError(ValueError):
    pass


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
