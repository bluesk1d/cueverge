import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.media_agent.fake import FakeMediaAgent


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


@dataclass
class ManualClock:
    now: float = 10.0

    def __call__(self) -> float:
        return self.now


def test_prepares_all_media_before_starting_playback(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        primary = FakeMediaAgent(agent_id="media-primary")
        secondary = FakeMediaAgent(agent_id="media-secondary")
        primary_channel = MemoryCommandChannel([])
        secondary_channel = MemoryCommandChannel([])
        primary.accept_welcome(runtime.register_agent(primary.hello(), primary_channel))
        secondary.accept_welcome(runtime.register_agent(secondary.hello(), secondary_channel))

        active = await runtime.start_scene("multi-media")

        assert [command["command"] for command in primary_channel.sent] == ["prepare"]
        assert [command["command"] for command in secondary_channel.sent] == ["prepare"]
        primary_session = active.session_for_agent(primary.agent_id)
        secondary_session = active.session_for_agent(secondary.agent_id)
        assert primary_session is not None
        assert secondary_session is not None
        assert active.playback_session_id == primary_session.playback_session_id
        assert primary_channel.sent[0]["playback_session_id"] == primary_session.playback_session_id
        assert (
            secondary_channel.sent[0]["playback_session_id"]
            == secondary_session.playback_session_id
        )

        primary_prepare = primary.acknowledge(primary_channel.sent[0]["request_id"])
        assert (await runtime.acknowledge_command(primary_prepare)).accepted
        assert [command["command"] for command in primary_channel.sent] == ["prepare"]

        secondary_prepare = secondary.acknowledge(secondary_channel.sent[0]["request_id"])
        assert (await runtime.acknowledge_command(secondary_prepare)).accepted
        assert [command["command"] for command in primary_channel.sent] == ["prepare", "play"]
        assert [command["command"] for command in secondary_channel.sent] == [
            "prepare",
            "play",
        ]

        primary.attach(active.scene_instance_id, primary_session.playback_session_id)
        secondary.attach(active.scene_instance_id, secondary_session.playback_session_id)
        assert runtime.ingest(secondary.position(900)).accepted
        assert runtime.status()["estimated_position_ms"] is None
        assert runtime.ingest(primary.position(1000)).accepted
        status = runtime.status()
        assert status["estimated_position_ms"] == 1000
        assert status["media_outputs"] == [
            {
                "output_id": "primary",
                "agent_id": "media-primary",
                "playback_session_id": primary_session.playback_session_id,
                "required": True,
                "start_delay_ms": 0,
                "clock_source": True,
                "connected": True,
                "prepare_failed": False,
                "last_position_ms": 1000,
                "drift_ms": 0,
                "pending_commands": 1,
            },
            {
                "output_id": "secondary",
                "agent_id": "media-secondary",
                "playback_session_id": secondary_session.playback_session_id,
                "required": True,
                "start_delay_ms": 0,
                "clock_source": False,
                "connected": True,
                "prepare_failed": False,
                "last_position_ms": 900,
                "drift_ms": -100,
                "pending_commands": 1,
            },
        ]
        assert status["lighting_outputs"] == [
            {
                "output_id": "roofline",
                "resource": "roofline",
                "required": True,
                "pixel_count": 8,
                "timing_offset_ms": 0,
                "error": None,
            }
        ]

        await runtime.stop()
        assert primary_channel.sent[-1]["command"] == "stop"
        assert secondary_channel.sent[-1]["command"] == "stop"
        await runtime.shutdown()

    asyncio.run(scenario())


def test_allows_disconnected_optional_media_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(
            catalog=load_show_package(_package(tmp_path, secondary_required=False))
        )
        primary = FakeMediaAgent(agent_id="media-primary")
        channel = MemoryCommandChannel([])
        primary.accept_welcome(runtime.register_agent(primary.hello(), channel))

        active = await runtime.start_scene("multi-media")

        assert [session.agent_id for session in active.media_sessions] == ["media-primary"]
        assert [command["command"] for command in channel.sent] == ["prepare"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_non_clock_media_disconnect_continues_degraded(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        primary = FakeMediaAgent(agent_id="media-primary")
        secondary = FakeMediaAgent(agent_id="media-secondary")
        primary_channel = MemoryCommandChannel([])
        secondary_channel = MemoryCommandChannel([])
        primary.accept_welcome(runtime.register_agent(primary.hello(), primary_channel))
        secondary.accept_welcome(runtime.register_agent(secondary.hello(), secondary_channel))
        await runtime.start_scene("multi-media")

        runtime.disconnect_agent(secondary.agent_id, secondary_channel)

        status = runtime.status()
        assert status["degraded_reason"] == "media_output_disconnected:secondary"
        assert status["render_loop_running"]
        assert status["media_outputs"][1]["connected"] is False
        await runtime.shutdown()

    asyncio.run(scenario())


def test_play_command_timeout_keeps_healthy_outputs_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = ManualClock()
        runtime = ControllerRuntime(
            catalog=load_show_package(_package(tmp_path)),
            clock=clock,
            command_timeout_ms=100,
        )
        primary = FakeMediaAgent(agent_id="media-primary")
        secondary = FakeMediaAgent(agent_id="media-secondary")
        primary_channel = MemoryCommandChannel([])
        secondary_channel = MemoryCommandChannel([])
        primary.accept_welcome(runtime.register_agent(primary.hello(), primary_channel))
        secondary.accept_welcome(runtime.register_agent(secondary.hello(), secondary_channel))
        await runtime.start_scene("multi-media")
        assert (
            await runtime.acknowledge_command(
                primary.acknowledge(primary_channel.sent[0]["request_id"])
            )
        ).accepted
        assert (
            await runtime.acknowledge_command(
                secondary.acknowledge(secondary_channel.sent[0]["request_id"])
            )
        ).accepted

        clock.now += 0.2
        status = runtime.status()

        assert status["command_timeouts"] == 2
        assert status["degraded_reason"] == "command_timeout"
        assert status["render_loop_running"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_required_prepare_failure_prevents_partial_start(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        primary = FakeMediaAgent(agent_id="media-primary")
        secondary = FakeMediaAgent(agent_id="media-secondary")
        primary_channel = MemoryCommandChannel([])
        secondary_channel = MemoryCommandChannel([])
        primary.accept_welcome(runtime.register_agent(primary.hello(), primary_channel))
        secondary.accept_welcome(runtime.register_agent(secondary.hello(), secondary_channel))
        await runtime.start_scene("multi-media")

        failed = secondary.acknowledge(
            secondary_channel.sent[0]["request_id"],
            status="error",
            error_code="asset_missing",
        )
        assert (await runtime.acknowledge_command(failed)).accepted
        assert (
            await runtime.acknowledge_command(
                primary.acknowledge(primary_channel.sent[0]["request_id"])
            )
        ).accepted

        assert [command["command"] for command in primary_channel.sent] == ["prepare"]
        assert [command["command"] for command in secondary_channel.sent] == ["prepare"]
        status = runtime.status()
        assert status["degraded_reason"] == "command_error:asset_missing"
        assert status["media_outputs"][1]["prepare_failed"] is True
        assert not status["render_loop_running"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_optional_prepare_failure_starts_remaining_media(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(
            catalog=load_show_package(_package(tmp_path, secondary_required=False))
        )
        primary = FakeMediaAgent(agent_id="media-primary")
        secondary = FakeMediaAgent(agent_id="media-secondary")
        primary_channel = MemoryCommandChannel([])
        secondary_channel = MemoryCommandChannel([])
        primary.accept_welcome(runtime.register_agent(primary.hello(), primary_channel))
        secondary.accept_welcome(runtime.register_agent(secondary.hello(), secondary_channel))
        await runtime.start_scene("multi-media")

        failed = secondary.acknowledge(
            secondary_channel.sent[0]["request_id"],
            status="error",
            error_code="asset_missing",
        )
        assert (await runtime.acknowledge_command(failed)).accepted
        assert (
            await runtime.acknowledge_command(
                primary.acknowledge(primary_channel.sent[0]["request_id"])
            )
        ).accepted

        assert [command["command"] for command in primary_channel.sent] == [
            "prepare",
            "play",
        ]
        assert [command["command"] for command in secondary_channel.sent] == ["prepare"]
        assert runtime.status()["degraded_reason"] == "command_error:asset_missing"
        assert runtime.status()["render_loop_running"]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_follower_reconciliation_does_not_interrupt_clock_source(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        primary = FakeMediaAgent(agent_id="media-primary")
        secondary = FakeMediaAgent(agent_id="media-secondary")
        primary_channel = MemoryCommandChannel([])
        secondary_channel = MemoryCommandChannel([])
        primary.accept_welcome(runtime.register_agent(primary.hello(), primary_channel))
        secondary.accept_welcome(runtime.register_agent(secondary.hello(), secondary_channel))
        active = await runtime.start_scene("multi-media")
        primary_session = active.session_for_agent(primary.agent_id)
        secondary_session = active.session_for_agent(secondary.agent_id)
        assert primary_session is not None
        assert secondary_session is not None
        primary.attach(active.scene_instance_id, primary_session.playback_session_id)
        secondary.attach(active.scene_instance_id, secondary_session.playback_session_id)
        assert runtime.ingest(primary.position(1000)).accepted
        assert runtime.ingest(secondary.position(980)).accepted

        runtime.disconnect_agent(secondary.agent_id, secondary_channel)
        secondary.reconnect()
        replacement_channel = MemoryCommandChannel([])
        matching = runtime.register_agent(
            secondary.hello(secondary.snapshot(1000)),
            replacement_channel,
        )
        secondary.accept_welcome(matching)
        assert matching.reconciliation == "continue"
        assert runtime.render_loop_running

        secondary.reconnect()
        mismatch = secondary.snapshot(1100).model_copy(
            update={"playback_session_id": "stale-follower"}
        )
        rejected = runtime.register_agent(secondary.hello(mismatch), replacement_channel)
        assert rejected.reconciliation == "stop"
        assert runtime.status()["degraded_reason"] == (
            "media_output_reconciliation_required:secondary"
        )
        assert runtime.render_loop_running
        await runtime.shutdown()

    asyncio.run(scenario())


def test_applies_media_start_delay_after_prepare_barrier(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(
            catalog=load_show_package(_package(tmp_path, secondary_start_delay_ms=10))
        )
        primary = FakeMediaAgent(agent_id="media-primary")
        secondary = FakeMediaAgent(agent_id="media-secondary")
        primary_channel = MemoryCommandChannel([])
        secondary_channel = MemoryCommandChannel([])
        primary.accept_welcome(runtime.register_agent(primary.hello(), primary_channel))
        secondary.accept_welcome(runtime.register_agent(secondary.hello(), secondary_channel))
        await runtime.start_scene("multi-media")

        assert (
            await runtime.acknowledge_command(
                primary.acknowledge(primary_channel.sent[0]["request_id"])
            )
        ).accepted
        assert (
            await runtime.acknowledge_command(
                secondary.acknowledge(secondary_channel.sent[0]["request_id"])
            )
        ).accepted

        assert [command["command"] for command in primary_channel.sent] == [
            "prepare",
            "play",
        ]
        assert [command["command"] for command in secondary_channel.sent] == ["prepare"]
        await asyncio.sleep(0.02)
        assert [command["command"] for command in secondary_channel.sent] == [
            "prepare",
            "play",
        ]
        await runtime.shutdown()

    asyncio.run(scenario())


def test_partial_stop_failure_is_reported_without_retaining_scene(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)))
        primary = FakeMediaAgent(agent_id="media-primary")
        secondary = FakeMediaAgent(agent_id="media-secondary")
        primary_channel = MemoryCommandChannel([])
        secondary_channel = MemoryCommandChannel([])
        primary.accept_welcome(runtime.register_agent(primary.hello(), primary_channel))
        secondary.accept_welcome(runtime.register_agent(secondary.hello(), secondary_channel))
        await runtime.start_scene("multi-media")

        await runtime.stop(continue_queue=False)
        primary_stop = primary_channel.sent[-1]
        secondary_stop = secondary_channel.sent[-1]
        assert primary_stop["command"] == "stop"
        assert secondary_stop["command"] == "stop"
        assert (
            await runtime.acknowledge_command(primary.acknowledge(primary_stop["request_id"]))
        ).accepted
        assert (
            await runtime.acknowledge_command(
                secondary.acknowledge(
                    secondary_stop["request_id"],
                    status="error",
                    error_code="player_stop_failed",
                )
            )
        ).accepted

        status = runtime.status()
        assert status["active_scene"] is None
        assert status["command_errors"] == 1
        assert status["degraded_reason"] == "command_error:player_stop_failed"
        await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome",
    ["success", "stop_error", "stop_timeout", "start_error", "play_error", "play_timeout"],
)
def test_catalog_handover_requires_real_stop_and_restart_acknowledgements(
    tmp_path: Path, outcome: str
) -> None:
    async def scenario():
        clock = ManualClock()
        runtime = ControllerRuntime(catalog=load_show_package(_package(tmp_path)), clock=clock)
        agents = [FakeMediaAgent(agent_id=value) for value in ("media-primary", "media-secondary")]
        channels = [MemoryCommandChannel([]), MemoryCommandChannel([])]
        for agent, channel in zip(agents, channels, strict=True):
            agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        processed = [0, 0]

        async def acknowledge(error_command=None, skip_play=False):
            for _ in range(3):
                for index, (agent, channel) in enumerate(zip(agents, channels, strict=True)):
                    while processed[index] < len(channel.sent):
                        command = channel.sent[processed[index]]
                        processed[index] += 1
                        if skip_play and index == 1 and command["command"] == "play":
                            continue
                        failure = index == 1 and command["command"] == error_command
                        ack = agent.acknowledge(
                            command["request_id"],
                            status="error" if failure else "completed",
                            error_code="original_ack_failure" if failure else None,
                        )
                        await runtime.acknowledge_command(ack)

        await runtime.start_scene("multi-media")
        await acknowledge()
        original = runtime.catalog
        identity = {
            "state": "loaded",
            "project_id": "synthetic",
            "revision": 1,
            "digest_sha256": "a" * 64,
            "loaded_at": "2026-10-23T00:00:00Z",
            "generation": "new",
        }
        task = asyncio.create_task(
            runtime.replace_catalog(original, identity=identity, start_scene_id="multi-media")
        )
        await asyncio.sleep(0.03)
        assert runtime.active_scene is None
        assert not task.done()
        if outcome == "stop_timeout":
            clock.now += runtime.command_timeout_ms / 1000 + 1
            runtime.check_timeouts()
        else:
            await acknowledge("stop" if outcome == "stop_error" else None)
        if outcome in {"stop_error", "stop_timeout"}:
            with pytest.raises(RuntimeError, match="stop"):
                await asyncio.wait_for(task, 1)
            assert runtime.loaded_catalog["state"] == "unreported"
        else:
            await asyncio.sleep(0.03)
            assert not task.done()
            await acknowledge(
                "prepare"
                if outcome == "start_error"
                else "play"
                if outcome == "play_error"
                else None,
                skip_play=outcome == "play_timeout",
            )
            if outcome == "play_timeout":
                clock.now += runtime.command_timeout_ms / 1000 + 1
                runtime.check_timeouts()
                with pytest.raises(RuntimeError, match="lost or timed out"):
                    await asyncio.wait_for(task, 1)
            elif outcome in {"start_error", "play_error"}:
                with pytest.raises(RuntimeError, match="rejected"):
                    await asyncio.wait_for(task, 1)
            else:
                active = runtime.active_scene
                assert active is not None
                for agent in agents:
                    session = active.session_for_agent(agent.agent_id)
                    assert session is not None
                    agent.attach(active.scene_instance_id, session.playback_session_id)
                    runtime.ingest(agent.position(0))
                await asyncio.wait_for(task, 1)
                assert runtime.loaded_catalog == identity
        await runtime.shutdown()

    asyncio.run(scenario())


def _package(
    tmp_path: Path,
    *,
    secondary_required: bool = True,
    secondary_start_delay_ms: int = 0,
) -> Path:
    root = tmp_path / ("required" if secondary_required else "optional")
    scenes = root / "scenes"
    scenes.mkdir(parents=True)
    installation = {
        "schema_version": 1,
        "resources": [
            {
                "resource": "projector.primary",
                "kind": "media_agent",
                "target": "media-primary",
            },
            {
                "resource": "projector.secondary",
                "kind": "media_agent",
                "target": "media-secondary",
            },
            {
                "resource": "roofline",
                "kind": "pixel_output",
                "target": "memory",
                "pixel_count": 8,
            },
        ],
    }
    scene = {
        "schema_version": 2,
        "id": "multi-media",
        "name": "Multi-media",
        "mode": "media_synchronized",
        "resources": ["projector.primary", "projector.secondary", "roofline"],
        "duration_ms": 4000,
        "clock_source": "primary",
        "media": [
            {
                "id": "primary",
                "resource": "projector.primary",
            },
            {
                "id": "secondary",
                "resource": "projector.secondary",
                "required": secondary_required,
                "start_delay_ms": secondary_start_delay_ms,
            },
        ],
        "lighting": [
            {
                "id": "roofline",
                "resource": "roofline",
                "effect": "synthetic_sweep",
            }
        ],
    }
    (root / "installation.json").write_text(json.dumps(installation), encoding="utf-8")
    (scenes / "multi-media.json").write_text(json.dumps(scene), encoding="utf-8")
    return root
