import asyncio
import json
import os
import stat
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiomqtt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cueverge.config import ControllerConfig, FrigateMqttConfig
from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.encounter import ObservedEventOutcome
from cueverge.core.events import ExternalEvent
from cueverge.core.models import AudiencePresenceDefinition
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import ConfigureInputSourceRequest, DraftProject, Role
from cueverge.gui.input_sources import InputSourceError, InputSourceService
from cueverge.gui.mcp_authoring import McpScope, allowed_mcp_scopes
from cueverge.gui.passwords import hash_password
from cueverge.gui.repository import GuiRepository
from cueverge.gui.service import GuiServices
from cueverge.integrations.frigate import FrigateEventNormalizer
from cueverge.integrations.mqtt import (
    MAX_INPUT_PAYLOAD_BYTES,
    MAX_INPUT_RECENT_EVENTS,
    MqttConnection,
)

ORIGIN = "http://localhost:5173"


def runtime_with_visitors() -> ControllerRuntime:
    base = ControllerRuntime()
    catalog = base.catalog.model_copy(
        update={
            "encounter": None,
            "opportunities": None,
            "audience": AudiencePresenceDefinition(
                schema_version=1,
                cameras=("porch",),
                zones=("walkway",),
                arrival_confirmation_ms=0,
            ),
        }
    )
    return ControllerRuntime(catalog=catalog)


