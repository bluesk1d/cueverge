import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cueverge.core.loader import ShowPackageError, load_show_package
from cueverge.core.models import (
    LightingCue,
    LightingLookDefinition,
    MultiLightingLooksDefinition,
)
from cueverge.gui.ambient_lighting import AmbientLighting, parse_ambient_lighting
from cueverge.gui.contracts import DraftProject
from cueverge.gui.render_pipeline import render_pipeline_manifest
from cueverge.gui.render_verification import verification_policy_manifest
from cueverge.gui.stage_renderer import bakes_lighting_only


class CompilationError(ValueError):
    def __init__(self, message: str, diagnostics: tuple["CompilationDiagnostic", ...] = ()) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class CompilationDiagnostic:
    code: str
    message: str
    object_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledPackage:
    digest: str
    files: tuple[str, ...]
    scene_ids: tuple[str, ...]
    package_root: Path


class DraftCompiler:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def compile(
        self,
        *,
        project_id: str,
        revision: int,
        draft: DraftProject,
        lighting_artifacts: dict[str, Path] | None = None,
        media_assets: dict[tuple[str, str], str] | None = None,
        lighting_looks: MultiLightingLooksDefinition | None = None,
    ) -> CompiledPackage:
        project_root = self.output_root / project_id
        project_root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix=".compile-", dir=project_root))
        try:
            scenes_root = temporary_root / "scenes"
            scenes_root.mkdir()
            _write_json(
                temporary_root / "installation.json",
                draft.installation.model_dump(mode="json"),
            )
            for filename, definition in (
                ("program.json", draft.program),
                ("audience.json", draft.audience),
                ("opportunities.json", draft.opportunities),
                ("encounter.json", draft.encounter),
            ):
                if definition is not None:
                    _write_json(
                        temporary_root / filename,
                        definition.model_dump(mode="json"),
                    )
            runtime_root = temporary_root / "runtime"
            runtime_root.mkdir()
            graph = _compile_composition_graph(draft)
            _write_json(
                runtime_root / "composition-graph.json",
                graph,
            )
            ambient = parse_ambient_lighting(draft.editor_metadata)
            ambient_compositions = {
                output.composition_id
                for output in (ambient.outputs if ambient is not None else ())
                if output.composition_id is not None
            }
            lighting_only = frozenset(
                key
                for key, value in _object_map(draft.editor_metadata.get("compositions")).items()
                if bakes_lighting_only(value)
            )
            if lighting_artifacts:
                try:
                    _install_lighting_sequences(
                        temporary_root,
                        graph,
                        lighting_artifacts,
                        revision,
                        ambient_compositions,
                        lighting_only,
                    )
                except (OSError, ValueError, KeyError) as error:
                    raise CompilationError(f"invalid lighting bake: {error}") from error
            try:
                looks = _compile_lighting_looks(
                    temporary_root,
                    ambient,
                    lighting_looks,
                    lighting_artifacts or {},
                    revision,
                    lighting_only,
                )
            except (OSError, ValueError, KeyError) as error:
                raise CompilationError(f"invalid ambient lighting: {error}") from error
            if looks is not None:
                _write_json(temporary_root / "looks.json", looks.model_dump(mode="json"))
            _write_json(
                runtime_root / "render-pipeline.json",
                render_pipeline_manifest(),
            )
            _write_json(
                runtime_root / "render-verification.json",
                verification_policy_manifest(),
            )
            for index, scene in enumerate(draft.scenes):
                document = scene.model_dump(mode="json")
                if media_assets:
                    outputs = document["media"]
                    if isinstance(outputs, list):
                        for output in outputs:
                            digest = media_assets.get((scene.id, output["id"]))
                            if digest is not None:
                                output["asset"] = digest
                    elif (scene.id, "media") in media_assets:
                        outputs["asset"] = media_assets[(scene.id, "media")]
                _write_json(
                    scenes_root / f"scene-{index + 1:04d}.json",
                    document,
                )
            try:
                catalog = load_show_package(temporary_root)
            except ShowPackageError as error:
                raise CompilationError(str(error)) from error
            lighting_compositions = {
                key
                for key, value in _object_map(draft.editor_metadata.get("compositions")).items()
                if value.get("space_kind") == "lighting_surface"
                or (value.get("space_kind") == "stage" and value.get("stage_lighting_surfaces"))
            }
            missing_bakes = lighting_compositions.difference(lighting_artifacts or {})
            if missing_bakes:
                _write_json(
                    runtime_root / "lighting-bake-required.json",
                    {"composition_ids": sorted(missing_bakes)},
                )
            files = tuple(
                str(path.relative_to(temporary_root))
                for path in sorted(path for path in temporary_root.rglob("*") if path.is_file())
            )
            digest = _package_digest(temporary_root, files)
            destination = project_root / f"r{revision}-{digest[:16]}"
            if destination.exists():
                shutil.rmtree(temporary_root)
            else:
                os.replace(temporary_root, destination)
            return CompiledPackage(
                digest=digest,
                files=files,
                scene_ids=tuple(sorted(catalog.scenes)),
                package_root=destination,
            )
        except BaseException:
            if temporary_root.exists():
                shutil.rmtree(temporary_root)
            raise


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def composition_scene_usage(
    draft: DraftProject,
) -> tuple[dict[str, tuple[str, ...]], set[str]]:
    """Scenes each composition plays in (including ambient), and the ambient compositions."""
    usage = {key: list(value) for key, value in _timeline_composition_usage(draft).items()}
    ambient = parse_ambient_lighting(draft.editor_metadata)
    ambient_ids = {
        output.composition_id
        for output in (ambient.outputs if ambient is not None else ())
        if output.composition_id is not None
    }
    if ambient is not None:
        inheriting = [
            scene.id for scene in draft.scenes if scene.id not in ambient.excluded_scene_ids
        ]
        for composition_id in ambient_ids:
            scenes = usage.setdefault(composition_id, [])
            scenes.extend(scene_id for scene_id in inheriting if scene_id not in scenes)
    return {key: tuple(value) for key, value in usage.items()}, ambient_ids


