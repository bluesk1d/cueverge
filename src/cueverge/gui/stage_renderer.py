import hashlib
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import numpy as np

from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.contracts import DraftProject
from cueverge.gui.frame_compositor import (
    compose_visual_frame,
    conform_preview_pixel_format,
    mix_audio_layers,
    overlap_distance_field_masks,
    planar_projector_mesh,
    polygon_mask,
    render_stage_to_projector,
    working_to_srgb8,
)
from cueverge.gui.projector_overlap import StoredProjectorOverlap
from cueverge.gui.projector_renderer import (
    ProjectorRenderError,
    prepare_media_layers,
)
from cueverge.gui.render_encoder import EncoderProfile, LightingMember, encode_output_set
from cueverge.gui.stage_coverage import StageCoverage
from cueverge.gui.sync_rehearsal import SyncAdmission
from cueverge.lighting.sequence import MAX_SEQUENCE_BYTES


@dataclass(frozen=True)
class PreparedStageOutput:
    members: dict[str, tuple[np.ndarray, ...]]
    audio_owner: str
    mixed_audio: np.ndarray
    frame_rate_numerator: int
    frame_rate_denominator: int
    duration_ms: int
    profile: EncoderProfile
    composition_digest: str
    source_checksums: dict[str, str]
    calibration_revisions: dict[str, int]
    media_end_behavior: str
    lighting_members: tuple[LightingMember, ...]


