import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import Role
from cueverge.gui.passwords import hash_password
from cueverge.gui.service import GuiServices

ORIGIN = "http://localhost:5173"
PASSWORD = "correct horse battery staple"
PASSWORD_HASH = hash_password(PASSWORD, salt=b"0123456789abcdef")


def test_gui_resources_require_authentication(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    client = TestClient(create_app(runtime, gui_services=_services(tmp_path, runtime)))

    assert client.get("/api/gui/v1/projects").status_code == 401
    assert client.get("/api/gui/v1/installation").status_code == 401
    assert client.get("/api/gui/v1/runtime/status").status_code == 401
    assert client.get("/api/gui/v1/topology").status_code == 401
    assert client.get("/api/gui/v1/audit").status_code == 401
    assert client.post("/api/gui/v1/projects", json={}).status_code == 401

    assert client.get("/api/v1/status").status_code == 404


def test_configured_web_build_is_served_without_shadowing_protected_api(
    tmp_path: Path,
) -> None:
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text(
        "<!doctype html><title>Cueverge test shell</title>",
        encoding="utf-8",
    )
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime, web_root=web_root)
    client = TestClient(create_app(runtime, gui_services=services))

    root = client.get("/")

    assert root.status_code == 200
    assert "Cueverge test shell" in root.text
    assert client.get("/api/gui/v1/topology").status_code == 401
    assert client.get("/api/v1/status").status_code == 404


