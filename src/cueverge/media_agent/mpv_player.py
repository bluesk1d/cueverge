import asyncio
import os
import struct
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Literal, cast

from cueverge.media_agent.client import PlayerError, PlayerStatus
from cueverge.media_agent.mpv import MpvClient, MpvError, resolve_local_asset
from cueverge.protocol.device_v2 import MediaFeature
from cueverge.protocol.models import PlaybackState

EndBehavior = Literal["stop", "hold_last_frame", "loop"]

# mpv loses a little time every time looping or repeating media wraps to its start, so
# the player keeps such media on its own wall clock by nudging playback speed.
PACE_START_MS = 30
PACE_STOP_MS = 10
PACE_HORIZON_MS = 1000
PACE_MAX_ADJUSTMENT = 0.04
PACE_SEEK_THRESHOLD_MS = 500
# Positions arrive in whole video frames, so the pacing error is smoothed and the speed
# moves in coarse steps rather than chasing every sample.
PACE_SMOOTHING = 0.1
PACE_SPEED_STEP = 0.005
# Audio priming or padding can run past the last video frame; a loop ends on a whole
# frame unless the file runs on by more than this fraction of a frame.
LOOP_FRAME_TOLERANCE = 0.25
# Prepare and advance acknowledge only once the file is open and its first frame decoded;
# the controller gives up on a command after 10 s.
READY_TIMEOUT_SECONDS = 8.0
READY_POLL_SECONDS = 0.02
# Opening an audio output can take about a second on some Windows drivers, and mpv closes
# it for any file without audio and on stop. Once the player has played sound it keeps one
# output open for good: files without audio play with a silent WAV of the same length as
# their audio track, and stop parks the player on a silent WAV in its black window instead
# of unloading.
SILENCE_SAMPLE_RATE = 48_000
SILENCE_CHANNELS = 2
STANDBY_SECONDS = 60


