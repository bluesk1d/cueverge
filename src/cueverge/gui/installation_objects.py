"""Installation, show and scene object services shared by the GUI router and MCP tools.

Scopes (see object_hierarchy):
* installation: wiring segments + reusable fixtures/paths, inherited by every show.
* show: fixtures/paths owned by one show.
* scene: the objects a scene uses; lighting may only target those.
"""

import logging
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import JsonValue, TypeAdapter

from cueverge.core.models import (
    InstallationDefinition,
    PixelFixture,
    PixelPath,
    PixelSegment,
    ResourceBinding,
    ResourceKind,
    SegmentReference,
)
from cueverge.gui.contracts import (
    DraftProject,
    InstallationObjectDeleteRequest,
    InstallationObjectDetails,
    InstallationObjectEditRequest,
    InstallationObjectGeometry,
    InstallationObjectInventory,
    InstallationObjectRecord,
    InstallationObjectsInventory,
    InstallationObjectTraversal,
    InstallationPixelResource,
    ObjectShowUsage,
    Role,
    SceneObjectEntry,
    SceneObjectReference,
    SceneObjectsResponse,
    ShowObjectSummary,
    ShowSummaryDevice,
    ShowSummaryObject,
    ShowSummaryScene,
)
from cueverge.gui.installation_geometry import StoredInstallationGeometry
from cueverge.gui.object_hierarchy import (
    DETAILS_KEY,
    INHERIT_FLAG,
    SCENE_OBJECTS_KEY,
    InheritanceError,
    InstallationObjectsDocument,
    ObjectKey,
    ResourceObjects,
    adopt_draft,
    explicit_scene_objects,
    inherited_keys,
    inherits,
    installation_keys,
    object_usage,
    promote,
    remove_unused_show_objects,
    scene_objects,
    show_owned,
    store_from_drafts,
    targeted_objects,
)
from cueverge.gui.repository import RevisionConflictError, StoredRevision

if TYPE_CHECKING:
    from cueverge.gui.service import GuiServices

LOGGER = logging.getLogger(__name__)
_details_adapter = TypeAdapter(tuple[InstallationObjectDetails, ...])
ObjectKind = Literal["segment", "fixture", "path"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _details(draft: DraftProject) -> tuple[InstallationObjectDetails, ...]:
    return _details_adapter.validate_python(draft.editor_metadata.get(DETAILS_KEY, []))


def _references(
    draft: DraftProject, resource: str, kind: ObjectKind, object_id: str
) -> tuple[str, ...]:
    found: set[str] = set()

    def visit(value: JsonValue, path: str, owner: str | None = None) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}/{index}", owner)
        elif isinstance(value, dict):
            binding = value.get("resource", value.get("resource_binding", owner))
            context = binding if isinstance(binding, str) else owner
            target = value.get("target")
            matches = (
                isinstance(target, dict)
                and target.get("kind") == kind
                and target.get("id") == object_id
            ) or (value.get("target_kind") == kind and value.get("target_id") == object_id)
            matches = (
                matches
                or (kind == "segment" and value.get("segment") == object_id)
                or (kind == "path" and value.get("path_id") == object_id)
            )
            # Older timeline tracks bind the object ID rather than its output resource.
            matches = matches or (
                value.get("kind") == kind and value.get("resource_binding") == object_id
            )
            if matches and (context in (None, resource, object_id)):
                found.add(path)
            for key, item in value.items():
                visit(item, f"{path}/{key}", context)

    for scene in draft.scenes:
        visit(scene.model_dump(mode="json"), f"/scenes/{scene.id}")
    # Unknown/custom metadata is retained, but only known authoring surfaces are references.
    for key in (
        "lighting_author_layers",
        "timeline_tracks",
        "timeline_cues",
        "timeline_cue_presets",
        "scene_presets",
        "ambient_lighting",
    ):
        visit(draft.editor_metadata.get(key), f"/editor_metadata/{key}")
    if kind == "segment":
        binding = next(item for item in draft.installation.resources if item.resource == resource)
        for section, targets in (("fixtures", binding.fixtures), ("paths", binding.paths)):
            for target in targets:
                if any(reference.segment == object_id for reference in target.segments):
                    found.add(f"/installation/resources/{resource}/{section}/{target.id}")
    return tuple(sorted(found))


