from typing import cast

from pydantic import JsonValue

from cueverge.gui.asset_upload import AssetUploadError, AssetUploadService
from cueverge.gui.contracts import DraftProject


class PreviewEvaluationError(RuntimeError):
    pass


def evaluate_preview_state(
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    space_id: str,
    timestamp_ms: int,
    assets: AssetUploadService,
) -> dict[str, JsonValue]:
    metadata = cast(dict[str, object], draft.editor_metadata)
    compositions = _mapping(metadata.get("compositions"))
    composition = _mapping(compositions.get(space_id))
    scene = next((candidate for candidate in draft.scenes if candidate.id == space_id), None)
    if composition:
        duration_ms = _integer(composition.get("duration_ms"))
        settings = _mapping(composition.get("render_settings"))
        numerator = _integer(settings.get("frame_rate_numerator"), default=30)
        denominator = _integer(settings.get("frame_rate_denominator"), default=1)
        resources = [_string(composition.get("target_id"))]
        source_ids = _composition_sources(metadata, space_id)
        space_kind = _string(composition.get("space_kind"))
    elif scene is not None:
        duration_ms = scene.duration_ms
        numerator = 30
        denominator = 1
        resources = list(scene.resources)
        source_ids = _scene_sources(scene.model_dump(mode="json"))
        space_kind = "scene"
    else:
        raise PreviewEvaluationError("preview space does not exist")
    if timestamp_ms < 0 or timestamp_ms > duration_ms:
        raise PreviewEvaluationError("preview timestamp is outside duration")
    frame_index = min(
        max(0, (timestamp_ms * numerator) // (1000 * denominator)),
        max(0, (duration_ms * numerator - 1) // (1000 * denominator)),
    )
    frame_timestamp_ms = (frame_index * 1000 * denominator) / numerator
    degraded_resources: list[dict[str, JsonValue]] = []
    for source_id in source_ids:
        try:
            asset = assets.get_asset(source_id)
        except AssetUploadError:
            degraded_resources.append({"source_id": source_id, "reason": "asset_missing"})
            continue
        if asset.status != "ready":
            degraded_resources.append(
                {
                    "source_id": source_id,
                    "reason": f"asset_{asset.status}",
                    "detail": asset.error,
                }
            )
        elif asset.original_file_status != "available":
            degraded_resources.append(
                {
                    "source_id": source_id,
                    "reason": f"original_{asset.original_file_status}",
                }
            )
        elif asset.artifact_faults:
            degraded_resources.append(
                {
                    "source_id": source_id,
                    "reason": "review_artifact_fault",
                    "detail": "; ".join(asset.artifact_faults),
                }
            )
    return cast(
        dict[str, JsonValue],
        {
            "project_id": project_id,
            "source_revision": source_revision,
            "space_id": space_id,
            "space_kind": space_kind,
            "timestamp_ms": timestamp_ms,
            "duration_ms": duration_ms,
            "frame_rate": {"numerator": numerator, "denominator": denominator},
            "frame_index": frame_index,
            "frame_timestamp_ms": frame_timestamp_ms,
            "timing_error_ms": timestamp_ms - frame_timestamp_ms,
            "resources": resources,
            "degraded_resources": degraded_resources,
            "evaluation": "backend_deterministic",
            "hardware_command_sent": False,
        },
    )


def _composition_sources(metadata: dict[str, object], space_id: str) -> list[str]:
    layers = _mapping(metadata.get("timeline_transform_layers")).get(space_id, [])
    if not isinstance(layers, list):
        raise PreviewEvaluationError("composition transform layers are invalid")
    return sorted(
        {
            source_id
            for layer in layers
            if isinstance(layer, dict)
            and isinstance((source_id := layer.get("source_id")), str)
            and layer.get("kind") in {"image", "video", "audio"}
        }
    )


def _scene_sources(scene: dict[str, object]) -> list[str]:
    media = scene.get("media")
    records = media if isinstance(media, list) else [media]
    return sorted(
        {
            asset
            for record in records
            if isinstance(record, dict) and isinstance((asset := record.get("asset")), str)
        }
    )


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _integer(value: object, *, default: int | None = None) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if default is not None:
        return default
    raise PreviewEvaluationError("preview timing metadata is invalid")


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PreviewEvaluationError("preview resource metadata is invalid")
    return value
