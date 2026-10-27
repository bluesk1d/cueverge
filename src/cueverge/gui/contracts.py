import math
import re
from datetime import date
from enum import StrEnum
from typing import Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)

from cueverge.core.models import (
    AudiencePresenceDefinition,
    EncounterDefinition,
    InstallationDefinition,
    LightingCue,
    OpportunityProgramDefinition,
    ResourceBinding,
    SceneDefinitionV1,
    SceneDefinitionV2,
    SegmentReference,
    ShowProgramDefinition,
)
from cueverge.gui.ambient_lighting import validate_ambient_lighting
from cueverge.gui.spatial_effects import SpatialRipple
from cueverge.protocol.device_v2 import (
    AGENT_SETTING_NAMES,
    AgentAudioDevice,
    AgentDisplay,
    AgentEffectiveSettings,
    AgentPlatform,
    AgentSettingName,
    AgentSettings,
    DeviceCapabilityDocument,
    DeviceKind,
    KeepAwakeStatus,
    PlayerState,
)


class GuiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(StrEnum):
    VIEWER = "viewer"
    DESIGNER = "designer"
    OPERATOR = "operator"
    PUBLISHER = "publisher"
    ADMINISTRATOR = "administrator"


class InputSourceSettings(GuiModel):
    enabled: bool = False
    host: str = Field(default="", max_length=253)
    port: int = Field(default=1883, ge=1, le=65535)
    tls: bool = False
    topic: str = Field(default="frigate/events", min_length=1, max_length=256)
    username: str | None = Field(default=None, max_length=128)
    accepted_labels: tuple[str, ...] = Field(default=("person",), min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_source(self) -> "InputSourceSettings":
        if self.enabled and not self.host:
            raise ValueError("A broker hostname is required to enable camera events")
        if any(character in self.host for character in "/@?#\\ \t\r\n"):
            raise ValueError("Use a broker hostname or IP address, not a URL or credentials")
        if "\x00" in self.topic or any(
            not label.strip() or len(label) > 128 for label in self.accepted_labels
        ):
            raise ValueError("Use a valid MQTT topic and nonempty labels of at most 128 characters")
        return self


class ConfigureInputSourceRequest(InputSourceSettings):
    expected_revision: int | None = Field(default=None, ge=0)
    password: SecretStr | None = Field(
        default=None,
        max_length=4096,
        description="Write-only; omitted retains the stored credential",
    )
    clear_password: bool = False

    @model_validator(mode="after")
    def validate_password_action(self) -> "ConfigureInputSourceRequest":
        if self.clear_password and self.password is not None:
            raise ValueError("Choose either replacement or removal of the password")
        if self.password is not None and not self.password.get_secret_value():
            raise ValueError("Use clear_password to remove a stored password")
        return self


def _normalize_folder(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().strip("/")
    parts = normalized.split("/") if normalized else []
    if any(part in {"", ".", ".."} for part in parts) or "\\" in normalized:
        raise ValueError("folder must contain plain slash-separated names")
    return normalized or None


def _normalize_tags(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(tag.strip() for tag in value)
    if any(not tag or len(tag) > 50 for tag in normalized):
        raise ValueError("tags must contain between 1 and 50 characters")
    if len({tag.casefold() for tag in normalized}) != len(normalized):
        raise ValueError("tags must be unique ignoring case")
    return normalized


class DraftProject(GuiModel):
    schema_version: Literal[1]
    installation: InstallationDefinition
    scenes: tuple[SceneDefinitionV1 | SceneDefinitionV2, ...] = Field(min_length=1)
    program: ShowProgramDefinition | None = None
    audience: AudiencePresenceDefinition | None = None
    opportunities: OpportunityProgramDefinition | None = None
    encounter: EncounterDefinition | None = None
    editor_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_scenes(self) -> "DraftProject":
        scene_ids = [scene.id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("draft scene IDs must be unique")
        _validate_composition_graph(
            self.editor_metadata,
            {scene.id: scene.duration_ms for scene in self.scenes},
        )
        _validate_autonomous_show(self)
        validate_ambient_lighting(self.editor_metadata, self.installation, set(scene_ids))
        if "installation_object_details" in self.editor_metadata:
            details = self.editor_metadata["installation_object_details"]
            if not isinstance(details, list):
                raise ValueError("Installation object details must be a list.")
            identities: set[tuple[str, str, str]] = set()
            for raw in details:
                detail = InstallationObjectDetails.model_validate(raw)
                identity = (detail.resource, detail.kind, detail.id)
                if identity in identities:
                    raise ValueError("Installation object details must have unique identities.")
                identities.add(identity)
        return self


def _validate_autonomous_show(draft: DraftProject) -> None:
    scene_ids = {scene.id for scene in draft.scenes}
    if draft.program is not None:
        referenced = {draft.program.initial_scene_id}
        referenced.update(entry.scene_id for pool in draft.program.pools for entry in pool.scenes)
        missing = referenced.difference(scene_ids)
        if missing:
            raise ValueError(f"show program references unknown scenes: {sorted(missing)}")
    if draft.opportunities is not None:
        missing = {
            opportunity.scene_id
            for opportunity in draft.opportunities.opportunities
            if opportunity.scene_id not in scene_ids
        }
        if missing:
            raise ValueError(
                f"engagement opportunities reference unknown scenes: {sorted(missing)}"
            )
    if draft.encounter is not None:
        referenced = {draft.encounter.ambient_scene_id}
        referenced.update(
            transition.scene_id
            for transition in draft.encounter.transitions
            if transition.scene_id is not None
        )
        missing = referenced.difference(scene_ids)
        if missing:
            raise ValueError(f"encounter references unknown scenes: {sorted(missing)}")


def _validate_composition_graph(
    editor_metadata: dict[str, JsonValue],
    scene_durations: dict[str, int],
) -> None:
    scene_ids = set(scene_durations)
    raw_compositions = editor_metadata.get("compositions", {})
    raw_instances = editor_metadata.get("scene_compositions", {})
    raw_instance_records = editor_metadata.get("composition_instances", {})
    if (
        not isinstance(raw_compositions, dict)
        or not isinstance(raw_instances, dict)
        or not isinstance(raw_instance_records, dict)
    ):
        raise ValueError("composition metadata must be objects")
    compositions: dict[str, dict[str, JsonValue]] = {}
    for composition_id, value in raw_compositions.items():
        if not isinstance(value, dict) or value.get("id") != composition_id:
            raise ValueError("composition keys must match stable composition IDs")
        if not isinstance(value.get("name"), str) or not value["name"]:
            raise ValueError("composition name is required")
        duration = value.get("duration_ms")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            raise ValueError("composition duration must be a positive integer")
        if value.get("space_kind") not in {
            "projector_native",
            "lighting_surface",
            "stage",
        }:
            raise ValueError("composition must declare a supported logical editing space")
        if value.get("space_kind") == "lighting_surface":
            _validate_lighting_composition_settings(value.get("lighting_settings"))
        stage_surfaces = value.get("stage_lighting_surfaces", [])
        if (
            not isinstance(stage_surfaces, list)
            or not all(isinstance(item, str) and item for item in stage_surfaces)
            or len(stage_surfaces) != len(set(stage_surfaces))
            or len(stage_surfaces) > 100
        ):
            raise ValueError("stage lighting surfaces must be up to 100 unique semantic IDs")
        if stage_surfaces and value.get("space_kind") != "stage":
            raise ValueError("only stage compositions can select stage lighting surfaces")
        led_background = value.get("led_background")
        if led_background is not None:
            if value.get("space_kind") != "stage":
                raise ValueError("only stage compositions set led_background")
            if led_background not in {"ambient", "background"}:
                raise ValueError("led_background must be ambient or background")
        source_pixel_units = value.get("source_pixel_units")
        if source_pixel_units is not None:
            if value.get("space_kind") != "stage":
                raise ValueError("only stage compositions set source_pixel_units")
            if (
                not isinstance(source_pixel_units, (int, float))
                or isinstance(source_pixel_units, bool)
                or not math.isfinite(source_pixel_units)
                or source_pixel_units <= 0
                or source_pixel_units > 1_000_000
            ):
                raise ValueError("source_pixel_units must be a positive number")
        stage_projectors = value.get("stage_projector_resources")
        if stage_projectors is not None:
            if (
                not isinstance(stage_projectors, list)
                or not all(isinstance(item, str) and item for item in stage_projectors)
                or len(stage_projectors) != len(set(stage_projectors))
                or len(stage_projectors) > 32
            ):
                raise ValueError("stage projectors must be up to 32 unique resource names")
            if value.get("space_kind") != "stage":
                raise ValueError("only stage compositions can select stage projectors")
            if not stage_projectors and not stage_surfaces:
                raise ValueError(
                    "a lights-only stage composition (no projectors) must select "
                    "at least one stage lighting surface"
                )
        if not isinstance(value.get("target_id"), str) or not value["target_id"]:
            raise ValueError("composition editing space requires a target ID")
        for dimension in ("width", "height"):
            size = value.get(dimension)
            if not isinstance(size, (int, float)) or isinstance(size, bool) or size <= 0:
                raise ValueError("composition editing-space dimensions must be positive")
        offset = value.get("local_time_offset_ms", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("composition local-time offset must be a nonnegative integer")
        children = value.get("children", [])
        if not isinstance(children, list) or not all(isinstance(child, str) for child in children):
            raise ValueError("composition children must be stable ID strings")
        if len(children) != len(set(children)):
            raise ValueError("composition children must be unique")
        compositions[composition_id] = value
    for composition_id, value in compositions.items():
        children = value.get("children", [])
        assert isinstance(children, list)
        unknown = {child for child in children if isinstance(child, str)}.difference(compositions)
        if unknown:
            raise ValueError(
                f"composition {composition_id} references unknown children: {sorted(unknown)}"
            )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(composition_id: str) -> None:
        if composition_id in visiting:
            raise ValueError("composition graph cannot contain cycles")
        if composition_id in visited:
            return
        visiting.add(composition_id)
        children = compositions[composition_id].get("children", [])
        assert isinstance(children, list)
        for child in children:
            assert isinstance(child, str)
            visit(child)
        visiting.remove(composition_id)
        visited.add(composition_id)

    for composition_id in compositions:
        visit(composition_id)
    unknown_scenes = set(raw_instances).difference(scene_ids)
    if unknown_scenes:
        raise ValueError(
            f"composition instances reference unknown scenes: {sorted(unknown_scenes)}"
        )
    for scene_id, value in raw_instances.items():
        if not isinstance(value, list) or not all(
            isinstance(composition_id, str) for composition_id in value
        ):
            raise ValueError(f"scene {scene_id} composition instances must be stable IDs")
        if len(value) != len(set(value)):
            raise ValueError(f"scene {scene_id} composition instances must be unique")
        unknown = {
            composition_id for composition_id in value if isinstance(composition_id, str)
        }.difference(compositions)
        if unknown:
            raise ValueError(f"scene {scene_id} references unknown compositions: {sorted(unknown)}")
    seen_instance_ids: set[str] = set()
    unknown_instance_scenes = set(raw_instance_records).difference(scene_ids)
    if unknown_instance_scenes:
        raise ValueError(
            f"composition instances reference unknown scenes: {sorted(unknown_instance_scenes)}"
        )
    for scene_id, values in raw_instance_records.items():
        if not isinstance(values, list):
            raise ValueError(f"scene {scene_id} composition instances must be a list")
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("composition instance must be an object")
            instance_id = value.get("id")
            instance_composition_id = value.get("composition_id")
            if not isinstance(instance_id, str) or not instance_id:
                raise ValueError("composition instance requires a stable ID")
            if instance_id in seen_instance_ids:
                raise ValueError("composition instance IDs must be unique")
            seen_instance_ids.add(instance_id)
            if (
                not isinstance(instance_composition_id, str)
                or instance_composition_id not in compositions
            ):
                raise ValueError("composition instance references an unknown composition")
            start_offset = value.get("start_offset_ms")
            if (
                not isinstance(start_offset, int)
                or isinstance(start_offset, bool)
                or start_offset < 0
            ):
                raise ValueError("composition instance start offset must be nonnegative")
            time_scale = value.get("time_scale")
            if (
                not isinstance(time_scale, (int, float))
                or isinstance(time_scale, bool)
                or time_scale <= 0
                or time_scale > 16
            ):
                raise ValueError("composition instance time scale must be in (0, 16]")
            if not isinstance(value.get("parameter_overrides", {}), dict):
                raise ValueError("composition parameter overrides must be an object")
            placement = value.get("placement")
            if not isinstance(placement, dict) or not all(
                isinstance(placement.get(axis), (int, float))
                and not isinstance(placement.get(axis), bool)
                for axis in ("x", "y")
            ):
                raise ValueError("composition instance placement requires numeric x and y")
    _validate_timeline_sync(editor_metadata, scene_durations, compositions)
    _validate_transform_layers(editor_metadata, scene_durations, compositions)
    _validate_lighting_author_layers(editor_metadata, scene_durations, compositions)


def _validate_lighting_composition_settings(value: JsonValue | None) -> None:
    if not isinstance(value, dict):
        raise ValueError("lighting surface composition requires lighting settings")
    for dimension in ("canvas_width", "canvas_height"):
        size = value.get(dimension)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > 2048:
            raise ValueError("lighting canvas dimensions must be in [1, 2048]")
    if value.get("sampling") not in {"nearest", "bilinear", "area"}:
        raise ValueError("lighting sampling mode is unsupported")
    gamma = value.get("gamma")
    brightness = value.get("brightness")
    if (
        not isinstance(gamma, (int, float))
        or isinstance(gamma, bool)
        or gamma <= 0
        or gamma > 5
        or not isinstance(brightness, (int, float))
        or isinstance(brightness, bool)
        or brightness < 0
        or brightness > 1
    ):
        raise ValueError("lighting evaluation settings are invalid")


def _validate_timeline_sync(
    editor_metadata: dict[str, JsonValue],
    scene_durations: dict[str, int],
    compositions: dict[str, dict[str, JsonValue]],
) -> None:
    raw_markers = editor_metadata.get("timeline_markers", {})
    raw_cues = editor_metadata.get("timeline_cues", {})
    raw_presets = editor_metadata.get("timeline_cue_presets", [])
    if not isinstance(raw_markers, dict) or not isinstance(raw_cues, dict):
        raise ValueError("timeline marker and cue metadata must be objects")
    if not isinstance(raw_presets, list):
        raise ValueError("timeline cue presets must be a list")
    preset_ids: set[str] = set()
    for value in raw_presets:
        if not isinstance(value, dict):
            raise ValueError("timeline cue preset must be an object")
        preset_id = value.get("id")
        if not isinstance(preset_id, str) or not preset_id:
            raise ValueError("timeline cue preset requires a stable ID")
        if preset_id in preset_ids:
            raise ValueError("timeline cue preset IDs must be unique")
        preset_ids.add(preset_id)
        if not isinstance(value.get("name"), str) or not value["name"]:
            raise ValueError("timeline cue preset name is required")
        lighting = value.get("lighting")
        if not isinstance(lighting, dict):
            raise ValueError("timeline cue preset requires lighting properties")
        LightingCue.model_validate({"id": preset_id, "start_ms": 0, **lighting})
    durations = dict(scene_durations)
    for composition_id, composition in compositions.items():
        duration = composition["duration_ms"]
        assert isinstance(duration, int) and not isinstance(duration, bool)
        durations[composition_id] = duration
    unknown_spaces = (set(raw_markers) | set(raw_cues)).difference(durations)
    if unknown_spaces:
        raise ValueError(
            f"timeline synchronization references unknown editing spaces: {sorted(unknown_spaces)}"
        )
    marker_spaces: dict[str, str] = {}
    marker_times: dict[str, int] = {}
    for space_id, values in raw_markers.items():
        if not isinstance(values, list):
            raise ValueError(f"timeline markers for {space_id} must be a list")
        names: set[str] = set()
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("timeline marker must be an object")
            marker_id = value.get("id")
            name = value.get("name")
            time_ms = value.get("time_ms")
            if not isinstance(marker_id, str) or not marker_id:
                raise ValueError("timeline marker requires a stable ID")
            if marker_id in marker_spaces:
                raise ValueError("timeline marker IDs must be unique")
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                raise ValueError("timeline marker name must contain 1 to 200 characters")
            normalized_name = name.strip().casefold()
            if normalized_name in names:
                raise ValueError("timeline marker names must be unique within an editing space")
            names.add(normalized_name)
            if (
                not isinstance(time_ms, int)
                or isinstance(time_ms, bool)
                or time_ms < 0
                or time_ms > durations[space_id]
            ):
                raise ValueError("timeline marker time must be within its editing space")
            marker_spaces[marker_id] = space_id
            marker_times[marker_id] = time_ms
    cue_ids: set[str] = set()
    supported_kinds = {
        "visual",
        "audio",
        "lighting",
        "switch",
        "physical_effect",
    }
    for space_id, values in raw_cues.items():
        if not isinstance(values, list):
            raise ValueError(f"timeline cues for {space_id} must be a list")
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("timeline cue must be an object")
            unsupported_fields = set(value).difference(
                {
                    "id",
                    "name",
                    "kind",
                    "track_id",
                    "time_ms",
                    "marker_id",
                    "marker_offset_ms",
                    "lighting",
                }
            )
            if unsupported_fields:
                raise ValueError(f"unsupported timeline cue fields: {sorted(unsupported_fields)}")
            cue_id = value.get("id")
            if not isinstance(cue_id, str) or not cue_id:
                raise ValueError("timeline cue requires a stable ID")
            if cue_id in cue_ids:
                raise ValueError("timeline cue IDs must be unique")
            cue_ids.add(cue_id)
            if value.get("kind") not in supported_kinds:
                raise ValueError("timeline cue kind is unsupported")
            if not isinstance(value.get("name"), str) or not value["name"]:
                raise ValueError("timeline cue name is required")
            if not isinstance(value.get("track_id"), str) or not value["track_id"]:
                raise ValueError("timeline cue requires a track ID")
            marker_id = value.get("marker_id")
            time_ms = value.get("time_ms")
            offset_ms = value.get("marker_offset_ms", 0)
            if not isinstance(offset_ms, int) or isinstance(offset_ms, bool):
                raise ValueError("timeline cue marker offset must be an integer")
            if marker_id is None:
                if (
                    not isinstance(time_ms, int)
                    or isinstance(time_ms, bool)
                    or time_ms < 0
                    or time_ms > durations[space_id]
                ):
                    raise ValueError("detached timeline cue time must be within its editing space")
                if offset_ms != 0:
                    raise ValueError("detached timeline cue cannot retain a marker offset")
                effective_time = time_ms
            else:
                if not isinstance(marker_id, str) or marker_spaces.get(marker_id) != space_id:
                    raise ValueError("timeline cue marker must exist in the same editing space")
                if time_ms is not None:
                    raise ValueError("marker-linked timeline cue cannot store an absolute time")
                effective_time = marker_times[marker_id] + offset_ms
                if effective_time < 0 or effective_time > durations[space_id]:
                    raise ValueError(
                        "timeline cue marker offset resolves outside its editing space"
                    )
            lighting = value.get("lighting")
            if value.get("kind") == "lighting":
                if not isinstance(lighting, dict):
                    raise ValueError("lighting timeline cue requires runtime properties")
                runtime_cue = LightingCue.model_validate(
                    {
                        "id": cue_id,
                        "start_ms": effective_time,
                        **lighting,
                    }
                )
                if runtime_cue.start_ms + runtime_cue.duration_ms > durations[space_id]:
                    raise ValueError("lighting timeline cue extends beyond its editing space")
            elif lighting is not None:
                raise ValueError("non-lighting timeline cue cannot define lighting properties")


def _validate_lighting_author_layers(
    editor_metadata: dict[str, JsonValue],
    scene_durations: dict[str, int],
    compositions: dict[str, dict[str, JsonValue]],
) -> None:
    raw_layers = editor_metadata.get("lighting_author_layers", {})
    if not isinstance(raw_layers, dict):
        raise ValueError("lighting author layer metadata must be an object")
    durations = dict(scene_durations)
    for composition_id, composition in compositions.items():
        duration = composition.get("duration_ms")
        if isinstance(duration, int) and not isinstance(duration, bool):
            durations[composition_id] = duration
    unknown_spaces = set(raw_layers).difference(durations)
    if unknown_spaces:
        raise ValueError(
            f"lighting layers reference unknown editing spaces: {sorted(unknown_spaces)}"
        )
    layer_ids: set[str] = set()
    for space_id, values in raw_layers.items():
        if not isinstance(values, list):
            raise ValueError(f"lighting layers for {space_id} must be a list")
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("lighting author layer must be an object")
            layer_id = value.get("id")
            if not isinstance(layer_id, str) or not layer_id:
                raise ValueError("lighting author layer requires a stable ID")
            if layer_id in layer_ids:
                raise ValueError("lighting author layer IDs must be unique")
            layer_ids.add(layer_id)
            if not isinstance(value.get("name"), str) or not value["name"]:
                raise ValueError("lighting author layer name is required")
            mode = value.get("mode")
            if not isinstance(mode, str) or mode not in {
                "spatial",
                "path",
                "fixture",
            }:
                raise ValueError("lighting author mode is unsupported")
            start = value.get("start_ms")
            duration = value.get("duration_ms")
            persistent = value.get("persistent", False)
            if (
                not isinstance(persistent, bool)
                or not isinstance(start, int)
                or isinstance(start, bool)
                or start < 0
                or not isinstance(duration, int)
                or isinstance(duration, bool)
                or duration <= 0
                or start + duration > durations[space_id]
            ):
                raise ValueError("lighting author layer interval is invalid")
            precedence = value.get("precedence")
            opacity = value.get("opacity")
            if (
                not isinstance(precedence, int)
                or isinstance(precedence, bool)
                or precedence < 0
                or precedence > 1000
                or value.get("blend_mode") not in {"replace", "add", "max", "multiply"}
                or not isinstance(opacity, (int, float))
                or isinstance(opacity, bool)
                or opacity < 0
                or opacity > 1
            ):
                raise ValueError("lighting layer blend settings are invalid")
            payload = value.get(mode)
            if not isinstance(payload, dict):
                raise ValueError(f"lighting {mode} layer payload is required")
            if mode == "spatial":
                if payload.get("primitive") not in {
                    "solid",
                    "linear_gradient",
                    "radial_gradient",
                    "shape",
                    "procedural_noise",
                    "ripple",
                }:
                    raise ValueError("spatial lighting primitive is unsupported")
                if payload.get("primitive") == "ripple":
                    SpatialRipple.model_validate(payload)
                elif not isinstance(payload.get("seed"), int):
                    raise ValueError("spatial lighting seed must be an integer")
            elif mode == "path":
                if (
                    not isinstance(payload.get("path_id"), str)
                    or not payload["path_id"]
                    or payload.get("primitive") not in {"chase", "pulse", "sweep"}
                    or payload.get("direction") not in {"forward", "reverse"}
                ):
                    raise ValueError("path lighting layer is invalid")
            elif (
                payload.get("target_kind") not in {"fixture", "group"}
                or not isinstance(payload.get("target_id"), str)
                or not payload["target_id"]
                or payload.get("primitive") not in {"solid", "pulse"}
            ):
                raise ValueError("fixture/group lighting layer is invalid")


def _validate_transform_layers(
    editor_metadata: dict[str, JsonValue],
    scene_durations: dict[str, int],
    compositions: dict[str, dict[str, JsonValue]],
) -> None:
    raw_layers = editor_metadata.get("timeline_transform_layers", {})
    if not isinstance(raw_layers, dict):
        raise ValueError("timeline transform layer metadata must be an object")
    raw_property_keyframes = editor_metadata.get("timeline_property_keyframes", {})
    if not isinstance(raw_property_keyframes, dict):
        raise ValueError("timeline property keyframe metadata must be an object")
    if any(value for value in raw_property_keyframes.values()):
        raise ValueError(
            "generic property keyframes are unsupported until runtime or baking support exists"
        )
    durations = dict(scene_durations)
    for composition_id, composition in compositions.items():
        duration = composition["duration_ms"]
        assert isinstance(duration, int) and not isinstance(duration, bool)
        durations[composition_id] = duration
    unknown_spaces = set(raw_layers).difference(durations)
    if unknown_spaces:
        raise ValueError(
            f"transform layers reference unknown editing spaces: {sorted(unknown_spaces)}"
        )
    layer_ids: set[str] = set()
    keyframe_ids: set[str] = set()
    transform_edges: dict[str, set[str]] = {}
    for composition_id, composition in compositions.items():
        children = composition.get("children", [])
        transform_edges[composition_id] = (
            {child for child in children if isinstance(child, str)}
            if isinstance(children, list)
            else set()
        )
    for space_id, values in raw_layers.items():
        if not isinstance(values, list):
            raise ValueError(f"transform layers for {space_id} must be a list")
        if (
            values
            and space_id in compositions
            and compositions[space_id].get("space_kind") != "lighting_surface"
        ):
            _validate_render_settings(compositions[space_id].get("render_settings"))
        audio_mix_orders: set[int] = set()
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("transform layer must be an object")
            layer_id = value.get("id")
            if not isinstance(layer_id, str) or not layer_id:
                raise ValueError("transform layer requires a stable ID")
            if layer_id in layer_ids:
                raise ValueError("transform layer IDs must be unique")
            layer_ids.add(layer_id)
            if not isinstance(value.get("name"), str) or not value["name"]:
                raise ValueError("transform layer name is required")
            kind = value.get("kind")
            if kind not in {"image", "video", "audio", "composition"}:
                raise ValueError("transform layer kind is unsupported")
            if value.get("playback", "hold") not in {"hold", "loop", "once"}:
                raise ValueError("media playback must be hold, loop, or once")
            source_id = value.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                raise ValueError("transform layer source ID is required")
            if kind == "composition" and source_id not in compositions:
                raise ValueError("transform layer references an unknown composition")
            if kind == "composition" and space_id in transform_edges:
                transform_edges[space_id].add(source_id)
            if not isinstance(value.get("track_id"), str) or not value["track_id"]:
                raise ValueError("transform layer requires a track ID")
            start_ms = value.get("start_ms")
            duration_ms = value.get("duration_ms")
            if (
                not isinstance(start_ms, int)
                or isinstance(start_ms, bool)
                or start_ms < 0
                or not isinstance(duration_ms, int)
                or isinstance(duration_ms, bool)
                or duration_ms <= 0
                or start_ms + duration_ms > durations[space_id]
            ):
                raise ValueError("transform layer interval must fit its editing space")
            if value.get("layer_schema_version") != 1:
                raise ValueError("media layer requires schema version 1")
            in_point_ms = value.get("in_point_ms")
            out_point_ms = value.get("out_point_ms")
            z_index = value.get("z_index")
            fade_in_ms = value.get("fade_in_ms")
            fade_out_ms = value.get("fade_out_ms")
            if (
                not isinstance(in_point_ms, int)
                or isinstance(in_point_ms, bool)
                or in_point_ms < 0
                or not isinstance(out_point_ms, int)
                or isinstance(out_point_ms, bool)
                or out_point_ms <= in_point_ms
                or out_point_ms - in_point_ms != duration_ms
            ):
                raise ValueError(
                    "media layer source in/out points must match its timeline duration"
                )
            if not isinstance(z_index, int) or isinstance(z_index, bool):
                raise ValueError("media layer ordering must be an integer")
            if (
                not isinstance(fade_in_ms, int)
                or isinstance(fade_in_ms, bool)
                or fade_in_ms < 0
                or not isinstance(fade_out_ms, int)
                or isinstance(fade_out_ms, bool)
                or fade_out_ms < 0
                or fade_in_ms + fade_out_ms > duration_ms
            ):
                raise ValueError("media layer fades must fit its duration")
            audio = value.get("audio")
            if kind == "audio":
                if not isinstance(audio, dict):
                    raise ValueError("audio layer requires mix properties")
                gain_db = audio.get("gain_db")
                mix_order = audio.get("mix_order")
                if (
                    not isinstance(gain_db, (int, float))
                    or isinstance(gain_db, bool)
                    or gain_db < -96
                    or gain_db > 24
                    or not isinstance(audio.get("mute"), bool)
                    or not isinstance(audio.get("solo"), bool)
                    or not isinstance(mix_order, int)
                    or isinstance(mix_order, bool)
                    or mix_order < 0
                ):
                    raise ValueError("audio layer mix properties are invalid")
                if mix_order in audio_mix_orders:
                    raise ValueError("audio layer mix order must be unique")
                audio_mix_orders.add(mix_order)
            elif audio is not None:
                raise ValueError("non-audio layer cannot define audio mix properties")
            alpha_interpretation = value.get("alpha_interpretation")
            source_color_space = value.get("source_color_space")
            scaling_behavior = value.get("scaling_behavior")
            if kind == "audio":
                if any(
                    item is not None
                    for item in (
                        alpha_interpretation,
                        source_color_space,
                        scaling_behavior,
                    )
                ):
                    raise ValueError("audio layers cannot define visual conformance")
            elif (
                alpha_interpretation
                not in {"opaque", "straight", "premultiplied", "black_transparent"}
                or source_color_space not in {"srgb", "bt709", "linear_srgb"}
                or scaling_behavior not in {"fit", "fill", "stretch", "native"}
            ):
                raise ValueError("visual layer conformance is invalid")
            _validate_layer_geometry(value, kind)
            keyframes = value.get("keyframes")
            if not isinstance(keyframes, list) or (kind != "audio" and not keyframes):
                raise ValueError("visual transform layer requires at least one keyframe")
            times: list[int] = []
            for keyframe in keyframes:
                if not isinstance(keyframe, dict):
                    raise ValueError("transform keyframe must be an object")
                keyframe_id = keyframe.get("id")
                if not isinstance(keyframe_id, str) or not keyframe_id:
                    raise ValueError("transform keyframe requires a stable ID")
                if keyframe_id in keyframe_ids:
                    raise ValueError("transform keyframe IDs must be unique")
                keyframe_ids.add(keyframe_id)
                time_ms = keyframe.get("time_ms")
                if (
                    not isinstance(time_ms, int)
                    or isinstance(time_ms, bool)
                    or time_ms < 0
                    or time_ms > duration_ms
                ):
                    raise ValueError("transform keyframe time must fit its layer")
                times.append(time_ms)
                _validate_transform_keyframe(keyframe)
            if times != sorted(set(times)):
                raise ValueError("transform keyframe times must be unique and ordered")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(composition_id: str) -> None:
        if composition_id in visiting:
            raise ValueError("nested transform compositions cannot contain cycles")
        if composition_id in visited:
            return
        visiting.add(composition_id)
        for child_id in transform_edges[composition_id]:
            visit(child_id)
        visiting.remove(composition_id)
        visited.add(composition_id)

    for composition_id in compositions:
        visit(composition_id)


def _validate_render_settings(value: JsonValue | None) -> None:
    if not isinstance(value, dict):
        raise ValueError("composition with media layers requires render settings")
    if value.get("render_pipeline_revision") != 1:
        raise ValueError("render pipeline revision must be 1")
    for dimension in ("render_width", "render_height"):
        size = value.get(dimension)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > 16384:
            raise ValueError("composition render dimensions are invalid")
    if value.get("working_color_space") != "linear_srgb":
        raise ValueError("working color space must be linear_srgb")
    if value.get("working_alpha_mode") != "premultiplied":
        raise ValueError("working alpha mode must be premultiplied")
    if value.get("scaling_filter") not in {"lanczos", "bicubic"}:
        raise ValueError("composition scaling filter is unsupported")
    background = value.get("output_background_color")
    if not isinstance(background, str) or re.fullmatch(r"#[0-9A-Fa-f]{6}", background) is None:
        raise ValueError("composition output background must be an RGB color")
    if value.get("media_end_behavior") not in {
        "stop",
        "hold_last_frame",
        "loop",
    }:
        raise ValueError("composition media end behavior is invalid")
    numerator = value.get("frame_rate_numerator")
    denominator = value.get("frame_rate_denominator")
    if (
        not isinstance(numerator, int)
        or isinstance(numerator, bool)
        or numerator <= 0
        or numerator > 240000
        or not isinstance(denominator, int)
        or isinstance(denominator, bool)
        or denominator <= 0
        or denominator > 1001
    ):
        raise ValueError("composition frame rate is invalid")
    profile = value.get("output_profile")
    if not isinstance(profile, dict):
        raise ValueError("composition output profile is required")
    expected = (
        {
            "container": "mp4",
            "video_codec": "h264",
            "video_profile": "main",
            "video_level": "4.0",
            "pixel_format": "yuv420p",
            "audio_codec": "aac",
            "audio_sample_rate": 48000,
            "audio_channel_layout": "stereo",
            "b_frames": 0,
            "hardware_decoder_compatibility": "h264_main_level_4_yuv420p",
        }
        if profile.get("preset_id") == "low_power_h264"
        else {
            "container": "mp4",
            "video_codec": "h264",
            "video_profile": "baseline",
            "video_level": "3.1",
            "pixel_format": "yuv420p",
            "audio_codec": "aac",
            "audio_sample_rate": 48000,
            "audio_channel_layout": "stereo",
            "b_frames": 0,
            "hardware_decoder_compatibility": "h264_baseline_level_3_1_yuv420p",
        }
        if profile.get("preset_id") == "legacy_h264"
        else None
    )
    if expected is None or any(profile.get(key) != item for key, item in expected.items()):
        raise ValueError("composition output profile is incompatible with its preset")
    bitrate = profile.get("max_video_bitrate_kbps")
    loudness = profile.get("loudness_target_lufs")
    keyframe_interval = profile.get("keyframe_interval_frames")
    if (
        not isinstance(bitrate, int)
        or isinstance(bitrate, bool)
        or bitrate < 500
        or bitrate > 50000
        or not isinstance(loudness, (int, float))
        or isinstance(loudness, bool)
        or loudness < -40
        or loudness > -5
        or not isinstance(keyframe_interval, int)
        or isinstance(keyframe_interval, bool)
        or keyframe_interval <= 0
        or keyframe_interval > 600
    ):
        raise ValueError("composition output profile numeric settings are invalid")


def _validate_layer_geometry(layer: dict[str, JsonValue], kind: object) -> None:
    corner_pin = layer.get("corner_pin")
    mask = layer.get("mask")
    if kind == "audio" and (corner_pin is not None or mask is not None):
        raise ValueError("audio layers cannot define corner pins or masks")
    if corner_pin is not None:
        points = _layer_points(corner_pin, "corner pin", 4, 4)
        twice_area = sum(
            x0 * y1 - x1 * y0
            for (x0, y0), (x1, y1) in zip(points, (*points[1:], points[0]), strict=True)
        )
        crosses = [
            (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
            for (x0, y0), (x1, y1), (x2, y2) in zip(
                points, (*points[1:], points[0]), (*points[2:], *points[:2]), strict=True
            )
        ]
        if abs(twice_area) < 1e-9 or not (
            all(value > 0 for value in crosses) or all(value < 0 for value in crosses)
        ):
            raise ValueError("corner pin must be a convex quadrilateral in TL, TR, BR, BL order")
    if mask is not None:
        if not isinstance(mask, dict) or set(mask).difference({"points", "feather", "invert"}):
            raise ValueError("layer mask supports points, feather and invert")
        _layer_points(mask.get("points"), "layer mask", 3, 128)
        feather = mask.get("feather", 0)
        if (
            not isinstance(feather, (int, float))
            or isinstance(feather, bool)
            or not math.isfinite(feather)
            or feather < 0
        ):
            raise ValueError("layer mask feather must be a nonnegative number")
        if not isinstance(mask.get("invert", False), bool):
            raise ValueError("layer mask invert must be a boolean")


def _layer_points(
    value: JsonValue | None,
    label: str,
    minimum: int,
    maximum: int,
) -> list[tuple[float, float]]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{label} requires {minimum} to {maximum} [x, y] points")
    points: list[tuple[float, float]] = []
    for point in value:
        if (
            not isinstance(point, list)
            or len(point) != 2
            or not all(
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(item)
                for item in point
            )
        ):
            raise ValueError(f"{label} points must be finite [x, y] pairs")
        points.append((float(cast(float, point[0])), float(cast(float, point[1]))))
    return points


def _validate_transform_keyframe(keyframe: dict[str, JsonValue]) -> None:
    supported_fields = {
        "id",
        "time_ms",
        "position",
        "scale",
        "rotation_deg",
        "anchor",
        "opacity",
        "crop",
        "interpolation",
    }
    unsupported_fields = set(keyframe).difference(supported_fields)
    if unsupported_fields:
        raise ValueError(f"unsupported transform keyframe properties: {sorted(unsupported_fields)}")
    for field, length in (
        ("position", 2),
        ("scale", 2),
        ("anchor", 2),
        ("crop", 4),
    ):
        value = keyframe.get(field)
        if (
            not isinstance(value, list)
            or len(value) != length
            or not all(
                isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
            )
        ):
            raise ValueError(f"transform keyframe {field} must contain {length} numbers")
    scale = keyframe["scale"]
    crop = keyframe["crop"]
    assert isinstance(scale, list) and isinstance(crop, list)
    scale_numbers = [
        float(value)
        for value in scale
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    crop_numbers = [
        float(value)
        for value in crop
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if any(value == 0 for value in scale_numbers):
        raise ValueError("transform keyframe scale cannot be zero")
    if any(value < 0 or value > 1 for value in crop_numbers):
        raise ValueError("transform keyframe crop bounds must be normalized")
    if crop_numbers[0] + crop_numbers[2] >= 1 or crop_numbers[1] + crop_numbers[3] >= 1:
        raise ValueError("transform keyframe crop removes the entire layer")
    rotation = keyframe.get("rotation_deg")
    opacity = keyframe.get("opacity")
    if not isinstance(rotation, (int, float)) or isinstance(rotation, bool):
        raise ValueError("transform keyframe rotation must be numeric")
    if (
        not isinstance(opacity, (int, float))
        or isinstance(opacity, bool)
        or opacity < 0
        or opacity > 1
    ):
        raise ValueError("transform keyframe opacity must be between 0 and 1")
    interpolation = keyframe.get("interpolation")
    if not isinstance(interpolation, dict) or interpolation.get("kind") not in {
        "hold",
        "linear",
        "ease_in",
        "ease_out",
        "bezier",
    }:
        raise ValueError("transform keyframe interpolation is unsupported")
    unsupported_interpolation = set(interpolation).difference(
        {"kind", "temporal_bezier", "spatial_in", "spatial_out"}
    )
    if unsupported_interpolation:
        raise ValueError(
            f"unsupported transform interpolation properties: {sorted(unsupported_interpolation)}"
        )
    temporal = interpolation.get("temporal_bezier")
    spatial_in = interpolation.get("spatial_in")
    spatial_out = interpolation.get("spatial_out")
    if interpolation["kind"] == "bezier":
        for name, value, length in (
            ("temporal", temporal, 4),
            ("spatial in", spatial_in, 2),
            ("spatial out", spatial_out, 2),
        ):
            if (
                not isinstance(value, list)
                or len(value) != length
                or not all(
                    isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
                )
            ):
                raise ValueError(f"Bezier {name} controls must contain {length} numbers")
    elif any(value is not None for value in (temporal, spatial_in, spatial_out)):
        raise ValueError("non-Bezier keyframes cannot define Bezier controls")


class LoginRequest(GuiModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class SessionResponse(GuiModel):
    username: str
    role: Role


class LocalUserRequest(GuiModel):
    username: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=12, max_length=1024)
    role: Role
    disabled: bool = False


class LocalUserResponse(GuiModel):
    username: str
    role: Role
    disabled: bool
    created_at: str


class ProjectMembershipRequest(GuiModel):
    role: Role
    can_live_output: bool = False


class ProjectMembershipResponse(GuiModel):
    project_id: str
    username: str
    role: Role
    can_live_output: bool
    created_at: str
    updated_at: str


class CommentRequest(GuiModel):
    revision: int = Field(ge=1)
    object_id: str | None = Field(default=None, min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class CommentStateRequest(GuiModel):
    state: Literal["open", "resolved"]


class CommentResponse(GuiModel):
    id: str
    project_id: str
    revision: int
    object_id: str | None
    body: str
    state: Literal["open", "resolved"]
    created_by: str
    resolved_by: str | None
    created_at: str
    updated_at: str


class ChangeRequestCreate(GuiModel):
    revision: int = Field(ge=1)
    object_id: str | None = Field(default=None, min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class ChangeRequestState(GuiModel):
    state: Literal["open", "accepted", "closed"]


class ChangeRequestResponse(GuiModel):
    id: str
    project_id: str
    revision: int
    object_id: str | None
    title: str
    body: str
    state: Literal["open", "accepted", "closed"]
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str


class ReviewLinkRequest(GuiModel):
    revision: int = Field(ge=1)
    expires_at: str = Field(min_length=1, max_length=64)


class ReviewLinkResponse(GuiModel):
    id: str
    project_id: str
    revision: int
    expires_at: str
    created_by: str
    created_at: str
    token: str


class ReviewLinkView(GuiModel):
    project_id: str
    project_name: str
    revision: int
    expires_at: str


class PresenceHeartbeatRequest(GuiModel):
    location: str = Field(min_length=1, max_length=200)


class PresenceResponse(GuiModel):
    project_id: str
    username: str
    location: str
    last_seen_at: str


McpScopeValue = Literal[
    "read",
    "draft_edit",
    "direct_edit",
    "simulation",
    "publish_prepare",
    "publish",
    "activate",
    "live_operation",
    "input_config",
    "installation_edit",
    "photo_replace",
    "device_update",
    "device_config",
]


class CreateMcpTokenRequest(GuiModel):
    client_id: str = Field(min_length=1, max_length=200)
    scopes: tuple[McpScopeValue, ...] = Field(default=("read",), min_length=1)
    expires_at: str = Field(min_length=1, max_length=64)


class McpTokenResponse(GuiModel):
    id: str
    client_id: str
    username: str
    project_id: str
    scopes: tuple[McpScopeValue, ...]
    expires_at: str
    revoked_at: str | None
    created_at: str
    token: str | None = None
    installation_id: str | None = None


class ConfigureHomeAssistantRequest(GuiModel):
    enabled: bool
    expected_revision: int | None = Field(default=None, ge=0)


class McpProposalDispositionRequest(GuiModel):
    reason: str = Field(min_length=1, max_length=2000)


class McpProposalResponse(GuiModel):
    id: str
    project_id: str
    base_revision: int = Field(ge=1)
    title: str
    kind: str
    changed_paths: tuple[str, ...]
    state: Literal[
        "proposed",
        "validated",
        "approved",
        "merged",
        "rejected",
        "failed",
    ]
    client_id: str
    username: str
    validation: tuple[dict[str, JsonValue], ...]
    simulation: dict[str, JsonValue] | None
    approved_by: str | None
    disposition_reason: str | None
    created_at: str
    updated_at: str


class CreateProjectRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)
    draft: DraftProject


class ProjectSummary(GuiModel):
    id: str
    name: str
    head_revision: int = Field(ge=1)
    metadata_revision: int = Field(ge=1)
    archived_at: str | None
    created_at: str
    updated_at: str


class ProjectDraftResponse(GuiModel):
    project: ProjectSummary
    revision: int = Field(ge=1)
    draft: DraftProject


class ProjectMetadataRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectMetadataConflictResponse(GuiModel):
    code: Literal["project_metadata_conflict"] = "project_metadata_conflict"
    requested_revision: int = Field(ge=1)
    current_revision: int = Field(ge=1)


class PortableAssetRevision(GuiModel):
    revision: int = Field(ge=1)
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=200)
    original_filename: str = Field(min_length=1, max_length=255)
    scan_status: Literal["unavailable", "clean", "quarantined"]
    metadata: dict[str, JsonValue]
    created_at: str

    @field_validator("original_filename")
    @classmethod
    def validate_original_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or "\x00" in value or value in {".", ".."}:
            raise ValueError("portable original filename must be a plain filename")
        return value

    @model_validator(mode="after")
    def reject_storage_paths(self) -> "PortableAssetRevision":
        if _contains_storage_path(self.metadata):
            raise ValueError("portable asset metadata cannot contain storage paths")
        return self


class PortableAsset(GuiModel):
    source_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    current_revision: int = Field(ge=1)
    owner: str | None = Field(default=None, max_length=200)
    license: str | None = Field(default=None, max_length=500)
    provenance: str | None = Field(default=None, max_length=4000)
    review_status: Literal["unreviewed", "approved", "rejected"]
    expires_on: date | None
    folder: str | None = Field(default=None, max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    favorite: bool
    revisions: tuple[PortableAssetRevision, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_revisions(self) -> "PortableAsset":
        revision_numbers = [revision.revision for revision in self.revisions]
        if len(revision_numbers) != len(set(revision_numbers)):
            raise ValueError("portable asset revision numbers must be unique")
        if self.current_revision not in revision_numbers:
            raise ValueError("portable asset current revision must exist")
        return self


class ProjectExportDocument(GuiModel):
    schema_version: Literal[1, 2] = 2
    name: str = Field(min_length=1, max_length=200)
    draft: DraftProject
    assets: tuple[PortableAsset, ...] = ()

    @model_validator(mode="after")
    def validate_assets(self) -> "ProjectExportDocument":
        source_ids = [asset.source_id for asset in self.assets]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("portable asset source IDs must be unique")
        if self.schema_version == 1 and self.assets:
            raise ValueError("schema-v1 exports cannot contain asset manifests")
        return self


def _contains_storage_path(value: JsonValue) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = key.casefold().replace("-", "_")
            if normalized_key in {
                "path",
                "absolute_path",
                "object_path",
                "source_path",
                "temporary_path",
            }:
                return True
            if _contains_storage_path(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_storage_path(item) for item in value)
    return False


class BeginAssetUploadRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)
    original_filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=200)
    total_bytes: int = Field(gt=0)
    expected_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class AssetSceneReferenceResponse(GuiModel):
    scene_id: str
    scene_name: str
    output_id: str | None


class BeginAssetReplacementRequest(GuiModel):
    original_filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=200)
    total_bytes: int = Field(gt=0)
    expected_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class ReplaceAssetFromAssetRequest(GuiModel):
    source_asset_id: str = Field(min_length=1)


class RevertAssetRevisionRequest(GuiModel):
    revision: int = Field(ge=1)


class AssetRevisionResponse(GuiModel):
    revision: int = Field(ge=1)
    digest_sha256: str
    size_bytes: int = Field(ge=0)
    media_type: str
    original_filename: str
    status: Literal["ready", "processing", "failed", "quarantined"]
    created_at: str


class UpdateAssetMetadataRequest(GuiModel):
    owner: str | None = Field(default=None, max_length=200)
    license: str | None = Field(default=None, max_length=500)
    provenance: str | None = Field(default=None, max_length=4000)
    review_status: Literal["unreviewed", "approved", "rejected"]
    expires_on: date | None = None
    folder: str | None = Field(default=None, max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    favorite: bool = False

    @field_validator("owner", "license", "provenance")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("folder")
    @classmethod
    def validate_folder(cls, value: str | None) -> str | None:
        return _normalize_folder(value)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_tags(value)


class AssetMetadataConflictResponse(GuiModel):
    code: Literal["asset_metadata_conflict"] = "asset_metadata_conflict"
    requested_revision: int = Field(ge=1)
    current_revision: int = Field(ge=1)


class PlaceAssetRequest(GuiModel):
    scene_id: str = Field(min_length=1)
    output_id: str | None = Field(default=None, min_length=1)


class ObjectOrganizationRequest(GuiModel):
    folder: str | None = Field(default=None, max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    favorite: bool = False

    @field_validator("folder")
    @classmethod
    def validate_folder(cls, value: str | None) -> str | None:
        return _normalize_folder(value)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_tags(value)


class CreateScenePresetRequest(ObjectOrganizationRequest):
    scene_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)


class ApplyScenePresetRequest(GuiModel):
    scene_id: str = Field(min_length=1)


class AssetResponse(GuiModel):
    id: str
    project_id: str
    name: str
    status: str
    current_revision: int | None
    metadata_revision: int = Field(ge=1)
    owner: str | None
    license: str | None
    provenance: str | None
    review_status: Literal["unreviewed", "approved", "rejected"]
    expires_on: str | None
    folder: str | None
    tags: tuple[str, ...]
    favorite: bool
    error: str | None
    digest_sha256: str | None
    size_bytes: int | None
    media_type: str | None
    original_filename: str | None
    scan_status: str | None
    metadata: dict[str, object] | None
    original_file_status: Literal["pending", "available", "missing", "size_mismatch"]
    artifact_faults: tuple[str, ...]
    references: tuple[AssetSceneReferenceResponse, ...]
    unused: bool
    draft_usage: tuple[str, ...] = ()
    trashed_at: str | None = None
    trashed_by: str | None = None
    purge_after: str | None = None
    removal_blockers: tuple[str, ...] = ()
    purge_blockers: tuple[str, ...] = ()
    pending_revision: int | None = None
    replacement_status: Literal["uploading", "processing"] | None = None
    replacement_error: str | None = None
    revisions: tuple[AssetRevisionResponse, ...] = ()
    created_at: str
    updated_at: str


class AssetRemovalBlockedResponse(GuiModel):
    code: Literal["asset_in_use"] = "asset_in_use"
    message: str
    blockers: tuple[str, ...]


class PurgeAssetRequest(GuiModel):
    confirmation_name: str = Field(min_length=1, max_length=200)


class AssetPurgeResponse(GuiModel):
    asset_id: str
    name: str
    released_objects: int = Field(ge=0)
    released_bytes: int = Field(ge=0)


class AssetUploadResponse(GuiModel):
    id: str
    asset_id: str
    total_bytes: int = Field(gt=0)
    received_bytes: int = Field(ge=0)
    state: str
    error: str | None
    chunk_max_bytes: int = Field(gt=0)
    asset: AssetResponse


class RevisionSummary(GuiModel):
    revision: int = Field(ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    actor: str
    transaction_kind: str
    created_at: str


class RevisionConflictResponse(GuiModel):
    code: Literal["revision_conflict"] = "revision_conflict"
    requested_revision: int = Field(ge=1)
    current_revision: int = Field(ge=1)
    conflicting_paths: tuple[str, ...]


class CompilationResponse(GuiModel):
    project_id: str
    revision: int = Field(ge=1)
    package_digest: str
    files: tuple[str, ...]
    scene_ids: tuple[str, ...]


class UpdateRevisionReleaseRequest(GuiModel):
    name: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    approval_state: Literal["draft", "in_review", "approved", "rejected"]


class RevisionReleaseResponse(GuiModel):
    project_id: str
    revision: int = Field(ge=1)
    name: str | None
    summary: str | None
    approval_state: Literal["draft", "in_review", "approved", "rejected"]
    approved_by: str | None
    approved_at: str | None


class RevisionDiffResponse(GuiModel):
    project_id: str
    base_revision: int = Field(ge=1)
    candidate_revision: int = Field(ge=1)
    changed_paths: tuple[str, ...]


class PublicationDiagnosticResponse(GuiModel):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str


class PublicationValidationResponse(GuiModel):
    project_id: str
    revision: int = Field(ge=1)
    passed: bool
    diagnostics: tuple[PublicationDiagnosticResponse, ...]


class PublishedPackageResponse(GuiModel):
    id: str
    project_id: str
    revision: int = Field(ge=1)
    digest_sha256: str
    state: Literal["published", "staged", "active", "inactive", "failed"]
    manifest: dict[str, JsonValue]
    created_by: str
    created_at: str
    staged_at: str | None
    activated_at: str | None
    deactivated_at: str | None


class ActivationRequest(GuiModel):
    simulation_mode: bool = False


class CreateRenderJobRequest(GuiModel):
    composition_id: str = Field(min_length=1, max_length=200)


class RenderJobResponse(GuiModel):
    id: str
    project_id: str
    source_revision: int = Field(ge=1)
    composition_id: str
    cache_key: str
    state: Literal["queued", "running", "completed", "failed", "canceled"]
    progress: float = Field(ge=0, le=1)
    reserved_bytes: int = Field(ge=0)
    error: str | None
    artifact_id: str | None
    cancel_requested: bool
    created_by: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    updated_at: str


class LightingBakeStatusResponse(GuiModel):
    composition_id: str
    name: str
    space_kind: str
    state: Literal["current", "queued", "running", "failed", "missing", "unavailable"]
    reusable_across_revisions: bool
    artifact_id: str | None
    baked_revision: int | None
    job_id: str | None
    progress: float | None = Field(default=None, ge=0, le=1)
    error: str | None
    scene_ids: list[str]
    ambient: bool
    ambient_scene_timeline_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Scenes that inherit the ambient but also place this ambient composition on their "
            "timeline; that copy follows scene time and restarts when the scene loops."
        ),
    )


class LightingBakeStatusListResponse(GuiModel):
    project_id: str
    revision: int = Field(ge=1)
    compositions: list[LightingBakeStatusResponse]


class SimulationStepRequest(GuiModel):
    kind: Literal[
        "advance",
        "observe",
        "operator_start",
        "request_scene",
        "disconnect",
        "reconnect",
        "output_failure",
        "visitor",
    ]
    delta_ms: int | None = Field(default=None, ge=0)
    event_type: str | None = Field(default=None, min_length=1)
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)
    scene_id: str | None = Field(default=None, min_length=1)
    agent_id: str | None = Field(default=None, min_length=1)
    resource: str | None = Field(default=None, min_length=1)
    failed: bool | None = None
    camera: str | None = Field(default=None, min_length=1, max_length=128)
    zone: str | None = Field(default=None, max_length=128)
    label: str = Field(default="person", min_length=1, max_length=128)
    object_id: str = Field(default="preview-visitor", min_length=1, max_length=128)
    observation: Literal["arrive", "stay", "leave"] = "arrive"
    age_ms: int = Field(default=0, ge=0, le=600000)

    @model_validator(mode="after")
    def validate_step_fields(self) -> "SimulationStepRequest":
        required = {
            "advance": (self.delta_ms, "delta_ms"),
            "observe": (self.event_type, "event_type"),
            "operator_start": (self.scene_id, "scene_id"),
            "request_scene": (self.scene_id, "scene_id"),
            "output_failure": (self.resource, "resource"),
            "visitor": (self.camera, "camera"),
        }
        if self.kind in required and required[self.kind][0] is None:
            raise ValueError(f"{self.kind} simulation step requires {required[self.kind][1]}")
        if self.kind == "output_failure" and self.failed is None:
            raise ValueError("output_failure simulation step requires failed")
        return self


class GuidedResponseRequest(GuiModel):
    id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    trigger_event: Literal[
        "audience.arrived",
        "audience.present",
        "audience.departed",
        "audience.long_idle",
    ]
    scene_id: str = Field(min_length=1)
    ttl_ms: int = Field(default=30000, gt=0)
    min_presence_ms: int = Field(default=0, ge=0)
    acknowledgement_cooldown_ms: int = Field(default=60000, ge=0)
    priority: int = Field(default=50, ge=0, le=100)
    fallback_scene_id: str | None = None
    prepare_completion: bool = False
    qualification_context: Literal["preserve", "present", "absent", "any"] = "preserve"
    min_absence_before_arrival_ms: int | None = Field(default=None, ge=0)
    qualification_window_ms: int | None = Field(default=None, gt=0)
    event_ttl_ms: int | None = Field(default=None, gt=0)


class InteractionEditRequest(GuiModel):
    source_revision: int = Field(ge=1)
    operation: Literal["response", "visitors", "transition"]
    response: GuidedResponseRequest | None = None
    visitors: AudiencePresenceDefinition | None = None
    transition_index: int | None = Field(default=None, ge=0)
    scene_id: str | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "InteractionEditRequest":
        if self.visitors is not None and any(
            not value.strip()
            for value in (*self.visitors.cameras, *self.visitors.zones, *self.visitors.labels)
        ):
            raise ValueError("Visitor camera, zone and label names must not be empty")
        if self.operation == "response" and self.response is None:
            raise ValueError("Response fields are required")
        if self.operation == "visitors" and self.visitors is None:
            raise ValueError("Visitor area fields are required")
        if self.operation == "transition" and self.transition_index is None:
            raise ValueError("A transition index is required")
        if self.operation == "transition" and "scene_id" not in self.model_fields_set:
            raise ValueError("Choose a response scene, or explicitly use null for no scene request")
        return self


class ShuffleEntryRequest(GuiModel):
    scene_id: str = Field(min_length=1)
    weight: int = Field(default=1, ge=1, le=1000)


class ShowPlanEditRequest(GuiModel):
    """Focused show-plan edit; writes only program start, one scene ending or the shuffle list."""

    source_revision: int = Field(ge=1)
    operation: Literal["starting_scene", "scene_ending", "shuffle_list"]
    scene_id: str | None = Field(default=None, min_length=1)
    ending: Literal["repeat", "play_next", "shuffle", "freeze", "stop"] | None = None
    next_scene_id: str | None = Field(default=None, min_length=1)
    shuffle: tuple[ShuffleEntryRequest, ...] | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_operation(self) -> "ShowPlanEditRequest":
        if self.operation in ("starting_scene", "scene_ending") and self.scene_id is None:
            raise ValueError("Choose a scene")
        if self.operation == "scene_ending" and self.ending is None:
            raise ValueError("Choose what happens when the scene ends")
        if self.ending == "play_next" and self.next_scene_id is None:
            raise ValueError("Choose which scene plays next")
        if self.next_scene_id is not None and self.ending != "play_next":
            raise ValueError("next_scene_id is only used with the play_next ending")
        if self.operation == "shuffle_list":
            if not self.shuffle:
                raise ValueError("Keep at least one scene in the shuffle list")
            ids = [entry.scene_id for entry in self.shuffle]
            if len(ids) != len(set(ids)):
                raise ValueError("Each scene can appear in the shuffle list only once")
        return self


class InteractionPreviewRequest(GuiModel):
    source_revision: int = Field(ge=1)
    proposed_draft: DraftProject | None = None
    proposed_edit: InteractionEditRequest | None = None
    steps: tuple[SimulationStepRequest, ...] = Field(default=(), max_length=200)
    frame_index: int | None = Field(default=None, ge=0)
    starting_scene_id: str | None = None
    resource: str | None = None
    pixel_limit: int = Field(default=1000, ge=1, le=4096)


class SaveSimulationScenarioRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)
    steps: tuple[SimulationStepRequest, ...] = Field(min_length=1)
    expected: dict[str, JsonValue] = Field(default_factory=dict)


class SimulationScenarioResponse(GuiModel):
    id: str
    project_id: str
    name: str
    source_revision: int = Field(ge=1)
    steps: tuple[dict[str, JsonValue], ...]
    expected: dict[str, JsonValue]
    created_at: str
    updated_at: str


class SimulationRunResponse(GuiModel):
    scenario_id: str
    source_revision: int = Field(ge=1)
    passed: bool
    mismatches: tuple[JsonValue, ...]
    actual: dict[str, JsonValue]
    hardware_adapters_opened: Literal[False] = False
    hardware_commands_sent: Literal[False] = False


class RecordSyncRehearsalRequest(GuiModel):
    resources: tuple[str, ...] = Field(min_length=2)
    start_error_ms: float = Field(ge=0)
    drift_ms_per_minute: float = Field(ge=0)
    measurement_duration_seconds: int = Field(gt=0)
    notes: str | None = Field(default=None, max_length=2000)
    measured_at: str


class SyncRehearsalResponse(RecordSyncRehearsalRequest):
    id: str
    actor: str
    context: dict[str, JsonValue]


class EvaluateSyncAdmissionRequest(GuiModel):
    resources: tuple[str, ...] = Field(min_length=2)


class SyncAdmissionResponse(GuiModel):
    allowed: bool
    code: str
    message: str
    rehearsal: SyncRehearsalResponse | None


class ArtifactDeploymentResponse(GuiModel):
    activation_identity: str
    artifact_id: str
    resources: tuple[str, ...]
    staged_device_ids: tuple[str, ...]
    activated_device_ids: tuple[str, ...]


class InstallationResourceResponse(GuiModel):
    resource: str
    kind: Literal["media_agent", "pixel_output", "downstream_controller"]
    target: str | None = None
    pixel_count: int | None = Field(default=None, gt=0)


class InstallationStatusResponse(GuiModel):
    installation_id: str
    name: str
    revision: int = Field(ge=1)
    runtime_active: bool
    package_schema: Literal["runtime-v1-v2"] = "runtime-v1-v2"
    resources: tuple[InstallationResourceResponse, ...]


class InstallationSummaryResponse(GuiModel):
    id: str
    name: str
    revision: int = Field(ge=1)
    runtime_active: bool
    folder: str | None
    tags: tuple[str, ...]
    favorite: bool
    used_by_projects: tuple[str, ...]
    created_at: str
    updated_at: str


class CreateInstallationRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)


class UpdateInstallationRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)
    folder: str | None = Field(default=None, max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    favorite: bool = False

    @field_validator("folder")
    @classmethod
    def validate_folder(cls, value: str | None) -> str | None:
        return _normalize_folder(value)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_tags(value)


class InstallationConflictResponse(GuiModel):
    code: Literal["installation_conflict"] = "installation_conflict"
    requested_revision: int
    current_revision: int | None


class InstallationPhotoResponse(GuiModel):
    id: str
    installation_id: str
    name: str
    viewpoint: str
    original_filename: str
    media_type: str
    digest_sha256: str
    size_bytes: int = Field(gt=0)
    orientation_degrees: Literal[0, 90, 180, 270]
    crop_x: float | None
    crop_y: float | None
    crop_width: float | None
    crop_height: float | None
    scale_hint: float | None
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def description(self) -> str:
        return self.name


class PhotoDescriptionRequest(GuiModel):
    description: str = Field(min_length=1, max_length=200)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe what the photo shows or is used for")
        return value


class UpdateInstallationPhotoRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)
    viewpoint: str = Field(min_length=1, max_length=200)
    orientation_degrees: Literal[0, 90, 180, 270] = 0
    crop_x: float | None = Field(default=None, ge=0, le=1)
    crop_y: float | None = Field(default=None, ge=0, le=1)
    crop_width: float | None = Field(default=None, gt=0, le=1)
    crop_height: float | None = Field(default=None, gt=0, le=1)
    scale_hint: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_crop(self) -> "UpdateInstallationPhotoRequest":
        crop = (self.crop_x, self.crop_y, self.crop_width, self.crop_height)
        if any(value is not None for value in crop) and any(value is None for value in crop):
            raise ValueError("crop requires x, y, width, and height")
        if (
            self.crop_x is not None
            and self.crop_width is not None
            and self.crop_x + self.crop_width > 1
        ):
            raise ValueError("crop exceeds photo width")
        if (
            self.crop_y is not None
            and self.crop_height is not None
            and self.crop_y + self.crop_height > 1
        ):
            raise ValueError("crop exceeds photo height")
        return self


class GeometryPoint(GuiModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


GeometryKind = Literal[
    "output",
    "pixel_segment",
    "fixture",
    "path",
    "projector_surface",
    "mask",
    "audience_zone",
    "safety_zone",
]


class ReplaceInstallationPhotoRequest(GuiModel):
    photo_id: str = Field(min_length=1)
    source_revision: int = Field(ge=1)
    asset_id: str = Field(min_length=1)
    confirm_alignment_review: bool

    @field_validator("confirm_alignment_review")
    @classmethod
    def require_alignment_review(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Confirm that the new photo alignment must be reviewed")
        return value


class InstallationGeometryRequest(GuiModel):
    photo_id: str | None = Field(default=None, min_length=1)
    kind: GeometryKind
    name: str = Field(min_length=1, max_length=200)
    points: tuple[GeometryPoint, ...] = Field(min_length=1, max_length=500)
    closed: bool
    semantic_id: str | None = None
    color: str = Field(default="#6ee7ff", pattern=r"^#[0-9a-fA-F]{6}$")
    label_visible: bool = True
    locked: bool = False
    hidden: bool = False
    z_index: int = Field(default=0, ge=-1000, le=1000)
    direction: Literal["forward", "reverse"] = "forward"
    start_index: int | None = Field(default=None, ge=0)
    pixel_count: int | None = Field(default=None, gt=0)
    stage_points: tuple[tuple[float, float], ...] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_shape(self) -> "InstallationGeometryRequest":
        polygon_kinds = {
            "output",
            "projector_surface",
            "mask",
            "audience_zone",
            "safety_zone",
        }
        if self.kind in polygon_kinds and (not self.closed or len(self.points) < 3):
            raise ValueError("area geometry requires at least three points and a closed shape")
        if self.kind == "path" and len(self.points) < 2:
            raise ValueError("linear geometry requires at least two points")
        if self.kind == "fixture" and len(self.points) != 1:
            raise ValueError("fixture geometry requires exactly one point")
        if self.kind == "pixel_segment" and (self.start_index is None or self.pixel_count is None):
            raise ValueError("pixel segments require start index and pixel count")
        if (
            self.kind == "pixel_segment"
            and len(self.points) < 2
            and not (len(self.points) == 1 and self.pixel_count == 1)
        ):
            raise ValueError("pixel segments require two points, or one point for one pixel")
        if self.kind != "pixel_segment" and (
            self.start_index is not None or self.pixel_count is not None
        ):
            raise ValueError("pixel index range is valid only for pixel segments")
        return self


class InstallationGeometryResponse(InstallationGeometryRequest):
    id: str
    installation_id: str
    semantic_id: str
    landmark_ids: tuple[str, ...]
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str


class GeometrySuggestionRequest(GuiModel):
    photo_id: str = Field(min_length=1)
    kind: GeometryKind
    name: str = Field(min_length=1, max_length=200)
    points: tuple[GeometryPoint, ...] = Field(min_length=1, max_length=500)
    closed: bool
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=4000)


class GeometrySuggestionResponse(GeometrySuggestionRequest):
    id: str
    installation_id: str
    state: Literal["pending", "reviewed", "rejected"]
    created_by: str
    reviewed_by: str | None
    created_at: str
    updated_at: str


class ProjectorMappingRequest(GuiModel):
    resource: str = Field(min_length=1, max_length=200)
    native_width: int = Field(gt=0, le=16384)
    native_height: int = Field(gt=0, le=16384)
    stage_placement: Literal["surface", "manual"] = Field(
        default="surface",
        description=(
            "surface derives the stage extent/homography from the drawn projector "
            "surface and ignores submitted stage_x/y/width/height when derivation "
            "succeeds; manual keeps the submitted rectangle."
        ),
    )
    stage_x: float = 0
    stage_y: float = 0
    stage_width: float = Field(default=4, gt=0)
    stage_height: float = Field(default=2.25, gt=0)
    crop_x: float = Field(default=0, ge=0, le=1)
    crop_y: float = Field(default=0, ge=0, le=1)
    crop_width: float = Field(default=1, gt=0, le=1)
    crop_height: float = Field(default=1, gt=0, le=1)
    content_asset_id: str | None = None
    surface_model: Literal["planar", "subdivided"] = "planar"
    fit_mode: Literal["preserve", "stretch"] = "preserve"
    overscan: bool = False
    brightness_limit: float = Field(default=1, gt=0, le=1)
    media_start_delay_ms: int = Field(default=0, ge=-5000, le=5000)
    media_release_delay_ms: int = Field(default=0, ge=-5000, le=5000)
    orientation_degrees: Literal[0, 90, 180, 270] = 0
    mirror_x: bool = False
    mirror_y: bool = False
    mask_semantic_ids: tuple[str, ...] = ()
    calibration_source: Literal["measured_grid", "easy_corner_pin", "unverified_estimate"] = (
        "unverified_estimate"
    )
    calibration_revision: int = Field(default=1, ge=1)
    mesh_vertices: tuple[tuple[float, float, float, float], ...] = ()
    mesh_triangles: tuple[tuple[int, int, int], ...] = ()

    @model_validator(mode="after")
    def validate_crop(self) -> "ProjectorMappingRequest":
        if self.crop_x + self.crop_width > 1 or self.crop_y + self.crop_height > 1:
            raise ValueError("projector crop exceeds the native canvas")
        if self.surface_model == "planar":
            if self.mesh_vertices or self.mesh_triangles:
                raise ValueError("planar projector mapping cannot define a mesh")
            return self
        if not self.mesh_vertices and not self.mesh_triangles:
            return self
        if len(self.mesh_vertices) < 4 or len(self.mesh_triangles) < 2:
            raise ValueError("subdivided projector mapping requires an indexed mesh")
        for u, v, _, _ in self.mesh_vertices:
            if not 0 <= u <= 1 or not 0 <= v <= 1:
                raise ValueError("projector mesh UV coordinates must be normalized")
        for triangle in self.mesh_triangles:
            if len(set(triangle)) != 3 or any(
                index < 0 or index >= len(self.mesh_vertices) for index in triangle
            ):
                raise ValueError("projector mesh triangle indices are invalid")
        return self


class ProjectorMappingResponse(ProjectorMappingRequest):
    geometry_id: str
    homography: tuple[float, ...] | None
    diagnostic: str | None
    fit_diagnostics: tuple[str, ...]
    photo_views: tuple["ProjectorPhotoViewResponse", ...]
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str
    stage_homography: tuple[float, ...] | None = Field(
        default=None,
        description=(
            "Derived normalized-native (u, v) to stage homography for surface "
            "placement; null for manual or subdivided placement."
        ),
    )
    stage_placement_basis: Literal["stage_view", "stage_points", "default_scale"] | None = Field(
        default=None,
        description=(
            "What surface placement was derived from; null when manual or when "
            "the drawn surface cannot be placed yet."
        ),
    )
    stage_placement_message: str | None = None


class ProjectorPhotoViewResponse(GuiModel):
    geometry_id: str
    photo_id: str | None
    points: tuple[GeometryPoint, ...]
    homography: tuple[float, ...] | None


class PixelPositionOverride(GuiModel):
    output_index: int = Field(ge=0)
    local_x: float
    local_y: float


class LightingSurfaceRequest(GuiModel):
    resource: str = Field(min_length=1, max_length=200)
    local_width: float = Field(gt=0)
    local_height: float = Field(gt=0)
    local_points: tuple[tuple[float, float], ...] = Field(min_length=1, max_length=500)
    stage_matrix: tuple[float, float, float, float, float, float]
    fixture_id: str | None = Field(default=None, max_length=200)
    group_id: str | None = Field(default=None, max_length=200)
    path_id: str | None = Field(default=None, max_length=200)
    strand_id: str | None = Field(default=None, max_length=200)
    calibration_id: str | None = Field(default=None, max_length=200)
    pixel_footprint_width: float = Field(default=0.01, gt=0)
    pixel_footprint_height: float = Field(default=0.01, gt=0)
    brightness_limit: float = Field(default=1, gt=0, le=1)
    timing_offset_ms: int = Field(default=0, ge=-5000, le=5000)
    release_offset_ms: int = Field(default=0, ge=-5000, le=5000)
    pixel_overrides: tuple[PixelPositionOverride, ...] = Field(
        default_factory=tuple,
        max_length=10000,
    )

    @model_validator(mode="after")
    def validate_unique_overrides(self) -> "LightingSurfaceRequest":
        indices = [override.output_index for override in self.pixel_overrides]
        if len(indices) != len(set(indices)):
            raise ValueError("pixel correction output indices must be unique")
        return self


class MappedPixelResponse(GuiModel):
    output_index: int = Field(ge=0)
    local_x: float
    local_y: float
    stage_x: float
    stage_y: float
    footprint_width: float = Field(gt=0)
    footprint_height: float = Field(gt=0)


class LightingSurfaceResponse(LightingSurfaceRequest):
    geometry_id: str
    pixels: tuple[MappedPixelResponse, ...]
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str


class CreateOutputArmRequest(GuiModel):
    resource: str = Field(min_length=1)
    confirmation: str = Field(min_length=1)
    duration_seconds: int = Field(default=15, ge=5, le=30)


class OutputArmResponse(GuiModel):
    id: str
    resource: str
    resource_kind: str
    device_identity: str
    stop_action: str
    state: Literal["active", "stopped", "expired"]
    expires_at: str
    hardware_command_sent: bool = False


class StartOutputTestRequest(CreateOutputArmRequest):
    speed: Literal[5, 15, 30, 60, 120] = 30
    use_mapping: bool = True
    installation_id: str | None = None


class OutputTestSegmentResponse(GuiModel):
    name: str
    start: int
    end: int
    color: tuple[int, int, int]


class OutputTestResponse(GuiModel):
    id: str
    arm_id: str
    resource: str
    pixel_count: int
    speed: int
    state: Literal["active", "stopped", "completed", "failed"]
    pixel_index: int
    frames_sent: int
    started_at: str
    expires_at: str
    error: str | None
    stop_reason: str | None
    segments: tuple[OutputTestSegmentResponse, ...]
    simulation_only: Literal[False] = False


class OutputTestOverviewResponse(GuiModel):
    mode: str
    playback_active: bool
    outputs: tuple[InstallationResourceResponse, ...]
    test: OutputTestResponse | None


class StartTimingCalibrationRequest(GuiModel):
    target_kind: Literal["projector_mapping", "lighting_surface"]
    geometry_id: str = Field(min_length=1)
    adjustment_kind: Literal["onset", "release"]
    test_asset_id: str | None = Field(default=None, max_length=200)
    test_asset_version: str | None = Field(default=None, max_length=200)
    observed_variability_ms: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=4000)
    context: dict[str, JsonValue] = Field(default_factory=dict)


class UpdateTimingCalibrationRequest(GuiModel):
    proposed_ms: int = Field(ge=-5000, le=5000)


class TimingCalibrationSessionResponse(GuiModel):
    id: str
    target_kind: Literal["projector_mapping", "lighting_surface"]
    geometry_id: str
    adjustment_kind: Literal["onset", "release"]
    baseline_ms: int
    proposed_ms: int
    state: Literal["active", "accepted", "cancelled", "expired"]
    revision: int = Field(ge=1)
    simulation_only: Literal[True] = True
    expires_at: str


class AcceptedCalibrationResponse(GuiModel):
    id: str
    target_kind: Literal["projector_mapping", "lighting_surface"]
    geometry_id: str
    adjustment_kind: Literal["onset", "release"]
    accepted_ms: int
    resource: str
    device_id: str | None
    software_version: str | None
    test_asset_id: str | None
    test_asset_version: str | None
    observed_variability_ms: float | None
    notes: str | None
    context: dict[str, JsonValue]
    actor: str
    accepted_at: str
    stale_reasons: tuple[str, ...]


class ReviewCalibrationContextRequest(GuiModel):
    target_kind: Literal["projector_mapping", "lighting_surface"]
    geometry_id: str = Field(min_length=1)
    context: dict[str, JsonValue] = Field(default_factory=dict)


class ProjectorOverlapRequest(GuiModel):
    id: str | None = None
    first_geometry_id: str = Field(min_length=1)
    second_geometry_id: str = Field(min_length=1)
    stage_points: tuple[tuple[float, float], ...] = Field(min_length=3, max_length=100)
    ramp_width: float = Field(gt=0, le=1)
    overlap_gamma: float = Field(gt=0, le=5)
    first_brightness: float = Field(gt=0, le=1)
    second_brightness: float = Field(gt=0, le=1)
    first_white: tuple[float, float, float]
    second_white: tuple[float, float, float]
    first_color_matrix: tuple[float, ...] = Field(min_length=9, max_length=9)
    second_color_matrix: tuple[float, ...] = Field(min_length=9, max_length=9)
    first_black_level: float = Field(ge=0, le=1)
    second_black_level: float = Field(ge=0, le=1)


class ProjectorOverlapResponse(ProjectorOverlapRequest):
    id: str
    installation_id: str
    revision: int = Field(ge=1)
    diagnostics: tuple[str, ...]
    created_at: str
    updated_at: str


class StageRegionResponse(GuiModel):
    kind: Literal["covered", "overlap", "occluded", "uncovered"]
    points: tuple[tuple[float, float], ...]
    resources: tuple[str, ...]
    source_ids: tuple[str, ...]


class StageCoverageResponse(GuiModel):
    installation_id: str
    minimum_x: float
    minimum_y: float
    maximum_x: float
    maximum_y: float
    regions: tuple[StageRegionResponse, ...]


StageUnitLabel = Literal["ft", "m", "in", "cm", "units"]


class StageReferenceRequest(GuiModel):
    """Choose the photo view that defines shared-stage coordinates and its scale."""

    photo_id: str = Field(min_length=1, description="Installation photo used as the stage.")
    unit_label: StageUnitLabel = Field(default="units", description="Display unit only.")
    stage_width: float | None = Field(
        default=None,
        gt=0,
        le=100000,
        description="Real width of the whole layout view; omit when measuring a distance.",
    )
    scale_points: tuple[GeometryPoint, GeometryPoint] | None = Field(
        default=None,
        description="Two normalized layout points spanning a known real distance.",
    )
    scale_distance: float | None = Field(default=None, gt=0, le=100000)

    @model_validator(mode="after")
    def validate_scale(self) -> "StageReferenceRequest":
        measured = self.scale_points is not None or self.scale_distance is not None
        if measured and (self.scale_points is None or self.scale_distance is None):
            raise ValueError("a measured scale needs two points and a distance")
        if measured and self.stage_width is not None:
            raise ValueError("choose a view width or a measured distance, not both")
        return self


class StageReferenceResponse(GuiModel):
    installation_id: str
    photo_id: str | None
    unit_label: StageUnitLabel
    units_per_canvas: float
    stage_width: float
    stage_height: float
    scale_points: tuple[tuple[float, float], tuple[float, float]] | None
    scale_distance: float | None
    revision: int = Field(ge=1)
    applied_at: str | None
    created_at: str
    updated_at: str


class StageMapItemResponse(GuiModel):
    semantic_id: str
    name: str
    kind: str
    status: Literal["placed", "needs_update", "manual", "unplaced"]
    reference_geometry_id: str | None
    resource: str | None
    stage_points: tuple[tuple[float, float], ...] | None
    message: str | None


class StageProjectorOverlapResponse(GuiModel):
    resources: tuple[str, str]
    fraction: float = Field(ge=0, le=1)


class StageMapResponse(GuiModel):
    installation_id: str
    reference: StageReferenceResponse | None
    counts: dict[str, int]
    needs_update: bool
    items: tuple[StageMapItemResponse, ...]
    projector_overlaps: tuple[StageProjectorOverlapResponse, ...]


class InstallationMapperExportResponse(GuiModel):
    schema_version: Literal[1] = 1
    installation_id: str
    resources: tuple[InstallationResourceResponse, ...]
    projectors: tuple[dict[str, JsonValue], ...]
    lighting_surfaces: tuple[dict[str, JsonValue], ...]
    fixtures: tuple[dict[str, JsonValue], ...]
    paths: tuple[dict[str, JsonValue], ...]
    zones: tuple[dict[str, JsonValue], ...]
    stage: dict[str, JsonValue] | None = None


class MapperDiagnosticResponse(GuiModel):
    code: str
    message: str
    object_ids: tuple[str, ...]


class LoadedCatalogIdentity(GuiModel):
    state: Literal["unreported", "loaded", "recovery_required"] = "unreported"
    project_id: str | None = None
    project_name: str | None = None
    revision: int | None = None
    digest_sha256: str | None = None
    loaded_at: str | None = None
    generation: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_loaded_identity(self) -> "LoadedCatalogIdentity":
        if self.state == "loaded" and any(
            value is None
            for value in (
                self.project_id,
                self.revision,
                self.digest_sha256,
                self.loaded_at,
                self.generation,
            )
        ):
            raise ValueError("A loaded catalog requires an acknowledged complete identity")
        if self.state == "recovery_required" and not self.error:
            raise ValueError("Recovery-required catalogs must explain the failure")
        return self


class RuntimeStatusResponse(GuiModel):
    observed_at: str
    source: Literal["controller_runtime"] = "controller_runtime"
    installation_id: str | None = None
    input_health: dict[str, object] | None = None
    apply_job: dict[str, object] | None = None
    loaded_catalog: LoadedCatalogIdentity = LoadedCatalogIdentity()
    status: dict[str, object]

    @model_validator(mode="before")
    @classmethod
    def populate_loaded_catalog(cls, value: object) -> object:
        if isinstance(value, dict) and "loaded_catalog" not in value:
            status = value.get("status")
            if isinstance(status, dict) and isinstance(status.get("loaded_catalog"), dict):
                return {**value, "loaded_catalog": status["loaded_catalog"]}
        return value


class DeleteSceneRequest(GuiModel):
    scene_id: str = Field(min_length=1)
    base_revision: int = Field(ge=1)


class SceneOutputCreate(GuiModel):
    output_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    resource: str = Field(min_length=1)


class CreateSceneRequest(GuiModel):
    scene_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    name: str = Field(min_length=1, max_length=200)
    duration_ms: int = Field(gt=0)
    mode: Literal["controller_timed", "media_synchronized"] = "controller_timed"
    completion_policy: Literal["loop", "hold", "stop"] = "loop"
    output: SceneOutputCreate

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Scene name must not be blank.")
        return value.strip()


class AddSceneOutputRequest(SceneOutputCreate):
    make_clock_source: bool = False


class RenameSceneOutputBody(GuiModel):
    new_output_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class RenameSceneOutputRequest(RenameSceneOutputBody):
    output_id: str = Field(min_length=1)


class RemoveSceneOutputRequest(GuiModel):
    output_id: str = Field(min_length=1)


class SceneAuthoringOptions(GuiModel):
    project_id: str
    revision: int
    installation_id: str | None
    resources: tuple[ResourceBinding, ...]
    importable_resources: tuple[str, ...]


class InstallationObjectDetails(GuiModel):
    resource: str = Field(min_length=1)
    kind: Literal["segment", "fixture", "path"]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    geometry_semantic_ids: tuple[str, ...] = ()


class InstallationObjectEditRequest(GuiModel):
    resource: str = Field(min_length=1)
    kind: Literal["fixture", "path"]
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    segments: tuple[SegmentReference, ...] = Field(min_length=1)
    precedence: int = Field(default=0, ge=0, le=1000)
    geometry_semantic_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_object(self) -> "InstallationObjectEditRequest":
        if not self.id.strip() or not self.name.strip():
            raise ValueError("Object ID and name must not be blank.")
        if self.kind == "path" and self.precedence:
            raise ValueError("Ordered paths do not define fixture precedence.")
        if len(set(self.geometry_semantic_ids)) != len(self.geometry_semantic_ids):
            raise ValueError("Layout links must be unique.")
        return self


class InstallationObjectDeleteRequest(GuiModel):
    resource: str = Field(min_length=1)
    kind: Literal["fixture", "path"]
    id: str = Field(min_length=1)


class InstallationObjectTraversal(GuiModel):
    segment: str
    first_pixel: int
    last_pixel: int
    pixel_count: int
    reverse: bool


class InstallationObjectRecord(InstallationObjectDetails):
    segments: tuple[SegmentReference, ...]
    precedence: int
    pixel_count: int
    traversal: tuple[InstallationObjectTraversal, ...]
    references: tuple[str, ...]
    geometry_ids: tuple[str, ...]
    missing_geometry_semantic_ids: tuple[str, ...]


class InstallationObjectGeometry(GuiModel):
    semantic_id: str
    name: str
    kind: str
    resource: str | None
    geometry_ids: tuple[str, ...]


class InstallationObjectInventory(GuiModel):
    project_id: str
    project_name: str
    revision: int
    archived: bool
    installation_id: str | None
    layout_available: bool
    resources: tuple[ResourceBinding, ...]
    objects: tuple[InstallationObjectRecord, ...]
    geometry: tuple[InstallationObjectGeometry, ...]
    editable_kinds: tuple[Literal["fixture", "path"], ...] = ("fixture", "path")


class SceneLightingPreviewRequest(GuiModel):
    source_revision: int = Field(ge=1)
    scene_id: str = Field(min_length=1)
    timestamp_ms: int = Field(ge=0)
    ambient_elapsed_ms: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Time since the show's ambient lighting started. Live, the ambient runs on the "
            "controller clock and does not restart when a scene loops or changes; pass the "
            "elapsed playback time here so looping previews stay continuous. Defaults to "
            "timestamp_ms."
        ),
    )
    resource: str | None = None
    pixel_offset: int = Field(default=0, ge=0)
    pixel_limit: int = Field(default=1000, ge=1, le=4096)
    proposed_draft: DraftProject | None = None


class AmbientLightingPreviewRequest(GuiModel):
    source_revision: int = Field(ge=1)
    timestamp_ms: int = Field(ge=0, description="Time since the ambient loop started.")
    resource: str | None = None
    pixel_offset: int = Field(default=0, ge=0)
    pixel_limit: int = Field(default=1000, ge=1, le=4096)
    proposed_draft: DraftProject | None = None


class SceneMediaPreviewRequest(GuiModel):
    source_revision: int = Field(ge=1)
    scene_id: str = Field(min_length=1)
    timestamp_ms: int = Field(ge=0)
    output_id: str | None = Field(default=None, min_length=1)
    include_frames: bool = True
    max_frame_width: int = Field(default=640, ge=64, le=1920)
    proposed_draft: DraftProject | None = None


class OutputMonitorRequest(GuiModel):
    resource: str | None = Field(default=None, min_length=1)
    pixel_offset: int = Field(default=0, ge=0)
    pixel_limit: int = Field(default=1000, ge=1, le=1000)


class OperateActionRequest(GuiModel):
    action: Literal[
        "start",
        "pause",
        "resume",
        "seek",
        "stop",
        "reset",
        "automatic",
        "maintenance",
        "blackout",
    ]
    position_ms: int | None = Field(default=None, ge=0)
    scene_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_position(self) -> "OperateActionRequest":
        if self.action == "seek" and self.position_ms is None:
            raise ValueError("seek action requires position_ms")
        if self.action != "seek" and self.position_ms is not None:
            raise ValueError("position_ms is valid only for seek")
        if self.action != "start" and self.scene_id is not None:
            raise ValueError("scene_id is valid only for start")
        return self


class OperateActionResponse(GuiModel):
    action: str
    mode: str
    active_scene: str | None


class MaintenancePreviewRequest(GuiModel):
    resource: str = Field(min_length=1)
    duration_seconds: int = Field(default=15, ge=5, le=30)


class MaintenancePreviewResponse(GuiModel):
    resource: str
    resource_kind: Literal["media_agent", "pixel_output"]
    pattern: Literal["alignment_grid", "pixel_index_chase"]
    simulation_only: Literal[True] = True
    started_at: str
    expires_at: str


AgentUpdateState = Literal[
    "current",
    "available",
    "updating",
    "failed",
    "rolled_back",
    "unmanaged",
    "unsupported_platform",
    "no_bundle",
    "disconnected",
]
UPDATABLE_AGENT_STATES: frozenset[str] = frozenset({"available", "failed", "rolled_back"})


class AgentBundleResponse(GuiModel):
    platform: AgentPlatform
    version: str
    build: str
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)