def _layout(
    services: "GuiServices", installation_id: str | None
) -> tuple[tuple[StoredInstallationGeometry, ...], dict[str, str]]:
    if installation_id != services.repository.get_runtime_installation().id:
        return (), {}
    geometry = services.installation_geometry.list(installation_id)
    # Read only the assignment, not per-pixel surface expansions.
    with sqlite3.connect(services.repository.database_path) as connection:
        assignments: dict[str, str] = {}
        for semantic_id, resource in connection.execute(
            """SELECT g.semantic_id, s.resource FROM lighting_surfaces s
                JOIN installation_geometry g ON g.id = s.geometry_id
                WHERE g.installation_id = ?""",
            (installation_id,),
        ):
            previous = assignments.get(semantic_id)
            if previous is not None and previous != resource:
                raise ValueError("Linked layout views disagree on their output assignment.")
            assignments[semantic_id] = resource
        resources = {
            shape.id: assignments[shape.semantic_id]
            for shape in geometry
            if shape.kind in {"pixel_segment", "point_light"} and shape.semantic_id in assignments
        }
    return geometry, resources


def _geometry_groups(
    geometry: tuple[StoredInstallationGeometry, ...], resources: dict[str, str]
) -> tuple[dict[str, list[StoredInstallationGeometry]], tuple[InstallationObjectGeometry, ...]]:
    grouped: dict[str, list[StoredInstallationGeometry]] = {}
    for shape in geometry:
        if shape.kind in {"pixel_segment", "point_light", "fixture", "path"}:
            grouped.setdefault(shape.semantic_id, []).append(shape)
    return grouped, tuple(
        InstallationObjectGeometry(
            semantic_id=semantic_id,
            name=shapes[0].name,
            kind=shapes[0].kind,
            resource=resources.get(shapes[0].id),
            geometry_ids=tuple(shape.id for shape in shapes),
        )
        for semantic_id, shapes in grouped.items()
    )


def _record(
    binding: ResourceBinding,
    kind: ObjectKind,
    object_id: str,
    members: tuple[SegmentReference, ...],
    precedence: int,
    *,
    name: str,
    links: tuple[str, ...],
    geometry: tuple[StoredInstallationGeometry, ...],
    geometry_resources: dict[str, str],
    grouped: dict[str, list[StoredInstallationGeometry]],
    owner: Literal["installation", "show"],
    references: tuple[str, ...] = (),
    scene_ids: tuple[str, ...] = (),
    used_by: tuple[ObjectShowUsage, ...] = (),
) -> InstallationObjectRecord:
    segments = {item.id: item for item in binding.segments}
    matched = {shape.id for semantic_id in links for shape in grouped.get(semantic_id, [])}
    traversal: list[InstallationObjectTraversal] = []
    for member in members:
        indices = member.pixel_range(segments[member.segment])
        first, last = indices[0], indices[-1]
        traversal.append(
            InstallationObjectTraversal(
                segment=member.segment,
                first_pixel=first,
                last_pixel=last,
                pixel_count=len(indices),
                reverse=member.reverse,
            )
        )
        low = min(first, last)
        matched.update(
            shape.id
            for shape in geometry
            if shape.kind in {"pixel_segment", "point_light"}
            and geometry_resources.get(shape.id) == binding.resource
            and shape.start_index == low
            and shape.pixel_count == len(indices)
        )
    return InstallationObjectRecord(
        resource=binding.resource,
        kind=kind,
        id=object_id,
        name=name,
        geometry_semantic_ids=links,
        segments=members,
        precedence=precedence,
        pixel_count=sum(item.pixel_count for item in traversal),
        traversal=tuple(traversal),
        references=references,
        geometry_ids=tuple(sorted(matched)),
        missing_geometry_semantic_ids=tuple(
            semantic_id for semantic_id in links if semantic_id not in grouped
        ),
        owner=owner,
        scene_ids=scene_ids,
        used_by=used_by,
    )


def _targets(
    binding: ResourceBinding,
) -> list[tuple[ObjectKind, str, tuple[SegmentReference, ...], int]]:
    targets: list[tuple[ObjectKind, str, tuple[SegmentReference, ...], int]] = [
        ("segment", item.id, (SegmentReference(segment=item.id),), 0) for item in binding.segments
    ]
    targets.extend(
        ("fixture", item.id, item.segments, item.precedence) for item in binding.fixtures
    )
    targets.extend(("path", item.id, item.segments, 0) for item in binding.paths)
    return targets


def _installation_id(draft: DraftProject) -> str | None:
    raw = draft.editor_metadata.get("installation_id")
    return raw if isinstance(raw, str) else None


