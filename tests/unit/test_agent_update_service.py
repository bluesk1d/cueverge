import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cueverge.agent_bundle import sha256_file
from cueverge.gui.agent_updates import (
    AgentBundleCatalog,
    AgentUpdateError,
    AgentUpdatePreconditionError,
    DeviceAgentUpdateService,
)
from cueverge.gui.contracts import Role
from cueverge.gui.device_app import create_device_app
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.mcp_authoring import McpScope, allowed_mcp_scopes
from cueverge.gui.repository import GuiRepository
from cueverge.protocol.device_v2 import (
    AgentRelease,
    AgentUpdateResult,
    DeviceAgentUpdateAck,
    DeviceAgentUpdateCommand,
    DeviceHello,
)
from tests.unit.test_agent_updates import make_bundle
from tests.unit.test_device_transport import DEVICE_ID, _enrolled_device, _with_peer_certificate

PUBLIC_URL = "wss://192.0.2.10:8443/api/device/v2/agent/ws"
OLD = "a" * 64


class FakeRuntime:
    def __init__(self, mode: str = "maintenance", active_scene: str | None = None) -> None:
        self.mode = mode
        self.active_scene = active_scene

    def status(self) -> dict[str, object]:
        return {"operations": {"mode": self.mode}, "active_scene": self.active_scene}


class FakeUpdatingAgent:
    def __init__(self, sessions: DeviceSessionRegistry, error: str | None = None) -> None:
        self.sessions = sessions
        self.error = error
        self.commands: list[DeviceAgentUpdateCommand] = []

    async def send_device_message(self, message: object) -> None:
        assert isinstance(message, DeviceAgentUpdateCommand)
        self.commands.append(message)
        ack = DeviceAgentUpdateAck(
            request_id=message.request_id,
            device_session_id=message.device_session_id,
            controller_epoch=message.controller_epoch,
            digest_sha256=message.digest_sha256,
            status="error" if self.error else "restarting",
            error=self.error,
        )
        asyncio.get_running_loop().call_soon(
            self.sessions.acknowledge, DEVICE_ID, message.request_id, ack
        )


def _bundles(tmp_path: Path, *platforms: Any) -> Path:
    root = tmp_path / "bundles"
    root.mkdir(exist_ok=True)
    for platform in platforms:
        make_bundle(root / f"cueverge-agent-{platform}.zip", platform=platform, version="2.0.0")
    return root


def _release(**changes: Any) -> AgentRelease:
    values: dict[str, Any] = {
        "version": "1.0.0",
        "platform": "linux_x86_64",
        "bundle_digest": OLD,
        "managed": True,
    }
    values.update(changes)
    return AgentRelease(**values)


def connect(
    repository: GuiRepository,
    sessions: DeviceSessionRegistry,
    capabilities: Any,
    release: AgentRelease | None,
    *,
    agent_update: bool = True,
    error: str | None = None,
) -> FakeUpdatingAgent:
    session_id = uuid4()
    hello = DeviceHello(
        device_id=DEVICE_ID,
        device_session_id=session_id,
        capabilities=capabilities,
        media_features=("agent_update",) if agent_update else (),
        agent_release=release,
    )
    sessions.connect(repository.get_device(str(DEVICE_ID)), hello)
    agent = FakeUpdatingAgent(sessions, error)
    sessions.attach_channel(DEVICE_ID, session_id, agent)
    return agent


def _service(
    tmp_path: Path, *platforms: Any, runtime: FakeRuntime | None = None, **options: Any
) -> tuple[DeviceAgentUpdateService, GuiRepository, DeviceSessionRegistry, Any]:
    repository, _, capabilities = _enrolled_device(tmp_path)
    sessions = DeviceSessionRegistry()
    catalog = AgentBundleCatalog(_bundles(tmp_path, *platforms))
    service = DeviceAgentUpdateService(
        repository, sessions, runtime or FakeRuntime(), catalog, PUBLIC_URL, **options
    )
    return service, repository, sessions, capabilities


def test_catalog_indexes_one_bundle_per_platform_and_reports_faults(tmp_path: Path) -> None:
    root = _bundles(tmp_path, "linux_x86_64", "windows_amd64")
    make_bundle(root / "duplicate.zip", platform="linux_x86_64")
    (root / "broken.zip").write_bytes(b"nope")
    catalog = AgentBundleCatalog(root)
    assert sorted(catalog.bundles) == ["linux_x86_64", "windows_amd64"]
    bundle = catalog.bundles["windows_amd64"]
    assert bundle.digest_sha256 == sha256_file(root / "cueverge-agent-windows_amd64.zip")
    assert catalog.by_digest(bundle.digest_sha256) == bundle
    assert len(catalog.faults) == 2
    assert AgentBundleCatalog(None).bundles == {}
    assert "doesn't exist" in AgentBundleCatalog(tmp_path / "missing").faults[0]


