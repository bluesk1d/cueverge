import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import Role
from cueverge.gui.endpoint_discovery import (
    DiscoveredController,
    EndpointDiscoveryService,
)
from cueverge.gui.passwords import hash_password
from cueverge.gui.service import GuiServices
from cueverge.protocol.device_v2 import (
    ControllerDiscoveryDocument,
    DiscoveryProtocolRange,
)

ORIGIN = "http://localhost:5173"
PASSWORD = "correct horse battery staple"
PASSWORD_HASH = hash_password(PASSWORD, salt=b"0123456789abcdef")


def test_gui_resources_require_authentication(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    client = TestClient(create_app(runtime, gui_services=_services(tmp_path, runtime)))

    assert client.get("/api/gui/v1/projects").status_code == 401
    assert client.get("/api/gui/v1/installation").status_code == 401
    assert client.get("/api/gui/v1/installations").status_code == 401
    assert client.get("/api/gui/v1/runtime/status").status_code == 401
    assert client.get("/api/gui/v1/topology").status_code == 401
    assert client.get("/api/gui/v1/activity").status_code == 401
    assert client.get("/api/gui/v1/discovery/endpoints").status_code == 401
    assert client.get("/api/gui/v1/audit").status_code == 401
    assert client.post("/api/gui/v1/projects", json={}).status_code == 401

    assert client.get("/api/v1/status").status_code == 404
    discovery = client.get("/api/device/v2/discovery")
    assert discovery.status_code == 200
    assert discovery.json()["capabilities"] == ["downstream_control"]


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
    connection_test = client.post(
        "/api/gui/v1/devices/unknown/connection-test",
        headers=_mutation_headers(client),
    )
    assert connection_test.status_code == 403
    assert connection_test.json()["detail"]["code"] == "insufficient_role"


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

    restored = client.post(
        f"/api/gui/v1/projects/{project_id}/revisions/1/restore",
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert restored.status_code == 200
    assert restored.headers["etag"] == '"3"'
    assert restored.json()["revision"] == 3
    assert restored.json()["draft"]["editor_metadata"] == {}

    stale_restore = client.post(
        f"/api/gui/v1/projects/{project_id}/revisions/2/restore",
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert stale_restore.status_code == 409
    assert stale_restore.json()["current_revision"] == 3

    revisions = client.get(f"/api/gui/v1/projects/{project_id}/revisions")
    assert [revision["revision"] for revision in revisions.json()] == [3, 2, 1]
    assert revisions.json()[0]["transaction_kind"] == "restore"
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
        ("draft.restore", "success"),
        ("draft.restore", "revision_conflict"),
    }


def test_installation_crud_preserves_runtime_active_record(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    client = TestClient(create_app(runtime, gui_services=_services(tmp_path, runtime)))
    _login(client)

    listed = client.get("/api/gui/v1/installations")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    active = listed.json()[0]
    assert active["runtime_active"] is True

    created = client.post(
        "/api/gui/v1/installations",
        json={"name": "Garage"},
        headers=_mutation_headers(client),
    )
    assert created.status_code == 201
    assert created.headers["etag"] == '"1"'
    installation_id = created.json()["id"]

    updated = client.put(
        f"/api/gui/v1/installations/{installation_id}",
        json={"name": "Garage Test"},
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2

    stale = client.put(
        f"/api/gui/v1/installations/{installation_id}",
        json={"name": "Stale"},
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "installation_conflict"

    active_delete = client.delete(
        f"/api/gui/v1/installations/{active['id']}",
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert active_delete.status_code == 409

    deleted = client.delete(
        f"/api/gui/v1/installations/{installation_id}",
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert deleted.status_code == 204


def test_project_metadata_duplicate_archive_import_and_export(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    client = TestClient(create_app(runtime, gui_services=_services(tmp_path, runtime)))
    _login(client)

    created = client.post(
        "/api/gui/v1/projects/from-active",
        json={"name": "Home Show"},
        headers=_mutation_headers(client),
    )
    assert created.status_code == 201
    project_id = created.json()["project"]["id"]
    assert created.json()["project"]["metadata_revision"] == 1
    installation = next(
        item for item in client.get("/api/gui/v1/installations").json() if item["runtime_active"]
    )
    assert installation["used_by_projects"] == ["Home Show"]
    organized_installation = client.put(
        f"/api/gui/v1/installations/{installation['id']}",
        json={
            "name": installation["name"],
            "folder": "Sites/Home",
            "tags": ["home", "primary"],
            "favorite": True,
        },
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert organized_installation.status_code == 200
    assert organized_installation.json()["folder"] == "Sites/Home"
    assert organized_installation.json()["tags"] == ["home", "primary"]
    assert organized_installation.json()["favorite"] is True

    renamed = client.put(
        f"/api/gui/v1/projects/{project_id}",
        json={"name": "Home Show 2026"},
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert renamed.status_code == 200
    assert renamed.headers["etag"] == '"2"'

    duplicated = client.post(
        f"/api/gui/v1/projects/{project_id}/duplicate",
        json={"name": "Home Show Copy"},
        headers=_mutation_headers(client),
    )
    assert duplicated.status_code == 201

    exported = client.get(f"/api/gui/v1/projects/{project_id}/export")
    assert exported.status_code == 200
    assert exported.json()["name"] == "Home Show 2026"
    imported_document = {**exported.json(), "name": "Imported Home Show"}
    imported = client.post(
        "/api/gui/v1/projects/import",
        json=imported_document,
        headers=_mutation_headers(client),
    )
    assert imported.status_code == 201

    scene_id = created.json()["draft"]["scenes"][0]["id"]
    organized = client.put(
        f"/api/gui/v1/projects/{project_id}/scenes/{scene_id}/organization",
        json={"folder": "Scenes/Ambient", "tags": ["ambient"], "favorite": True},
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert organized.status_code == 200
    assert organized.headers["etag"] == '"2"'
    preset = client.post(
        f"/api/gui/v1/projects/{project_id}/presets",
        json={
            "scene_id": scene_id,
            "name": "Ambient base",
            "folder": "Presets/Base",
            "tags": ["base"],
            "favorite": True,
        },
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert preset.status_code == 201
    assert preset.headers["etag"] == '"3"'
    preset_id = next(iter(preset.json()["draft"]["editor_metadata"]["scene_presets"]))
    applied = client.post(
        f"/api/gui/v1/projects/{project_id}/presets/{preset_id}/apply",
        json={"scene_id": scene_id},
        headers=_mutation_headers(client, if_match='"3"'),
    )
    assert applied.status_code == 200
    assert applied.headers["etag"] == '"4"'
    assert applied.json()["draft"]["editor_metadata"]["scene_preset_usage"] == {scene_id: preset_id}

    archived = client.post(
        f"/api/gui/v1/projects/{project_id}/archive",
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert archived.status_code == 200
    assert archived.json()["metadata_revision"] == 3
    assert archived.json()["archived_at"] is not None

    stale = client.put(
        f"/api/gui/v1/projects/{project_id}",
        json={"name": "Stale"},
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "project_metadata_conflict"
    assert len(client.get("/api/gui/v1/projects").json()) == 3


def test_asset_upload_api_is_chunked_resumable_and_digest_checked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    processed_assets: list[str] = []
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"thumbnail")
    monkeypatch.setattr(
        services.asset_upload,
        "process_asset",
        lambda asset_id, *, occurred_at: processed_assets.append(asset_id),
    )
    monkeypatch.setattr(
        services.asset_upload,
        "get_artifact",
        lambda asset_id, kind: (thumbnail, "image/jpeg"),
    )
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)
    project = client.post(
        "/api/gui/v1/projects/from-active",
        json={"name": "Asset fixture"},
        headers=_mutation_headers(client),
    ).json()
    project_id = project["project"]["id"]
    content = b"chunked-asset"
    begun = client.post(
        f"/api/gui/v1/projects/{project_id}/assets/uploads",
        json={
            "name": "Clip",
            "original_filename": "clip.mp4",
            "media_type": "video/mp4",
            "total_bytes": len(content),
            "expected_sha256": hashlib.sha256(content).hexdigest(),
        },
        headers=_mutation_headers(client),
    )
    assert begun.status_code == 201
    upload_id = begun.json()["id"]

    first = client.put(
        f"/api/gui/v1/assets/uploads/{upload_id}",
        content=content[:4],
        headers={**_mutation_headers(client), "Upload-Offset": "0"},
    )
    assert first.status_code == 200
    assert first.json()["received_bytes"] == 4
    conflict = client.put(
        f"/api/gui/v1/assets/uploads/{upload_id}",
        content=content[4:],
        headers={**_mutation_headers(client), "Upload-Offset": "0"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["received_bytes"] == 4
    completed = client.put(
        f"/api/gui/v1/assets/uploads/{upload_id}",
        content=content[4:],
        headers={**_mutation_headers(client), "Upload-Offset": "4"},
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "completed"
    assert completed.json()["asset"]["status"] == "uploading"
    assert processed_assets == [completed.json()["asset"]["id"]]
    assets = client.get(f"/api/gui/v1/projects/{project_id}/assets")
    assert len(assets.json()) == 1
    assert assets.json()[0]["digest_sha256"] == hashlib.sha256(content).hexdigest()
    assert assets.json()[0]["original_file_status"] == "available"
    assert assets.json()[0]["artifact_faults"] == []
    assert assets.json()[0]["references"] == []
    assert assets.json()[0]["unused"] is True
    artifact = client.get(
        f"/api/gui/v1/assets/{completed.json()['asset']['id']}/artifacts/thumbnail"
    )
    assert artifact.status_code == 200
    assert artifact.headers["content-type"] == "image/jpeg"
    assert artifact.content == b"thumbnail"
    asset_id = completed.json()["asset"]["id"]
    governed = client.put(
        f"/api/gui/v1/assets/{asset_id}/metadata",
        json={
            "owner": "Projection team",
            "license": "Original production",
            "provenance": "Created for the Home installation.",
            "review_status": "approved",
            "expires_on": "2027-10-31",
            "folder": "Visuals/Windows",
            "tags": ["window", "halloween"],
            "favorite": True,
        },
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert governed.status_code == 200
    assert governed.headers["etag"] == '"2"'
    assert governed.json()["metadata_revision"] == 2
    assert governed.json()["owner"] == "Projection team"
    assert governed.json()["review_status"] == "approved"
    assert governed.json()["expires_on"] == "2027-10-31"
    assert governed.json()["folder"] == "Visuals/Windows"
    assert governed.json()["tags"] == ["window", "halloween"]
    assert governed.json()["favorite"] is True
    stale = client.put(
        f"/api/gui/v1/assets/{asset_id}/metadata",
        json={
            "owner": None,
            "license": None,
            "provenance": None,
            "review_status": "unreviewed",
            "expires_on": None,
        },
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "asset_metadata_conflict",
        "requested_revision": 1,
        "current_revision": 2,
    }
    assert any(
        event.action == "asset.metadata.update" and event.target == asset_id
        for event in services.repository.list_audit_events()
    )
    get_asset = services.asset_upload.get_asset
    monkeypatch.setattr(
        services.asset_upload,
        "get_asset",
        lambda requested_id: replace(
            get_asset(requested_id),
            status="ready",
            original_file_status="available",
        ),
    )
    scene = project["draft"]["scenes"][0]
    output_id = scene["media"][0]["id"] if scene["schema_version"] == 2 else None
    placed = client.post(
        f"/api/gui/v1/projects/{project_id}/assets/{asset_id}/place",
        json={"scene_id": scene["id"], "output_id": output_id},
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert placed.status_code == 200
    assert placed.headers["etag"] == '"2"'
    assert placed.json()["draft"]["editor_metadata"]["asset_bindings"] == {asset_id: 1}
    placed_scene = placed.json()["draft"]["scenes"][0]
    if placed_scene["schema_version"] == 1:
        assert placed_scene["media"]["asset"] == asset_id
    else:
        assert placed_scene["media"][0]["asset"] == asset_id
    exported = client.get(f"/api/gui/v1/projects/{project_id}/export")
    assert exported.status_code == 200
    assert exported.json()["schema_version"] == 2
    assert exported.json()["assets"][0]["source_id"] == asset_id
    assert '"path"' not in json.dumps(exported.json()["assets"])
    portable_document = {**exported.json(), "name": "Portable Asset Copy"}
    imported_runtime = ControllerRuntime()
    imported_client = TestClient(
        create_app(
            imported_runtime,
            gui_services=_services(tmp_path / "portable-import", imported_runtime),
        )
    )
    _login(imported_client)
    unsafe_document = json.loads(json.dumps(portable_document))
    unsafe_document["assets"][0]["revisions"][0]["original_filename"] = "../clip.mp4"
    unsafe = imported_client.post(
        "/api/gui/v1/projects/import",
        json=unsafe_document,
        headers=_mutation_headers(imported_client),
    )
    assert unsafe.status_code == 422
    imported = imported_client.post(
        "/api/gui/v1/projects/import",
        json=portable_document,
        headers=_mutation_headers(imported_client),
    )
    assert imported.status_code == 201
    imported_project_id = imported.json()["project"]["id"]
    imported_assets = imported_client.get(
        f"/api/gui/v1/projects/{imported_project_id}/assets"
    ).json()
    assert len(imported_assets) == 1
    imported_asset = imported_assets[0]
    assert imported_asset["id"] != asset_id
    assert imported_asset["status"] == "failed"
    assert imported_asset["original_file_status"] == "missing"
    assert "checksum-safe relink" in imported_asset["error"]
    imported_scene = imported.json()["draft"]["scenes"][0]
    imported_reference = (
        imported_scene["media"]["asset"]
        if imported_scene["schema_version"] == 1
        else imported_scene["media"][0]["asset"]
    )
    assert imported_reference == imported_asset["id"]
    assert imported.json()["draft"]["editor_metadata"]["asset_bindings"] == {
        imported_asset["id"]: 1
    }


def test_explicit_discovery_is_untrusted_and_rejectable(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = replace(
        _services(tmp_path, runtime),
        endpoint_discovery=_FixtureEndpointDiscovery(),
    )
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)

    discovered = client.post(
        "/api/gui/v1/discovery/probe",
        json={"address": "https://controller.local"},
        headers=_mutation_headers(client),
    )
    assert discovered.status_code == 200
    assert discovered.json()["compatible"] is True
    assert discovered.json()["state"] == "discovered"
    endpoint_id = discovered.json()["id"]

    listed = client.get("/api/gui/v1/discovery/endpoints")
    assert [endpoint["id"] for endpoint in listed.json()] == [endpoint_id]
    rejected = client.post(
        f"/api/gui/v1/discovery/endpoints/{endpoint_id}/reject",
        headers=_mutation_headers(client),
    )
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "rejected"


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

    preview = client.post(
        "/api/gui/v1/maintenance/test-pattern-preview",
        json={"resource": "projector.demo", "duration_seconds": 5},
        headers=_mutation_headers(client),
    )
    assert preview.status_code == 200
    assert preview.json()["pattern"] == "alignment_grid"
    assert preview.json()["simulation_only"] is True

    automatic = client.post(
        "/api/gui/v1/operate/actions",
        json={"action": "automatic"},
        headers=_mutation_headers(client),
    )
    assert automatic.status_code == 200
    rejected_preview = client.post(
        "/api/gui/v1/maintenance/test-pattern-preview",
        json={"resource": "roofline.demo", "duration_seconds": 5},
        headers=_mutation_headers(client),
    )
    assert rejected_preview.status_code == 409
    assert rejected_preview.json()["detail"]["code"] == "maintenance_mode_required"

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
        if event.action.startswith(("operate.", "maintenance."))
    }
    assert results == {
        ("maintenance.preview", "success"),
        ("operate.automatic", "success"),
        ("operate.maintenance", "success"),
        ("operate.start", "error"),
    }
    activity = client.get("/api/gui/v1/activity")
    assert activity.status_code == 200
    assert activity.json()[0] == {
        "id": activity.json()[0]["id"],
        "occurred_at": activity.json()[0]["occurred_at"],
        "category": "operation",
        "action": "operate.start",
        "actor": "admin",
        "target": "controller",
        "result": "error",
    }
    assert "authorization_context" not in activity.json()[0]


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


class _FixtureEndpointDiscovery(EndpointDiscoveryService):
    def probe(self, value: str) -> DiscoveredController:
        return DiscoveredController(
            base_url=value,
            document=ControllerDiscoveryDocument(
                software_version="0.1.0",
                protocol=DiscoveryProtocolRange(minimum=2, maximum=2),
            ),
        )


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