def _store(
    services: "GuiServices", installation_id: str | None
) -> tuple[int, InstallationObjectsDocument]:
    stored = (
        services.repository.get_installation_objects(installation_id) if installation_id else None
    )
    return stored if stored is not None else (0, InstallationObjectsDocument())


def _names(draft: DraftProject) -> dict[ObjectKey, str]:
    names: dict[ObjectKey, str] = {}
    raw = draft.editor_metadata.get("inherited_objects")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                key = (str(item.get("resource")), str(item.get("kind")), str(item.get("id")))
                names[key] = str(item.get("name"))
    for detail in _details(draft):
        names[(detail.resource, detail.kind, detail.id)] = detail.name
    return names


def _name(names: dict[ObjectKey, str], key: ObjectKey) -> str:
    return names.get(key) or key[2].replace("-", " ")


def _bound_shows(
    services: "GuiServices", installation_id: str, *, inheriting: bool = True
) -> list[StoredRevision]:
    shows: list[StoredRevision] = []
    for project in services.repository.list_projects():
        if project.archived_at is not None:
            continue
        stored = services.repository.get_revision(project.id)
        if _installation_id(stored.draft) != installation_id:
            continue
        if inheriting and not inherits(stored.draft):
            continue
        shows.append(stored)
    return shows


# --- Show scope -------------------------------------------------------------------------


def installation_object_inventory(
    services: "GuiServices", project_id: str
) -> InstallationObjectInventory:
    """A show's objects: inherited (installation) and its own, with scene usage."""
    stored = services.repository.get_revision(project_id)
    draft = stored.draft
    installation_id = _installation_id(draft)
    geometry, geometry_resources = _layout(services, installation_id)
    grouped, shapes = _geometry_groups(geometry, geometry_resources)
    details: dict[ObjectKey, InstallationObjectDetails] = {
        (item.resource, item.kind, item.id): item for item in _details(draft)
    }
    names = _names(draft)
    inherited = inherited_keys(draft) if inherits(draft) else set()
    usage = object_usage(draft)
    store_details: dict[ObjectKey, InstallationObjectDetails] = {
        (item.resource, item.kind, item.id): item
        for item in _store(services, installation_id)[1].details
    }
    bindings = tuple(
        item for item in draft.installation.resources if item.kind is ResourceKind.PIXEL_OUTPUT
    )
    records: list[InstallationObjectRecord] = []
    for binding in bindings:
        for kind, object_id, members, precedence in _targets(binding):
            key: ObjectKey = (binding.resource, kind, object_id)
            detail = details.get(key) or store_details.get(key)
            records.append(
                _record(
                    binding,
                    kind,
                    object_id,
                    members,
                    precedence,
                    name=_name(names, key),
                    links=detail.geometry_semantic_ids if detail else (),
                    geometry=geometry,
                    geometry_resources=geometry_resources,
                    grouped=grouped,
                    owner="installation" if key in inherited else "show",
                    references=_references(draft, binding.resource, kind, object_id),
                    scene_ids=tuple(usage.get(key, ())),
                )
            )
    return InstallationObjectInventory(
        project_id=project_id,
        project_name=stored.project.name,
        revision=stored.revision,
        archived=stored.project.archived_at is not None,
        installation_id=installation_id,
        layout_available=installation_id == services.repository.get_runtime_installation().id,
        resources=bindings,
        objects=tuple(records),
        geometry=shapes,
        inherits=inherits(draft),
    )


def _validate_links(
    services: "GuiServices",
    installation_id: str | None,
    request: InstallationObjectEditRequest,
) -> None:
    geometry, resources = _layout(services, installation_id)
    for semantic_id in request.geometry_semantic_ids:
        linked = [shape for shape in geometry if shape.semantic_id == semantic_id]
        if not linked or any(
            shape.kind not in {"pixel_segment", "point_light", request.kind}
            or (
                shape.kind in {"pixel_segment", "point_light"}
                and resources.get(shape.id) != request.resource
            )
            for shape in linked
        ):
            raise ValueError(
                "Layout links must reference this installation's matching object "
                "geometry or pixel segments assigned to this output."
            )


def _definition(request: InstallationObjectEditRequest) -> PixelFixture | PixelPath:
    if request.kind == "fixture":
        return PixelFixture(id=request.id, segments=request.segments, precedence=request.precedence)
    return PixelPath(id=request.id, segments=request.segments)


