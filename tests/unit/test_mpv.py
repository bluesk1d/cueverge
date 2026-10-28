import asyncio
import json
import sys
from pathlib import Path

import pytest

from cueverge.media_agent.client import PlayerError
from cueverge.media_agent.mpv import (
    JsonLineCodec,
    MpvClient,
    MpvError,
    MpvProcess,
    UnixSocketTransport,
    create_mpv_transport,
    resolve_local_asset,
)
from cueverge.media_agent.mpv_player import MpvPlayer, _loop_end
from cueverge.protocol.models import PlaybackState


class FakeTransport:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.reads: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = False

    async def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    async def readline(self) -> bytes:
        return await self.reads.get()

    async def close(self) -> None:
        self.closed = True


class FakeMpvClient:
    def __init__(self, properties: dict[str, object]) -> None:
        self.properties = properties
        self.requests: list[str] = []

    async def command(self, *items: object) -> object:
        assert items[0] == "get_property"
        name = str(items[1])
        self.requests.append(name)
        return self.properties[name]


READY_READS = [
    ("get_property", "path"),
    ("get_property", "duration"),
    ("get_property", "time-pos"),
    ("get_property", "seeking"),
    ("get_property", "vid"),
    ("get_property", "video-params/w"),
]


class RecordingMpvClient:
    def __init__(self) -> None:
        self.commands: list[tuple[object, ...]] = []
        self.path: object = None

    async def command(self, *items: object) -> object:
        self.commands.append(items)
        if items[0] == "loadfile":
            self.path = items[1]
        if items[0] == "get_property":
            return {
                "path": self.path,
                "duration": 3.0,
                "time-pos": 0.0,
                "seeking": False,
                "vid": 1,
                "video-params/w": 1280,
            }.get(str(items[1]))
        return None


def test_json_line_codec_is_compact_and_newline_delimited() -> None:
    encoded = JsonLineCodec.encode(["get_property", "time-pos/full"], 7)

    assert encoded.endswith(b"\n")
    assert json.loads(encoded) == {
        "command": ["get_property", "time-pos/full"],
        "request_id": 7,
    }
    assert JsonLineCodec.decode(b'{"event":"property-change"}\n')["event"] == "property-change"


def test_mpv_client_correlates_responses_and_preserves_events() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        client = MpvClient(transport)
        client.start_reader()
        command = asyncio.create_task(client.command("get_property", "pause"))
        await asyncio.sleep(0)
        request = json.loads(transport.writes[0])
        await transport.reads.put(b'{"event":"property-change","name":"pause","data":false}\n')
        await transport.reads.put(
            json.dumps(
                {
                    "request_id": request["request_id"],
                    "error": "success",
                    "data": False,
                }
            ).encode()
            + b"\n"
        )

        assert await command is False
        assert (await client.next_event())["name"] == "pause"
        await client.close()
        assert transport.closed

    asyncio.run(scenario())


def test_mpv_client_bounds_command_wait() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        client = MpvClient(transport, command_timeout_seconds=0.01)
        client.start_reader()
        with pytest.raises(MpvError, match="timed out"):
            await client.command("get_property", "pause")
        await client.close()

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets are not available on Windows")
def test_unix_socket_transport_exchanges_mpv_json(tmp_path: Path) -> None:
    async def scenario() -> None:
        socket_path = tmp_path / "mpv.sock"

        async def handle(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            request = json.loads(await reader.readline())
            writer.write(
                json.dumps(
                    {
                        "request_id": request["request_id"],
                        "error": "success",
                        "data": False,
                    }
                ).encode()
                + b"\n"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle, path=socket_path)
        transport = UnixSocketTransport(str(socket_path))
        await transport.connect()
        client = MpvClient(transport)
        client.start_reader()

        assert await client.command("get_property", "pause") is False

        await client.close()
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform == "win32", reason="running on Windows")
def test_selects_unix_transport_on_non_windows_host() -> None:
    assert isinstance(create_mpv_transport("/tmp/mpv.sock"), UnixSocketTransport)


