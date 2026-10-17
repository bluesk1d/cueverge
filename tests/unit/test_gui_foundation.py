import asyncio
import hashlib
import json
import shutil
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.gui.asset_upload import StoredAsset
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.endpoint_discovery import (
    DiscoveredController,
    EndpointDiscoveryService,
)
from cueverge.gui.mapper_export import MapperExportError
from cueverge.gui.mcp_authoring import McpAuthoringError, McpScope
from cueverge.gui.passwords import hash_password
from cueverge.gui.publication import PublicationDiagnostic, PublicationError
from cueverge.gui.repository import StoredDevice
from cueverge.gui.service import GuiServices
from cueverge.protocol.device_v2 import (
    ControllerDiscoveryDocument,
    DeviceHello,
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


def test_observability_reports_api_storage_and_browser_errors(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)

    report = client.post(
        "/api/gui/v1/browser-errors",
        json={
            "message": "render failed",
            "source": "timeline",
            "occurred_at": "2026-03-10T12:00:00+00:00",
        },
        headers=_mutation_headers(client),
    )
    assert report.status_code == 204

    response = client.get("/api/gui/v1/observability")

    assert response.status_code == 200
    metrics = response.json()
    assert metrics["api_latency"]["request_count"] >= 1
    assert metrics["storage"]["total_bytes"] > 0
    assert metrics["browser_errors"][-1] == {
        "message": "render failed",
        "source": "timeline",
        "occurred_at": "2026-03-10T12:00:00+00:00",
    }
    assert any(
        event.action == "browser.error"
        and event.actor == "admin"
        and event.target == "timeline"
        for event in services.repository.list_audit_events()
    )


def test_mcp_tokens_are_hashed_scoped_expiring_and_revocable(tmp_path: Path) -> None:
    services = _services(tmp_path, ControllerRuntime())
    project = services.repository.create_project(
        name="MCP token fixture",
        draft=DraftProject.model_validate(_draft()),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-03-10T12:00:00+00:00",
    )
    created = services.mcp_store.create_token(
        client_id="assistant-one",
        username="admin",
        project_id=project.project.id,
        scopes=(McpScope.READ, McpScope.DRAFT_EDIT),
        expires_at="2026-03-11T12:00:00+00:00",
        occurred_at="2026-03-10T12:00:00+00:00",
    )

    assert created.token.startswith("cvmcp_")
    assert (
        services.mcp_store.verify_token(
            created.token,
            "2026-03-10T12:30:00+00:00",
        )
        == created.record
    )
    assert (
        services.mcp_store.verify_token(
            created.token,
            "2026-03-12T12:00:00+00:00",
        )
        is None
    )
    assert created.token not in services.repository.database_path.read_text(
        encoding="utf-8",
        errors="ignore",
    )

    services.mcp_store.revoke_token(
        created.record.id,
        "2026-03-10T13:00:00+00:00",
    )
    assert (
        services.mcp_store.verify_token(
            created.token,
            "2026-03-10T13:00:01+00:00",
        )
        is None
    )


def test_mcp_proposal_isolated_until_human_approval_and_merge(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, ControllerRuntime())
    project = services.repository.create_project(
        name="MCP proposal fixture",
        draft=DraftProject.model_validate(_draft()),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-03-10T12:00:00+00:00",
    )
    template = project.draft.scenes[0]

    proposal = services.mcp_authoring.propose_scene_outline(
        project_id=project.project.id,
        base_revision=project.revision,
        template_scene_id=template.id,
        scene_id="assistant-scene",
        name="Assistant scene",
        duration_ms=2500,
        client_id="assistant-one",
        username="admin",
    )

    unchanged = services.repository.get_revision(project.project.id)
    assert unchanged.revision == 1
    assert [scene.id for scene in unchanged.draft.scenes] == [template.id]

    validated = services.mcp_authoring.validate_proposal(proposal.id)
    assert validated.state.value == "validated"
    approved = services.mcp_authoring.approve(
        proposal.id,
        "reviewer",
        "Validated against the installation",
    )
    assert approved.state.value == "approved"
    merged = services.mcp_authoring.merge(
        proposal.id,
        "reviewer",
        Role.DESIGNER,
    )

    assert merged.state.value == "merged"
    updated = services.repository.get_revision(project.project.id)
    assert updated.revision == 2
    assert [scene.id for scene in updated.draft.scenes] == [
        template.id,
        "assistant-scene",
    ]


def test_mcp_rejected_and_stale_proposals_never_change_working_revision(
    tmp_path: Path,
) -> None:
    services = _services(tmp_path, ControllerRuntime())
    project = services.repository.create_project(
        name="MCP conflict fixture",
        draft=DraftProject.model_validate(_draft()),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-03-10T12:00:00+00:00",
    )
    template = project.draft.scenes[0]

    rejected = services.mcp_authoring.propose_scene_outline(
        project_id=project.project.id,
        base_revision=1,
        template_scene_id=template.id,
        scene_id="rejected-scene",
        name="Rejected scene",
        duration_ms=1000,
        client_id="assistant-one",
        username="admin",
    )
    services.mcp_authoring.reject(rejected.id, "reviewer", "Not appropriate")
    with pytest.raises(McpAuthoringError, match="requires explicit approval"):
        services.mcp_authoring.merge(rejected.id, "reviewer", Role.DESIGNER)

    stale = services.mcp_authoring.propose_scene_outline(
        project_id=project.project.id,
        base_revision=1,
        template_scene_id=template.id,
        scene_id="stale-scene",
        name="Stale scene",
        duration_ms=1000,
        client_id="assistant-one",
        username="admin",
    )
    services.mcp_authoring.validate_proposal(stale.id)
    services.mcp_authoring.approve(stale.id, "reviewer")
    services.repository.update_draft(
        project_id=project.project.id,
        expected_revision=1,
        draft=project.draft,
        actor="reviewer",
        role=Role.DESIGNER,
        occurred_at="2026-03-10T13:00:00+00:00",
    )

    with pytest.raises(McpAuthoringError, match="working revision changed"):
        services.mcp_authoring.merge(stale.id, "reviewer", Role.DESIGNER)
    current = services.repository.get_revision(project.project.id)
    assert current.revision == 2
    assert [scene.id for scene in current.draft.scenes] == [template.id]


def test_mcp_endpoint_requires_project_token_and_exposes_no_live_tools(
    tmp_path: Path,
) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    with TestClient(
        create_app(runtime, gui_services=services),
        base_url="http://localhost",
    ) as client:
        _login(client)
        created_project = client.post(
            "/api/gui/v1/projects",
            json={"name": "MCP protocol fixture", "draft": _draft()},
            headers=_mutation_headers(client),
        )
        assert created_project.status_code == 201
        project_id = created_project.json()["project"]["id"]
        expiry = (datetime.now(UTC) + timedelta(days=1)).isoformat()

        created_token = client.post(
            f"/api/gui/v1/projects/{project_id}/mcp-tokens",
            json={
                "client_id": "protocol-client",
                "scopes": ["read"],
                "expires_at": expiry,
            },
            headers=_mutation_headers(client),
        )
        assert created_token.status_code == 201
        token = created_token.json()["token"]
        assert token.startswith("cvmcp_")

        listed = client.get(f"/api/gui/v1/projects/{project_id}/mcp-tokens")
        assert listed.status_code == 200
        assert listed.json()[0]["token"] is None

        initialize_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        }
        unauthenticated = client.post(
            "/mcp/",
            json=initialize_body,
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert unauthenticated.status_code == 401

        authenticated_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        }
        initialized = client.post(
            "/mcp/",
            json=initialize_body,
            headers=authenticated_headers,
        )
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "Cueverge Authoring"

        tools = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=authenticated_headers,
        )
        assert tools.status_code == 200
        names = {tool["name"] for tool in tools.json()["result"]["tools"]}
        assert "get_project_structure" in names
        assert "propose_scene_outline" in names
        assert not names.intersection(
            {"publish", "activate", "enroll_device", "approve_proposal"}
        )

        project_structure = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_project_structure",
                    "arguments": {},
                },
            },
            headers=authenticated_headers,
        )
        assert project_structure.status_code == 200
        assert project_structure.json()["result"]["isError"] is False

        denied_proposal = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "propose_scene_outline",
                    "arguments": {
                        "base_revision": 1,
                        "template_scene_id": "scene-welcome",
                        "scene_id": "unauthorized",
                        "name": "Unauthorized",
                        "duration_ms": 1000,
                    },
                },
            },
            headers=authenticated_headers,
        )
        assert denied_proposal.status_code == 200
        assert denied_proposal.json()["result"]["isError"] is True
        assert services.repository.get_revision(project_id).revision == 1
        tool_events = [
            event
            for event in services.repository.list_audit_events()
            if event.action == "mcp.tool.call"
        ]
        assert {event.target for event in tool_events[-2:]} == {
            "get_project_structure",
            "propose_scene_outline",
        }


