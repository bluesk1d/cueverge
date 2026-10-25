"""Scene-clock preview of media outputs (projector video/images and audio sources).

The preview mirrors what the controller sends each media output during a scene: a
scene composition whose render targets the output takes precedence, otherwise the
output's assigned asset plays. Playback starts after the output's start delay and
follows its end behavior. Frames come from the same compositors used for bakes;
nothing is sent to devices.
"""

import base64
import math
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from threading import BoundedSemaphore
from typing import TYPE_CHECKING, Literal, cast

import numpy as np
from PIL import Image

from cueverge.gui.asset_upload import AssetUploadError, StoredAsset
from cueverge.gui.contracts import DraftProject, SceneMediaPreviewRequest
from cueverge.gui.frame_compositor import (
    ByteImage,
    VideoFrameSource,
    decode_animated_image,
    decode_still,
    prepare_video_source,
    select_video_frame,
    working_to_srgb8,
)
from cueverge.gui.mapper_export import MapperExportError
from cueverge.gui.projector_overlap import ProjectorOverlapError, StoredProjectorOverlap
from cueverge.gui.projector_renderer import ProjectorRenderError, preview_projector_composition
from cueverge.gui.stage_coverage import StageCoverage
from cueverge.gui.stage_renderer import preview_stage_composition_member, selected_stage_projectors

if TYPE_CHECKING:
    from cueverge.gui.service import GuiServices

_PREVIEW_SLOTS = BoundedSemaphore(2)
MediaState = Literal["unassigned", "waiting", "playing", "looping", "holding", "stopped", "error"]


class SceneMediaPreviewError(ValueError):
    pass


class SceneMediaPreviewBusyError(SceneMediaPreviewError):
    pass


@dataclass(frozen=True)
class _Output:
    id: str
    resource: str
    asset: str | None
    required: bool
    start_delay_ms: int
    end_behavior: str
    repeat_count: int | None = None


@contextmanager
def _preview_slot() -> Generator[None, None, None]:
    if not _PREVIEW_SLOTS.acquire(blocking=False):
        raise SceneMediaPreviewBusyError("Scene media preview is busy; try again shortly.")
    try:
        yield
    finally:
        _PREVIEW_SLOTS.release()


def preview_project_scene_media(
    services: "GuiServices", project_id: str, request: SceneMediaPreviewRequest
) -> dict[str, object]:
    stored = services.repository.get_revision(project_id, request.source_revision)
    draft = request.proposed_draft if request.proposed_draft is not None else stored.draft
    if draft.editor_metadata.get("installation_id") != stored.draft.editor_metadata.get(
        "installation_id"
    ):
        raise SceneMediaPreviewError("preview cannot change the project's installation binding")
    with _preview_slot():
        return evaluate_scene_media(
            services,
            project_id=stored.project.id,
            source_revision=stored.revision,
            draft=draft,
            scene_id=request.scene_id,
            timestamp_ms=request.timestamp_ms,
            output_id=request.output_id,
            include_frames=request.include_frames,
            max_frame_width=request.max_frame_width,
            unsaved=draft != stored.draft,
        )


