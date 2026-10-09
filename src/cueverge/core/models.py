from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResourceKind(StrEnum):
    MEDIA_AGENT = "media_agent"
    PIXEL_OUTPUT = "pixel_output"


class PixelSegment(FrozenModel):
    id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    strand: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> "PixelSegment":
        if self.end <= self.start:
            raise ValueError("pixel segment end must be greater than start")
        return self

    @property
    def pixel_count(self) -> int:
        return self.end - self.start


class ResourceBinding(FrozenModel):
    resource: str = Field(min_length=1)
    kind: ResourceKind
    target: str = Field(min_length=1)
    pixel_count: int | None = Field(default=None, gt=0)
    segments: tuple[PixelSegment, ...] = ()

    @model_validator(mode="after")
    def validate_pixel_count(self) -> "ResourceBinding":
        if self.kind is ResourceKind.PIXEL_OUTPUT and self.pixel_count is None:
            raise ValueError("pixel_output binding requires pixel_count")
        if self.kind is ResourceKind.MEDIA_AGENT:
            if self.pixel_count is not None:
                raise ValueError("media_agent binding cannot define pixel_count")
            if self.segments:
                raise ValueError("media_agent binding cannot define pixel segments")
            return self

        segment_ids = [segment.id for segment in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("pixel segment ids must be unique")
        expected_start = 0
        for segment in self.segments:
            if segment.start != expected_start:
                raise ValueError("pixel segments must form a contiguous partition starting at zero")
            expected_start = segment.end
        if self.segments and expected_start != self.pixel_count:
            raise ValueError("pixel segments must end at pixel_count")
        return self


class InstallationDefinition(FrozenModel):
    schema_version: Literal[1]
    resources: tuple[ResourceBinding, ...]

    @model_validator(mode="after")
    def validate_unique_resources(self) -> "InstallationDefinition":
        names = [binding.resource for binding in self.resources]
        if len(names) != len(set(names)):
            raise ValueError("resource bindings must be unique")
        return self


class MediaDefinition(FrozenModel):
    resource: str = Field(min_length=1)
    asset: str | None = None


class LightingDefinition(FrozenModel):
    resource: str = Field(min_length=1)
    effect: Literal["synthetic_sweep", "segment_pulse"]
    color: str = Field(default="#00FF60", pattern=r"^#[0-9A-Fa-f]{6}$")
    segment: str | None = Field(default=None, min_length=1)
    pulse_times_ms: tuple[int, ...] = ()
    pulse_duration_ms: int = Field(default=120, gt=0)

    @model_validator(mode="after")
    def validate_effect_configuration(self) -> "LightingDefinition":
        if self.effect == "segment_pulse":
            if self.segment is None:
                raise ValueError("segment_pulse effect requires a segment")
            if not self.pulse_times_ms:
                raise ValueError("segment_pulse effect requires pulse_times_ms")
            if any(time_ms < 0 for time_ms in self.pulse_times_ms):
                raise ValueError("pulse times must be non-negative")
            if tuple(sorted(set(self.pulse_times_ms))) != self.pulse_times_ms:
                raise ValueError("pulse times must be unique and strictly increasing")
        return self


class CompletionDefinition(FrozenModel):
    policy: Literal["loop", "select_next", "transition_to", "hold", "stop"] = "hold"
    target_scene_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_target(self) -> "CompletionDefinition":
        if self.policy == "transition_to" and self.target_scene_id is None:
            raise ValueError("transition_to completion requires target_scene_id")
        if self.policy != "transition_to" and self.target_scene_id is not None:
            raise ValueError("target_scene_id is only valid for transition_to completion")
        return self


class SceneDefinition(FrozenModel):
    schema_version: Literal[1]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mode: Literal["media_synchronized"]
    priority: int = Field(default=50, ge=0, le=100)
    interruption: Literal["replace_by_priority", "queue", "ignore"] = "replace_by_priority"
    resources: tuple[str, ...] = Field(min_length=1)
    duration_ms: int = Field(gt=0)
    media: MediaDefinition
    lighting: LightingDefinition
    completion: CompletionDefinition = CompletionDefinition()
    failure_scene_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_claims(self) -> "SceneDefinition":
        if len(self.resources) != len(set(self.resources)):
            raise ValueError("scene resource claims must be unique")
        required = {self.media.resource, self.lighting.resource}
        missing = required.difference(self.resources)
        if missing:
            raise ValueError(f"scene must claim referenced resources: {sorted(missing)}")
        if self.lighting.effect == "segment_pulse" and any(
            time_ms + self.lighting.pulse_duration_ms > self.duration_ms
            for time_ms in self.lighting.pulse_times_ms
        ):
            raise ValueError("lighting pulse extends beyond scene duration")
        if self.failure_scene_id == self.id:
            raise ValueError("scene failure fallback cannot reference itself")
        if self.completion.target_scene_id == self.id:
            raise ValueError("scene completion target cannot reference itself")
        return self


class LoadedScene(FrozenModel):
    definition: SceneDefinition
    agent_id: str
    pixel_count: int
    pixel_segments: tuple[PixelSegment, ...] = ()
    lighting_segment: PixelSegment | None = None
    color: tuple[int, int, int]
    asset_id: str | None = None


class SceneCatalog(FrozenModel):
    package_root: Path
    scenes: dict[str, LoadedScene]
    encounter: "EncounterDefinition | None" = None
    program: "ShowProgramDefinition | None" = None
    audience: "AudiencePresenceDefinition | None" = None

    def require(self, scene_id: str) -> LoadedScene:
        try:
            return self.scenes[scene_id]
        except KeyError as error:
            raise LookupError(f"unknown scene: {scene_id}") from error


class ProgramScene(FrozenModel):
    scene_id: str = Field(min_length=1)
    weight: int = Field(default=1, ge=1, le=1000)
    cooldown_ms: int = Field(default=0, ge=0)
    conditions: dict[str, str | int | float | bool] = Field(default_factory=dict)


class ScenePool(FrozenModel):
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    scenes: tuple[ProgramScene, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_scenes(self) -> "ScenePool":
        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene pool entries must be unique")
        return self


class ShowProgramDefinition(FrozenModel):
    schema_version: Literal[1]
    initial_scene_id: str = Field(min_length=1)
    default_pool_id: str = Field(min_length=1)
    recent_history_size: int = Field(default=1, ge=0, le=100)
    random_seed: int = 0
    pools: tuple[ScenePool, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pools(self) -> "ShowProgramDefinition":
        pool_ids = [pool.id for pool in self.pools]
        if len(pool_ids) != len(set(pool_ids)):
            raise ValueError("show program pool IDs must be unique")
        if self.default_pool_id not in pool_ids:
            raise ValueError("show program default_pool_id must reference a pool")
        return self


class AudiencePresenceDefinition(FrozenModel):
    schema_version: Literal[1]
    labels: tuple[str, ...] = ("person",)
    cameras: tuple[str, ...] = ()
    zones: tuple[str, ...] = ()
    observation_freshness_ms: int = Field(default=5000, gt=0)
    arrival_confirmation_ms: int = Field(default=1000, ge=0)
    absence_grace_ms: int = Field(default=5000, ge=0)
    long_idle_ms: int = Field(default=300000, gt=0)

    @model_validator(mode="after")
    def validate_scope(self) -> "AudiencePresenceDefinition":
        if not self.labels:
            raise ValueError("audience presence labels must not be empty")
        if not self.cameras and not self.zones:
            raise ValueError("audience presence requires at least one camera or zone")
        return self


class EncounterTransition(FrozenModel):
    event_type: str = Field(min_length=1)
    from_states: tuple[str, ...] = Field(min_length=1)
    to_state: str = Field(min_length=1)
    scene_id: str | None = None
    min_state_age_ms: int = Field(default=0, ge=0)
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)


class EncounterDefinition(FrozenModel):
    schema_version: Literal[1]
    initial_state: str = Field(min_length=1)
    cooldown_state: str = Field(min_length=1)
    ambient_scene_id: str
    encounter_timeout_ms: int = Field(gt=0)
    cooldown_ms: int = Field(ge=0)
    debounce_ms: int = Field(ge=0)
    event_ttl_ms: int = Field(gt=0)
    transitions: tuple[EncounterTransition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_states(self) -> "EncounterDefinition":
        for transition in self.transitions:
            if self.cooldown_state in transition.from_states:
                raise ValueError("cooldown transitions are controlled by cooldown expiry")
        return self
