import asyncio
import json
from pathlib import Path

import pytest

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import ShowPackageError, load_show_package
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.contracts import DraftProject
from cueverge.lighting.sink import MemoryFrameSink
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.models import PlaybackState
from cueverge.simulation import ShowSimulation
from cueverge.timing.controller import ControllerClock
from tests.unit.test_multi_output_media import ManualClock, MemoryCommandChannel


def package(tmp_path: Path, *, media: bool = False, policy: str = "hold") -> Path:
    (tmp_path / "scenes").mkdir()
    resources = [{"resource": "leds", "kind": "pixel_output", "target": "memory", "pixel_count": 8}]
    if media:
        resources.append({"resource": "video", "kind": "media_agent", "target": "player"})
    (tmp_path / "installation.json").write_text(
        json.dumps({"schema_version": 1, "resources": resources})
    )
    scene = {
        "schema_version": 2,
        "id": "preshow",
        "name": "Halloween pre-show",
        "mode": "controller_timed",
        "clock_source": "controller",
        "duration_ms": 2000,
        "resources": ["leds", "video"] if media else ["leds"],
        "media": [{"id": "video", "resource": "video"}] if media else [],
        "lighting": [{"id": "leds", "resource": "leds", "effect": "synthetic_sweep"}],
        "completion": {"policy": policy},
    }
    (tmp_path / "scenes/preshow.json").write_text(json.dumps(scene))
    return tmp_path


