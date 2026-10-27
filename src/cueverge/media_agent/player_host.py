"""Supervise the mpv player process so device presence can start and stop it on request."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from cueverge.media_agent.client import EndBehavior, PlayerError, PlayerStatus
from cueverge.media_agent.mpv import ConnectableMpvTransport, MpvClient, MpvError
from cueverge.media_agent.mpv_player import MpvPlayer
from cueverge.protocol.device_v2 import MediaFeature, PlayerState

LOGGER = logging.getLogger(__name__)

PLAYER_FEATURES: tuple[MediaFeature, ...] = (
    "seamless_transition",
    "repeat_count",
    "player_control",
)


class PlayerProcess(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


class PlayerHost:
    """A `Player` whose mpv process can be started and stopped while presence stays up."""

    media_features = PLAYER_FEATURES

    def __init__(
        self,
        process: PlayerProcess,
        transport_factory: Callable[[], ConnectableMpvTransport],
        asset_root: Path,
    ) -> None:
        self._process = process
        self._transport_factory = transport_factory
        self._asset_root = asset_root
        self._client: MpvClient | None = None
        self._player: MpvPlayer | None = None
        self._lock = asyncio.Lock()
        self.state: PlayerState = "stopped"
        self.failure: str | None = None

    @property
    def entry(self) -> int:
        return self._player.entry if self._player is not None else 0

    async def start(self) -> PlayerState:
        async with self._lock:
            if self.state == "ready":
                return self.state
            self.state = "starting"
            self.failure = None
            transport = self._transport_factory()
            client = MpvClient(transport)
            try:
                await asyncio.to_thread(self._process.start)
                await transport.connect()
                client.start_reader()
                await client.command("get_property", "idle-active")
            except (MpvError, OSError) as error:
                with suppress(MpvError, OSError):
                    await client.close()
                await asyncio.to_thread(self._process.stop)
                self.state = "failed"
                self.failure = str(error)
                LOGGER.error("media player failed to start: %s", error)
                return self.state
            self._client = client
            self._player = MpvPlayer(client, self._asset_root)
            self.state = "ready"
            LOGGER.info("media player started")
            return self.state

    async def stop_player(self) -> PlayerState:
        async with self._lock:
            if self.state == "stopped":
                return self.state
            self.state = "stopping"
            client, self._client, self._player = self._client, None, None
            if client is not None:
                # Quitting over IPC also closes a player adopted from an earlier agent run.
                with suppress(MpvError, OSError):
                    await client.command("quit")
                with suppress(MpvError, OSError):
                    await client.close()
            await asyncio.to_thread(self._process.stop)
            self.state = "stopped"
            self.failure = None
            LOGGER.info("media player stopped")
            return self.state

    async def prepare(
        self,
        asset_id: str | None,
        end_behavior: EndBehavior,
        repeat_count: int | None = None,
    ) -> None:
        await self._call(lambda player: player.prepare(asset_id, end_behavior, repeat_count))

    async def queue(
        self,
        asset_id: str | None,
        end_behavior: EndBehavior,
        repeat_count: int | None,
        auto_advance: bool,
    ) -> None:
        await self._call(
            lambda player: player.queue(asset_id, end_behavior, repeat_count, auto_advance)
        )

    async def advance(self) -> None:
        await self._call(lambda player: player.advance())

    async def play(self) -> None:
        await self._call(lambda player: player.play())

    async def pause(self) -> None:
        await self._call(lambda player: player.pause())

    async def resume(self) -> None:
        await self._call(lambda player: player.resume())

    async def seek(self, position_ms: int) -> None:
        await self._call(lambda player: player.seek(position_ms))

    async def stop(self) -> None:
        if self._player is not None:
            await self._call(lambda player: player.stop())

    async def status(self) -> PlayerStatus:
        player = self._require()
        try:
            return await player.status()
        except (MpvError, PlayerError) as error:
            self._mark_failed(error)
            raise PlayerError(str(error)) from error

    def _require(self) -> MpvPlayer:
        if self._player is None:
            raise PlayerError(f"media player is {self.state}")
        return self._player

    async def _call(self, operation: Callable[[MpvPlayer], Awaitable[None]]) -> None:
        player = self._require()
        try:
            await operation(player)
        except (MpvError, PlayerError) as error:
            self._mark_failed(error)
            raise PlayerError(str(error)) from error

    def _mark_failed(self, error: Exception) -> None:
        message = str(error)
        if "connection closed" in message or "not connected" in message:
            # The player window was closed or crashed: report it instead of retrying blindly.
            self.state = "failed"
            self.failure = message
            self._player = None
            self._client = None
