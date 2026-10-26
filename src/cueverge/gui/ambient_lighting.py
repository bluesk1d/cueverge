"""Show-wide ambient lighting authored in the draft and compiled into the runtime looks."""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from cueverge.core.models import InstallationDefinition, LightingCue, ResourceKind

AMBIENT_LIGHTING_KEY = "ambient_lighting"


class AmbientLightingOutput(BaseModel):
    """Ambient lighting for one LED output."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    resource: str = Field(min_length=1, description="Installation pixel_output resource.")
    cycle_ms: int = Field(
        default=10_000,
        gt=0,
        le=3_600_000,
        description="Loop length of `cues`, on the controller clock.",
    )
    brightness_limit: float = Field(default=1.0, gt=0, le=1)
    cues: tuple[LightingCue, ...] = Field(
        default=(),
        max_length=100,
        description="Procedural cues looping every cycle_ms (solid, pulse, chase, sparkle...).",
    )
    composition_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Stage composition whose baked LED sequence for this resource loops on top of the "
            "cues over its own duration; bake it before publishing."
        ),
    )

    @model_validator(mode="after")
    def validate_source(self) -> "AmbientLightingOutput":
        if not self.cues and self.composition_id is None:
            raise ValueError("ambient lighting output needs cues or a composition")
        ids = [cue.id for cue in self.cues]
        if len(ids) != len(set(ids)):
            raise ValueError("ambient lighting cue IDs must be unique")
        for cue in self.cues:
            if cue.start_ms + cue.duration_ms > self.cycle_ms:
                raise ValueError(f"ambient cue {cue.id} extends past cycle_ms")
        return self


class AmbientLighting(BaseModel):
    """`editor_metadata.ambient_lighting`: one ambient loop per LED output for every scene."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    outputs: tuple[AmbientLightingOutput, ...] = Field(default=(), max_length=64)
    excluded_scene_ids: tuple[str, ...] = Field(
        default=(),
        description=(
            "Scenes that do not inherit the ambient lighting (every other scene does). Their "
            "own lighting renders over black and LED outputs they do not light show black."
        ),
    )

    @model_validator(mode="after")
    def validate_unique(self) -> "AmbientLighting":
        resources = [output.resource for output in self.outputs]
        if len(resources) != len(set(resources)):
            raise ValueError("ambient lighting resources must be unique")
        if len(self.excluded_scene_ids) != len(set(self.excluded_scene_ids)):
            raise ValueError("ambient lighting excluded scenes must be unique")
        return self


def parse_ambient_lighting(editor_metadata: Mapping[str, JsonValue]) -> AmbientLighting | None:
    raw = editor_metadata.get(AMBIENT_LIGHTING_KEY)
    if raw is None:
        return None
    return AmbientLighting.model_validate(raw)


def validate_ambient_lighting(
    editor_metadata: Mapping[str, JsonValue],
    installation: InstallationDefinition,
    scene_ids: set[str],
) -> None:
    ambient = parse_ambient_lighting(editor_metadata)
    if ambient is None:
        return
    pixel_outputs = {
        binding.resource
        for binding in installation.resources
        if binding.kind is ResourceKind.PIXEL_OUTPUT
    }
    for output in ambient.outputs:
        if output.resource not in pixel_outputs:
            raise ValueError(f"ambient lighting resource {output.resource} is not an LED output")
    missing = sorted(set(ambient.excluded_scene_ids).difference(scene_ids))
    if missing:
        raise ValueError(f"ambient lighting excludes unknown scenes: {', '.join(missing)}")
    compositions = editor_metadata.get("compositions")
    for output in ambient.outputs:
        if output.composition_id is None:
            continue
        composition = (
            compositions.get(output.composition_id) if isinstance(compositions, dict) else None
        )
        if (
            not isinstance(composition, dict)
            or composition.get("space_kind") != "stage"
            or not composition.get("stage_lighting_surfaces")
        ):
            raise ValueError(
                f"ambient composition {output.composition_id} must be a stage composition "
                "with LED outputs"
            )