@pytest.mark.parametrize(
    ("release", "agent_update", "state"),
    [
        (None, False, "unmanaged"),
        (_release(managed=False, bundle_digest=None), False, "unmanaged"),
        (_release(platform=None), True, "unsupported_platform"),
        (_release(platform="windows_amd64"), True, "no_bundle"),
        (_release(), True, "available"),
    ],
)
def test_reports_why_a_device_can_or_cannot_update(
    tmp_path: Path, release: AgentRelease | None, agent_update: bool, state: str
) -> None:
    service, repository, sessions, capabilities = _service(tmp_path, "linux_x86_64")
    [offline] = service.statuses()
    assert (offline.state, offline.connected) == ("disconnected", False)
    connect(repository, sessions, capabilities, release, agent_update=agent_update)
    [status] = service.statuses()
    assert status.state == state
    assert status.connected


def test_current_and_rolled_back_states(tmp_path: Path) -> None:
    service, repository, sessions, capabilities = _service(tmp_path, "linux_x86_64")
    digest = service.catalog.bundles["linux_x86_64"].digest_sha256
    connect(repository, sessions, capabilities, _release(bundle_digest=digest))
    assert service.statuses()[0].state == "current"
    rolled_back = AgentUpdateResult(
        bundle_digest=digest,
        outcome="rolled_back",
        detail="The new version exited with code 1 before connecting.",
        finished_at_unix_ms=1,
    )
    connect(repository, sessions, capabilities, _release(last_update=rolled_back))
    [status] = service.statuses()
    assert (status.state, status.detail) == ("rolled_back", rolled_back.detail)


def test_updates_require_idle_maintenance(tmp_path: Path) -> None:
    runtime = FakeRuntime(mode="automatic")
    service, repository, sessions, capabilities = _service(
        tmp_path, "linux_x86_64", runtime=runtime
    )
    connect(repository, sessions, capabilities, _release())
    with pytest.raises(AgentUpdatePreconditionError, match="maintenance"):
        asyncio.run(service.start())
    runtime.mode, runtime.active_scene = "maintenance", "ambient"
    with pytest.raises(AgentUpdatePreconditionError):
        asyncio.run(service.start())


def test_update_installs_restarts_and_completes_on_reconnect(tmp_path: Path) -> None:
    service, repository, sessions, capabilities = _service(tmp_path, "linux_x86_64")
    agent = connect(repository, sessions, capabilities, _release())
    bundle = service.catalog.bundles["linux_x86_64"]

    async def scenario() -> None:
        [started] = await service.start()
        assert (started.state, started.detail) == ("updating", "Downloading and installing.")
        for _ in range(100):
            await asyncio.sleep(0.01)
            if service.statuses()[0].detail == "Restarting into the new version.":
                break

    asyncio.run(scenario())
    [command] = agent.commands
    assert command.digest_sha256 == bundle.digest_sha256
    assert command.size_bytes == bundle.size_bytes
    assert str(command.download_url) == (
        f"https://192.0.2.10:8443/api/device/v2/agent-bundles/{bundle.digest_sha256}"
    )
    assert service.statuses()[0].state == "updating"
    connect(repository, sessions, capabilities, _release(bundle_digest=bundle.digest_sha256))
    assert service.statuses()[0].state == "current"
    audit = [(event.action, event.result) for event in repository.list_audit_events()]
    assert ("device.agent_update.install", "success") in audit
    with pytest.raises(AgentUpdateError, match="can't be updated"):
        asyncio.run(service.start([str(DEVICE_ID)]))
    assert asyncio.run(service.start()) == ()


def test_failed_installs_and_missing_reconnects_are_reported(tmp_path: Path) -> None:
    service, repository, sessions, capabilities = _service(
        tmp_path, "linux_x86_64", reconnect_timeout_seconds=0.05
    )
    connect(repository, sessions, capabilities, _release(), error="disk full")

    async def settle() -> None:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if service.statuses()[0].state != "updating":
                return

    async def failing() -> None:
        await service.start([str(DEVICE_ID)])
        await settle()

    asyncio.run(failing())
    [status] = service.statuses()
    assert (status.state, status.detail) == ("failed", "disk full")

    connect(repository, sessions, capabilities, _release())

    async def silent() -> None:
        await service.start()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if service.statuses()[0].detail == "Restarting into the new version.":
                break
        time.sleep(0.06)

    asyncio.run(silent())
    assert "didn't reconnect" in (service.statuses()[0].detail or "")
    with pytest.raises(AgentUpdateError, match="not found"):
        asyncio.run(service.start(["missing-device"]))


