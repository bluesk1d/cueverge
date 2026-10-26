from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cueverge.lighting.sequence import LightingSequenceReference, LoadedLightingSequence


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResourceKind(StrEnum):
    MEDIA_AGENT = "media_agent"
    PIXEL_OUTPUT = "pixel_output"
    DOWNSTREAM_CONTROLLER = "downstream_controller"


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


class SegmentReference(FrozenModel):
    segment: str = Field(min_length=1)
    reverse: bool = False


class PixelFixture(FrozenModel):
    id: str = Field(min_length=1)
    segments: tuple[SegmentReference, ...] = Field(min_length=1)
    precedence: int = Field(default=0, ge=0, le=1000)


class PixelPath(FrozenModel):
    id: str = Field(min_length=1)
    segments: tuple[SegmentReference, ...] = Field(min_length=1)


class ResourceBinding(FrozenModel):
    resource: str = Field(min_length=1)
    kind: ResourceKind
    target: str = Field(min_length=1)
    pixel_count: int | None = Field(default=None, gt=0)
    segments: tuple[PixelSegment, ...] = ()
    fixtures: tuple[PixelFixture, ...] = ()
    paths: tuple[PixelPath, ...] = ()

    @model_validator(mode="after")
    def validate_pixel_count(self) -> "ResourceBinding":
        if self.kind is ResourceKind.PIXEL_OUTPUT and self.pixel_count is None:
            raise ValueError("pixel_output binding requires pixel_count")
        if self.kind in {
            ResourceKind.MEDIA_AGENT,
            ResourceKind.DOWNSTREAM_CONTROLLER,
        }:
            if self.pixel_count is not None:
                raise ValueError(f"{self.kind.value} binding cannot define pixel_count")
            if self.segments:
                raise ValueError(f"{self.kind.value} binding cannot define pixel segments")
            if self.fixtures or self.paths:
                raise ValueError(f"{self.kind.value} binding cannot define pixel fixtures or paths")
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
        fixture_ids = [fixture.id for fixture in self.fixtures]
        path_ids = [path.id for path in self.paths]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("pixel fixture ids must be unique")
        if len(path_ids) != len(set(path_ids)):
            raise ValueError("pixel path ids must be unique")
        known_segments = set(segment_ids)
        targets = tuple((fixture.id, fixture.segments) for fixture in self.fixtures) + tuple(
            (path.id, path.segments) for path in self.paths
        )
        for target_id, references in targets:
            referenced = [reference.segment for reference in references]
            missing = set(referenced).difference(known_segments)
            if missing:
                raise ValueError(
                    f"pixel target {target_id} references unknown segments: {sorted(missing)}"
                )
            if len(referenced) != len(set(referenced)):
                raise ValueError(f"pixel target {target_id} repeats a segment")
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


class MediaOutputDefinition(MediaDefinition):
    id: str = Field(min_length=1)
    required: bool = True
    start_delay_ms: int = Field(default=0, ge=0, le=5000)
    end_behavior: Literal["stop", "hold_last_frame", "loop"] = "hold_last_frame"
    repeat_count: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        exclude_if=lambda value: value is None,
        description=(
            "Total number of times the media plays back to back before end_behavior "
            "(hold_last_frame or stop) applies. Omit to play once."
        ),
    )

    @model_validator(mode="after")
    def validate_repeat_count(self) -> "MediaOutputDefinition":
        if self.repeat_count is not None and self.end_behavior == "loop":
            raise ValueError("repeat_count cannot be combined with end_behavior loop")
        return self


class LightingTargetDefinition(FrozenModel):
    kind: Literal["segment", "fixture", "path"]
    id: str = Field(min_length=1)


class LightingCue(FrozenModel):
    """Scene-native runtime lighting cue, preserved independently of editor timeline metadata."""

    id: str = Field(min_length=1)
    primitive: Literal[
        "solid",
        "blackout",
        "envelope",
        "pulse",
        "chase",
        "path_sweep",
        "flicker",
        "sparkle",
        "ripple",
        "color_transition",
    ]
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(gt=0)
    target: LightingTargetDefinition | None = None
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    end_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    brightness: float = Field(default=1.0, ge=0, le=1)
    end_brightness: float | None = Field(default=None, ge=0, le=1)
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)
    period_ms: int = Field(default=200, gt=0)
    width_pixels: int = Field(default=1, gt=0)
    density: float = Field(default=0.1, gt=0, le=1)
    seed: int = 0
    reverse: bool = False
    blend_mode: Literal["replace", "add", "max", "multiply"] = "replace"
    precedence: int = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def validate_timing(self) -> "LightingCue":
        if self.fade_in_ms + self.fade_out_ms > self.duration_ms:
            raise ValueError("cue fades exceed cue duration")
        if self.primitive == "color_transition" and self.end_color is None:
            raise ValueError("color_transition requires end_color")
        return self


