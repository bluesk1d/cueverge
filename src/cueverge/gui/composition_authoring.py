"""Draft edits that delete compositions or take them out of a scene.

Every reference is removed in one edit so the result validates: parent
composition children, scene roots, scene instances, nested composition media
layers, the composition's own timeline data and stale sync-schedule entries.
"""

from typing import Any, cast

from cueverge.gui.contracts import DraftProject

_SPACE_KEYED_METADATA = (
    "timeline_views",
    "timeline_tracks",
    "timeline_markers",
    "timeline_cues",
    "timeline_transform_layers",
    "timeline_property_keyframes",
    "lighting_author_layers",
)


class CompositionEditError(ValueError):
    pass


def composition_usage(draft: DraftProject, composition_id: str) -> list[str]:
    """Human-readable places that use a composition, for confirmation and errors."""
    metadata = cast(dict[str, Any], draft.model_dump(mode="json")["editor_metadata"])
    compositions = _object(metadata.get("compositions"))
    if composition_id not in compositions:
        raise CompositionEditError("Composition does not exist.")
    scene_names = {scene.id: scene.name for scene in draft.scenes}
    usage: list[str] = []
    for parent_id, parent in compositions.items():
        if composition_id in _list(_object(parent).get("children")):
            usage.append(f"composition {_object(parent).get('name', parent_id)}")
    for scene_id, roots in _object(metadata.get("scene_compositions")).items():
        if composition_id in _list(roots):
            usage.append(f"scene {scene_names.get(scene_id, scene_id)}")
    for scene_id, instances in _object(metadata.get("composition_instances")).items():
        for instance in _list(instances):
            if _object(instance).get("composition_id") == composition_id:
                usage.append(
                    f"scene {scene_names.get(scene_id, scene_id)} instance "
                    f"{_object(instance).get('id')}"
                )
    for space_id, layers in _object(metadata.get("timeline_transform_layers")).items():
        for layer in _list(layers):
            record = _object(layer)
            if record.get("kind") == "composition" and record.get("source_id") == composition_id:
                owner = (
                    f"scene {scene_names[space_id]}"
                    if space_id in scene_names
                    else f"composition {_object(compositions.get(space_id)).get('name', space_id)}"
                )
                usage.append(f"{owner} media layer {record.get('name', record.get('id'))}")
    return list(dict.fromkeys(usage))


def delete_composition(draft: DraftProject, composition_id: str) -> DraftProject:
    """Delete a composition and remove it from everywhere it is used."""
    document = draft.model_dump(mode="json")
    metadata = cast(dict[str, Any], document["editor_metadata"])
    compositions = _object(metadata.get("compositions"))
    if composition_id not in compositions:
        raise CompositionEditError("Composition does not exist.")
    scene_ids = {scene.id for scene in draft.scenes}
    before = {scene_id: _reachable_spaces(metadata, scene_id) for scene_id in scene_ids}
    del compositions[composition_id]
    for parent in compositions.values():
        record = _object(parent)
        if isinstance(record.get("children"), list):
            record["children"] = [child for child in record["children"] if child != composition_id]
    _filter_scene_maps(metadata, composition_id, scene_id=None)
    for key in _SPACE_KEYED_METADATA:
        values = metadata.get(key)
        if isinstance(values, dict):
            values.pop(composition_id, None)
    _drop_nested_layers(metadata, composition_id, space_ids=None)
    _prune_sync_schedule(metadata, before)
    return DraftProject.model_validate(document)


def remove_composition_from_scene(
    draft: DraftProject,
    scene_id: str,
    composition_id: str,
) -> DraftProject:
    """Take a composition out of one scene, keeping it for reuse elsewhere."""
    if not any(scene.id == scene_id for scene in draft.scenes):
        raise CompositionEditError("Scene does not exist.")
    document = draft.model_dump(mode="json")
    metadata = cast(dict[str, Any], document["editor_metadata"])
    if composition_id not in _object(metadata.get("compositions")):
        raise CompositionEditError("Composition does not exist.")
    before = _scene_reference_count(metadata, scene_id, composition_id)
    if before == 0:
        raise CompositionEditError("Composition is not used directly by this scene.")
    reachable_before = {scene_id: _reachable_spaces(metadata, scene_id)}
    _filter_scene_maps(metadata, composition_id, scene_id=scene_id)
    _drop_nested_layers(metadata, composition_id, space_ids={scene_id})
    _prune_sync_schedule(metadata, reachable_before)
    return DraftProject.model_validate(document)


