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
    _last_position_ms: int = 0
    _last_duration_ms: int | None = None

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
        idle = await self._property("idle-active")
        if not isinstance(idle, bool):
            raise PlayerError(f"mpv returned invalid idle-active value: {idle!r}")
        if idle:
            return PlayerStatus(
                position_ms=self._last_position_ms,
                playback_rate=1.0,
                state=PlaybackState.COMPLETED,
                duration_ms=self._last_duration_ms,
            )

        position = await self._property("time-pos")
        paused = await self._property("pause")
        duration = await self._property("duration")
        rate = await self._property("speed")
        if not all(
            (
                isinstance(position, int | float),
                isinstance(paused, bool),
                isinstance(rate, int | float),
            )
        ):
            raise PlayerError(
                "mpv returned invalid playback properties: "
                f"position={position!r}, paused={paused!r}, "
                f"duration={duration!r}, rate={rate!r}"
            )
        duration_ms = (
            round(cast(float, duration) * 1000) if isinstance(duration, int | float) else None
        )
        self._last_position_ms = max(0, round(position * 1000))
        self._last_duration_ms = duration_ms
        return PlayerStatus(
            position_ms=self._last_position_ms,
            playback_rate=float(rate),
            state=PlaybackState.PAUSED if paused else PlaybackState.PLAYING,
            duration_ms=duration_ms,
        )

    async def _property(self, name: str) -> Any:
        try:
            return await self.client.command("get_property", name)
        except MpvError as error:
            raise PlayerError(f"mpv property {name} failed: {error}") from error

    async def _command(self, *items: object) -> Any:
        try:
            return await self.client.command(*items)
        except MpvError as error:
            raise PlayerError(str(error)) from error
