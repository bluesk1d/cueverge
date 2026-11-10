"""Installation → show → scene ownership of LED objects.

* The installation owns each pixel output's wiring segments and any reusable
  fixtures/paths (one store per installation).
* A show owns its own fixtures/paths. Inherited installation objects are
  materialized into the show draft on every save so compiled packages stay
  self-contained, but a show can never redefine them.
* A scene lists the objects it uses; its lighting may only target those.

Only drafts with ``editor_metadata.object_inheritance == 1`` take part, so shows
that could not be adopted keep their legacy, self-contained definitions.
"""

from collections.abc import Iterable, Iterator
from itertools import pairwise
from typing import Any, Literal

from pydantic import Field, JsonValue, ValidationError, model_validator

from cueverge.core.models import (
    PixelFixture,
    PixelPath,
    PixelSegment,
    ResourceBinding,
    ResourceKind,
    SegmentReference,
)
from cueverge.gui.contracts import (
    DraftProject,
    GuiModel,
    InstallationObjectDetails,
    SceneObjectReference,
)

INHERIT_FLAG = "object_inheritance"
INHERITED_KEY = "inherited_objects"
SCENE_OBJECTS_KEY = "scene_objects"
DETAILS_KEY = "installation_object_details"
ObjectKind = Literal["segment", "fixture", "path"]
ObjectKey = tuple[str, str, str]


class InheritanceError(ValueError):
    """A change would break the installation/show/scene object boundaries."""


class ResourceObjects(GuiModel):
    resource: str = Field(min_length=1)
    pixel_count: int = Field(gt=0)
    segments: tuple[PixelSegment, ...] = ()
    fixtures: tuple[PixelFixture, ...] = ()
    paths: tuple[PixelPath, ...] = ()

    @model_validator(mode="after")
    def validate_binding(self) -> "ResourceObjects":
        self.binding("installation")
        return self

    def binding(self, target: str) -> ResourceBinding:
        return ResourceBinding(
            resource=self.resource,
            kind=ResourceKind.PIXEL_OUTPUT,
            target=target,
            pixel_count=self.pixel_count,
            segments=self.segments,
            fixtures=self.fixtures,
            paths=self.paths,
        )


class InstallationObjectsDocument(GuiModel):
    schema_version: Literal[1] = 1
    resources: tuple[ResourceObjects, ...] = ()
    details: tuple[InstallationObjectDetails, ...] = ()

    def entry(self, resource: str) -> ResourceObjects | None:
        return next((item for item in self.resources if item.resource == resource), None)

    def name(self, resource: str, kind: str, object_id: str) -> str:
        detail = next(
            (
                item
                for item in self.details
                if (item.resource, item.kind, item.id) == (resource, kind, object_id)
            ),
            None,
        )
        return detail.name if detail else object_id.replace("-", " ")


class SceneObject(SceneObjectReference):
    @property
    def key(self) -> ObjectKey:
        return (self.resource, self.kind, self.id)


def inherits(draft: DraftProject) -> bool:
    return draft.editor_metadata.get(INHERIT_FLAG) == 1


def installation_keys(document: InstallationObjectsDocument) -> set[ObjectKey]:
    return {
        (entry.resource, kind, item.id)
        for entry in document.resources
        for kind, items in (
            ("segment", entry.segments),
            ("fixture", entry.fixtures),
            ("path", entry.paths),
        )
        for item in items
    }


def inherited_keys(draft: DraftProject) -> set[ObjectKey]:
    """Objects the draft inherits, from its materialized mirror."""
    raw = draft.editor_metadata.get(INHERITED_KEY, [])
    keys: set[ObjectKey] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                keys.add((str(item.get("resource")), str(item.get("kind")), str(item.get("id"))))
    return keys


def _dump(items: Iterable[Any]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]