def _scene_reference_count(metadata: dict[str, Any], scene_id: str, composition_id: str) -> int:
    roots = _list(_object(metadata.get("scene_compositions")).get(scene_id))
    instances = _list(_object(metadata.get("composition_instances")).get(scene_id))
    layers = _list(_object(metadata.get("timeline_transform_layers")).get(scene_id))
    return int(
        roots.count(composition_id)
        + sum(_object(item).get("composition_id") == composition_id for item in instances)
        + sum(
            _object(item).get("kind") == "composition"
            and _object(item).get("source_id") == composition_id
            for item in layers
        )
    )


def _filter_scene_maps(
    metadata: dict[str, Any],
    composition_id: str,
    *,
    scene_id: str | None,
) -> None:
    roots = metadata.get("scene_compositions")
    if isinstance(roots, dict):
        for key, ids in roots.items():
            if (scene_id is None or key == scene_id) and isinstance(ids, list):
                roots[key] = [item for item in ids if item != composition_id]
    instances = metadata.get("composition_instances")
    if isinstance(instances, dict):
        for key, values in instances.items():
            if (scene_id is None or key == scene_id) and isinstance(values, list):
                instances[key] = [
                    item for item in values if _object(item).get("composition_id") != composition_id
                ]


def _drop_nested_layers(
    metadata: dict[str, Any],
    composition_id: str,
    *,
    space_ids: set[str] | None,
) -> None:
    layers = metadata.get("timeline_transform_layers")
    if not isinstance(layers, dict):
        return
    for space_id, values in layers.items():
        if (space_ids is None or space_id in space_ids) and isinstance(values, list):
            layers[space_id] = [
                item
                for item in values
                if not (
                    _object(item).get("kind") == "composition"
                    and _object(item).get("source_id") == composition_id
                )
            ]


def _prune_sync_schedule(metadata: dict[str, Any], before: dict[str, set[str]]) -> None:
    """Drop schedule entries for spaces this edit made unreachable from their scene."""
    schedule = metadata.get("timeline_sync_schedule")
    if not isinstance(schedule, list):
        return
    removed = {
        scene_id: spaces - _reachable_spaces(metadata, scene_id)
        for scene_id, spaces in before.items()
    }
    metadata["timeline_sync_schedule"] = [
        entry
        for entry in schedule
        if not isinstance(entry, dict)
        or entry.get("editing_space_id") not in removed.get(str(entry.get("scene_id")), set())
    ]


def _reachable_spaces(metadata: dict[str, Any], scene_id: str) -> set[str]:
    compositions = _object(metadata.get("compositions"))
    layers = _object(metadata.get("timeline_transform_layers"))
    pending = [
        *(str(item) for item in _list(_object(metadata.get("scene_compositions")).get(scene_id))),
        *(
            str(_object(item).get("composition_id"))
            for item in _list(_object(metadata.get("composition_instances")).get(scene_id))
        ),
    ]
    reachable = {scene_id}
    for layer in _list(layers.get(scene_id)):
        if _object(layer).get("kind") == "composition":
            pending.append(str(_object(layer).get("source_id")))
    while pending:
        current = pending.pop()
        if current in reachable or current not in compositions:
            continue
        reachable.add(current)
        pending.extend(
            str(child) for child in _list(_object(compositions[current]).get("children"))
        )
        for layer in _list(layers.get(current)):
            if _object(layer).get("kind") == "composition":
                pending.append(str(_object(layer).get("source_id")))
    return reachable


def _object(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