class DeviceAgentUpdateResponse(GuiModel):
    """Running media agent software compared with the bundle this controller offers."""

    device_id: str
    display_name: str
    connected: bool
    state: AgentUpdateState
    detail: str | None = None
    managed: bool = False
    platform: AgentPlatform | None = None
    running_version: str | None = None
    running_digest: str | None = None
    available: AgentBundleResponse | None = None


class AgentUpdatesResponse(GuiModel):
    bundles: tuple[AgentBundleResponse, ...]
    bundle_faults: tuple[str, ...] = ()
    devices: tuple[DeviceAgentUpdateResponse, ...]
    requires: str = "maintenance mode with no scene playing; global administrator"


class StartAgentUpdatesRequest(GuiModel):
    device_ids: tuple[str, ...] | None = Field(
        default=None,
        description="Devices to update; omit to update every device with an update available.",
    )


AgentSettingsSync = Literal["applied", "pending", "offline", "unsupported", "failed"]


class DeviceAgentSettingsResponse(GuiModel):
    """Controller overrides for a media agent and what the agent reports it runs with.

    overrides are what the controller stores (null = host default from the agent's env
    file). effective, host_defaults and the display/audio choices come from the
    connected agent and are null while it is offline or too old to report them.
    """

    device_id: str
    display_name: str
    connected: bool
    supported: bool
    revision: int = Field(ge=0)
    overrides: AgentSettings
    sync: AgentSettingsSync
    detail: str | None = None
    applied_revision: int | None = Field(default=None, ge=0)
    effective: AgentEffectiveSettings | None = None
    host_defaults: AgentEffectiveSettings | None = None
    pending_player_restart: tuple[AgentSettingName, ...] = ()
    displays: tuple[AgentDisplay, ...] = ()
    audio_devices: tuple[AgentAudioDevice, ...] = ()
    editable_settings: tuple[AgentSettingName, ...] = AGENT_SETTING_NAMES


