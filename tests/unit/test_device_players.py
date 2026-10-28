import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from cueverge.core.loader import load_show_package
from cueverge.gui.device_players import DevicePlayerError, DevicePlayerService
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.repository import GuiRepository
from cueverge.protocol.device_v2 import (
    DeviceHeartbeat,
    DeviceHello,
    DevicePlayerAck,
    DevicePlayerCommand,
    KeepAwakeStatus,
)
from tests.unit.test_device_transport import (
    DEVICE_ID,
    NOW,
    SHOWS,
    _assign,
    _enrolled_device,
)


class FakeAgentChannel:
    """Answers player commands the way an agent with player control would."""

    def __init__(self, sessions: DeviceSessionRegistry, *, fail_start: str | None = None) -> None:
        self.sessions = sessions
        self.fail_start = fail_start
        self.commands: list[str] = []

    async def send_device_message(self, message: object) -> None:
        assert isinstance(message, DevicePlayerCommand)
        self.commands.append(message.type)
        starting = message.type == "device.player.start"
        error = self.fail_start if starting else None
        state = "failed" if error else "ready" if starting else "stopped"
        ack = DevicePlayerAck(
            request_id=message.request_id,
            device_session_id=message.device_session_id,
            controller_epoch=message.controller_epoch,
            status="error" if error else "completed",
            player_state=state,  # type: ignore[arg-type]
            error=error,
        )
        self.sessions.set_player_state(DEVICE_ID, message.device_session_id, ack.player_state)
        asyncio.get_running_loop().call_soon(
            self.sessions.acknowledge, DEVICE_ID, message.request_id, ack
        )


def _connect(
    repository: GuiRepository,
    sessions: DeviceSessionRegistry,
    capabilities: Any,
    *,
    player_control: bool = True,
    state: str = "stopped",
    fail_start: str | None = None,
    keep_awake: KeepAwakeStatus | None = None,
) -> FakeAgentChannel:
    session_id = uuid4()
    hello = DeviceHello(
        device_id=DEVICE_ID,
        device_session_id=session_id,
        capabilities=capabilities,
        media_features=("player_control",) if player_control else (),
        player_state=state if player_control else None,  # type: ignore[arg-type]
        keep_awake=keep_awake,
    )
    sessions.connect(repository.get_device(str(DEVICE_ID)), hello)
    channel = FakeAgentChannel(sessions, fail_start=fail_start)
    sessions.attach_channel(DEVICE_ID, session_id, channel)
    return channel


def _service(repository: GuiRepository, sessions: DeviceSessionRegistry, active_scene=None):
    runtime = SimpleNamespace(
        catalog=load_show_package(SHOWS / "multi-output-demo"),
        status=lambda: {"active_scene": active_scene},
    )
    return DevicePlayerService(repository, sessions, runtime, idle_stop_seconds=600)


def test_operator_can_start_and_stop_a_connected_player(tmp_path: Path) -> None:
    repository, _, capabilities = _enrolled_device(tmp_path)
    sessions = DeviceSessionRegistry()
    channel = _connect(repository, sessions, capabilities)
    service = _service(repository, sessions)

    async def scenario() -> None:
        assert await service.start(str(DEVICE_ID)) == "ready"
        assert sessions.player_ready(DEVICE_ID)
        assert await service.stop(str(DEVICE_ID)) == "stopped"
        assert not sessions.player_ready(DEVICE_ID)

    asyncio.run(scenario())
    assert channel.commands == ["device.player.start", "device.player.stop"]
    [status] = service.list()
    assert (status.connected, status.player_control, status.player_state) == (
        True,
        True,
        "stopped",
    )


def test_player_requests_report_unavailable_and_failed_players(tmp_path: Path) -> None:
    repository, _, capabilities = _enrolled_device(tmp_path)
    sessions = DeviceSessionRegistry()
    service = _service(repository, sessions)

    with pytest.raises(DevicePlayerError, match="not connected"):
        asyncio.run(service.start(str(DEVICE_ID)))
    with pytest.raises(DevicePlayerError, match="Device not found"):
        asyncio.run(service.start(str(uuid4())))

    _connect(repository, sessions, capabilities, player_control=False)
    with pytest.raises(DevicePlayerError, match="update the agent"):
        asyncio.run(service.start(str(DEVICE_ID)))

    _connect(repository, sessions, capabilities, fail_start="mpv executable does not exist")
    with pytest.raises(DevicePlayerError, match="mpv executable does not exist"):
        asyncio.run(service.start(str(DEVICE_ID)))
    assert sessions.player_status(DEVICE_ID) == (True, "failed")


