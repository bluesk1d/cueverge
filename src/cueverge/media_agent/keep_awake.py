"""Keep the media host awake while the agent runs so projectors stay lit and reachable.

Linux holds a systemd-logind sleep inhibitor plus a desktop-session (GNOME session manager
or freedesktop ScreenSaver) inhibitor over D-Bus. Windows sets the thread execution state.
Every inhibitor is tied to the agent process, so a crash or kill releases it.
"""

import asyncio
import ctypes
import logging
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, Protocol

from cueverge.protocol.device_v2 import KeepAwakeMode, KeepAwakeStatus

LOGGER = logging.getLogger(__name__)

KeepAwakeSetting = Literal["off", "system", "display"]
KEEP_AWAKE_RETRY_SECONDS = 30.0
_DBUS_TIMEOUT_SECONDS = 5.0
_APP_ID = "cueverge-media-agent"
_REASON = "Cueverge media agent is presenting a show"


class KeepAwakeError(RuntimeError):
    pass


class KeepAwakeBackend(Protocol):
    def acquire(self, mode: KeepAwakeMode) -> None:
        """Hold the inhibitors for mode, or raise KeepAwakeError explaining what is missing."""

    def release(self) -> None: ...


class KeepAwake:
    """Hold a host sleep inhibitor for the agent lifetime and retry until it is granted.

    Backend calls run on one dedicated thread: Windows execution state is per thread, and
    D-Bus calls must not block the event loop.
    """

    def __init__(
        self,
        setting: KeepAwakeSetting,
        backend: KeepAwakeBackend | None = None,
        *,
        retry_seconds: float = KEEP_AWAKE_RETRY_SECONDS,
    ) -> None:
        self.setting = setting
        self.retry_seconds = retry_seconds
        self._backend = backend if backend is not None else platform_backend()
        self._executor: ThreadPoolExecutor | None = None
        self._active = False
        self._detail: str | None = None

    @property
    def status(self) -> KeepAwakeStatus | None:
        if self.setting == "off":
            return None
        return KeepAwakeStatus(mode=self.setting, active=self._active, detail=self._detail)

    async def start(self) -> KeepAwakeStatus | None:
        if self.setting == "off":
            return None
        if self._backend is None:
            self._detail = f"Keep awake is not supported on {sys.platform}."
            LOGGER.warning("%s", self._detail)
            return self.status
        await self._acquire()
        return self.status

    async def maintain(self) -> None:
        """Retry a refused inhibitor, e.g. when the agent started before the desktop session."""
        if self._backend is None:
            return
        while True:
            await asyncio.sleep(self.retry_seconds)
            if self.setting != "off" and not self._active:
                await self._run(self._backend.release)
                await self._acquire()

    async def stop(self) -> None:
        if self._backend is None or self._executor is None:
            return
        await self._run(self._backend.release)
        self._executor.shutdown(wait=False)
        self._executor = None
        self._active = False
        LOGGER.info("keep awake released")

    async def change(self, setting: KeepAwakeSetting) -> KeepAwakeStatus | None:
        """Switch modes while running; maintain() keeps retrying a refused new mode."""
        if setting == self.setting:
            return self.status
        if self._backend is not None and self._executor is not None:
            await self._run(self._backend.release)
        self.setting, self._active, self._detail = setting, False, None
        return await self.start()

    async def _acquire(self) -> None:
        backend = self._backend
        assert backend is not None and self.setting != "off"
        mode: KeepAwakeMode = self.setting
        try:
            await self._run(lambda: backend.acquire(mode))
        except KeepAwakeError as error:
            if self._active or self._detail != str(error):
                LOGGER.warning("keep awake (%s) is not active: %s", mode, error)
            self._active = False
            self._detail = str(error)[:300]
            return
        self._active = True
        self._detail = None
        LOGGER.info("keep awake (%s) active", mode)

    async def _run(self, operation: Callable[[], object]) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="keep-awake")
        await asyncio.get_running_loop().run_in_executor(self._executor, operation)


