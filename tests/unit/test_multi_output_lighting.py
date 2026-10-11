import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cueverge.controller.runtime import ActiveScene, ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.lighting.sink import FrameSinkRouter, MemoryFrameSink, RenderedFrame
from cueverge.media_agent.fake import FakeMediaAgent


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


class FailingFrameSink:
    def send(self, frame: RenderedFrame) -> None:
        raise OSError("synthetic output failure")

    def close(self) -> None:
        pass


def test_routes_each_lighting_output_to_its_bound_sink(tmp_path: Path) -> None:
    catalog = load_show_package(_multi_output_package(tmp_path))
    roofline_sink = MemoryFrameSink()
    porch_sink = MemoryFrameSink()
    runtime = ControllerRuntime(
        catalog=catalog,
        sink=FrameSinkRouter(
            {
                "roofline": roofline_sink,
                "porch": porch_sink,
            }
        ),
    )
    agent = FakeMediaAgent()
    channel = MemoryCommandChannel([])
    agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
    scene = catalog.require("multi-output")
    runtime.active_scene = active = ActiveScene(scene, "scene-1", "playback-1")
    runtime.follower.begin(active.scene_instance_id, active.playback_session_id)
    agent.attach(active.scene_instance_id, active.playback_session_id)
    assert runtime.ingest(agent.position(1000)).accepted

    assert runtime.render_frame()

    assert len(roofline_sink.frames) == 1
    assert roofline_sink.frames[0].resource == "roofline"
    assert len(roofline_sink.frames[0].pixels) == 8
    assert set(roofline_sink.frames[0].pixels) == {(0, 0, 0), (255, 0, 0)}
    assert len(porch_sink.frames) == 1
    assert porch_sink.frames[0].resource == "porch"
    assert len(porch_sink.frames[0].pixels) == 4
    assert set(porch_sink.frames[0].pixels) == {(0, 0, 0), (0, 0, 255)}
    assert runtime.rendered_frames == 2


def test_requires_resource_router_for_multiple_lighting_outputs(tmp_path: Path) -> None:
    catalog = load_show_package(_multi_output_package(tmp_path))

    with pytest.raises(ValueError, match="require a FrameSinkRouter"):
        ControllerRuntime(catalog=catalog, sink=MemoryFrameSink())


def test_lighting_failure_does_not_block_other_outputs(tmp_path: Path) -> None:
    catalog = load_show_package(_multi_output_package(tmp_path))
    porch_sink = MemoryFrameSink()
    runtime = ControllerRuntime(
        catalog=catalog,
        sink=FrameSinkRouter(
            {
                "roofline": FailingFrameSink(),
                "porch": porch_sink,
            }
        ),
    )
    agent = FakeMediaAgent()
    channel = MemoryCommandChannel([])
    agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
    scene = catalog.require("multi-output")
    runtime.active_scene = active = ActiveScene(scene, "scene-1", "playback-1")
    runtime.follower.begin(active.scene_instance_id, active.playback_session_id)
    agent.attach(active.scene_instance_id, active.playback_session_id)
    assert runtime.ingest(agent.position(1000)).accepted

    assert runtime.render_frame()

    assert len(porch_sink.frames) == 1
    status = runtime.status()
    assert status["degraded_reason"] == "lighting_output_error:roofline"
    assert status["lighting_output_error_count"] == 1
    assert status["lighting_outputs"][0]["error"] == ("OSError: synthetic output failure")
    assert status["lighting_outputs"][1]["error"] is None


