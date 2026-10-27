import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cueverge.media_agent.client import PlayerError
from cueverge.media_agent.device_presence import DevicePresenceClient
from cueverge.media_agent.mpv import MpvError
from cueverge.media_agent.player_host import PlayerHost
from cueverge.protocol.device_v2 import DevicePlayerCommand, DeviceWelcome


class FakeProcess:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.running = False
        self.starts = 0

    def start(self) -> None:
        self.starts += 1
        if self.fail:
            raise MpvError("mpv executable does not exist: mpv.exe")
        self.running = True

    def stop(self) -> None:
        self.running = False


class FakeTransport:
    """Answers every mpv IPC request successfully and records the commands."""

    def __init__(self) -> None:
        self.commands: list[object] = []
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = False

    async def connect(self, attempts: int = 50, delay_seconds: float = 0.1) -> None:
        return None

    async def write(self, payload: bytes) -> None:
        message = json.loads(payload)
        self.commands.append(message["command"])
        response = {"request_id": message["request_id"], "error": "success", "data": True}
        await self._responses.put(json.dumps(response).encode() + b"\n")

    async def readline(self) -> bytes:
        return await self._responses.get()

    async def close(self) -> None:
        self.closed = True


def test_player_host_starts_and_stops_mpv_on_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        process = FakeProcess()
        transports: list[FakeTransport] = []

        def transport() -> FakeTransport:
            transports.append(FakeTransport())
            return transports[-1]

        host = PlayerHost(process, transport, tmp_path)
        assert host.state == "stopped"
        with pytest.raises(PlayerError, match="stopped"):
            await host.play()

        assert await host.start() == "ready"
        assert process.running
        assert await host.start() == "ready"
        assert process.starts == 1
        await host.pause()

        assert await host.stop_player() == "stopped"
        assert not process.running
        assert transports[0].commands[-1] == ["quit"]
        assert transports[0].closed
        with pytest.raises(PlayerError, match="stopped"):
            await host.status()

    asyncio.run(scenario())


def test_player_host_reports_start_failure_and_can_retry(tmp_path: Path) -> None:
    async def scenario() -> None:
        process = FakeProcess(fail=True)
        host = PlayerHost(process, FakeTransport, tmp_path)

        assert await host.start() == "failed"
        assert host.failure == "mpv executable does not exist: mpv.exe"
        process.fail = False
        assert await host.start() == "ready"
        assert host.failure is None

    asyncio.run(scenario())


def test_presence_acknowledges_player_start_and_stop() -> None:
    async def scenario() -> None:
        session_id = uuid4()
        welcome = DeviceWelcome(
            device_id=uuid4(),
            controller_epoch=uuid4(),
            heartbeat_interval_ms=1000,
            heartbeat_timeout_ms=3000,
        )
        cleared: list[bool] = []
        host = SimpleNamespace(state="stopped", failure=None)

        async def start() -> str:
            host.state = "ready"
            return host.state

        async def stop_player() -> str:
            host.state = "stopped"
            return host.state

        host.start, host.stop_player = start, stop_player
        client = object.__new__(DevicePresenceClient)
        client.player_host = host  # type: ignore[assignment]
        client.media_client = SimpleNamespace(_clear_playback=lambda: cleared.append(True))

        def command(kind: str) -> DevicePlayerCommand:
            return DevicePlayerCommand(
                type=kind,  # type: ignore[arg-type]
                request_id=uuid4(),
                device_session_id=session_id,
                controller_epoch=welcome.controller_epoch,
            )

        started = await client._control_player(command("device.player.start"), session_id, welcome)
        assert (started.status, started.player_state, started.error) == ("completed", "ready", None)
        assert client._player_state() == "ready"
        stopped = await client._control_player(command("device.player.stop"), session_id, welcome)
        assert (stopped.status, stopped.player_state) == ("completed", "stopped")
        assert cleared == [True]

        async def failing_start() -> str:
            host.state, host.failure = "failed", "no display"
            return host.state

        host.start = failing_start
        failed = await client._control_player(command("device.player.start"), session_id, welcome)
        assert (failed.status, failed.player_state, failed.error) == (
            "error",
            "failed",
            "no display",
        )

    asyncio.run(scenario())