def test_mcp_proposal_requires_browser_csrf_approval_before_merge(
    tmp_path: Path,
) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)
    created_project = client.post(
        "/api/gui/v1/projects",
        json={"name": "MCP review fixture", "draft": _draft()},
        headers=_mutation_headers(client),
    ).json()
    project_id = created_project["project"]["id"]
    template_id = created_project["draft"]["scenes"][0]["id"]
    proposal = services.mcp_authoring.propose_scene_outline(
        project_id=project_id,
        base_revision=1,
        template_scene_id=template_id,
        scene_id="reviewed-scene",
        name="Reviewed scene",
        duration_ms=1500,
        client_id="assistant-one",
        username="admin",
    )

    assert (
        client.post(
            f"/api/gui/v1/projects/{project_id}/mcp-proposals/{proposal.id}/validate"
        ).status_code
        == 403
    )
    validate = client.post(
        f"/api/gui/v1/projects/{project_id}/mcp-proposals/{proposal.id}/validate",
        headers=_mutation_headers(client),
    )
    assert validate.status_code == 200
    approve = client.post(
        f"/api/gui/v1/projects/{project_id}/mcp-proposals/{proposal.id}/approve",
        json={"reason": "Human-reviewed validation and diff"},
        headers=_mutation_headers(client),
    )
    assert approve.status_code == 200
    assert approve.json()["approved_by"] == "admin"
    merged = client.post(
        f"/api/gui/v1/projects/{project_id}/mcp-proposals/{proposal.id}/merge",
        headers=_mutation_headers(client),
    )
    assert merged.status_code == 200
    assert merged.json()["state"] == "merged"
    assert services.repository.get_revision(project_id).revision == 2
    proposal_events = [
        event
        for event in services.repository.list_audit_events()
        if event.target == proposal.id
    ]
    assert {"mcp.proposal.validate", "mcp.proposal.approve", "mcp.proposal.merge"}.issubset(
        {event.action for event in proposal_events}
    )


