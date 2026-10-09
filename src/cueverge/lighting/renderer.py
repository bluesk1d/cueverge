from dataclasses import dataclass

Rgb = tuple[int, int, int]


@dataclass(frozen=True)
class SyntheticSweep:
    pixel_count: int = 8
    cycle_ms: int = 4000
    color: Rgb = (0, 255, 96)

    def __post_init__(self) -> None:
        if self.pixel_count <= 0 or self.cycle_ms <= 0:
            raise ValueError("pixel_count and cycle_ms must be positive")

    def render(self, position_ms: int) -> tuple[Rgb, ...]:
        active = (position_ms % self.cycle_ms) * self.pixel_count // self.cycle_ms
        return tuple(
            self.color if index == active else (0, 0, 0) for index in range(self.pixel_count)
        )


@dataclass(frozen=True)
class SegmentPulse:
    pixel_count: int
    segment_start: int
    segment_end: int
    pulse_times_ms: tuple[int, ...]
    pulse_duration_ms: int = 120
    color: Rgb = (255, 255, 255)

    def __post_init__(self) -> None:
        if self.pixel_count <= 0 or self.pulse_duration_ms <= 0:
            raise ValueError("pixel_count and pulse_duration_ms must be positive")
        if not 0 <= self.segment_start < self.segment_end <= self.pixel_count:
            raise ValueError("segment range must be within pixel_count")
        if not self.pulse_times_ms:
            raise ValueError("pulse_times_ms must not be empty")

    def render(self, position_ms: int) -> tuple[Rgb, ...]:
        active = any(
            pulse_time <= position_ms < pulse_time + self.pulse_duration_ms
            for pulse_time in self.pulse_times_ms
        )
        return tuple(
            self.color if active and self.segment_start <= index < self.segment_end else (0, 0, 0)
            for index in range(self.pixel_count)
        )


@dataclass(frozen=True)
class TargetPulse:
    pixel_count: int
    target_indices: tuple[int, ...]
    pulse_times_ms: tuple[int, ...]
    pulse_duration_ms: int = 120
    color: Rgb = (255, 255, 255)

    def __post_init__(self) -> None:
        if self.pixel_count <= 0 or self.pulse_duration_ms <= 0:
            raise ValueError("pixel_count and pulse_duration_ms must be positive")
        if not self.target_indices:
            raise ValueError("target_indices must not be empty")
        if len(self.target_indices) != len(set(self.target_indices)):
            raise ValueError("target_indices must be unique")
        if any(not 0 <= index < self.pixel_count for index in self.target_indices):
            raise ValueError("target index must be within pixel_count")
        if not self.pulse_times_ms:
            raise ValueError("pulse_times_ms must not be empty")

    def render(self, position_ms: int) -> tuple[Rgb, ...]:
        active = any(
            pulse_time <= position_ms < pulse_time + self.pulse_duration_ms
            for pulse_time in self.pulse_times_ms
        )
        active_indices = set(self.target_indices) if active else set()
        return tuple(
            self.color if index in active_indices else (0, 0, 0)
            for index in range(self.pixel_count)
        )