class LightingDefinition(FrozenModel):
    resource: str = Field(min_length=1)
    effect: Literal["synthetic_sweep", "segment_pulse"] | None = None
    color: str = Field(default="#00FF60", pattern=r"^#[0-9A-Fa-f]{6}$")
    segment: str | None = Field(default=None, min_length=1)
    target: LightingTargetDefinition | None = None
    pulse_times_ms: tuple[int, ...] = ()
    pulse_duration_ms: int = Field(default=120, gt=0)
    timeline: tuple[LightingCue, ...] = ()
    brightness_limit: float = Field(
        default=1.0,
        gt=0,
        le=1,
        description="Output limit for timelines/spatial sequences; legacy effects do not apply it.",
    )

    @model_validator(mode="after")
    def validate_effect_configuration(self) -> "LightingDefinition":
        if (self.effect is None) == (not self.timeline):
            raise ValueError("lighting requires exactly one legacy effect or timeline")
        if self.timeline:
            cue_ids = [cue.id for cue in self.timeline]
            if len(cue_ids) != len(set(cue_ids)):
                raise ValueError("lighting cue IDs must be unique")
            if self.segment is not None or self.target is not None or self.pulse_times_ms:
                raise ValueError("timeline lighting cannot define legacy effect fields")
            return self
        if self.effect == "segment_pulse":
            if (self.segment is None) == (self.target is None):
                raise ValueError("segment_pulse effect requires exactly one segment or target")
            if not self.pulse_times_ms:
                raise ValueError("segment_pulse effect requires pulse_times_ms")
            if any(time_ms < 0 for time_ms in self.pulse_times_ms):
                raise ValueError("pulse times must be non-negative")
            if tuple(sorted(set(self.pulse_times_ms))) != self.pulse_times_ms:
                raise ValueError("pulse times must be unique and strictly increasing")
        return self


class LightingOutputDefinition(LightingDefinition):
    id: str = Field(min_length=1)
    required: bool = True
    timing_offset_ms: int = Field(default=0, ge=-5000, le=5000)


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


