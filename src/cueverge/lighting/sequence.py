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
    channels: Literal[3, 4] = Field(
        default=3,
        description=(
            "3: RGB that replaces the covered LEDs. 4: RGB plus coverage alpha; the RGB is the "
            "color over black and is laid over the ambient/scene lighting underneath "
            "(alpha 0 leaves an LED untouched)."
        ),
    )

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
        if self.frame_count * len(self.output_indices) * self.channels > MAX_SEQUENCE_BYTES:
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
        stride = len(self.reference.output_indices) * self.reference.channels
        return self.data[index * stride : (index + 1) * stride]

    def overlay(self, pixels: list[tuple[int, int, int]], position_ms: int) -> None:
        """Lay this sequence's frame over `pixels` in place (no-op outside its interval)."""
        frame = self.frame(position_ms)
        if frame is None:
            return
        if self.reference.channels == 3:
            for offset, index in enumerate(self.reference.output_indices):
                base = offset * 3
                pixels[index] = (frame[base], frame[base + 1], frame[base + 2])
            return
        for offset, index in enumerate(self.reference.output_indices):
            base = offset * 4
            alpha = frame[base + 3]
            if alpha == 0:
                continue
            if alpha == 255:
                pixels[index] = (frame[base], frame[base + 1], frame[base + 2])
                continue
            keep = 255 - alpha
            red, green, blue = pixels[index]
            pixels[index] = (
                min(255, frame[base] + (red * keep + 127) // 255),
                min(255, frame[base + 1] + (green * keep + 127) // 255),
                min(255, frame[base + 2] + (blue * keep + 127) // 255),
            )


def load_lighting_sequence(
    root: Path, reference: LightingSequenceReference
) -> LoadedLightingSequence:
    path = (root / reference.path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("lighting sequence escapes the package")
    expected = reference.frame_count * len(reference.output_indices) * reference.channels
    if path.stat().st_size != expected:
        raise ValueError("lighting sequence size does not match its manifest")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != reference.digest_sha256:
        raise ValueError("lighting sequence checksum mismatch")
    return LoadedLightingSequence(reference, data)
