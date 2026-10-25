import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from cueverge.media_agent.client import PlayerError, PlayerStatus
from cueverge.media_agent.mpv import MpvClient, MpvError, resolve_local_asset
from cueverge.protocol.device_v2 import MediaFeature
from cueverge.protocol.models import PlaybackState

EndBehavior = Literal["stop", "hold_last_frame", "loop"]


@dataclass
class _PlaylistItem:
    end_behavior: EndBehavior
    repeat_count: int | None
    play_index: int = 0
    last_raw_position_ms: int = 0
    blanked: bool = False


@dataclass
class MpvPlayer:
    client: MpvClient
    asset_root: Path
    media_features: tuple[MediaFeature, ...] = ("seamless_transition", "repeat_count")
    entry: int = 0
    _last_position_ms: int = 0
    _last_duration_ms: int | None = None
    _current: _PlaylistItem | None = None
    _queued: _PlaylistItem | None = None
    _queued_auto_advance: bool = False
    _pending_advance_cleanup: bool = field(default=False, repr=False)

    async def prepare(
        self,
        asset_id: str | None,
        end_behavior: EndBehavior,
        repeat_count: int | None = None,
    ) -> None:
        if asset_id is None:
            raise PlayerError("media-backed scene did not provide an asset")
        try:
            asset = resolve_local_asset(self.asset_root, asset_id)
        except MpvError as error:
            raise PlayerError(str(error)) from error
        await self._command("playlist-clear")
        await self._command("set_property", "loop-file", _loop_file(end_behavior, repeat_count))
        await self._command(
            "set_property",
            "keep-open",
            "always" if end_behavior == "hold_last_frame" else "no",
        )
        await self._command(
            "set_property",
            "gapless-audio",
            "yes" if end_behavior == "loop" else "no",
        )
        await self._command("set_property", "vid", "auto")
        await self._command("loadfile", str(asset), "replace")
        await self._command("set_property", "pause", True)
        if end_behavior == "loop":
            duration = await self._wait_for_duration()
            await self._command("set_property", "ab-loop-a", 0)
            await self._command("set_property", "ab-loop-b", duration)
        else:
            await self._command("set_property", "ab-loop-a", "no")
            await self._command("set_property", "ab-loop-b", "no")
        self._current = _PlaylistItem(end_behavior, repeat_count)
        self._queued = None
        self.entry += 1

    async def queue(
        self,
        asset_id: str | None,
        end_behavior: EndBehavior,
        repeat_count: int | None,
        auto_advance: bool,
    ) -> None:
        """Preload the next file behind the current one without showing it."""
        if asset_id is None:
            raise PlayerError("queued media did not provide an asset")
        if self._current is None:
            raise PlayerError("nothing is playing to queue media behind")
        if auto_advance and self._current.end_behavior == "loop":
            raise PlayerError("looping media never ends, so it cannot advance automatically")
        try:
            asset = resolve_local_asset(self.asset_root, asset_id)
        except MpvError as error:
            raise PlayerError(str(error)) from error
        loop_file = "inf" if end_behavior == "loop" else _loop_file(end_behavior, repeat_count)
        options = ",".join(
            (
                f"loop-file={loop_file}",
                f"keep-open={'always' if end_behavior == 'hold_last_frame' else 'no'}",
                f"gapless-audio={'yes' if end_behavior == 'loop' else 'no'}",
                "ab-loop-a=no",
                "ab-loop-b=no",
                "vid=auto",
            )
        )
        await self._command("playlist-clear")
        await self._named("loadfile", url=str(asset), flags="append", options=options)
        await self._command("set_property", "prefetch-playlist", "yes")
        # Auto-advance lets mpv continue gaplessly at end of file; otherwise hold the end
        # until the controller sends advance.
        await self._command("set_property", "keep-open", "no" if auto_advance else "always")
        self._queued = _PlaylistItem(end_behavior, repeat_count)
        self._queued_auto_advance = auto_advance

    async def advance(self) -> None:
        if self._queued is None:
            raise PlayerError("no media is queued to advance to")
        await self._command("playlist-next", "force")
        await self._command("set_property", "pause", False)
        await self._promote_queued()

    async def _promote_queued(self) -> None:
        assert self._queued is not None
        self._current = self._queued
        self._queued = None
        self.entry += 1
        self._last_position_ms = 0
        self._last_duration_ms = None
        try:
            await self._command("playlist-remove", 0)
        except PlayerError:
            self._pending_advance_cleanup = True

    async def _wait_for_duration(self) -> float:
        for _ in range(200):
            try:
                duration = await self._property("duration")
            except PlayerError:
                await asyncio.sleep(0.05)
                continue
            if isinstance(duration, int | float) and duration > 0:
                return float(duration)
            await asyncio.sleep(0.05)
        raise PlayerError("mpv did not report loop duration within 10 seconds")

    async def play(self) -> None:
        await self._command("set_property", "pause", False)

    async def pause(self) -> None:
        await self._command("set_property", "pause", True)

    async def resume(self) -> None:
        await self.play()

    async def seek(self, position_ms: int) -> None:
        item = self._current
        if item is not None and item.repeat_count and self._last_duration_ms:
            single = max(1, self._last_duration_ms // item.repeat_count)
            item.play_index = min(item.repeat_count - 1, position_ms // single)
            remaining = item.repeat_count - 1 - item.play_index
            await self._command("set_property", "loop-file", remaining if remaining else "no")
            position_ms -= item.play_index * single
            item.last_raw_position_ms = position_ms
        await self._command("seek", position_ms / 1000, "absolute+exact")

    async def stop(self) -> None:
        await self._command("stop")
        self._current = None
        self._queued = None

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
                entry=self.entry,
            )
        if self._pending_advance_cleanup:
            self._pending_advance_cleanup = False
            await self._command("playlist-remove", 0)
        if self._queued is not None:
            playlist_position = await self._property("playlist-pos")
            if isinstance(playlist_position, int) and playlist_position >= 1:
                await self._promote_queued()

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
        single_ms = (
            round(cast(float, duration) * 1000) if isinstance(duration, int | float) else None
        )
        raw_ms = max(0, round(position * 1000))
        item = self._current
        position_ms = raw_ms
        duration_ms = single_ms
        if item is not None and item.repeat_count:
            wrap_threshold = max(250, (single_ms or 0) // 2)
            if (
                raw_ms + wrap_threshold < item.last_raw_position_ms
                and item.play_index < item.repeat_count - 1
            ):
                item.play_index += 1
            item.last_raw_position_ms = raw_ms
            if single_ms is not None:
                position_ms = item.play_index * single_ms + raw_ms
                duration_ms = single_ms * item.repeat_count
        state = PlaybackState.PAUSED if paused else PlaybackState.PLAYING
        if item is not None and item.end_behavior != "loop":
            eof = await self._property("eof-reached")
            if eof is True:
                state = PlaybackState.COMPLETED
                if duration_ms is not None:
                    position_ms = duration_ms
                if item.end_behavior == "stop" and self._queued is not None and not item.blanked:
                    # A held end stands in for "stop" while the next scene waits to advance.
                    await self._command("set_property", "vid", "no")
                    item.blanked = True
        self._last_position_ms = position_ms
        self._last_duration_ms = duration_ms
        return PlayerStatus(
            position_ms=position_ms,
            playback_rate=float(rate),
            state=state,
            duration_ms=duration_ms,
            entry=self.entry,
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

    async def _named(self, name: str, **arguments: object) -> Any:
        try:
            return await self.client.named_command(name, **arguments)
        except MpvError as error:
            raise PlayerError(str(error)) from error


def _loop_file(end_behavior: EndBehavior, repeat_count: int | None) -> str:
    if end_behavior == "loop" or repeat_count is None or repeat_count <= 1:
        return "no"
    return str(repeat_count - 1)
