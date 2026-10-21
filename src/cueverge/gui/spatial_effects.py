from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat


class SpatialRipple(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    primitive: Literal["ripple"] = "ripple"
    coordinate_space: Literal["surface_local", "stage"] = "surface_local"
    origin: tuple[FiniteFloat, FiniteFloat]
    speed: float = Field(gt=0, le=10000, strict=True)
    ring_width: float = Field(gt=0, le=10000, strict=True)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    repeat_ms: int | None = Field(default=None, gt=0, strict=True)


def spatial_effect_catalog() -> dict[str, object]:
    return {
        "schema_version": 1,
        "effects": {"ripple": SpatialRipple.model_json_schema()},
        "distance_units": "installation units; not assumed to be metres",
        "time_units": "milliseconds; speed is installation units per second",
        "semantics": {
            "ripple": "Expanding circular ring in the declared coordinate space.",
            "stage": "One origin and speed shared across calibrated lighting surfaces.",
            "surface_local": "Distances in the target surface's local units.",
        },
    }