def platform_backend() -> KeepAwakeBackend | None:
    if sys.platform == "win32":
        return WindowsKeepAwake()
    if sys.platform.startswith("linux"):
        return LinuxKeepAwake()
    return None


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class WindowsKeepAwake:
    """SetThreadExecutionState; the state lasts until released or the thread ends."""

    def __init__(self, set_state: Callable[[int], int] | None = None) -> None:
        self._set_state = set_state
        self._held = False

    def acquire(self, mode: KeepAwakeMode) -> None:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        if mode == "display":
            flags |= ES_DISPLAY_REQUIRED
        if self._setter()(flags) == 0:
            raise KeepAwakeError(
                f"Windows refused SetThreadExecutionState (error {_last_windows_error()})."
            )
        self._held = True

    def release(self) -> None:
        if self._held:
            self._setter()(ES_CONTINUOUS)
            self._held = False

    def _setter(self) -> Callable[[int], int]:
        if self._set_state is None:
            kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
            function = kernel32.SetThreadExecutionState
            function.argtypes = (ctypes.c_uint32,)
            function.restype = ctypes.c_uint32
            self._set_state = function
        return self._set_state


def _last_windows_error() -> int:
    get_last_error: Callable[[], int] | None = getattr(ctypes, "get_last_error", None)
    return get_last_error() if get_last_error is not None else 0


DbusConnector = Callable[[str, bool], Any]

_LOGIN1 = ("/org/freedesktop/login1", "org.freedesktop.login1", "org.freedesktop.login1.Manager")
_GNOME_SESSION = (
    "/org/gnome/SessionManager",
    "org.gnome.SessionManager",
    "org.gnome.SessionManager",
)
_SCREENSAVER = (
    "/org/freedesktop/ScreenSaver",
    "org.freedesktop.ScreenSaver",
    "org.freedesktop.ScreenSaver",
)
# org.gnome.SessionManager.Inhibit flags.
_GNOME_INHIBIT_SUSPEND = 4
_GNOME_INHIBIT_IDLE = 8


class LinuxKeepAwake:
    """logind blocks sleep; the desktop session inhibitor stops its idle suspend and blanking.

    The logind inhibitor lasts while its file descriptor is open and the session inhibitor
    while the session-bus connection stays open.
    """

    def __init__(self, connect: DbusConnector | None = None) -> None:
        self._connect = connect if connect is not None else _open_dbus_connection
        self._sleep_lock: Any = None
        self._session: Any = None

    def acquire(self, mode: KeepAwakeMode) -> None:
        what = "sleep:idle" if mode == "display" else "sleep"
        try:
            with self._connect("SYSTEM", True) as system:
                (self._sleep_lock,) = _call(
                    system, _LOGIN1, "Inhibit", "ssss", (what, _APP_ID, _REASON, "block")
                )
        except Exception as error:
            raise KeepAwakeError(f"systemd-logind refused the sleep inhibitor: {error}") from error
        try:
            session = self._connect("SESSION", False)
        except Exception as error:
            if mode == "display":
                raise KeepAwakeError(
                    "Sleep is blocked, but screen blanking is not: the desktop session bus is "
                    f"unavailable ({error}). Run the agent inside the logged-in desktop session."
                ) from error
            LOGGER.info("no desktop session bus; relying on the logind sleep inhibitor")
            return
        self._session = session
        flags = _GNOME_INHIBIT_SUSPEND | (_GNOME_INHIBIT_IDLE if mode == "display" else 0)
        try:
            _call(session, _GNOME_SESSION, "Inhibit", "susu", (_APP_ID, 0, _REASON, flags))
            return
        except Exception as gnome_error:
            if mode == "system":
                return
            try:
                _call(session, _SCREENSAVER, "Inhibit", "ss", (_APP_ID, _REASON))
                return
            except Exception as screensaver_error:
                raise KeepAwakeError(
                    "Sleep is blocked, but the desktop refused to stop screen blanking "
                    f"(GNOME: {gnome_error}; ScreenSaver: {screensaver_error})."
                ) from screensaver_error

    def release(self) -> None:
        session, self._session = self._session, None
        lock, self._sleep_lock = self._sleep_lock, None
        if session is not None:
            session.close()
        if lock is not None:
            lock.close()


def _open_dbus_connection(bus: str, enable_fds: bool) -> Any:
    from jeepney.io.blocking import open_dbus_connection

    return open_dbus_connection(bus=bus, enable_fds=enable_fds)


def _call(
    connection: Any,
    target: tuple[str, str, str],
    method: str,
    signature: str,
    body: tuple[object, ...],
) -> tuple[Any, ...]:
    from jeepney import DBusAddress, new_method_call
    from jeepney.wrappers import unwrap_msg

    path, bus_name, interface = target
    message = new_method_call(
        DBusAddress(path, bus_name=bus_name, interface=interface), method, signature, body
    )
    reply = connection.send_and_get_reply(message, timeout=_DBUS_TIMEOUT_SECONDS)
    return tuple(unwrap_msg(reply))
