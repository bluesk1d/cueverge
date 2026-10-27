import asyncio
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cueverge.gui.agent_settings import AgentSettingsError, DeviceAgentSettingsService
from cueverge.gui.contracts import Role
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.mcp_authoring import McpScope, allowed_mcp_scopes
from cueverge.gui.repository import GuiRepository
from cueverge.media_agent.agent_settings import (
    AgentSettingsStore,
    _linux_displays,
    discover_audio_devices,
)
from cueverge.media_agent.device_presence import DevicePresenceClient
from cueverge.protocol.device_v2 import (
    AgentEffectiveSettings,
    AgentSettings,
    AgentSettingsState,
    DeviceAgentSettingsAck,
    DeviceAgentSettingsCommand,
    DeviceHello,
    DeviceWelcome,
)
from tests.unit.test_agent_update_service import FakeRuntime
from tests.unit.test_device_transport import DEVICE_ID, _enrolled_device

DEFAULTS = AgentEffectiveSettings(audio_delay_ms=20, fullscreen_screen=1)


def test_store_merges_persists_and_reports_changes(tmp_path: Path) -> None:
    path = tmp_path / "controller-settings.json"
    store = AgentSettingsStore(path, DEFAULTS)
    assert (store.revision, store.effective) == (0, DEFAULTS)
    changed = store.replace(3, AgentSettings(keep_awake="display", audio_delay_ms=20))
    assert changed == ("keep_awake",)
    assert store.effective.fullscreen_screen == 1
    reloaded = AgentSettingsStore(path, DEFAULTS)
    assert reloaded.revision == 3
    assert reloaded.effective.keep_awake == "display"
    assert reloaded.replace(4, AgentSettings()) == ("keep_awake",)
    path.write_text("{not json", encoding="utf-8")
    assert AgentSettingsStore(path, DEFAULTS).revision == 0


def test_discovers_mpv_audio_devices_and_connected_displays(tmp_path: Path) -> None:
    fake_mpv = tmp_path / "mpv"
    fake_mpv.write_text(
        f"#!{sys.executable}\n"
        "print('List of detected audio devices:')\n"
        "print(\"  'auto' (Autoselect device)\")\n"
        "print(\"  'pipewire/alsa_output.hdmi' (HDMI / DisplayPort 3 Output)\")\n",
        encoding="utf-8",
    )
    fake_mpv.chmod(fake_mpv.stat().st_mode | stat.S_IEXEC)
    devices = discover_audio_devices(fake_mpv)
    assert [device.name for device in devices] == ["auto", "pipewire/alsa_output.hdmi"]
    assert devices[1].description == "HDMI / DisplayPort 3 Output"
    assert discover_audio_devices(tmp_path / "missing") == ()

    drm = tmp_path / "drm"
    for name, status, modes in (
        ("card1-DP-1", "disconnected", ""),
        ("card1-HDMI-A-1", "connected", "1920x1080\n1280x720\n"),
        ("card1-HDMI-A-2", "connected", "3840x2160\n"),
        ("card1", "", ""),
    ):
        (drm / name).mkdir(parents=True)
        if status:
            (drm / name / "status").write_text(status + "\n")
            (drm / name / "modes").write_text(modes)
    displays = _linux_displays(drm)
    assert [(d.index, d.name, d.width, d.height) for d in displays] == [
        (0, "HDMI-A-1", 1920, 1080),
        (1, "HDMI-A-2", 3840, 2160),
    ]


