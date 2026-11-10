import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cueverge.config import LifxResourceConfig
from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.gui.point_light_outputs import create_output, load_outputs
from tests.unit.test_gui_foundation import _login, _mcp_fixture, _mutation_headers


def test_registration_restores_resources_and_rejects_duplicates(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    second = ControllerRuntime()
    config = LifxResourceConfig(resource="light.left", host="127.0.0.1", target="d073d58b1a6d")
    try:
        create_output(tmp_path, runtime, config)
        assert runtime._with_point_lights(runtime.catalog) is runtime.catalog
        assert runtime.catalog.installation.resources[-1].pixel_count == 1
        assert runtime.lighting_targets()[-1].resource == "light.left"
        with pytest.raises(ValueError):
            create_output(tmp_path, runtime, config)
        load_outputs(tmp_path, second)
        load_outputs(tmp_path, second)
        # Restart after a show containing the imported resource has been applied.
        restored = ControllerRuntime(catalog=runtime.catalog)
        try:
            load_outputs(tmp_path, restored)
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


def test_output_creation_and_import_into_existing_show(tmp_path: Path) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    installation = services.repository.get_runtime_installation().id
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        url = f"/api/gui/v1/installations/{installation}/point-lights"
        body = dict(resource="light.left", host="127.0.0.1", target="d073d58b1a6d")
        assert client.post(url, json=body).status_code == 401
        _login(client)
        project = client.post(
            "/api/gui/v1/projects/from-active",
            json={"name": "Existing"},
            headers=_mutation_headers(client),
        )
        assert project.status_code == 201, project.text
        assert client.post(url, json=body).status_code == 403
        created = client.post(url, json=body, headers=_mutation_headers(client))
        assert created.status_code == 201, created.text
        assert created.json()["pixel_count"] == 1
        document = project.json()
        imported = client.post(
            f"/api/gui/v1/projects/{document['project']['id']}/point-light-resources/light.left",
            headers={**_mutation_headers(client), "If-Match": f'"{document["revision"]}"'},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["draft"]["installation"]["resources"][-1]["resource"] == "light.left"
        scene = client.post(
            f"/api/gui/v1/projects/{document['project']['id']}/scenes",
            json={
                "scene_id": "point-test",
                "name": "Point test",
                "duration_ms": 3000,
                "output": {"output_id": "left", "resource": "light.left"},
            },
            headers={**_mutation_headers(client), "If-Match": f'"{imported.json()["revision"]}"'},
        )
        assert scene.status_code == 200, scene.text
        assert scene.json()["draft"]["scenes"][-1]["lighting"][0]["resource"] == "light.left"
