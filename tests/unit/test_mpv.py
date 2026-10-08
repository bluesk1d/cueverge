import asyncio
import json
from pathlib import Path

import pytest

from cueverge.media_agent.mpv import (
    JsonLineCodec,
    MpvClient,
    MpvError,
    MpvProcess,
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


def test_builds_windows_mpv_command_without_device_assumptions() -> None:
    process = MpvProcess(
        executable=Path("C:/tools/mpv/mpv.exe"),
        pipe_path=r"\\.\pipe\cueverge-mpv",
        fullscreen_screen=1,
        audio_device="wasapi/example",
    )

    assert process.command() == [
        "C:/tools/mpv/mpv.exe",
        "--idle=yes",
        "--force-window=yes",
        "--fullscreen=yes",
        r"--input-ipc-server=\\.\pipe\cueverge-mpv",
        "--fs-screen=1",
        "--audio-device=wasapi/example",
    ]


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