def evaluate_scene_media(
    services: "GuiServices",
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    scene_id: str,
    timestamp_ms: int,
    output_id: str | None = None,
    include_frames: bool = True,
    max_frame_width: int = 640,
    unsaved: bool = False,
) -> dict[str, object]:
    scene = next((candidate for candidate in draft.scenes if candidate.id == scene_id), None)
    if scene is None:
        raise SceneMediaPreviewError("scene does not exist")
    if not 0 <= timestamp_ms < scene.duration_ms:
        raise SceneMediaPreviewError("preview time must be within the scene duration")
    outputs = _scene_outputs(draft, scene_id)
    if output_id is not None:
        outputs = [output for output in outputs if output.id == output_id]
        if not outputs:
            raise SceneMediaPreviewError("media output does not exist in this scene")
    compositions = _mapping(draft.editor_metadata.get("compositions"))
    roots = _scene_root_compositions(draft, scene_id)
    stage_inputs: dict[str, object] | None = None
    results: list[dict[str, object]] = []
    for output in outputs:
        sources: list[str] = []
        for composition_id in roots:
            composition = _mapping(compositions.get(composition_id))
            kind = composition.get("space_kind")
            if kind == "projector_native" and composition.get("target_id") == output.resource:
                sources.append(composition_id)
            elif kind == "stage":
                if stage_inputs is None:
                    stage_inputs = _stage_inputs(services, draft)
                mapper_export = stage_inputs.get("mapper_export")
                if isinstance(mapper_export, dict):
                    try:
                        projectors = selected_stage_projectors(composition, mapper_export)
                    except ProjectorRenderError:
                        projectors = ()
                    if any(item.get("resource") == output.resource for item in projectors):
                        sources.append(composition_id)
        results.append(
            _evaluate_output(
                services,
                project_id=project_id,
                source_revision=source_revision,
                draft=draft,
                output=output,
                composition_ids=sources,
                timestamp_ms=timestamp_ms,
                include_frames=include_frames,
                max_frame_width=max_frame_width,
                stage_inputs=stage_inputs,
            )
        )
    return {
        "project_id": project_id,
        "source_revision": source_revision,
        "scene_id": scene_id,
        "timestamp_ms": timestamp_ms,
        "duration_ms": scene.duration_ms,
        "unsaved": unsaved,
        "evaluation": "controller_media_compositor",
        "hardware_commands_sent": False,
        "outputs": results,
        "limitations": [
            "Frames are rendered stills on the scene clock; real playback timing on media "
            "devices, codecs and projector calibration may differ.",
            "Browser audio preview uses review proxies and is approximate.",
        ],
    }