def save_installation_object(
    services: "GuiServices",
    *,
    project_id: str,
    base_revision: int,
    request: InstallationObjectEditRequest | InstallationObjectDeleteRequest,
    action: Literal["create", "update", "delete"],
    actor: str,
    role: Role,
) -> StoredRevision:
    """Create, change or delete a fixture/path owned by one show."""
    stored = services.repository.get_revision(project_id)
    if stored.revision != base_revision:
        raise RevisionConflictError(
            requested_revision=base_revision,
            current_revision=stored.revision,
            conflicting_paths=("/installation/resources",),
        )
    if stored.project.archived_at is not None:
        raise ValueError("Archived shows cannot be edited.")
    draft = stored.draft
    key = (request.resource, request.kind, request.id)
    if inherits(draft) and key in inherited_keys(draft):
        raise ValueError(
            f"{request.kind} {request.id} belongs to the installation; edit it under "
            "Installation → Objects."
        )
    binding = next(
        (item for item in draft.installation.resources if item.resource == request.resource), None
    )
    if binding is None or binding.kind is not ResourceKind.PIXEL_OUTPUT:
        raise ValueError("Choose a saved pixel-output resource from this show.")
    section = "fixtures" if request.kind == "fixture" else "paths"
    targets = binding.fixtures if request.kind == "fixture" else binding.paths
    exists = any(item.id == request.id for item in targets)
    if action == "create" and exists:
        raise ValueError("Object ID already exists on this output.")
    if action != "create" and not exists:
        raise ValueError("Object does not exist. IDs cannot be renamed during editing.")
    if action == "delete":
        references = _references(draft, request.resource, request.kind, request.id)
        if references:
            raise ValueError("Object is still referenced: " + ", ".join(references))
    elif not isinstance(request, InstallationObjectEditRequest):
        raise ValueError("An object definition is required.")
    else:
        _validate_links(services, _installation_id(draft), request)
        if request.scene_id is not None and not any(
            scene.id == request.scene_id for scene in draft.scenes
        ):
            raise ValueError("Choose a scene of this show.")
    document = draft.model_dump(mode="json")
    resource_document = next(
        item
        for item in document["installation"]["resources"]
        if item["resource"] == request.resource
    )
    definitions = [item for item in resource_document[section] if item["id"] != request.id]
    if isinstance(request, InstallationObjectEditRequest) and action != "delete":
        definition = _definition(request).model_dump(mode="json")
        # Replace in place so editing does not reorder otherwise unchanged definitions.
        if exists:
            definitions = [
                definition if item["id"] == request.id else item
                for item in resource_document[section]
            ]
        else:
            definitions.append(definition)
    resource_document[section] = definitions
    details = [item for item in _details(draft) if (item.resource, item.kind, item.id) != key]
    metadata = document["editor_metadata"]
    if isinstance(request, InstallationObjectEditRequest) and action != "delete":
        details.append(
            InstallationObjectDetails(
                resource=request.resource,
                kind=request.kind,
                id=request.id,
                name=request.name.strip(),
                geometry_semantic_ids=request.geometry_semantic_ids,
            )
        )
        if action == "create" and request.scene_id is not None:
            lists = metadata.setdefault(SCENE_OBJECTS_KEY, {})
            lists.setdefault(request.scene_id, []).append(
                {"resource": request.resource, "kind": request.kind, "id": request.id}
            )
    if action == "delete" and isinstance(metadata.get(SCENE_OBJECTS_KEY), dict):
        metadata[SCENE_OBJECTS_KEY] = {
            scene_id: [
                item
                for item in items
                if (item.get("resource"), item.get("kind"), item.get("id")) != key
            ]
            for scene_id, items in metadata[SCENE_OBJECTS_KEY].items()
        }
    metadata[DETAILS_KEY] = [item.model_dump(mode="json") for item in details]
    candidate = DraftProject.model_validate(document)
    return services.repository.update_draft(
        project_id=project_id,
        expected_revision=base_revision,
        draft=candidate,
        actor=actor,
        role=role,
        occurred_at=_now(),
    )


def remove_unused_objects(
    services: "GuiServices", *, project_id: str, base_revision: int, actor: str, role: Role
) -> tuple[StoredRevision, list[ObjectKey]]:
    stored = services.repository.get_revision(project_id)
    store = _store(services, _installation_id(stored.draft))[1]
    cleaned, removed = remove_unused_show_objects(stored.draft, store)
    if not removed:
        return stored, []
    return (
        services.repository.update_draft(
            project_id=project_id,
            expected_revision=base_revision,
            draft=cleaned,
            actor=actor,
            role=role,
            occurred_at=_now(),
        ),
        removed,
    )


