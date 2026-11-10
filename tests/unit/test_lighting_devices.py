import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cueverge.config import LifxResourceConfig, WledResourceConfig
from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.gui.lighting_devices import LightingDeviceError, LightingDeviceRegistry
from cueverge.gui.mcp_authoring import McpScope
from cueverge.gui.wled_status import WledStatus
from cueverge.lighting.ddp import DdpUdpSink
from tests.unit.test_gui_foundation import _login, _mcp_fixture, _mcp_tool, _mutation_headers

LEFT = LifxResourceConfig(resource="light.left", host="127.0.0.1", target="d073d58b1a6d")
ROOF = WledResourceConfig(resource="roofline", host="127.0.0.2", expected_pixel_count=60)


def test_registration_restores_resources_and_rejects_duplicates(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    second = ControllerRuntime()
    try:
        LightingDeviceRegistry(tmp_path, runtime).create(LEFT)
        assert runtime.catalog.installation.resources[-1].pixel_count == 1
        assert runtime.lighting_targets()[-1].resource == "light.left"
        with pytest.raises(LightingDeviceError):
            LightingDeviceRegistry(tmp_path, runtime).create(LEFT)
        LightingDeviceRegistry(tmp_path, second).load()
        LightingDeviceRegistry(tmp_path, second).load()
        # Restart after a show containing the imported resource has been applied.
        restored = ControllerRuntime(catalog=runtime.catalog)
        try:
            LightingDeviceRegistry(tmp_path, restored).load()
            assert restored.lighting_targets()[0].resource == "light.left"
        finally:
            restored.sink.close()
        assert second.catalog.installation.resources[-1].resource == "light.left"
        asyncio.run(
            second.replace_catalog(ControllerRuntime().catalog, identity={"source": "test"})
        )
        assert second.catalog.installation.resources[-1].resource == "light.left"
        assert runtime.lighting_transport_status()["light.left"]["frames_sent"] == 0
    finally:
        runtime.sink.close()
        second.sink.close()


def test_legacy_lifx_file_is_migrated(tmp_path: Path) -> None:
    (tmp_path / "lifx-outputs.json").write_text(json.dumps([LEFT.model_dump()]))
    runtime = ControllerRuntime()
    try:
        registry = LightingDeviceRegistry(tmp_path, runtime)
        registry.load()
        assert runtime.lighting_targets()[-1].driver == "lifx"
        assert registry.view()["revision"] == 1
        assert not (tmp_path / "lifx-outputs.json").exists()
        assert (tmp_path / "lighting-devices.json").exists()
    finally:
        runtime.sink.close()


def test_wled_lifecycle_binds_pixels_and_guards_changes(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    registry = LightingDeviceRegistry(tmp_path, runtime)
    try:
        created = registry.create(ROOF)
        assert created["revision"] == 1 and created["pixel_count"] == 60
        binding = runtime.catalog.installation.resources[-1]
        assert (binding.resource, binding.target, binding.pixel_count) == ("roofline", "wled", 60)
        assert runtime.lighting_targets()[-1].driver == "ddp"
        with pytest.raises(LightingDeviceError) as stale:
            registry.update("roofline", ROOF, expected_revision=0)
        assert stale.value.status == 412
        with pytest.raises(LightingDeviceError, match="pixel count"):
            registry.update(
                "roofline",
                ROOF.model_copy(update={"expected_pixel_count": 61}),
                expected_revision=1,
            )
        registry.update(
            "roofline", ROOF.model_copy(update={"host": "127.0.0.3"}), expected_revision=1
        )
        sink = runtime.sink.sinks["roofline"]  # type: ignore[attr-defined]
        assert isinstance(sink, DdpUdpSink) and sink.host == "127.0.0.3"
        restored = ControllerRuntime()
        try:
            LightingDeviceRegistry(tmp_path, restored).load()
            assert restored.lighting_targets()[-1].host == "127.0.0.3"
        finally:
            restored.sink.close()
        runtime.lighting_output_in_use = lambda resource: True  # type: ignore[method-assign]
        with pytest.raises(LightingDeviceError, match="loaded show uses"):
            registry.remove("roofline", expected_revision=2)
        assert registry.view()["revision"] == 2
        del runtime.lighting_output_in_use
        registry.remove("roofline", expected_revision=2)
        assert all(b.resource != "roofline" for b in runtime.catalog.installation.resources)
        assert registry.view()["devices"] == []
    finally:
        runtime.sink.close()


def test_wled_requires_private_lan_address() -> None:
    with pytest.raises(ValueError):
        WledResourceConfig(resource="roof", host="8.8.8.8", expected_pixel_count=10)


def test_gui_and_mcp_manage_lighting_devices(tmp_path: Path, monkeypatch) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    installation = services.repository.get_runtime_installation().id
    probed: list[str] = []

    async def observe(host: str) -> WledStatus:
        probed.append(host)
        return WledStatus(True, 3.0, "0.15.0", 60, 80, True, None)

    monkeypatch.setattr(services.wled_status, "observe", observe)

    def token(scopes: tuple[McpScope, ...]) -> str:
        now = datetime.now(UTC)
        return services.mcp_store.create_installation_token(
            client_id="lighting",
            username="admin",
            installation_id=installation,
            scopes=scopes,
            expires_at=(now + timedelta(hours=1)).isoformat(),
            occurred_at=now.isoformat(),
        ).token

    read = token((McpScope.READ,))
    configure = token((McpScope.READ, McpScope.DEVICE_CONFIG))
    base = f"/api/gui/v1/installations/{installation}/lighting-devices"
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        lifx = {
            "kind": "lifx",
            "resource": "light.left",
            "host": "127.0.0.1",
            "target": "d073d58b1a6d",
        }
        assert client.post(base, json=lifx).status_code == 401
        _login(client)
        project = client.post(
            "/api/gui/v1/projects/from-active",
            json={"name": "Existing"},
            headers=_mutation_headers(client),
        )
        assert project.status_code == 201, project.text
        assert client.post(base, json=lifx).status_code == 403
        # An empty registry is revision 0; the GUI sends it as If-Match.
        created = client.post(base, json=lifx, headers=_mutation_headers(client, if_match='"0"'))
        assert created.status_code == 201, created.text
        assert created.json()["pixel_count"] == 1
        detect = f"{base}/wled-detect"
        public = client.post(detect, json={"host": "8.8.8.8"}, headers=_mutation_headers(client))
        assert public.status_code == 422
        found = client.post(detect, json={"host": "127.0.0.2"}, headers=_mutation_headers(client))
        assert found.json()["reported_pixel_count"] == 60

        # MCP: read tokens list; only device_config tokens change the registry.
        listed = _mcp_tool(client, read, "get_lighting_devices")["structuredContent"]
        assert [d["resource"] for d in listed["devices"]] == ["light.left"]
        wled = {
            "kind": "wled",
            "resource": "roofline",
            "host": "127.0.0.2",
            "expected_pixel_count": 60,
        }
        assert _mcp_tool(client, read, "add_lighting_device", device=wled)["isError"]
        assert (
            _mcp_tool(client, configure, "detect_wled_controller", host="10.0.0.9")[
                "structuredContent"
            ]["software_version"]
            == "0.15.0"
        )
        added = _mcp_tool(
            client, configure, "add_lighting_device", device=wled, expected_revision=1
        )["structuredContent"]
        assert added["revision"] == 2 and added["hardware_commands_sent"] is False
        bad = _mcp_tool(
            client, configure, "add_lighting_device", device={**wled, "host": "8.8.8.8"}
        )
        assert bad["isError"]

        # The MCP-created device is visible and editable in the GUI.
        view = client.get(base).json()
        assert {d["resource"]: d["kind"] for d in view["devices"]} == {
            "light.left": "lifx",
            "roofline": "wled",
        }
        edited = client.put(
            f"{base}/roofline",
            json={**wled, "brightness_limit": 0.5},
            headers=_mutation_headers(client, if_match='"2"'),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["brightness_limit"] == 0.5
        stale = client.put(
            f"{base}/roofline", json=wled, headers=_mutation_headers(client, if_match='"2"')
        )
        assert stale.status_code == 412

        # Topology probes WLED over HTTP, never LIFX bulbs.
        probed.clear()
        nodes = {n["id"]: n for n in client.get("/api/gui/v1/topology").json()["nodes"]}
        assert probed == ["127.0.0.2"]
        assert nodes["output:light.left"]["output_driver"] == "lifx"
        assert nodes["output:roofline"]["compatibility"] == "compatible"

        document = project.json()
        imported = client.post(
            f"/api/gui/v1/projects/{document['project']['id']}/lighting-device-resources/roofline",
            headers=_mutation_headers(client, if_match=f'"{document["revision"]}"'),
        )
        assert imported.status_code == 200, imported.text
        resources = imported.json()["draft"]["installation"]["resources"]
        assert (resources[-1]["resource"], resources[-1]["target"]) == ("roofline", "wled")
        assert resources[-1]["pixel_count"] == 60
        scene = client.post(
            f"/api/gui/v1/projects/{document['project']['id']}/scenes",
            json={
                "scene_id": "roof-test",
                "name": "Roof test",
                "duration_ms": 3000,
                "output": {"output_id": "roof", "resource": "roofline"},
            },
            headers=_mutation_headers(client, if_match=f'"{imported.json()["revision"]}"'),
        )
        assert scene.status_code == 200, scene.text

        assert _mcp_tool(
            client, configure, "remove_lighting_device", resource="roofline", expected_revision=3
        )["isError"]
        removed = _mcp_tool(
            client,
            configure,
            "remove_lighting_device",
            resource="roofline",
            expected_revision=3,
            confirm_remove=True,
        )["structuredContent"]
        assert [d["resource"] for d in removed["devices"]] == ["light.left"]
        deleted = client.delete(
            f"{base}/light.left", headers=_mutation_headers(client, if_match='"4"')
        )
        assert deleted.status_code == 204
        actions = [e.action for e in services.repository.list_audit_events(limit=20)]
        for verb in ("create", "update", "remove"):
            assert f"installation.lighting_device.{verb}" in actions
        setup = _mcp_tool(client, read, "get_installation_setup")["structuredContent"]
        assert "add_lighting_device" in setup["device_setup"]["mcp_lighting_device_tools"]
        capabilities: dict[str, Any] = _mcp_tool(
            client, read, "get_capabilities", project_id=document["project"]["id"]
        )["structuredContent"]
        assert capabilities["lighting_devices"]["scope"] == "device_config"
        assert "kind" in json.dumps(capabilities["lighting_devices"]["device_schema"])
