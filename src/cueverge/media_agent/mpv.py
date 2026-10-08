import asyncio
import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Protocol


class MpvError(RuntimeError):
    pass


class MpvTransport(Protocol):
    async def write(self, payload: bytes) -> None: ...

    async def readline(self) -> bytes: ...

    async def close(self) -> None: ...


@dataclass
class JsonLineCodec:
    @staticmethod
    def encode(command: list[object], request_id: int) -> bytes:
        return (
            json.dumps(
                {"command": command, "request_id": request_id},
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )

    @staticmethod
    def decode(payload: bytes) -> dict[str, Any]:
        try:
            message = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MpvError(f"invalid mpv JSON message: {error}") from error
        if not isinstance(message, dict):
            raise MpvError("mpv JSON message must be an object")
        return message


@dataclass
class WindowsNamedPipeTransport:
    pipe_path: str
    _pipe: BinaryIO | None = None

    async def connect(self, attempts: int = 50, delay_seconds: float = 0.1) -> None:
        if sys.platform != "win32":
            raise MpvError("Windows named-pipe transport requires Windows")
        for attempt in range(attempts):
            try:
                self._pipe = await asyncio.to_thread(open, self.pipe_path, "r+b", buffering=0)
                return
            except FileNotFoundError:
                if attempt == attempts - 1:
                    raise MpvError(f"mpv IPC pipe was not created: {self.pipe_path}") from None
                await asyncio.sleep(delay_seconds)

    async def write(self, payload: bytes) -> None:
        if self._pipe is None:
            raise MpvError("mpv IPC pipe is not connected")
        await asyncio.to_thread(self._pipe.write, payload)

    async def readline(self) -> bytes:
        if self._pipe is None:
            raise MpvError("mpv IPC pipe is not connected")
        return await asyncio.to_thread(self._pipe.readline)

    async def close(self) -> None:
        if self._pipe is not None:
            await asyncio.to_thread(self._pipe.close)
            self._pipe = None


@dataclass
class MpvProcess:
    executable: Path
    pipe_path: str
    fullscreen_screen: int | None = None
    audio_device: str | None = None
    process: subprocess.Popen[bytes] | None = None

    def command(self) -> list[str]:
        command = [
            str(self.executable),
            "--idle=yes",
            "--force-window=yes",
            "--fullscreen=yes",
            f"--input-ipc-server={self.pipe_path}",
        ]
        if self.fullscreen_screen is not None:
            command.append(f"--fs-screen={self.fullscreen_screen}")
        if self.audio_device is not None:
            command.append(f"--audio-device={self.audio_device}")
        return command

    def start(self) -> None:
        if sys.platform != "win32":
            raise MpvError("the configured mpv process adapter requires Windows")
        if not self.executable.is_file():
            raise MpvError(f"mpv executable does not exist: {self.executable}")
        if self.process is not None and self.process.poll() is None:
            raise MpvError("mpv process is already running")
        self.process = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()


@dataclass
class MpvClient:
    transport: MpvTransport
    command_timeout_seconds: float = 5.0
    _next_request_id: int = 1
    _events: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    _command_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _started: bool = False

    def start_reader(self) -> None:
        self._started = True

    async def command(self, *items: object) -> Any:
        if not self._started:
            raise MpvError("mpv client is not started")
        async with self._command_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            await self.transport.write(JsonLineCodec.encode(list(items), request_id))
            try:
                response = await asyncio.wait_for(
                    self._read_response(request_id),
                    timeout=self.command_timeout_seconds,
                )
            except TimeoutError as timeout_error:
                raise MpvError(
                    f"mpv command timed out after {self.command_timeout_seconds}s"
                ) from timeout_error
        response_error = response.get("error")
        if response_error != "success":
            raise MpvError(f"mpv command failed: {response_error}")
        return response.get("data")

    async def observe_property(self, observer_id: int, name: str) -> None:
        await self.command("observe_property", observer_id, name)

    async def next_event(self) -> Mapping[str, Any]:
        return await self._events.get()

    async def close(self) -> None:
        self._started = False
        await self.transport.close()

    async def _read_response(self, request_id: int) -> dict[str, Any]:
        while True:
            payload = await self.transport.readline()
            if not payload:
                raise MpvError("mpv IPC connection closed")
            message = JsonLineCodec.decode(payload)
            if message.get("request_id") == request_id:
                return message
            await self._events.put(message)


def resolve_local_asset(asset_root: Path, asset_id: str) -> Path:
    root = asset_root.resolve()
    candidate = (root / asset_id).resolve()
    if not candidate.is_relative_to(root):
        raise MpvError(f"asset path escapes configured root: {asset_id}")
    if not candidate.is_file():
        raise MpvError(f"local media asset does not exist: {asset_id}")
    return candidate