class FakeHost:
    def __init__(self, state: str = "ready") -> None:
        self.state = state
        self.running = {"fullscreen_screen": 1, "audio_device": None, "audio_delay_ms": 20}
        self.calls: list[str] = []
        self.configured: AgentEffectiveSettings | None = None

    def configure(self, settings: AgentEffectiveSettings) -> None:
        self.configured = settings

    async def apply_live(self, settings: AgentEffectiveSettings) -> None:
        self.calls.append("live")
        self.running["audio_device"] = settings.audio_device
        self.running["audio_delay_ms"] = settings.audio_delay_ms

    def pending_restart(self, settings: AgentEffectiveSettings) -> tuple[str, ...]:
        if self.state != "ready":
            return ()
        return tuple(
            name for name, value in self.running.items() if getattr(settings, name) != value
        )

    async def restart(self) -> str:
        self.calls.append("restart")
        assert self.configured is not None
        self.running["fullscreen_screen"] = self.configured.fullscreen_screen
        return "ready"

    async def start(self) -> str:
        self.calls.append("start")
        self.state = "ready"
        return "ready"


class FakeKeepAwake:
    def __init__(self) -> None:
        self.modes: list[str] = []
        self.status = None

    async def change(self, mode: str) -> None:
        self.modes.append(mode)


def _presence(tmp_path: Path, host: FakeHost) -> tuple[DevicePresenceClient, FakeKeepAwake]:
    client = object.__new__(DevicePresenceClient)
    client.settings = AgentSettingsStore(tmp_path / "settings.json", DEFAULTS)
    client.player_host = host  # type: ignore[assignment]
    keep_awake = FakeKeepAwake()
    client.keep_awake = keep_awake  # type: ignore[assignment]
    client.displays = ()
    client.audio_devices = ()
    client.media_client = SimpleNamespace(
        _clear_playback=lambda: host.calls.append("clear"),
        config=SimpleNamespace(mpv_executable=tmp_path / "missing-mpv"),
    )
    client._settings_reported = None
    return client, keep_awake


def _command(revision: int, settings: AgentSettings, restart: bool) -> tuple[Any, ...]:
    welcome = DeviceWelcome(
        device_id=DEVICE_ID,
        controller_epoch=uuid4(),
        heartbeat_interval_ms=1,
        heartbeat_timeout_ms=9,
    )
    session_id = uuid4()
    command = DeviceAgentSettingsCommand(
        request_id=uuid4(),
        device_session_id=session_id,
        controller_epoch=welcome.controller_epoch,
        revision=revision,
        settings=settings,
        allow_player_restart=restart,
    )
    return command, session_id, welcome


def test_agent_applies_audio_live_and_defers_display_changes(tmp_path: Path) -> None:
    host = FakeHost()
    client, keep_awake = _presence(tmp_path, host)
    settings = AgentSettings(keep_awake="system", fullscreen_screen=0, audio_delay_ms=-40)
    ack = asyncio.run(client._apply_settings(*_command(1, settings, restart=False)))
    assert ack.status == "completed" and ack.state is not None
    assert ack.state.revision == 1
    assert ack.state.effective.audio_delay_ms == -40
    assert ack.state.host_defaults == DEFAULTS
    assert ack.state.pending_player_restart == ("fullscreen_screen",)
    assert keep_awake.modes == ["system"]
    assert host.calls == ["live"]

    ack = asyncio.run(client._apply_settings(*_command(2, settings, restart=True)))
    assert ack.state is not None and ack.state.pending_player_restart == ()
    assert host.calls == ["live", "live", "clear", "restart"]
    assert keep_awake.modes == ["system"]
    assert client._changed_settings_state() is None


def test_agent_starts_a_stopped_player_when_switched_to_always(tmp_path: Path) -> None:
    host = FakeHost(state="stopped")
    client, _ = _presence(tmp_path, host)
    client.settings = AgentSettingsStore(
        tmp_path / "s.json", DEFAULTS.model_copy(update={"player_mode": "on_demand"})
    )
    asyncio.run(client._apply_settings(*_command(1, AgentSettings(player_mode="always"), False)))
    assert "start" not in host.calls
    asyncio.run(client._apply_settings(*_command(2, AgentSettings(player_mode="on_demand"), True)))
    asyncio.run(client._apply_settings(*_command(3, AgentSettings(player_mode="always"), True)))
    assert host.calls.count("start") == 1


