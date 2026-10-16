import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cueverge.config import MediaAgentConfig
from cueverge.media_agent.client import MediaAgentClient, PlayerError, PlayerStatus
from cueverge.protocol.models import AgentCommand, PlaybackState


@dataclass
class FakePlayer:
    prepared: str | None = None
    playing: bool = False
    position_ms: int = 1250
    status_error: bool = False
    end_behavior: str | None = None

    async def prepare(
        self,
        asset_id: str | None,
        end_behavior: Literal["stop", "hold_last_frame", "loop"],
    ) -> None:
        self.prepared = asset_id
        self.end_behavior = end_behavior

    async def play(self) -> None:
        self.playing = True

    async def pause(self) -> None:
        self.playing = False

    async def resume(self) -> None:
        self.playing = True

    async def seek(self, position_ms: int) -> None:
        self.position_ms = position_ms

    async def stop(self) -> None:
        self.playing = False

    async def status(self) -> PlayerStatus:
        if self.status_error:
            raise PlayerError("synthetic status failure")
        return PlayerStatus(
            position_ms=self.position_ms,
            playback_rate=1.0,
            state=PlaybackState.PLAYING if self.playing else PlaybackState.PAUSED,
            duration_ms=4000,
        )


def test_commands_are_fenced_acknowledged_and_emit_telemetry() -> None:
    async def scenario() -> None:
        player = FakePlayer()
        client = MediaAgentClient(_config(), player)
        client.agent_session_id = "device-session-1"
        client.controller_epoch = "controller-1"

        prepare = _command(client, "prepare", asset_id="media/demo.mp4")
        acknowledgment = await client.handle_command(prepare)
        assert acknowledgment.status == "completed"
        assert player.prepared == "media/demo.mp4"
        assert player.end_behavior == "hold_last_frame"

        play = _command(client, "play")
        assert (await client.handle_command(play)).status == "completed"
        telemetry = await client.telemetry()
        assert telemetry is not None
        assert telemetry.position_ms == 1250
        assert telemetry.state is PlaybackState.PLAYING
        assert telemetry.sequence == 1

        pause = _command(client, "pause")
        assert (await client.handle_command(pause)).status == "completed"
        assert not player.playing

        resume = _command(client, "resume")
        assert (await client.handle_command(resume)).status == "completed"
        assert player.playing

        stale = play.model_copy(update={"controller_epoch": "old"})
        rejected = await client.handle_command(stale)
        assert rejected.status == "error"
        assert rejected.error_code == "controller_epoch_fence"

    asyncio.run(scenario())


def _config() -> MediaAgentConfig:
    return MediaAgentConfig(
        asset_root=Path("C:/cueverge/assets"),
        mpv_executable=Path("C:/tools/mpv/mpv.exe"),
    )


def _command(
    client: MediaAgentClient,
    command: str,
    *,
    asset_id: str | None = None,
) -> AgentCommand:
    return AgentCommand(
        request_id=f"request-{command}",
        command=command,
        agent_id=client.runtime_identity,
        agent_session_id=client.agent_session_id,
        controller_epoch="controller-1",
        scene_id="scene",
        scene_instance_id="scene-1",
        playback_session_id="playback-1",
        asset_id=asset_id,
        end_behavior="hold_last_frame" if command == "prepare" else None,
    )