class UpdateDeviceAgentSettingsRequest(GuiModel):
    settings: AgentSettings
    expected_revision: int = Field(ge=0)


class TopologyNodeResponse(GuiModel):
    id: str
    label: str
    kind: Literal[
        "controller",
        "direct_media_agent",
        "downstream_controller",
        "controller_output",
    ]
    parent_id: str | None = None
    configured: bool
    connection: Literal["connected", "disconnected", "not_independently_detectable"]
    enrollment: Literal[
        "local_controller",
        "enrolled",
        "suspended",
        "revoked",
        "not_applicable",
    ]
    compatibility: Literal["compatible", "conflict", "unknown", "not_applicable"]
    compatibility_detail: str | None = None
    protocol_version: int | None = Field(default=None, ge=1)
    capabilities: tuple[str, ...] = ()
    assigned_resources: tuple[str, ...] = ()
    last_seen_age_ms: int | None = Field(default=None, ge=0)
    software_version: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    clock_drift_ms: float | None = None
    connection_policy: Literal["continuous", "on_demand", "not_applicable"] = "not_applicable"
    reported_pixel_count: int | None = Field(default=None, gt=0)
    signal_percent: int | None = Field(default=None, ge=0, le=100)
    output_on: bool | None = None
    diagnostic: str | None = None
    player_control: bool | None = Field(
        default=None,
        description="Whether the connected agent lets the controller start and stop its player.",
    )
    player_state: PlayerState | None = None
    keep_awake: KeepAwakeStatus | None = Field(
        default=None,
        description="Host sleep prevention set on the media agent; None when off or unreported.",
    )
    agent_update: DeviceAgentUpdateResponse | None = None
    capability_document: DeviceCapabilityDocument | None = None
    certificate_expires_at: str | None = None
    storage_total_bytes: int | None = Field(default=None, gt=0)
    storage_free_bytes: int | None = Field(default=None, ge=0)
    artifact_count: int | None = Field(default=None, ge=0)
    artifact_bytes: int | None = Field(default=None, ge=0)
    pinned_artifact_count: int | None = Field(default=None, ge=0)
    supports_pinning: bool | None = None
    supports_resumable_transfer: bool | None = None
    active_transfers: int | None = Field(default=None, ge=0)
    failed_transfers: int | None = Field(default=None, ge=0)
    artifact_faults: tuple[str, ...] = ()