@dataclass
class _PlaylistItem:
    end_behavior: EndBehavior
    repeat_count: int | None
    play_index: int = 0
    last_raw_position_ms: int = 0
    blanked: bool = False
    loop_ms: int | None = None
    path: str | None = None
    has_audio: bool | None = None
    silence: Path | None = None


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
    monotonic: Callable[[], float] = field(default=time.monotonic, repr=False)
    # (monotonic seconds, position ms) the current playback is held to; set on first sample.
    _pace_anchor: tuple[float, int] | None = field(default=None, repr=False)
    _pace_error_ms: float | None = field(default=None, repr=False)
    _speed: float = field(default=1.0, repr=False)
    silence_root: Path = field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "cueverge-silence", repr=False
    )
    _audio_open: bool = field(default=False, repr=False)
    _media_info: dict[Path, "Mp4Audio"] = field(default_factory=dict, repr=False)

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
        item = await self._item(asset, end_behavior, repeat_count)
        await self._command("playlist-clear")
        await self._command("set_property", "loop-file", _loop_file(end_behavior, repeat_count))
        await self._command(
            "set_property",
            "keep-open",
            "always" if end_behavior == "hold_last_frame" else "no",
        )
        # Gapless audio keeps the audio output open from one file to the next.
        await self._command("set_property", "gapless-audio", "yes")
        await self._command("set_property", "vid", "auto")
        if item.silence is not None:
            await self._named(
                "loadfile",
                url=str(asset),
                flags="replace",
                options=f"audio-files={_quoted(str(item.silence))}",
            )
        else:
            await self._command("loadfile", str(asset), "replace")
        await self._command("set_property", "pause", True)
        await self._reset_pace()
        duration = await self._wait_until_ready(item)
        if end_behavior == "loop":
            await self._set_loop_range(item, duration)
        else:
            await self._command("set_property", "ab-loop-a", "no")
            await self._command("set_property", "ab-loop-b", "no")
        self._make_current(item)
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
        item = await self._item(asset, end_behavior, repeat_count)
        loop_file = "inf" if end_behavior == "loop" else _loop_file(end_behavior, repeat_count)
        options = ",".join(
            (
                f"loop-file={loop_file}",
                f"keep-open={'always' if end_behavior == 'hold_last_frame' else 'no'}",
                "gapless-audio=yes",
                "ab-loop-a=no",
                "ab-loop-b=no",
                "vid=auto",
                *(
                    (f"audio-files={_quoted(str(item.silence))}",)
                    if item.silence is not None
                    else ()
                ),
            )
        )
        await self._command("playlist-clear")
        await self._named("loadfile", url=str(asset), flags="append", options=options)
        await self._command("set_property", "prefetch-playlist", "yes")
        # Auto-advance lets mpv continue gaplessly at end of file; otherwise hold the end
        # until the controller sends advance.
        await self._command("set_property", "keep-open", "no" if auto_advance else "always")
        self._queued = item
        self._queued_auto_advance = auto_advance

    async def advance(self) -> None:
        """Switch to the queued file and hold its first frame until play."""
        if self._queued is None:
            raise PlayerError("no media is queued to advance to")
        await self._command("set_property", "pause", True)
        await self._command("playlist-next", "force")
        await self._promote_queued()
        assert self._current is not None
        await self._wait_until_ready(self._current)

    async def _promote_queued(self) -> None:
        assert self._queued is not None
        self._make_current(self._queued)
        self._queued = None
        self.entry += 1
        self._last_position_ms = 0
        self._last_duration_ms = None
        await self._reset_pace()
        try:
            await self._command("playlist-remove", 0)
        except PlayerError:
            self._pending_advance_cleanup = True

    async def _item(
        self, asset: Path, end_behavior: EndBehavior, repeat_count: int | None
    ) -> _PlaylistItem:
        info = self._media_info.get(asset)
        if info is None:
            info = self._media_info[asset] = await asyncio.to_thread(read_mp4_audio, asset)
        silence = None
        if info.has_audio is False and info.duration_seconds and self._audio_open:
            silence = await asyncio.to_thread(silent_wav, self.silence_root, info.duration_seconds)
        return _PlaylistItem(
            end_behavior,
            repeat_count,
            path=str(asset),
            has_audio=info.has_audio,
            silence=silence,
        )

    def _make_current(self, item: _PlaylistItem) -> None:
        self._current = item
        if item.has_audio is not False:
            self._audio_open = True

    async def _set_loop_range(self, item: _PlaylistItem, duration: float) -> None:
        """Loop between the first and last whole video frames of the file."""
        try:
            fps = await self._property("container-fps")
        except PlayerError:
            fps = None
        end = _loop_end(duration, float(fps) if isinstance(fps, int | float) else None)
        await self._command("set_property", "ab-loop-a", 0)
        await self._command("set_property", "ab-loop-b", end)
        item.loop_ms = round(end * 1000)

    async def _wait_until_ready(self, item: _PlaylistItem) -> float:
        """Wait until mpv has opened the file and decoded its first frame; return its duration.

        Acknowledging before then lets the controller start every device while a slower one
        is still opening its file, so that device starts late by its load time.
        """
        deadline = self.monotonic() + READY_TIMEOUT_SECONDS
        while True:
            duration = await self._ready_duration(item)
            if duration is not None:
                return duration
            if self.monotonic() >= deadline:
                raise PlayerError(
                    f"mpv did not finish loading the media within {READY_TIMEOUT_SECONDS:g} s"
                )
            await asyncio.sleep(READY_POLL_SECONDS)

    async def _ready_duration(self, item: _PlaylistItem) -> float | None:
        try:
            path = await self._property("path")
            duration = await self._property("duration")
            position = await self._property("time-pos")
            seeking = await self._property("seeking")
            video = await self._property("vid")
        except PlayerError:
            return None
        if (
            (item.path is not None and path != item.path)
            or not isinstance(duration, int | float)
            or duration <= 0
            or not isinstance(position, int | float)
            or seeking is True
        ):
            return None
        if video not in (False, "no", None):
            try:
                width = await self._property("video-params/w")
            except PlayerError:
                return None
            if not isinstance(width, int) or width <= 0:
                return None
        return float(duration)

    async def play(self) -> None:
        self._pace_anchor = None
        await self._command("set_property", "pause", False)

    async def pause(self) -> None:
        await self._command("set_property", "pause", True)
        await self._reset_pace()

    async def resume(self) -> None:
        await self.play()

    async def seek(self, position_ms: int) -> None:
        self._pace_anchor = None
        await self._seek(position_ms)

    async def _seek(self, position_ms: int) -> None:
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
        if self._audio_open:
            await self._standby()
        else:
            await self._command("stop")
        self._current = None
        self._queued = None
        await self._reset_pace()

    async def _standby(self) -> None:
        """Play silence in the black window, so the audio output stays open for the next file."""
        standby = await asyncio.to_thread(silent_wav, self.silence_root, STANDBY_SECONDS)
        await self._command("playlist-clear")
        for name, value in (("ab-loop-a", "no"), ("ab-loop-b", "no")):
            await self._command("set_property", name, value)
        await self._named("loadfile", url=str(standby), flags="replace", options="loop-file=inf")
        await self._command("set_property", "pause", False)

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
        if item is not None and item.end_behavior == "loop":
            if item.loop_ms is None and isinstance(duration, int | float) and duration > 0:
                # Looping media continued into from the queue starts on mpv's own loop.
                await self._set_loop_range(item, float(duration))
            if item.loop_ms is not None:
                single_ms = item.loop_ms
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
                if (
                    item.end_behavior == "stop"
                    and self._queued is not None
                    and not self._queued_auto_advance
                    and not item.blanked
                ):
                    # A held end stands in for "stop" while the next scene waits to advance.
                    await self._command("set_property", "vid", "no")
                    item.blanked = True
        if state is PlaybackState.PLAYING and item is not None and single_ms:
            await self._keep_pace(item, position_ms, single_ms, duration_ms)
        self._last_position_ms = position_ms
        self._last_duration_ms = duration_ms
        return PlayerStatus(
            position_ms=position_ms,
            playback_rate=float(rate),
            state=state,
            duration_ms=duration_ms,
            entry=self.entry,
        )

    async def _keep_pace(
        self,
        item: _PlaylistItem,
        position_ms: int,
        single_ms: int,
        duration_ms: int | None,
    ) -> None:
        """Hold looping or repeating media to wall-clock time despite the time lost at wraps."""
        looping = item.end_behavior == "loop"
        if not looping and (item.repeat_count or 0) <= 1:
            return
        now = self.monotonic()
        if self._pace_anchor is None:
            self._pace_anchor = (now, position_ms)
            self._pace_error_ms = None
            return
        anchor_at, anchor_ms = self._pace_anchor
        expected_ms = anchor_ms + (now - anchor_at) * 1000
        if not looping and duration_ms is not None and expected_ms >= duration_ms:
            return
        sample_ms = expected_ms - position_ms
        if looping:
            sample_ms = (sample_ms + single_ms / 2) % single_ms - single_ms / 2
        previous = self._pace_error_ms
        error_ms = (
            sample_ms if previous is None else previous + PACE_SMOOTHING * (sample_ms - previous)
        )
        self._pace_error_ms = error_ms
        if abs(error_ms) > PACE_SEEK_THRESHOLD_MS:
            target_ms = round(expected_ms % single_ms if looping else expected_ms)
            await self._set_speed(1.0)
            await self._seek(target_ms)
            self._pace_error_ms = None
            return
        speed = 1.0
        threshold = PACE_STOP_MS if self._speed != 1.0 else PACE_START_MS
        if abs(error_ms) > threshold:
            adjustment = max(
                -PACE_MAX_ADJUSTMENT,
                min(PACE_MAX_ADJUSTMENT, error_ms / PACE_HORIZON_MS),
            )
            speed = round(1.0 + round(adjustment / PACE_SPEED_STEP) * PACE_SPEED_STEP, 3)
        await self._set_speed(speed)

    async def _set_speed(self, speed: float) -> None:
        if speed != self._speed:
            await self._command("set_property", "speed", speed)
            self._speed = speed

    async def _reset_pace(self) -> None:
        self._pace_anchor = None
        self._pace_error_ms = None
        await self._set_speed(1.0)

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


