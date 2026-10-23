from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cueverge.gui.apply import ApplyRequest
from cueverge.gui.contracts import (
    ConfigureInputSourceRequest,
    DraftProject,
    InteractionEditRequest,
    InteractionPreviewRequest,
    OperateActionRequest,
    OutputMonitorRequest,
    PhotoDescriptionRequest,
)
from cueverge.gui.spatial_effects import SpatialRipple


class MetadataSchema(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)


class LightingSettings(MetadataSchema):
    canvas_width: int = Field(gt=0, le=2048)
    canvas_height: int = Field(gt=0, le=2048)
    sampling: Literal["nearest", "bilinear", "area"]
    gamma: float = Field(gt=0, le=5)
    brightness: float = Field(ge=0, le=1)


class OutputProfile(MetadataSchema):
    container: Literal["mp4"]
    video_codec: Literal["h264"]
    pixel_format: Literal["yuv420p"]
    max_video_bitrate_kbps: int = Field(ge=500, le=50000)
    audio_codec: Literal["aac"]
    audio_sample_rate: Literal[48000]
    audio_channel_layout: Literal["stereo"]
    loudness_target_lufs: float = Field(ge=-40, le=-5)
    keyframe_interval_frames: int = Field(gt=0, le=600)
    b_frames: Literal[0]


class LegacyOutputProfile(OutputProfile):
    preset_id: Literal["legacy_h264"]
    video_profile: Literal["baseline"]
    video_level: Literal["3.1"]
    hardware_decoder_compatibility: Literal["h264_baseline_level_3_1_yuv420p"]


class LowPowerOutputProfile(OutputProfile):
    preset_id: Literal["low_power_h264"]
    video_profile: Literal["main"]
    video_level: Literal["4.0"]
    hardware_decoder_compatibility: Literal["h264_main_level_4_yuv420p"]


class RenderSettings(MetadataSchema):
    render_pipeline_revision: Literal[1]
    render_width: int = Field(gt=0, le=16384)
    render_height: int = Field(gt=0, le=16384)
    frame_rate_numerator: int = Field(gt=0, le=240000)
    frame_rate_denominator: int = Field(gt=0, le=1001)
    working_color_space: Literal["linear_srgb"]
    working_alpha_mode: Literal["premultiplied"]
    scaling_filter: Literal["lanczos", "bicubic"]
    output_background_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    media_end_behavior: Literal["stop", "hold_last_frame", "loop"]
    output_profile: LegacyOutputProfile | LowPowerOutputProfile = Field(discriminator="preset_id")


class Composition(MetadataSchema):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    duration_ms: int = Field(gt=0)
    space_kind: Literal["projector_native", "lighting_surface", "stage"]
    target_id: str = Field(min_length=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    local_time_offset_ms: int = Field(default=0, ge=0)
    children: list[str] = Field(default_factory=list)
    stage_lighting_surfaces: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Unique mapped LED semantic IDs sampled from a shared-stage composition.",
    )
    render_settings: RenderSettings | None = None
    lighting_settings: LightingSettings | None = None


class Interpolation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["hold", "linear", "ease_in", "ease_out", "bezier"]
    temporal_bezier: tuple[float, float, float, float] | None = None
    spatial_in: tuple[float, float] | None = None
    spatial_out: tuple[float, float] | None = None


class TransformKeyframe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    time_ms: int = Field(ge=0)
    position: tuple[float, float]
    scale: tuple[float, float]
    rotation_deg: float
    anchor: tuple[float, float]
    opacity: float = Field(ge=0, le=1)
    crop: tuple[float, float, float, float]
    interpolation: Interpolation


class AudioMix(MetadataSchema):
    gain_db: float = Field(ge=-96, le=24)
    mute: bool
    solo: bool
    mix_order: int = Field(ge=0)


