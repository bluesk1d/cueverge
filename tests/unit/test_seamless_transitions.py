import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cueverge.controller.runtime import ActiveScene, ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.core.models import MediaOutputDefinition
from cueverge.gui.device_app import _runtime_capabilities
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.device_v2 import (
    DeviceCapabilityDocument,
    DeviceHello,
    DeviceKind,
    DeviceMediaCommand,
    MediaPlaybackCapability,
    ProtocolRange,
)
from cueverge.protocol.models import (
    MEDIA_REPEAT_CAPABILITY,
    MEDIA_SEAMLESS_CAPABILITY,
    PlaybackState,
)

SEAMLESS = ("media.playback", MEDIA_SEAMLESS_CAPABILITY, MEDIA_REPEAT_CAPABILITY)


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)

    def names(self) -> list[str]:
        return [str(command["command"]) for command in self.sent]


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _start_playing(
    runtime: ControllerRuntime,
    agent: FakeMediaAgent,
    channel: MemoryCommandChannel,
    scene_id: str,
) -> ActiveScene:
    active = await runtime.start_scene(scene_id)
    prepare = next(command for command in reversed(channel.sent) if command["command"] == "prepare")
    assert (await runtime.acknowledge_command(agent.acknowledge(prepare["request_id"]))).accepted
    session = active.session_for_agent(agent.agent_id)
    assert session is not None
    agent.attach(active.scene_instance_id, session.playback_session_id)
    return active


def _connect(
    runtime: ControllerRuntime, capabilities: tuple[str, ...] = SEAMLESS
) -> tuple[FakeMediaAgent, MemoryCommandChannel]:
    agent = FakeMediaAgent(agent_id="media-primary")
    channel = MemoryCommandChannel([])
    hello = agent.hello().model_copy(update={"capabilities": capabilities})
    agent.accept_welcome(runtime.register_agent(hello, channel))
    return agent, channel


def test_clock_media_continues_into_preloaded_next_scene_without_reload(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        agent, channel = _connect(runtime)
        first = await _start_playing(runtime, agent, channel, "intro")
        assert channel.names() == ["prepare", "play"]

        assert runtime.ingest(agent.position(500)).accepted
        await _settle()
        assert "queue" not in channel.names()

        assert runtime.ingest(agent.position(1500)).accepted
        await _settle()
        queue = channel.sent[-1]
        assert queue["command"] == "queue"
        assert queue["scene_id"] == "main"
        assert queue["asset_id"] == "main.mp4"
        assert queue["end_behavior"] == "hold_last_frame"
        assert queue["repeat_count"] == 3
        assert queue["auto_advance"] is True
        status = runtime.status()
        assert status["next_scene"] == {
            "scene_id": "main",
            "preloaded_outputs": ["primary"],
            "automatic_cut": True,
        }
        assert (await runtime.acknowledge_command(agent.acknowledge(queue["request_id"]))).accepted

        # The agent continues into the queued file: its new session reports before the
        # controller has adopted it, which is expected rather than a fencing error.
        rejected_before = runtime.rejected_telemetry
        early = agent.position(0)
        early = early.model_copy(
            update={
                "scene_instance_id": queue["scene_instance_id"],
                "playback_session_id": queue["playback_session_id"],
            }
        )
        assert runtime.ingest(early).reason == "handoff_pending"
        assert runtime.rejected_telemetry == rejected_before

        assert runtime.ingest(agent.position(4000, state=PlaybackState.COMPLETED)).accepted
        await _settle()
        active = runtime.active_scene
        assert active is not None
        assert active.definition.id == "main"
        assert active.scene_instance_id == queue["scene_instance_id"]
        assert active.playback_session_id == queue["playback_session_id"]
        assert active.scene_instance_id != first.scene_instance_id
        # Play waits until the device has switched files and holds the first frame.
        assert channel.names()[-1] == "advance"
        advance = channel.sent[-1]
        assert advance["playback_session_id"] == queue["playback_session_id"]
        assert (
            await runtime.acknowledge_command(agent.acknowledge(advance["request_id"]))
        ).accepted
        assert channel.names()[-2:] == ["advance", "play"]
        assert "stop" not in channel.names()
        assert runtime.status()["seamless_transitions"] == 1

        agent.attach(active.scene_instance_id, active.playback_session_id)
        assert runtime.ingest(agent.position(100)).accepted
        await runtime.shutdown()

    asyncio.run(scenario())


def test_devices_without_seamless_support_change_scenes_the_existing_way(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path, repeat=None)))
        agent, channel = _connect(runtime, ("media.playback",))
        await _start_playing(runtime, agent, channel, "intro")
        assert runtime.ingest(agent.position(3500)).accepted
        await _settle()
        assert "queue" not in channel.names()
        assert runtime.status()["next_scene"] is None

        assert runtime.ingest(agent.position(4000, state=PlaybackState.COMPLETED)).accepted
        await _settle()
        assert channel.names()[-2:] == ["stop", "prepare"]
        assert runtime.status()["seamless_transitions"] == 0
        await runtime.shutdown()

    asyncio.run(scenario())


