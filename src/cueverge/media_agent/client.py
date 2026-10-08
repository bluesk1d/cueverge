import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from cueverge.config import MediaAgentConfig
from cueverge.protocol.models import (
    AgentCommand,
    AgentHeartbeat,
    AgentHello,
    AgentWelcome,
    CommandAck,
    MediaPosition,
    PlaybackSnapshot,
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


class Player(Protocol):
    async def prepare(self, asset_id: str | None) -> None: ...

    async def play(self) -> None: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def seek(self, position_ms: int) -> None: ...

    async def stop(self) -> None: ...

    async def status(self) -> PlayerStatus: ...


class MediaAgentClient:
    def __init__(self, config: MediaAgentConfig, player: Player) -> None:
        self.config = config
        self.player = player
        self.agent_session_id = str(uuid4())
        self.controller_epoch: str | None = None
        self.scene_instance_id: str | None = None
        self.playback_session_id: str | None = None
        self.sequence = 0
        self.heartbeat_sequence = 0

    async def run_forever(self) -> None:
        while True:
            try:
                LOGGER.info("connecting to controller at %s", self.config.controller_url)
                async with connect(str(self.config.controller_url)) as websocket:
                    LOGGER.info("connected to controller")
                    await self._run_connection(websocket)
            except (OSError, ValidationError, PlayerError, ConnectionClosed) as error:
                LOGGER.warning("agent connection failed: %s", error)
                await asyncio.sleep(self.config.reconnect_delay_ms / 1000)
                self.agent_session_id = str(uuid4())

    async def _run_connection(self, websocket: ClientConnection) -> None:
        await websocket.send((await self.hello()).model_dump_json())
        welcome = AgentWelcome.model_validate_json(await websocket.recv())
        self.controller_epoch = welcome.controller_epoch
        LOGGER.info("controller reconciliation action: %s", welcome.reconciliation)
        if welcome.reconciliation == "stop":
            await self.player.stop()
            self._clear_playback()

        heartbeat = asyncio.create_task(
            self._heartbeat_loop(websocket, welcome.heartbeat_interval_ms),
            name="agent-heartbeat",
        )
        telemetry = asyncio.create_task(
            self._telemetry_loop(websocket),
            name="agent-telemetry",
        )
        try:
            await self._receive_commands(websocket)
        finally:
            heartbeat.cancel()
            telemetry.cancel()
            await asyncio.gather(heartbeat, telemetry, return_exceptions=True)

    async def hello(self) -> AgentHello:
        playback = None
        if (
            self.controller_epoch is not None
            and self.scene_instance_id is not None
            and self.playback_session_id is not None
        ):
            status = await self.player.status()
            playback = PlaybackSnapshot(
                controller_epoch=self.controller_epoch,
                scene_instance_id=self.scene_instance_id,
                playback_session_id=self.playback_session_id,
                sequence=self.sequence,
                position_ms=status.position_ms,
                playback_rate=status.playback_rate,
                state=status.state,
                duration_ms=status.duration_ms,
                agent_monotonic_ms=round(time.monotonic() * 1000),
            )
        return AgentHello(
            agent_id=self.config.agent_id,
            agent_session_id=self.agent_session_id,
            playback=playback,
        )

    async def handle_command(self, command: AgentCommand) -> CommandAck:
        LOGGER.info("handling command %s request_id=%s", command.command, command.request_id)
        rejection = self._command_rejection(command)
        if rejection is not None:
            LOGGER.warning("rejected command %s: %s", command.request_id, rejection)
            return self._ack(command, "error", rejection, rejection)
        try:
            if command.command == "prepare":
                await self.player.prepare(command.asset_id)
                self.scene_instance_id = command.scene_instance_id
                self.playback_session_id = command.playback_session_id
                self.sequence = 0
            elif command.command in {"play", "resume"}:
                await self.player.play() if command.command == "play" else self.player.resume()
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
        self.sequence += 1
        return MediaPosition(
            agent_id=self.config.agent_id,
            agent_session_id=self.agent_session_id,
            controller_epoch=self.controller_epoch,
            scene_instance_id=self.scene_instance_id,
            playback_session_id=self.playback_session_id,
            sequence=self.sequence,
            position_ms=status.position_ms,
            playback_rate=status.playback_rate,
            state=status.state,
            duration_ms=status.duration_ms,
            agent_monotonic_ms=round(time.monotonic() * 1000),
        )

    async def _receive_commands(self, websocket: ClientConnection) -> None:
        async for payload in websocket:
            if not isinstance(payload, str):
                raise PlayerError("controller sent a non-text WebSocket message")
            message = json.loads(payload)
            if isinstance(message, dict) and message.get("type") == "agent.command":
                command = AgentCommand.model_validate(message)
                await websocket.send((await self.handle_command(command)).model_dump_json())

    async def _heartbeat_loop(
        self,
        websocket: ClientConnection,
        interval_ms: int,
    ) -> None:
        while True:
            await asyncio.sleep(interval_ms / 1000)
            if self.controller_epoch is None:
                continue
            self.heartbeat_sequence += 1
            heartbeat = AgentHeartbeat(
                agent_id=self.config.agent_id,
                agent_session_id=self.agent_session_id,
                controller_epoch=self.controller_epoch,
                sequence=self.heartbeat_sequence,
                agent_monotonic_ms=round(time.monotonic() * 1000),
            )
            await websocket.send(heartbeat.model_dump_json())

    async def _telemetry_loop(self, websocket: ClientConnection) -> None:
        interval = 1 / self.config.telemetry_hz
        while True:
            try:
                telemetry = await self.telemetry()
                if telemetry is not None:
                    await websocket.send(telemetry.model_dump_json())
            except PlayerError as error:
                LOGGER.error("could not read player telemetry: %s", error)
            await asyncio.sleep(interval)

    def _command_rejection(self, command: AgentCommand) -> str | None:
        if command.agent_id != self.config.agent_id:
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
            agent_id=self.config.agent_id,
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
