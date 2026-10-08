from pydantic import BaseModel, ConfigDict, Field


class SceneDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    pixel_count: int = Field(gt=0)
    duration_ms: int = Field(gt=0)


SYNTHETIC_SWEEP = SceneDefinition(
    id="synthetic-sweep",
    name="Synthetic Sweep",
    agent_id="fake-media-1",
    pixel_count=8,
    duration_ms=4000,
)