def apply_inheritance(
    draft: DraftProject, store: InstallationObjectsDocument | None, *, strict: bool
) -> DraftProject:
    """Materialize installation objects into a participating show draft.

    strict rejects drafts that change inherited definitions (author edits);
    non-strict replaces them (propagating an installation change).
    """
    if store is None or not inherits(draft):
        return draft
    document = draft.model_dump(mode="json")
    mirror: list[dict[str, JsonValue]] = []
    inherited: set[ObjectKey] = set()
    for binding in document["installation"]["resources"]:
        entry = store.entry(binding["resource"])
        if entry is None or binding["kind"] != ResourceKind.PIXEL_OUTPUT.value:
            continue
        if binding.get("pixel_count") != entry.pixel_count:
            raise InheritanceError(
                f"{binding['resource']} has {binding.get('pixel_count')} pixels in this show but "
                f"{entry.pixel_count} in the installation"
            )
        segments = _dump(entry.segments)
        if strict and binding.get("segments") and binding["segments"] != segments:
            raise InheritanceError(
                f"Wiring segments of {binding['resource']} belong to the installation; "
                "edit them under Installation → Objects"
            )
        binding["segments"] = segments
        for section, kind, items in (
            ("fixtures", "fixture", entry.fixtures),
            ("paths", "path", entry.paths),
        ):
            definitions = {item.id: item.model_dump(mode="json") for item in items}
            own = []
            for item in binding.get(section, []):
                if item["id"] in definitions:
                    if strict and item != definitions[item["id"]]:
                        raise InheritanceError(
                            f"{kind} {item['id']} is defined by the installation; edit it "
                            "under Installation → Objects"
                        )
                    continue
                own.append(item)
            binding[section] = [*definitions.values(), *own]
        owned: tuple[tuple[str, tuple[PixelSegment | PixelFixture | PixelPath, ...]], ...] = (
            ("segment", entry.segments),
            ("fixture", entry.fixtures),
            ("path", entry.paths),
        )
        for kind, members in owned:
            for item in members:
                inherited.add((entry.resource, kind, item.id))
                mirror.append(
                    {
                        "resource": entry.resource,
                        "kind": kind,
                        "id": item.id,
                        "name": store.name(entry.resource, kind, item.id),
                    }
                )
    metadata = document["editor_metadata"]
    metadata[INHERITED_KEY] = mirror
    details = metadata.get(DETAILS_KEY)
    if isinstance(details, list):
        metadata[DETAILS_KEY] = [
            item
            for item in details
            if not isinstance(item, dict)
            or (item.get("resource"), item.get("kind"), item.get("id")) not in inherited
        ]
    if not strict:
        _prune_scene_objects(document)
    try:
        return DraftProject.model_validate(document)
    except ValidationError as error:
        raise InheritanceError(str(error)) from error


def _available(document: dict[str, Any]) -> set[ObjectKey]:
    return {
        (binding["resource"], kind, item["id"])
        for binding in document["installation"]["resources"]
        for kind, section in (("segment", "segments"), ("fixture", "fixtures"), ("path", "paths"))
        for item in binding.get(section, [])
    }


def _prune_scene_objects(document: dict[str, Any]) -> None:
    raw = document["editor_metadata"].get(SCENE_OBJECTS_KEY)
    if not isinstance(raw, dict):
        return
    available = _available(document)
    document["editor_metadata"][SCENE_OBJECTS_KEY] = {
        scene_id: [
            item
            for item in items
            if isinstance(item, dict)
            and (item.get("resource"), item.get("kind"), item.get("id")) in available
        ]
        for scene_id, items in raw.items()
        if isinstance(items, list)
    }


def validate_scene_objects(draft: DraftProject) -> None:
    """scene_objects must name existing scenes and objects of this draft."""
    raw = draft.editor_metadata.get(SCENE_OBJECTS_KEY)
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ValueError("scene_objects must map scene IDs to object lists")
    scenes = {scene.id for scene in draft.scenes}
    available = _available(draft.model_dump(mode="json"))
    for scene_id, items in raw.items():
        if scene_id not in scenes:
            raise ValueError(f"scene_objects references unknown scene {scene_id}")
        if not isinstance(items, list):
            raise ValueError("scene_objects entries must be lists")
        seen: set[ObjectKey] = set()
        for item in items:
            key = SceneObject.model_validate(item).key
            if key not in available:
                raise ValueError(f"scene {scene_id} lists unknown object {'/'.join(key)}")
            if key in seen:
                raise ValueError(f"scene {scene_id} lists {'/'.join(key)} twice")
            seen.add(key)


