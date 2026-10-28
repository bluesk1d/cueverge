"""Bakes for media layers placed directly on a scene's media output tracks.

A scene output plays its asset as-is. Audio layers added to that output's track on the
scene ("Whole scene") timeline are mixed with the asset's own soundtrack into a new file:
the video stream is copied unchanged and only the audio is re-encoded. The result is a
normal render artifact, so packaging, staging and playback treat it like any other bake.
"""

import hashlib
import json
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

from cueverge.gui import media_tools
from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject
from cueverge.gui.frame_compositor import (
    AudioLayerBuffer,
    decode_audio_stereo_48k,
    mix_audio_layers,
)
from cueverge.gui.projector_renderer import ProjectorRenderError, prepare_media_layers
from cueverge.gui.render_encoder import _write_manifest
from cueverge.gui.render_pipeline import RENDER_PIPELINE_REVISION
from cueverge.gui.render_verification import VERIFICATION_POLICY_REVISION

SCENE_OUTPUT_PREFIX = "scene-output:"
SCENE_OUTPUT_RENDERER = "scene-output-remux-v1"
VISUAL_KINDS = frozenset({"image", "video", "composition"})


class SceneOutputBakeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneOutputBake:
    """A scene media output whose track carries scene-level media layers."""

    composition_id: str
    scene_id: str
    scene_name: str
    output_id: str
    resource: str
    asset_id: str | None
    end_behavior: str
    layers: tuple[dict[str, object], ...]

    @property
    def label(self) -> str:
        return f"{self.scene_name} · {self.resource}"

    def problem(self) -> str | None:
        """Why this output can't be baked, or None."""
        visual = [
            str(layer.get("name") or layer.get("id"))
            for layer in self.layers
            if layer.get("kind") in VISUAL_KINDS
        ]
        if visual:
            return (
                f"{self.label}: {', '.join(visual)} can't be drawn on a whole-scene output "
                "track; move visual layers into a composition."
            )
        if self.asset_id is None:
            return f"{self.label}: place a media asset on this output before adding audio layers."
        return None


def scene_output_composition_id(scene_id: str, output_id: str) -> str:
    return f"{SCENE_OUTPUT_PREFIX}{scene_id}:{output_id}"


def is_scene_output_id(composition_id: str) -> bool:
    return composition_id.startswith(SCENE_OUTPUT_PREFIX)


def scene_output_bakes(draft: DraftProject) -> tuple[SceneOutputBake, ...]:
    """Every scene media output with layers on its whole-scene timeline track."""
    layers_by_space = _mapping(draft.editor_metadata.get("timeline_transform_layers"))
    result: list[SceneOutputBake] = []
    for scene in draft.scenes:
        raw_layers = layers_by_space.get(scene.id)
        if not isinstance(raw_layers, list) or not raw_layers:
            continue
        outputs = scene.media if isinstance(scene.media, tuple) else (scene.media,)
        for output in outputs:
            output_id = str(getattr(output, "id", "media"))
            track = f"media:{output_id}"
            layers = tuple(
                cast(dict[str, object], item)
                for item in raw_layers
                if isinstance(item, dict) and item.get("track_id") == track
            )
            if not layers:
                continue
            result.append(
                SceneOutputBake(
                    composition_id=scene_output_composition_id(scene.id, output_id),
                    scene_id=scene.id,
                    scene_name=scene.name,
                    output_id=output_id,
                    resource=output.resource,
                    asset_id=output.asset,
                    end_behavior=str(getattr(output, "end_behavior", "stop")),
                    layers=layers,
                )
            )
    return tuple(result)


def find_scene_output_bake(draft: DraftProject, composition_id: str) -> SceneOutputBake:
    bake = next(
        (item for item in scene_output_bakes(draft) if item.composition_id == composition_id),
        None,
    )
    if bake is None:
        raise SceneOutputBakeError("scene output has no scene-level media layers to bake")
    return bake


def scene_output_cache_payload(
    bake: SceneOutputBake, assets: AssetUploadService, project_id: str
) -> tuple[dict[str, object], dict[str, str]]:
    """The inputs that determine the baked file, and their source checksums."""
    problem = bake.problem()
    if problem is not None:
        raise SceneOutputBakeError(problem)
    assert bake.asset_id is not None
    checksums: dict[str, str] = {}
    for source_id in [bake.asset_id, *(str(layer.get("source_id")) for layer in bake.layers)]:
        asset = assets.get_asset(source_id)
        if asset.project_id != project_id or asset.digest_sha256 is None:
            raise SceneOutputBakeError(f"{bake.label}: media source {source_id} is unavailable")
        checksums[source_id] = asset.digest_sha256
    composition: dict[str, object] = {
        "renderer": SCENE_OUTPUT_RENDERER,
        "space_kind": "scene_output",
        "scene_id": bake.scene_id,
        "output_id": bake.output_id,
        "resource": bake.resource,
        "asset_id": bake.asset_id,
        "end_behavior": bake.end_behavior,
        "layers": sorted(bake.layers, key=lambda layer: str(layer.get("id"))),
    }
    return composition, checksums