def test_builds_windows_mpv_command_without_device_assumptions() -> None:
    process = MpvProcess(
        executable=Path("C:/tools/mpv/mpv.exe"),
        pipe_path=r"\\.\pipe\cueverge-mpv",
        fullscreen_screen=1,
        audio_device="wasapi/example",
        audio_delay_seconds=0.1,
    )

    assert process.command() == [
        "C:/tools/mpv/mpv.exe",
        "--idle=yes",
        "--force-window=yes",
        "--fullscreen=yes",
        r"--input-ipc-server=\\.\pipe\cueverge-mpv",
        "--fs-screen=1",
        "--audio-device=wasapi/example",
        "--audio-delay=0.100",
    ]


def test_process_adopts_existing_cueverge_ipc_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "mpv"
    executable.write_bytes(b"")
    monkeypatch.setattr(
        "cueverge.media_agent.mpv._ipc_server_available",
        lambda _path: True,
    )

    process = MpvProcess(executable=executable, pipe_path=str(tmp_path / "mpv.sock"))
    process.start()

    assert process.process is None
    assert not process.owns_process


def test_asset_resolution_is_confined_to_cache_root(tmp_path: Path) -> None:
    asset = tmp_path / "clip.mp4"
    asset.write_bytes(b"demo")
    assert resolve_local_asset(tmp_path, "clip.mp4") == asset.resolve()

    with pytest.raises(MpvError, match="escapes"):
        resolve_local_asset(tmp_path, "../outside.mp4")
    with pytest.raises(MpvError, match="does not exist"):
        resolve_local_asset(tmp_path, "missing.mp4")


def test_player_checks_idle_before_position_and_retains_last_time() -> None:
    async def scenario() -> None:
        fake = FakeMpvClient(
            {
                "idle-active": False,
                "time-pos": 1.25,
                "pause": False,
                "duration": 4.0,
                "speed": 1.0,
            }
        )
        player = MpvPlayer(fake, Path("."))  # type: ignore[arg-type]
        playing = await player.status()
        assert playing.position_ms == 1250
        assert playing.state is PlaybackState.PLAYING
        assert fake.requests[0] == "idle-active"

        fake.properties["idle-active"] = True
        fake.requests.clear()
        completed = await player.status()
        assert completed.position_ms == 1250
        assert completed.duration_ms == 4000
        assert completed.state is PlaybackState.COMPLETED
        assert fake.requests == ["idle-active"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("end_behavior", "keep_open_value"),
    [
        ("hold_last_frame", "always"),
        ("loop", "no"),
        ("stop", "no"),
    ],
)
def test_player_configures_exact_end_behavior(
    tmp_path: Path,
    end_behavior: str,
    keep_open_value: str,
) -> None:
    async def scenario() -> None:
        asset = tmp_path / "clip.mp4"
        asset.write_bytes(b"video")
        fake = RecordingMpvClient()
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]

        await player.prepare(asset.name, end_behavior)  # type: ignore[arg-type]

        assert fake.commands == [
            ("playlist-clear",),
            ("set_property", "loop-file", "no"),
            ("set_property", "keep-open", keep_open_value),
            (
                "set_property",
                "gapless-audio",
                "yes" if end_behavior == "loop" else "no",
            ),
            ("set_property", "vid", "auto"),
            ("loadfile", str(asset.resolve()), "replace"),
            ("set_property", "pause", True),
            *READY_READS,
            *(
                [
                    ("get_property", "container-fps"),
                    ("set_property", "ab-loop-a", 0),
                    ("set_property", "ab-loop-b", 3.0),
                ]
                if end_behavior == "loop"
                else [
                    ("set_property", "ab-loop-a", "no"),
                    ("set_property", "ab-loop-b", "no"),
                ]
            ),
        ]
        assert player.entry == 1

    asyncio.run(scenario())