def promote_show_object(
    services: "GuiServices",
    *,
    project_id: str,
    base_revision: int,
    resource: str,
    kind: str,
    object_id: str,
    actor: str,
    role: Role,
) -> StoredRevision:
    """Make a show fixture/path an installation object every show can use."""
    stored = services.repository.get_revision(project_id)
    if stored.revision != base_revision:
        raise RevisionConflictError(
            requested_revision=base_revision,
            current_revision=stored.revision,
            conflicting_paths=("/installation/resources",),
        )
    draft = stored.draft
    installation_id = _installation_id(draft)
    if not inherits(draft) or installation_id is None:
        raise InheritanceError("Adopt installation objects for this show before promoting")
    revision, store = _store(services, installation_id)
    promoted = promote(store, draft, resource, kind, object_id)
    definition = next(
        item
        for entry in promoted.resources
        if entry.resource == resource
        for item in (entry.fixtures if kind == "fixture" else entry.paths)
        if item.id == object_id
    )
    for show in _bound_shows(services, installation_id):
        if show.project.id == project_id:
            continue
        for owned_resource, owned_kind, owned in show_owned(show.draft):
            if (owned_resource, owned_kind, owned.id) == (resource, kind, object_id) and (
                owned.model_dump() != definition.model_dump()
            ):
                raise InheritanceError(
                    f"Show {show.project.name} has a different {kind} {object_id}; rename or "
                    "align it before promoting"
                )
    services.repository.save_installation_objects(
        installation_id=installation_id,
        expected_revision=revision,
        document=promoted,
        actor=actor,
        role=role,
        occurred_at=_now(),
        action="installation.objects.promote",
    )
    return services.repository.get_revision(project_id)


# --- Scene scope ------------------------------------------------------------------------


def scene_object_view(
    services: "GuiServices", project_id: str, scene_id: str
) -> SceneObjectsResponse:
    stored = services.repository.get_revision(project_id)
    draft = stored.draft
    if not any(scene.id == scene_id for scene in draft.scenes):
        raise ValueError("Scene does not exist in this show")
    names = _names(draft)
    inherited = inherited_keys(draft) if inherits(draft) else set()
    targeted = targeted_objects(draft, scene_id)
    members = scene_objects(draft, scene_id)
    keys = {item.key for item in members}

    def entry(key: ObjectKey) -> SceneObjectEntry:
        return SceneObjectEntry(
            resource=key[0],
            kind=key[1],  # type: ignore[arg-type]
            id=key[2],
            name=_name(names, key),
            owner="installation" if key in inherited else "show",
            targeted=key in targeted,
        )

    available = [
        (binding.resource, kind, object_id)
        for binding in draft.installation.resources
        if binding.kind is ResourceKind.PIXEL_OUTPUT
        for kind, object_id, _, _ in _targets(binding)
    ]
    return SceneObjectsResponse(
        project_id=project_id,
        revision=stored.revision,
        scene_id=scene_id,
        objects=tuple(entry(item.key) for item in members),
        available=tuple(entry(key) for key in available if key not in keys),
    )


def set_scene_objects(
    services: "GuiServices",
    *,
    project_id: str,
    base_revision: int,
    scene_id: str,
    objects: Iterable[SceneObjectReference],
    actor: str,
    role: Role,
) -> StoredRevision:
    """Replace a scene's object list. Objects its effects target cannot be removed."""
    stored = services.repository.get_revision(project_id)
    draft = stored.draft
    if not any(scene.id == scene_id for scene in draft.scenes):
        raise ValueError("Scene does not exist in this show")
    requested = [(item.resource, item.kind, item.id) for item in objects]
    missing = sorted(targeted_objects(draft, scene_id) - set(requested))
    if missing:
        raise ValueError(
            "Effects in this scene still target "
            + ", ".join(f"{kind} {object_id}" for _, kind, object_id in missing)
            + "; change those effects first"
        )
    document = draft.model_dump(mode="json")
    lists = document["editor_metadata"].setdefault(SCENE_OBJECTS_KEY, {})
    lists[scene_id] = [
        {"resource": resource, "kind": kind, "id": object_id}
        for resource, kind, object_id in dict.fromkeys(requested)
    ]
    return services.repository.update_draft(
        project_id=project_id,
        expected_revision=base_revision,
        draft=DraftProject.model_validate(document),
        actor=actor,
        role=role,
        occurred_at=_now(),
    )