def test_show_start_starts_players_the_loaded_show_uses(tmp_path: Path) -> None:
    repository, _, capabilities = _enrolled_device(tmp_path)
    sessions = DeviceSessionRegistry()
    channel = _connect(repository, sessions, capabilities)
    service = _service(repository, sessions)

    assert asyncio.run(service.ensure_ready_for_loaded_show()) == ()
    _assign(repository, "projector.right")
    assert asyncio.run(service.ensure_ready_for_loaded_show()) == ("CuevergeClient01",)
    assert asyncio.run(service.ensure_ready_for_loaded_show()) == ()
    assert channel.commands == ["device.player.start"]

    _connect(repository, sessions, capabilities, fail_start="no projector display")
    with pytest.raises(DevicePlayerError, match=r"CuevergeClient01: .*no projector display"):
        asyncio.run(service.ensure_ready_for_loaded_show())


def test_idle_on_demand_players_stop_after_the_idle_period(tmp_path: Path) -> None:
    repository, _, capabilities = _enrolled_device(tmp_path)
    _assign(repository, "projector.right")
    sessions = DeviceSessionRegistry()
    channel = _connect(repository, sessions, capabilities, state="ready")
    playing = _service(repository, sessions, active_scene="dual-window-sweep")
    idle = _service(repository, sessions)

    async def scenario() -> None:
        assert await idle.stop_idle_players(now=0) == ()
        assert await idle.stop_idle_players(now=10_000) == ()  # continuous policy
        repository.set_device_connection_policy(
            device_id=str(DEVICE_ID),
            policy="on_demand",
            actor="admin",
            occurred_at=NOW.isoformat(),
        )
        assert await playing.stop_idle_players(now=0) == ()
        assert await playing.stop_idle_players(now=10_000) == ()  # in use by the scene
        assert await idle.stop_idle_players(now=20_000) == ()
        assert await idle.stop_idle_players(now=20_599) == ()
        assert await idle.stop_idle_players(now=20_600) == ("CuevergeClient01",)

    asyncio.run(scenario())
    assert channel.commands == ["device.player.stop"]
    assert sessions.player_status(DEVICE_ID) == (True, "stopped")


def test_gui_player_controls_are_audited_and_reported_in_topology(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from cueverge.controller.app import create_app
    from cueverge.controller.runtime import ControllerRuntime
    from cueverge.gui.service import GuiServices
    from tests.unit.test_device_enrollment_api import _config, _login, _mutation_headers

    _, _, capabilities = _enrolled_device(tmp_path)
    runtime = ControllerRuntime()
    services = GuiServices.create(_config(tmp_path), runtime)
    _assign(services.repository, "projector.demo")
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)
    url = f"/api/gui/v1/devices/{DEVICE_ID}/player"

    def node() -> dict[str, Any]:
        topology = client.get("/api/gui/v1/topology").json()
        return next(item for item in topology["nodes"] if item["id"] == f"device:{DEVICE_ID}")

    offline = client.post(url, json={"action": "start"}, headers=_mutation_headers(client))
    assert offline.status_code == 409
    assert offline.json()["detail"]["code"] == "player_control_failed"
    assert "not connected" in offline.json()["detail"]["message"]

    _connect(services.repository, services.device_sessions, capabilities)
    services.repository.mark_device_connected(
        device_id=str(DEVICE_ID),
        session_id="listener-session",
        observed_at_ms=time.time_ns() // 1_000_000,
    )
    assert node()["player_control"] is True
    assert node()["player_state"] == "stopped"
    assert node()["keep_awake"] is None
    assert node()["diagnostic"] == "Opens when the show starts, or start it from the device card."
    started = client.post(url, json={"action": "start"}, headers=_mutation_headers(client))
    assert started.status_code == 200
    assert started.json() == {"device_id": str(DEVICE_ID), "player_state": "ready"}
    assert node()["player_state"] == "ready"
    assert node()["diagnostic"] is None
    stopped = client.post(url, json={"action": "stop"}, headers=_mutation_headers(client))
    assert stopped.json()["player_state"] == "stopped"
    assert (
        client.post(url, json={"action": "pause"}, headers=_mutation_headers(client)).status_code
        == 422
    )

    _connect(
        services.repository,
        services.device_sessions,
        capabilities,
        fail_start="no projector display",
    )
    rejected = client.post(
        "/api/gui/v1/operate/actions",
        json={"action": "start"},
        headers=_mutation_headers(client),
    )
    assert rejected.status_code == 409
    assert "Media player did not start" in rejected.json()["detail"]["message"]
    actions = [(event.action, event.result) for event in services.repository.list_audit_events()]
    assert ("device.player.start", "success") in actions
    assert ("device.player.stop", "success") in actions
    assert ("device.player.start", "error") in actions