def iter_targets(value: Any, owner: str | None = None) -> Iterator[tuple[str | None, str, str]]:
    """Yield (resource, kind, id) for every object target in a scene/metadata document."""
    if isinstance(value, list):
        for item in value:
            yield from iter_targets(item, owner)
    elif isinstance(value, dict):
        resource = value.get("resource")
        context = resource if isinstance(resource, str) else owner
        target = value.get("target")
        if isinstance(target, dict) and target.get("kind") in {"segment", "fixture", "path"}:
            yield context, str(target["kind"]), str(target.get("id"))
        if value.get("target_kind") in {"segment", "fixture", "path"}:
            yield context, str(value["target_kind"]), str(value.get("target_id"))
        if isinstance(value.get("segment"), str):
            yield context, "segment", value["segment"]
        for key, item in value.items():
            if key != "target":
                yield from iter_targets(item, context)


_SCENE_METADATA_KEYS = (
    "timeline_cues",
    "timeline_transform_layers",
    "scene_compositions",
    "composition_instances",
    "lighting_author_layers",
)


def targeted_objects(draft: DraftProject, scene_id: str) -> set[ObjectKey]:
    """Objects a scene's lighting (and its timeline metadata) target."""
    scene = next((item for item in draft.scenes if item.id == scene_id), None)
    if scene is None:
        return set()
    resources = {binding.resource for binding in draft.installation.resources}
    sources: list[Any] = [scene.model_dump(mode="json")]
    for metadata_key in _SCENE_METADATA_KEYS:
        values = draft.editor_metadata.get(metadata_key)
        if isinstance(values, dict):
            sources.append(values.get(scene_id))
    available = _available(draft.model_dump(mode="json"))
    found: set[ObjectKey] = set()
    for source in sources:
        for resource, kind, object_id in iter_targets(source):
            candidates = [resource] if resource in resources else sorted(resources)
            for candidate in candidates:
                key: ObjectKey = (str(candidate), kind, object_id)
                if key in available:
                    found.add(key)
    return found


def explicit_scene_objects(draft: DraftProject, scene_id: str) -> list[SceneObject]:
    raw = draft.editor_metadata.get(SCENE_OBJECTS_KEY)
    items = raw.get(scene_id) if isinstance(raw, dict) else None
    return [SceneObject.model_validate(item) for item in items] if isinstance(items, list) else []


def scene_objects(draft: DraftProject, scene_id: str) -> list[SceneObject]:
    """The scene's object list: explicit members plus anything it already targets."""
    members = explicit_scene_objects(draft, scene_id)
    keys = {item.key for item in members}
    for key in sorted(targeted_objects(draft, scene_id) - keys):
        members.append(SceneObject(resource=key[0], kind=key[1], id=key[2]))  # type: ignore[arg-type]
    return members


# --- Adopting legacy drafts -------------------------------------------------------------


def coarsest_partition(
    partitions: Iterable[tuple[PixelSegment, ...]], pixel_count: int
) -> tuple[PixelSegment, ...]:
    """Segments whose boundaries appear in every partition, named from an exact match."""
    collected = [partition for partition in partitions if partition]
    if not collected:
        return ()
    boundaries = set.intersection(
        *({0, pixel_count} | {segment.start for segment in partition} for partition in collected)
    )
    edges = sorted(boundaries)
    names: dict[tuple[int, int], PixelSegment] = {}
    for partition in collected:
        for segment in partition:
            names.setdefault((segment.start, segment.end), segment)
    segments: list[PixelSegment] = []
    used: set[str] = set()
    for start, end in pairwise(edges):
        match = names.get((start, end))
        identifier = match.id if match is not None else f"segment-{start}-{end - 1}"
        if identifier in used:
            identifier = f"{identifier}-{start}"
        used.add(identifier)
        segments.append(
            PixelSegment(
                id=identifier, start=start, end=end, strand=match.strand if match else None
            )
        )
    return tuple(segments)