class FakeSettingsAgent:
    def __init__(self, sessions: DeviceSessionRegistry, session_id: Any, error: str | None = None):
        self.sessions = sessions
        self.session_id = session_id
        self.error = error
        self.commands: list[DeviceAgentSettingsCommand] = []

    async def send_device_message(self, message: object) -> None:
        assert isinstance(message, DeviceAgentSettingsCommand)
        self.commands.append(message)
        state = _state(message.revision, AgentEffectiveSettings().merged(message.settings))
        ack = DeviceAgentSettingsAck(
            request_id=message.request_id,
            device_session_id=message.device_session_id,
            controller_epoch=message.controller_epoch,
            revision=message.revision,
            status="error" if self.error else "completed",
            state=None if self.error else state,
            error=self.error,
        )
        if ack.state is not None:
            self.sessions.set_agent_settings_state(DEVICE_ID, self.session_id, ack.state)
        asyncio.get_running_loop().call_soon(
            self.sessions.acknowledge, DEVICE_ID, message.request_id, ack
        )


def _state(revision: int, effective: AgentEffectiveSettings | None = None) -> AgentSettingsState:
    return AgentSettingsState(
        revision=revision,
        effective=effective or AgentEffectiveSettings(),
        host_defaults=AgentEffectiveSettings(),
    )


def connect(
    repository: GuiRepository,
    sessions: DeviceSessionRegistry,
    capabilities: Any,
    *,
    supported: bool = True,
    revision: int = 0,
    error: str | None = None,
) -> FakeSettingsAgent:
    session_id = uuid4()
    hello = DeviceHello(
        device_id=DEVICE_ID,
        device_session_id=session_id,
        capabilities=capabilities,
        media_features=("agent_settings",) if supported else (),
        agent_settings=_state(revision) if supported else None,
    )
    sessions.connect(repository.get_device(str(DEVICE_ID)), hello)
    agent = FakeSettingsAgent(sessions, session_id, error)
    sessions.attach_channel(DEVICE_ID, session_id, agent)
    return agent


def test_controller_stores_pushes_and_reports_settings(tmp_path: Path) -> None:
    repository, _, capabilities = _enrolled_device(tmp_path)
    sessions = DeviceSessionRegistry()
    runtime = FakeRuntime(mode="automatic")
    service = DeviceAgentSettingsService(repository, sessions, runtime)
    device_id = str(DEVICE_ID)
    assert service.view(device_id).sync == "offline"

    async def offline_save() -> None:
        await service.update(
            device_id, AgentSettings(keep_awake="display"), expected_revision=0, actor="admin"
        )

    asyncio.run(offline_save())
    offline = service.view(device_id)
    assert (offline.revision, offline.sync) == (1, "offline")
    assert "when the device connects" in (offline.detail or "")

    connect(repository, sessions, capabilities, supported=False)
    assert service.view(device_id).sync == "unsupported"

    agent = connect(repository, sessions, capabilities, revision=0)
    assert service.view(device_id).sync == "pending"

    async def reconnect_sync() -> None:
        service.sync_on_connect(device_id, 0)
        for _ in range(100):
            await asyncio.sleep(0.01)
            if agent.commands:
                break
        await asyncio.sleep(0.05)

    asyncio.run(reconnect_sync())
    [pushed] = agent.commands
    assert (pushed.revision, pushed.settings.keep_awake, pushed.allow_player_restart) == (
        1,
        "display",
        False,
    )
    applied = service.view(device_id)
    assert (applied.sync, applied.applied_revision) == ("applied", 1)
    assert applied.effective is not None and applied.effective.keep_awake == "display"

    runtime.mode = "maintenance"

    async def edit() -> Any:
        return await service.update(
            device_id, AgentSettings(fullscreen_screen=2), expected_revision=1, actor="admin"
        )

    result = asyncio.run(edit())
    assert agent.commands[-1].allow_player_restart is True
    assert result.overrides == AgentSettings(fullscreen_screen=2)
    assert result.sync == "applied"
    with pytest.raises(AgentSettingsError, match="revision 2, not 1"):
        asyncio.run(edit())

    failing = connect(repository, sessions, capabilities, revision=2, error="disk full")

    async def failed_edit() -> Any:
        return await service.update(device_id, AgentSettings(), expected_revision=2, actor="a")

    failed = asyncio.run(failed_edit())
    assert (failed.sync, failed.detail) == ("failed", "disk full")
    assert len(failing.commands) == 1
    audit = [event.action for event in repository.list_audit_events()]
    assert audit.count("device.agent_settings.update") == 3