def test_keep_awake_is_reported_from_hello_and_heartbeats(tmp_path: Path) -> None:
    repository, _, capabilities = _enrolled_device(tmp_path)
    sessions = DeviceSessionRegistry()
    refused = KeepAwakeStatus(mode="display", active=False, detail="no desktop session")
    _connect(repository, sessions, capabilities, keep_awake=refused)
    service = _service(repository, sessions)
    assert sessions.keep_awake(DEVICE_ID) == refused
    assert service.list()[0].keep_awake == refused

    session_id = sessions._sessions[DEVICE_ID].session_id
    granted = KeepAwakeStatus(mode="display", active=True)
    heartbeat = DeviceHeartbeat(
        device_id=DEVICE_ID,
        device_session_id=session_id,
        controller_epoch=sessions.controller_epoch,
        sequence=0,
        device_monotonic_ms=1,
        keep_awake=granted,
    )
    assert sessions.heartbeat(heartbeat).accepted
    assert service.list()[0].keep_awake == granted
    assert sessions.heartbeat(
        heartbeat.model_copy(update={"sequence": 1, "keep_awake": None})
    ).accepted
    assert sessions.keep_awake(DEVICE_ID) is None


def test_keep_awake_is_omitted_on_the_wire_when_off(tmp_path: Path) -> None:
    _, _, capabilities = _enrolled_device(tmp_path)
    hello = DeviceHello(device_id=DEVICE_ID, device_session_id=uuid4(), capabilities=capabilities)
    assert "keep_awake" not in hello.model_dump(mode="json")
    status = KeepAwakeStatus(mode="system", active=True)
    sent = hello.model_copy(update={"keep_awake": status}).model_dump_json()
    assert DeviceHello.model_validate_json(sent).keep_awake == status
    with pytest.raises(ValueError):
        KeepAwakeStatus.model_validate({"mode": "always", "active": True})


def test_runtime_target_registers_again_after_a_heartbeat_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cueverge.controller.runtime import ControllerRuntime
    from cueverge.gui import device_app

    repository, _, capabilities = _enrolled_device(tmp_path)
    sessions = DeviceSessionRegistry()
    channel = _connect(repository, sessions, capabilities, state="ready")
    now = [100.0]
    runtime = ControllerRuntime(clock=lambda: now[0])
    monkeypatch.setattr(device_app, "_assigned_agent_id", lambda *_: "media-1")
    session = sessions._sessions[DEVICE_ID]
    hello = DeviceHello(
        device_id=DEVICE_ID,
        device_session_id=session.session_id,
        capabilities=capabilities,
        media_features=("player_control",),
        player_state="ready",
    )
    target = device_app._RuntimeTarget(
        repository,
        runtime,
        sessions,
        str(DEVICE_ID),
        hello,
        channel,  # type: ignore[arg-type]
    )

    target.sync()
    assert runtime.is_agent_registered("media-1", channel)  # type: ignore[arg-type]

    # An event-loop stall longer than the heartbeat timeout drops the agent from the runtime
    # while its authenticated device session stays connected.
    now[0] += runtime.heartbeat_timeout_ms / 1000 + 1
    runtime.check_timeouts()
    assert runtime.status()["connected_agents"] == []

    target.sync()
    assert runtime.status()["connected_agents"] == ["media-1"]
    assert runtime.is_agent_registered("media-1", channel)  # type: ignore[arg-type]