def _locate(
    segment: PixelSegment, installation: tuple[PixelSegment, ...]
) -> tuple[PixelSegment, int, int] | None:
    for candidate in installation:
        if candidate.start <= segment.start and segment.end <= candidate.end:
            return candidate, segment.start - candidate.start, segment.end - candidate.start
    return None


def adopt_draft(
    draft: DraftProject, store: InstallationObjectsDocument, *, prune_unreferenced: bool = False
) -> DraftProject:
    """Convert a self-contained show draft to inherit from the installation.

    Show segments that refine an installation segment become pixel-range members;
    targets of such segments become show fixtures with the same ID. Existing
    fixtures/paths stay show-owned (promote them explicitly). Scene object lists are
    seeded from what each scene targets.
    """
    document = draft.model_dump(mode="json")
    replacements: dict[tuple[str, str], str] = {}
    # Only segments effects target directly need a stand-in fixture; fixture and path
    # members are rewritten to pixel ranges in place.
    targeted_segments = {
        object_id
        for source in (document["scenes"], document["editor_metadata"])
        for _, kind, object_id in iter_targets(source)
        if kind == "segment"
    }
    for binding in document["installation"]["resources"]:
        entry = store.entry(binding["resource"])
        if entry is None or binding["kind"] != ResourceKind.PIXEL_OUTPUT.value:
            continue
        if binding.get("pixel_count") != entry.pixel_count:
            raise InheritanceError(f"{binding['resource']} pixel count differs from installation")
        installation_ids = {segment.id for segment in entry.segments}
        mapping: dict[str, tuple[PixelSegment, int, int]] = {}
        for raw in binding.get("segments", []):
            segment = PixelSegment.model_validate(raw)
            located = _locate(segment, entry.segments)
            if located is None:
                raise InheritanceError(
                    f"{binding['resource']} segment {segment.id} crosses installation wiring"
                )
            if not (
                located[0].id == segment.id
                and located[1] == 0
                and located[2] == located[0].pixel_count
            ):
                mapping[segment.id] = located

        def member(
            reference: dict[str, Any], current: dict[str, tuple[PixelSegment, int, int]]
        ) -> dict[str, Any]:
            located = current.get(reference["segment"])
            if located is None:
                return reference
            parent, offset, end = located
            inner_start = offset + int(reference.get("start") or 0)
            inner_end = offset + int(reference["end"]) if reference.get("end") is not None else end
            return {
                "segment": parent.id,
                "reverse": bool(reference.get("reverse")),
                **(
                    {}
                    if inner_start == 0 and inner_end == parent.pixel_count
                    else {"start": inner_start, "end": inner_end}
                ),
            }

        for section in ("fixtures", "paths"):
            for item in binding.get(section, []):
                item["segments"] = [member(ref, mapping) for ref in item["segments"]]
        fixture_ids = {item["id"] for item in binding.get("fixtures", [])}
        for old_id, (parent, offset, end) in mapping.items():
            if old_id not in targeted_segments:
                continue
            fixture_id = (
                old_id if old_id not in fixture_ids | installation_ids else f"segment-{old_id}"
            )
            replacements[(binding["resource"], old_id)] = fixture_id
            binding.setdefault("fixtures", []).append(
                {
                    "id": fixture_id,
                    "segments": [{"segment": parent.id, "start": offset, "end": end}],
                    "precedence": 0,
                }
            )
            fixture_ids.add(fixture_id)
        binding["segments"] = _dump(entry.segments)
    _rewrite_segment_targets(document, replacements)
    metadata = document["editor_metadata"]
    metadata[INHERIT_FLAG] = 1
    candidate = DraftProject.model_validate(document)
    if prune_unreferenced:
        candidate = remove_unused_show_objects(candidate, store)[0]
    seeded = {
        scene.id: [
            {"resource": key[0], "kind": key[1], "id": key[2]}
            for key in sorted(targeted_objects(candidate, scene.id))
        ]
        for scene in candidate.scenes
    }
    document = candidate.model_dump(mode="json")
    document["editor_metadata"][SCENE_OBJECTS_KEY] = seeded
    return apply_inheritance(DraftProject.model_validate(document), store, strict=False)


