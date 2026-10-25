import logging
import time
from dataclasses import dataclass
from typing import Literal, Protocol

from cueverge.config import MediaAgentConfig
from cueverge.protocol.device_v2 import MediaFeature
from cueverge.protocol.models import (
    AgentCommand,
    CommandAck,
    MediaPosition,
    PlaybackState,
)

LOGGER = logging.getLogger(__name__)


class PlayerError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlayerStatus:
    position_ms: int
    playback_rate: float
    state: PlaybackState
    duration_ms: int | None
    entry: int = 0
    """Increments whenever the player starts a different file (prepare or advance)."""


EndBehavior = Literal["stop", "hold_last_frame", "loop"]


class Player(Protocol):
    async def prepare(
        self,
        asset_id: str | None,
        end_behavior: EndBehavior,
        repeat_count: int | None = None,
    ) -> None: ...

    async def queue(
        self,
        asset_id: str | None,
        end_behavior: EndBehavior,
        repeat_count: int | None,
        auto_advance: bool,
    ) -> None: ...

    async def advance(self) -> None: ...

    async def play(self) -> None: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def seek(self, position_ms: int) -> None: ...

    async def stop(self) -> None: ...

    async def status(self) -> PlayerStatus: ...


class MediaAgentClient:
    runtime_identity = "authenticated-device"

    def __init__(self, config: MediaAgentConfig, player: Player) -> None:
        self.config = config
        self.player = player
        self.agent_session_id = ""
        self.controller_epoch: str | None = None
        self.scene_instance_id: str | None = None
        self.playback_session_id: str | None = None
        self.sequence = 0
        self._queued_session: tuple[str, str] | None = None
        self._entry = 0
        self._last_duration_ms: int | None = None

    @property
    def media_features(self) -> tuple[MediaFeature, ...]:
        return tuple(getattr(self.player, "media_features", ()))

    async def handle_command(self, command: AgentCommand) -> CommandAck:
        LOGGER.info("handling command %s request_id=%s", command.command, command.request_id)
        rejection = self._command_rejection(command)
        if rejection is not None:
            LOGGER.warning("rejected command %s: %s", command.request_id, rejection)
            return self._ack(command, "error", rejection, rejection)
        try:
            if command.command == "prepare":
                await self.player.prepare(
                    command.asset_id,
                    command.end_behavior or "hold_last_frame",
                    command.repeat_count,
                )
                self.scene_instance_id = command.scene_instance_id
                self.playback_session_id = command.playback_session_id
                self.sequence = 0
                self._queued_session = None
                self._entry = getattr(self.player, "entry", 0)
            elif command.command == "queue":
                if self.playback_session_id is None:
                    raise PlayerError("nothing is playing to queue media behind")
                await self.player.queue(
                    command.asset_id,
                    command.end_behavior or "hold_last_frame",
                    command.repeat_count,
                    bool(command.auto_advance),
                )
                self._queued_session = (command.scene_instance_id, command.playback_session_id)
            elif command.command == "advance":
                target = (command.scene_instance_id, command.playback_session_id)
                if target == (self.scene_instance_id, self.playback_session_id):
                    pass  # Already continued into this file at the end of the previous one.
                elif self._queued_session != target:
                    raise PlayerError("advance does not match the queued playback session")
                else:
                    await self.player.advance()
                    self._switch_to_queued()
            elif command.command == "play":
                await self.player.play()
            elif command.command == "resume":
                await self.player.resume()
            elif command.command == "pause":
                await self.player.pause()
            elif command.command == "seek":
                if command.target_position_ms is None:
                    raise PlayerError("seek command requires target_position_ms")
                await self.player.seek(command.target_position_ms)
            elif command.command == "stop":
                await self.player.stop()
                self._clear_playback()
            elif command.command == "get_status":
                await self.player.status()
        except (PlayerError, OSError, ValueError) as error:
            LOGGER.error("player command %s failed: %s", command.command, error)
            return self._ack(command, "error", "player_error", str(error))
        LOGGER.info("completed command %s request_id=%s", command.command, command.request_id)
        return self._ack(command, "completed")

    async def telemetry(self) -> MediaPosition | None:
        if (
            self.controller_epoch is None
            or self.scene_instance_id is None
            or self.playback_session_id is None
        ):
            return None
        status = await self.player.status()
        if self._queued_session is not None and status.entry != self._entry:
            # The player continued into the queued file on its own: report the finished
            # session once, then follow the new one.
            completed = self._message(
                self._last_duration_ms or 0,
                PlaybackState.COMPLETED,
                self._last_duration_ms,
                1.0,
            )
            self._switch_to_queued()
            return completed
        self._last_duration_ms = status.duration_ms
        return self._message(
            status.position_ms, status.state, status.duration_ms, status.playback_rate
        )

    def _message(
        self,
        position_ms: int,
        state: PlaybackState,
        duration_ms: int | None,
        playback_rate: float,
    ) -> MediaPosition:
        assert self.controller_epoch is not None
        assert self.scene_instance_id is not None
        assert self.playback_session_id is not None
        self.sequence += 1
        return MediaPosition(
            agent_id=self.runtime_identity,
            agent_session_id=self.agent_session_id,
            controller_epoch=self.controller_epoch,
            scene_instance_id=self.scene_instance_id,
            playback_session_id=self.playback_session_id,
            sequence=self.sequence,
            position_ms=position_ms,
            playback_rate=playback_rate,
            state=state,
            duration_ms=duration_ms,
            agent_monotonic_ms=round(time.monotonic() * 1000),
        )

    def _switch_to_queued(self) -> None:
        assert self._queued_session is not None
        self.scene_instance_id, self.playback_session_id = self._queued_session
        self._queued_session = None
        self.sequence = 0
        self._entry = getattr(self.player, "entry", self._entry + 1)
        self._last_duration_ms = None

    def _command_rejection(self, command: AgentCommand) -> str | None:
        if command.agent_id != self.runtime_identity:
            return "wrong_agent"
        if command.agent_session_id != self.agent_session_id:
            return "agent_session_fence"
        if command.controller_epoch != self.controller_epoch:
            return "controller_epoch_fence"
        return None

    def _ack(
        self,
        command: AgentCommand,
        status: Literal["completed", "error"],
        error_code: str | None = None,
        detail: str | None = None,
    ) -> CommandAck:
        return CommandAck(
            request_id=command.request_id,
            agent_id=self.runtime_identity,
            agent_session_id=self.agent_session_id,
            controller_epoch=command.controller_epoch,
            status=status,
            error_code=error_code,
            detail=detail,
        )

    def _clear_playback(self) -> None:
        self.scene_instance_id = None
        self.playback_session_id = None
        self.sequence = 0
        self._queued_session = None
