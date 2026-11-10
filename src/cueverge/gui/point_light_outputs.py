"""Persistent installation-owned LIFX output registration."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from cueverge.config import LifxResourceConfig
from cueverge.controller.runtime import ControllerRuntime
from cueverge.lighting.lifx import LifxUdpSink
from cueverge.lighting.sink import FrameSinkRouter

_adapter = TypeAdapter(tuple[LifxResourceConfig, ...])


def load_outputs(root: Path, runtime: ControllerRuntime) -> None:
    path = root / "lifx-outputs.json"
    if not path.exists():
        return
    for config in _adapter.validate_json(path.read_bytes()):
        if isinstance(runtime.sink, FrameSinkRouter):
            current = runtime.sink.sinks.get(config.resource)
            if isinstance(current, LifxUdpSink) and (
                current.host == config.host and current.target.hex() == config.target.lower()
            ):
                continue
        sink = LifxUdpSink(
            **config.model_dump(exclude={"kind", "resource", "expected_pixel_count"})
        )
        try:
            runtime.add_point_light(config.resource, sink, restore=True)
        except Exception:
            sink.close()
            raise


def create_output(root: Path, runtime: ControllerRuntime, config: LifxResourceConfig) -> None:
    path = root / "lifx-outputs.json"
    existing = _adapter.validate_json(path.read_bytes()) if path.exists() else ()
    if any(
        c.resource == config.resource
        or c.target.lower() == config.target.lower()
        or c.host == config.host
        for c in existing
    ):
        raise ValueError("Resource, target or address is already registered")
    # Prepare durable state before exposing the output. On a rejected runtime change,
    # the original file remains untouched and the unowned socket is closed.
    sink = LifxUdpSink(**config.model_dump(exclude={"kind", "resource", "expected_pixel_count"}))
    temporary = path.with_suffix(".tmp")
    original = path.read_bytes() if path.exists() else None
    replaced = False
    try:
        root.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps([c.model_dump() for c in (*existing, config)], indent=2))
        temporary.replace(path)
        replaced = True
        runtime.add_point_light(config.resource, sink)
    except Exception:
        sink.close()
        temporary.unlink(missing_ok=True)
        if not replaced:
            raise
        if original is None:
            path.unlink(missing_ok=True)
        else:
            temporary.write_bytes(original)
            temporary.replace(path)
        raise
