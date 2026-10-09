import math
import random
from dataclasses import dataclass

from cueverge.core.models import LightingCue, LoadedLightingCue
from cueverge.lighting.renderer import Rgb


@dataclass(frozen=True)
class LightingTimeline:
    pixel_count: int
    cues: tuple[LoadedLightingCue, ...]
    brightness_limit: float = 1.0

    def __post_init__(self) -> None:
        if self.pixel_count <= 0:
            raise ValueError("pixel_count must be positive")
        if not 0 < self.brightness_limit <= 1:
            raise ValueError("brightness_limit must be within (0, 1]")

    def render(self, position_ms: int) -> tuple[Rgb, ...]:
        output: list[Rgb] = [(0, 0, 0)] * self.pixel_count
        ordered = sorted(
            enumerate(self.cues),
            key=lambda item: (item[1].target_precedence, item[0]),
        )
        for _, loaded in ordered:
            cue = loaded.definition
            if not cue.start_ms <= position_ms < cue.start_ms + cue.duration_ms:
                continue
            values = self._render_cue(loaded, position_ms - cue.start_ms)
            for index, color in values.items():
                output[index] = _blend(output[index], color, cue.blend_mode)
        return tuple(_scale(color, self.brightness_limit) for color in output)

    def _render_cue(self, loaded: LoadedLightingCue, local_ms: int) -> dict[int, Rgb]:
        cue = loaded.definition
        indices = tuple(reversed(loaded.target_indices)) if cue.reverse else loaded.target_indices
        progress = min(1.0, local_ms / max(1, cue.duration_ms - 1))
        envelope = _envelope(cue, local_ms)
        brightness = _lerp(
            cue.brightness,
            cue.end_brightness if cue.end_brightness is not None else cue.brightness,
            progress,
        )
        base_color = _hex_color(cue.color)
        color = (
            _interpolate_color(base_color, _hex_color(cue.end_color), progress)
            if cue.primitive == "color_transition" and cue.end_color is not None
            else base_color
        )
        factor = brightness * envelope

        if cue.primitive == "blackout":
            return {index: (0, 0, 0) for index in indices}
        if cue.primitive == "pulse":
            phase = (local_ms % cue.period_ms) / cue.period_ms
            factor *= math.sin(math.pi * phase) ** 2
        if cue.primitive in {"chase", "path_sweep"}:
            return _moving_window(indices, color, factor, progress, cue.width_pixels)
        if cue.primitive == "flicker":
            bucket = local_ms // cue.period_ms
            variation = 0.25 + 0.75 * random.Random(cue.seed + bucket).random()
            factor *= variation
        if cue.primitive == "sparkle":
            bucket = local_ms // cue.period_ms
            return {
                index: (
                    _scale(color, factor)
                    if random.Random(cue.seed + bucket * self.pixel_count + index).random()
                    < cue.density
                    else (0, 0, 0)
                )
                for index in indices
            }
        if cue.primitive == "ripple":
            return _ripple(indices, color, factor, progress, cue.width_pixels)
        return {index: _scale(color, factor) for index in indices}


def _envelope(cue: LightingCue, local_ms: int) -> float:
    factor = 1.0
    if cue.fade_in_ms > 0:
        factor = min(factor, local_ms / cue.fade_in_ms)
    remaining_ms = cue.duration_ms - local_ms
    if cue.fade_out_ms > 0:
        factor = min(factor, remaining_ms / cue.fade_out_ms)
    return max(0.0, min(1.0, factor))


def _moving_window(
    indices: tuple[int, ...],
    color: Rgb,
    factor: float,
    progress: float,
    width: int,
) -> dict[int, Rgb]:
    if not indices:
        return {}
    head = round(progress * (len(indices) - 1))
    active = set(indices[max(0, head - width + 1) : head + 1])
    return {index: _scale(color, factor) if index in active else (0, 0, 0) for index in indices}


def _ripple(
    indices: tuple[int, ...],
    color: Rgb,
    factor: float,
    progress: float,
    width: int,
) -> dict[int, Rgb]:
    if not indices:
        return {}
    center = (len(indices) - 1) / 2
    radius = progress * center
    return {
        index: (
            _scale(color, factor) if abs(abs(position - center) - radius) < width else (0, 0, 0)
        )
        for position, index in enumerate(indices)
    }


def _hex_color(value: str) -> Rgb:
    color = tuple(bytes.fromhex(value.removeprefix("#")))
    return color[0], color[1], color[2]


def _interpolate_color(start: Rgb, end: Rgb, progress: float) -> Rgb:
    return (
        round(_lerp(start[0], end[0], progress)),
        round(_lerp(start[1], end[1], progress)),
        round(_lerp(start[2], end[2], progress)),
    )


def _lerp(start: float, end: float, progress: float) -> float:
    return start + (end - start) * progress


def _scale(color: Rgb, factor: float) -> Rgb:
    return (
        max(0, min(255, round(color[0] * factor))),
        max(0, min(255, round(color[1] * factor))),
        max(0, min(255, round(color[2] * factor))),
    )


def _blend(base: Rgb, overlay: Rgb, mode: str) -> Rgb:
    if mode == "replace":
        return overlay
    if mode == "add":
        return (
            min(255, base[0] + overlay[0]),
            min(255, base[1] + overlay[1]),
            min(255, base[2] + overlay[2]),
        )
    if mode == "max":
        return (
            max(base[0], overlay[0]),
            max(base[1], overlay[1]),
            max(base[2], overlay[2]),
        )
    if mode == "multiply":
        return (
            round(base[0] * overlay[0] / 255),
            round(base[1] * overlay[1] / 255),
            round(base[2] * overlay[2] / 255),
        )
    raise ValueError(f"unsupported blend mode: {mode}")