def show_object_summary(services: "GuiServices", project_id: str) -> ShowObjectSummary:
    """Read-only roll-up of devices and objects the show's scenes use, deduplicated."""
    stored = services.repository.get_revision(project_id)
    draft = stored.draft
    names = _names(draft)
    inherited = inherited_keys(draft) if inherits(draft) else set()
    kinds = {binding.resource: binding.kind.value for binding in draft.installation.resources}
    scenes: list[ShowSummaryScene] = []
    device_scenes: dict[str, list[str]] = {}
    object_scenes: dict[ObjectKey, list[str]] = {}
    for scene in draft.scenes:
        devices = tuple(sorted(set(scene.resources)))
        members = scene_objects(draft, scene.id)
        for resource in devices:
            device_scenes.setdefault(resource, []).append(scene.id)
        for item in members:
            object_scenes.setdefault(item.key, []).append(scene.id)
        scenes.append(
            ShowSummaryScene(
                scene_id=scene.id,
                name=scene.name,
                devices=devices,
                objects=tuple(
                    SceneObjectReference(resource=item.resource, kind=item.kind, id=item.id)
                    for item in members
                ),
            )
        )
    return ShowObjectSummary(
        project_id=project_id,
        revision=stored.revision,
        inherits=inherits(draft),
        scenes=tuple(scenes),
        devices=tuple(
            ShowSummaryDevice(
                resource=resource, kind=kinds.get(resource, "unknown"), scene_ids=tuple(ids)
            )
            for resource, ids in sorted(device_scenes.items())
        ),
        objects=tuple(
            ShowSummaryObject(
                resource=key[0],
                kind=key[1],  # type: ignore[arg-type]
                id=key[2],
                name=_name(names, key),
                owner="installation" if key in inherited else "show",
                scene_ids=tuple(ids),
            )
            for key, ids in sorted(object_scenes.items())
        ),
    )


# --- Installation scope -----------------------------------------------------------------


def _pixel_bindings(services: "GuiServices", installation_id: str) -> dict[str, ResourceBinding]:
    if installation_id != services.repository.get_runtime_installation().id:
        return {}
    return {
        binding.resource: binding
        for binding in services.runtime.catalog.installation.resources
        if binding.kind is ResourceKind.PIXEL_OUTPUT and binding.pixel_count is not None
    }


def installation_inventory(
    services: "GuiServices", installation_id: str
) -> InstallationObjectsInventory:
    revision, store = _store(services, installation_id)
    geometry, geometry_resources = _layout(services, installation_id)
    grouped, shapes = _geometry_groups(geometry, geometry_resources)
    usage: dict[ObjectKey, list[ObjectShowUsage]] = {}
    for show in _bound_shows(services, installation_id):
        for used_key, scene_ids in object_usage(show.draft).items():
            usage.setdefault(used_key, []).append(
                ObjectShowUsage(
                    project_id=show.project.id,
                    project_name=show.project.name,
                    scene_ids=tuple(scene_ids),
                )
            )
    entries = {entry.resource: entry for entry in store.resources}
    for resource, binding in _pixel_bindings(services, installation_id).items():
        entries.setdefault(
            resource, ResourceObjects(resource=resource, pixel_count=int(binding.pixel_count or 1))
        )
    details: dict[ObjectKey, InstallationObjectDetails] = {
        (item.resource, item.kind, item.id): item for item in store.details
    }
    records: list[InstallationObjectRecord] = []
    for entry in entries.values():
        binding = entry.binding("installation")
        for kind, object_id, members, precedence in _targets(binding):
            key: ObjectKey = (entry.resource, kind, object_id)
            detail = details.get(key)
            records.append(
                _record(
                    binding,
                    kind,
                    object_id,
                    members,
                    precedence,
                    name=store.name(*key),
                    links=detail.geometry_semantic_ids if detail else (),
                    geometry=geometry,
                    geometry_resources=geometry_resources,
                    grouped=grouped,
                    owner="installation",
                    used_by=tuple(usage.get(key, ())),
                )
            )
    return InstallationObjectsInventory(
        installation_id=installation_id,
        revision=revision,
        layout_available=installation_id == services.repository.get_runtime_installation().id,
        resources=tuple(
            InstallationPixelResource(
                resource=entry.resource, pixel_count=entry.pixel_count, segments=entry.segments
            )
            for entry in entries.values()
        ),
        objects=tuple(records),
        geometry=shapes,
    )