class DeviceArtifactResponse(GuiModel):
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    pinned: bool
    in_use: bool
    state: Literal["verified", "corrupt"]
    last_used_unix_ms: int = Field(ge=0)


class CleanupDeviceArtifactsRequest(GuiModel):
    digests: tuple[str, ...] = Field(min_length=1)

    @field_validator("digests")
    @classmethod
    def validate_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("cleanup artifact digests must be unique")
        if any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in value):
            raise ValueError("cleanup artifact digests must be SHA-256 values")
        return value


class CleanupDeviceArtifactsResponse(GuiModel):
    removed_digests: tuple[str, ...]
    rejected_digests: tuple[str, ...]


class DeviceTransferStatusResponse(GuiModel):
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    received_bytes: int = Field(ge=0)
    total_bytes: int = Field(gt=0)
    state: Literal["active", "failed"]
    fault: str | None = None


class TopologyResponse(GuiModel):
    observed_at: str
    source: Literal["controller_runtime"] = "controller_runtime"
    enrollment_supported: bool
    nodes: tuple[TopologyNodeResponse, ...]


class CreatePairingTokenRequest(GuiModel):
    device_kind: DeviceKind
    discovered_endpoint_id: str | None = None


class PairingTokenResponse(GuiModel):
    id: str
    pairing_token: str
    device_kind: DeviceKind
    expires_at: str
    device_transport_url: str
    discovered_endpoint_id: str | None = None


