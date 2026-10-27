import asyncio
import threading
from typing import Any

import pytest

from cueverge.config import MediaAgentConfig
from cueverge.media_agent.keep_awake import (
    ES_CONTINUOUS,
    ES_DISPLAY_REQUIRED,
    ES_SYSTEM_REQUIRED,
    KeepAwake,
    KeepAwakeError,
    LinuxKeepAwake,
    WindowsKeepAwake,
)
from cueverge.protocol.device_v2 import KeepAwakeMode, KeepAwakeStatus


class FakeBackend:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[str, str | None, int]] = []

    def acquire(self, mode: KeepAwakeMode) -> None:
        self.calls.append(("acquire", mode, threading.get_ident()))
        if self.failures:
            self.failures -= 1
            raise KeepAwakeError("desktop session not ready")

    def release(self) -> None:
        self.calls.append(("release", None, threading.get_ident()))


def test_off_reports_nothing_and_never_touches_the_host() -> None:
    backend = FakeBackend()
    keep_awake = KeepAwake("off", backend)

    async def scenario() -> None:
        assert await keep_awake.start() is None
        await keep_awake.maintain()
        await keep_awake.stop()

    asyncio.run(scenario())
    assert keep_awake.status is None
    assert backend.calls == []


def test_holds_and_releases_the_inhibitor_on_one_thread() -> None:
    backend = FakeBackend()
    keep_awake = KeepAwake("display", backend)

    async def scenario() -> None:
        assert await keep_awake.start() == KeepAwakeStatus(mode="display", active=True)
        await keep_awake.stop()

    asyncio.run(scenario())
    assert [call[:2] for call in backend.calls] == [("acquire", "display"), ("release", None)]
    # Windows execution state is per thread, so release must run where acquire ran.
    assert backend.calls[0][2] == backend.calls[1][2] != threading.get_ident()
    assert keep_awake.status == KeepAwakeStatus(mode="display", active=False)


def test_reports_a_refusal_and_retries_until_granted() -> None:
    backend = FakeBackend(failures=1)
    keep_awake = KeepAwake("system", backend, retry_seconds=0.01)

    async def scenario() -> None:
        assert await keep_awake.start() == KeepAwakeStatus(
            mode="system", active=False, detail="desktop session not ready"
        )
        task = asyncio.create_task(keep_awake.maintain())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if keep_awake.status and keep_awake.status.active:
                break
        task.cancel()
        await keep_awake.stop()

    asyncio.run(scenario())
    assert [call[:2] for call in backend.calls[:3]] == [
        ("acquire", "system"),
        ("release", None),
        ("acquire", "system"),
    ]


def test_unsupported_platforms_report_why() -> None:
    keep_awake = KeepAwake("system", None)
    keep_awake._backend = None
    status = asyncio.run(keep_awake.start())
    assert status is not None and not status.active
    assert "not supported" in (status.detail or "")


def test_windows_sets_and_clears_the_execution_state() -> None:
    flags: list[int] = []
    backend = WindowsKeepAwake(lambda value: flags.append(value) or 1)
    backend.acquire("system")
    backend.release()
    backend.acquire("display")
    backend.release()
    backend.release()
    assert flags == [
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED,
        ES_CONTINUOUS,
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED,
        ES_CONTINUOUS,
    ]
    with pytest.raises(KeepAwakeError, match="SetThreadExecutionState"):
        WindowsKeepAwake(lambda value: 0).acquire("system")


class FakeLock:
    closed = False

    def close(self) -> None:
        self.closed = True


class FakeBus:
    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.sent: list[tuple[str, str, tuple[object, ...]]] = []
        self.closed = False

    def __enter__(self) -> "FakeBus":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def send_and_get_reply(self, message: Any, *, timeout: float) -> Any:
        from jeepney import new_method_return
        from jeepney.low_level import HeaderFields
        from jeepney.wrappers import new_error

        interface = message.header.fields[HeaderFields.interface]
        method = message.header.fields[HeaderFields.member]
        self.sent.append((interface, method, message.body))
        reply = self.replies.get(interface)
        if isinstance(reply, str):
            return new_error(message, reply)
        return new_method_return(message, "u", (0,)) if reply is None else reply(message)

    def close(self) -> None:
        self.closed = True