def test_login_requires_allowed_origin_and_mutations_require_csrf(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))

    credentials = {"username": "admin", "password": PASSWORD}
    assert client.post("/api/gui/v1/auth/login", json=credentials).status_code == 403
    assert (
        client.post(
            "/api/gui/v1/auth/login",
            json=credentials,
            headers={"Origin": "http://attacker.invalid"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/gui/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
            headers={"Origin": ORIGIN},
        ).status_code
        == 401
    )

    login = client.post(
        "/api/gui/v1/auth/login",
        json=credentials,
        headers={"Origin": ORIGIN},
    )
    assert login.status_code == 200
    assert login.json() == {"username": "admin", "role": "administrator"}
    assert client.get("/api/gui/v1/auth/session").status_code == 200

    without_csrf = client.post(
        "/api/gui/v1/projects",
        json={"name": "Draft", "draft": _draft()},
        headers={"Origin": ORIGIN},
    )
    assert without_csrf.status_code == 403
    assert without_csrf.json()["detail"]["code"] == "csrf_failed"


def test_viewer_role_cannot_mutate_drafts(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime, role=Role.VIEWER)
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)

    response = client.post(
        "/api/gui/v1/projects",
        json={"name": "Draft", "draft": _draft()},
        headers=_mutation_headers(client),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "insufficient_role"
    policy = client.put(
        "/api/gui/v1/devices/unknown/connection-policy",
        json={"policy": "on_demand"},
        headers=_mutation_headers(client),
    )
    assert policy.status_code == 403
    assert policy.json()["detail"]["code"] == "insufficient_role"
    operate = client.post(
        "/api/gui/v1/operate/actions",
        json={"action": "maintenance"},
        headers=_mutation_headers(client),
    )
    assert operate.status_code == 403
    assert operate.json()["detail"]["code"] == "insufficient_role"
    lifecycle = client.put(
        "/api/gui/v1/devices/unknown/enrollment-state",
        json={"state": "suspended"},
        headers=_mutation_headers(client),
    )
    assert lifecycle.status_code == 403
    assert lifecycle.json()["detail"]["code"] == "insufficient_role"


def test_draft_revisions_are_durable_and_reject_stale_writes(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)

    created = client.post(
        "/api/gui/v1/projects",
        json={"name": "Halloween windows", "draft": _draft()},
        headers=_mutation_headers(client),
    )
    assert created.status_code == 201
    assert created.headers["etag"] == '"1"'
    project_id = created.json()["project"]["id"]

    changed = _draft(editor_metadata={"selected_scene": "ambient"})
    updated = client.put(
        f"/api/gui/v1/projects/{project_id}/draft",
        json=changed,
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert updated.status_code == 200
    assert updated.headers["etag"] == '"2"'
    assert updated.json()["revision"] == 2

    stale = client.put(
        f"/api/gui/v1/projects/{project_id}/draft",
        json=_draft(editor_metadata={"selected_scene": "reaction"}),
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "revision_conflict",
        "requested_revision": 1,
        "current_revision": 2,
        "conflicting_paths": ["/editor_metadata"],
    }

    reloaded_services = GuiServices.create(services.config, runtime)
    reloaded = reloaded_services.repository.get_revision(project_id)
    assert reloaded.revision == 2
    assert reloaded.draft.editor_metadata == {"selected_scene": "ambient"}

    revisions = client.get(f"/api/gui/v1/projects/{project_id}/revisions")
    assert [revision["revision"] for revision in revisions.json()] == [2, 1]
    audit = client.get("/api/gui/v1/audit")
    assert audit.status_code == 200
    assert {
        (event["action"], event["result"])
        for event in audit.json()
        if event["target"] == project_id
    } == {
        ("project.create", "success"),
        ("draft.update", "success"),
        ("draft.update", "revision_conflict"),
    }


def test_compilation_round_trips_through_loader_without_runtime_side_effects(
    tmp_path: Path,
) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)
    created = client.post(
        "/api/gui/v1/projects",
        json={
            "name": "Compiler fixture",
            "draft": _draft(editor_metadata={"panel_layout": {"timeline_height": 320}}),
        },
        headers=_mutation_headers(client),
    )
    project_id = created.json()["project"]["id"]

    compiled = client.post(
        f"/api/gui/v1/projects/{project_id}/compile",
        headers=_mutation_headers(client, if_match='"1"'),
    )

    assert compiled.status_code == 200
    assert compiled.json()["scene_ids"] == ["ambient"]
    package_roots = list((tmp_path / "compiled" / project_id).glob("r1-*"))
    assert len(package_roots) == 1
    package_root = package_roots[0]
    assert load_show_package(package_root).require("ambient").definition.name == "Ambient"
    for document in package_root.rglob("*.json"):
        assert "editor_metadata" not in json.loads(document.read_text(encoding="utf-8"))

    assert runtime.active_scene is None
    assert runtime.connected_agent_count == 0
    assert runtime.sink.frames == []

    status = client.get("/api/gui/v1/runtime/status")
    assert status.status_code == 200
    assert status.json()["source"] == "controller_runtime"
    assert runtime.active_scene is None
    assert runtime.sink.frames == []


def test_operate_actions_require_role_csrf_and_are_audited(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)

    without_csrf = client.post(
        "/api/gui/v1/operate/actions",
        json={"action": "maintenance"},
        headers={"Origin": ORIGIN},
    )
    assert without_csrf.status_code == 403

    maintenance = client.post(
        "/api/gui/v1/operate/actions",
        json={"action": "maintenance"},
        headers=_mutation_headers(client),
    )
    assert maintenance.status_code == 200
    assert maintenance.json()["mode"] == "maintenance"

    rejected = client.post(
        "/api/gui/v1/operate/actions",
        json={"action": "start"},
        headers=_mutation_headers(client),
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "operation_rejected"
    results = {
        (event.action, event.result)
        for event in services.repository.list_audit_events()
        if event.action.startswith("operate.")
    }
    assert results == {
        ("operate.maintenance", "success"),
        ("operate.start", "error"),
    }


def test_topology_does_not_expose_internal_runtime_channels_as_devices(
    tmp_path: Path,
) -> None:
    runtime = ControllerRuntime()
    client = TestClient(create_app(runtime, gui_services=_services(tmp_path, runtime)))
    _login(client)

    response = client.get("/api/gui/v1/topology")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "controller_runtime"
    assert body["enrollment_supported"] is False
    nodes = {node["id"]: node for node in body["nodes"]}
    assert nodes["controller"]["enrollment"] == "local_controller"
    assert not any(node_id.startswith("agent:") for node_id in nodes)
    assert any(node["kind"] == "controller_output" for node in body["nodes"])


def _services(
    tmp_path: Path,
    runtime: ControllerRuntime,
    *,
    role: Role = Role.ADMINISTRATOR,
    web_root: Path | None = None,
) -> GuiServices:
    return GuiServices.create(
        GuiConfig(
            data_root=tmp_path,
            admin_username="admin",
            admin_password_hash=SecretStr(PASSWORD_HASH),
            admin_role=role,
            allowed_origins=(ORIGIN,),
            secure_cookies=False,
            web_root=web_root,
        ),
        runtime,
    )


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/gui/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


def _mutation_headers(
    client: TestClient,
    *,
    if_match: str | None = None,
) -> dict[str, str]:
    csrf_token = client.cookies.get("cueverge_csrf")
    assert csrf_token is not None
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf_token}
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


def _draft(*, editor_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "installation": {
            "schema_version": 1,
            "resources": [
                {
                    "resource": "projector.demo",
                    "kind": "media_agent",
                    "target": "fake-media-1",
                },
                {
                    "resource": "roofline.demo",
                    "kind": "pixel_output",
                    "target": "memory-roofline",
                    "pixel_count": 8,
                },
            ],
        },
        "scenes": [
            {
                "schema_version": 1,
                "id": "ambient",
                "name": "Ambient",
                "mode": "media_synchronized",
                "resources": ["projector.demo", "roofline.demo"],
                "duration_ms": 4000,
                "media": {"resource": "projector.demo"},
                "lighting": {
                    "resource": "roofline.demo",
                    "effect": "synthetic_sweep",
                },
            }
        ],
        "editor_metadata": editor_metadata or {},
    }
