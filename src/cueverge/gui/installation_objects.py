import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import JsonValue, TypeAdapter

from cueverge.core.models import ResourceKind, SegmentReference
from cueverge.gui.contracts import (
    DraftProject,
    InstallationObjectDeleteRequest,
    InstallationObjectDetails,
    InstallationObjectEditRequest,
    InstallationObjectGeometry,
    InstallationObjectInventory,
    InstallationObjectRecord,
    InstallationObjectTraversal,
    Role,
)
from cueverge.gui.installation_geometry import StoredInstallationGeometry
from cueverge.gui.repository import RevisionConflictError, StoredRevision

if TYPE_CHECKING:
    from cueverge.gui.service import GuiServices

DETAILS_KEY = "installation_object_details"
_details_adapter = TypeAdapter(tuple[InstallationObjectDetails, ...])
ObjectKind = Literal["segment", "fixture", "path"]


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
    services: "GuiServices", draft: DraftProject
) -> tuple[str | None, tuple[StoredInstallationGeometry, ...], dict[str, str]]:
    raw_id = draft.editor_metadata.get("installation_id")
    installation_id = raw_id if isinstance(raw_id, str) else None
    if installation_id != services.repository.get_runtime_installation().id:
        return installation_id, (), {}
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
            if shape.kind == "pixel_segment" and shape.semantic_id in assignments
        }
    return installation_id, geometry, resources


def installation_object_inventory(
    services: "GuiServices", project_id: str
) -> InstallationObjectInventory:
    stored = services.repository.get_revision(project_id)
    draft = stored.draft
    installation_id, geometry, geometry_resources = _layout(services, draft)
    details = {(item.resource, item.kind, item.id): item for item in _details(draft)}
    grouped: dict[str, list[StoredInstallationGeometry]] = {}
    for shape in geometry:
        if shape.kind in {"pixel_segment", "fixture", "path"}:
            grouped.setdefault(shape.semantic_id, []).append(shape)
    records: list[InstallationObjectRecord] = []
    bindings = tuple(
        item for item in draft.installation.resources if item.kind is ResourceKind.PIXEL_OUTPUT
    )
    for binding in bindings:
        segments = {item.id: item for item in binding.segments}
        targets: list[tuple[ObjectKind, str, tuple[SegmentReference, ...], int]] = [
            ("segment", item.id, (SegmentReference(segment=item.id),), 0)
            for item in binding.segments
        ]
        targets.extend(
            ("fixture", item.id, item.segments, item.precedence) for item in binding.fixtures
        )
        targets.extend(("path", item.id, item.segments, 0) for item in binding.paths)
        for kind, object_id, members, precedence in targets:
            detail = details.get((binding.resource, kind, object_id))
            linked = detail.geometry_semantic_ids if detail else ()
            matched: set[str] = {
                shape.id for semantic_id in linked for shape in grouped.get(semantic_id, [])
            }
            traversal: list[InstallationObjectTraversal] = []
            for member in members:
                segment = segments[member.segment]
                traversal.append(
                    InstallationObjectTraversal(
                        segment=segment.id,
                        first_pixel=segment.end - 1 if member.reverse else segment.start,
                        last_pixel=segment.start if member.reverse else segment.end - 1,
                        pixel_count=segment.pixel_count,
                        reverse=member.reverse,
                    )
                )
                matched.update(
                    shape.id
                    for shape in geometry
                    if shape.kind == "pixel_segment"
                    and geometry_resources.get(shape.id) == binding.resource
                    and shape.start_index == segment.start
                    and shape.pixel_count == segment.pixel_count
                )
            records.append(
                InstallationObjectRecord(
                    resource=binding.resource,
                    kind=kind,
                    id=object_id,
                    name=detail.name if detail else object_id.replace("-", " "),
                    geometry_semantic_ids=linked,
                    segments=members,
                    precedence=precedence,
                    pixel_count=sum(item.pixel_count for item in traversal),
                    traversal=tuple(traversal),
                    references=_references(draft, binding.resource, kind, object_id),
                    geometry_ids=tuple(sorted(matched)),
                    missing_geometry_semantic_ids=tuple(
                        semantic_id for semantic_id in linked if semantic_id not in grouped
                    ),
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
        geometry=tuple(
            InstallationObjectGeometry(
                semantic_id=semantic_id,
                name=shapes[0].name,
                kind=shapes[0].kind,
                resource=geometry_resources.get(shapes[0].id),
                geometry_ids=tuple(shape.id for shape in shapes),
            )
            for semantic_id, shapes in grouped.items()
        ),
    )


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
        _, geometry, resources = _layout(services, draft)
        for semantic_id in request.geometry_semantic_ids:
            linked = [shape for shape in geometry if shape.semantic_id == semantic_id]
            if not linked or any(
                shape.kind not in {"pixel_segment", request.kind}
                or (shape.kind == "pixel_segment" and resources.get(shape.id) != request.resource)
                for shape in linked
            ):
                raise ValueError(
                    "Layout links must reference this installation's matching object "
                    "geometry or pixel segments assigned to this output."
                )
    document = draft.model_dump(mode="json")
    resource_document = next(
        item
        for item in document["installation"]["resources"]
        if item["resource"] == request.resource
    )
    definitions = [item for item in resource_document[section] if item["id"] != request.id]
    if isinstance(request, InstallationObjectEditRequest) and action != "delete":
        definition: dict[str, JsonValue] = {
            "id": request.id,
            "segments": [item.model_dump(mode="json") for item in request.segments],
        }
        if request.kind == "fixture":
            definition["precedence"] = request.precedence
        # Replace in place so editing does not reorder otherwise unchanged definitions.
        if exists:
            definitions = [
                definition if item["id"] == request.id else item
                for item in resource_document[section]
            ]
        else:
            definitions.append(definition)
    resource_document[section] = definitions
    details = [
        item
        for item in _details(draft)
        if (item.resource, item.kind, item.id) != (request.resource, request.kind, request.id)
    ]
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
    document["editor_metadata"][DETAILS_KEY] = [item.model_dump(mode="json") for item in details]
    candidate = DraftProject.model_validate(document)
    return services.repository.update_draft(
        project_id=project_id,
        expected_revision=base_revision,
        draft=candidate,
        actor=actor,
        role=role,
        occurred_at=datetime.now(UTC).isoformat(),
    )
