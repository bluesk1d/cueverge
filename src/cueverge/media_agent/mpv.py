import asyncio
import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


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
    _handle: Any = None
    _read_buffer: bytearray = field(default_factory=bytearray)

    async def connect(self, attempts: int = 50, delay_seconds: float = 0.1) -> None:
        if sys.platform != "win32":
            raise MpvError("Windows named-pipe transport requires Windows")
        import pywintypes  # type: ignore[import-untyped]
        import win32file  # type: ignore[import-untyped]

        for attempt in range(attempts):
            try:
                self._handle = await asyncio.to_thread(
                    win32file.CreateFile,
                    self.pipe_path,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
                return
            except pywintypes.error as error:
                if error.winerror not in {2, 231}:
                    raise MpvError(f"could not open mpv IPC pipe: {error}") from error
                if attempt == attempts - 1:
                    raise MpvError(f"mpv IPC pipe was not created: {self.pipe_path}") from None
                await asyncio.sleep(delay_seconds)

    async def write(self, payload: bytes) -> None:
        if self._handle is None:
            raise MpvError("mpv IPC pipe is not connected")
        import win32file

        await asyncio.to_thread(win32file.WriteFile, self._handle, payload)

    async def readline(self) -> bytes:
        if self._handle is None:
            raise MpvError("mpv IPC pipe is not connected")
        import win32file

        while b"\n" not in self._read_buffer:
            _, chunk = await asyncio.to_thread(win32file.ReadFile, self._handle, 4096)
            if not chunk:
                return b""
            self._read_buffer.extend(chunk)
        line, _, remainder = self._read_buffer.partition(b"\n")
        self._read_buffer = bytearray(remainder)
        return bytes(line) + b"\n"

    async def close(self) -> None:
        if self._handle is not None:
            import win32file

            await asyncio.to_thread(win32file.CloseHandle, self._handle)
            self._handle = None
            self._read_buffer.clear()


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
    _pending: dict[int, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    _events: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    _reader_task: asyncio.Task[None] | None = None

    def start_reader(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_messages(), name="mpv-ipc-reader")

    async def command(self, *items: object) -> Any:
        if self._reader_task is None:
            raise MpvError("mpv reader is not running")
        request_id = self._next_request_id
        self._next_request_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self.transport.write(JsonLineCodec.encode(list(items), request_id))
        try:
            response = await asyncio.wait_for(future, timeout=self.command_timeout_seconds)
        except TimeoutError as timeout_error:
            self._pending.pop(request_id, None)
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
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        await self.transport.close()

    async def _read_messages(self) -> None:
        while payload := await self.transport.readline():
            message = JsonLineCodec.decode(payload)
            request_id = message.get("request_id")
            if isinstance(request_id, int) and request_id in self._pending:
                self._pending.pop(request_id).set_result(message)
            else:
                await self._events.put(message)
        closed_error = MpvError("mpv IPC connection closed")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(closed_error)
        self._pending.clear()


def resolve_local_asset(asset_root: Path, asset_id: str) -> Path:
    root = asset_root.resolve()
    candidate = (root / asset_id).resolve()
    if not candidate.is_relative_to(root):
        raise MpvError(f"asset path escapes configured root: {asset_id}")
    if not candidate.is_file():
        raise MpvError(f"local media asset does not exist: {asset_id}")
    return candidate
