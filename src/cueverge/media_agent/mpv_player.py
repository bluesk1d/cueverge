from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from cueverge.media_agent.client import PlayerError, PlayerStatus
from cueverge.media_agent.mpv import MpvClient, MpvError, resolve_local_asset
from cueverge.protocol.models import PlaybackState


@dataclass
class MpvPlayer:
    client: MpvClient
    asset_root: Path

    async def prepare(self, asset_id: str | None) -> None:
        if asset_id is None:
            raise PlayerError("media-backed scene did not provide an asset")
        try:
            asset = resolve_local_asset(self.asset_root, asset_id)
        except MpvError as error:
            raise PlayerError(str(error)) from error
        await self._command("loadfile", str(asset), "replace")
        await self._command("set_property", "pause", True)

    async def play(self) -> None:
        await self._command("set_property", "pause", False)

    async def pause(self) -> None:
        await self._command("set_property", "pause", True)

    async def resume(self) -> None:
        await self.play()

    async def seek(self, position_ms: int) -> None:
        await self._command("seek", position_ms / 1000, "absolute+exact")

    async def stop(self) -> None:
        await self._command("stop")

    async def status(self) -> PlayerStatus:
        position = await self._command("get_property", "time-pos/full")
        paused = await self._command("get_property", "pause")
        duration = await self._command("get_property", "duration")
        rate = await self._command("get_property", "speed")
        idle = await self._command("get_property", "idle-active")
        if not all(
            (
                isinstance(position, int | float),
                isinstance(paused, bool),
                isinstance(rate, int | float),
                isinstance(idle, bool),
            )
        ):
            raise PlayerError("mpv returned invalid playback property types")
        duration_ms = (
            round(cast(float, duration) * 1000) if isinstance(duration, int | float) else None
        )
        state = (
            PlaybackState.COMPLETED
            if idle
            else PlaybackState.PAUSED
            if paused
            else PlaybackState.PLAYING
        )
        return PlayerStatus(
            position_ms=max(0, round(position * 1000)),
            playback_rate=float(rate),
            state=state,
            duration_ms=duration_ms,
        )

    async def _command(self, *items: object) -> Any:
        try:
            return await self.client.command(*items)
        except MpvError as error:
            raise PlayerError(str(error)) from error