def ambient_compositions_on_scene_timelines(draft: DraftProject) -> dict[str, tuple[str, ...]]:
    """Ambient compositions also placed on an inheriting scene's timeline, with those scenes.

    The placed copy plays on scene time, so it restarts whenever the scene loops or starts,
    unlike the ambient itself, which runs on the controller clock.
    """
    ambient = parse_ambient_lighting(draft.editor_metadata)
    if ambient is None:
        return {}
    ambient_ids = {
        output.composition_id for output in ambient.outputs if output.composition_id is not None
    }
    usage = _timeline_composition_usage(draft)
    result: dict[str, tuple[str, ...]] = {}
    for composition_id in sorted(ambient_ids):
        scenes = tuple(
            scene_id
            for scene_id in usage.get(composition_id, ())
            if scene_id not in ambient.excluded_scene_ids
        )
        if scenes:
            result[composition_id] = scenes
    return result


def _timeline_composition_usage(draft: DraftProject) -> dict[str, tuple[str, ...]]:
    usage: dict[str, list[str]] = {}
    try:
        graph = _compile_composition_graph(draft)
    except (CompilationError, ValueError, KeyError, TypeError):
        graph = {}
    for scene in _list_items(graph.get("scenes")):
        record = _object(scene)
        scene_id = str(record.get("scene_id"))
        for raw_context in _list_items(record.get("contexts")):
            space = str(_object(raw_context).get("editing_space_id"))
            if space != scene_id and scene_id not in usage.setdefault(space, []):
                usage[space].append(scene_id)
    return {key: tuple(value) for key, value in usage.items()}