def _entry(
    services: "GuiServices", installation_id: str, store: InstallationObjectsDocument, resource: str
) -> ResourceObjects:
    entry = store.entry(resource)
    if entry is not None:
        return entry
    binding = _pixel_bindings(services, installation_id).get(resource)
    if binding is None or binding.pixel_count is None:
        raise ValueError("Choose a pixel output of this installation.")
    return ResourceObjects(resource=resource, pixel_count=binding.pixel_count)


def _with_entry(
    store: InstallationObjectsDocument, entry: ResourceObjects
) -> InstallationObjectsDocument:
    resources = [item for item in store.resources if item.resource != entry.resource]
    resources.append(entry)
    return store.model_copy(
        update={"resources": tuple(sorted(resources, key=lambda item: item.resource))}
    )


def save_installation_level_object(
    services: "GuiServices",
    *,
    installation_id: str,
    base_revision: int,
    request: InstallationObjectEditRequest | InstallationObjectDeleteRequest,
    action: Literal["create", "update", "delete"],
    actor: str,
    role: Role,
) -> int:
    """Create, change or delete a reusable installation fixture/path."""
    revision, store = _store(services, installation_id)
    if revision != base_revision:
        raise RevisionConflictError(
            requested_revision=base_revision,
            current_revision=revision,
            conflicting_paths=("/installation_objects",),
        )
    entry = _entry(services, installation_id, store, request.resource)
    key = (request.resource, request.kind, request.id)
    items = entry.fixtures if request.kind == "fixture" else entry.paths
    exists = any(item.id == request.id for item in items)
    if action == "create" and exists:
        raise ValueError("Object ID already exists on this output.")
    if action != "create" and not exists:
        raise ValueError("Object does not exist. IDs cannot be renamed during editing.")
    shows = _bound_shows(services, installation_id)
    if action == "create":
        clashes = [
            show.project.name
            for show in shows
            if any((r, k, item.id) == key for r, k, item in show_owned(show.draft))
        ]
        if clashes:
            raise InheritanceError(
                f"{', '.join(clashes)} already define {request.kind} {request.id}; promote it "
                "from that show or choose another ID"
            )
    if action == "delete":
        users = [
            f"{show.project.name} ({', '.join(scenes)})"
            for show in shows
            for object_key, scenes in object_usage(show.draft).items()
            if object_key == key
            and any(object_key in targeted_objects(show.draft, scene) for scene in scenes)
        ]
        if users:
            raise ValueError("Object is still used by effects in: " + "; ".join(users))
    remaining = [item for item in items if item.id != request.id]
    details = [item for item in store.details if (item.resource, item.kind, item.id) != key]
    if isinstance(request, InstallationObjectEditRequest) and action != "delete":
        _validate_links(services, installation_id, request)
        definition = _definition(request)
        remaining = (
            [definition if item.id == request.id else item for item in items]
            if exists
            else [*remaining, definition]
        )
        details.append(
            InstallationObjectDetails(
                resource=request.resource,
                kind=request.kind,
                id=request.id,
                name=request.name.strip(),
                geometry_semantic_ids=request.geometry_semantic_ids,
            )
        )
    elif action != "delete":
        raise ValueError("An object definition is required.")
    updated = ResourceObjects.model_validate(
        {
            **entry.model_dump(),
            "fixtures" if request.kind == "fixture" else "paths": [
                item.model_dump() for item in remaining
            ],
        }
    )
    document = _with_entry(store, updated).model_copy(update={"details": tuple(details)})
    return services.repository.save_installation_objects(
        installation_id=installation_id,
        expected_revision=revision,
        document=document,
        actor=actor,
        role=role,
        occurred_at=_now(),
        action=f"installation.objects.{action}",
    )[0]


