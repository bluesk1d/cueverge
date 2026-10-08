from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResourceKind(StrEnum):
    MEDIA_AGENT = "media_agent"
    PIXEL_OUTPUT = "pixel_output"


class ResourceBinding(FrozenModel):
    resource: str = Field(min_length=1)
    kind: ResourceKind
    target: str = Field(min_length=1)
    pixel_count: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_pixel_count(self) -> "ResourceBinding":
        if self.kind is ResourceKind.PIXEL_OUTPUT and self.pixel_count is None:
            raise ValueError("pixel_output binding requires pixel_count")
        if self.kind is ResourceKind.MEDIA_AGENT and self.pixel_count is not None:
            raise ValueError("media_agent binding cannot define pixel_count")
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
    effect: Literal["synthetic_sweep"]
    color: str = Field(default="#00FF60", pattern=r"^#[0-9A-Fa-f]{6}$")


class SceneDefinition(FrozenModel):
    schema_version: Literal[1]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mode: Literal["media_synchronized"]
    priority: int = Field(default=50, ge=0, le=100)
    resources: tuple[str, ...] = Field(min_length=1)
    duration_ms: int = Field(gt=0)
    media: MediaDefinition
    lighting: LightingDefinition

    @model_validator(mode="after")
    def validate_claims(self) -> "SceneDefinition":
        if len(self.resources) != len(set(self.resources)):
            raise ValueError("scene resource claims must be unique")
        required = {self.media.resource, self.lighting.resource}
        missing = required.difference(self.resources)
        if missing:
            raise ValueError(f"scene must claim referenced resources: {sorted(missing)}")
        return self


class LoadedScene(FrozenModel):
    definition: SceneDefinition
    agent_id: str
    pixel_count: int
    color: tuple[int, int, int]
    asset_path: Path | None = None


class SceneCatalog(FrozenModel):
    package_root: Path
    scenes: dict[str, LoadedScene]

    def require(self, scene_id: str) -> LoadedScene:
        try:
            return self.scenes[scene_id]
        except KeyError as error:
            raise LookupError(f"unknown scene: {scene_id}") from error