def payload(*, camera: str = "porch", label: str = "person", age_ms: int = 0) -> dict[str, Any]:
    timestamp = (time.time_ns() // 1_000_000 - age_ms) / 1000
    return {
        "type": "new",
        "after": {
            "id": f"original-example-{camera}-{age_ms}",
            "camera": camera,
            "label": label,
            "start_time": timestamp,
            "frame_time": timestamp,
            "entered_zones": ["walkway"],
        },
    }


def test_input_credentials_are_private_separate_durable_rotatable_and_write_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "input-source"
        runtime = runtime_with_visitors()
        service = InputSourceService(root, runtime, handler=runtime.ingest_event_with_presence)
        first = await service.configure(
            ConfigureInputSourceRequest(
                host="localhost",
                username="mqtt-user",
                password=SecretStr("original-test-secret"),
            )
        )
        assert first["credential_present"] is True
        assert "original-test-secret" not in json.dumps(first)
        assert "original-test-secret" not in (root / "settings.json").read_text()
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        for file in root.iterdir():
            assert stat.S_IMODE(file.stat().st_mode) == 0o600
        restored = InputSourceService(root, runtime, handler=runtime.ingest_event_with_presence)
        assert restored.snapshot()["credential_present"] is True
        assert restored.snapshot()["configuration_revision"] == 1
        await restored.configure(ConfigureInputSourceRequest(host="other", username="mqtt-user"))
        assert restored.snapshot()["credential_present"] is True
        await restored.configure(
            ConfigureInputSourceRequest(
                host="other",
                username="mqtt-user",
                password=SecretStr("rotated-test-secret"),
            )
        )
        credentials = list(root.glob("credential-*.json"))
        assert len(credentials) == 1
        assert "original-test-secret" not in credentials[0].read_text()
        await restored.configure(ConfigureInputSourceRequest(host="other", clear_password=True))
        assert not list(root.glob("credential-*.json"))
        assert restored.snapshot()["credential_present"] is False

    asyncio.run(scenario())


def test_fresh_gui_data_root_accepts_protected_input_under_group_writable_umask(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        data_root = tmp_path / "gui"
        previous_umask = os.umask(0o007)
        try:
            GuiRepository(data_root / "gui.sqlite3").migrate()
            service = InputSourceService(data_root / "input-source", ControllerRuntime())
            await service.configure(
                ConfigureInputSourceRequest(
                    enabled=True,
                    host="broker.local",
                    username="mqtt",
                    password=SecretStr("test-secret"),
                )
            )
        finally:
            os.umask(previous_umask)
        assert stat.S_IMODE(data_root.stat().st_mode) == 0o700
        assert stat.S_IMODE((data_root / "input-source").stat().st_mode) == 0o700

    asyncio.run(scenario())


def test_input_storage_failures_do_not_replace_active_settings(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime()
        service = InputSourceService(tmp_path / "inputs", runtime)
        await service.configure(ConfigureInputSourceRequest(host="first"))
        service.root.chmod(0o755)
        with pytest.raises(InputSourceError, match="active settings are unchanged"):
            await service.configure(ConfigureInputSourceRequest(host="second"))
        assert service.snapshot()["settings"]["host"] == "first"
        failed = InputSourceService(service.root, runtime)
        assert failed.snapshot()["state"] == "unavailable"
        service.root.chmod(0o700)

    asyncio.run(scenario())


def test_missing_credential_is_explicit_and_does_not_fall_back_to_anonymous(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime()
        service = InputSourceService(tmp_path / "inputs", runtime)
        await service.configure(
            ConfigureInputSourceRequest(
                host="first",
                username="test",
                password=SecretStr("test-secret"),
            )
        )
        next(service.root.glob("credential-*.json")).unlink()
        restored = InputSourceService(service.root, runtime)
        assert restored.snapshot()["state"] == "unavailable"
        assert "credentials cannot be read" in restored.snapshot()["error"]
        with pytest.raises(InputSourceError):
            await restored.configure(ConfigureInputSourceRequest(host="first", enabled=True))

    asyncio.run(scenario())


def test_environment_input_is_read_only_and_never_uses_gui_storage(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime()
        service = InputSourceService(
            tmp_path / "inputs",
            runtime,
            mode="environment",
            environment=FrigateMqttConfig(
                host="operator-broker",
                topic="events/" + "part" * 80,
                accepted_labels=tuple(f"label-{index}" for index in range(60)),
                username="operator",
                password=SecretStr("environment-secret"),
            ),
        )
        assert service.snapshot()["management"] == "environment"
        assert "environment-secret" not in json.dumps(service.snapshot())
        with pytest.raises(InputSourceError, match="environment-managed"):
            await service.configure(ConfigureInputSourceRequest(host="gui-broker"))
        assert not service.root.exists()

    asyncio.run(scenario())


def test_input_reconfiguration_uses_revision_guard_and_validates_before_replacing(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = InputSourceService(tmp_path / "inputs", ControllerRuntime())
        await service.configure(ConfigureInputSourceRequest(host="first", expected_revision=0))
        with pytest.raises(InputSourceError, match="changed elsewhere"):
            await service.configure(ConfigureInputSourceRequest(host="second", expected_revision=0))
        with pytest.raises(InputSourceError, match="requires a broker username"):
            await service.configure(
                ConfigureInputSourceRequest(host="second", password=SecretStr("test-secret"))
            )
        assert service.snapshot()["settings"]["host"] == "first"
        assert service.snapshot()["configuration_revision"] == 1

    asyncio.run(scenario())


def test_input_reception_reconnect_filter_rejection_freshness_and_qualification(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = runtime_with_visitors()
        queued: asyncio.Queue[bytes | None] = asyncio.Queue()
        attempts = 0
        topics: list[str] = []

        class Connection:
            async def subscribe(self, topic: str) -> None:
                topics.append(topic)

            async def messages(self) -> AsyncIterator[bytes]:
                while True:
                    message = await queued.get()
                    if message is None:
                        raise aiomqtt.MqttError("original synthetic disconnected stream")
                    yield message

        @asynccontextmanager
        async def connector(_: FrigateMqttConfig) -> AsyncIterator[MqttConnection]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise aiomqtt.MqttError("original synthetic connection failure")
            yield Connection()

        service = InputSourceService(
            tmp_path / "inputs",
            runtime,
            connector=connector,
            handler=runtime.ingest_event_with_presence,
        )
        await service.configure(ConfigureInputSourceRequest(host="loopback-example", enabled=True))
        service._config = service._config.model_copy(update={"reconnect_delay_ms": 100})
        service._ingress = service._make_ingress(service._config)
        service.start()
        try:

            async def until(predicate: Any) -> None:
                async with asyncio.timeout(2):
                    while not predicate():
                        await asyncio.sleep(0.01)

            await until(lambda: service.snapshot()["state"] == "subscribed")
            assert attempts == 2
            assert service.snapshot()["freshness"] == "no_events"
            for example in (
                b"not-json",
                json.dumps(payload(label="cat")).encode(),
                json.dumps(payload(camera="other")).encode(),
                json.dumps(payload(age_ms=10000)).encode(),
                json.dumps(payload()).encode(),
            ):
                await queued.put(example)
            await until(
                lambda: (
                    service.snapshot()["messages_received"] == 5
                    and service.snapshot()["audience_matches"] == 1
                )
            )
            health = service.snapshot()
            assert health["invalid_messages"] == 1
            assert health["filtered_messages"] == 1
            assert health["controller_rejected"] >= 2
            assert health["controller_accepted"] >= 1
            assert health["freshness"] == "fresh"
            assert runtime.audience is not None and runtime.audience.state.value == "present"
            reasons = {row.get("reason") for row in health["recent_events"]}
            assert {
                "invalid_payload",
                "label_filtered",
                "camera_not_matched",
                "stale_observation",
            } <= reasons
            assert topics == ["frigate/events"]
            await queued.put(None)
            await until(lambda: attempts == 3 and service.snapshot()["state"] == "subscribed")
            assert service.snapshot()["messages_received"] == 5
            assert service.snapshot()["audience_matches"] == 1
            assert topics == ["frigate/events", "frigate/events"]
            await service.configure(
                ConfigureInputSourceRequest(host="loopback-example", enabled=False)
            )
            assert service.snapshot()["state"] == "disabled"
        finally:
            await service.stop()
            await runtime.shutdown()

    asyncio.run(scenario())


def test_input_example_uses_isolated_authoritative_rules_without_live_mutation(
    tmp_path: Path,
) -> None:
    runtime = runtime_with_visitors()
    service = InputSourceService(
        tmp_path / "inputs", runtime, handler=runtime.ingest_event_with_presence
    )
    before = runtime.status()
    matching = service.validate_payload(payload())
    assert matching["simulation"] and matching["hardware_commands_sent"] is False
    assert any(event["audience_relevant"] for event in matching["events"])
    assert not any(
        event["audience_relevant"]
        for event in service.validate_payload(payload(camera="other"))["events"]
    )
    assert all(
        event["stale"] for event in service.validate_payload(payload(age_ms=10000))["events"]
    )
    assert service.validate_payload(payload(label="cat"))["label_filtered"] is True
    assert runtime.status()["audience"] == before["audience"]
    assert service.snapshot()["messages_received"] == 0
    with pytest.raises(InputSourceError, match="Invalid Frigate"):
        service.validate_payload({"type": "invalid"})


def test_input_payload_and_diagnostic_limits_are_exact(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = InputSourceService(tmp_path / "inputs", ControllerRuntime())
        await service.configure(ConfigureInputSourceRequest(host="loopback-example", enabled=True))
        assert service._ingress is not None
        example = payload()
        example["padding"] = ""
        example["padding"] = "x" * (MAX_INPUT_PAYLOAD_BYTES - len(json.dumps(example).encode()))
        packet = json.dumps(example).encode()
        assert len(packet) == MAX_INPUT_PAYLOAD_BYTES
        await service._ingress._process(packet)
        assert service.snapshot()["invalid_messages"] == 0
        service.validate_payload(example)
        await service._ingress._process(packet + b" ")
        assert service.snapshot()["invalid_messages"] == 1
        assert service.snapshot()["recent_events"][-1]["reason"] == "payload_too_large"
        example["padding"] += "x"
        with pytest.raises(InputSourceError, match="256 KiB"):
            service.validate_payload(example)
        for _ in range(MAX_INPUT_RECENT_EVENTS + 1):
            await service._ingress._process(b"invalid")
        assert len(service.snapshot()["recent_events"]) == MAX_INPUT_RECENT_EVENTS

    asyncio.run(scenario())


def test_input_matching_is_attributed_to_this_event_not_other_controller_traffic(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = runtime_with_visitors()
        other = FrigateEventNormalizer().normalize(
            json.dumps(payload()),
            received_at_ms=time.time_ns() // 1_000_000,
        )[-1]

        async def deliver(event: ExternalEvent) -> ObservedEventOutcome:
            observed = await runtime.ingest_event_with_presence(event)
            await runtime.ingest_event(other)
            return observed

        service = InputSourceService(tmp_path / "inputs", runtime, handler=deliver)
        await service.configure(ConfigureInputSourceRequest(host="loopback-example", enabled=True))
        assert service._ingress is not None
        await service._ingress._process(json.dumps(payload(camera="other")).encode())
        assert runtime.audience is not None
        assert runtime.audience.relevant_observations > 0
        assert service.snapshot()["audience_matches"] == 0
        assert all(row["audience_relevant"] is False for row in service.snapshot()["recent_events"])
        await runtime.shutdown()

    asyncio.run(scenario())


def test_input_freshness_expires_and_does_not_claim_connected_means_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        now = [time.time_ns() // 1_000_000]
        monkeypatch.setattr(time, "time_ns", lambda: now[0] * 1_000_000)
        runtime = runtime_with_visitors()
        runtime.event_clock = lambda: now[0]
        service = InputSourceService(
            tmp_path / "inputs", runtime, handler=runtime.ingest_event_with_presence
        )
        await service.configure(ConfigureInputSourceRequest(host="loopback-example", enabled=True))
        assert service._ingress is not None
        assert service.snapshot()["freshness"] == "no_events"
        await service._ingress._process(json.dumps(payload()).encode())
        assert service.snapshot()["freshness"] == "fresh"
        assert service.snapshot()["audience_matches"] == 1
        now[0] += 5001
        assert service.snapshot()["freshness"] == "stale"
        await runtime.tick_encounter()
        assert runtime.audience is not None
        assert runtime.audience.status()["active_objects"] == 0
        await runtime.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("timestamp", [float("inf"), float("nan")])
def test_input_rejects_nonfinite_source_timestamps_without_crashing(
    tmp_path: Path,
    timestamp: float,
) -> None:
    example = payload()
    example["after"]["frame_time"] = timestamp
    service = InputSourceService(tmp_path / "inputs", ControllerRuntime())
    with pytest.raises(InputSourceError, match="Invalid Frigate"):
        service.validate_payload(example)


def test_input_api_and_mcp_are_bound_scoped_secret_free_and_csrf_protected(tmp_path: Path) -> None:
    runtime = runtime_with_visitors()
    services = GuiServices.create(
        GuiConfig(
            data_root=tmp_path,
            admin_password_hash=SecretStr(hash_password("original test login")),
            allowed_origins=(ORIGIN,),
            secure_cookies=False,
        ),
        runtime,
    )
    draft = DraftProject.model_validate_json(
        Path("tests/fixtures/usability-authoring.json").read_text()
    )
    draft = draft.model_copy(
        update={
            "editor_metadata": {
                **draft.editor_metadata,
                "installation_id": services.repository.get_runtime_installation().id,
            }
        }
    )
    project = services.repository.create_project(
        name="Original inputs test",
        draft=draft,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )

    def token(scopes: tuple[McpScope, ...]) -> str:
        return services.mcp_store.create_token(
            client_id="original-input-test",
            username="admin",
            project_id=project.project.id,
            scopes=scopes,
            expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            occurred_at=datetime.now(UTC).isoformat(),
        ).token

    def tool(client: TestClient, credential: str, name: str, **arguments: Any) -> dict[str, Any]:
        result = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers={
                "Authorization": f"Bearer {credential}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert result.status_code == 200
        return result.json()["result"]

    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        assert client.get("/api/gui/v1/input-source").status_code == 401
        client.post(
            "/api/gui/v1/auth/login",
            json={"username": "admin", "password": "original test login"},
            headers={"Origin": ORIGIN},
        )
        assert client.put("/api/gui/v1/input-source", json={"host": "first"}).status_code == 403
        headers = {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["cueverge_csrf"]}
        configured = client.put(
            "/api/gui/v1/input-source",
            json={
                "host": "loopback-example",
                "username": "test",
                "password": "original-private-input-secret",
            },
            headers=headers,
        )
        assert configured.status_code == 200
        assert "original-private-input-secret" not in configured.text
        assert configured.json()["credential_present"] is True
        invalid = client.put(
            "/api/gui/v1/input-source",
            json={
                "host": "bad/url",
                "password": "original-private-input-secret",
            },
            headers=headers,
        )
        assert invalid.status_code == 422
        assert "original-private-input-secret" not in invalid.text
        direct = token((McpScope.READ, McpScope.DIRECT_EDIT))
        assert (
            tool(client, direct, "configure_input_source", configuration={"host": "blocked"})[
                "isError"
            ]
            is True
        )
        admin = token((McpScope.READ, McpScope.INPUT_CONFIG, McpScope.SIMULATION))
        capabilities = tool(client, admin, "get_capabilities")["structuredContent"]
        assert capabilities["input_source"]["configure_scope"] == "input_config"
        assert "writeOnly" in json.dumps(capabilities["input_source"]["configuration_schema"])
        checked = tool(client, admin, "validate_input_source_payload", payload=payload())[
            "structuredContent"
        ]
        assert checked["hardware_commands_sent"] is False
        assert services.input_source.snapshot()["messages_received"] == 0
        read = tool(client, admin, "get_input_source")["structuredContent"]
        assert read["settings"] == configured.json()["settings"]
        changed = tool(
            client,
            admin,
            "configure_input_source",
            configuration={
                "host": "second",
                "username": "test",
                "expected_revision": 1,
            },
        )["structuredContent"]
        assert changed["credential_present"] is True
        assert "original-private-input-secret" not in json.dumps(changed)
        assert client.get("/api/gui/v1/input-source").json()["settings"]["host"] == "second"
        assert services.repository.get_revision(project.project.id).draft == draft
        other = services.repository.create_project(
            name="Unbound input fixture",
            draft=draft.model_copy(update={"editor_metadata": {}}),
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=datetime.now(UTC).isoformat(),
        )
        unbound = services.mcp_store.create_token(
            client_id="unbound",
            username="admin",
            project_id=other.project.id,
            scopes=(McpScope.READ, McpScope.INPUT_CONFIG),
            expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            occurred_at=datetime.now(UTC).isoformat(),
        ).token
        assert tool(client, unbound, "get_input_source")["isError"] is True
        assert (
            tool(client, unbound, "configure_input_source", configuration={"host": "blocked"})[
                "isError"
            ]
            is True
        )
    assert McpScope.INPUT_CONFIG not in allowed_mcp_scopes(Role.DESIGNER, Role.ADMINISTRATOR, True)
    assert McpScope.INPUT_CONFIG in allowed_mcp_scopes(Role.ADMINISTRATOR, None, True)


def test_nonadministrator_cannot_change_input_settings(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = GuiServices.create(
        GuiConfig(
            data_root=tmp_path,
            admin_password_hash=SecretStr(hash_password("original test login")),
            admin_role=Role.DESIGNER,
            allowed_origins=(ORIGIN,),
            secure_cookies=False,
        ),
        runtime,
    )
    with TestClient(create_app(runtime, gui_services=services)) as client:
        client.post(
            "/api/gui/v1/auth/login",
            json={"username": "admin", "password": "original test login"},
            headers={"Origin": ORIGIN},
        )
        assert client.get("/api/gui/v1/input-source").status_code == 200
        response = client.put(
            "/api/gui/v1/input-source",
            json={"host": "blocked"},
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": client.cookies["cueverge_csrf"],
            },
        )
        assert response.status_code == 403


def test_explicit_gui_management_does_not_parse_or_mix_frigate_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUEVERGE_GUI_ENABLED", "true")
    monkeypatch.setenv("CUEVERGE_INPUT_MANAGEMENT", "gui")
    monkeypatch.setenv("CUEVERGE_FRIGATE_MQTT_ENABLED", "true")
    monkeypatch.delenv("CUEVERGE_FRIGATE_MQTT_HOST", raising=False)
    monkeypatch.setenv("CUEVERGE_FRIGATE_MQTT_PORT", "not-an-integer")
    config = ControllerConfig.from_environment()
    assert config.frigate_mqtt.enabled is False
    assert config.frigate_mqtt.password is None


@pytest.mark.parametrize(
    "mode,legacy,expected",
    [
        (None, False, "gui"),
        (None, True, "environment"),
        ("gui", True, "gui"),
        ("environment", False, "environment"),
    ],
)
def test_input_management_selection_preserves_explicit_operator_environment(
    monkeypatch: pytest.MonkeyPatch,
    mode: str | None,
    legacy: bool,
    expected: str,
) -> None:
    for key in tuple(os.environ):
        if key.startswith("CUEVERGE_FRIGATE_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("CUEVERGE_GUI_ENABLED", "true")
    monkeypatch.setenv("CUEVERGE_GUI_ADMIN_PASSWORD_HASH", "original-test-hash")
    monkeypatch.delenv("CUEVERGE_INPUT_MANAGEMENT", raising=False)
    if mode is not None:
        monkeypatch.setenv("CUEVERGE_INPUT_MANAGEMENT", mode)
    if legacy:
        monkeypatch.setenv("CUEVERGE_FRIGATE_MQTT_ENABLED", "false")
    config = GuiConfig.from_environment()
    assert config is not None and config.input_management == expected