def save_installation_segments(
    services: "GuiServices",
    *,
    installation_id: str,
    base_revision: int,
    resource: str,
    segments: tuple[PixelSegment, ...],
    names: dict[str, str],
    actor: str,
    role: Role,
) -> int:
    """Replace an output's wiring partition. Fixtures/paths using removed segments block it."""
    revision, store = _store(services, installation_id)
    if revision != base_revision:
        raise RevisionConflictError(
            requested_revision=base_revision,
            current_revision=revision,
            conflicting_paths=("/installation_objects",),
        )
    entry = _entry(services, installation_id, store, resource)
    removed = {segment.id for segment in entry.segments} - {segment.id for segment in segments}
    for show in _bound_shows(services, installation_id):
        used = [
            f"{show.project.name} ({', '.join(scenes)})"
            for (object_resource, kind, object_id), scenes in object_usage(show.draft).items()
            if object_resource == resource and kind == "segment" and object_id in removed
        ]
        if used:
            raise ValueError("Removed segments are still used by: " + "; ".join(used))
    try:
        updated = ResourceObjects.model_validate(
            {**entry.model_dump(), "segments": [item.model_dump() for item in segments]}
        )
    except ValueError as error:
        raise InheritanceError(str(error)) from error
    details = [
        item for item in store.details if not (item.resource == resource and item.kind == "segment")
    ]
    previous = {
        item.id: item.name
        for item in store.details
        if item.resource == resource and item.kind == "segment"
    }
    for segment in segments:
        name = (names.get(segment.id) or previous.get(segment.id) or "").strip()
        if name:
            details.append(
                InstallationObjectDetails(
                    resource=resource, kind="segment", id=segment.id, name=name
                )
            )
    document = _with_entry(store, updated).model_copy(update={"details": tuple(details)})
    return services.repository.save_installation_objects(
        installation_id=installation_id,
        expected_revision=revision,
        document=document,
        actor=actor,
        role=role,
        occurred_at=_now(),
        action="installation.objects.segments",
    )[0]


# --- Adoption and new shows -------------------------------------------------------------


def adopt_show(
    services: "GuiServices", *, project_id: str, base_revision: int, actor: str, role: Role
) -> StoredRevision:
    """Make a legacy show inherit its installation's wiring and objects."""
    stored = services.repository.get_revision(project_id)
    if inherits(stored.draft):
        return stored
    installation_id = _installation_id(stored.draft)
    if installation_id is None:
        raise InheritanceError("This show is not bound to an installation")
    store = _store(services, installation_id)[1]
    return services.repository.update_draft(
        project_id=project_id,
        expected_revision=base_revision,
        draft=adopt_draft(stored.draft, store),
        actor=actor,
        role=role,
        occurred_at=_now(),
    )


def ensure_installation_objects(services: "GuiServices") -> None:
    """Seed the runtime installation's store once and adopt its existing shows."""
    installation_id = services.repository.get_runtime_installation().id
    if services.repository.get_installation_objects(installation_id) is not None:
        return
    shows = _bound_shows(services, installation_id, inheriting=False)
    store = store_from_drafts(
        (show.draft for show in shows), services.runtime.catalog.installation.resources
    )
    services.repository.save_installation_objects(
        installation_id=installation_id,
        expected_revision=0,
        document=store,
        actor="system",
        role=Role.ADMINISTRATOR,
        occurred_at=_now(),
        action="installation.objects.seed",
    )
    for show in shows:
        try:
            adopt_show(
                services,
                project_id=show.project.id,
                base_revision=show.revision,
                actor="system",
                role=Role.ADMINISTRATOR,
            )
        except (ValueError, RevisionConflictError) as error:
            LOGGER.warning("show %s keeps legacy object definitions: %s", show.project.name, error)


def new_show_draft(
    services: "GuiServices",
    *,
    scenes: tuple[object, ...] = (),
    source: InstallationDefinition | None = None,
) -> DraftProject:
    """A new show bound to the runtime installation: devices + inherited objects only.

    With scenes (from the running show), only show objects those scenes use are copied.
    """
    installation_id = services.repository.get_runtime_installation().id
    installation = source or services.runtime.catalog.installation
    draft = DraftProject.model_validate(
        {
            "schema_version": 1,
            "installation": installation.model_dump(mode="json"),
            "scenes": [
                scene.model_dump(mode="json")  # type: ignore[attr-defined]
                for scene in scenes
            ],
            "editor_metadata": {"installation_id": installation_id},
        }
    )
    store = _store(services, installation_id)[1]
    try:
        return adopt_draft(draft, store, prune_unreferenced=True)
    except ValueError as error:
        LOGGER.warning("new show keeps legacy object definitions: %s", error)
        return draft


__all__ = [
    "INHERIT_FLAG",
    "adopt_show",
    "ensure_installation_objects",
    "explicit_scene_objects",
    "installation_inventory",
    "installation_keys",
    "installation_object_inventory",
    "new_show_draft",
    "promote_show_object",
    "remove_unused_objects",
    "save_installation_level_object",
    "save_installation_object",
    "save_installation_segments",
    "scene_object_view",
    "set_scene_objects",
    "show_object_summary",
]
