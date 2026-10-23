from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from cueverge.lighting.renderer import Rgb


@dataclass(frozen=True)
class RenderedFrame:
    scene_instance_id: str
    position_ms: int
    pixels: tuple[Rgb, ...]
    resource: str | None = None


class FrameSink(Protocol):
    def send(self, frame: RenderedFrame) -> None: ...

    def close(self) -> None: ...


@dataclass
class MemoryFrameSink:
    frames: list[RenderedFrame] = field(default_factory=list)
    failure: str | None = None
    max_frames: int | None = None

    def __post_init__(self) -> None:
        if self.max_frames is not None and self.max_frames < 1:
            raise ValueError("Memory frame history limit must be positive")

    def send(self, frame: RenderedFrame) -> None:
        if self.failure is not None:
            raise OSError(self.failure)
        self.frames.append(frame)
        if self.max_frames is not None:
            del self.frames[: -self.max_frames]

    def close(self) -> None:
        pass


@dataclass
class FrameSinkRouter:
    sinks: Mapping[str, FrameSink]

    def require_resources(self, resources: set[str]) -> None:
        missing = resources.difference(self.sinks)
        if missing:
            raise ValueError(f"lighting outputs have no configured sinks: {sorted(missing)}")

    def send(self, frame: RenderedFrame) -> None:
        if frame.resource is None:
            raise ValueError("routed lighting frame does not identify a resource")
        try:
            sink = self.sinks[frame.resource]
        except KeyError as error:
            raise ValueError(f"lighting output has no configured sink: {frame.resource}") from error
        sink.send(frame)

    def close(self) -> None:
        closed: set[int] = set()
        for sink in self.sinks.values():
            identity = id(sink)
            if identity not in closed:
                sink.close()
                closed.add(identity)