def test_failed_preload_falls_back_to_a_normal_scene_change(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        agent, channel = _connect(runtime)
        await _start_playing(runtime, agent, channel, "intro")
        assert runtime.ingest(agent.position(3500)).accepted
        await _settle()
        queue = channel.sent[-1]
        assert queue["command"] == "queue"
        failed = agent.acknowledge(
            queue["request_id"], status="error", error_code="player_error", detail="no file"
        )
        assert (await runtime.acknowledge_command(failed)).accepted
        assert runtime.status()["degraded_reason"] is None

        assert runtime.ingest(agent.position(4000, state=PlaybackState.COMPLETED)).accepted
        await _settle()
        assert channel.names()[-2:] == ["stop", "prepare"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_shuffle_endings_choose_the_next_scene_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        selections: list[str] = []

        async def selector(active: ActiveScene) -> str | None:
            selections.append(active.definition.id)
            return "main"

        runtime = ControllerRuntime(
            catalog=load_show_package(_package(tmp_path, policy="select_next")),
            scene_selector=selector,
        )
        agent, channel = _connect(runtime)
        await _start_playing(runtime, agent, channel, "intro")
        assert runtime.ingest(agent.position(3500)).accepted
        await _settle()
        assert channel.sent[-1]["command"] == "queue"
        assert runtime.ingest(agent.position(4000, state=PlaybackState.COMPLETED)).accepted
        await _settle()
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "main"
        assert selections == ["intro"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_repeat_counts_require_device_support(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        _, channel = _connect(runtime, ("media.playback",))
        with pytest.raises(RuntimeError, match="do not support repeat counts"):
            await runtime.start_scene("main")
        assert channel.sent == []
        await runtime.shutdown()

    asyncio.run(scenario())


def test_lost_completion_report_still_finishes_after_automatic_cut(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        agent, channel = _connect(runtime)
        await _start_playing(runtime, agent, channel, "intro")
        assert runtime.ingest(agent.position(3500)).accepted
        await _settle()
        queue = channel.sent[-1]
        assert queue["command"] == "queue"
        assert (await runtime.acknowledge_command(agent.acknowledge(queue["request_id"]))).accepted

        following = agent.position(200).model_copy(
            update={
                "scene_instance_id": queue["scene_instance_id"],
                "playback_session_id": queue["playback_session_id"],
            }
        )
        fenced = following.model_copy(update={"agent_session_id": str(uuid4())})
        assert runtime.ingest(fenced).reason == "handoff_pending"
        await _settle()
        assert runtime.active_scene is not None
        assert runtime.active_scene.definition.id == "intro"

        # The single completed report for intro never arrived.
        assert runtime.ingest(following).reason == "handoff_pending"
        await _settle()
        active = runtime.active_scene
        assert active is not None
        assert active.definition.id == "main"
        assert active.playback_session_id == queue["playback_session_id"]
        assert channel.names()[-1] == "advance"
        assert (
            await runtime.acknowledge_command(agent.acknowledge(channel.sent[-1]["request_id"]))
        ).accepted
        assert channel.names()[-2:] == ["advance", "play"]
        assert runtime.status()["scene_completions"] == 1
        await runtime.shutdown()

    asyncio.run(scenario())


def test_projectors_start_the_next_scene_together_after_the_slowest_switch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_two_projector_package(tmp_path)))
        left = FakeMediaAgent(agent_id="media-left")
        right = FakeMediaAgent(agent_id="media-right")
        channels: dict[str, MemoryCommandChannel] = {}
        for agent in (left, right):
            channels[agent.agent_id] = MemoryCommandChannel([])
            hello = agent.hello().model_copy(update={"capabilities": SEAMLESS})
            agent.accept_welcome(runtime.register_agent(hello, channels[agent.agent_id]))

        active = await runtime.start_scene("intro")
        for agent in (left, right):
            prepare = channels[agent.agent_id].sent[-1]
            assert prepare["command"] == "prepare"
            await runtime.acknowledge_command(agent.acknowledge(prepare["request_id"]))
            session = active.session_for_agent(agent.agent_id)
            assert session is not None
            agent.attach(active.scene_instance_id, session.playback_session_id)
        assert runtime.ingest(left.position(3500)).accepted
        await _settle()
        for agent in (left, right):
            queue = channels[agent.agent_id].sent[-1]
            assert queue["command"] == "queue"
            await runtime.acknowledge_command(agent.acknowledge(queue["request_id"]))

        assert runtime.ingest(left.position(4000, state=PlaybackState.COMPLETED)).accepted
        await _settle()
        assert channels["media-left"].names()[-1] == "advance"
        assert channels["media-right"].names()[-1] == "advance"

        # The fast device switched; the slow one is still opening its file.
        right_advance = channels["media-right"].sent[-1]
        await runtime.acknowledge_command(right.acknowledge(right_advance["request_id"]))
        assert channels["media-right"].names()[-1] == "advance"
        assert channels["media-left"].names()[-1] == "advance"

        left_advance = channels["media-left"].sent[-1]
        await runtime.acknowledge_command(left.acknowledge(left_advance["request_id"]))
        assert channels["media-left"].names()[-1] == "play"
        assert channels["media-right"].names()[-1] == "play"
        assert channels["media-right"].names().count("play") == 2
        await runtime.shutdown()

    asyncio.run(scenario())


def test_preloaded_changes_apply_calibrated_output_timing(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_two_projector_package(tmp_path)))
        runtime.set_output_timing({"projector.right": 40})
        left = FakeMediaAgent(agent_id="media-left")
        right = FakeMediaAgent(agent_id="media-right")
        channels: dict[str, MemoryCommandChannel] = {}
        for agent in (left, right):
            channels[agent.agent_id] = MemoryCommandChannel([])
            hello = agent.hello().model_copy(update={"capabilities": SEAMLESS})
            agent.accept_welcome(runtime.register_agent(hello, channels[agent.agent_id]))
        active = await runtime.start_scene("intro")
        for agent in (left, right):
            prepare = channels[agent.agent_id].sent[-1]
            await runtime.acknowledge_command(agent.acknowledge(prepare["request_id"]))
            session = active.session_for_agent(agent.agent_id)
            assert session is not None
            agent.attach(active.scene_instance_id, session.playback_session_id)
        await asyncio.sleep(0.06)
        assert runtime.ingest(left.position(3500)).accepted
        await _settle()
        for agent in (left, right):
            queue = channels[agent.agent_id].sent[-1]
            await runtime.acknowledge_command(agent.acknowledge(queue["request_id"]))
        assert runtime.ingest(left.position(4000, state=PlaybackState.COMPLETED)).accepted
        await _settle()
        for agent in (left, right):
            advance = channels[agent.agent_id].sent[-1]
            await runtime.acknowledge_command(agent.acknowledge(advance["request_id"]))

        assert channels["media-left"].names()[-1] == "play"
        assert channels["media-right"].names()[-1] == "advance"
        await asyncio.sleep(0.06)
        assert channels["media-right"].names()[-1] == "play"
        next_scene = runtime.active_scene
        assert next_scene is not None
        assert next_scene.session_for_agent("media-right").installation_delay_ms == 40  # type: ignore[union-attr]
        await runtime.shutdown()

    asyncio.run(scenario())


async def _queue_after_follower_reports(
    tmp_path: Path, *, right_duration_ms: int, left_end: str = "hold_last_frame"
) -> tuple[dict[str, Any], dict[str, Any], ControllerRuntime]:
    runtime = ControllerRuntime(
        catalog=load_show_package(_two_projector_package(tmp_path, left_end=left_end))
    )
    left = FakeMediaAgent(agent_id="media-left")
    right = FakeMediaAgent(agent_id="media-right")
    channels: dict[str, MemoryCommandChannel] = {}
    for agent in (left, right):
        channels[agent.agent_id] = MemoryCommandChannel([])
        hello = agent.hello().model_copy(update={"capabilities": SEAMLESS})
        agent.accept_welcome(runtime.register_agent(hello, channels[agent.agent_id]))
    active = await runtime.start_scene("intro")
    for agent in (left, right):
        prepare = channels[agent.agent_id].sent[-1]
        await runtime.acknowledge_command(agent.acknowledge(prepare["request_id"]))
        session = active.session_for_agent(agent.agent_id)
        assert session is not None
        agent.attach(active.scene_instance_id, session.playback_session_id)
    assert runtime.ingest(right.position(3400, duration_ms=right_duration_ms)).accepted
    assert runtime.ingest(left.position(3500)).accepted
    await _settle()
    return channels["media-left"].sent[-1], channels["media-right"].sent[-1], runtime


def test_followers_ending_with_the_clock_media_cut_by_themselves(tmp_path: Path) -> None:
    async def scenario() -> None:
        left, right, runtime = await _queue_after_follower_reports(tmp_path, right_duration_ms=4020)
        assert (left["command"], left["auto_advance"]) == ("queue", True)
        assert (right["command"], right["auto_advance"]) == ("queue", True)
        await runtime.shutdown()

    asyncio.run(scenario())


def test_followers_with_shorter_media_wait_for_advance(tmp_path: Path) -> None:
    async def scenario() -> None:
        left, right, runtime = await _queue_after_follower_reports(tmp_path, right_duration_ms=3000)
        assert left["auto_advance"] is True
        assert right["auto_advance"] is False
        await runtime.shutdown()

    asyncio.run(scenario())


def test_followers_never_cut_by_themselves_when_the_clock_loops(tmp_path: Path) -> None:
    async def scenario() -> None:
        left, right, runtime = await _queue_after_follower_reports(
            tmp_path, right_duration_ms=4000, left_end="loop"
        )
        assert left["auto_advance"] is False
        assert right["auto_advance"] is False
        await runtime.shutdown()

    asyncio.run(scenario())


def _two_projector_package(tmp_path: Path, *, left_end: str = "hold_last_frame") -> Path:
    root = tmp_path / f"two-projectors-{left_end}"
    scenes = root / "scenes"
    scenes.mkdir(parents=True)
    installation = {
        "schema_version": 1,
        "resources": [
            {"resource": "projector.left", "kind": "media_agent", "target": "media-left"},
            {"resource": "projector.right", "kind": "media_agent", "target": "media-right"},
            {"resource": "roofline", "kind": "pixel_output", "target": "memory", "pixel_count": 8},
        ],
    }

    def scene(scene_id: str, completion: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": 2,
            "id": scene_id,
            "name": scene_id,
            "mode": "media_synchronized",
            "resources": ["projector.left", "projector.right", "roofline"],
            "duration_ms": 4000,
            "clock_source": "left",
            "completion": completion,
            "media": [
                {
                    "id": "left",
                    "resource": "projector.left",
                    "asset": f"{scene_id}-left.mp4",
                    "end_behavior": left_end,
                },
                {"id": "right", "resource": "projector.right", "asset": f"{scene_id}-right.mp4"},
            ],
            "lighting": [{"id": "roofline", "resource": "roofline", "effect": "synthetic_sweep"}],
        }

    (root / "installation.json").write_text(json.dumps(installation), encoding="utf-8")
    (scenes / "intro.json").write_text(
        json.dumps(scene("intro", {"policy": "transition_to", "target_scene_id": "main"})),
        encoding="utf-8",
    )
    (scenes / "main.json").write_text(
        json.dumps(scene("main", {"policy": "hold"})), encoding="utf-8"
    )
    return root


def test_stop_discards_the_planned_scene(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        agent, channel = _connect(runtime)
        await _start_playing(runtime, agent, channel, "intro")
        assert runtime.ingest(agent.position(3500)).accepted
        await _settle()
        assert runtime.status()["next_scene"] is not None
        await runtime.stop(continue_queue=False)
        assert channel.names()[-1] == "stop"
        assert runtime.status()["next_scene"] is None
        await runtime.shutdown()

    asyncio.run(scenario())


def _package(tmp_path: Path, *, repeat: int | None = 3, policy: str = "transition_to") -> Path:
    root = tmp_path / f"package-{policy}-{repeat}"
    scenes = root / "scenes"
    scenes.mkdir(parents=True)
    installation = {
        "schema_version": 1,
        "resources": [
            {"resource": "projector.primary", "kind": "media_agent", "target": "media-primary"},
            {"resource": "roofline", "kind": "pixel_output", "target": "memory", "pixel_count": 8},
        ],
    }

    def scene(scene_id: str, asset: str, completion: dict[str, object], **media: object) -> dict:
        return {
            "schema_version": 2,
            "id": scene_id,
            "name": scene_id,
            "mode": "media_synchronized",
            "resources": ["projector.primary", "roofline"],
            "duration_ms": 4000,
            "clock_source": "primary",
            "completion": completion,
            "media": [{"id": "primary", "resource": "projector.primary", "asset": asset, **media}],
            "lighting": [{"id": "roofline", "resource": "roofline", "effect": "synthetic_sweep"}],
        }

    intro_completion: dict[str, object] = (
        {"policy": "transition_to", "target_scene_id": "main"}
        if policy == "transition_to"
        else {"policy": policy}
    )
    main_media = {"repeat_count": repeat} if repeat is not None else {}
    (root / "installation.json").write_text(json.dumps(installation), encoding="utf-8")
    (scenes / "intro.json").write_text(
        json.dumps(scene("intro", "intro.mp4", intro_completion)), encoding="utf-8"
    )
    (scenes / "main.json").write_text(
        json.dumps(scene("main", "main.mp4", {"policy": "hold"}, **main_media)),
        encoding="utf-8",
    )
    return root


def test_device_commands_validate_queue_and_advance_fields() -> None:
    def command(**fields: object) -> DeviceMediaCommand:
        return DeviceMediaCommand.model_validate(
            {
                "request_id": str(uuid4()),
                "device_session_id": str(uuid4()),
                "controller_epoch": str(uuid4()),
                "scene_id": "main",
                "scene_instance_id": str(uuid4()),
                "playback_session_id": str(uuid4()),
                **fields,
            }
        )

    queued = command(
        command="queue",
        asset_id="main.mp4",
        end_behavior="stop",
        repeat_count=2,
        auto_advance=True,
    )
    assert queued.auto_advance is True
    assert command(command="advance").asset_id is None
    for invalid in (
        {"command": "queue", "asset_id": "a.mp4", "end_behavior": "stop"},
        {"command": "prepare", "asset_id": "a.mp4", "end_behavior": "loop", "repeat_count": 2},
        {"command": "advance", "auto_advance": True},
        {"command": "play", "asset_id": "a.mp4"},
        {"command": "play", "repeat_count": 2},
    ):
        with pytest.raises(ValidationError):
            command(**invalid)


def test_device_media_features_become_runtime_capabilities() -> None:
    capabilities = DeviceCapabilityDocument(
        protocol=ProtocolRange(minimum=2, maximum=2),
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        software_version="0.1.0",
        capabilities=(MediaPlaybackCapability(codecs=("h264",), containers=("mp4",)),),
    )
    legacy = DeviceHello(device_id=uuid4(), device_session_id=uuid4(), capabilities=capabilities)
    assert _runtime_capabilities(legacy) == ("media_playback",)
    current = legacy.model_copy(update={"media_features": ("seamless_transition", "repeat_count")})
    assert _runtime_capabilities(current) == (
        "media_playback",
        MEDIA_SEAMLESS_CAPABILITY,
        MEDIA_REPEAT_CAPABILITY,
    )


def test_repeat_count_cannot_be_combined_with_loop() -> None:
    with pytest.raises(ValidationError, match="repeat_count cannot be combined"):
        MediaOutputDefinition(id="main", resource="projector", end_behavior="loop", repeat_count=2)
    assert MediaOutputDefinition(id="main", resource="projector", repeat_count=3).repeat_count == 3


def test_messages_omit_new_fields_for_agents_and_controllers_that_predate_them() -> None:
    play = DeviceMediaCommand(
        request_id=uuid4(),
        device_session_id=uuid4(),
        controller_epoch=uuid4(),
        command="play",
        scene_id="main",
        scene_instance_id=uuid4(),
        playback_session_id=uuid4(),
    ).model_dump(mode="json")
    assert "repeat_count" not in play
    assert "auto_advance" not in play
    capabilities = DeviceCapabilityDocument(
        protocol=ProtocolRange(minimum=2, maximum=2),
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        software_version="0.1.0",
        capabilities=(MediaPlaybackCapability(codecs=("h264",), containers=("mp4",)),),
    )
    hello = DeviceHello(device_id=uuid4(), device_session_id=uuid4(), capabilities=capabilities)
    assert "media_features" not in hello.model_dump(mode="json")
    plain = MediaOutputDefinition(id="main", resource="projector")
    assert "repeat_count" not in plain.model_dump(mode="json")