def _rewrite_segment_targets(
    document: dict[str, Any], replacements: dict[tuple[str, str], str]
) -> None:
    if not replacements:
        return
    single: dict[str, set[tuple[str, str]]] = {}
    for (resource, old), new in replacements.items():
        single.setdefault(old, set()).add((resource, new))

    def replacement(resource: str | None, old: str) -> str | None:
        if resource is not None and (resource, old) in replacements:
            return replacements[(resource, old)]
        options = single.get(old, set())
        return next(iter(options))[1] if len(options) == 1 else None

    def visit(value: Any, owner: str | None) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, owner)
            return
        if not isinstance(value, dict):
            return
        resource = value.get("resource")
        context = resource if isinstance(resource, str) else owner
        target = value.get("target")
        if isinstance(target, dict) and target.get("kind") == "segment":
            new = replacement(context, str(target.get("id")))
            if new is not None:
                value["target"] = {**target, "kind": "fixture", "id": new}
        if value.get("target_kind") == "segment":
            new = replacement(context, str(value.get("target_id")))
            if new is not None:
                value["target_kind"], value["target_id"] = "fixture", new
        if isinstance(value.get("segment"), str):
            new = replacement(context, value["segment"])
            if new is not None:
                value["segment"] = None
                value["target"] = {"kind": "fixture", "id": new}
        for key, item in value.items():
            if key != "target":
                visit(item, context)

    visit(document["scenes"], None)
    visit(document["editor_metadata"], None)


def show_owned(draft: DraftProject) -> list[tuple[str, str, Any]]:
    """(resource, kind, definition) of fixtures/paths the show itself defines."""
    inherited = inherited_keys(draft)
    return [
        (binding.resource, kind, item)
        for binding in draft.installation.resources
        for kind, items in (("fixture", binding.fixtures), ("path", binding.paths))
        for item in items
        if (binding.resource, kind, item.id) not in inherited
    ]


def object_usage(draft: DraftProject) -> dict[ObjectKey, list[str]]:
    """Scene IDs that list or target each object (scene-level membership)."""
    usage: dict[ObjectKey, list[str]] = {}
    for scene in draft.scenes:
        for item in scene_objects(draft, scene.id):
            usage.setdefault(item.key, []).append(scene.id)
    ambient = draft.editor_metadata.get("ambient_lighting")
    for resource, kind, object_id in iter_targets(ambient):
        for binding in draft.installation.resources:
            if resource in (None, binding.resource):
                usage.setdefault((binding.resource, kind, object_id), []).append("ambient")
    return usage


def remove_unused_show_objects(
    draft: DraftProject, store: InstallationObjectsDocument | None
) -> tuple[DraftProject, list[ObjectKey]]:
    """Drop show-owned fixtures/paths no scene lists, targets or another object needs."""
    used = set(object_usage(draft))
    document = draft.model_dump(mode="json")
    inherited = inherited_keys(draft)
    removed: list[ObjectKey] = []
    for binding in document["installation"]["resources"]:
        for kind, section in (("fixture", "fixtures"), ("path", "paths")):
            kept = []
            for item in binding.get(section, []):
                key = (binding["resource"], kind, item["id"])
                if key in inherited or key in used:
                    kept.append(item)
                else:
                    removed.append(key)
            binding[section] = kept
    metadata = document["editor_metadata"]
    details = metadata.get(DETAILS_KEY)
    if isinstance(details, list):
        gone = set(removed)
        metadata[DETAILS_KEY] = [
            item
            for item in details
            if not isinstance(item, dict)
            or (item.get("resource"), item.get("kind"), item.get("id")) not in gone
        ]
    result = DraftProject.model_validate(document)
    if store is not None and inherits(result):
        result = apply_inheritance(result, store, strict=False)
    return result, removed