def prepare_stage_composition(
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    composition_id: str,
    assets: AssetUploadService,
    mapper_export: dict[str, object],
    coverage: StageCoverage,
    overlaps: tuple[StoredProjectorOverlap, ...],
    sync_admission: SyncAdmission,
) -> PreparedStageOutput:
    if not sync_admission.allowed:
        raise ProjectorRenderError(
            f"stage render blocked: {sync_admission.code}: {sync_admission.message}"
        )
    metadata = cast(dict[str, object], draft.editor_metadata)
    compositions = _mapping(metadata.get("compositions"))
    composition = _mapping(compositions.get(composition_id))
    if composition.get("space_kind") != "stage":
        raise ProjectorRenderError("stage runner requires a stage-space composition")
    settings = _mapping(composition.get("render_settings"))
    render_width = _integer(settings.get("render_width"))
    render_height = _integer(settings.get("render_height"))
    duration_ms = _integer(composition.get("duration_ms"))
    frame_rate_numerator = _integer(settings.get("frame_rate_numerator"))
    frame_rate_denominator = _integer(settings.get("frame_rate_denominator"))
    logical_width = _number(composition.get("width"))
    logical_height = _number(composition.get("height"))
    stage_bounds = (0.0, 0.0, logical_width, logical_height)
    raw_layers = _mapping(metadata.get("timeline_transform_layers")).get(composition_id, [])
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ProjectorRenderError("stage composition has no media layers")
    prepared = prepare_media_layers(
        project_id=project_id,
        raw_layers=raw_layers,
        assets=assets,
    )
    stage_layers = tuple(
        replace(
            layer,
            keyframes=_scale_stage_keyframes(
                layer.keyframes,
                render_width / logical_width,
                render_height / logical_height,
            ),
        )
        for layer in prepared.visual_layers
    )
    frame_count = max(
        1,
        (duration_ms * frame_rate_numerator + 1000 * frame_rate_denominator - 1)
        // (1000 * frame_rate_denominator),
    )
    lighting_surfaces = selected_stage_lighting_surfaces(composition, mapper_export)
    pixel_count = sum(len(_list(surface.get("pixels"))) for surface in lighting_surfaces)
    if pixel_count:
        if frame_rate_numerator > 60 * frame_rate_denominator:
            raise ProjectorRenderError("mixed stage lighting supports at most 60 fps")
        if pixel_count * frame_count * 3 > MAX_SEQUENCE_BYTES:
            raise ProjectorRenderError("stage lighting exceeds the 256 MiB sequence budget")
    stage_frames = tuple(
        compose_visual_frame(
            stage_layers,
            timestamp_ms=(index * 1000 * frame_rate_denominator) // frame_rate_numerator,
            output_width=render_width,
            output_height=render_height,
            background_rgb=_hex_rgb(_string(settings.get("output_background_color"))),
        )[0]
        for index in range(frame_count)
    )
    lighting_members = _prepare_stage_lighting(
        stage_frames,
        lighting_surfaces,
        logical_width,
        logical_height,
    )
    projectors = [_mapping(item) for item in _list(mapper_export.get("projectors"))]
    if not projectors or (len(projectors) < 2 and not lighting_members):
        raise ProjectorRenderError(
            "stage render requires two projectors or a projector with selected LED surfaces"
        )
    projector_by_geometry = {
        _string(projector.get("geometry_id")): projector for projector in projectors
    }
    stage_masks = {
        _string(projector.get("resource")): np.ones((render_height, render_width), dtype=np.float32)
        for projector in projectors
    }
    adjustments: dict[str, tuple[float, float, tuple[float, ...]]] = {}
    for region in coverage.regions:
        if region.kind != "occluded":
            continue
        occlusion = polygon_mask(
            output_width=render_width,
            output_height=render_height,
            stage_bounds=stage_bounds,
            polygon=region.points,
        )
        for resource in region.resources:
            if resource in stage_masks:
                stage_masks[resource] *= np.float32(1) - occlusion
    for overlap in overlaps:
        first = projector_by_geometry.get(overlap.first_geometry_id)
        second = projector_by_geometry.get(overlap.second_geometry_id)
        if first is None or second is None:
            continue
        first_resource = _string(first.get("resource"))
        second_resource = _string(second.get("resource"))
        first_exclusive = _exclusive_polygons(coverage, first_resource, second_resource)
        second_exclusive = _exclusive_polygons(coverage, second_resource, first_resource)
        first_mask, second_mask = overlap_distance_field_masks(
            output_width=render_width,
            output_height=render_height,
            stage_bounds=stage_bounds,
            overlap_polygon=overlap.stage_points,
            first_exclusive_polygons=first_exclusive,
            second_exclusive_polygons=second_exclusive,
            ramp_width=overlap.ramp_width,
            gamma=overlap.overlap_gamma,
        )
        stage_masks[first_resource] *= first_mask
        stage_masks[second_resource] *= second_mask
        adjustments[first_resource] = (
            overlap.first_brightness,
            overlap.first_black_level,
            _white_matrix(overlap.first_white, overlap.first_color_matrix),
        )
        adjustments[second_resource] = (
            overlap.second_brightness,
            overlap.second_black_level,
            _white_matrix(overlap.second_white, overlap.second_color_matrix),
        )
    members: dict[str, tuple[np.ndarray, ...]] = {}
    calibration_revisions: dict[str, int] = {}
    for projector in projectors:
        resource = _string(projector.get("resource"))
        projector_width = _integer(projector.get("native_width"))
        projector_height = _integer(projector.get("native_height"))
        mesh_vertices = _mesh_vertices(projector)
        mesh_triangles = _mesh_triangles(projector)
        stage_mask = stage_masks[resource]
        mask_rgba = np.repeat(stage_mask[..., None], 4, axis=2).astype(
            np.float32,
            copy=False,
        )
        projector_mask = render_stage_to_projector(
            mask_rgba,
            stage_bounds=stage_bounds,
            projector_width=projector_width,
            projector_height=projector_height,
            mesh_vertices=mesh_vertices,
            mesh_triangles=mesh_triangles,
        )[..., 3]
        brightness, black_level, color_matrix = adjustments.get(
            resource,
            (
                _number(projector.get("brightness_limit")),
                0.0,
                (1, 0, 0, 0, 1, 0, 0, 0, 1),
            ),
        )
        members[resource] = tuple(
            working_to_srgb8(
                render_stage_to_projector(
                    stage_frame,
                    stage_bounds=stage_bounds,
                    projector_width=projector_width,
                    projector_height=projector_height,
                    mesh_vertices=mesh_vertices,
                    mesh_triangles=mesh_triangles,
                    blend_alpha=projector_mask,
                    brightness=brightness,
                    black_level=black_level,
                    color_matrix=color_matrix,
                )
            )
            for stage_frame in stage_frames
        )
        calibration_revisions[resource] = _integer(projector.get("calibration_revision"))
    audio_owner = _audio_owner(draft, composition_id, set(members))
    mixed_audio = mix_audio_layers(
        prepared.audio_layers,
        total_samples=duration_ms * 48,
    )
    output_profile = _mapping(settings.get("output_profile"))
    profile = EncoderProfile(
        video_profile=_string(output_profile.get("video_profile")),
        video_level=_string(output_profile.get("video_level")),
        pixel_format=_string(output_profile.get("pixel_format")),
        max_video_bitrate_kbps=_integer(output_profile.get("max_video_bitrate_kbps")),
        keyframe_interval_frames=_integer(output_profile.get("keyframe_interval_frames")),
        b_frames=_integer(output_profile.get("b_frames")),
        loudness_target_lufs=_number(output_profile.get("loudness_target_lufs")),
    )
    composition_digest = hashlib.sha256(
        json.dumps(composition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PreparedStageOutput(
        members=members,
        audio_owner=audio_owner,
        mixed_audio=mixed_audio,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        duration_ms=duration_ms,
        profile=profile,
        composition_digest=composition_digest,
        source_checksums=prepared.source_checksums,
        calibration_revisions=calibration_revisions,
        media_end_behavior=_string(settings.get("media_end_behavior")),
        lighting_members=lighting_members,
    )


def selected_stage_lighting_surfaces(
    composition: dict[str, object],
    mapper_export: dict[str, object],
) -> tuple[dict[str, object], ...]:
    selected = composition.get("stage_lighting_surfaces", [])
    if (
        not isinstance(selected, list)
        or not all(isinstance(item, str) and item for item in selected)
        or len(selected) != len(set(selected))
    ):
        raise ProjectorRenderError("stage lighting selection must contain unique semantic IDs")
    surfaces = {
        _string(_mapping(raw).get("semantic_id")): _mapping(raw)
        for raw in _list(mapper_export.get("lighting_surfaces"))
    }
    if set(selected).difference(surfaces):
        raise ProjectorRenderError("selected stage lighting surface is not mapped")
    return tuple(surfaces[str(semantic_id)] for semantic_id in selected)


def _sample_stage_surface(
    frame: np.ndarray,
    surface: dict[str, object],
    width: float,
    height: float,
) -> list[dict[str, object]]:
    brightness = _number(surface.get("brightness_limit"))
    if not math.isfinite(brightness) or not 0 <= brightness <= 1:
        raise ProjectorRenderError("stage lighting brightness is invalid")
    samples: list[dict[str, object]] = []
    for raw in _list(surface.get("pixels")):
        pixel = _mapping(raw)
        x, y = _number(pixel.get("stage_x")), _number(pixel.get("stage_y"))
        output_index = _integer(pixel.get("output_index"))
        if not (0 <= x <= width and 0 <= y <= height) or output_index < 0:
            raise ProjectorRenderError(
                "stage lighting pixel is outside the composition or unmapped"
            )
        requested = frame[
            round(y / height * (frame.shape[0] - 1)),
            round(x / width * (frame.shape[1] - 1)),
            :3,
        ]
        color = np.rint(requested.astype(np.float32) * brightness).astype(np.uint8)
        samples.append(
            {
                "output_index": output_index,
                "requested_color": requested.tolist(),
                "color": color.tolist(),
            }
        )
    if not samples:
        raise ProjectorRenderError("selected stage lighting surface has no mapped pixels")
    return samples


def _prepare_stage_lighting(
    frames: tuple[np.ndarray, ...],
    surfaces: tuple[dict[str, object], ...],
    width: float,
    height: float,
) -> tuple[LightingMember, ...]:
    if not surfaces:
        return ()
    indices: dict[str, list[int]] = {}
    buffers: dict[str, bytearray] = {}
    for frame in frames:
        grouped: dict[str, dict[int, list[int]]] = {}
        srgb = working_to_srgb8(frame)
        for surface in surfaces:
            resource = _string(surface.get("resource"))
            samples = grouped.setdefault(resource, {})
            for sample in _sample_stage_surface(srgb, surface, width, height):
                index = _integer(sample["output_index"])
                if index in samples:
                    raise ProjectorRenderError("stage lighting surfaces overlap physical indices")
                samples[index] = cast(list[int], sample["color"])
        for resource, samples in grouped.items():
            indices[resource] = sorted(samples)
            buffers.setdefault(resource, bytearray()).extend(
                channel for index in indices[resource] for channel in samples[index]
            )
    mapping_digest = hashlib.sha256(
        json.dumps(surfaces, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return tuple(
        LightingMember(resource, tuple(indices[resource]), bytes(data), mapping_digest)
        for resource, data in sorted(buffers.items())
    )


def preview_stage_lighting_samples(
    *,
    project_id: str,
    draft: DraftProject,
    composition_id: str,
    timestamp_ms: int,
    assets: AssetUploadService,
    mapper_export: dict[str, object],
    device_reported_scales: dict[str, float],
) -> dict[str, object]:
    metadata = cast(dict[str, object], draft.editor_metadata)
    composition = _mapping(_mapping(metadata.get("compositions")).get(composition_id))
    if composition.get("space_kind") != "stage":
        raise ProjectorRenderError("stage lighting preview requires stage-space composition")
    duration_ms = _integer(composition.get("duration_ms"))
    if timestamp_ms < 0 or timestamp_ms >= duration_ms:
        raise ProjectorRenderError("stage lighting preview timestamp is outside duration")
    settings = _mapping(composition.get("render_settings"))
    numerator = _integer(settings.get("frame_rate_numerator"))
    period = 1000 * _integer(settings.get("frame_rate_denominator"))
    frame_timestamp_ms = (timestamp_ms * numerator // period) * period // numerator
    render_width = _integer(settings.get("render_width"))
    render_height = _integer(settings.get("render_height"))
    logical_width = _number(composition.get("width"))
    logical_height = _number(composition.get("height"))
    raw_layers = _mapping(metadata.get("timeline_transform_layers")).get(composition_id, [])
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ProjectorRenderError("stage composition has no media layers")
    prepared = prepare_media_layers(
        project_id=project_id,
        raw_layers=raw_layers,
        assets=assets,
    )
    stage_layers = tuple(
        replace(
            layer,
            keyframes=_scale_stage_keyframes(
                layer.keyframes,
                render_width / logical_width,
                render_height / logical_height,
            ),
        )
        for layer in prepared.visual_layers
    )
    frame = working_to_srgb8(
        compose_visual_frame(
            stage_layers,
            timestamp_ms=frame_timestamp_ms,
            output_width=render_width,
            output_height=render_height,
            background_rgb=_hex_rgb(_string(settings.get("output_background_color"))),
        )[0]
    )
    surfaces: list[dict[str, object]] = []
    preview_surfaces = (
        selected_stage_lighting_surfaces(composition, mapper_export)
        if "stage_lighting_surfaces" in composition
        else tuple(_mapping(raw) for raw in _list(mapper_export.get("lighting_surfaces")))
    )
    for raw_surface in preview_surfaces:
        surface = _mapping(raw_surface)
        resource = _string(surface.get("resource"))
        mapped_brightness = max(0.0, min(1.0, _number(surface.get("brightness_limit"))))
        device_scale = max(
            0.0,
            min(1.0, device_reported_scales.get(resource, 1.0)),
        )
        samples = _sample_stage_surface(frame, surface, logical_width, logical_height)
        for sample in samples:
            requested = np.asarray(sample["requested_color"], dtype=np.float32)
            sample["color"] = (
                np.clip(
                    np.rint(requested * mapped_brightness * device_scale),
                    0,
                    255,
                )
                .astype(np.uint8)
                .tolist()
            )
        surfaces.append(
            {
                "surface_semantic_id": _string(surface.get("semantic_id")),
                "surface_resource": resource,
                "mapped_brightness_limit": mapped_brightness,
                "device_reported_scale": device_reported_scales.get(resource),
                "samples": samples,
            }
        )
    return {
        "composition_id": composition_id,
        "timestamp_ms": timestamp_ms,
        "frame_timestamp_ms": frame_timestamp_ms,
        "source": "shared_stage_compositor",
        "surfaces": surfaces,
        "hardware_command_sent": False,
    }


def render_stage_composition(
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    composition_id: str,
    assets: AssetUploadService,
    mapper_export: dict[str, object],
    coverage: StageCoverage,
    overlaps: tuple[StoredProjectorOverlap, ...],
    sync_admission: SyncAdmission,
    output_directory: Path,
    ffmpeg: str = "ffmpeg",
) -> dict[str, object]:
    prepared = prepare_stage_composition(
        project_id=project_id,
        source_revision=source_revision,
        draft=draft,
        composition_id=composition_id,
        assets=assets,
        mapper_export=mapper_export,
        coverage=coverage,
        overlaps=overlaps,
        sync_admission=sync_admission,
    )
    return encode_output_set(
        output_directory=output_directory,
        members=prepared.members,
        audio_owner=prepared.audio_owner,
        audio_samples=prepared.mixed_audio,
        frame_rate_numerator=prepared.frame_rate_numerator,
        frame_rate_denominator=prepared.frame_rate_denominator,
        duration_ms=prepared.duration_ms,
        profile=prepared.profile,
        source_revision=source_revision,
        composition_id=composition_id,
        composition_digest=prepared.composition_digest,
        source_checksums=prepared.source_checksums,
        calibration_revisions=prepared.calibration_revisions,
        media_end_behavior=prepared.media_end_behavior,
        lighting_members=prepared.lighting_members,
        ffmpeg=ffmpeg,
    )


def preview_stage_composition_member(
    *,
    project_id: str,
    source_revision: int,
    draft: DraftProject,
    composition_id: str,
    assets: AssetUploadService,
    mapper_export: dict[str, object],
    coverage: StageCoverage,
    overlaps: tuple[StoredProjectorOverlap, ...],
    resource: str,
    timestamp_ms: int,
) -> np.ndarray:
    prepared = prepare_stage_composition(
        project_id=project_id,
        source_revision=source_revision,
        draft=draft,
        composition_id=composition_id,
        assets=assets,
        mapper_export=mapper_export,
        coverage=coverage,
        overlaps=overlaps,
        sync_admission=SyncAdmission(
            True,
            "preview_only",
            "Preview sends no hardware command.",
            None,
        ),
    )
    frames = prepared.members.get(resource)
    if frames is None:
        raise ProjectorRenderError("preview resource is not in the output set")
    if timestamp_ms < 0 or timestamp_ms >= prepared.duration_ms:
        raise ProjectorRenderError("preview timestamp is outside composition duration")
    frame_index = min(
        len(frames) - 1,
        timestamp_ms * prepared.frame_rate_numerator // (1000 * prepared.frame_rate_denominator),
    )
    return conform_preview_pixel_format(
        frames[frame_index],
        prepared.profile.pixel_format,
    )


def _scale_stage_keyframes(
    keyframes: tuple[dict[str, object], ...],
    scale_x: float,
    scale_y: float,
) -> tuple[dict[str, object], ...]:
    scaled: list[dict[str, object]] = []
    for keyframe in keyframes:
        value = dict(keyframe)
        position = _pair(value.get("position"))
        value["position"] = [position[0] * scale_x, position[1] * scale_y]
        interpolation = dict(_mapping(value.get("interpolation")))
        for field in ("spatial_in", "spatial_out"):
            handle = interpolation.get(field)
            if handle is not None:
                pair = _pair(handle)
                interpolation[field] = [pair[0] * scale_x, pair[1] * scale_y]
        value["interpolation"] = interpolation
        scaled.append(value)
    return tuple(scaled)


def _exclusive_polygons(
    coverage: StageCoverage,
    resource: str,
    other_resource: str,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    polygons = tuple(
        region.points
        for region in coverage.regions
        if region.kind == "covered"
        and resource in region.resources
        and other_resource not in region.resources
    )
    if not polygons:
        raise ProjectorRenderError(f"exclusive stage coverage is missing for {resource}")
    return polygons


def _mesh_vertices(
    projector: dict[str, object],
) -> tuple[tuple[float, float, float, float], ...]:
    values = projector.get("mesh_vertices")
    if isinstance(values, list) and values:
        return tuple(
            cast(tuple[float, float, float, float], tuple(map(float, item)))
            for item in values
            if isinstance(item, list) and len(item) == 4
        )
    extent = _quad(projector.get("stage_extent"))
    return planar_projector_mesh(extent)[0]


def _mesh_triangles(
    projector: dict[str, object],
) -> tuple[tuple[int, int, int], ...]:
    values = projector.get("mesh_triangles")
    if isinstance(values, list) and values:
        return tuple(
            cast(tuple[int, int, int], tuple(map(int, item)))
            for item in values
            if isinstance(item, list) and len(item) == 3
        )
    return ((0, 1, 2), (0, 2, 3))


def _white_matrix(
    white: tuple[float, float, float],
    matrix: tuple[float, ...],
) -> tuple[float, ...]:
    corrected = np.diag(np.asarray(white, dtype=np.float32)) @ np.asarray(
        matrix, dtype=np.float32
    ).reshape(3, 3)
    return tuple(float(value) for value in corrected.reshape(-1))


def _audio_owner(
    draft: DraftProject,
    composition_id: str,
    resources: set[str],
) -> str:
    scene_roots = _mapping(draft.editor_metadata.get("scene_compositions"))
    for scene in draft.scenes:
        roots = scene_roots.get(scene.id, [])
        if scene.schema_version == 2 and isinstance(roots, list) and composition_id in roots:
            output = next(item for item in scene.media if item.id == scene.clock_source)
            if output.resource not in resources:
                raise ProjectorRenderError(
                    "scene clock-source resource is not in the projector output set"
                )
            return output.resource
    raise ProjectorRenderError(
        "stage composition must be a root of a scene with a clock-source projector"
    )


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ProjectorRenderError("stage render value must be a nonempty string")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProjectorRenderError("stage render value must be an integer")
    return value


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ProjectorRenderError("stage render value must be numeric")
    return float(value)


def _pair(value: object) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ProjectorRenderError("stage render pair is invalid")
    return (_number(value[0]), _number(value[1]))


def _quad(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ProjectorRenderError("stage render extent is invalid")
    return (
        _number(value[0]),
        _number(value[1]),
        _number(value[2]),
        _number(value[3]),
    )


def _hex_rgb(value: str) -> tuple[int, int, int]:
    if len(value) != 7 or not value.startswith("#"):
        raise ProjectorRenderError("stage output background is invalid")
    return (
        int(value[1:3], 16),
        int(value[3:5], 16),
        int(value[5:7], 16),
    )
