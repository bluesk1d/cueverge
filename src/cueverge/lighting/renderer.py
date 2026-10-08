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