class ScriptedMpvClient:
    def __init__(self) -> None:
        self.commands: list[tuple[object, ...]] = []
        self.named: list[tuple[str, dict[str, object]]] = []
        self.properties: dict[str, object] = {
            "idle-active": False,
            "time-pos": 0.0,
            "pause": False,
            "duration": 4.0,
            "container-fps": 30.0,
            "speed": 1.0,
            "eof-reached": False,
            "playlist-pos": 0,
            "seeking": False,
            "vid": 1,
            "video-params/w": 1280,
            "path": None,
        }
        self.appended: object = None

    async def command(self, *items: object) -> object:
        if items[0] == "get_property":
            return self.properties[str(items[1])]
        self.commands.append(items)
        if items[0] == "loadfile":
            self.properties["path"] = items[1]
        if items == ("playlist-next", "force"):
            self.properties["path"] = self.appended
        return None

    async def named_command(self, name: str, **arguments: object) -> object:
        self.named.append((name, arguments))
        if name == "loadfile":
            self.appended = arguments["url"]
        return None


def test_player_repeats_media_with_cumulative_position_then_completes(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "clip.mp4").write_bytes(b"video")
        fake = ScriptedMpvClient()
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]
        await player.prepare("clip.mp4", "hold_last_frame", 3)
        assert ("set_property", "loop-file", "2") in fake.commands

        fake.properties["time-pos"] = 3.9
        first = await player.status()
        assert (first.position_ms, first.duration_ms) == (3900, 12000)
        fake.properties["time-pos"] = 0.1
        second = await player.status()
        assert second.position_ms == 4100
        assert second.state is PlaybackState.PLAYING

        await player.seek(9000)
        assert ("set_property", "loop-file", "no") in fake.commands
        assert ("seek", 1.0, "absolute+exact") in fake.commands
        fake.properties.update({"time-pos": 4.0, "pause": True, "eof-reached": True})
        finished = await player.status()
        assert finished.state is PlaybackState.COMPLETED
        assert finished.position_ms == 12000

    asyncio.run(scenario())


def test_player_queues_next_file_and_continues_into_it_automatically(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "a.mp4").write_bytes(b"a")
        (tmp_path / "b.mp4").write_bytes(b"b")
        fake = ScriptedMpvClient()
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]
        await player.prepare("a.mp4", "hold_last_frame")
        fake.commands.clear()

        await player.queue("b.mp4", "stop", 2, True)

        assert fake.named == [
            (
                "loadfile",
                {
                    "url": str((tmp_path / "b.mp4").resolve()),
                    "flags": "append",
                    "options": (
                        "loop-file=1,keep-open=no,gapless-audio=no,"
                        "ab-loop-a=no,ab-loop-b=no,vid=auto"
                    ),
                },
            )
        ]
        assert fake.commands == [
            ("playlist-clear",),
            ("set_property", "prefetch-playlist", "yes"),
            ("set_property", "keep-open", "no"),
        ]
        fake.properties.update({"playlist-pos": 1, "time-pos": 0.04})
        status = await player.status()
        assert status.entry == 2
        assert status.position_ms == 40
        assert status.duration_ms == 8000
        assert ("playlist-remove", 0) in fake.commands

    asyncio.run(scenario())


def test_player_holds_queued_switch_until_advance(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "a.mp4").write_bytes(b"a")
        (tmp_path / "b.mp4").write_bytes(b"b")
        fake = ScriptedMpvClient()
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]
        await player.prepare("a.mp4", "stop")
        await player.queue("b.mp4", "hold_last_frame", None, False)
        assert ("set_property", "keep-open", "always") in fake.commands

        fake.properties.update({"time-pos": 4.0, "pause": True, "eof-reached": True})
        held = await player.status()
        assert held.state is PlaybackState.COMPLETED
        assert ("set_property", "vid", "no") in fake.commands

        fake.commands.clear()
        await player.advance()
        # The switch holds the queued file's first frame until the controller sends play.
        assert fake.commands == [
            ("set_property", "pause", True),
            ("playlist-next", "force"),
            ("playlist-remove", 0),
        ]
        assert fake.properties["path"] == str((tmp_path / "b.mp4").resolve())
        assert player.entry == 2
        with pytest.raises(PlayerError):
            await player.advance()

    asyncio.run(scenario())