def _linux(system: FakeBus, session: FakeBus | None) -> LinuxKeepAwake:
    def connect(bus: str, enable_fds: bool) -> FakeBus:
        if bus == "SYSTEM":
            assert enable_fds
            return system
        if session is None:
            raise KeyError("DBUS_SESSION_BUS_ADDRESS")
        return session

    return LinuxKeepAwake(connect)


def _logind(lock: FakeLock) -> FakeBus:
    return FakeBus({"org.freedesktop.login1.Manager": lambda message: SimpleReply((lock,))})


class SimpleReply:
    """A method-return message stand-in carrying an already-received file descriptor."""

    def __init__(self, body: tuple[object, ...]) -> None:
        from jeepney.low_level import Header, MessageType

        self.header = Header(0, MessageType.method_return, 0, 1, 0, 1, {})
        self.body = body


def test_linux_display_blocks_sleep_and_gnome_blanking() -> None:
    lock = FakeLock()
    system = _logind(lock)
    session = FakeBus({})
    backend = _linux(system, session)
    backend.acquire("display")
    assert system.sent[0][1] == "Inhibit"
    assert system.sent[0][2][0::3] == ("sleep:idle", "block")
    assert system.closed
    assert session.sent == [
        (
            "org.gnome.SessionManager",
            "Inhibit",
            ("cueverge-media-agent", 0, "Cueverge media agent is presenting a show", 12),
        )
    ]
    assert not session.closed and not lock.closed
    backend.release()
    assert session.closed and lock.closed


def test_linux_falls_back_to_freedesktop_screensaver_outside_gnome() -> None:
    session = FakeBus({"org.gnome.SessionManager": "org.freedesktop.DBus.Error.ServiceUnknown"})
    backend = _linux(_logind(FakeLock()), session)
    backend.acquire("display")
    assert [sent[0] for sent in session.sent] == [
        "org.gnome.SessionManager",
        "org.freedesktop.ScreenSaver",
    ]


def test_linux_system_mode_needs_only_logind() -> None:
    lock = FakeLock()
    backend = _linux(_logind(lock), None)
    backend.acquire("system")
    backend.release()
    assert lock.closed
    session = FakeBus({"org.gnome.SessionManager": "org.freedesktop.DBus.Error.ServiceUnknown"})
    _linux(_logind(FakeLock()), session).acquire("system")
    assert session.sent[0][2][3] == 4


def test_linux_display_reports_missing_session_but_keeps_sleep_blocked() -> None:
    lock = FakeLock()
    backend = _linux(_logind(lock), None)
    with pytest.raises(KeepAwakeError, match="screen blanking is not"):
        backend.acquire("display")
    assert not lock.closed
    backend.release()
    assert lock.closed

    refused = FakeBus(
        {
            "org.gnome.SessionManager": "org.freedesktop.DBus.Error.ServiceUnknown",
            "org.freedesktop.ScreenSaver": "org.freedesktop.DBus.Error.ServiceUnknown",
        }
    )
    with pytest.raises(KeepAwakeError, match="refused to stop screen blanking"):
        _linux(_logind(FakeLock()), refused).acquire("display")


def test_linux_reports_a_logind_refusal() -> None:
    system = FakeBus({"org.freedesktop.login1.Manager": "org.freedesktop.DBus.Error.AccessDenied"})
    with pytest.raises(KeepAwakeError, match="systemd-logind refused"):
        _linux(system, FakeBus({})).acquire("system")


def test_config_reads_keep_awake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUEVERGE_ASSET_ROOT", "/tmp/assets")
    monkeypatch.setenv("CUEVERGE_MPV_EXECUTABLE", "/usr/bin/mpv")
    monkeypatch.delenv("CUEVERGE_KEEP_AWAKE", raising=False)
    assert MediaAgentConfig.from_environment().keep_awake == "off"
    monkeypatch.setenv("CUEVERGE_KEEP_AWAKE", "display")
    assert MediaAgentConfig.from_environment().keep_awake == "display"
    monkeypatch.setenv("CUEVERGE_KEEP_AWAKE", "always")
    with pytest.raises(ValueError, match="keep_awake"):
        MediaAgentConfig.from_environment()
