import json
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import BoundedSemaphore
from typing import TYPE_CHECKING

from cueverge.core.loader import load_show_package
from cueverge.core.models import SceneDefinitionV2
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.contracts import DraftProject, SceneLightingPreviewRequest
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
        compiled = DraftCompiler(Path(directory)).compile(
            project_id=project_id,
            revision=source_revision,
            draft=draft,
        )
        missing = compiled.package_root / "runtime" / "lighting-bake-required.json"
        if missing.exists():
            required = set(json.loads(missing.read_text())["composition_ids"])
            graph = json.loads(
                (compiled.package_root / "runtime" / "composition-graph.json").read_text()
            )
            contexts = next(
                item["contexts"] for item in graph["scenes"] if item["scene_id"] == scene_id
            )
            if any(context["editing_space_id"] in required for context in contexts):
                raise ScenePreviewError(
                    "This scene uses spatial lighting compositions. "
                    "Use the advanced composition preview; raw-scene preview does not replace it."
                )
        loaded = load_show_package(compiled.package_root).require(scene_id)
        outputs = tuple(
            output
            for output in loaded.lighting_outputs
            if resource is None or output.definition.resource == resource
        )
        if not outputs:
            raise ScenePreviewError("lighting output does not exist in this scene")
        samples = sum(
            min(pixel_limit, max(0, output.pixel_count - pixel_offset)) for output in outputs
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
            colors = evaluate_lighting_output(
                output, position_ms=output_position_ms, duration_ms=scene.duration_ms
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
        }
