import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cueverge.config import FrigateMqttConfig
from cueverge.controller.app import create_app
from cueverge.gui.contracts import DraftProject, OperateActionRequest, Role
from cueverge.gui.home_assistant import HomeAssistantBridge, HomeAssistantError
from cueverge.gui.mcp_authoring import McpScope
from tests.unit.test_gui_foundation import (
    ORIGIN,
    PASSWORD,
    PASSWORD_HASH,
    _draft,
    _login,
    _mcp_fixture,
    _mcp_token,
    _mcp_tool,
    _mutation_headers,
)


def _bound_project(services: Any, name: str, installation_id: str | None) -> str:
    metadata = {"installation_id": installation_id} if installation_id else {}
    return str(
        services.repository.create_project(
            name=name,
            draft=DraftProject.model_validate(_draft(editor_metadata=metadata)),
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=datetime.now(UTC).isoformat(),
        ).project.id
    )


def _expiry(days: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def test_installation_tokens_select_shows_per_call_and_respect_boundaries(
    tmp_path: Path,
) -> None:
    runtime, services, unbound = _mcp_fixture(tmp_path)
    installation_id = services.repository.get_runtime_installation().id
    preshow = _bound_project(services, "Pre-show", installation_id)
    night = _bound_project(services, "Halloween Night", installation_id)
    services.collaboration.upsert_user(
        "designer", PASSWORD_HASH, Role.DESIGNER, False, datetime.now(UTC).isoformat()
    )
    show_token = _mcp_token(services, preshow, (McpScope.READ,))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        assert client.get("/api/gui/v1/integrations/mcp-tokens").status_code == 401
        login = client.post(
            "/api/gui/v1/auth/login",
            json={"username": "designer", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200
        denied = client.post(
            "/api/gui/v1/integrations/mcp-tokens",
            json={"client_id": "chatgpt", "scopes": ["read"], "expires_at": _expiry()},
            headers=_mutation_headers(client),
        )
        assert denied.status_code == 403
        client.cookies.clear()
        _login(client)
        body = {
            "client_id": "chatgpt-tunnel",
            "scopes": ["read", "direct_edit", "live_operation"],
            "expires_at": _expiry(),
        }
        assert client.post("/api/gui/v1/integrations/mcp-tokens", json=body).status_code == 403
        for invalid in ({**body, "scopes": ["direct_edit"]}, {**body, "expires_at": _expiry(91)}):
            response = client.post(
                "/api/gui/v1/integrations/mcp-tokens",
                json=invalid,
                headers=_mutation_headers(client),
            )
            assert response.status_code == 422
        created = client.post(
            "/api/gui/v1/integrations/mcp-tokens", json=body, headers=_mutation_headers(client)
        )
        assert created.status_code == 201, created.text
        record = created.json()
        token = record["token"]
        assert record["installation_id"] == installation_id
        assert record["project_id"] == ""
        listed = client.get("/api/gui/v1/integrations/mcp-tokens").json()
        assert [item["id"] for item in listed] == [record["id"]]
        assert all(item["token"] is None for item in listed)
        assert (
            client.get(f"/api/gui/v1/projects/{preshow}/mcp-tokens").json()[0]["id"]
            != (record["id"])
        )

        tools = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        ).json()["result"]["tools"]
        by_name = {item["name"]: item for item in tools}
        assert "list_shows" in by_name
        draft_schema = by_name["get_project_draft"]["inputSchema"]["properties"]
        assert "project_id" in draft_schema and "revision" in draft_schema
        assert "list_shows" in draft_schema["project_id"]["description"]

        shows = _mcp_tool(client, token, "list_shows")["structuredContent"]
        assert shows["token_scope"] == "installation"
        assert {item["project_id"] for item in shows["shows"]} == {preshow, night}
        assert unbound not in {item["project_id"] for item in shows["shows"]}

        missing = _mcp_tool(client, token, "get_project_draft")
        assert missing["isError"] is True
        assert "list_shows" in missing["content"][0]["text"]
        for project_id, name in ((preshow, "Pre-show"), (night, "Halloween Night")):
            read = _mcp_tool(client, token, "get_project_structure", project_id=project_id)
            assert read["isError"] is False
            assert read["structuredContent"]["name"] == name
        foreign = _mcp_tool(client, token, "get_project_structure", project_id=unbound)
        assert foreign["isError"] is True
        assert "not bound" in foreign["content"][0]["text"]
        nothing_loaded = _mcp_tool(
            client,
            token,
            "operate_loaded_show",
            request={"action": "stop"},
            confirm_physical_output=True,
        )
        assert nothing_loaded["isError"] is True
        assert "no show is currently loaded" in nothing_loaded["content"][0]["text"]
        assert _mcp_tool(client, token, "get_runtime_status")["isError"] is False
        setup = _mcp_tool(client, token, "get_installation_setup")
        assert setup["isError"] is False
        capabilities = _mcp_tool(client, token, "get_capabilities", project_id=night)
        assert capabilities["structuredContent"]["token_scope"] == "installation"
        assert capabilities["structuredContent"]["project_id"] == night

        assert _mcp_tool(client, show_token, "get_project_structure")["isError"] is False
        mismatch = _mcp_tool(client, show_token, "get_project_structure", project_id=night)
        assert mismatch["isError"] is True
        assert "scoped to show" in mismatch["content"][0]["text"]
        own = _mcp_tool(client, show_token, "list_shows")["structuredContent"]
        assert [item["project_id"] for item in own["shows"]] == [preshow]

        revoked = client.delete(
            f"/api/gui/v1/integrations/mcp-tokens/{record['id']}",
            headers=_mutation_headers(client),
        )
        assert revoked.status_code == 204
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_shows", "arguments": {}},
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert response.status_code == 401
        audit = {item.action for item in services.repository.list_audit_events()}
        assert {"mcp.installation_token.create", "mcp.installation_token.revoke"} <= audit
        assert runtime.active_scene is None


def test_installation_token_checks_current_show_role_per_call(tmp_path: Path) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    installation_id = services.repository.get_runtime_installation().id
    project_id = _bound_project(services, "Night", installation_id)
    created = services.mcp_store.create_installation_token(
        client_id="tunnel",
        username="admin",
        installation_id=installation_id,
        scopes=(McpScope.READ,),
        expires_at=_expiry(),
        occurred_at=datetime.now(UTC).isoformat(),
    )
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        result = _mcp_tool(
            client,
            created.token,
            "update_project_draft",
            base_revision=1,
            draft={},
            project_id=project_id,
        )
        assert result["isError"] is True
        assert "direct_edit" in result["content"][0]["text"]
        other = services.mcp_store.create_installation_token(
            client_id="elsewhere",
            username="admin",
            installation_id="another-installation",
            scopes=(McpScope.READ,),
            expires_at=_expiry(),
            occurred_at=datetime.now(UTC).isoformat(),
        )
        wrong = _mcp_tool(client, other.token, "list_shows")
        assert wrong["isError"] is True
        assert "different installation" in wrong["content"][0]["text"]


class _FakeConnection:
    def __init__(self, broker: "_FakeBroker") -> None:
        self.broker = broker

    async def publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        self.broker.published.append((topic, payload, retain))
        if retain:
            self.broker.retained[topic] = payload

    async def subscribe(self, topic: str) -> None:
        self.broker.subscriptions.append(topic)

    async def messages(self) -> AsyncIterator[tuple[str, bytes]]:
        while True:
            yield await self.broker.inbox.get()


class _FakeBroker:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []
        self.retained: dict[str, str] = {}
        self.subscriptions: list[str] = []
        self.inbox: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self.wills: list[str] = []
        self.connections = 0

    @asynccontextmanager
    async def connect(
        self, config: FrigateMqttConfig, availability: str
    ) -> AsyncIterator[_FakeConnection]:
        self.connections += 1
        self.wills.append(availability)
        try:
            yield _FakeConnection(self)
        finally:
            self.retained[availability] = "offline"


def _status(scene: str | None = None, mode: str = "disabled") -> dict[str, object]:
    return {
        "active_scene": scene,
        "playback_state": "playing" if scene else None,
        "degraded": False,
        "loaded_catalog": {"project_name": "Pre-show", "project_id": "p1", "revision": 4},
        "operations": {
            "mode": mode,
            "scene_options": [
                {"id": "klown-ship", "name": "Klown Ship"},
                {"id": "ambush", "name": "Shorty's Ambush"},
            ],
        },
    }


async def _until(condition: Any, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def test_home_assistant_bridge_discovers_publishes_state_and_runs_commands(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        broker = _FakeBroker()
        status = {"value": _status()}
        operations: list[OperateActionRequest] = []
        audits: list[tuple[str, str, dict[str, Any]]] = []
        broker_config = {"value": FrigateMqttConfig(enabled=True, host="mqtt.local")}

        async def operate(request: OperateActionRequest) -> None:
            if request.action == "pause":
                raise RuntimeError("Apply is in progress. Stop and Blackout remain available.")
            operations.append(request)

        bridge = HomeAssistantBridge(
            tmp_path / "home-assistant",
            installation_id="home",
            installation_name=lambda: "Home",
            status=lambda: status["value"],
            operate=operate,
            broker=lambda: broker_config["value"],
            audit=lambda action, result, context: audits.append((action, result, context)),
            connector=broker.connect,
            reconnect_delay_s=0.01,
        )
        bridge.start()
        await asyncio.sleep(0.05)
        assert broker.connections == 0
        assert bridge.snapshot()["state"] == "disabled"
        with pytest.raises(HomeAssistantError, match="changed elsewhere"):
            await bridge.configure(enabled=True, expected_revision=5)
        snapshot = await bridge.configure(enabled=True, expected_revision=0)
        assert snapshot["enabled"] is True
        await _until(lambda: "cueverge/home/state" in broker.retained)
        assert bridge.snapshot()["state"] == "connected"
        assert broker.wills == ["cueverge/home/availability"]
        assert broker.retained["cueverge/home/availability"] == "online"
        assert set(broker.subscriptions) == {
            "cueverge/home/command",
            "cueverge/home/scene/set",
            "cueverge/home/mode/set",
        }
        discovery = {
            topic: json.loads(payload)
            for topic, payload in broker.retained.items()
            if topic.startswith("homeassistant/") and payload
        }
        prefix = "homeassistant/{}/cueverge_home/{}/config"
        for key in ("start", "stop", "pause", "resume", "blackout"):
            button = discovery[prefix.format("button", key)]
            assert button["payload_press"] == key
            assert button["command_topic"] == "cueverge/home/command"
            assert button["device"]["identifiers"] == ["cueverge_home"]
            assert button["availability_topic"] == "cueverge/home/availability"
        for key in ("playback", "active_scene", "loaded_show"):
            assert discovery[prefix.format("sensor", key)]["state_topic"] == "cueverge/home/state"
        assert discovery[prefix.format("select", "mode")]["options"] == [
            "Automatic",
            "Stopped",
            "Maintenance",
            "Blackout",
        ]
        assert discovery[prefix.format("select", "scene")]["options"] == [
            "Klown Ship",
            "Shorty's Ambush",
        ]
        state = json.loads(broker.retained["cueverge/home/state"])
        assert state == {
            "playback": "idle",
            "active_scene": None,
            "active_scene_name": None,
            "mode": "disabled",
            "mode_label": "Stopped",
            "loaded_show": "Pre-show",
            "loaded_revision": 4,
            "degraded": False,
        }

        for topic, payload in (
            ("cueverge/home/command", b"start"),
            ("cueverge/home/scene/set", b"Shorty's Ambush"),
            ("cueverge/home/mode/set", b"Maintenance"),
            ("cueverge/home/command", b"BLACKOUT"),
            ("cueverge/home/command", b"pause"),
            ("cueverge/home/command", b"reboot"),
            ("cueverge/home/scene/set", b"Unknown scene"),
            ("cueverge/home/command", b"x" * 300),
        ):
            await broker.inbox.put((topic, payload))
        await _until(lambda: bridge.commands_received == 8)
        assert [(item.action, item.scene_id) for item in operations] == [
            ("start", None),
            ("start", "ambush"),
            ("maintenance", None),
            ("blackout", None),
        ]
        assert bridge.commands_rejected == 4
        assert [action for action, result, _ in audits if result == "success"] == [
            "mqtt.operate.start",
            "mqtt.operate.start",
            "mqtt.operate.maintenance",
            "mqtt.operate.blackout",
        ]
        rejected = [context["message"] for _, result, context in audits if result == "error"]
        assert "Apply is in progress" in rejected[0]
        assert "Unknown command" in rejected[1]
        assert "not in the loaded show" in rejected[2]
        assert "too large" in rejected[3]

        status["value"] = _status("ambush", "automatic")
        await _until(
            lambda: (
                json.loads(broker.retained["cueverge/home/state"])["active_scene_name"]
                == "Shorty's Ambush"
            )
        )
        assert json.loads(broker.retained["cueverge/home/state"])["mode_label"] == "Automatic"

        broker_config["value"] = FrigateMqttConfig(enabled=True, host="mqtt.other")
        await _until(lambda: broker.connections == 2)

        await bridge.configure(enabled=False, expected_revision=None)
        assert bridge.snapshot()["state"] == "disabled"
        for topic in discovery:
            assert broker.retained[topic] == ""
        assert broker.retained["cueverge/home/availability"] == "offline"
        published = len(broker.published)
        await broker.inbox.put(("cueverge/home/command", b"start"))
        await asyncio.sleep(0.05)
        assert len(operations) == 4
        assert len(broker.published) == published
        await bridge.stop()

        restored = HomeAssistantBridge(
            tmp_path / "home-assistant",
            installation_id="home",
            installation_name=lambda: "Home",
            status=lambda: status["value"],
            operate=operate,
            broker=lambda: broker_config["value"],
            audit=lambda *_: None,
            connector=broker.connect,
        )
        assert restored.settings.enabled is False
        assert restored.settings.revision == 2

    asyncio.run(scenario())


def test_home_assistant_rest_and_mcp_share_admin_toggle(tmp_path: Path) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    installation_id = services.repository.get_runtime_installation().id
    project_id = _bound_project(services, "Night", installation_id)
    read = _mcp_token(services, project_id, (McpScope.READ,))
    admin = _mcp_token(services, project_id, (McpScope.READ, McpScope.INPUT_CONFIG))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        assert client.get("/api/gui/v1/integrations/home-assistant").status_code == 401
        _login(client)
        status = client.get("/api/gui/v1/integrations/home-assistant").json()
        assert status["enabled"] is False
        assert status["state"] == "disabled"
        assert status["topics"]["command"] == f"cueverge/{installation_id}/command"
        assert (
            client.put(
                "/api/gui/v1/integrations/home-assistant", json={"enabled": True}
            ).status_code
            == 403
        )
        enabled = client.put(
            "/api/gui/v1/integrations/home-assistant",
            json={"enabled": True, "expected_revision": 0},
            headers=_mutation_headers(client),
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["state"] == "unavailable"
        assert "Installation > Inputs" in enabled.json()["error"]
        stale = client.put(
            "/api/gui/v1/integrations/home-assistant",
            json={"enabled": False, "expected_revision": 0},
            headers=_mutation_headers(client),
        )
        assert stale.status_code == 409

        capabilities = _mcp_tool(client, read, "get_capabilities")["structuredContent"]
        assert capabilities["home_assistant"]["configure_tool"] == "configure_home_assistant"
        mcp_status = _mcp_tool(client, read, "get_home_assistant_integration")
        assert mcp_status["structuredContent"]["enabled"] is True
        assert _mcp_tool(client, read, "configure_home_assistant", enabled=False)["isError"] is True
        disabled = _mcp_tool(
            client, admin, "configure_home_assistant", enabled=False, expected_revision=1
        )
        assert disabled["isError"] is False
        assert disabled["structuredContent"]["enabled"] is False
        audit = [
            item
            for item in services.repository.list_audit_events()
            if item.action == "home_assistant.configure"
        ]
        assert len(audit) == 2
        assert runtime.active_scene is None