class MediaLayer(MetadataSchema):
    layer_schema_version: Literal[1]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["image", "video", "audio", "composition"] = Field(
        description="Use video for animated GIFs as well as video clips; image is static."
    )
    source_id: str = Field(min_length=1, description="Ready project asset ID or composition ID.")
    track_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(gt=0)
    in_point_ms: int = Field(ge=0)
    out_point_ms: int = Field(gt=0)
    z_index: int
    fade_in_ms: int = Field(ge=0)
    fade_out_ms: int = Field(ge=0)
    playback: Literal["hold", "loop", "once"] = "hold"
    alpha_interpretation: Literal["opaque", "straight", "premultiplied"] | None
    source_color_space: Literal["srgb", "bt709", "linear_srgb"] | None
    scaling_behavior: Literal["fit", "fill", "stretch", "native"] | None
    audio: AudioMix | None = None
    keyframes: list[TransformKeyframe] = Field(
        description="Visual layers need keyframes; audio layers use [] and audio settings."
    )


class TimelineTrack(MetadataSchema):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["media_output", "audio_reference", "lighting_output", "fixture", "group", "path"]
    resource_binding: str


class SpatialMetadata(MetadataSchema):
    installation_id: str | None = None
    compositions: dict[str, Composition] = Field(default_factory=dict)
    scene_compositions: dict[str, list[str]] = Field(default_factory=dict)
    timeline_tracks: dict[str, list[TimelineTrack]] = Field(default_factory=dict)
    timeline_transform_layers: dict[str, list[MediaLayer]] = Field(default_factory=dict)


def authoring_schema() -> dict[str, Any]:
    """Describe known spatial metadata while retaining other GUI-owned metadata."""
    schema = DraftProject.model_json_schema()
    schema["x-runtime-workflows"] = {
        "apply": {
            "tool": "apply_saved_show",
            "schema": ApplyRequest.model_json_schema(),
            "scopes": ["publish", "activate"],
            "restore_scope": "activate",
            "status_tool": "get_apply_jobs",
            "cancel_tool": "cancel_apply",
            "saved_is_not_loaded": True,
        },
        "monitor": {
            "tool": "get_output_monitor",
            "schema": OutputMonitorRequest.model_json_schema(),
            "scope": "read",
            "maximum_capture_hz": 5,
            "maximum_samples": 4096,
            "requires_loaded_project_match": True,
            "physical_output_verified": False,
        },
        "transport": {
            "tool": "operate_loaded_show",
            "schema": OperateActionRequest.model_json_schema(),
            "scope": "live_operation",
            "physical_confirmation_required": True,
            "requires_loaded_project_match": True,
        },
        "schema_usage": "Extract each nested schema as its own JSON Schema document.",
    }
    schema["x-installation-setup"] = {
        "read_tool": "get_installation_setup",
        "description_tool": "describe_installation_photo",
        "description_scope": "installation_edit",
        "description_schema": PhotoDescriptionRequest.model_json_schema(),
        "photo_authority": "name is the persisted description; viewpoint identity is preserved",
        "device_writes": "administrator GUI only; no added MCP device-control grant",
        "geometry_editing": False,
    }
    schema["x-guided-interactions"] = {
        "read_tool": "get_interactions",
        "edit_tool": "update_interaction",
        "preview_tool": "preview_interaction_workflow",
        "edit_scope": "direct_edit",
        "preview_scope": "simulation",
        "edit_schema": InteractionEditRequest.model_json_schema(),
        "preview_schema": InteractionPreviewRequest.model_json_schema(),
        "schema_usage": "Extract each nested schema as its own JSON Schema document.",
        "names_metadata": (
            "editor_metadata.interaction_names maps response IDs to human-readable names."
        ),
        "preserves_source_models": True,
    }
    schema["x-installation-input-configuration"] = {
        "schema": ConfigureInputSourceRequest.model_json_schema(),
        "stored_outside_show": True,
        "tool": "configure_input_source",
        "required_scope": "input_config",
        "required_role": "global_administrator",
        "password": "write_only_controller_local",
    }
    metadata = SpatialMetadata.model_json_schema()
    schema.setdefault("$defs", {}).update(metadata.pop("$defs", {}))
    schema["properties"]["editor_metadata"] = {
        **metadata,
        "description": (
            "Spatial authoring metadata. Map keys must match IDs; tracks must bind scene "
            "resources; source intervals must match layer duration and fit compositions. "
            "Lighting surfaces require lighting_settings; stage/projector media require "
            "render_settings. Preset fields must match preset_id. Draft save/proposal "
            "validation enforces these cross-field rules and preserves unrelated metadata."
        ),
    }
    schema["$defs"]["SpatialRipple"] = SpatialRipple.model_json_schema()
    return schema