class TimeInterval(FrozenModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "TimeInterval":
        if self.end_ms <= self.start_ms:
            raise ValueError("time interval end_ms must be greater than start_ms")
        return self


class SchedulingDefinition(FrozenModel):
    request_policy: Literal[
        "priority",
        "immediate",
        "at_safe_point",
        "after_completion",
        "ignore",
    ] = "priority"
    request_ttl_ms: int = Field(default=30000, gt=0)
    safe_points_ms: tuple[int, ...] = ()
    protected_intervals: tuple[TimeInterval, ...] = ()

    @model_validator(mode="after")
    def validate_ordering(self) -> "SchedulingDefinition":
        if tuple(sorted(set(self.safe_points_ms))) != self.safe_points_ms:
            raise ValueError("safe_points_ms must be unique and strictly increasing")
        for left, right in pairwise(self.protected_intervals):
            if right.start_ms < left.end_ms:
                raise ValueError("protected intervals must not overlap")
        return self


class SceneDefinitionBase(FrozenModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mode: Literal["media_synchronized", "controller_timed"]
    priority: int = Field(default=50, ge=0, le=100)
    interruption: Literal["replace_by_priority", "queue", "ignore"] = "replace_by_priority"
    resources: tuple[str, ...] = Field(min_length=1)
    duration_ms: int = Field(gt=0)
    completion: CompletionDefinition = CompletionDefinition()
    failure_scene_id: str | None = Field(default=None, min_length=1)
    scheduling: SchedulingDefinition = SchedulingDefinition()

    def _validate_common(self) -> None:
        if len(self.resources) != len(set(self.resources)):
            raise ValueError("scene resource claims must be unique")
        if self.failure_scene_id == self.id:
            raise ValueError("scene failure fallback cannot reference itself")
        if self.completion.target_scene_id == self.id:
            raise ValueError("scene completion target cannot reference itself")
        if any(point > self.duration_ms for point in self.scheduling.safe_points_ms):
            raise ValueError("safe point exceeds scene duration")
        if any(
            interval.end_ms > self.duration_ms for interval in self.scheduling.protected_intervals
        ):
            raise ValueError("protected interval exceeds scene duration")

    def _validate_lighting(self, lighting: LightingDefinition) -> None:
        if lighting.effect == "segment_pulse" and any(
            time_ms + lighting.pulse_duration_ms > self.duration_ms
            for time_ms in lighting.pulse_times_ms
        ):
            raise ValueError("lighting pulse extends beyond scene duration")
        if any(cue.start_ms + cue.duration_ms > self.duration_ms for cue in lighting.timeline):
            raise ValueError("lighting cue extends beyond scene duration")


class SceneDefinitionV1(SceneDefinitionBase):
    schema_version: Literal[1]
    mode: Literal["media_synchronized"]
    media: MediaDefinition
    lighting: LightingDefinition

    @model_validator(mode="after")
    def validate_claims(self) -> "SceneDefinitionV1":
        self._validate_common()
        required = {self.media.resource, self.lighting.resource}
        missing = required.difference(self.resources)
        if missing:
            raise ValueError(f"scene must claim referenced resources: {sorted(missing)}")
        self._validate_lighting(self.lighting)
        return self


class SceneDefinitionV2(SceneDefinitionBase):
    schema_version: Literal[2]
    clock_source: str = Field(
        min_length=1,
        description=(
            "Use controller in controller_timed mode; in media_synchronized mode, "
            "reference a required media output ID. Clock authority never fails over automatically."
        ),
    )
    media: tuple[MediaOutputDefinition, ...] = Field(
        description=(
            "Use [] for LED-only controller_timed scenes; media_synchronized scenes require "
            "a media clock output. Controller-timed media still prepares before scene time "
            "starts, but its telemetry is diagnostic and does not drive scene time."
        )
    )
    lighting: tuple[LightingOutputDefinition, ...] = Field(
        description=(
            "Use [] for media-only scenes. At least one media or lighting output is required."
        )
    )

    @model_validator(mode="after")
    def validate_outputs(self) -> "SceneDefinitionV2":
        self._validate_common()
        output_ids = [output.id for output in self.media]
        output_ids.extend(output.id for output in self.lighting)
        if not output_ids:
            raise ValueError("scene must contain at least one media or lighting output")
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("scene output IDs must be unique")
        media_ids = {output.id for output in self.media}
        if self.mode == "controller_timed":
            if self.clock_source != "controller":
                raise ValueError("controller_timed scene clock_source must be controller")
        else:
            if self.clock_source not in media_ids:
                raise ValueError("scene clock_source must reference a media output")
            if not next(output for output in self.media if output.id == self.clock_source).required:
                raise ValueError("scene clock_source media output must be required")
        output_resources = [output.resource for output in self.media]
        output_resources.extend(output.resource for output in self.lighting)
        referenced = set(output_resources)
        missing = referenced.difference(self.resources)
        if missing:
            raise ValueError(f"scene must claim referenced resources: {sorted(missing)}")
        duplicate_resources = sorted(
            resource for resource in referenced if output_resources.count(resource) > 1
        )
        if duplicate_resources:
            raise ValueError(f"scene outputs must use unique resources: {duplicate_resources}")
        for lighting in self.lighting:
            self._validate_lighting(lighting)
        return self


SceneDefinition = SceneDefinitionV1 | SceneDefinitionV2


class LoadedMediaOutput(FrozenModel):
    definition: MediaOutputDefinition
    agent_id: str
    asset_id: str | None = None


class LoadedLightingOutput(FrozenModel):
    definition: LightingOutputDefinition
    pixel_count: int
    pixel_segments: tuple[PixelSegment, ...] = ()
    lighting_segment: PixelSegment | None = None
    lighting_target_kind: Literal["segment", "fixture", "path"] | None = None
    lighting_target_id: str | None = None
    lighting_target_indices: tuple[int, ...] = ()
    lighting_target_precedence: int = 0
    lighting_cues: tuple["LoadedLightingCue", ...] = ()
    color: tuple[int, int, int]
    spatial_sequences: tuple[LoadedLightingSequence, ...] = ()


class LoadedScene(FrozenModel):
    definition: SceneDefinition
    media_outputs: tuple[LoadedMediaOutput, ...]
    lighting_outputs: tuple[LoadedLightingOutput, ...]
    clock_source_id: str
    agent_id: str | None
    pixel_count: int
    pixel_segments: tuple[PixelSegment, ...] = ()
    lighting_segment: PixelSegment | None = None
    lighting_target_kind: Literal["segment", "fixture", "path"] | None = None
    lighting_target_id: str | None = None
    lighting_target_indices: tuple[int, ...] = ()
    lighting_target_precedence: int = 0
    lighting_cues: tuple["LoadedLightingCue", ...] = ()
    color: tuple[int, int, int]
    asset_id: str | None = None


class LoadedLightingCue(FrozenModel):
    definition: LightingCue
    target_indices: tuple[int, ...]
    target_precedence: int = 0


class SceneCatalog(FrozenModel):
    package_root: Path
    installation: InstallationDefinition
    scenes: dict[str, LoadedScene]
    encounter: "EncounterDefinition | None" = None
    program: "ShowProgramDefinition | None" = None
    audience: "AudiencePresenceDefinition | None" = None
    opportunities: "OpportunityProgramDefinition | None" = None
    lighting_looks: "LoadedLightingLooks | None" = None
    lighting_looks_by_resource: dict[str, "LoadedLightingLooks"] = Field(default_factory=dict)

    def require(self, scene_id: str) -> LoadedScene:
        try:
            return self.scenes[scene_id]
        except KeyError as error:
            raise LookupError(f"unknown scene: {scene_id}") from error


class ProgramScene(FrozenModel):
    scene_id: str = Field(min_length=1)
    weight: int = Field(default=1, ge=1, le=1000)
    cooldown_ms: int = Field(default=0, ge=0)
    available: bool = True
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


class OpportunityDefinition(FrozenModel):
    id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    trigger_event: str = Field(min_length=1)
    trigger_attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)
    required_context: dict[str, str | int | float | bool] = Field(default_factory=dict)
    ttl_ms: int = Field(gt=0)
    event_ttl_ms: int = Field(default=3000, gt=0)
    qualification_window_ms: int = Field(default=3600000, gt=0)
    min_presence_ms: int = Field(default=0, ge=0)
    min_absence_before_arrival_ms: int = Field(default=0, ge=0)
    acknowledgement_cooldown_ms: int = Field(default=0, ge=0)
    priority: int = Field(default=50, ge=0, le=100)


class OpportunityProgramDefinition(FrozenModel):
    schema_version: Literal[1]
    opportunities: tuple[OpportunityDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_opportunities(self) -> "OpportunityProgramDefinition":
        ids = [opportunity.id for opportunity in self.opportunities]
        if len(ids) != len(set(ids)):
            raise ValueError("opportunity IDs must be unique")
        return self


class LightingLookDefinition(FrozenModel):
    """Ambient (persistent) lighting for one LED output, running across scene changes."""

    resource: str = Field(min_length=1)
    cycle_ms: int = Field(gt=0)
    brightness_limit: float = Field(default=1.0, gt=0, le=1)
    baseline: tuple[LightingCue, ...] = Field(
        default=(),
        description="Procedural cues looping every cycle_ms on the controller clock.",
    )
    baseline_sequence: LightingSequenceReference | None = Field(
        default=None,
        description=(
            "Baked LED sequence (for example a lights-only composition) looping over its own "
            "duration on top of the baseline cues."
        ),
    )
    safe_state: tuple[LightingCue, ...] = Field(min_length=1)
    excluded_scene_ids: tuple[str, ...] = Field(
        default=(),
        description=(
            "Scenes that do not inherit this ambient lighting: their own lighting renders over "
            "black, and outputs they do not light show the safe state."
        ),
    )

    @model_validator(mode="after")
    def validate_cues(self) -> "LightingLookDefinition":
        if not self.baseline and self.baseline_sequence is None:
            raise ValueError("ambient lighting needs baseline cues or a baseline sequence")
        if self.baseline_sequence is not None and self.baseline_sequence.resource != self.resource:
            raise ValueError("ambient baseline sequence must drive the look's resource")
        if len(self.excluded_scene_ids) != len(set(self.excluded_scene_ids)):
            raise ValueError("ambient excluded scenes must be unique")
        for cue in self.baseline:
            if cue.start_ms + cue.duration_ms > self.cycle_ms:
                raise ValueError("baseline lighting cue exceeds look cycle")
        for cue in self.safe_state:
            if cue.start_ms != 0:
                raise ValueError("safe-state lighting cues must start at zero")
        return self


class LightingLooksDefinition(LightingLookDefinition):
    schema_version: Literal[1]


class MultiLightingLooksDefinition(FrozenModel):
    schema_version: Literal[2]
    outputs: tuple[LightingLookDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_resources(self) -> "MultiLightingLooksDefinition":
        resources = [output.resource for output in self.outputs]
        if len(resources) != len(set(resources)):
            raise ValueError("persistent lighting resources must be unique")
        return self


class LoadedLightingLooks(FrozenModel):
    definition: LightingLookDefinition
    pixel_count: int
    baseline_cues: tuple[LoadedLightingCue, ...]
    safe_state_cues: tuple[LoadedLightingCue, ...]
    baseline_sequence: LoadedLightingSequence | None = None


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