def _bake_manifest(
    source: Path, composition_id: str, revision: int, lighting_only: bool = False
) -> dict[str, Any]:
    manifest_path = (
        source / "manifest.json"
        if (source / "manifest.json").exists()
        else source / "render-manifest.json"
    )
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    # LED-only bakes are keyed on what they draw, so one from an earlier revision stays valid.
    if manifest.get("source_revision") != revision and not lighting_only:
        raise CompilationError("lighting bake belongs to a different draft revision")
    if manifest.get("composition_id") != composition_id:
        raise CompilationError("lighting bake belongs to a different composition")
    if manifest_path.name != "render-manifest.json":
        manifest["lighting_sequences"] = [{**manifest, "path": "frames.rgb"}]
    return manifest


def _compile_lighting_looks(
    root: Path,
    ambient: AmbientLighting | None,
    controller_looks: MultiLightingLooksDefinition | None,
    artifacts: dict[str, Path],
    revision: int,
    lighting_only: frozenset[str] = frozenset(),
) -> MultiLightingLooksDefinition | None:
    """Ambient lighting from the draft, over any retained controller looks per LED output."""
    from cueverge.lighting.sequence import LightingSequenceReference, load_lighting_sequence

    retained = {
        look.resource: look for look in (controller_looks.outputs if controller_looks else ())
    }
    outputs: dict[str, LightingLookDefinition] = dict(retained)
    excluded = ambient.excluded_scene_ids if ambient is not None else ()
    for output in ambient.outputs if ambient is not None else ():
        sequence_reference: LightingSequenceReference | None = None
        if output.composition_id is not None:
            source = artifacts.get(output.composition_id)
            if source is None:
                # Unbaked: the package carries the bake-required marker; cues still preview.
                if not output.cues:
                    continue
            else:
                manifest = _bake_manifest(
                    source,
                    output.composition_id,
                    revision,
                    output.composition_id in lighting_only,
                )
                sequence = next(
                    (
                        item
                        for item in manifest.get("lighting_sequences", [])
                        if item.get("resource") == output.resource
                    ),
                    None,
                )
                if sequence is None:
                    raise CompilationError(
                        f"ambient composition {output.composition_id} does not light "
                        f"{output.resource}; include its LED surfaces in the composition"
                    )
                channels = sequence.get("channels", 3)
                relative = (
                    f"runtime/lighting/ambient-{output.resource.encode().hex()}."
                    f"{'rgba' if channels == 4 else 'rgb'}"
                )
                reference = LightingSequenceReference.model_validate(
                    {
                        **{
                            key: sequence[key]
                            for key in (
                                "resource",
                                "output_indices",
                                "frame_rate",
                                "frame_count",
                                "duration_ms",
                                "digest_sha256",
                                "path",
                            )
                        },
                        "frame_rate_denominator": sequence.get("frame_rate_denominator", 1),
                        "channels": channels,
                        "scene_id": "ambient",
                    }
                )
                load_lighting_sequence(source, reference)
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / reference.path, destination)
                sequence_reference = reference.model_copy(update={"path": relative})
        previous = retained.get(output.resource)
        outputs[output.resource] = LightingLookDefinition(
            resource=output.resource,
            cycle_ms=(
                output.cycle_ms
                if output.cues or sequence_reference is None
                else sequence_reference.duration_ms
            ),
            brightness_limit=output.brightness_limit,
            baseline=output.cues,
            baseline_sequence=sequence_reference,
            safe_state=(
                previous.safe_state
                if previous is not None
                else (
                    LightingCue(
                        id="ambient-safe-blackout",
                        primitive="blackout",
                        start_ms=0,
                        duration_ms=1,
                    ),
                )
            ),
            excluded_scene_ids=excluded,
        )
    if not outputs:
        return None
    return MultiLightingLooksDefinition(
        schema_version=2,
        outputs=tuple(
            look.model_copy(update={"excluded_scene_ids": excluded})
            if ambient is not None
            else look
            for _, look in sorted(outputs.items())
        ),
    )


