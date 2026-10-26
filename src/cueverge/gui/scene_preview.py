import json
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import BoundedSemaphore
from typing import TYPE_CHECKING

from cueverge.core.loader import load_show_package
from cueverge.core.models import SceneDefinitionV2
from cueverge.gui.compiler import CompilationError, DraftCompiler
from cueverge.gui.contracts import DraftProject, SceneLightingPreviewRequest
from cueverge.gui.mapper_export import MapperExportError
from cueverge.gui.render_jobs import RenderJobError
from cueverge.lighting.ambient import (
    render_ambient,
    render_ambient_output,
    scene_inherits_ambient,
)
from cueverge.lighting.evaluation import evaluate_lighting_output

if TYPE_CHECKING:
    from cueverge.gui.service import GuiServices

_PREVIEW_SLOTS = BoundedSemaphore(2)


class ScenePreviewError(ValueError):
    pass


class ScenePreviewBusyError(ScenePreviewError):
    pass


@contextmanager
def _preview_slot() -> Generator[None, None, None]:
    if not _PREVIEW_SLOTS.acquire(blocking=False):
        raise ScenePreviewBusyError("Scene preview is busy; try again shortly.")
    try:
        yield
    finally:
        _PREVIEW_SLOTS.release()


def preview_project_scene(
    services: "GuiServices", project_id: str, request: SceneLightingPreviewRequest
) -> dict[str, object]:
    stored = services.repository.get_revision(project_id, request.source_revision)
    draft = request.proposed_draft if request.proposed_draft is not None else stored.draft
    if draft.editor_metadata.get("installation_id") != stored.draft.editor_metadata.get(
        "installation_id"
    ):
        raise ScenePreviewError("preview cannot change the project's installation binding")
    unsaved = draft != stored.draft
    lighting_artifacts: dict[str, Path] | None = None
    if not unsaved:
        try:
            lighting_artifacts = services.render_jobs.lighting_artifacts(
                stored.project.id, stored.revision
            )
        except (RenderJobError, MapperExportError, ValueError, OSError):
            lighting_artifacts = None
    return evaluate_scene_lighting(
        project_id=stored.project.id,
        source_revision=stored.revision,
        draft=draft,
        scene_id=request.scene_id,
        timestamp_ms=request.timestamp_ms,
        resource=request.resource,
        pixel_offset=request.pixel_offset,
        pixel_limit=request.pixel_limit,
        unsaved=unsaved,
        lighting_artifacts=lighting_artifacts,
    )