class ObservedProtocolRange(GuiModel):
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "ObservedProtocolRange":
        if self.maximum < self.minimum:
            raise ValueError("protocol maximum must be greater than or equal to minimum")
        return self


class CapabilityReviewRequest(GuiModel):
    schema_version: int = Field(ge=1)
    protocol: ObservedProtocolRange
    device_kind: DeviceKind
    software_version: str = Field(min_length=1, max_length=128)
    capabilities: tuple[dict[str, JsonValue], ...] = Field(min_length=1)


class CapabilityReviewIssue(GuiModel):
    code: Literal[
        "unsupported_schema",
        "protocol_mismatch",
        "duplicate_capability",
        "missing_media_playback",
        "missing_downstream_control",
        "direct_agent_claims_downstream_control",
    ]
    message: str


class CapabilityReviewResponse(GuiModel):
    compatible: bool
    controller_protocol_version: int
    capability_kinds: tuple[str, ...]
    issues: tuple[CapabilityReviewIssue, ...]


class ProbeEndpointRequest(GuiModel):
    address: str = Field(min_length=1, max_length=2048)


class DiscoveredEndpointResponse(GuiModel):
    id: str
    base_url: str
    device_kind: DeviceKind
    software_version: str
    protocol_minimum: int = Field(ge=1)
    protocol_maximum: int = Field(ge=1)
    capability_kinds: tuple[str, ...]
    compatible: bool
    state: Literal["discovered", "pairing", "enrolled", "rejected"]
    enrolled_device_id: str | None
    observed_at: str
    updated_at: str