def test_device_listener_serves_bundles_only_to_enrolled_media_agents(tmp_path: Path) -> None:
    repository, certificate, _ = _enrolled_device(tmp_path)
    catalog = AgentBundleCatalog(_bundles(tmp_path, "linux_x86_64"))
    bundle = catalog.bundles["linux_x86_64"]
    app = create_device_app(repository, DeviceSessionRegistry(), agent_bundles=catalog)
    url = f"/api/device/v2/agent-bundles/{bundle.digest_sha256}"
    with TestClient(app) as anonymous:
        assert anonymous.get(url).status_code == 403
        assert (
            anonymous.get("/api/device/v2/agent-bundles?platform=linux_x86_64").status_code == 403
        )
    with TestClient(_with_peer_certificate(app, certificate)) as client:
        metadata = client.get("/api/device/v2/agent-bundles?platform=linux_x86_64").json()
        assert metadata["download_path"] == url
        assert metadata["version"] == "2.0.0"
        assert client.get("/api/device/v2/agent-bundles?platform=windows_amd64").status_code == 404
        download = client.get(url)
        assert download.status_code == 200
        assert download.content == bundle.path.read_bytes()
        assert client.get(url, headers={"Range": "bytes=10-"}).content == download.content[10:]
        assert client.get(f"/api/device/v2/agent-bundles/{'f' * 64}").status_code == 403


def test_device_update_scope_is_for_global_administrators_only() -> None:
    assert McpScope.DEVICE_UPDATE in allowed_mcp_scopes(Role.ADMINISTRATOR, None, True)
    for role in (Role.OPERATOR, Role.PUBLISHER, Role.DESIGNER):
        assert McpScope.DEVICE_UPDATE not in allowed_mcp_scopes(role, Role.ADMINISTRATOR, True)


def test_gui_and_mcp_list_and_start_agent_updates(tmp_path: Path) -> None:
    from cueverge.controller.app import create_app
    from cueverge.controller.runtime import ControllerRuntime, ShowMode
    from cueverge.gui.service import GuiServices
    from tests.unit.test_device_enrollment_api import _config, _login, _mutation_headers

    _, _, capabilities = _enrolled_device(tmp_path)
    runtime = ControllerRuntime()
    config = _config(tmp_path, agent_bundle_root=_bundles(tmp_path, "linux_x86_64"))
    services = GuiServices.create(config, runtime)
    services.repository.mark_device_connected(
        device_id=str(DEVICE_ID), session_id="s", observed_at_ms=time.time_ns() // 1_000_000
    )
    agent = connect(services.repository, services.device_sessions, capabilities, _release())
    now = datetime.now(UTC)
    installation_id = services.repository.get_runtime_installation().id

    def token(scopes: tuple[McpScope, ...]) -> str:
        return services.mcp_store.create_installation_token(
            client_id="agent-updates",
            username="admin",
            installation_id=installation_id,
            scopes=scopes,
            expires_at=(now + timedelta(hours=1)).isoformat(),
            occurred_at=now.isoformat(),
        ).token

    read = token((McpScope.READ,))
    updater = token((McpScope.READ, McpScope.DEVICE_UPDATE))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:

        def tool(secret: str, name: str, **arguments: object) -> dict[str, Any]:
            result = client.post(
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
            assert result.status_code == 200, result.text
            return dict(result.json()["result"])

        listed = tool(read, "get_agent_updates")["structuredContent"]
        assert listed["devices"][0]["state"] == "available"
        assert listed["bundles"][0]["platform"] == "linux_x86_64"
        assert tool(read, "update_device_agents", confirm_restart=True)["isError"]
        assert tool(updater, "update_device_agents")["isError"]
        refused = tool(updater, "update_device_agents", confirm_restart=True)
        assert refused["isError"] and "maintenance" in refused["content"][0]["text"]

        _login(client)
        overview = client.get("/api/gui/v1/devices/agent-updates").json()
        assert overview["devices"][0]["state"] == "available"
        node = next(
            item
            for item in client.get("/api/gui/v1/topology").json()["nodes"]
            if item["id"] == f"device:{DEVICE_ID}"
        )
        assert node["agent_update"]["state"] == "available"
        rejected = client.post(
            "/api/gui/v1/devices/agent-updates", json={}, headers=_mutation_headers(client)
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "agent_update_requires_idle_maintenance"

        asyncio.run(runtime.set_show_mode(ShowMode.MAINTENANCE))
        started = client.post(
            "/api/gui/v1/devices/agent-updates",
            json={"device_ids": [str(DEVICE_ID)]},
            headers=_mutation_headers(client),
        )
        assert started.status_code == 200, started.text
        assert started.json()["devices"][0]["state"] == "updating"
        for _ in range(100):
            if agent.commands:
                break
            time.sleep(0.01)
        assert len(agent.commands) == 1

        connect(services.repository, services.device_sessions, capabilities, _release())
        services.agent_updates._pending.clear()
        result = tool(updater, "update_device_agents", confirm_restart=True)["structuredContent"]
        assert result["started"] == [str(DEVICE_ID)]
        assert result["hardware_operation_requested"] is True

    actions = {(event.action, event.result) for event in services.repository.list_audit_events()}
    assert ("device.agent_update.start", "error") in actions
    assert ("device.agent_update.start", "success") in actions
    assert ("mcp.device.agent_update.start", "success") in actions
