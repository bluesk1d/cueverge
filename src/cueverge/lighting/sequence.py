import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SEQUENCE_BYTES = 256 * 1024 * 1024


class LightingSequenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1] = 1
    scene_id: str
    resource: str
    path: str
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_indices: tuple[int, ...] = Field(min_length=1, max_length=65536)
    frame_rate: int = Field(ge=1, le=240000)
    frame_rate_denominator: int = Field(default=1, ge=1, le=1001)
    frame_count: int = Field(gt=0)
    duration_ms: int = Field(gt=0)
    offset_ms: int = Field(default=0, ge=0)
    time_scale: float = Field(default=1, gt=0, le=100)

    @model_validator(mode="after")
    def validate_frames(self) -> "LightingSequenceReference":
        if (
            tuple(sorted(set(self.output_indices))) != self.output_indices
            or self.output_indices[0] < 0
        ):
            raise ValueError("sequence indices must be unique, nonnegative and ordered")
        period = 1000 * self.frame_rate_denominator
        if self.frame_rate > 60 * self.frame_rate_denominator:
            raise ValueError("lighting sequence frame rate exceeds 60 fps")
        if self.frame_count != (self.duration_ms * self.frame_rate + period - 1) // period:
            raise ValueError("sequence frame count does not match duration")
        if self.frame_count * len(self.output_indices) * 3 > MAX_SEQUENCE_BYTES:
            raise ValueError("lighting sequence exceeds the 256 MiB budget")
        relative = PurePosixPath(self.path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in self.path:
            raise ValueError("lighting sequence path must be package-relative")
        return self


@dataclass(frozen=True)
class LoadedLightingSequence:
    reference: LightingSequenceReference
    data: bytes

    def frame(self, position_ms: int) -> bytes | None:
        local_ms = (position_ms - self.reference.offset_ms) / self.reference.time_scale
        if local_ms < 0 or local_ms >= self.reference.duration_ms:
            return None
        index = int(
            local_ms * self.reference.frame_rate / (1000 * self.reference.frame_rate_denominator)
        )
        stride = len(self.reference.output_indices) * 3
        return self.data[index * stride : (index + 1) * stride]


def load_lighting_sequence(
    root: Path, reference: LightingSequenceReference
) -> LoadedLightingSequence:
    path = (root / reference.path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("lighting sequence escapes the package")
    expected = reference.frame_count * len(reference.output_indices) * 3
    if path.stat().st_size != expected:
        raise ValueError("lighting sequence size does not match its manifest")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != reference.digest_sha256:
        raise ValueError("lighting sequence checksum mismatch")
    return LoadedLightingSequence(reference, data)