def test_light_only_clock_transport_and_rendering(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = ManualClock()
        sink = MemoryFrameSink()
        runtime = ControllerRuntime(
            catalog=load_show_package(package(tmp_path)), clock=clock, sink=sink
        )
        try:
            active = await runtime.start_scene("preshow")
            assert active.resolved_media_sessions == ()
            assert runtime.status()["connected_agents"] == []
            clock.now += 0.75
            assert runtime.render_frame()
            assert sink.frames[-1].position_ms == 750
            assert runtime.status()["degraded"] is False
            await runtime.pause()
            clock.now += 5
            assert runtime.status()["estimated_position_ms"] == 750
            await runtime.seek(1000)
            assert runtime.status()["estimated_position_ms"] == 1000
            assert runtime.status()["playback_state"] == "paused"
            await runtime.resume()
            clock.now += 0.25
            await runtime.resume()
            assert runtime.status()["estimated_position_ms"] == 1250
            with pytest.raises(ValueError):
                await runtime.seek(-1)
            await runtime.seek(9000)
            assert runtime.status()["estimated_position_ms"] == 2000
            runtime.render_frame()
            await runtime.wait_for_lifecycle_idle()
            assert runtime.status()["playback_state"] == "completed"
            assert runtime.scene_completions == 1
            await runtime.stop()
            assert runtime.status()["estimated_position_ms"] is None
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("policy", ["hold", "stop", "loop"])
def test_controller_duration_drives_completion(tmp_path: Path, policy: str) -> None:
    async def scenario() -> None:
        clock = ManualClock()
        runtime = ControllerRuntime(
            catalog=load_show_package(package(tmp_path, policy=policy)), clock=clock
        )
        try:
            active = await runtime.start_scene("preshow")
            clock.now += 3
            runtime.render_frame()
            await runtime.wait_for_lifecycle_idle()
            assert runtime.scene_completions == 1
            if policy == "stop":
                assert runtime.active_scene is None
            elif policy == "loop":
                assert runtime.active_scene is not None
                assert runtime.active_scene.scene_instance_id != active.scene_instance_id
                assert runtime.status()["estimated_position_ms"] == 0
            else:
                assert runtime.active_scene is active
                runtime.render_frame()
                await runtime.wait_for_lifecycle_idle()
                assert runtime.scene_completions == 1
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_controller_waits_for_prepare_but_not_media_telemetry(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = ManualClock()
        runtime = ControllerRuntime(
            catalog=load_show_package(package(tmp_path, media=True)), clock=clock
        )
        agent = FakeMediaAgent(agent_id="player")
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        try:
            active = await runtime.start_scene("preshow")
            clock.now += 0.6
            assert runtime.status()["estimated_position_ms"] == 0
            await runtime.acknowledge_command(agent.acknowledge(channel.sent[0]["request_id"]))
            assert [item["command"] for item in channel.sent] == ["prepare", "play"]
            session = active.resolved_media_sessions[0]
            agent.attach(active.scene_instance_id, session.playback_session_id)
            clock.now += 0.2
            assert runtime.ingest(agent.position(1800)).accepted
            assert runtime.status()["estimated_position_ms"] == 200
            assert runtime.status()["media_outputs"][0]["clock_source"] is False
            assert runtime.ingest(agent.position(2000, state=PlaybackState.COMPLETED)).accepted
            await runtime.wait_for_lifecycle_idle()
            assert runtime.active_scene is active
            assert runtime.scene_completions == 0
            await runtime.pause()
            await runtime.seek(500)
            await runtime.resume()
            assert [item["command"] for item in channel.sent][-3:] == ["pause", "seek", "resume"]
            runtime.disconnect_agent(agent.agent_id, channel)
            clock.now += 0.8
            assert runtime.render_frame()
            assert runtime.status()["estimated_position_ms"] == 1300
            assert runtime.status()["degraded_reason"] == "media_output_disconnected:video"
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_controller_clock_keeps_required_media_admission_and_prepare_failures(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = ManualClock()
        runtime = ControllerRuntime(
            catalog=load_show_package(package(tmp_path, media=True)),
            clock=clock,
            heartbeat_timeout_ms=10000,
        )
        try:
            with pytest.raises(RuntimeError, match="required agents are not connected"):
                await runtime.start_scene("preshow")
            agent = FakeMediaAgent(agent_id="player")
            channel = MemoryCommandChannel([])
            agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
            await runtime.start_scene("preshow")
            acknowledgment = agent.acknowledge(channel.sent[0]["request_id"]).model_copy(
                update={"status": "error", "error_code": "prepare_failed"}
            )
            await runtime.acknowledge_command(acknowledgment)
            clock.now += 5
            assert runtime.status()["estimated_position_ms"] == 0
            assert runtime.status()["degraded_reason"] == "command_error:prepare_failed"
            assert [item["command"] for item in channel.sent] == ["prepare"]
            assert not runtime.render_loop_running
            assert runtime.scene_completions == 0
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_controller_clock_starts_without_disconnected_optional_media(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = package(tmp_path, media=True)
        path = root / "scenes/preshow.json"
        scene = json.loads(path.read_text())
        scene["media"][0]["required"] = False
        path.write_text(json.dumps(scene))
        clock = ManualClock()
        runtime = ControllerRuntime(catalog=load_show_package(root), clock=clock)
        try:
            active = await runtime.start_scene("preshow")
            clock.now += 1
            assert active.resolved_media_sessions == ()
            assert runtime.status()["estimated_position_ms"] == 1000
            assert runtime.status()["degraded"] is False
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_led_only_scheduler_completes_without_operator_ticks(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = package(tmp_path, policy="stop")
        path = root / "scenes/preshow.json"
        scene = json.loads(path.read_text())
        scene["duration_ms"] = 40
        path.write_text(json.dumps(scene))
        runtime = ControllerRuntime(catalog=load_show_package(root), render_fps=100)
        try:
            await runtime.start_scene("preshow")
            async with asyncio.timeout(2):
                while runtime.active_scene is not None:
                    await asyncio.sleep(0.01)
            assert runtime.scene_completions == 1
            assert runtime.rendered_frames >= 2
            assert runtime.status()["clock_authority"] is None
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_transport_during_prepare_does_not_start_clock() -> None:
    clock = ControllerClock(2000, 10)
    clock.resume(11)
    assert clock.estimate(12).position_ms == 0
    clock.pause(12)
    clock.start(13)
    assert clock.estimate(14).position_ms == 0
    clock.resume(14)
    assert clock.estimate(14.5).position_ms == 500
    clock.start(15)
    assert clock.estimate(15).position_ms == 1000


def test_safe_point_and_completion_transition_use_controller_time(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = package(tmp_path)
        path = root / "scenes/preshow.json"
        scene = json.loads(path.read_text())
        scene["scheduling"] = {"safe_points_ms": [500]}
        path.write_text(json.dumps(scene))
        incoming = {
            **scene,
            "id": "reaction",
            "priority": 90,
            "scheduling": {"request_policy": "at_safe_point"},
            "completion": {"policy": "transition_to", "target_scene_id": "preshow"},
        }
        (root / "scenes/reaction.json").write_text(json.dumps(incoming))
        clock = ManualClock()
        runtime = ControllerRuntime(catalog=load_show_package(root), clock=clock)
        try:
            await runtime.start_scene("preshow")
            clock.now += 0.2
            assert await runtime.request_scene("reaction") == "queue"
            clock.now += 0.4
            runtime.render_frame()
            await runtime.wait_for_lifecycle_idle()
            assert runtime.active_scene is not None
            assert runtime.active_scene.definition.id == "reaction"
            clock.now += 2.1
            runtime.render_frame()
            await runtime.wait_for_lifecycle_idle()
            assert runtime.active_scene is not None
            assert runtime.active_scene.definition.id == "preshow"
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_compile_and_simulate_led_only_demo(tmp_path: Path) -> None:
    async def scenario() -> None:
        catalog = load_show_package(Path("shows/led-only-demo"))
        draft = DraftProject(
            schema_version=1,
            installation=catalog.installation,
            scenes=tuple(scene.definition for scene in catalog.scenes.values()),
            program=catalog.program,
        )
        compiled = DraftCompiler(tmp_path).compile(project_id="leds", revision=1, draft=draft)
        loaded = load_show_package(compiled.package_root)
        assert loaded.require("halloween-preshow").agent_id is None
        simulation = ShowSimulation(loaded)
        try:
            await simulation.start()
            await simulation.advance(12500)
            status = simulation.runtime.status()
            assert status["estimated_position_ms"] == 2500
            assert status["clock_authority"] == "controller"
            assert status["degraded"] is False
            assert simulation.agents == {}
            assert simulation.scene_history == ["halloween-preshow", "halloween-preshow"]
            assert simulation.frames_for("roofline.demo")[-1].position_ms == 2500
        finally:
            await simulation.shutdown()

    asyncio.run(scenario())


def test_simulates_controller_timed_media_outputs(tmp_path: Path) -> None:
    async def scenario() -> None:
        simulation = ShowSimulation(load_show_package(package(tmp_path, media=True, policy="loop")))
        try:
            await simulation.operator_start("preshow")
            await simulation.advance(2500)
            assert simulation.runtime.status()["estimated_position_ms"] == 500
            assert simulation.scene_history == ["preshow", "preshow"]
        finally:
            await simulation.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation",
    [
        {"mode": "media_synchronized"},
        {"clock_source": "missing"},
    ],
)
def test_rejects_inconsistent_clock_configuration(tmp_path: Path, mutation: dict[str, str]) -> None:
    root = package(tmp_path)
    path = root / "scenes/preshow.json"
    scene = json.loads(path.read_text())
    scene.update(mutation)
    path.write_text(json.dumps(scene))
    with pytest.raises(ShowPackageError, match="clock_source"):
        load_show_package(root)