def test_emits_safe_state_for_each_configured_lighting_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = _multi_output_package(tmp_path)
        looks = {
            "schema_version": 2,
            "outputs": [
                {
                    "resource": "roofline",
                    "cycle_ms": 4000,
                    "baseline": [
                        {
                            "id": "roofline-baseline",
                            "primitive": "blackout",
                            "start_ms": 0,
                            "duration_ms": 4000,
                        }
                    ],
                    "safe_state": [
                        {
                            "id": "roofline-safe",
                            "primitive": "solid",
                            "start_ms": 0,
                            "duration_ms": 1,
                            "color": "#100000",
                        }
                    ],
                },
                {
                    "resource": "porch",
                    "cycle_ms": 4000,
                    "baseline": [
                        {
                            "id": "porch-baseline",
                            "primitive": "blackout",
                            "start_ms": 0,
                            "duration_ms": 4000,
                        }
                    ],
                    "safe_state": [
                        {
                            "id": "porch-safe",
                            "primitive": "solid",
                            "start_ms": 0,
                            "duration_ms": 1,
                            "color": "#000010",
                        }
                    ],
                },
            ],
        }
        (root / "looks.json").write_text(json.dumps(looks), encoding="utf-8")
        catalog = load_show_package(root)
        roofline_sink = MemoryFrameSink()
        porch_sink = MemoryFrameSink()
        runtime = ControllerRuntime(
            catalog=catalog,
            sink=FrameSinkRouter(
                {
                    "roofline": roofline_sink,
                    "porch": porch_sink,
                }
            ),
        )

        await runtime.stop(continue_queue=False)

        assert set(catalog.lighting_looks_by_resource) == {"roofline", "porch"}
        assert roofline_sink.frames[-1].pixels == ((16, 0, 0),) * 8
        assert porch_sink.frames[-1].pixels == ((0, 0, 16),) * 4

    asyncio.run(scenario())


def test_applies_per_output_lighting_timing_offset(tmp_path: Path) -> None:
    catalog = load_show_package(_multi_output_package(tmp_path, roofline_timing_offset_ms=500))
    roofline_sink = MemoryFrameSink()
    porch_sink = MemoryFrameSink()
    runtime = ControllerRuntime(
        catalog=catalog,
        sink=FrameSinkRouter(
            {
                "roofline": roofline_sink,
                "porch": porch_sink,
            }
        ),
    )
    agent = FakeMediaAgent()
    channel = MemoryCommandChannel([])
    agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
    scene = catalog.require("multi-output")
    runtime.active_scene = active = ActiveScene(scene, "scene-1", "playback-1")
    runtime.follower.begin(active.scene_instance_id, active.playback_session_id)
    agent.attach(active.scene_instance_id, active.playback_session_id)
    assert runtime.ingest(agent.position(1000)).accepted

    assert runtime.render_frame()

    assert roofline_sink.frames[-1].position_ms == 500
    assert roofline_sink.frames[-1].pixels[1] == (255, 0, 0)
    assert porch_sink.frames[-1].position_ms == 1000
    assert porch_sink.frames[-1].pixels[1] == (0, 0, 255)


def _multi_output_package(
    tmp_path: Path,
    *,
    roofline_timing_offset_ms: int = 0,
) -> Path:
    root = tmp_path / "show"
    scenes = root / "scenes"
    scenes.mkdir(parents=True)
    installation = {
        "schema_version": 1,
        "resources": [
            {
                "resource": "projector",
                "kind": "media_agent",
                "target": "fake-media-1",
            },
            {
                "resource": "roofline",
                "kind": "pixel_output",
                "target": "roofline-memory",
                "pixel_count": 8,
            },
            {
                "resource": "porch",
                "kind": "pixel_output",
                "target": "porch-memory",
                "pixel_count": 4,
            },
        ],
    }
    scene = {
        "schema_version": 2,
        "id": "multi-output",
        "name": "Multi-output",
        "mode": "media_synchronized",
        "resources": ["projector", "roofline", "porch"],
        "duration_ms": 4000,
        "clock_source": "projector",
        "media": [{"id": "projector", "resource": "projector"}],
        "lighting": [
            {
                "id": "roofline",
                "resource": "roofline",
                "effect": "synthetic_sweep",
                "color": "#FF0000",
                "timing_offset_ms": roofline_timing_offset_ms,
            },
            {
                "id": "porch",
                "resource": "porch",
                "effect": "synthetic_sweep",
                "color": "#0000FF",
            },
        ],
    }
    (root / "installation.json").write_text(json.dumps(installation), encoding="utf-8")
    (scenes / "multi-output.json").write_text(json.dumps(scene), encoding="utf-8")
    return root
