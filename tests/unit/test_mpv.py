import asyncio
import json
import sys
from pathlib import Path

import pytest

from cueverge.media_agent.mpv import (
    JsonLineCodec,
    MpvClient,
    MpvError,
    MpvProcess,
    UnixSocketTransport,
    create_mpv_transport,
    resolve_local_asset,
)
from cueverge.media_agent.mpv_player import MpvPlayer
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


class RecordingMpvClient:
    def __init__(self) -> None:
        self.commands: list[tuple[object, ...]] = []

    async def command(self, *items: object) -> object:
        self.commands.append(items)
        if items == ("get_property", "duration"):
            return 3.0
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
            ("set_property", "loop-file", "no"),
            ("set_property", "keep-open", keep_open_value),
            (
                "set_property",
                "gapless-audio",
                "yes" if end_behavior == "loop" else "no",
            ),
            ("loadfile", str(asset.resolve()), "replace"),
            ("set_property", "pause", True),
            *(
                [
                    ("get_property", "duration"),
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

    asyncio.run(scenario())
