from dataclasses import dataclass, field

from cueverge.lighting.renderer import Rgb


@dataclass(frozen=True)
class RenderedFrame:
    scene_instance_id: str
    position_ms: int
    pixels: tuple[Rgb, ...]


@dataclass
class MemoryFrameSink:
    frames: list[RenderedFrame] = field(default_factory=list)

    def send(self, frame: RenderedFrame) -> None:
        self.frames.append(frame)