def store_from_drafts(
    drafts: Iterable[DraftProject], runtime_bindings: Iterable[ResourceBinding]
) -> InstallationObjectsDocument:
    """Seed an installation's wiring from shows and the running package (coarsest common)."""
    partitions: dict[str, list[tuple[PixelSegment, ...]]] = {}
    counts: dict[str, int] = {}
    names: dict[tuple[str, str], str] = {}
    sources = [binding for binding in runtime_bindings]
    for draft in drafts:
        sources.extend(draft.installation.resources)
        for raw in _list(draft.editor_metadata.get(DETAILS_KEY)):
            if isinstance(raw, dict) and raw.get("kind") == "segment":
                names.setdefault(
                    (str(raw.get("resource")), str(raw.get("id"))), str(raw.get("name"))
                )
    for binding in sources:
        if binding.kind is not ResourceKind.PIXEL_OUTPUT or binding.pixel_count is None:
            continue
        count = counts.setdefault(binding.resource, binding.pixel_count)
        if count != binding.pixel_count:
            continue
        partitions.setdefault(binding.resource, []).append(binding.segments)
    resources = tuple(
        ResourceObjects(
            resource=resource,
            pixel_count=counts[resource],
            segments=coarsest_partition(partitions[resource], counts[resource]),
        )
        for resource in sorted(partitions)
    )
    details = tuple(
        InstallationObjectDetails(resource=entry.resource, kind="segment", id=segment.id, name=name)
        for entry in resources
        for segment in entry.segments
        if (name := names.get((entry.resource, segment.id)))
    )
    return InstallationObjectsDocument(resources=resources, details=details)


def promote(
    store: InstallationObjectsDocument,
    draft: DraftProject,
    resource: str,
    kind: str,
    object_id: str,
) -> InstallationObjectsDocument:
    """Move a show fixture/path (and its details) into the installation store."""
    if kind not in {"fixture", "path"}:
        raise InheritanceError("Only fixtures and ordered paths can be promoted")
    if (resource, kind, object_id) in installation_keys(store):
        raise InheritanceError(f"The installation already defines {kind} {object_id}")
    binding = next(
        (item for item in draft.installation.resources if item.resource == resource), None
    )
    entry = store.entry(resource)
    if binding is None or entry is None:
        raise InheritanceError("Promote objects of a pixel output the installation knows")
    definition = next(
        (
            item
            for item in (binding.fixtures if kind == "fixture" else binding.paths)
            if item.id == object_id
        ),
        None,
    )
    if definition is None:
        raise InheritanceError(f"This show has no {kind} {object_id}")
    detail = next(
        (
            InstallationObjectDetails.model_validate(item)
            for item in _list(draft.editor_metadata.get(DETAILS_KEY))
            if isinstance(item, dict)
            and (item.get("resource"), item.get("kind"), item.get("id"))
            == (resource, kind, object_id)
        ),
        None,
    )
    updated = entry.model_copy(
        update={
            "fixtures": (*entry.fixtures, definition) if kind == "fixture" else entry.fixtures,
            "paths": (*entry.paths, definition) if kind == "path" else entry.paths,
        }
    )
    ResourceObjects.model_validate(updated.model_dump())
    return store.model_copy(
        update={
            "resources": tuple(
                updated if item.resource == resource else item for item in store.resources
            ),
            "details": (*store.details, detail) if detail is not None else store.details,
        }
    )


def _list(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def as_reference(value: dict[str, Any]) -> SegmentReference:
    return SegmentReference.model_validate(value)