def test_data_root_backup_restores_projects_and_audit_history(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    runtime = ControllerRuntime()
    source_client = TestClient(
        create_app(runtime, gui_services=_services(source_root, runtime))
    )
    _login(source_client)
    created = source_client.post(
        "/api/gui/v1/projects",
        json={"name": "Recovery fixture", "draft": _draft()},
        headers=_mutation_headers(source_client),
    )
    assert created.status_code == 201

    restored_root = tmp_path / "restored"
    shutil.copytree(source_root, restored_root)
    restored_runtime = ControllerRuntime()
    restored_client = TestClient(
        create_app(
            restored_runtime,
            gui_services=_services(restored_root, restored_runtime),
        )
    )
    _login(restored_client)

    projects = restored_client.get("/api/gui/v1/projects")
    audit = restored_client.get("/api/gui/v1/audit")

    assert projects.status_code == 200
    assert [project["name"] for project in projects.json()] == ["Recovery fixture"]
    assert audit.status_code == 200
    assert any(event["action"] == "project.create" for event in audit.json())


def test_compilation_meets_documented_maximum_scene_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    draft_document = _draft()
    template = draft_document["scenes"][0]
    draft_document["scenes"] = [
        {**template, "id": f"scene-{index:03}", "name": f"Scene {index:03}"}
        for index in range(250)
    ]
    draft = DraftProject.model_validate(draft_document)

    started = time.perf_counter()
    compilation = services.compiler.compile(
        project_id="scale-budget",
        revision=1,
        draft=draft,
    )
    elapsed_seconds = time.perf_counter() - started

    assert len(compilation.scene_ids) == 250
    assert elapsed_seconds < 5

    stored = services.repository.create_project(
        name="Scale publication",
        draft=draft,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-16T12:00:00+00:00",
    )
    services.publication.update_release(
        project_id=stored.project.id,
        revision=stored.revision,
        name="Scale publication",
        summary="Maximum supported scene count.",
        approval_state="approved",
        actor="admin",
        occurred_at="2026-10-16T12:01:00+00:00",
    )
    monkeypatch.setattr(services.publication, "validate", lambda *_: ())
    publication_started = time.perf_counter()
    package = services.publication.publish(
        project_id=stored.project.id,
        revision=stored.revision,
        actor="admin",
        occurred_at="2026-10-16T12:02:00+00:00",
    )

    assert package.revision == 1
    assert time.perf_counter() - publication_started < 5


def test_device_session_registry_meets_hundred_client_budget(tmp_path: Path) -> None:
    services = _services(tmp_path, ControllerRuntime())
    started = time.perf_counter()
    for index in range(100):
        device_id = uuid4()
        session_id = uuid4()
        capabilities = (f"capability-{index}",)
        device = cast(
            StoredDevice,
            SimpleNamespace(id=str(device_id), capabilities=capabilities),
        )
        hello = cast(
            DeviceHello,
            SimpleNamespace(
                device_id=device_id,
                device_session_id=session_id,
                capabilities=capabilities,
            ),
        )
        services.device_sessions.connect(device, hello)

    connected = services.device_sessions.connected_device_ids()
    elapsed_seconds = time.perf_counter() - started

    assert len(connected) == 100
    assert elapsed_seconds < 2


def test_hundred_step_simulation_meets_documented_budget(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    client = TestClient(create_app(runtime, gui_services=_services(tmp_path, runtime)))
    _login(client)
    project = client.post(
        "/api/gui/v1/projects",
        json={"name": "Simulation scale", "draft": _draft()},
        headers=_mutation_headers(client),
    ).json()
    project_id = project["project"]["id"]
    scenario = client.post(
        f"/api/gui/v1/projects/{project_id}/simulation-scenarios",
        json={
            "name": "One hundred deterministic ticks",
            "steps": [{"kind": "advance", "delta_ms": 10} for _ in range(100)],
            "expected": {},
        },
        headers=_mutation_headers(client),
    )
    assert scenario.status_code == 201

    started = time.perf_counter()
    result = client.post(
        f"/api/gui/v1/projects/{project_id}/simulation-scenarios/"
        f"{scenario.json()['id']}/run",
        headers=_mutation_headers(client),
    )

    assert result.status_code == 200
    assert result.json()["hardware_adapters_opened"] is False
    assert time.perf_counter() - started < 5


def test_local_accounts_project_roles_collaboration_and_live_output(
    tmp_path: Path,
) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    admin = TestClient(create_app(runtime, gui_services=services))
    _login(admin)
    created = admin.post(
        "/api/gui/v1/projects",
        json={"name": "Collaborative show", "draft": _draft()},
        headers=_mutation_headers(admin),
    )
    project_id = created.json()["project"]["id"]

    for username, role in (("reviewer", "viewer"), ("operator", "operator")):
        response = admin.put(
            f"/api/gui/v1/users/{username}",
            json={
                "username": username,
                "password": f"{username}-password-2026",
                "role": role,
                "disabled": False,
            },
            headers=_mutation_headers(admin),
        )
        assert response.status_code == 200
    reviewer_membership = admin.put(
        f"/api/gui/v1/projects/{project_id}/members/reviewer",
        json={"role": "viewer", "can_live_output": False},
        headers=_mutation_headers(admin),
    )
    assert reviewer_membership.status_code == 200
    admin.put(
        f"/api/gui/v1/projects/{project_id}/members/operator",
        json={"role": "operator", "can_live_output": False},
        headers=_mutation_headers(admin),
    )

    reviewer = TestClient(create_app(runtime, gui_services=services))
    reviewer_login = reviewer.post(
        "/api/gui/v1/auth/login",
        json={"username": "reviewer", "password": "reviewer-password-2026"},
        headers={"Origin": ORIGIN},
    )
    assert reviewer_login.status_code == 200
    assert [item["id"] for item in reviewer.get("/api/gui/v1/projects").json()] == [
        project_id
    ]
    comment = reviewer.post(
        f"/api/gui/v1/projects/{project_id}/comments",
        json={"revision": 1, "object_id": "ambient", "body": "Raise the contrast."},
        headers=_mutation_headers(reviewer),
    )
    assert comment.status_code == 201
    comment_id = comment.json()["id"]
    denied_resolution = reviewer.put(
        f"/api/gui/v1/projects/{project_id}/comments/{comment_id}",
        json={"state": "resolved"},
        headers=_mutation_headers(reviewer),
    )
    assert denied_resolution.status_code == 403
    denied_draft_update = reviewer.put(
        f"/api/gui/v1/projects/{project_id}/draft",
        json=_draft(),
        headers=_mutation_headers(reviewer, if_match='"1"'),
    )
    assert denied_draft_update.status_code == 403

    admin.put(
        f"/api/gui/v1/projects/{project_id}/members/reviewer",
        json={"role": "designer", "can_live_output": False},
        headers=_mutation_headers(admin),
    )
    resolved = reviewer.put(
        f"/api/gui/v1/projects/{project_id}/comments/{comment_id}",
        json={"state": "resolved"},
        headers=_mutation_headers(reviewer),
    )
    assert resolved.status_code == 200
    allowed_draft_update = reviewer.put(
        f"/api/gui/v1/projects/{project_id}/draft",
        json=_draft(),
        headers=_mutation_headers(reviewer, if_match='"1"'),
    )
    assert allowed_draft_update.status_code == 200
    review_link = reviewer.post(
        f"/api/gui/v1/projects/{project_id}/review-links",
        json={"revision": 2, "expires_at": "2099-01-01T00:00:00+00:00"},
        headers=_mutation_headers(reviewer),
    )
    assert review_link.status_code == 201
    public_review = TestClient(create_app(runtime, gui_services=services)).get(
        f"/api/gui/v1/review-links/{review_link.json()['token']}"
    )
    assert public_review.status_code == 200
    assert public_review.json()["project_name"] == "Collaborative show"
    assert (
        reviewer.post(
            f"/api/gui/v1/projects/{project_id}/presence",
            json={"location": "timeline:ambient"},
            headers=_mutation_headers(reviewer),
        ).status_code
        == 204
    )
    assert reviewer.get(f"/api/gui/v1/projects/{project_id}/presence").json()[0][
        "location"
    ] == "timeline:ambient"

    operator = TestClient(create_app(runtime, gui_services=services))
    assert (
        operator.post(
            "/api/gui/v1/auth/login",
            json={"username": "operator", "password": "operator-password-2026"},
            headers={"Origin": ORIGIN},
        ).status_code
        == 200
    )
    denied_live = operator.post(
        "/api/gui/v1/operate/actions",
        json={"action": "pause"},
        headers=_mutation_headers(operator),
    )
    assert denied_live.status_code == 403
    assert denied_live.json()["detail"]["code"] == "live_output_permission_required"
    admin.put(
        f"/api/gui/v1/projects/{project_id}/members/operator",
        json={"role": "operator", "can_live_output": True},
        headers=_mutation_headers(admin),
    )
    allowed_live = operator.post(
        "/api/gui/v1/operate/actions",
        json={"action": "pause"},
        headers=_mutation_headers(operator),
    )
    assert allowed_live.status_code != 403


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


def test_projector_simulation_overlay_warps_authenticated_review_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)
    review = tmp_path / "review.png"
    Image.new("RGBA", (20, 10), (40, 120, 220, 255)).save(review)
    mapping = SimpleNamespace(
        content_asset_id="asset-review",
        photo_views=(
            SimpleNamespace(
                photo_id="photo-front",
                homography=(0.025, 0, 0.25, 0, 0.05, 0.25, 0, 0, 1),
                points=((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)),
            ),
        ),
        native_width=20,
        native_height=10,
        mesh_vertices=(),
        mesh_triangles=(),
        orientation_degrees=0,
        mirror_x=False,
        mirror_y=False,
        calibration_source="unverified_estimate",
        calibration_revision=3,
    )
    monkeypatch.setattr(services.projector_mapping, "get", lambda _: mapping)
    monkeypatch.setattr(
        services.asset_upload,
        "get_artifact",
        lambda asset_id, kind: (review, "image/png"),
    )

    response = client.get(
        "/api/gui/v1/installation-geometry/projector-front/"
        "projector-simulation-overlay?photo_id=photo-front"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-cueverge-approximation"] == "true"
    assert response.headers["x-cueverge-calibration-source"] == "unverified_estimate"
    assert response.headers["x-cueverge-calibration-revision"] == "3"
    overlay = Image.open(BytesIO(response.content)).convert("RGBA")
    assert overlay.size == (1000, 600)
    assert overlay.getpixel((500, 300)) == (40, 120, 220, 255)
    assert overlay.getpixel((20, 20))[3] == 0

    mapping.content_asset_id = None
    missing = client.get(
        "/api/gui/v1/installation-geometry/projector-front/"
        "projector-simulation-overlay?photo_id=photo-front"
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "projector_simulation_overlay_invalid"


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
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)

    listed = client.get("/api/gui/v1/installations")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    active = listed.json()[0]
    assert active["runtime_active"] is True
    empty_export = client.get(f"/api/gui/v1/installations/{active['id']}/mapper-export")
    assert empty_export.status_code == 200
    assert {resource["resource"] for resource in empty_export.json()["resources"]} == {
        binding.resource for binding in runtime.catalog.installation.resources
    }

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

    photo = client.post(
        f"/api/gui/v1/installations/{installation_id}/photos",
        content=b"synthetic-photo",
        headers={
            **_mutation_headers(client),
            "Content-Type": "image/jpeg",
            "X-Photo-Name": "Wide front",
            "X-Photo-Viewpoint": "Front yard",
            "X-Original-Filename": "front.jpg",
        },
    )
    assert photo.status_code == 201
    photo_id = photo.json()["id"]
    assert photo.json()["revision"] == 1
    content = client.get(f"/api/gui/v1/installation-photos/{photo_id}/content")
    assert content.status_code == 200
    assert content.content == b"synthetic-photo"
    edited_photo = client.put(
        f"/api/gui/v1/installation-photos/{photo_id}",
        json={
            "name": "Wide front",
            "viewpoint": "Front yard",
            "orientation_degrees": 90,
            "crop_x": 0.1,
            "crop_y": 0.2,
            "crop_width": 0.8,
            "crop_height": 0.7,
            "scale_hint": 120.5,
        },
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert edited_photo.status_code == 200
    assert edited_photo.json()["revision"] == 2
    assert edited_photo.json()["orientation_degrees"] == 90
    suggestion = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry-suggestions",
        json={
            "photo_id": photo_id,
            "kind": "projector_surface",
            "name": "Suggested garage surface",
            "points": [
                {"x": 0.1, "y": 0.1},
                {"x": 0.9, "y": 0.1},
                {"x": 0.9, "y": 0.8},
                {"x": 0.1, "y": 0.8},
            ],
            "closed": True,
            "confidence": 0.87,
            "evidence": "Detected rectangular high-contrast plane.",
        },
        headers=_mutation_headers(client),
    )
    assert suggestion.status_code == 201
    assert client.get(f"/api/gui/v1/installations/{installation_id}/geometry").json() == []
    reviewed_suggestion = client.post(
        f"/api/gui/v1/geometry-suggestions/{suggestion.json()['id']}/review",
        headers=_mutation_headers(client),
    )
    assert reviewed_suggestion.json()["state"] == "reviewed"
    assert client.get(f"/api/gui/v1/installations/{installation_id}/geometry").json() == []
    geometry = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry",
        json={
            "photo_id": photo_id,
            "kind": "projector_surface",
            "name": "Garage door",
            "points": [
                {"x": 0.85, "y": 0.12},
                {"x": 0.18, "y": 0.2},
                {"x": 0.12, "y": 0.82},
                {"x": 0.92, "y": 0.9},
            ],
            "closed": True,
        },
        headers=_mutation_headers(client),
    )
    assert geometry.status_code == 201
    geometry_id = geometry.json()["id"]
    mask = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry",
        json={
            "photo_id": photo_id,
            "kind": "mask",
            "name": "Window trim mask",
            "points": [
                {"x": 0.1, "y": 0.1},
                {"x": 0.2, "y": 0.1},
                {"x": 0.2, "y": 0.2},
            ],
            "closed": True,
        },
        headers=_mutation_headers(client),
    )
    assert mask.status_code == 201
    mapping = client.put(
        f"/api/gui/v1/installation-geometry/{geometry_id}/projector-mapping",
        json={
            "resource": "projector.left",
            "native_width": 1920,
            "native_height": 1080,
            "stage_x": 0,
            "stage_y": 0,
            "stage_width": 4.0,
            "stage_height": 2.25,
            "crop_x": 0,
            "crop_y": 0,
            "crop_width": 0.8,
            "crop_height": 1,
            "brightness_limit": 0.75,
            "media_start_delay_ms": 120,
            "orientation_degrees": 90,
            "mirror_x": True,
            "mirror_y": False,
            "mask_semantic_ids": [mask.json()["semantic_id"]],
        },
        headers=_mutation_headers(client),
    )
    assert mapping.status_code == 200
    assert mapping.json()["revision"] == 1
    assert len(mapping.json()["homography"]) == 9
    assert mapping.json()["homography"][8] == 1
    assert mapping.json()["homography"] != [1, 0, 0, 0, 1, 0, 0, 0, 1]
    assert mapping.json()["brightness_limit"] == 0.75
    assert mapping.json()["media_start_delay_ms"] == 120
    assert mapping.json()["orientation_degrees"] == 90
    assert mapping.json()["mirror_x"] is True
    assert mapping.json()["mask_semantic_ids"] == [mask.json()["semantic_id"]]
    assert len(mapping.json()["photo_views"]) == 1
    initial_coverage = client.get(f"/api/gui/v1/installations/{installation_id}/stage-coverage")
    assert "occluded" in {region["kind"] for region in initial_coverage.json()["regions"]}
    maintenance = client.post(
        "/api/gui/v1/operate/actions",
        json={"action": "maintenance"},
        headers=_mutation_headers(client),
    )
    assert maintenance.status_code == 200
    arm_resource = runtime.catalog.installation.resources[0].resource
    arm = client.post(
        "/api/gui/v1/maintenance/output-arms",
        json={
            "resource": arm_resource,
            "confirmation": arm_resource,
            "duration_seconds": 15,
        },
        headers=_mutation_headers(client),
    )
    assert arm.status_code == 201
    assert arm.json()["state"] == "active"
    assert arm.json()["hardware_command_sent"] is False
    assert arm.json()["device_identity"]
    stopped_arm = client.post(
        f"/api/gui/v1/maintenance/output-arms/{arm.json()['id']}/stop",
        headers=_mutation_headers(client),
    )
    assert stopped_arm.json()["state"] == "stopped"
    assert stopped_arm.json()["stop_action"] == "stop media playback"
    timing = client.post(
        "/api/gui/v1/timing-calibrations",
        json={
            "target_kind": "projector_mapping",
            "geometry_id": geometry_id,
            "adjustment_kind": "release",
            "test_asset_id": "timing-reference",
            "test_asset_version": "sha256:fixture",
            "observed_variability_ms": 12.5,
            "notes": "Observed from the front yard.",
            "context": {
                "hardware": "projector-a",
                "routing": "hdmi-1",
                "display_mode": "1920x1080@60",
                "media_settings": "mpv-default",
                "environment": "night",
            },
        },
        headers=_mutation_headers(client),
    )
    assert timing.status_code == 201
    assert timing.json()["baseline_ms"] == 0
    adjusted = client.put(
        f"/api/gui/v1/timing-calibrations/{timing.json()['id']}",
        json={"proposed_ms": -85},
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert adjusted.status_code == 200
    assert adjusted.json()["baseline_ms"] == 0
    assert adjusted.json()["proposed_ms"] == -85
    accepted = client.post(
        f"/api/gui/v1/timing-calibrations/{timing.json()['id']}/accept",
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "accepted"
    persisted_mapping = client.get(
        f"/api/gui/v1/installation-geometry/{geometry_id}/projector-mapping"
    )
    assert persisted_mapping.json()["media_release_delay_ms"] == -85
    calibration_review = client.post(
        "/api/gui/v1/accepted-calibrations/review",
        json={
            "target_kind": "projector_mapping",
            "geometry_id": geometry_id,
            "context": {
                "hardware": "projector-a",
                "routing": "hdmi-2",
                "display_mode": "1920x1080@60",
                "media_settings": "mpv-default",
                "environment": "night",
            },
        },
        headers=_mutation_headers(client),
    )
    assert calibration_review.status_code == 200
    assert calibration_review.json()[0]["test_asset_version"] == "sha256:fixture"
    assert "Routing changed." in calibration_review.json()[0]["stale_reasons"]
    cancelled_timing = client.post(
        "/api/gui/v1/timing-calibrations",
        json={
            "target_kind": "projector_mapping",
            "geometry_id": geometry_id,
            "adjustment_kind": "onset",
        },
        headers=_mutation_headers(client),
    )
    changed_timing = client.put(
        f"/api/gui/v1/timing-calibrations/{cancelled_timing.json()['id']}",
        json={"proposed_ms": 500},
        headers=_mutation_headers(client, if_match='"1"'),
    )
    cancelled = client.post(
        f"/api/gui/v1/timing-calibrations/{cancelled_timing.json()['id']}/cancel",
        headers=_mutation_headers(client, if_match=f'"{changed_timing.json()["revision"]}"'),
    )
    assert cancelled.json()["state"] == "cancelled"
    assert (
        client.get(f"/api/gui/v1/installation-geometry/{geometry_id}/projector-mapping").json()[
            "media_start_delay_ms"
        ]
        == 120
    )
    calibration_photo = client.post(
        f"/api/gui/v1/installations/{installation_id}/photos",
        content=b"fresh-calibration-photo",
        headers={
            **_mutation_headers(client),
            "Content-Type": "image/jpeg",
            "X-Photo-Name": "Fresh calibration",
            "X-Photo-Viewpoint": "Front yard calibrated",
            "X-Original-Filename": "calibration.jpg",
        },
    )
    assert calibration_photo.status_code == 201
    calibration_photo_id = calibration_photo.json()["id"]
    edited_geometry = client.put(
        f"/api/gui/v1/installation-geometry/{geometry_id}",
        json={
            "photo_id": calibration_photo_id,
            "kind": "projector_surface",
            "name": "Garage projection surface",
            "points": [
                *geometry.json()["points"],
                {"x": 0.5, "y": 0.55},
            ],
            "closed": True,
        },
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert edited_geometry.status_code == 200
    assert edited_geometry.json()["revision"] == 2
    assert edited_geometry.json()["photo_id"] == calibration_photo_id
    subdivided = client.put(
        f"/api/gui/v1/installation-geometry/{geometry_id}/projector-mapping",
        json={
            "resource": "projector.left",
            "native_width": 1920,
            "native_height": 1080,
            "stage_x": 0,
            "stage_y": 0,
            "stage_width": 4.0,
            "stage_height": 2.25,
            "crop_x": 0,
            "crop_y": 0,
            "crop_width": 0.8,
            "crop_height": 1,
            "surface_model": "subdivided",
            "content_asset_id": None,
            "fit_mode": "stretch",
            "overscan": True,
            "calibration_source": "measured_grid",
            "calibration_revision": 3,
            "mesh_vertices": [
                [0, 0, 0, 0],
                [1, 0, 4, 0],
                [1, 1, 4, 2.25],
                [0, 1, 0, 2.25],
                [0.5, 0.5, 2, 1.2],
            ],
            "mesh_triangles": [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]],
        },
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert subdivided.status_code == 200
    assert subdivided.json()["homography"] is None
    assert subdivided.json()["diagnostic"] is None
    assert subdivided.json()["calibration_source"] == "measured_grid"
    assert subdivided.json()["calibration_revision"] == 3
    assert len(subdivided.json()["mesh_triangles"]) == 4
    assert "Stretch distortion will be visible." in subdivided.json()["fit_diagnostics"]
    assert "Overscan is enabled" in " ".join(subdivided.json()["fit_diagnostics"])
    linked_geometry = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry",
        json={
            "photo_id": photo_id,
            "kind": "projector_surface",
            "name": "Garage projection in original view",
            "points": edited_geometry.json()["points"],
            "closed": True,
            "semantic_id": edited_geometry.json()["semantic_id"],
        },
        headers=_mutation_headers(client),
    )
    assert linked_geometry.status_code == 201
    assert linked_geometry.json()["semantic_id"] == edited_geometry.json()["semantic_id"]
    assert linked_geometry.json()["landmark_ids"] == edited_geometry.json()["landmark_ids"]
    linked_geometry_id = linked_geometry.json()["id"]
    second_mapping = client.put(
        f"/api/gui/v1/installation-geometry/{linked_geometry_id}/projector-mapping",
        json={
            "resource": "projector.right",
            "native_width": 1920,
            "native_height": 1080,
            "stage_x": 3,
            "stage_y": 1,
            "stage_width": 4,
            "stage_height": 2.25,
            "surface_model": "subdivided",
        },
        headers=_mutation_headers(client),
    )
    assert second_mapping.status_code == 200
    multi_view_mapping = client.get(
        f"/api/gui/v1/installation-geometry/{geometry_id}/projector-mapping"
    )
    assert len(multi_view_mapping.json()["photo_views"]) == 2
    assert {view["photo_id"] for view in multi_view_mapping.json()["photo_views"]} == {
        photo_id,
        calibration_photo_id,
    }
    overlap = client.put(
        f"/api/gui/v1/installations/{installation_id}/projector-overlaps",
        json={
            "first_geometry_id": geometry_id,
            "second_geometry_id": linked_geometry_id,
            "stage_points": [[3, 1], [4, 1], [4, 2.25], [3, 2.25]],
            "ramp_width": 0.2,
            "overlap_gamma": 1.1,
            "first_brightness": 0.9,
            "second_brightness": 0.85,
            "first_white": [1, 0.98, 0.95],
            "second_white": [0.96, 1, 0.97],
            "first_color_matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1],
            "second_color_matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1],
            "first_black_level": 0.02,
            "second_black_level": 0.04,
        },
        headers=_mutation_headers(client),
    )
    assert overlap.status_code == 200
    assert overlap.json()["revision"] == 1
    assert overlap.json()["overlap_gamma"] == 1.1
    assert "Combined overlap brightness may appear double-bright." in overlap.json()["diagnostics"]
    assert "Projector black levels do not match." in overlap.json()["diagnostics"]
    coverage = client.get(f"/api/gui/v1/installations/{installation_id}/stage-coverage")
    assert coverage.status_code == 200
    region_kinds = {region["kind"] for region in coverage.json()["regions"]}
    assert {"covered", "overlap", "uncovered"} <= region_kinds
    renamed_link = client.put(
        f"/api/gui/v1/installation-geometry/{linked_geometry_id}",
        json={
            "photo_id": photo_id,
            "kind": "projector_surface",
            "name": "Shared garage projection",
            "points": [
                {"x": 0.8, "y": 0.15},
                *linked_geometry.json()["points"][1:],
            ],
            "closed": True,
            "semantic_id": linked_geometry.json()["semantic_id"],
            "color": "#ff8800",
            "label_visible": True,
            "locked": False,
            "hidden": False,
            "z_index": 2,
            "direction": "forward",
            "start_index": None,
            "pixel_count": None,
        },
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert renamed_link.status_code == 200
    linked_instances = [
        item
        for item in client.get(f"/api/gui/v1/installations/{installation_id}/geometry").json()
        if item["semantic_id"] == linked_geometry.json()["semantic_id"]
    ]
    assert {item["name"] for item in linked_instances} == {"Shared garage projection"}
    assert {item["color"] for item in linked_instances} == {"#ff8800"}
    assert (
        len(
            {
                tuple((point["x"], point["y"]) for point in item["points"])
                for item in linked_instances
            }
        )
        == 2
    )
    duplicate_mapping = client.put(
        f"/api/gui/v1/installation-geometry/{linked_geometry_id}/projector-mapping",
        json={
            "resource": "projector.left",
            "native_width": 1920,
            "native_height": 1080,
            "stage_x": 3,
            "stage_y": 1,
            "stage_width": 4,
            "stage_height": 2.25,
            "surface_model": "subdivided",
        },
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert duplicate_mapping.status_code == 200
    diagnostics = services.mapper_export.validate(installation_id)
    assert "multiply_assigned_projector_resource" in {diagnostic.code for diagnostic in diagnostics}
    with pytest.raises(MapperExportError):
        services.mapper_export.export(installation_id)
    restored_mapping = client.put(
        f"/api/gui/v1/installation-geometry/{linked_geometry_id}/projector-mapping",
        json={
            "resource": "projector.right",
            "native_width": 1920,
            "native_height": 1080,
            "stage_x": 3,
            "stage_y": 1,
            "stage_width": 4,
            "stage_height": 2.25,
            "surface_model": "subdivided",
        },
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert restored_mapping.status_code == 200
    assert (
        client.delete(
            f"/api/gui/v1/installation-geometry/{linked_geometry_id}",
            headers=_mutation_headers(client, if_match='"2"'),
        ).status_code
        == 204
    )
    pixel_segment = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry",
        json={
            "photo_id": photo_id,
            "kind": "pixel_segment",
            "name": "Roofline pixels",
            "points": [{"x": 0.1, "y": 0.9}, {"x": 0.9, "y": 0.9}],
            "closed": False,
            "direction": "reverse",
            "start_index": 100,
            "pixel_count": 50,
        },
        headers=_mutation_headers(client),
    )
    assert pixel_segment.status_code == 201
    assert pixel_segment.json()["direction"] == "reverse"
    assert pixel_segment.json()["start_index"] == 100
    assert pixel_segment.json()["pixel_count"] == 50
    lighting_surface = client.put(
        f"/api/gui/v1/installation-geometry/{pixel_segment.json()['id']}/lighting-surface",
        json={
            "resource": "roofline.demo",
            "local_width": 10,
            "local_height": 2,
            "local_points": [[1, 1], [9, 1]],
            "stage_matrix": [1, 0, 0, 1, 5, 3],
            "fixture_id": "roofline",
            "group_id": "front",
            "path_id": "roof-path",
            "strand_id": "strand-a",
            "calibration_id": "cal-1",
            "pixel_footprint_width": 0.02,
            "pixel_footprint_height": 0.03,
            "brightness_limit": 0.6,
            "timing_offset_ms": -45,
        },
        headers=_mutation_headers(client),
    )
    assert lighting_surface.status_code == 200
    assert len(lighting_surface.json()["pixels"]) == 50
    assert lighting_surface.json()["pixels"][0]["output_index"] == 100
    assert lighting_surface.json()["pixels"][-1]["output_index"] == 149
    assert lighting_surface.json()["pixels"][0]["stage_x"] == 14
    assert lighting_surface.json()["brightness_limit"] == 0.6
    assert lighting_surface.json()["timing_offset_ms"] == -45
    corrected_surface = client.put(
        f"/api/gui/v1/installation-geometry/{pixel_segment.json()['id']}/lighting-surface",
        json={
            "resource": "roofline.demo",
            "local_width": 10,
            "local_height": 2,
            "local_points": [[1, 1], [9, 1]],
            "stage_matrix": [1, 0, 0, 1, 5, 3],
            "fixture_id": "roofline",
            "group_id": "front",
            "path_id": "roof-path",
            "strand_id": "strand-a",
            "calibration_id": "cal-1",
            "pixel_footprint_width": 0.02,
            "pixel_footprint_height": 0.03,
            "brightness_limit": 0.6,
            "timing_offset_ms": -45,
            "pixel_overrides": [{"output_index": 120, "local_x": 4.2, "local_y": 1.4}],
        },
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert corrected_surface.status_code == 200
    corrected_pixel = next(
        pixel for pixel in corrected_surface.json()["pixels"] if pixel["output_index"] == 120
    )
    assert corrected_pixel["local_x"] == 4.2
    assert corrected_pixel["local_y"] == 1.4
    assert corrected_pixel["stage_x"] == 9.2
    assert corrected_surface.json()["pixel_overrides"] == [
        {"output_index": 120, "local_x": 4.2, "local_y": 1.4}
    ]
    second_strand = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry",
        json={
            "photo_id": photo_id,
            "kind": "pixel_segment",
            "name": "Window pixels",
            "points": [{"x": 0.2, "y": 0.6}, {"x": 0.8, "y": 0.6}],
            "closed": False,
            "direction": "forward",
            "start_index": 200,
            "pixel_count": 25,
        },
        headers=_mutation_headers(client),
    )
    assert second_strand.status_code == 201
    second_surface = client.put(
        f"/api/gui/v1/installation-geometry/{second_strand.json()['id']}/lighting-surface",
        json={
            "resource": "roofline.demo",
            "local_width": 10,
            "local_height": 2,
            "local_points": [[2, 0.5], [8, 0.5]],
            "stage_matrix": [1, 0, 0, 1, 5, 3],
            "group_id": "front",
            "strand_id": "strand-b",
            "pixel_footprint_width": 0.02,
            "pixel_footprint_height": 0.03,
        },
        headers=_mutation_headers(client),
    )
    assert second_surface.status_code == 200
    fixture = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry",
        json={
            "photo_id": photo_id,
            "kind": "fixture",
            "name": "Window center",
            "points": [{"x": 0.5, "y": 0.5}],
            "stage_points": [[2, 1]],
            "closed": False,
        },
        headers=_mutation_headers(client),
    )
    path = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry",
        json={
            "photo_id": photo_id,
            "kind": "path",
            "name": "Window sweep",
            "points": [{"x": 0.2, "y": 0.5}, {"x": 0.8, "y": 0.5}],
            "stage_points": [[1, 1], [3, 1]],
            "closed": False,
        },
        headers=_mutation_headers(client),
    )
    zone = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry",
        json={
            "photo_id": photo_id,
            "kind": "safety_zone",
            "name": "No output walkway",
            "points": [
                {"x": 0.1, "y": 0.8},
                {"x": 0.4, "y": 0.8},
                {"x": 0.4, "y": 0.95},
            ],
            "stage_points": [[0, 1.8], [2, 1.8], [2, 2.2]],
            "closed": True,
        },
        headers=_mutation_headers(client),
    )
    assert fixture.status_code == 201
    assert path.status_code == 201
    assert zone.status_code == 201
    mapped_export = services.mapper_export.export(installation_id)
    assert mapped_export["fixtures"][0]["stage_points"] == [[2, 1]]
    assert mapped_export["paths"][0]["stage_points"] == [[1, 1], [3, 1]]
    assert mapped_export["zones"][0]["kind"] == "safety_zone"
    assert mapped_export["zones"][0]["stage_points"] == [
        [0, 1.8],
        [2, 1.8],
        [2, 2.2],
    ]
    assert mapped_export["projectors"][0]["media_start_delay_ms"] == 0
    assert (
        next(
            surface
            for surface in mapped_export["lighting_surfaces"]
            if surface["strand_id"] == "strand-a"
        )["timing_offset_ms"]
        == -45
    )
    assert {surface["strand_id"] for surface in mapped_export["lighting_surfaces"]} == {
        "strand-a",
        "strand-b",
    }
    corrected_export = next(
        surface
        for surface in mapped_export["lighting_surfaces"]
        if surface["strand_id"] == "strand-a"
    )
    assert corrected_export["pixel_overrides"] == [
        {"output_index": 120, "local_x": 4.2, "local_y": 1.4}
    ]
    assert "photo" not in json.dumps(mapped_export)
    linked_pixels = client.post(
        f"/api/gui/v1/installations/{installation_id}/geometry",
        json={
            "photo_id": calibration_photo_id,
            "kind": "pixel_segment",
            "name": "Roofline pixels close-up",
            "points": [{"x": 0.2, "y": 0.7}, {"x": 0.8, "y": 0.75}],
            "closed": False,
            "semantic_id": pixel_segment.json()["semantic_id"],
            "direction": "reverse",
            "start_index": 100,
            "pixel_count": 50,
        },
        headers=_mutation_headers(client),
    )
    assert linked_pixels.status_code == 201
    linked_surface = client.get(
        f"/api/gui/v1/installation-geometry/{linked_pixels.json()['id']}/lighting-surface"
    )
    assert linked_surface.status_code == 200
    assert linked_surface.json()["geometry_id"] == linked_pixels.json()["id"]
    assert linked_surface.json()["pixels"] == corrected_surface.json()["pixels"]
    assert linked_pixels.json()["points"] != pixel_segment.json()["points"]
    assert (
        client.delete(
            f"/api/gui/v1/installation-geometry/{second_strand.json()['id']}",
            headers=_mutation_headers(client, if_match='"1"'),
        ).status_code
        == 204
    )
    assert (
        client.delete(
            f"/api/gui/v1/installation-geometry/{linked_pixels.json()['id']}",
            headers=_mutation_headers(client, if_match='"1"'),
        ).status_code
        == 204
    )
    assert (
        client.delete(
            f"/api/gui/v1/installation-geometry/{pixel_segment.json()['id']}",
            headers=_mutation_headers(client, if_match='"1"'),
        ).status_code
        == 204
    )
    removed_geometry = client.delete(
        f"/api/gui/v1/installation-geometry/{geometry_id}",
        headers=_mutation_headers(client, if_match='"3"'),
    )
    assert removed_geometry.status_code == 204
    assert (
        client.delete(
            f"/api/gui/v1/installation-geometry/{mask.json()['id']}",
            headers=_mutation_headers(client, if_match='"1"'),
        ).status_code
        == 204
    )
    for item in (fixture, path, zone):
        assert (
            client.delete(
                f"/api/gui/v1/installation-geometry/{item.json()['id']}",
                headers=_mutation_headers(client, if_match='"1"'),
            ).status_code
            == 204
        )
    removed_photo = client.delete(
        f"/api/gui/v1/installation-photos/{photo_id}",
        headers=_mutation_headers(client, if_match='"2"'),
    )
    assert removed_photo.status_code == 204

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


def test_project_rejects_cyclic_composition_graph(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    client = TestClient(create_app(runtime, gui_services=_services(tmp_path, runtime)))
    _login(client)

    created = client.post(
        "/api/gui/v1/projects",
        json={
            "name": "Cyclic compositions",
            "draft": _draft(
                editor_metadata={
                    "compositions": {
                        "composition-a": {
                            "id": "composition-a",
                            "name": "A",
                            "duration_ms": 1000,
                            "space_kind": "stage",
                            "target_id": "shared-stage",
                            "width": 10,
                            "height": 4,
                            "children": ["composition-b"],
                        },
                        "composition-b": {
                            "id": "composition-b",
                            "name": "B",
                            "duration_ms": 1000,
                            "space_kind": "projector_native",
                            "target_id": "projector.left",
                            "width": 1920,
                            "height": 1080,
                            "children": ["composition-a"],
                        },
                    }
                }
            ),
        },
        headers=_mutation_headers(client),
    )

    assert created.status_code == 422
    assert "cannot contain cycles" in created.text
    invalid_instance = client.post(
        "/api/gui/v1/projects",
        json={
            "name": "Invalid composition instance",
            "draft": _draft(
                editor_metadata={
                    "compositions": {
                        "composition-a": {
                            "id": "composition-a",
                            "name": "A",
                            "duration_ms": 1000,
                            "space_kind": "stage",
                            "target_id": "shared-stage",
                            "width": 10,
                            "height": 4,
                            "children": [],
                        }
                    },
                    "composition_instances": {
                        "ambient": [
                            {
                                "id": "instance-a",
                                "composition_id": "composition-a",
                                "start_offset_ms": 0,
                                "time_scale": 0,
                                "parameter_overrides": {},
                                "placement": {"x": 0, "y": 0},
                            }
                        ]
                    },
                }
            ),
        },
        headers=_mutation_headers(client),
    )
    assert invalid_instance.status_code == 422
    assert "time scale" in invalid_instance.text


def test_project_validates_timeline_synchronization_anchors(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    client = TestClient(create_app(runtime, gui_services=_services(tmp_path, runtime)))
    _login(client)
    valid_metadata = {
        "timeline_cue_presets": [
            {
                "id": "preset-impact",
                "name": "Impact",
                "lighting": {
                    "primitive": "solid",
                    "duration_ms": 250,
                    "target": None,
                    "color": "#FFFFFF",
                    "brightness": 1,
                },
            }
        ],
        "timeline_markers": {
            "ambient": [{"id": "marker-impact", "name": "Impact", "time_ms": 1000}]
        },
        "timeline_cues": {
            "ambient": [
                {
                    "id": "cue-light",
                    "name": "Lighting impact",
                    "kind": "lighting",
                    "track_id": "lighting:roofline",
                    "time_ms": None,
                    "marker_id": "marker-impact",
                    "marker_offset_ms": -100,
                    "lighting": {
                        "primitive": "solid",
                        "duration_ms": 250,
                        "target": None,
                        "color": "#FFFFFF",
                        "brightness": 1,
                        "fade_in_ms": 0,
                        "fade_out_ms": 0,
                        "period_ms": 200,
                        "width_pixels": 1,
                        "density": 0.1,
                        "seed": 0,
                        "reverse": False,
                        "blend_mode": "replace",
                        "precedence": 0,
                    },
                }
            ]
        },
    }
    created = client.post(
        "/api/gui/v1/projects",
        json={
            "name": "Synchronized cues",
            "draft": _draft(editor_metadata=valid_metadata),
        },
        headers=_mutation_headers(client),
    )
    assert created.status_code == 201

    invalid_metadata = {
        **valid_metadata,
        "timeline_cues": {
            "ambient": [
                {
                    "id": "cue-light",
                    "name": "Lighting impact",
                    "kind": "lighting",
                    "track_id": "lighting:roofline",
                    "time_ms": None,
                    "marker_id": "missing-marker",
                    "marker_offset_ms": 0,
                    "lighting": {
                        "primitive": "solid",
                        "duration_ms": 250,
                        "target": None,
                        "color": "#FFFFFF",
                        "brightness": 1,
                    },
                }
            ]
        },
    }
    rejected = client.post(
        "/api/gui/v1/projects",
        json={
            "name": "Broken synchronized cues",
            "draft": _draft(editor_metadata=invalid_metadata),
        },
        headers=_mutation_headers(client),
    )
    assert rejected.status_code == 422
    assert "marker must exist in the same editing space" in rejected.text

    unsupported_property = client.post(
        "/api/gui/v1/projects",
        json={
            "name": "Preview-only keyframes",
            "draft": _draft(
                editor_metadata={
                    "timeline_property_keyframes": {
                        "ambient": [{"property": "lighting.brightness"}]
                    }
                }
            ),
        },
        headers=_mutation_headers(client),
    )
    assert unsupported_property.status_code == 422
    assert "generic property keyframes are unsupported" in unsupported_property.text

    hidden_preset_reference = client.post(
        "/api/gui/v1/projects",
        json={
            "name": "Hidden preset reference",
            "draft": _draft(
                editor_metadata={
                    **valid_metadata,
                    "timeline_cues": {
                        "ambient": [
                            {
                                **valid_metadata["timeline_cues"]["ambient"][0],
                                "preset_id": "preset-impact",
                            }
                        ]
                    },
                }
            ),
        },
        headers=_mutation_headers(client),
    )
    assert hidden_preset_reference.status_code == 422
    assert "unsupported timeline cue fields" in hidden_preset_reference.text


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
    draft = _draft(editor_metadata={"panel_layout": {"timeline_height": 320}})
    draft.update(
        {
            "program": {
                "schema_version": 1,
                "initial_scene_id": "ambient",
                "default_pool_id": "ambient",
                "recent_history_size": 1,
                "random_seed": 42,
                "pools": [
                    {
                        "id": "ambient",
                        "category": "ambient",
                        "scenes": [
                            {
                                "scene_id": "ambient",
                                "weight": 1,
                                "cooldown_ms": 0,
                                "available": True,
                                "conditions": {},
                            }
                        ],
                    }
                ],
            },
            "audience": {
                "schema_version": 1,
                "labels": ["person"],
                "cameras": ["camera.demo"],
                "zones": [],
                "observation_freshness_ms": 5000,
                "arrival_confirmation_ms": 1000,
                "absence_grace_ms": 5000,
                "long_idle_ms": 300000,
            },
            "opportunities": {
                "schema_version": 1,
                "opportunities": [
                    {
                        "id": "welcome",
                        "scene_id": "ambient",
                        "trigger_event": "audience.arrived",
                        "ttl_ms": 10000,
                    }
                ],
            },
            "encounter": {
                "schema_version": 1,
                "initial_state": "ambient",
                "cooldown_state": "cooldown",
                "ambient_scene_id": "ambient",
                "encounter_timeout_ms": 30000,
                "cooldown_ms": 5000,
                "debounce_ms": 500,
                "event_ttl_ms": 3000,
                "transitions": [
                    {
                        "event_type": "audience.arrived",
                        "from_states": ["ambient"],
                        "to_state": "engaged",
                        "scene_id": "ambient",
                    }
                ],
            },
        }
    )
    created = client.post(
        "/api/gui/v1/projects",
        json={
            "name": "Compiler fixture",
            "draft": draft,
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
    catalog = load_show_package(package_root)
    assert catalog.require("ambient").definition.name == "Ambient"
    assert catalog.program is not None
    assert catalog.program.random_seed == 42
    assert catalog.audience is not None
    assert catalog.audience.cameras == ("camera.demo",)
    assert catalog.opportunities is not None
    assert catalog.opportunities.opportunities[0].id == "welcome"
    assert catalog.encounter is not None
    assert catalog.encounter.initial_state == "ambient"
    render_pipeline = json.loads(
        (package_root / "runtime" / "render-pipeline.json").read_text(encoding="utf-8")
    )
    assert render_pipeline["render_pipeline_revision"] == 1
    assert render_pipeline["consumers"] == ["preview", "bake"]
    assert [stage["id"] for stage in render_pipeline["stages"]] == [
        "decode_conform",
        "logical_composite",
        "physical_object_mapping",
        "projector_native_warp",
        "overlap_blend",
        "projector_color",
        "audio_encode_mux",
    ]
    render_verification = json.loads(
        (package_root / "runtime" / "render-verification.json").read_text(encoding="utf-8")
    )
    assert render_verification["verification_policy_revision"] == 1
    assert render_verification["frame"] == {
        "mean_rgb_error_limit": 1 / 255,
        "max_rgb_error_limit": 4 / 255,
    }
    assert render_verification["audio"] == {
        "peak_error_dbfs_limit": -60.0,
        "loudness_error_lufs_limit": 0.2,
    }
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


def test_publication_requires_approval_and_writes_reproducible_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _services(tmp_path, ControllerRuntime())
    revision = services.repository.create_project(
        name="Publication fixture",
        draft=DraftProject.model_validate(_draft()),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-16T12:00:00Z",
    )
    diagnostics = services.publication.validate(
        revision.project.id,
        revision.revision,
    )
    assert "resource_unassigned" in {item.code for item in diagnostics}
    with pytest.raises(PublicationError, match="approved"):
        services.publication.publish(
            project_id=revision.project.id,
            revision=revision.revision,
            actor="admin",
            occurred_at="2026-10-16T12:01:00Z",
        )

    release = services.publication.update_release(
        project_id=revision.project.id,
        revision=revision.revision,
        name="Opening night",
        summary="Approved deterministic package.",
        approval_state="approved",
        actor="admin",
        occurred_at="2026-10-16T12:02:00Z",
    )
    assert release.approved_by == "admin"
    monkeypatch.setattr(
        services.publication,
        "validate",
        lambda _project_id, _revision: (
            PublicationDiagnostic(
                "validation_complete",
                "info",
                "Fixture validation completed.",
            ),
        ),
    )
    package = services.publication.publish(
        project_id=revision.project.id,
        revision=revision.revision,
        actor="admin",
        occurred_at="2026-10-16T12:03:00Z",
    )
    manifest_path = (
        tmp_path
        / "published"
        / revision.project.id
        / package.digest_sha256
        / "publication-manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_revision"] == revision.revision
    assert manifest["creator"] == "admin"
    assert manifest["revision_name"] == "Opening night"
    assert manifest["package_digest_sha256"] == package.digest_sha256

    staged = asyncio.run(
        services.publication.stage(package.id, "2026-10-16T12:04:00Z")
    )
    assert staged.state == "staged"
    with pytest.raises(PublicationError, match="simulation mode"):
        asyncio.run(
            services.publication.activate(
                package.id,
                occurred_at="2026-10-16T12:05:00Z",
                simulation_mode=True,
            )
        )


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"status": "quarantined", "scan_status": "quarantined"}, "asset_quarantined"),
        (
            {"status": "failed", "error": "ffprobe rejected the stream"},
            "asset_processing_failed",
        ),
        ({"status": "uploading"}, "asset_processing_incomplete"),
        ({"original_file_status": "missing"}, "asset_original_missing"),
        ({"original_file_status": "size_mismatch"}, "asset_original_size_mismatch"),
        ({"original_file_status": "pending"}, "asset_original_unverified"),
        ({"digest_sha256": None}, "asset_checksum_missing"),
    ],
)
def test_publication_reports_exact_asset_blocker_and_remediation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, object],
    expected_code: str,
) -> None:
    services = _services(tmp_path, ControllerRuntime())
    document = _draft()
    document["scenes"][0]["media"]["asset"] = "asset-1"
    revision = services.repository.create_project(
        name="Asset diagnostics fixture",
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-16T12:00:00Z",
    )
    asset = StoredAsset(
        id="asset-1",
        project_id=revision.project.id,
        name="Opening film",
        status="ready",
        current_revision=1,
        metadata_revision=1,
        owner=None,
        license=None,
        provenance=None,
        review_status="approved",
        expires_on=None,
        folder=None,
        tags=(),
        favorite=False,
        error=None,
        digest_sha256="a" * 64,
        size_bytes=1024,
        media_type="video/mp4",
        original_filename="opening.mp4",
        scan_status="clean",
        metadata={},
        created_at="2026-10-16T12:00:00Z",
        updated_at="2026-10-16T12:00:00Z",
        original_file_status="available",
    )
    monkeypatch.setattr(
        services.asset_upload,
        "list_assets",
        lambda _project_id: (replace(asset, **changes),),
    )

    diagnostics = services.publication.validate(
        revision.project.id,
        revision.revision,
    )
    diagnostic = next(item for item in diagnostics if item.code == expected_code)
    assert "before publishing" in diagnostic.message


