from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from cueverge.gui.ambient_lighting import AmbientLighting
from cueverge.gui.apply import ApplyRequest
from cueverge.gui.contracts import (
    AddSceneOutputRequest,
    ConfigureInputSourceRequest,
    CreateSceneRequest,
    DeleteSceneRequest,
    DraftProject,
    InstallationGeometryRequest,
    InstallationObjectDeleteRequest,
    InstallationObjectDetails,
    InstallationObjectEditRequest,
    InteractionEditRequest,
    InteractionPreviewRequest,
    OperateActionRequest,
    OutputMonitorRequest,
    PhotoDescriptionRequest,
    RemoveSceneOutputRequest,
    RenameSceneOutputRequest,
    ReplaceInstallationPhotoRequest,
    ShowPlanEditRequest,
    StageReferenceRequest,
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
    stage_projector_resources: list[str] | None = Field(
        default=None,
        max_length=32,
        description=(
            "Projector resources taking part in a stage composition; omit to use every "
            "mapped projector, or use [] for a lights-only overlay that bakes LED "
            "sequences for stage_lighting_surfaces and no projector video."
        ),
    )
    source_pixel_units: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Stage compositions: composition units covered by one source media pixel at "
            "keyframe scale 1, so media size does not depend on render_width. Stage rasters "
            "keep the composition's aspect ratio (render height = render_width * height / "
            "width). Omit only on older compositions, where one source pixel is one render "
            "pixel."
        ),
    )
    led_background: Literal["ambient", "background"] | None = Field(
        default=None,
        description=(
            "Stage compositions with LEDs: where nothing is drawn, LEDs show the ambient/scene "
            "lighting underneath ('ambient', default for black backgrounds) or the background "
            "color ('background', default otherwise)."
        ),
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


class LayerMask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: list[tuple[float, float]] = Field(
        min_length=3,
        max_length=128,
        description="Polygon in the composition's logical coordinates (stage units for stage).",
    )
    feather: float = Field(default=0, ge=0, description="Inward soft edge in logical units.")
    invert: bool = False


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
    alpha_interpretation: (
        Literal["opaque", "straight", "premultiplied", "black_transparent"] | None
    ) = Field(
        description=(
            "Visual layers only; audio uses null. opaque ignores source alpha; straight and "
            "premultiplied read the source alpha channel. black_transparent derives coverage "
            "from the brightest channel so black drops out: it looks the same over black, and "
            "in LED overlay bakes it acts like a screen blend over the ambient/scene lighting "
            "underneath (use for effects rendered on black without an alpha channel)."
        )
    )
    source_color_space: Literal["srgb", "bt709", "linear_srgb"] | None
    scaling_behavior: Literal["fit", "fill", "stretch", "native"] | None
    audio: AudioMix | None = None
    keyframes: list[TransformKeyframe] = Field(
        description="Visual layers need keyframes; audio layers use [] and audio settings."
    )
    corner_pin: list[tuple[float, float]] | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        description=(
            "Optional convex TL, TR, BR, BL corners in logical coordinates. When set, the "
            "cropped source is perspective-mapped onto these corners and keyframe position, "
            "scale, rotation and anchor are ignored; opacity and crop still animate."
        ),
    )
    mask: LayerMask | None = Field(
        default=None,
        description="Optional static polygon mask limiting where a visual layer appears.",
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
    ambient_lighting: AmbientLighting | None = Field(
        default=None,
        description=(
            "Show-wide ambient lighting: per LED output, looping cues and/or a baked stage "
            "composition on the controller clock, under every scene's lighting. Scenes listed "
            "in excluded_scene_ids do not inherit it."
        ),
    )


def authoring_schema() -> dict[str, Any]:
    """Describe known spatial metadata while retaining other GUI-owned metadata."""
    schema = DraftProject.model_json_schema()
    schema["x-installation-objects"] = {
        "read_tool": "get_installation_objects",
        "edit_tool": "edit_installation_object",
        "read_scope": "read",
        "write_scope": "direct_edit",
        "edit_schema": InstallationObjectEditRequest.model_json_schema(),
        "delete_schema": InstallationObjectDeleteRequest.model_json_schema(),
        "actions": ["create", "update", "delete"],
        "editable_kinds": ["fixture", "path"],
        "segment_partition_editing": False,
        "requires_current_base_revision": True,
        "definition_owner": "show_working_draft",
        "geometry_owner": "installation",
        "metadata_key": "installation_object_details",
        "details_schema": InstallationObjectDetails.model_json_schema(),
        "geometry_links": "semantic IDs; optional; never create or delete geometry",
        "placement": "explicit links plus mapped member segments on the same output",
        "blocks_referenced_deletion": True,
        "ui": "Installation > Objects; choose the show explicitly",
        "changes_live_playback": False,
    }
    schema["x-scene-authoring"] = {
        "options_tool": "get_scene_authoring_options",
        "create_tool": "create_scene",
        "create_schema": CreateSceneRequest.model_json_schema(),
        "add_output_tool": "add_scene_output",
        "add_output_schema": AddSceneOutputRequest.model_json_schema(),
        "rename_output_tool": "rename_scene_output",
        "rename_output_schema": RenameSceneOutputRequest.model_json_schema(),
        "rename_updates": [
            "clock_source",
            "timeline_transform_layers track_id",
            "timeline_cues track_id",
            "timeline_sync_schedule track_id",
        ],
        "remove_output_tool": "remove_scene_output",
        "remove_output_schema": RemoveSceneOutputRequest.model_json_schema(),
        "remove_blocked_by": [
            "last output in the scene",
            "media clock source of a media_synchronized scene",
            "media layers or synchronized cues on the output's track",
        ],
        "remove_drops_unused_resource_claim": True,
        "ui": "Timeline editor: select an output track, Inspector > Rename / Remove output",
        "read_scope": "read",
        "write_scope": "direct_edit",
        "requires_current_base_revision": True,
        "media_only_scenes": True,
        "requires_initial_output": True,
        "new_lighting": "full-duration blackout cue",
        "new_media": "unassigned; place an asset before publication",
        "clock_preserved_unless_explicitly_changed": True,
        "v1_upgrade": {"schema_version": 2, "media_id": "media", "lighting_id": "lighting"},
        "resource_import": (
            "Only selected bindings from the bound runtime installation; "
            "never overwrite saved bindings."
        ),
        "creates_surface_composition": False,
        "changes_live_playback": False,
    }
    schema["x-scene-deletion"] = {
        "tool": "delete_scene",
        "schema": DeleteSceneRequest.model_json_schema(),
        "scope": "direct_edit",
        "blocks_last_scene": True,
        "blocks_incoming_references": True,
        "removes_scene_owned_metadata": True,
        "retains_reusable_compositions_presets_and_assets": True,
        "publishes_or_changes_live_output": False,
    }
    schema["x-composition-deletion"] = {
        "usage_tool": "get_composition_usage",
        "delete_tool": "delete_composition",
        "remove_from_scene_tool": "remove_composition_from_scene",
        "scope": "direct_edit",
        "requires_base_revision": True,
        "removes_scene_roots_instances_parent_children_and_nested_layers": True,
        "removes_composition_owned_timeline_metadata": True,
        "retains_media_assets_and_revision_history": True,
        "publishes_or_changes_live_output": False,
    }
    schema["x-runtime-workflows"] = {
        "apply": {
            "tool": "apply_saved_show",
            "schema": ApplyRequest.model_json_schema(),
            "scopes": ["publish", "activate"],
            "restore_scope": "activate",
            "status_tool": "get_apply_jobs",
            "cancel_tool": "cancel_apply",
            "saved_is_not_loaded": True,
            "bakes_missing_or_stale_first": True,
            "bake_requirements_tool": "get_bake_requirements",
            "states": [
                "preparing",
                "baking",
                "publishing",
                "staging",
                "handover",
                "completed",
                "failed",
                "cancelled",
            ],
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
        "geometry_owner": "installation",
        "photo_optional": True,
        "geometry_schema": InstallationGeometryRequest.model_json_schema(),
        "photo_removal": "detach_reference_preserve_geometry_wiring_and_calibration",
        "replacement_tool": "replace_installation_photo",
        "replacement_scope": "photo_replace",
        "replacement_schema": ReplaceInstallationPhotoRequest.model_json_schema(),
        "read_tool": "get_installation_setup",
        "description_tool": "describe_installation_photo",
        "description_scope": "installation_edit",
        "description_schema": PhotoDescriptionRequest.model_json_schema(),
        "photo_authority": "name is the persisted description; viewpoint identity is preserved",
        "device_writes": "administrator GUI only; no added MCP device-control grant",
        "geometry_editing": False,
        "stage_reference": {
            "read_tool": "get_stage_map",
            "update_tool": "update_stage_reference",
            "update_scope": "installation_edit",
            "request_schema": StageReferenceRequest.model_json_schema(),
            "stage_coordinates": "layout x * 1000 * units_per_canvas, y * 600 * units_per_canvas",
        },
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
    schema["x-show-plan"] = {
        "read_tool": "get_show_plan",
        "edit_tool": "update_show_plan",
        "edit_scope": "direct_edit",
        "edit_schema": ShowPlanEditRequest.model_json_schema(),
        "schema_usage": "Extract each nested schema as its own JSON Schema document.",
        "endings": {
            "repeat": "loop",
            "play_next": "transition_to",
            "shuffle": "select_next",
            "freeze": "hold",
            "stop": "stop",
        },
        "shuffle_list": "program default pool; existing cooldowns/conditions are preserved",
        "starting_scene_without_program": "creates a one-scene default pool named main",
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