def _install_lighting_sequences(
    root: Path,
    graph: dict[str, object],
    artifacts: dict[str, Path],
    revision: int,
    ambient_compositions: set[str] | None = None,
    lighting_only: frozenset[str] = frozenset(),
) -> None:
    from cueverge.lighting.sequence import LightingSequenceReference, load_lighting_sequence

    references: list[dict[str, object]] = []
    attached: set[str] = set()
    placements: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for scene in _list_items(graph.get("scenes")):
        record = _object(scene)
        for raw_context in _list_items(record.get("contexts")):
            context = _object(raw_context)
            composition_id = str(context.get("editing_space_id"))
            if composition_id not in artifacts:
                continue
            if any(_number(value, 0) != 0 for value in _object(context.get("placement")).values()):
                raise CompilationError("lighting sequence instances cannot override placement")
            source = artifacts[composition_id]
            manifest_path = (
                source / "manifest.json"
                if (source / "manifest.json").exists()
                else source / "render-manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("source_revision") != revision and composition_id not in lighting_only:
                raise CompilationError("lighting bake belongs to a different draft revision")
            if manifest.get("composition_id") != composition_id:
                raise CompilationError("lighting bake belongs to a different composition")
            start = _number(context.get("offset_ms"), 0)
            end = start + _number(manifest.get("duration_ms"), 0) * _number(
                context.get("time_scale"), 1
            )
            placed = placements.setdefault((str(record.get("scene_id")), composition_id), [])
            if any(start < other_end and other_start < end for other_start, other_end in placed):
                raise CompilationError(
                    f"LED composition {composition_id} is placed more than once at overlapping "
                    f"times in scene {record.get('scene_id')}; remove the extra instance "
                    "(Timeline editor > Compositions)"
                )
            placed.append((start, end))
            sequences = (
                manifest["lighting_sequences"]
                if manifest_path.name == "render-manifest.json"
                else [{**manifest, "path": "frames.rgb"}]
            )
            if not isinstance(sequences, list) or not sequences:
                raise CompilationError("stage bake contains no lighting sequences")
            for index, sequence in enumerate(sequences):
                extension = "rgba" if sequence.get("channels", 3) == 4 else "rgb"
                relative = f"runtime/lighting/{composition_id.encode().hex()}-{index}.{extension}"
                reference = LightingSequenceReference.model_validate(
                    {
                        **{
                            key: sequence[key]
                            for key in (
                                "resource",
                                "output_indices",
                                "frame_rate",
                                "frame_count",
                                "duration_ms",
                                "digest_sha256",
                                "path",
                            )
                        },
                        "frame_rate_denominator": sequence.get("frame_rate_denominator", 1),
                        "channels": sequence.get("channels", 3),
                        "scene_id": record["scene_id"],
                        "offset_ms": context["offset_ms"],
                        "time_scale": context["time_scale"],
                    }
                )
                load_lighting_sequence(source, reference)
                destination = root / relative
                destination.parent.mkdir(exist_ok=True)
                shutil.copyfile(source / reference.path, destination)
                references.append({**reference.model_dump(mode="json"), "path": relative})
            attached.add(composition_id)
    if set(artifacts).difference(attached, ambient_compositions or set()):
        raise CompilationError(
            "every lighting composition must be attached to a scene or be the show's ambient "
            "lighting before publication"
        )
    _write_json(root / "runtime/lighting-sequences.json", references)


def _package_digest(root: Path, files: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative_path in files:
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative_path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _compile_composition_graph(draft: DraftProject) -> dict[str, object]:
    metadata = draft.editor_metadata
    compositions = _object_map(metadata.get("compositions"))
    scene_roots = _list_map(metadata.get("scene_compositions"))
    instances = _list_map(metadata.get("composition_instances"))
    marker_map = _list_map(metadata.get("timeline_markers"))
    cue_map = _list_map(metadata.get("timeline_cues"))
    track_map = _list_map(metadata.get("timeline_tracks"))
    transform_map = _list_map(metadata.get("timeline_transform_layers"))
    diagnostics: list[CompilationDiagnostic] = []
    compiled_scenes: list[dict[str, object]] = []

    for scene in draft.scenes:
        scene_tracks = _scene_tracks(scene.model_dump(mode="json"))
        contexts: list[dict[str, object]] = []
        _append_context(
            contexts,
            scene_id=scene.id,
            context_id=f"scene:{scene.id}",
            editing_space_id=scene.id,
            offset_ms=0,
            time_scale=1.0,
            placement={"x": 0.0, "y": 0.0},
            parameter_overrides={},
        )
        placed = {
            str(_object(value).get("composition_id")) for value in instances.get(scene.id, [])
        }
        for root_id in _string_items(scene_roots.get(scene.id)):
            root = compositions.get(root_id)
            # A root with instances is placed by those instances, not a second time here.
            if root is not None and root_id not in placed:
                _walk_composition_contexts(
                    contexts,
                    compositions,
                    scene.id,
                    root_id,
                    f"scene:{scene.id}>{root_id}",
                    _integer(root.get("local_time_offset_ms"), 0),
                    1.0,
                    {"x": 0.0, "y": 0.0},
                    {},
                    set(),
                )
        for value in instances.get(scene.id, []):
            instance = _object(value)
            composition_id = instance.get("composition_id")
            if not isinstance(composition_id, str) or composition_id not in compositions:
                continue
            overrides = _object(instance.get("parameter_overrides"))
            if overrides:
                diagnostics.append(
                    CompilationDiagnostic(
                        code="unsupported_composition_parameter_override",
                        message=(
                            f"composition instance {instance.get('id')} has parameter overrides "
                            "that cannot yet be represented by the runtime package"
                        ),
                        object_ids=(str(instance.get("id")), composition_id),
                    )
                )
            placement = _object(instance.get("placement"))
            instance_id = str(instance.get("id"))
            _walk_composition_contexts(
                contexts,
                compositions,
                scene.id,
                composition_id,
                f"instance:{instance_id}",
                _integer(instance.get("start_offset_ms"), 0),
                _number(instance.get("time_scale"), 1.0),
                {
                    "x": _number(placement.get("x"), 0.0),
                    "y": _number(placement.get("y"), 0.0),
                },
                overrides,
                set(),
            )

        outputs: dict[str, dict[str, object]] = {}
        cues: list[dict[str, object]] = []
        transform_layers: list[dict[str, object]] = []
        for context in contexts:
            space_id = str(context["editing_space_id"])
            markers = {
                str(marker.get("id")): marker
                for marker in (_object(value) for value in marker_map.get(space_id, []))
                if isinstance(marker.get("id"), str)
            }
            tracks = {
                **(scene_tracks if space_id == scene.id else {}),
                **{
                    str(track.get("id")): track
                    for track in (_object(value) for value in track_map.get(space_id, []))
                    if isinstance(track.get("id"), str)
                },
            }
            for value in cue_map.get(space_id, []):
                cue = _object(value)
                cue_id = str(cue.get("id"))
                track_id = cue.get("track_id")
                track = tracks.get(str(track_id))
                if track is None:
                    diagnostics.append(
                        CompilationDiagnostic(
                            code="unsupported_composition_track",
                            message=(
                                f"timeline cue {cue_id} references track {track_id!s}, "
                                "which has no runtime output binding"
                            ),
                            object_ids=(cue_id, str(track_id)),
                        )
                    )
                    continue
                local_time = _cue_time(cue, markers)
                scene_time = round(
                    _number(context["offset_ms"], 0.0)
                    + local_time * _number(context["time_scale"], 1.0)
                )
                lighting = _object(cue.get("lighting"))
                compiled_duration = (
                    round(
                        _integer(lighting.get("duration_ms"), 0)
                        * _number(context["time_scale"], 1.0)
                    )
                    if lighting
                    else 0
                )
                if (
                    scene_time < 0
                    or scene_time > scene.duration_ms
                    or scene_time + compiled_duration > scene.duration_ms
                ):
                    diagnostics.append(
                        CompilationDiagnostic(
                            code="composition_cue_outside_scene",
                            message=(
                                f"timeline cue {cue_id} resolves to interval "
                                f"{scene_time}..{scene_time + compiled_duration} ms outside "
                                f"scene {scene.id} duration {scene.duration_ms} ms"
                            ),
                            object_ids=(scene.id, cue_id, str(context["context_id"])),
                        )
                    )
                    continue
                output_id = f"{context['context_id']}|{track_id!s}"
                outputs[output_id] = {
                    "id": output_id,
                    "context_id": context["context_id"],
                    "track_id": track_id,
                    "track_kind": track.get("kind"),
                    "resource_binding": track.get("resource_binding"),
                }
                marker_id = cue.get("marker_id")
                marker = markers.get(marker_id) if isinstance(marker_id, str) else None
                compiled_lighting = (
                    {
                        **lighting,
                        "duration_ms": compiled_duration,
                    }
                    if lighting
                    else None
                )
                cues.append(
                    {
                        "id": f"{context['context_id']}|{cue_id}",
                        "source_cue_id": cue_id,
                        "name": cue.get("name"),
                        "kind": cue.get("kind"),
                        "output_id": output_id,
                        "timestamp_ms": scene_time,
                        "local_time_ms": local_time,
                        "marker_id": marker_id,
                        "marker_name": marker.get("name") if marker else None,
                        "marker_offset_ms": cue.get("marker_offset_ms", 0),
                        "editing_space_id": space_id,
                        "context_id": context["context_id"],
                        "lighting": compiled_lighting,
                    }
                )
            for value in transform_map.get(space_id, []):
                layer = _object(value)
                layer_id = str(layer.get("id"))
                track_id = layer.get("track_id")
                track = tracks.get(str(track_id))
                if track is None:
                    diagnostics.append(
                        CompilationDiagnostic(
                            code="unsupported_transform_track",
                            message=(
                                f"transform layer {layer_id} references track "
                                f"{track_id!s}, which has no runtime output binding"
                            ),
                            object_ids=(layer_id, str(track_id)),
                        )
                    )
                    continue
                time_scale = _number(context["time_scale"], 1.0)
                start_ms = round(
                    _number(context["offset_ms"], 0.0)
                    + _integer(layer.get("start_ms"), 0) * time_scale
                )
                duration_ms = round(_integer(layer.get("duration_ms"), 0) * time_scale)
                if start_ms < 0 or start_ms + duration_ms > scene.duration_ms:
                    diagnostics.append(
                        CompilationDiagnostic(
                            code="transform_layer_outside_scene",
                            message=(
                                f"transform layer {layer_id} resolves to interval "
                                f"{start_ms}..{start_ms + duration_ms} ms outside "
                                f"scene {scene.id} duration {scene.duration_ms} ms"
                            ),
                            object_ids=(scene.id, layer_id, str(context["context_id"])),
                        )
                    )
                    continue
                output_id = f"{context['context_id']}|{track_id!s}"
                outputs[output_id] = {
                    "id": output_id,
                    "context_id": context["context_id"],
                    "track_id": track_id,
                    "track_kind": track.get("kind"),
                    "resource_binding": track.get("resource_binding"),
                }
                transform_layers.append(
                    {
                        "id": f"{context['context_id']}|{layer_id}",
                        "source_layer_id": layer_id,
                        "name": layer.get("name"),
                        "kind": layer.get("kind"),
                        "source_id": layer.get("source_id"),
                        "layer_schema_version": layer.get("layer_schema_version"),
                        "output_id": output_id,
                        "editing_space_id": space_id,
                        "context_id": context["context_id"],
                        "coordinate_space": _coordinate_space(
                            space_id,
                            compositions.get(space_id),
                        ),
                        "calibration_policy": {
                            "static_installation_calibration": "runtime",
                            "projector_handoff_keyframes": False,
                        },
                        "keyframed_properties": [
                            "position",
                            "scale_x",
                            "scale_y",
                            "rotation",
                            "anchor",
                            "opacity",
                            "crop",
                        ],
                        "property_animation_policy": {
                            "transform": "runtime",
                            "lighting": "static_cue",
                            "unsupported": "reject",
                        },
                        "source_in_ms": layer.get("in_point_ms"),
                        "source_out_ms": layer.get("out_point_ms"),
                        "source_conformance": {
                            "alpha_interpretation": layer.get("alpha_interpretation"),
                            "color_space": layer.get("source_color_space"),
                            "scaling_behavior": layer.get("scaling_behavior"),
                        },
                        "render_conformance": _render_contract(compositions.get(space_id)),
                        "z_index": layer.get("z_index"),
                        "fade_in_ms": round(_integer(layer.get("fade_in_ms"), 0) * time_scale),
                        "fade_out_ms": round(_integer(layer.get("fade_out_ms"), 0) * time_scale),
                        "audio": layer.get("audio"),
                        "start_ms": start_ms,
                        "duration_ms": duration_ms,
                        "keyframes": [
                            {
                                **_object(keyframe),
                                "timestamp_ms": start_ms
                                + round(
                                    _integer(
                                        _object(keyframe).get("time_ms"),
                                        0,
                                    )
                                    * time_scale
                                ),
                            }
                            for keyframe in layer.get("keyframes", [])
                            if isinstance(keyframe, dict)
                        ],
                    }
                )
        compiled_scenes.append(
            {
                "scene_id": scene.id,
                "contexts": contexts,
                "outputs": sorted(outputs.values(), key=lambda value: str(value["id"])),
                "cues": sorted(
                    cues,
                    key=lambda value: (
                        _integer(value["timestamp_ms"], 0),
                        str(value["id"]),
                    ),
                ),
                "transform_layers": sorted(
                    transform_layers,
                    key=lambda value: (
                        _integer(value["z_index"], 0),
                        _integer(value["start_ms"], 0),
                        str(value["id"]),
                    ),
                ),
            }
        )
    if diagnostics:
        raise CompilationError(
            "composition graph contains unsupported or invalid runtime constructs",
            tuple(diagnostics),
        )
    return {"schema_version": 1, "scenes": compiled_scenes}


def _append_context(
    contexts: list[dict[str, object]],
    *,
    scene_id: str,
    context_id: str,
    editing_space_id: str,
    offset_ms: int,
    time_scale: float,
    placement: dict[str, float],
    parameter_overrides: dict[str, Any],
) -> None:
    contexts.append(
        {
            "scene_id": scene_id,
            "context_id": context_id,
            "editing_space_id": editing_space_id,
            "offset_ms": offset_ms,
            "time_scale": time_scale,
            "placement": placement,
            "parameter_overrides": parameter_overrides,
        }
    )


def _coordinate_space(
    space_id: str,
    composition: dict[str, Any] | None,
) -> dict[str, object]:
    if composition is None:
        return {
            "kind": "scene",
            "target_id": space_id,
            "width": None,
            "height": None,
        }
    return {
        "kind": composition.get("space_kind"),
        "target_id": composition.get("target_id"),
        "width": composition.get("width"),
        "height": composition.get("height"),
    }


def _render_contract(
    composition: dict[str, Any] | None,
) -> dict[str, object] | None:
    if composition is None:
        return None
    settings = _object(composition.get("render_settings"))
    return {
        "render_pipeline_revision": settings.get("render_pipeline_revision", 1),
        "resolution": {
            "width": settings.get("render_width"),
            "height": settings.get("render_height"),
        },
        "frame_rate": {
            "numerator": settings.get("frame_rate_numerator", 30000),
            "denominator": settings.get("frame_rate_denominator", 1001),
        },
        "working_color_space": settings.get("working_color_space", "linear_srgb"),
        "working_alpha_mode": settings.get("working_alpha_mode", "premultiplied"),
        "scaling_filter": settings.get("scaling_filter", "lanczos"),
        "output_background_color": settings.get("output_background_color", "#000000"),
        "media_end_behavior": settings.get("media_end_behavior", "hold_last_frame"),
        "output_profile": settings.get("output_profile"),
    }


def _walk_composition_contexts(
    contexts: list[dict[str, object]],
    compositions: dict[str, dict[str, Any]],
    scene_id: str,
    composition_id: str,
    context_id: str,
    offset_ms: int,
    time_scale: float,
    placement: dict[str, float],
    parameter_overrides: dict[str, Any],
    path: set[str],
) -> None:
    if composition_id in path:
        return
    composition = compositions.get(composition_id)
    if composition is None:
        return
    _append_context(
        contexts,
        scene_id=scene_id,
        context_id=context_id,
        editing_space_id=composition_id,
        offset_ms=offset_ms,
        time_scale=time_scale,
        placement=placement,
        parameter_overrides=parameter_overrides,
    )
    next_path = {*path, composition_id}
    for child_id in _string_items(composition.get("children")):
        child = compositions.get(child_id)
        if child is not None:
            _walk_composition_contexts(
                contexts,
                compositions,
                scene_id,
                child_id,
                f"{context_id}>{child_id}",
                offset_ms + round(_integer(child.get("local_time_offset_ms"), 0) * time_scale),
                time_scale,
                placement,
                parameter_overrides,
                next_path,
            )


def _scene_tracks(scene: dict[str, Any]) -> dict[str, dict[str, object]]:
    if scene.get("schema_version") == 2:
        tracks = {
            f"media:{output['id']}": {
                "id": f"media:{output['id']}",
                "kind": "media_output",
                "resource_binding": output["resource"],
            }
            for output in scene.get("media", [])
        }
        tracks.update(
            {
                f"lighting:{output['id']}": {
                    "id": f"lighting:{output['id']}",
                    "kind": "lighting_output",
                    "resource_binding": output["resource"],
                }
                for output in scene.get("lighting", [])
            }
        )
        return tracks
    scene_id = str(scene["id"])
    return {
        f"media:{scene_id}": {
            "id": f"media:{scene_id}",
            "kind": "media_output",
            "resource_binding": _object(scene.get("media")).get("resource"),
        },
        f"lighting:{scene_id}": {
            "id": f"lighting:{scene_id}",
            "kind": "lighting_output",
            "resource_binding": _object(scene.get("lighting")).get("resource"),
        },
    }


def _cue_time(
    cue: dict[str, Any],
    markers: dict[str, dict[str, Any]],
) -> int:
    marker_id = cue.get("marker_id")
    if isinstance(marker_id, str):
        marker = markers[marker_id]
        return _integer(marker.get("time_ms"), 0) + _integer(cue.get("marker_offset_ms"), 0)
    return _integer(cue.get("time_ms"), 0)


def _object_map(value: object) -> dict[str, dict[str, Any]]:
    return {
        str(key): _object(item) for key, item in _object(value).items() if isinstance(item, dict)
    }


def _list_map(value: object) -> dict[str, list[Any]]:
    return {str(key): item for key, item in _object(value).items() if isinstance(item, list)}


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_items(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise CompilationError("compiled composition graph requires lists")
    return value


def _string_items(value: object) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def _integer(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _number(value: object, fallback: float) -> float:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else fallback
    )