class SetDeviceConnectionPolicyRequest(GuiModel):
    policy: Literal["continuous", "on_demand"]


class DeviceConnectionPolicyResponse(GuiModel):
    device_id: str
    policy: Literal["continuous", "on_demand"]
    updated_at: str


class DevicePlayerActionRequest(GuiModel):
    action: Literal["start", "stop"]


class DevicePlayerResponse(GuiModel):
    device_id: str
    player_state: PlayerState


class SetDeviceEnrollmentStateRequest(GuiModel):
    state: Literal["enrolled", "suspended", "revoked"]


class DeviceEnrollmentStateResponse(GuiModel):
    device_id: str
    state: Literal["enrolled", "suspended", "revoked"]
    updated_at: str


class ConnectionTestCheck(GuiModel):
    name: Literal["enrollment", "heartbeat", "timing", "assignment"]
    passed: bool
    detail: str


class DeviceConnectionTestResponse(GuiModel):
    device_id: str
    observed_at: str
    result: Literal["passed", "failed"]
    checks: tuple[ConnectionTestCheck, ...]
    latency_ms: float | None = Field(default=None, ge=0)
    clock_offset_ms: float | None = None


class SetResourceAssignmentRequest(GuiModel):
    device_id: str = Field(min_length=1)
    capability_kind: Literal[
        "media_playback",
        "pixel_output",
        "downstream_control",
    ]
    device_output: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=1)


class ResourceAssignmentResponse(GuiModel):
    resource: str
    device_id: str
    capability_kind: str
    device_output: str | None
    revision: int = Field(ge=1)
    actor: str
    created_at: str
    updated_at: str


class ResourceAssignmentConflictResponse(GuiModel):
    code: Literal["resource_assignment_conflict"] = "resource_assignment_conflict"
    requested_revision: int | None
    current_revision: int | None


class AuditEventResponse(GuiModel):
    id: int = Field(ge=1)
    actor: str
    action: str
    target: str
    result: str
    occurred_at: str
    authorization_context: dict[str, JsonValue]


class BrowserErrorRequest(GuiModel):
    message: str = Field(min_length=1, max_length=2000)
    source: str = Field(min_length=1, max_length=500)
    occurred_at: str = Field(min_length=1, max_length=64)


class ActivityEventResponse(GuiModel):
    id: int = Field(ge=1)
    occurred_at: str
    category: Literal["device", "operation", "configuration", "project", "security"]
    action: str
    actor: str
    target: str
    result: str