def test_device_config_scope_is_for_global_administrators_only() -> None:
    assert McpScope.DEVICE_CONFIG in allowed_mcp_scopes(Role.ADMINISTRATOR, None, True)
    for role in (Role.OPERATOR, Role.PUBLISHER, Role.DESIGNER):
        assert McpScope.DEVICE_CONFIG not in allowed_mcp_scopes(role, Role.ADMINISTRATOR, True)


def test_gui_and_mcp_read_and_edit_agent_settings(tmp_path: Path) -> None:
    from cueverge.controller.app import create_app
    from cueverge.controller.runtime import ControllerRuntime
    from cueverge.gui.service import GuiServices
    from tests.unit.test_device_enrollment_api import _config, _login, _mutation_headers

    _, _, capabilities = _enrolled_device(tmp_path)
    runtime = ControllerRuntime()
    services = GuiServices.create(_config(tmp_path), runtime)
    agent = connect(services.repository, services.device_sessions, capabilities)
    now = datetime.now(UTC)
    installation_id = services.repository.get_runtime_installation().id

    def token(scopes: tuple[McpScope, ...]) -> str:
        return services.mcp_store.create_installation_token(
            client_id="agent-settings",
            username="admin",
            installation_id=installation_id,
            scopes=scopes,
            expires_at=(now + timedelta(hours=1)).isoformat(),
            occurred_at=now.isoformat(),
        ).token

    read, configure = token((McpScope.READ,)), token((McpScope.READ, McpScope.DEVICE_CONFIG))
    url = f"/api/gui/v1/devices/{DEVICE_ID}/agent-settings"
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:

        def tool(secret: str, name: str, **arguments: object) -> dict[str, Any]:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert response.status_code == 200, response.text
            return dict(response.json()["result"])

        viewed = tool(read, "get_device_agent_settings", device_id=str(DEVICE_ID))
        assert viewed["structuredContent"]["sync"] == "applied"
        arguments = {
            "device_id": str(DEVICE_ID),
            "settings": {"keep_awake": "display", "audio_delay_ms": 35},
            "expected_revision": 0,
        }
        assert tool(read, "set_device_agent_settings", **arguments)["isError"]
        invalid = tool(
            configure,
            "set_device_agent_settings",
            **{**arguments, "settings": {"keep_awake": "always"}},
        )
        assert invalid["isError"]
        saved = tool(configure, "set_device_agent_settings", **arguments)["structuredContent"]
        assert (saved["revision"], saved["sync"]) == (1, "applied")
        assert saved["effective"]["audio_delay_ms"] == 35

        _login(client)
        assert client.get(url).json()["overrides"]["keep_awake"] == "display"
        stale = client.put(
            url,
            json={"settings": {}, "expected_revision": 0},
            headers=_mutation_headers(client),
        )
        assert stale.status_code == 409
        reset = client.put(
            url,
            json={"settings": {"telemetry_hz": 30}, "expected_revision": 1},
            headers=_mutation_headers(client),
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["overrides"]["keep_awake"] is None
        assert reset.json()["effective"]["telemetry_hz"] == 30
        applied = client.post(f"{url}/apply", headers=_mutation_headers(client))
        assert applied.status_code == 200
        assert client.get("/api/gui/v1/devices/missing/agent-settings").status_code == 404

    assert len(agent.commands) == 3
    actions = [event.action for event in services.repository.list_audit_events()]
    assert actions.count("device.agent_settings.update") == 2
    assert "device.agent_settings.apply" in actions