def test_player_refuses_automatic_advance_after_looping_media(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "a.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]
        await player.prepare("a.mp4", "loop")
        with pytest.raises(PlayerError, match="never ends"):
            await player.queue("a.mp4", "stop", None, True)

    asyncio.run(scenario())


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _speeds(fake: "ScriptedMpvClient") -> list[object]:
    return [command[2] for command in fake.commands if command[:2] == ("set_property", "speed")]


def test_looping_media_catches_up_time_lost_at_each_wrap(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "beat.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        clock = _Clock()
        player = MpvPlayer(fake, tmp_path, monotonic=clock)  # type: ignore[arg-type]
        await player.prepare("beat.mp4", "loop")
        await player.play()
        await player.status()

        # The wrap back to the start cost 60 ms of wall-clock time.
        clock.now += 4.5
        fake.properties["time-pos"] = 0.44
        await player.status()
        assert _speeds(fake) == [1.04]

        # Back on the beat: the smoothed error decays and normal speed returns.
        for _ in range(30):
            clock.now += 0.1
            fake.properties["time-pos"] = (clock.now - 100.0) % 4.0
            await player.status()
        assert _speeds(fake)[-1] == 1.0
        assert not any(command[0] == "seek" for command in fake.commands)

        # Running ahead slows down by the same rule.
        for _ in range(10):
            clock.now += 0.1
            fake.properties["time-pos"] = (clock.now - 100.0) % 4.0 + 0.2
            await player.status()
        assert _speeds(fake)[-1] < 1.0

    asyncio.run(scenario())


def test_small_position_noise_does_not_change_speed(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "beat.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        clock = _Clock()
        player = MpvPlayer(fake, tmp_path, monotonic=clock)  # type: ignore[arg-type]
        await player.prepare("beat.mp4", "loop")
        await player.play()
        for index in range(120):
            fake.properties["time-pos"] = (clock.now - 100.0) % 4.0 + (0.025 if index % 2 else 0)
            await player.status()
            clock.now += 1 / 15
        assert _speeds(fake) == []

    asyncio.run(scenario())


def test_large_loop_error_seeks_back_onto_the_beat(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "beat.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        clock = _Clock()
        player = MpvPlayer(fake, tmp_path, monotonic=clock)  # type: ignore[arg-type]
        await player.prepare("beat.mp4", "loop")
        await player.play()
        await player.status()

        clock.now += 3.0
        fake.properties["time-pos"] = 2.2
        await player.status()
        assert ("seek", 3.0, "absolute+exact") in fake.commands
        assert _speeds(fake) == []

    asyncio.run(scenario())


def test_pause_and_seek_restart_pacing_and_restore_normal_speed(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "beat.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        clock = _Clock()
        player = MpvPlayer(fake, tmp_path, monotonic=clock)  # type: ignore[arg-type]
        await player.prepare("beat.mp4", "loop")
        await player.play()
        await player.status()
        clock.now += 1.0
        fake.properties["time-pos"] = 0.9
        await player.status()
        assert _speeds(fake) == [1.04]

        await player.pause()
        assert _speeds(fake) == [1.04, 1.0]
        clock.now += 10.0
        await player.resume()
        fake.properties["time-pos"] = 0.9
        await player.status()
        clock.now += 0.5
        fake.properties["time-pos"] = 1.4
        await player.status()
        assert _speeds(fake) == [1.04, 1.0]

        await player.seek(3000)
        clock.now += 0.5
        fake.properties["time-pos"] = 3.0
        await player.status()
        assert _speeds(fake) == [1.04, 1.0]

    asyncio.run(scenario())


def test_repeat_counts_keep_pace_but_single_plays_are_left_alone(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "clip.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        clock = _Clock()
        player = MpvPlayer(fake, tmp_path, monotonic=clock)  # type: ignore[arg-type]
        await player.prepare("clip.mp4", "hold_last_frame")
        await player.play()
        await player.status()
        clock.now += 1.0
        fake.properties["time-pos"] = 0.9
        await player.status()
        assert _speeds(fake) == []

        await player.prepare("clip.mp4", "hold_last_frame", 3)
        await player.play()
        fake.properties["time-pos"] = 3.9
        await player.status()
        clock.now += 0.2
        fake.properties["time-pos"] = 0.05
        repeated = await player.status()
        assert repeated.position_ms == 4050
        assert _speeds(fake) == [1.04]

    asyncio.run(scenario())


def test_loops_end_on_the_last_whole_video_frame(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "beat.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        # Untrimmed AAC priming runs the file 21 ms past its last video frame.
        fake.properties["duration"] = 2.021333
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]
        await player.prepare("beat.mp4", "loop")
        assert ("set_property", "ab-loop-b", 2.0) in fake.commands
        await player.play()
        fake.properties["time-pos"] = 1.5
        status = await player.status()
        assert status.duration_ms == 2000

    asyncio.run(scenario())


def test_looping_media_continued_into_from_the_queue_gets_a_frame_aligned_loop(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        (tmp_path / "a.mp4").write_bytes(b"a")
        (tmp_path / "beat.mp4").write_bytes(b"b")
        fake = ScriptedMpvClient()
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]
        await player.prepare("a.mp4", "hold_last_frame")
        await player.queue("beat.mp4", "loop", None, False)
        await player.advance()
        fake.commands.clear()
        fake.properties.update({"duration": 2.021333, "time-pos": 0.1})
        status = await player.status()
        assert ("set_property", "ab-loop-a", 0) in fake.commands
        assert ("set_property", "ab-loop-b", 2.0) in fake.commands
        assert status.duration_ms == 2000

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("duration", "fps", "end"),
    [
        (2.0, 30.0, 2.0),
        (2.021333, 30.0, 2.0),
        (1.99999, 30.0, 2.0),
        (36.2, 25.0, 36.2),
        (2.0, None, 2.0),
        (0.01, 30.0, 0.01),
    ],
)
def test_loop_end_rounds_to_whole_frames(duration: float, fps: float | None, end: float) -> None:
    assert _loop_end(duration, fps) == pytest.approx(end)


def test_prepare_acknowledges_only_once_the_first_frame_is_decoded(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "long.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        fake.properties["path"] = "/old/file.mp4"
        # Still opening: the first frame isn't decoded yet.
        fake.properties["video-params/w"] = None
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]
        prepare = asyncio.create_task(player.prepare("long.mp4", "hold_last_frame"))
        await asyncio.sleep(0.1)
        assert not prepare.done()
        fake.properties["video-params/w"] = 1072
        await asyncio.wait_for(prepare, timeout=1)

    asyncio.run(scenario())


def test_prepare_does_not_mistake_the_previous_file_for_the_new_one(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "next.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]
        original = fake.command

        async def loads_slowly(*items: object) -> object:
            result = await original(*items)
            if items[0] == "loadfile":
                fake.properties["path"] = "/old/file.mp4"
            return result

        fake.command = loads_slowly  # type: ignore[method-assign]
        prepare = asyncio.create_task(player.prepare("next.mp4", "stop"))
        await asyncio.sleep(0.1)
        assert not prepare.done()
        fake.properties["path"] = str((tmp_path / "next.mp4").resolve())
        await asyncio.wait_for(prepare, timeout=1)

    asyncio.run(scenario())


def test_prepare_fails_when_the_media_never_finishes_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        (tmp_path / "stuck.mp4").write_bytes(b"a")
        fake = ScriptedMpvClient()
        fake.properties["video-params/w"] = None
        clock = _Clock()
        player = MpvPlayer(fake, tmp_path, monotonic=clock)  # type: ignore[arg-type]

        async def tick(_: float) -> None:
            clock.now += 1.0

        monkeypatch.setattr("cueverge.media_agent.mpv_player.asyncio.sleep", tick)
        with pytest.raises(PlayerError, match="did not finish loading"):
            await player.prepare("stuck.mp4", "stop")

    asyncio.run(scenario())


def test_audio_only_media_is_ready_without_a_video_frame(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "sound.wav").write_bytes(b"a")
        fake = ScriptedMpvClient()
        fake.properties.update({"vid": False, "video-params/w": None})
        player = MpvPlayer(fake, tmp_path)  # type: ignore[arg-type]
        await asyncio.wait_for(player.prepare("sound.wav", "stop"), timeout=1)

    asyncio.run(scenario())
