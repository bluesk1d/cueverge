from pydantic import BaseModel, ConfigDict, Field


class ExternalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    occurred_at_ms: int = Field(ge=0)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)
