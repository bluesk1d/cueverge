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
    repeat_count: int | None = None
    queued: tuple[str | None, str, int | None, bool] | None = None
    entry: int = 0
    media_features: tuple[str, ...] = ("seamless_transition", "repeat_count")

    async def prepare(
        self,
        asset_id: str | None,
        end_behavior: Literal["stop", "hold_last_frame", "loop"],
        repeat_count: int | None = None,
    ) -> None:
        self.prepared = asset_id
        self.end_behavior = end_behavior
        self.repeat_count = repeat_count
        self.entry += 1

    async def queue(
        self,
        asset_id: str | None,
        end_behavior: Literal["stop", "hold_last_frame", "loop"],
        repeat_count: int | None,
        auto_advance: bool,
    ) -> None:
        self.queued = (asset_id, end_behavior, repeat_count, auto_advance)

    async def advance(self) -> None:
        if self.queued is None:
            raise PlayerError("nothing queued")
        self.prepared = self.queued[0]
        self.queued = None
        self.entry += 1
        self.position_ms = 0
        self.playing = True

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
            entry=self.entry,
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


def test_queue_then_advance_switches_sessions_on_command() -> None:
    async def scenario() -> None:
        player = FakePlayer()
        client = _ready_client(player)
        assert client.media_features == ("seamless_transition", "repeat_count")
        assert (
            await client.handle_command(
                _command(client, "prepare", asset_id="media/a.mp4", repeat_count=3)
            )
        ).status == "completed"
        assert player.repeat_count == 3
        queue = _command(
            client,
            "queue",
            asset_id="media/b.mp4",
            scene_instance_id="scene-2",
            playback_session_id="playback-2",
            auto_advance=False,
        )
        assert (await client.handle_command(queue)).status == "completed"
        assert player.queued == ("media/b.mp4", "hold_last_frame", None, False)
        wrong = _command(client, "advance", playback_session_id="playback-9")
        assert (await client.handle_command(wrong)).error_code == "player_error"
        advance = _command(
            client, "advance", scene_instance_id="scene-2", playback_session_id="playback-2"
        )
        assert (await client.handle_command(advance)).status == "completed"
        assert player.prepared == "media/b.mp4"
        telemetry = await client.telemetry()
        assert telemetry is not None
        assert (telemetry.scene_instance_id, telemetry.playback_session_id) == (
            "scene-2",
            "playback-2",
        )
        assert telemetry.sequence == 1
        assert (await client.handle_command(advance)).status == "completed"

    asyncio.run(scenario())


def test_automatic_advance_reports_completion_then_follows_queued_session() -> None:
    async def scenario() -> None:
        player = FakePlayer(playing=True, position_ms=3900)
        client = _ready_client(player)
        await client.handle_command(_command(client, "prepare", asset_id="media/a.mp4"))
        assert (await client.telemetry()) is not None
        await client.handle_command(
            _command(
                client,
                "queue",
                asset_id="media/b.mp4",
                scene_instance_id="scene-2",
                playback_session_id="playback-2",
                auto_advance=True,
            )
        )
        await player.advance()

        completed = await client.telemetry()
        assert completed is not None
        assert completed.playback_session_id == "playback-1"
        assert completed.state is PlaybackState.COMPLETED
        assert completed.position_ms == 4000
        following = await client.telemetry()
        assert following is not None
        assert following.playback_session_id == "playback-2"
        assert following.state is PlaybackState.PLAYING
        assert following.sequence == 1

    asyncio.run(scenario())


def _ready_client(player: FakePlayer) -> MediaAgentClient:
    client = MediaAgentClient(_config(), player)
    client.agent_session_id = "device-session-1"
    client.controller_epoch = "controller-1"
    return client


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
    scene_instance_id: str = "scene-1",
    playback_session_id: str = "playback-1",
    repeat_count: int | None = None,
    auto_advance: bool | None = None,
) -> AgentCommand:
    return AgentCommand(
        request_id=f"request-{command}",
        command=command,
        agent_id=client.runtime_identity,
        agent_session_id=client.agent_session_id,
        controller_epoch="controller-1",
        scene_id="scene",
        scene_instance_id=scene_instance_id,
        playback_session_id=playback_session_id,
        asset_id=asset_id,
        end_behavior="hold_last_frame" if command in {"prepare", "queue"} else None,
        repeat_count=repeat_count,
        auto_advance=auto_advance,
    )