def _loop_end(duration: float, fps: float | None) -> float:
    if fps is None or fps <= 0:
        return duration
    frames = duration * fps
    whole = round(frames)
    if abs(frames - whole) > LOOP_FRAME_TOLERANCE:
        whole = int(frames)
    return whole / fps if whole > 0 else duration


def _quoted(value: str) -> str:
    """An mpv option value that may contain separators, in mpv's %length% form."""
    return f"%{len(value.encode())}%{value}"


@dataclass(frozen=True)
class Mp4Audio:
    has_audio: bool | None
    """Whether the file has an audio track; None when that can't be told (not MP4)."""
    duration_seconds: float | None


def read_mp4_audio(path: Path) -> Mp4Audio:
    """Read whether an MP4/MOV file has an audio track, and its length, from its header."""
    try:
        with path.open("rb") as file:
            end = file.seek(0, 2)
            moov = _child_box(file, 0, end, b"moov")
            if moov is None:
                return Mp4Audio(None, None)
            duration = _movie_duration(file, *moov)
            tracks = 0
            for kind, start, stop in _boxes(file, *moov):
                if kind != b"trak":
                    continue
                tracks += 1
                media = _child_box(file, start, stop, b"mdia")
                handler = _child_box(file, *media, b"hdlr") if media is not None else None
                if handler is None:
                    continue
                file.seek(handler[0] + 8)
                if file.read(4) == b"soun":
                    return Mp4Audio(True, duration)
            return Mp4Audio(False if tracks else None, duration)
    except OSError:
        return Mp4Audio(None, None)