def test_publication_reports_missing_asset_with_relink_remediation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = _services(tmp_path, ControllerRuntime())
    document = _draft()
    document["scenes"][0]["media"]["asset"] = "missing-asset"
    revision = services.repository.create_project(
        name="Missing asset fixture",
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-16T12:00:00Z",
    )
    monkeypatch.setattr(services.asset_upload, "list_assets", lambda _project_id: ())

    diagnostics = services.publication.validate(
        revision.project.id,
        revision.revision,
    )
    diagnostic = next(item for item in diagnostics if item.code == "asset_missing")
    assert "Relink" in diagnostic.message


def test_render_job_api_requires_auth_csrf_and_reports_invalid_composition(
    tmp_path: Path,
) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))
    unauthorized = client.get("/api/gui/v1/projects/missing/render-jobs")
    assert unauthorized.status_code == 401
    _login(client)
    project = client.post(
        "/api/gui/v1/projects/from-active",
        json={"name": "Render API fixture"},
        headers=_mutation_headers(client),
    ).json()
    project_id = project["project"]["id"]

    without_csrf = client.post(
        f"/api/gui/v1/projects/{project_id}/render-jobs",
        json={"composition_id": "missing"},
        headers={"Origin": ORIGIN},
    )
    assert without_csrf.status_code == 403
    invalid = client.post(
        f"/api/gui/v1/projects/{project_id}/render-jobs",
        json={"composition_id": "missing"},
        headers=_mutation_headers(client),
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "render_job_invalid"
    assert client.get(f"/api/gui/v1/projects/{project_id}/render-jobs").json() == []


def test_compilation_flattens_compositions_and_reports_unsupported_constructs(
    tmp_path: Path,
) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    client = TestClient(create_app(runtime, gui_services=services))
    _login(client)
    composition = {
        "id": "impact",
        "name": "Impact",
        "duration_ms": 1000,
        "space_kind": "stage",
        "target_id": "shared-stage",
        "width": 10,
        "height": 4,
        "local_time_offset_ms": 500,
        "children": [],
        "render_settings": {
            "render_pipeline_revision": 1,
            "render_width": 1920,
            "render_height": 1080,
            "frame_rate_numerator": 30000,
            "frame_rate_denominator": 1001,
            "working_color_space": "linear_srgb",
            "working_alpha_mode": "premultiplied",
            "scaling_filter": "lanczos",
            "output_background_color": "#000000",
            "media_end_behavior": "hold_last_frame",
            "output_profile": {
                "preset_id": "low_power_h264",
                "container": "mp4",
                "video_codec": "h264",
                "video_profile": "main",
                "video_level": "4.0",
                "pixel_format": "yuv420p",
                "max_video_bitrate_kbps": 8000,
                "audio_codec": "aac",
                "audio_sample_rate": 48000,
                "audio_channel_layout": "stereo",
                "loudness_target_lufs": -16,
                "keyframe_interval_frames": 60,
                "b_frames": 0,
                "hardware_decoder_compatibility": "h264_main_level_4_yuv420p",
            },
        },
    }
    metadata = {
        "compositions": {"impact": composition},
        "scene_compositions": {"ambient": ["impact"]},
        "timeline_tracks": {
            "impact": [
                {
                    "id": "fixture-track",
                    "kind": "fixture",
                    "name": "Window fixture",
                    "resource_binding": "fixture.window",
                }
            ]
        },
        "timeline_markers": {"impact": [{"id": "impact-marker", "name": "Impact", "time_ms": 100}]},
        "timeline_transform_layers": {
            "impact": [
                {
                    "layer_schema_version": 1,
                    "id": "ghost-layer",
                    "name": "Ghost",
                    "kind": "image",
                    "source_id": "ghost-asset",
                    "track_id": "fixture-track",
                    "start_ms": 200,
                    "duration_ms": 500,
                    "in_point_ms": 100,
                    "out_point_ms": 600,
                    "z_index": 2,
                    "fade_in_ms": 50,
                    "fade_out_ms": 75,
                    "audio": None,
                    "alpha_interpretation": "straight",
                    "source_color_space": "srgb",
                    "scaling_behavior": "fit",
                    "keyframes": [
                        {
                            "id": "ghost-start",
                            "time_ms": 0,
                            "position": [0, 0],
                            "scale": [1, 1],
                            "rotation_deg": 0,
                            "anchor": [0.5, 0.5],
                            "opacity": 0.5,
                            "crop": [0, 0, 0, 0],
                            "interpolation": {
                                "kind": "bezier",
                                "temporal_bezier": [0.25, 0.1, 0.25, 1],
                                "spatial_in": [-1, 0],
                                "spatial_out": [1, 0],
                            },
                        },
                        {
                            "id": "ghost-end",
                            "time_ms": 500,
                            "position": [4, 2],
                            "scale": [1.5, 0.75],
                            "rotation_deg": 15,
                            "anchor": [0.5, 0.5],
                            "opacity": 1,
                            "crop": [0.1, 0, 0.1, 0],
                            "interpolation": {
                                "kind": "ease_out",
                                "temporal_bezier": None,
                                "spatial_in": None,
                                "spatial_out": None,
                            },
                        },
                    ],
                }
            ]
        },
        "timeline_cues": {
            "impact": [
                {
                    "id": "impact-cue",
                    "name": "Window flash",
                    "kind": "lighting",
                    "track_id": "fixture-track",
                    "time_ms": None,
                    "marker_id": "impact-marker",
                    "marker_offset_ms": 50,
                    "lighting": {
                        "primitive": "pulse",
                        "duration_ms": 300,
                        "target": {"kind": "fixture", "id": "fixture.window"},
                        "color": "#FF6600",
                        "brightness": 0.8,
                        "fade_in_ms": 25,
                        "fade_out_ms": 25,
                        "period_ms": 150,
                        "width_pixels": 2,
                        "density": 0.25,
                        "seed": 42,
                        "reverse": False,
                        "blend_mode": "add",
                        "precedence": 10,
                    },
                },
                {
                    "id": "audio-lead",
                    "name": "Audio lead",
                    "kind": "audio",
                    "track_id": "fixture-track",
                    "time_ms": None,
                    "marker_id": "impact-marker",
                    "marker_offset_ms": -50,
                },
            ]
        },
    }
    created = client.post(
        "/api/gui/v1/projects",
        json={"name": "Compiled composition", "draft": _draft(editor_metadata=metadata)},
        headers=_mutation_headers(client),
    )
    project_id = created.json()["project"]["id"]

    compiled = client.post(
        f"/api/gui/v1/projects/{project_id}/compile",
        headers=_mutation_headers(client, if_match='"1"'),
    )

    assert compiled.status_code == 200
    package_root = next((tmp_path / "compiled" / project_id).glob("r1-*"))
    runtime_graph = json.loads(
        (package_root / "runtime" / "composition-graph.json").read_text(encoding="utf-8")
    )
    runtime_scene = runtime_graph["scenes"][0]
    assert runtime_scene["contexts"][1] == {
        "scene_id": "ambient",
        "context_id": "scene:ambient>impact",
        "editing_space_id": "impact",
        "offset_ms": 500,
        "time_scale": 1.0,
        "placement": {"x": 0.0, "y": 0.0},
        "parameter_overrides": {},
    }
    assert runtime_scene["outputs"] == [
        {
            "id": "scene:ambient>impact|fixture-track",
            "context_id": "scene:ambient>impact",
            "track_id": "fixture-track",
            "track_kind": "fixture",
            "resource_binding": "fixture.window",
        }
    ]
    assert [cue["source_cue_id"] for cue in runtime_scene["cues"]] == [
        "audio-lead",
        "impact-cue",
    ]
    assert runtime_scene["cues"][0]["timestamp_ms"] == 550
    assert runtime_scene["cues"][1] == {
        "id": "scene:ambient>impact|impact-cue",
        "source_cue_id": "impact-cue",
        "name": "Window flash",
        "kind": "lighting",
        "output_id": "scene:ambient>impact|fixture-track",
        "timestamp_ms": 650,
        "local_time_ms": 150,
        "marker_id": "impact-marker",
        "marker_name": "Impact",
        "marker_offset_ms": 50,
        "editing_space_id": "impact",
        "context_id": "scene:ambient>impact",
        "lighting": {
            "primitive": "pulse",
            "duration_ms": 300,
            "target": {"kind": "fixture", "id": "fixture.window"},
            "color": "#FF6600",
            "brightness": 0.8,
            "fade_in_ms": 25,
            "fade_out_ms": 25,
            "period_ms": 150,
            "width_pixels": 2,
            "density": 0.25,
            "seed": 42,
            "reverse": False,
            "blend_mode": "add",
            "precedence": 10,
        },
    }
    assert runtime_scene["transform_layers"][0] == {
        "id": "scene:ambient>impact|ghost-layer",
        "source_layer_id": "ghost-layer",
        "name": "Ghost",
        "kind": "image",
        "source_id": "ghost-asset",
        "layer_schema_version": 1,
        "output_id": "scene:ambient>impact|fixture-track",
        "editing_space_id": "impact",
        "context_id": "scene:ambient>impact",
        "coordinate_space": {
            "kind": "stage",
            "target_id": "shared-stage",
            "width": 10,
            "height": 4,
        },
        "calibration_policy": {
            "static_installation_calibration": "runtime",
            "projector_handoff_keyframes": False,
        },
        "keyframed_properties": [
            "position",
            "scale_x",
            "scale_y",
            "rotation",
            "anchor",
            "opacity",
            "crop",
        ],
        "property_animation_policy": {
            "transform": "runtime",
            "lighting": "static_cue",
            "unsupported": "reject",
        },
        "source_in_ms": 100,
        "source_out_ms": 600,
        "source_conformance": {
            "alpha_interpretation": "straight",
            "color_space": "srgb",
            "scaling_behavior": "fit",
        },
        "render_conformance": {
            "render_pipeline_revision": 1,
            "resolution": {"width": 1920, "height": 1080},
            "frame_rate": {"numerator": 30000, "denominator": 1001},
            "working_color_space": "linear_srgb",
            "working_alpha_mode": "premultiplied",
            "scaling_filter": "lanczos",
            "output_background_color": "#000000",
            "media_end_behavior": "hold_last_frame",
            "output_profile": {
                "preset_id": "low_power_h264",
                "container": "mp4",
                "video_codec": "h264",
                "video_profile": "main",
                "video_level": "4.0",
                "pixel_format": "yuv420p",
                "max_video_bitrate_kbps": 8000,
                "audio_codec": "aac",
                "audio_sample_rate": 48000,
                "audio_channel_layout": "stereo",
                "loudness_target_lufs": -16,
                "keyframe_interval_frames": 60,
                "b_frames": 0,
                "hardware_decoder_compatibility": "h264_main_level_4_yuv420p",
            },
        },
        "z_index": 2,
        "fade_in_ms": 50,
        "fade_out_ms": 75,
        "audio": None,
        "start_ms": 700,
        "duration_ms": 500,
        "keyframes": [
            {
                "id": "ghost-start",
                "time_ms": 0,
                "timestamp_ms": 700,
                "position": [0, 0],
                "scale": [1, 1],
                "rotation_deg": 0,
                "anchor": [0.5, 0.5],
                "opacity": 0.5,
                "crop": [0, 0, 0, 0],
                "interpolation": {
                    "kind": "bezier",
                    "temporal_bezier": [0.25, 0.1, 0.25, 1],
                    "spatial_in": [-1, 0],
                    "spatial_out": [1, 0],
                },
            },
            {
                "id": "ghost-end",
                "time_ms": 500,
                "timestamp_ms": 1200,
                "position": [4, 2],
                "scale": [1.5, 0.75],
                "rotation_deg": 15,
                "anchor": [0.5, 0.5],
                "opacity": 1,
                "crop": [0.1, 0, 0.1, 0],
                "interpolation": {
                    "kind": "ease_out",
                    "temporal_bezier": None,
                    "spatial_in": None,
                    "spatial_out": None,
                },
            },
        ],
    }

    unsupported_metadata = {
        "compositions": {"impact": composition},
        "composition_instances": {
            "ambient": [
                {
                    "id": "impact-instance",
                    "composition_id": "impact",
                    "start_offset_ms": 0,
                    "time_scale": 1,
                    "parameter_overrides": {"variant": "ghost"},
                    "placement": {"x": 0, "y": 0},
                }
            ]
        },
    }
    unsupported = client.post(
        "/api/gui/v1/projects",
        json={
            "name": "Unsupported composition",
            "draft": _draft(editor_metadata=unsupported_metadata),
        },
        headers=_mutation_headers(client),
    )
    unsupported_project_id = unsupported.json()["project"]["id"]
    rejected = client.post(
        f"/api/gui/v1/projects/{unsupported_project_id}/compile",
        headers=_mutation_headers(client, if_match='"1"'),
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "compilation_failed"
    assert rejected.json()["detail"]["diagnostics"] == [
        {
            "code": "unsupported_composition_parameter_override",
            "message": (
                "composition instance impact-instance has parameter overrides "
                "that cannot yet be represented by the runtime package"
            ),
            "object_ids": ["impact-instance", "impact"],
        }
    ]


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