def render_scene_output(
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    composition_id: str,
    assets: AssetUploadService,
    output_directory: Path,
) -> dict[str, object]:
    bake = find_scene_output_bake(draft, composition_id)
    composition, checksums = scene_output_cache_payload(bake, assets, project_id)
    assert bake.asset_id is not None
    base, base_path = assets.get_original(bake.asset_id)
    metadata = base.metadata or {}
    duration_ms = metadata.get("duration_ms")
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms <= 0:
        raise SceneOutputBakeError(f"{bake.label}: the output's media has no known duration")
    total_samples = duration_ms * 48
    try:
        prepared = prepare_media_layers(
            project_id=project_id, raw_layers=list(bake.layers), assets=assets
        )
    except (ProjectorRenderError, ValueError) as error:
        raise SceneOutputBakeError(f"{bake.label}: {error}") from error
    layers = list(prepared.audio_layers)
    if metadata.get("audio_codecs"):
        original = decode_audio_stereo_48k(str(base_path))[:total_samples]
        # The output's own soundtrack always plays; solo only chooses among added layers.
        layers.insert(
            0,
            AudioLayerBuffer(
                samples=original,
                start_sample=0,
                gain_db=0.0,
                mute=False,
                solo=any(layer.solo for layer in layers),
                mix_order=-1,
                fade_in_samples=0,
                fade_out_samples=0,
            ),
        )
    mixed = mix_audio_layers(tuple(layers), total_samples=total_samples)
    output_directory.mkdir(parents=True, exist_ok=True)
    activation_identity = str(uuid4())
    temporary = Path(tempfile.mkdtemp(prefix=".scene-output-", dir=output_directory))
    try:
        filename = "member-0001.mp4"
        target = temporary / filename
        try:
            video = media_tools.remux_with_audio(
                source=base_path, audio_samples=mixed, target=target
            )
            boundary = media_tools.audio_boundary(target)
        except media_tools.MediaToolError as error:
            raise SceneOutputBakeError(f"{bake.label}: {error}") from error
        frame_rate = metadata.get("frame_rate")
        numerator, denominator = _frame_rate(frame_rate)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest: dict[str, object] = {
            "schema_version": 1,
            "activation_identity": activation_identity,
            "source_revision": source_revision,
            "composition_id": composition_id,
            "composition_digest": hashlib.sha256(
                json.dumps(composition, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "source_checksums": checksums,
            "duration_ms": duration_ms,
            "frame_rate": {"numerator": numerator, "denominator": denominator},
            "frame_time_origin_ms": 0,
            "render_pipeline_revision": RENDER_PIPELINE_REVISION,
            "verification_policy_revision": VERIFICATION_POLICY_REVISION,
            "audio_owner": bake.resource,
            "media_end_behavior": bake.end_behavior,
            "hold_last_frame": bake.end_behavior == "hold_last_frame",
            "calibration_revisions": {bake.resource: 0},
            "encoder": {
                "identity": media_tools.engine_identity(),
                "name": "remux+aac",
                "video": "copied from the output's media",
                "audio_codec": "aac",
                "audio_sample_rate": 48000,
                "audio_channel_layout": "stereo",
            },
            "scene_output": {
                "scene_id": bake.scene_id,
                "output_id": bake.output_id,
                "base_asset_id": bake.asset_id,
                "layer_ids": [str(layer.get("id")) for layer in bake.layers],
            },
            "members": [
                {
                    "resource": bake.resource,
                    "filename": filename,
                    "digest_sha256": digest,
                    "size_bytes": target.stat().st_size,
                    "has_audio": True,
                    "streams": {
                        "video": video or None,
                        "audio": {
                            "codec": "aac",
                            "sample_rate": 48000,
                            "channel_layout": "stereo",
                            "sample_count": int(mixed.shape[0]),
                            "boundary": boundary,
                        },
                    },
                }
            ],
        }
        _write_manifest(temporary, manifest)
        destination = output_directory / activation_identity
        temporary.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _frame_rate(value: object) -> tuple[int, int]:
    if isinstance(value, Mapping):
        numerator, denominator = value.get("numerator"), value.get("denominator")
        if isinstance(numerator, int) and isinstance(denominator, int) and denominator > 0:
            return numerator, denominator
    if isinstance(value, (int, float)) and value > 0:
        return round(float(value) * 1000), 1000
    return 30, 1


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}