def _evaluate_output(
    services: "GuiServices",
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    output: _Output,
    composition_ids: list[str],
    timestamp_ms: int,
    include_frames: bool,
    max_frame_width: int,
    stage_inputs: dict[str, object] | None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "output_id": output.id,
        "resource": output.resource,
        "required": output.required,
        "start_delay_ms": output.start_delay_ms,
        "end_behavior": output.end_behavior,
        "repeat_count": output.repeat_count,
        "play_number": None,
        "source": None,
        "state": "unassigned",
        "media_position_ms": None,
        "media_duration_ms": None,
        "frame": None,
        "audio": [],
        "message": None,
    }
    if len(composition_ids) > 1:
        result["state"] = "error"
        result["message"] = (
            "Several scene compositions target this output; combine them into one before "
            "Apply. The controller refuses this configuration."
        )
        return result
    compositions = _mapping(draft.editor_metadata.get("compositions"))
    duration_ms: int | None
    if composition_ids:
        composition_id = composition_ids[0]
        composition = _mapping(compositions.get(composition_id))
        duration_ms = _integer(composition.get("duration_ms"))
        result["source"] = {
            "kind": "composition",
            "composition_id": composition_id,
            "name": str(composition.get("name") or composition_id),
            "space_kind": composition.get("space_kind"),
        }
    elif output.asset is not None:
        try:
            asset = services.asset_upload.get_asset(output.asset)
        except AssetUploadError:
            result["state"] = "error"
            result["message"] = "The assigned asset no longer exists. Assign another asset."
            return result
        duration_ms = _asset_duration(asset)
        result["source"] = {
            "kind": "asset",
            "asset_id": asset.id,
            "name": asset.name,
            "media_type": asset.media_type,
        }
        if asset.trashed_at is not None:
            result["message"] = "This asset is in the trash; restore it before publishing."
    else:
        result["message"] = "No media is assigned to this output."
        return result
    result["media_duration_ms"] = duration_ms
    state, position = _media_position(timestamp_ms, output, duration_ms)
    result["state"] = state
    result["media_position_ms"] = position
    if output.repeat_count is not None and duration_ms:
        elapsed = timestamp_ms - output.start_delay_ms
        result["play_number"] = (
            min(output.repeat_count, elapsed // duration_ms + 1) if elapsed >= 0 else None
        )
    if position is None:
        return result
    source = cast(dict[str, object], result["source"])
    try:
        if source["kind"] == "composition":
            result["audio"] = _composition_audio(draft, str(source["composition_id"]), position)
            if include_frames and source["space_kind"] in {"projector_native", "stage"}:
                result["frame"] = _png(
                    _composition_frame(
                        services,
                        project_id=project_id,
                        source_revision=source_revision,
                        draft=draft,
                        composition_id=str(source["composition_id"]),
                        resource=output.resource,
                        position_ms=position,
                        stage_inputs=stage_inputs,
                    ),
                    max_frame_width,
                )
        else:
            asset, path = services.asset_upload.get_original(str(source["asset_id"]))
            if _has_audio(asset):
                result["audio"] = [
                    {
                        "asset_id": asset.id,
                        "name": asset.name,
                        "source_position_ms": position,
                        "gain_db": 0.0,
                        "mute": False,
                    }
                ]
            if include_frames and (asset.media_type or "").startswith(("image/", "video/")):
                result["frame"] = _png(_asset_frame(asset, str(path), position), max_frame_width)
    except (ProjectorRenderError, AssetUploadError, ValueError, OSError) as error:
        result["state"] = "error"
        result["message"] = f"Frame could not be rendered: {error}"
        result["frame"] = None
    return result


def _media_position(
    timestamp_ms: int,
    output: _Output,
    duration_ms: int | None,
) -> tuple[MediaState, int | None]:
    position = timestamp_ms - output.start_delay_ms
    if position < 0:
        return "waiting", None
    if duration_ms is None:
        return "playing", position
    plays = output.repeat_count or 1
    if position < duration_ms * plays:
        return "playing", position % duration_ms
    if output.end_behavior == "loop":
        return "looping", position % duration_ms
    if output.end_behavior == "stop":
        return "stopped", None
    return "holding", duration_ms - 1


def _scene_outputs(draft: DraftProject, scene_id: str) -> list[_Output]:
    scene = next(candidate for candidate in draft.scenes if candidate.id == scene_id)
    if scene.schema_version == 1:
        media = scene.media
        return [
            _Output(
                id="media",
                resource=media.resource,
                asset=media.asset,
                required=True,
                start_delay_ms=0,
                end_behavior="hold_last_frame",
            )
        ]
    return [
        _Output(
            id=output.id,
            resource=output.resource,
            asset=output.asset,
            required=output.required,
            start_delay_ms=output.start_delay_ms,
            end_behavior=output.end_behavior,
            repeat_count=output.repeat_count,
        )
        for output in scene.media
    ]


def _scene_root_compositions(draft: DraftProject, scene_id: str) -> list[str]:
    roots = _mapping(draft.editor_metadata.get("scene_compositions")).get(scene_id, [])
    identifiers = (
        [item for item in roots if isinstance(item, str)] if isinstance(roots, list) else []
    )
    instances = _mapping(draft.editor_metadata.get("composition_instances")).get(scene_id, [])
    if isinstance(instances, list):
        identifiers.extend(
            str(item["composition_id"])
            for item in instances
            if isinstance(item, dict) and isinstance(item.get("composition_id"), str)
        )
    return list(dict.fromkeys(identifiers))


def _stage_inputs(services: "GuiServices", draft: DraftProject) -> dict[str, object]:
    installation_id = draft.editor_metadata.get("installation_id")
    if not isinstance(installation_id, str):
        return {}
    try:
        return {
            "installation_id": installation_id,
            "mapper_export": services.mapper_export.export(installation_id),
            "coverage": services.stage_coverage.analyze(installation_id),
            "overlaps": services.projector_overlap.list(installation_id),
        }
    except (MapperExportError, ProjectorOverlapError, LookupError, ValueError):
        # An installation without a usable mapping simply has no whole-display outputs.
        return {}


def _composition_frame(
    services: "GuiServices",
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    composition_id: str,
    resource: str,
    position_ms: int,
    stage_inputs: dict[str, object] | None,
) -> ByteImage:
    composition = _mapping(_mapping(draft.editor_metadata.get("compositions")).get(composition_id))
    if composition.get("space_kind") == "stage":
        if not stage_inputs or "mapper_export" not in stage_inputs:
            raise ProjectorRenderError("whole-display preview needs a mapped installation")
        return cast(
            ByteImage,
            preview_stage_composition_member(
                project_id=project_id,
                source_revision=source_revision,
                draft=draft,
                composition_id=composition_id,
                assets=services.asset_upload,
                mapper_export=cast(dict[str, object], stage_inputs["mapper_export"]),
                coverage=cast(StageCoverage, stage_inputs["coverage"]),
                overlaps=cast(tuple[StoredProjectorOverlap, ...], stage_inputs["overlaps"]),
                resource=resource,
                timestamp_ms=position_ms,
            ),
        )
    return preview_projector_composition(
        project_id=project_id,
        draft=draft,
        composition_id=composition_id,
        timestamp_ms=position_ms,
        assets=services.asset_upload,
    )


def _composition_audio(
    draft: DraftProject, composition_id: str, position_ms: int
) -> list[dict[str, object]]:
    layers = _mapping(draft.editor_metadata.get("timeline_transform_layers")).get(
        composition_id, []
    )
    audio: list[dict[str, object]] = []
    for item in layers if isinstance(layers, list) else []:
        layer = _mapping(item)
        if layer.get("kind") != "audio" or not isinstance(layer.get("source_id"), str):
            continue
        start = _integer(layer.get("start_ms"))
        duration = _integer(layer.get("duration_ms"))
        if not start <= position_ms < start + duration:
            continue
        settings = _mapping(layer.get("audio"))
        audio.append(
            {
                "asset_id": layer["source_id"],
                "name": str(layer.get("name") or layer["source_id"]),
                "source_position_ms": _integer(layer.get("in_point_ms")) + position_ms - start,
                "gain_db": float(cast(float, settings.get("gain_db", 0.0)) or 0.0),
                "mute": bool(settings.get("mute", False)),
            }
        )
    return audio


def _asset_frame(asset: StoredAsset, path: str, position_ms: int) -> ByteImage:
    media_type = asset.media_type or ""
    if media_type == "image/gif":
        frames, duration = decode_animated_image(
            path, alpha_interpretation="straight", source_color_space="srgb"
        )
        image = select_video_frame(frames, position_ms % max(1, duration)).image
    elif media_type.startswith("image/"):
        image = decode_still(path, alpha_interpretation="straight", source_color_space="srgb")
    else:
        source: VideoFrameSource = prepare_video_source(
            path, alpha_interpretation="opaque", source_color_space="srgb"
        )
        image = source.frame_at(position_ms)
    return working_to_srgb8(image)


def _asset_duration(asset: StoredAsset) -> int | None:
    if (asset.media_type or "").startswith("image/") and asset.media_type != "image/gif":
        return None
    value = (asset.metadata or {}).get("duration_ms")
    return value if isinstance(value, int) and value > 0 else None


def _has_audio(asset: StoredAsset) -> bool:
    media_type = asset.media_type or ""
    if media_type.startswith("audio/"):
        return True
    codecs = (asset.metadata or {}).get("audio_codecs")
    return media_type.startswith("video/") and isinstance(codecs, list) and bool(codecs)


def _png(image: ByteImage, max_width: int) -> dict[str, object]:
    array = np.asarray(image, dtype=np.uint8)
    picture = Image.fromarray(array, mode="RGBA" if array.shape[2] == 4 else "RGB")
    native = picture.size
    if picture.width > max_width:
        height = max(1, math.floor(picture.height * max_width / picture.width))
        picture = picture.resize((max_width, height), Image.Resampling.BILINEAR)
    output = BytesIO()
    picture.save(output, format="PNG")
    return {
        "media_type": "image/png",
        "width": picture.width,
        "height": picture.height,
        "native_width": native[0],
        "native_height": native[1],
        "data_base64": base64.b64encode(output.getvalue()).decode("ascii"),
    }


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