def evaluate_scene_lighting(
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    scene_id: str,
    timestamp_ms: int,
    resource: str | None = None,
    pixel_offset: int = 0,
    pixel_limit: int = 1000,
    unsaved: bool = False,
    lighting_artifacts: dict[str, Path] | None = None,
) -> dict[str, object]:
    scene = next((candidate for candidate in draft.scenes if candidate.id == scene_id), None)
    if scene is None:
        raise ScenePreviewError("scene does not exist")
    if not 0 <= timestamp_ms < scene.duration_ms:
        raise ScenePreviewError("preview time must be within the scene duration")
    if pixel_offset < 0 or not 1 <= pixel_limit <= 4096:
        raise ScenePreviewError("pixel offset/limit is outside the supported range")
    counts = {
        binding.resource: binding.pixel_count
        for binding in draft.installation.resources
        if binding.pixel_count is not None
    }
    work = sum(
        counts.get(output.resource, 0) * (len(output.timeline) + 1)
        for candidate in draft.scenes
        for output in (
            candidate.lighting
            if isinstance(candidate, SceneDefinitionV2)
            else (candidate.lighting,)
        )
    )
    editor_cues = draft.editor_metadata.get("timeline_cues")
    if isinstance(editor_cues, dict):
        work += max(counts.values(), default=0) * sum(
            len(values) for values in editor_cues.values() if isinstance(values, list)
        )
    if sum(counts.values()) > 65536 or work > 2_000_000:
        raise ScenePreviewError(
            "Scene preview exceeds its pixel/index budget; use offline rendering."
        )
    with (
        _preview_slot(),
        tempfile.TemporaryDirectory(prefix="cueverge-scene-preview-") as directory,
    ):
        compiler = DraftCompiler(Path(directory))
        bake_error: str | None = None
        try:
            compiled = compiler.compile(
                project_id=project_id,
                revision=source_revision,
                draft=draft,
                lighting_artifacts=lighting_artifacts or None,
            )
        except CompilationError as error:
            if not lighting_artifacts:
                raise
            bake_error = str(error)
            compiled = compiler.compile(
                project_id=project_id, revision=source_revision, draft=draft
            )
        scene_limitations: list[str] = []
        if bake_error is not None:
            scene_limitations.append(f"Baked LED compositions could not be used: {bake_error}.")
        missing = compiled.package_root / "runtime" / "lighting-bake-required.json"
        if missing.exists():
            required = set(json.loads(missing.read_text())["composition_ids"])
            graph = json.loads(
                (compiled.package_root / "runtime" / "composition-graph.json").read_text()
            )
            contexts = next(
                item["contexts"] for item in graph["scenes"] if item["scene_id"] == scene_id
            )
            compositions = draft.editor_metadata.get("compositions")
            names = sorted(
                {
                    _composition_name(compositions, str(context["editing_space_id"]))
                    for context in contexts
                    if context["editing_space_id"] in required
                }
            )
            # The preview package is temporary; the marker only blocks runtime playback.
            missing.unlink()
            if names and bake_error is None:
                scene_limitations.append(
                    "LED compositions not shown: "
                    + ", ".join(names)
                    + (
                        ". Save the scene, then bake them (Timeline editor > Bake LED "
                        "animation, or Render & bake > Bake LED overlay) to include them in "
                        "this preview."
                        if unsaved
                        else ". Bake them for this saved revision (Timeline editor > Bake LED "
                        "animation, or Render & bake > Bake LED overlay) to include them in "
                        "this preview."
                    )
                )
        catalog = load_show_package(compiled.package_root)
        loaded = catalog.require(scene_id)
        looks_by_resource = dict(catalog.lighting_looks_by_resource)
        if catalog.lighting_looks is not None:
            looks_by_resource.setdefault(
                catalog.lighting_looks.definition.resource, catalog.lighting_looks
            )
        outputs = tuple(
            output
            for output in loaded.lighting_outputs
            if resource is None or output.definition.resource == resource
        )
        lit = {output.definition.resource for output in loaded.lighting_outputs}
        ambient_only = tuple(
            looks
            for key, looks in sorted(looks_by_resource.items())
            if key not in lit and (resource is None or key == resource)
        )
        if not outputs and not ambient_only:
            raise ScenePreviewError("lighting output does not exist in this scene")
        if looks_by_resource:
            scene_limitations.append(
                "Ambient lighting is previewed from its loop start at scene time 0; live, it "
                "keeps running across scene changes."
                if any(
                    scene_inherits_ambient(looks, scene_id) for looks in looks_by_resource.values()
                )
                else "This scene does not inherit the show's ambient lighting."
            )
        samples = sum(
            min(pixel_limit, max(0, output.pixel_count - pixel_offset)) for output in outputs
        ) + sum(
            min(pixel_limit, max(0, looks.pixel_count - pixel_offset)) for looks in ambient_only
        )
        if samples > 4096:
            raise ScenePreviewError(
                "preview exceeds 4096 samples; select an output or smaller page"
            )
        result = []
        for output in outputs:
            if pixel_offset >= output.pixel_count:
                raise ScenePreviewError("pixel offset is outside the output")
            output_position_ms = max(0, timestamp_ms - output.definition.timing_offset_ms)
            looks = looks_by_resource.get(output.definition.resource)
            colors = evaluate_lighting_output(
                output,
                position_ms=output_position_ms,
                duration_ms=scene.duration_ms,
                base_frame=(
                    render_ambient(looks, timestamp_ms)
                    if looks is not None
                    and (output.spatial_sequences or output.definition.timeline)
                    and scene_inherits_ambient(looks, scene_id)
                    else None
                ),
                brightness_limit=(
                    min(output.definition.brightness_limit, looks.definition.brightness_limit)
                    if looks is not None
                    else None
                ),
            )
            end = min(output.pixel_count, pixel_offset + pixel_limit)
            result.append(
                {
                    "output_id": output.definition.id,
                    "resource": output.definition.resource,
                    "pixel_count": output.pixel_count,
                    "pixel_offset": pixel_offset,
                    "output_position_ms": output_position_ms,
                    "colors": [list(color) for color in colors[pixel_offset:end]],
                    "next_pixel_offset": end if end < output.pixel_count else None,
                    "limitations": (
                        ["This legacy renderer does not apply its output brightness limit."]
                        if output.definition.effect is not None
                        and output.definition.brightness_limit < 1
                        else []
                    ),
                }
            )
        for looks in ambient_only:
            if pixel_offset >= looks.pixel_count:
                raise ScenePreviewError("pixel offset is outside the output")
            colors = render_ambient_output(looks, timestamp_ms, scene_id)
            end = min(looks.pixel_count, pixel_offset + pixel_limit)
            result.append(
                {
                    "output_id": f"ambient:{looks.definition.resource}",
                    "resource": looks.definition.resource,
                    "pixel_count": looks.pixel_count,
                    "pixel_offset": pixel_offset,
                    "output_position_ms": timestamp_ms,
                    "colors": [list(color) for color in colors[pixel_offset:end]],
                    "next_pixel_offset": end if end < looks.pixel_count else None,
                    "limitations": [],
                }
            )
        return {
            "project_id": project_id,
            "source_revision": source_revision,
            "scene_id": scene_id,
            "timestamp_ms": timestamp_ms,
            "duration_ms": scene.duration_ms,
            "unsaved": unsaved,
            "evaluation": "production_lighting_renderer",
            "hardware_commands_sent": False,
            "outputs": result,
            "limitations": scene_limitations,
        }


def _composition_name(compositions: object, composition_id: str) -> str:
    composition = compositions.get(composition_id) if isinstance(compositions, dict) else None
    name = composition.get("name") if isinstance(composition, dict) else None
    return name if isinstance(name, str) and name else composition_id