def _movie_duration(file: BinaryIO, start: int, end: int) -> float | None:
    header = _child_box(file, start, end, b"mvhd")
    if header is None:
        return None
    file.seek(header[0])
    version = file.read(4)[:1]
    if version == b"\x01":
        fields = file.read(28)
        timescale, duration = (
            int.from_bytes(fields[16:20], "big"),
            int.from_bytes(fields[20:28], "big"),
        )
    else:
        fields = file.read(16)
        timescale, duration = (
            int.from_bytes(fields[8:12], "big"),
            int.from_bytes(fields[12:16], "big"),
        )
    return duration / timescale if timescale and duration else None


def silent_wav(root: Path, seconds: float) -> Path:
    """A silent 48 kHz stereo WAV of the given length, written once and reused."""
    frames = max(1, round(seconds * SILENCE_SAMPLE_RATE))
    path = root / f"silence-{frames}.wav"
    if path.is_file():
        return path
    root.mkdir(parents=True, exist_ok=True)
    data_bytes = frames * SILENCE_CHANNELS * 2
    header = (
        b"RIFF"
        + (36 + data_bytes).to_bytes(4, "little")
        + b"WAVEfmt "
        + struct.pack(
            "<IHHIIHH",
            16,
            1,
            SILENCE_CHANNELS,
            SILENCE_SAMPLE_RATE,
            SILENCE_SAMPLE_RATE * SILENCE_CHANNELS * 2,
            SILENCE_CHANNELS * 2,
            16,
        )
        + b"data"
        + data_bytes.to_bytes(4, "little")
    )
    partial = path.with_name(f".{path.name}.{os.getpid()}.partial")
    with partial.open("wb") as file:
        file.write(header)
        file.truncate(len(header) + data_bytes)
    partial.replace(path)
    return path


def _boxes(file: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """(type, payload start, box end) for each box between two file offsets."""
    position = start
    while position + 8 <= end:
        file.seek(position)
        header = file.read(8)
        if len(header) < 8:
            return
        size = int.from_bytes(header[:4], "big")
        payload = position + 8
        if size == 1:
            large = file.read(8)
            if len(large) < 8:
                return
            size = int.from_bytes(large, "big")
            payload += 8
        elif size == 0:
            size = end - position
        if size < payload - position or position + size > end:
            return
        yield header[4:8], payload, position + size
        position += size


def _child_box(file: BinaryIO, start: int, end: int, kind: bytes) -> tuple[int, int] | None:
    for found, payload, stop in _boxes(file, start, end):
        if found == kind:
            return payload, stop
    return None


def _loop_file(end_behavior: EndBehavior, repeat_count: int | None) -> str:
    if end_behavior == "loop" or repeat_count is None or repeat_count <= 1:
        return "no"
    return str(repeat_count - 1)
