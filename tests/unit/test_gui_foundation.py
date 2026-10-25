import asyncio
import base64
import copy
import hashlib
import json
import shutil
import socket
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

import cueverge.gui.scene_preview as scene_preview
from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.core.models import SceneDefinitionV2
from cueverge.gui.asset_upload import StoredAsset
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import DraftProject, Role, SceneLightingPreviewRequest
from cueverge.gui.endpoint_discovery import (
    DiscoveredController,
    EndpointDiscoveryService,
)
from cueverge.gui.mapper_export import MapperExportError
from cueverge.gui.mcp_authoring import McpAuthoringError, McpScope
from cueverge.gui.passwords import hash_password
from cueverge.gui.publication import PublicationDiagnostic, PublicationError
from cueverge.gui.repository import StoredDevice
from cueverge.gui.scene_authoring import delete_scene
from cueverge.gui.scene_preview import preview_project_scene
from cueverge.gui.service import GuiServices
from cueverge.lighting.ddp import DdpUdpSink
from cueverge.lighting.evaluation import evaluate_lighting_output
from cueverge.lighting.sink import FrameSinkRouter
from cueverge.lighting.timeline import LightingTimeline
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
        event.action == "browser.error" and event.actor == "admin" and event.target == "timeline"
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


def test_mcp_endpoint_requires_project_token_and_enforces_tool_scopes(
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
        token_body = {
            "client_id": "protocol-client",
            "scopes": ["read"],
            "expires_at": expiry,
        }
        without_csrf = client.post(
            f"/api/gui/v1/projects/{project_id}/mcp-tokens",
            json=token_body,
            headers={"Origin": ORIGIN},
        )
        assert without_csrf.status_code == 403
        assert without_csrf.json()["detail"]["code"] == "csrf_failed"

        created_token = client.post(
            f"/api/gui/v1/projects/{project_id}/mcp-tokens",
            json=token_body,
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
        assert {
            "get_spatial_layout",
            "get_spatial_effect_catalog",
            "preview_composition",
            "create_render_job",
            "get_render_jobs",
            "get_render_job",
            "cancel_render_job",
        }.issubset(names)
        assert {
            "get_project_draft",
            "update_project_draft",
            "update_revision_release",
            "publish_revision",
            "stage_published_package",
            "activate_published_package",
        }.issubset(names)
        assert not names.intersection(
            {"enroll_device", "approve_proposal", "start_scene", "stop_scene"}
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
        event for event in services.repository.list_audit_events() if event.target == proposal.id
    ]
    assert {"mcp.proposal.validate", "mcp.proposal.approve", "mcp.proposal.merge"}.issubset(
        {event.action for event in proposal_events}
    )


@pytest.mark.parametrize(
    ("global_role", "project_role", "live_output", "allowed"),
    [
        (Role.VIEWER, Role.VIEWER, False, set()),
        (Role.VIEWER, Role.OPERATOR, True, set()),
        (Role.VIEWER, Role.DESIGNER, False, {McpScope.DIRECT_EDIT}),
        (
            Role.VIEWER,
            Role.PUBLISHER,
            False,
            {McpScope.DIRECT_EDIT, McpScope.PUBLISH},
        ),
        (
            Role.VIEWER,
            Role.PUBLISHER,
            True,
            {McpScope.DIRECT_EDIT, McpScope.PUBLISH, McpScope.ACTIVATE},
        ),
        (
            Role.VIEWER,
            Role.ADMINISTRATOR,
            False,
            {McpScope.DIRECT_EDIT, McpScope.PUBLISH},
        ),
        (
            Role.ADMINISTRATOR,
            None,
            False,
            {McpScope.DIRECT_EDIT, McpScope.PUBLISH, McpScope.ACTIVATE},
        ),
    ],
)
def test_mcp_privileged_scope_issuance_matches_current_project_permissions(
    tmp_path: Path,
    global_role: Role,
    project_role: Role | None,
    live_output: bool,
    allowed: set[McpScope],
) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    services.collaboration.upsert_user(
        "member", PASSWORD_HASH, global_role, False, datetime.now(UTC).isoformat()
    )
    if project_role is not None:
        services.collaboration.put_membership(
            project_id, "member", project_role, live_output, datetime.now(UTC).isoformat()
        )
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        login = client.post(
            "/api/gui/v1/auth/login",
            json={"username": "member", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200
        for scope in (McpScope.DIRECT_EDIT, McpScope.PUBLISH, McpScope.ACTIVATE):
            created = client.post(
                f"/api/gui/v1/projects/{project_id}/mcp-tokens",
                json={
                    "client_id": "permission-fixture",
                    "scopes": ["read", scope.value],
                    "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                },
                headers=_mutation_headers(client),
            )
            if scope in allowed:
                assert created.status_code == 201
                assert set(created.json()["scopes"]) == {"read", scope.value}
                authorized = _mcp_tool(client, created.json()["token"], "get_project_structure")
                assert authorized["isError"] is False
            else:
                assert created.status_code == 403
                assert created.json()["detail"]["code"] == "mcp_scope_not_permitted"


@pytest.mark.parametrize("via_mcp", [False, True])
def test_scene_deletion_is_revisioned_authorized_and_preserves_shared_content(
    tmp_path: Path,
    via_mcp: bool,
) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    keys = (
        "scene_organization",
        "scene_preset_usage",
        "scene_compositions",
        "composition_instances",
        "timeline_views",
        "timeline_tracks",
        "timeline_markers",
        "timeline_cues",
        "timeline_transform_layers",
        "timeline_property_keyframes",
        "lighting_author_layers",
    )
    metadata = {key: {"ambient": [], "unwanted": []} for key in keys}
    metadata.update(
        {
            "installation_id": "keep-installation",
            "asset_bindings": {"keep-asset": 1},
            "scene_presets": {"keep-preset": {"name": "Reusable"}},
            "custom_note": "Keep unrelated metadata",
            "compositions": {
                "shared": {
                    "id": "shared",
                    "name": "Reusable composition",
                    "duration_ms": 4000,
                    "space_kind": "stage",
                    "target_id": "stage",
                    "width": 1000,
                    "height": 600,
                }
            },
            "timeline_sync_schedule": [{"scene_id": "unwanted"}, {"scene_id": "ambient"}],
        }
    )
    document = _draft(editor_metadata=metadata)
    document["scenes"].append({**copy.deepcopy(document["scenes"][0]), "id": "unwanted"})
    document["editor_metadata"]["scene_compositions"]["unwanted"] = ["shared"]
    original = DraftProject.model_validate(document)
    project = services.repository.create_project(
        name="Delete scene fixture",
        draft=original,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    project_id = project.project.id
    direct = _mcp_token(services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT))
    read = _mcp_token(services, project_id, (McpScope.READ, McpScope.DRAFT_EDIT))
    path = f"/api/gui/v1/projects/{project_id}/scenes/unwanted"
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        assert client.delete(path).status_code == 401
        _login(client)
        assert client.delete(path).status_code == 403
        assert client.delete(path, headers=_mutation_headers(client)).status_code == 428
        assert (
            _mcp_tool(client, read, "delete_scene", scene_id="unwanted", base_revision=1)["isError"]
            is True
        )
        schema = _mcp_tool(client, read, "get_authoring_schema")["structuredContent"]
        assert schema["x-scene-deletion"]["scope"] == "direct_edit"
        capabilities = _mcp_tool(client, read, "get_capabilities")["structuredContent"]
        assert capabilities["features"]["scene_deletion"] is True
        assert capabilities["tools"]["delete_scene"] == "delete_scene"
        missing = client.delete(
            path.replace("unwanted", "missing"),
            headers=_mutation_headers(client, if_match='"1"'),
        )
        assert missing.status_code == 422
        if via_mcp:
            result = _mcp_tool(client, direct, "delete_scene", scene_id="unwanted", base_revision=1)
            assert result["isError"] is False
            assert result["structuredContent"]["revision"] == 2
            assert result["structuredContent"]["hardware_commands_sent"] is False
        else:
            response = client.delete(path, headers=_mutation_headers(client, if_match='"1"'))
            assert response.status_code == 200, response.text
            assert response.headers["etag"] == '"2"'
        stored = services.repository.get_revision(project_id)
        assert [scene.id for scene in stored.draft.scenes] == ["ambient"]
        expected_metadata = copy.deepcopy(original.editor_metadata)
        for key in keys:
            expected_metadata[key].pop("unwanted")
        expected_metadata["timeline_sync_schedule"] = [{"scene_id": "ambient"}]
        assert stored.draft.editor_metadata == expected_metadata
        assert stored.draft.scenes == original.scenes[:1]
        assert services.repository.get_revision(project_id, 1).draft == original
        assert (
            client.delete(path, headers=_mutation_headers(client, if_match='"1"')).status_code
            == 409
        )
        assert (
            _mcp_tool(client, direct, "delete_scene", scene_id="unwanted", base_revision=1)[
                "isError"
            ]
            is True
        )
        last = client.delete(
            path.replace("unwanted", "ambient"),
            headers=_mutation_headers(client, if_match='"2"'),
        )
        assert last.status_code == 422
        assert "last scene" in last.json()["detail"]["message"]
        assert (
            _mcp_tool(client, direct, "delete_scene", scene_id="ambient", base_revision=2)[
                "isError"
            ]
            is True
        )
        assert services.repository.get_revision(project_id).revision == 2
        assert services.publication.list(project_id) == ()
        assert runtime.active_scene is None
        services.repository.archive_project(
            project_id=project_id,
            expected_revision=stored.project.metadata_revision,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=datetime.now(UTC).isoformat(),
        )
        archived = client.delete(
            path.replace("unwanted", "ambient"),
            headers=_mutation_headers(client, if_match='"2"'),
        )
        assert archived.status_code == 422
        assert "Archived" in archived.json()["detail"]["message"]
        assert (
            _mcp_tool(client, direct, "delete_scene", scene_id="ambient", base_revision=2)[
                "isError"
            ]
            is True
        )
        assert services.repository.get_revision(project_id).revision == 2


@pytest.mark.parametrize(
    "reference",
    [
        "fallback",
        "completion",
        "program-start",
        "program-pool",
        "visitor-response",
        "encounter-ambient",
        "encounter-transition",
    ],
)
def test_scene_deletion_blocks_incoming_references_through_http_and_mcp(
    tmp_path: Path,
    reference: str,
) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    document = _draft()
    document["scenes"].append({**copy.deepcopy(document["scenes"][0]), "id": "unwanted"})
    if reference == "fallback":
        document["scenes"][0]["failure_scene_id"] = "unwanted"
    elif reference == "completion":
        document["scenes"][0]["completion"] = {
            "policy": "transition_to",
            "target_scene_id": "unwanted",
        }
    elif reference.startswith("program"):
        document["program"] = {
            "schema_version": 1,
            "initial_scene_id": "unwanted" if reference == "program-start" else "ambient",
            "default_pool_id": "default",
            "pools": [
                {
                    "id": "default",
                    "category": "ambient",
                    "scenes": [
                        {
                            "scene_id": "unwanted" if reference == "program-pool" else "ambient",
                        }
                    ],
                }
            ],
        }
    elif reference == "visitor-response":
        document["opportunities"] = {
            "schema_version": 1,
            "opportunities": [
                {
                    "id": "welcome",
                    "scene_id": "unwanted",
                    "trigger_event": "audience.arrived",
                    "ttl_ms": 10000,
                }
            ],
        }
    else:
        document["encounter"] = {
            "schema_version": 1,
            "initial_state": "ambient",
            "cooldown_state": "cooldown",
            "ambient_scene_id": "unwanted" if reference == "encounter-ambient" else "ambient",
            "encounter_timeout_ms": 30000,
            "cooldown_ms": 5000,
            "debounce_ms": 500,
            "event_ttl_ms": 3000,
            "transitions": [
                {
                    "event_type": "audience.arrived",
                    "from_states": ["ambient"],
                    "to_state": "engaged",
                    "scene_id": "unwanted",
                }
            ],
        }
    project = services.repository.create_project(
        name="Referenced scene",
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    project_id = project.project.id
    token = _mcp_token(services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        _login(client)
        rejected = client.delete(
            f"/api/gui/v1/projects/{project_id}/scenes/unwanted",
            headers=_mutation_headers(client, if_match='"1"'),
        )
        assert rejected.status_code == 422
        message = rejected.json()["detail"]["message"]
        assert "Update these references first" in message
        expected_guidance = {
            "fallback": "failure fallback of scene",
            "completion": "which plays it next",
            "program-start": "choose another under Starts with",
            "program-pool": "adding another scene first if it is the only one",
            "visitor-response": "(Interactions)",
            "encounter-ambient": "encounter ambient scene",
            "encounter-transition": "encounter transition",
        }
        assert expected_guidance[reference] in message
        assert (
            _mcp_tool(client, token, "delete_scene", scene_id="unwanted", base_revision=1)[
                "isError"
            ]
            is True
        )
        assert services.repository.get_revision(project_id).revision == 1
        assert services.publication.list(project_id) == ()
        assert runtime.active_scene is None


def test_scene_deletion_requires_project_author_permission(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime, role=Role.VIEWER)
    document = _draft()
    document["scenes"].append({**copy.deepcopy(document["scenes"][0]), "id": "unwanted"})
    project = services.repository.create_project(
        name="Viewer cannot delete",
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    project_id = project.project.id
    services.collaboration.put_membership(
        project_id,
        "admin",
        Role.VIEWER,
        False,
        datetime.now(UTC).isoformat(),
    )
    with TestClient(create_app(runtime, gui_services=services)) as client:
        _login(client)
        response = client.delete(
            f"/api/gui/v1/projects/{project_id}/scenes/unwanted",
            headers=_mutation_headers(client, if_match='"1"'),
        )
        assert response.status_code == 403
        assert services.repository.get_revision(project_id).revision == 1


def test_scene_deletion_preserves_composition_editing_space_with_same_id() -> None:
    document = _draft(
        editor_metadata={
            "compositions": {
                "unwanted": {
                    "id": "unwanted",
                    "name": "Shared space",
                    "duration_ms": 4000,
                    "space_kind": "stage",
                    "target_id": "stage",
                    "width": 1000,
                    "height": 600,
                }
            },
            "timeline_markers": {"unwanted": []},
            "scene_compositions": {"unwanted": ["unwanted"]},
        }
    )
    document["scenes"].append({**copy.deepcopy(document["scenes"][0]), "id": "unwanted"})
    result = delete_scene(DraftProject.model_validate(document), "unwanted")
    assert result.editor_metadata["timeline_markers"] == {"unwanted": []}
    assert result.editor_metadata["scene_compositions"] == {}
    assert "unwanted" in result.editor_metadata["compositions"]


def test_mcp_scene_media_inspector_fields_share_revision_and_validation_boundaries(
    tmp_path: Path,
) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    token = _mcp_token(services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT))
    read_only = _mcp_token(services, project_id, (McpScope.READ,))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        capabilities = _mcp_tool(client, token, "get_capabilities")["structuredContent"]
        representation = capabilities["authoring_representations"]["runtime_scene_media"]
        assert representation["write_requires_scope"] == "direct_edit"
        assert representation["editor_metadata_required"] is False
        assert "Inspector" in representation["ui_workflow"]
        assert (
            capabilities["authoring_representations"]["editor_timeline"][
                "includes_runtime_scene_media"
            ]
            is False
        )
        schema = _mcp_tool(client, token, "get_authoring_schema")["structuredContent"]
        properties = schema["$defs"]["MediaOutputDefinition"]["properties"]
        assert {"asset", "required", "start_delay_ms", "end_behavior"} <= properties.keys()
        assert properties["start_delay_ms"]["maximum"] == 5000

        document = _mcp_tool(client, token, "get_project_draft")["structuredContent"]["draft"]
        original = copy.deepcopy(document)
        scene = document["scenes"][0]
        scene.update(
            schema_version=2,
            mode="media_synchronized",
            clock_source="main",
            media=[
                {
                    **scene["media"],
                    "id": "main",
                    "required": True,
                    "start_delay_ms": 500,
                    "end_behavior": "loop",
                }
            ],
            lighting=[],
        )
        denied = _mcp_tool(
            client, read_only, "update_project_draft", base_revision=1, draft=document
        )
        assert denied["isError"] is True
        updated = _mcp_tool(client, token, "update_project_draft", base_revision=1, draft=document)
        assert updated["isError"] is False
        saved = _mcp_tool(client, token, "get_project_draft")["structuredContent"]
        assert saved["revision"] == 2
        assert saved["draft"]["scenes"][0]["media"] == scene["media"]
        assert saved["draft"]["editor_metadata"] == original["editor_metadata"]
        for changes in ({"start_delay_ms": 5001}, {"required": False}, {"end_behavior": "bad"}):
            invalid = copy.deepcopy(saved["draft"])
            invalid["scenes"][0]["media"][0].update(changes)
            rejected = _mcp_tool(
                client, token, "update_project_draft", base_revision=2, draft=invalid
            )
            assert rejected["isError"] is True
            assert services.repository.get_revision(project_id).revision == 2
        for scene_duration in (0, -1):
            invalid = copy.deepcopy(saved["draft"])
            invalid["scenes"][0]["duration_ms"] = scene_duration
            rejected = _mcp_tool(
                client, token, "update_project_draft", base_revision=2, draft=invalid
            )
            assert rejected["isError"] is True
        scene_properties = schema["$defs"]["SceneDefinitionV2"]["properties"]
        assert scene_properties["duration_ms"]["exclusiveMinimum"] == 0
        longer = copy.deepcopy(saved["draft"])
        longer["scenes"][0]["duration_ms"] = scene.get("duration_ms", 1000) + 30000
        extended = _mcp_tool(client, token, "update_project_draft", base_revision=2, draft=longer)
        assert extended["isError"] is False
        assert (
            services.repository.get_revision(project_id).draft.scenes[0].duration_ms
            == (longer["scenes"][0]["duration_ms"])
        )
        stale = _mcp_tool(client, token, "update_project_draft", base_revision=1, draft=document)
        assert stale["isError"] is True
        assert runtime.active_scene is None


def test_mcp_direct_edit_is_immediate_revisioned_and_schema_validated(tmp_path: Path) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    direct_token = _mcp_token(services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT))
    proposal_token = _mcp_token(services, project_id, (McpScope.READ, McpScope.DRAFT_EDIT))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        read = _mcp_tool(client, proposal_token, "get_project_draft")
        assert read["isError"] is False
        draft = read["structuredContent"]["draft"]
        assert read["structuredContent"]["revision"] == 1
        draft["scenes"][0]["name"] = "Changed through chat"
        draft["editor_metadata"]["chat_note"] = "No proposal required"

        denied = _mcp_tool(
            client, proposal_token, "update_project_draft", base_revision=1, draft=draft
        )
        assert denied["isError"] is True
        assert "direct_edit" in denied["content"][0]["text"]
        assert services.repository.get_revision(project_id).revision == 1

        invalid = _mcp_tool(
            client,
            direct_token,
            "update_project_draft",
            base_revision=1,
            draft={**draft, "schema_version": 999},
        )
        assert invalid["isError"] is True
        assert services.repository.get_revision(project_id).revision == 1

        updated = _mcp_tool(
            client, direct_token, "update_project_draft", base_revision=1, draft=draft
        )
        assert updated["isError"] is False
        result = updated["structuredContent"]
        assert result["revision"] == 2
        assert result["base_revision"] == 1
        assert result["requires_human_approval"] is False
        assert "/scenes/0/name" in result["changed_paths"]
        assert services.repository.get_revision(project_id).draft.scenes[0].name == (
            "Changed through chat"
        )
        assert services.repository.get_revision(project_id, 1).draft.scenes[0].name == "Ambient"
        assert services.mcp_store.list_proposals(project_id) == ()
        assert services.publication.list(project_id) == ()

        unchanged = _mcp_tool(
            client, direct_token, "update_project_draft", base_revision=2, draft=draft
        )
        assert unchanged["isError"] is True
        stale = _mcp_tool(
            client, direct_token, "update_project_draft", base_revision=1, draft=draft
        )
        assert stale["isError"] is True
        assert "base revision" in stale["content"][0]["text"]
        assert services.repository.get_revision(project_id).revision == 2
        audit = [
            item
            for item in services.repository.list_audit_events()
            if item.action == "mcp.draft.update"
        ]
        assert len(audit) == 1
        assert audit[0].actor == "admin"
        assert audit[0].authorization_context["mcp_client_id"] == "chat-fixture"
        assert audit[0].authorization_context["created_revision"] == 2


@pytest.mark.parametrize(
    "scopes",
    [
        (McpScope.READ,),
        (McpScope.READ, McpScope.DRAFT_EDIT),
        (McpScope.READ, McpScope.SIMULATION, McpScope.PUBLISH_PREPARE, McpScope.LIVE_OPERATION),
    ],
)
def test_mcp_existing_scopes_do_not_gain_direct_edit_or_deployment(
    tmp_path: Path, scopes: tuple[McpScope, ...]
) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    token = _mcp_token(services, project_id, scopes)
    calls = [
        ("update_project_draft", {"base_revision": 1, "draft": _draft()}),
        ("update_revision_release", {"revision": 1, "approval_state": "approved"}),
        ("publish_revision", {"revision": 1}),
        ("stage_published_package", {"package_id": "not-a-package"}),
        ("activate_published_package", {"package_id": "not-a-package"}),
    ]
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        for name, arguments in calls:
            denied = _mcp_tool(client, token, name, **arguments)
            assert denied["isError"] is True
            assert "MCP scope required" in denied["content"][0]["text"]
    assert services.repository.get_revision(project_id).revision == 1
    assert services.publication.release(project_id, 1).approval_state == "draft"
    assert services.publication.list(project_id) == ()


def test_mcp_publication_and_activation_preserve_validation_and_project_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    publisher = _mcp_token(services, project_id, (McpScope.READ, McpScope.PUBLISH))
    activator = _mcp_token(services, project_id, (McpScope.READ, McpScope.ACTIVATE))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        unapproved = _mcp_tool(client, publisher, "publish_revision", revision=1)
        assert unapproved["isError"] is True
        assert "approved" in unapproved["content"][0]["text"]
        approved = _mcp_tool(
            client,
            publisher,
            "update_revision_release",
            revision=1,
            approval_state="approved",
            name="Chat release",
            summary="Explicit publisher approval",
        )
        assert approved["isError"] is False
        assert approved["structuredContent"]["approved_by"] == "admin"
        blocked = _mcp_tool(client, publisher, "publish_revision", revision=1)
        assert blocked["isError"] is True
        assert "publication validation failed" in blocked["content"][0]["text"]
        assert services.publication.list(project_id) == ()

        monkeypatch.setattr(
            services.publication,
            "validate",
            lambda _project_id, _revision: (
                PublicationDiagnostic("validation_complete", "info", "Fixture validation passed."),
            ),
        )
        published = _mcp_tool(client, publisher, "publish_revision", revision=1)
        assert published["isError"] is False
        package_id = published["structuredContent"]["id"]
        assert published["structuredContent"]["state"] == "published"
        assert services.publication.get(package_id).manifest["revision_name"] == "Chat release"
        not_staged = _mcp_tool(
            client, activator, "activate_published_package", package_id=package_id
        )
        assert not_staged["isError"] is True
        assert "staged" in not_staged["content"][0]["text"]
        staged = _mcp_tool(client, publisher, "stage_published_package", package_id=package_id)
        assert staged["isError"] is False
        assert staged["structuredContent"]["state"] == "staged"

        denied = _mcp_tool(client, publisher, "activate_published_package", package_id=package_id)
        assert denied["isError"] is True
        denied_publish = _mcp_tool(client, activator, "publish_revision", revision=1)
        assert denied_publish["isError"] is True
        simulated = _mcp_tool(
            client,
            activator,
            "activate_published_package",
            package_id=package_id,
            simulation_mode=True,
        )
        assert simulated["isError"] is True
        assert "simulation mode" in simulated["content"][0]["text"]
        assert services.publication.get(package_id).state == "staged"
        activated = _mcp_tool(
            client, activator, "activate_published_package", package_id=package_id
        )
        assert activated["isError"] is False
        assert activated["structuredContent"]["state"] == "active"

        foreign = services.repository.create_project(
            name="Other project",
            draft=DraftProject.model_validate(_draft()),
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=datetime.now(UTC).isoformat(),
        )
        services.publication.update_release(
            project_id=foreign.project.id,
            revision=1,
            name=None,
            summary=None,
            approval_state="approved",
            actor="admin",
            occurred_at=datetime.now(UTC).isoformat(),
        )
        foreign_package = services.publication.publish(
            project_id=foreign.project.id,
            revision=1,
            actor="admin",
            occurred_at=datetime.now(UTC).isoformat(),
        )
        for token, name in (
            (publisher, "stage_published_package"),
            (activator, "activate_published_package"),
        ):
            outside = _mcp_tool(client, token, name, package_id=foreign_package.id)
            assert outside["isError"] is True
            assert "outside the token project" in outside["content"][0]["text"]
        assert services.publication.get(foreign_package.id).state == "published"
        assert {
            "mcp.revision.release.update",
            "mcp.package.publish",
            "mcp.package.stage",
            "mcp.package.activate",
        }.issubset({item.action for item in services.repository.list_audit_events()})


@pytest.mark.parametrize("change", ["disabled", "project_role", "live_output", "revoked"])
def test_mcp_new_privileges_are_rechecked_for_every_request(tmp_path: Path, change: str) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    now = datetime.now(UTC).isoformat()
    services.collaboration.upsert_user("member", PASSWORD_HASH, Role.VIEWER, False, now)
    services.collaboration.put_membership(project_id, "member", Role.PUBLISHER, True, now)
    token = _mcp_token(
        services,
        project_id,
        (McpScope.READ, McpScope.DIRECT_EDIT, McpScope.PUBLISH, McpScope.ACTIVATE),
        username="member",
    )
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        assert _mcp_tool(client, token, "get_project_draft")["isError"] is False
        if change == "disabled":
            services.collaboration.upsert_user("member", PASSWORD_HASH, Role.VIEWER, True, now)
        elif change == "project_role":
            services.collaboration.put_membership(project_id, "member", Role.DESIGNER, True, now)
        elif change == "live_output":
            services.collaboration.put_membership(project_id, "member", Role.PUBLISHER, False, now)
        else:
            record = services.mcp_store.verify_token(token, now)
            assert record is not None
            services.mcp_store.revoke_token(record.id, now)
        denied = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_project_draft", "arguments": {}},
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert denied.status_code == 401
        assert services.repository.get_revision(project_id).revision == 1


def test_data_root_backup_restores_projects_and_audit_history(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    runtime = ControllerRuntime()
    source_client = TestClient(create_app(runtime, gui_services=_services(source_root, runtime)))
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
        {**template, "id": f"scene-{index:03}", "name": f"Scene {index:03}"} for index in range(250)
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
        f"/api/gui/v1/projects/{project_id}/simulation-scenarios/{scenario.json()['id']}/run",
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
    assert [item["id"] for item in reviewer.get("/api/gui/v1/projects").json()] == [project_id]
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
    assert (
        reviewer.get(f"/api/gui/v1/projects/{project_id}/presence").json()[0]["location"]
        == "timeline:ambient"
    )

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
            "stage_placement": "manual",
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
            "stage_placement": "manual",
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
            "stage_placement": "manual",
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
            "stage_placement": "manual",
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
            "stage_placement": "manual",
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

    staged = asyncio.run(services.publication.stage(package.id, "2026-10-16T12:04:00Z"))
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
        ({"trashed_at": "2026-10-16T13:00:00Z"}, "asset_trashed"),
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
        lambda _project_id, **_: (replace(asset, **changes),),
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
    monkeypatch.setattr(services.asset_upload, "list_assets", lambda _project_id, **_: ())

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


def _mcp_fixture(tmp_path: Path) -> tuple[ControllerRuntime, GuiServices, str]:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    project = services.repository.create_project(
        name="MCP direct authoring fixture",
        draft=DraftProject.model_validate(_draft()),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    return runtime, services, project.project.id


@pytest.mark.parametrize("binding", ["runtime", "unrelated", None])
def test_mcp_runtime_snapshot_is_read_only_installation_bound_and_honest(
    tmp_path: Path, binding: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    installation_id = services.repository.get_runtime_installation().id
    metadata = {"installation_id": installation_id if binding == "runtime" else binding}
    project = services.repository.create_project(
        name="Runtime observation fixture",
        draft=DraftProject.model_validate(_draft(editor_metadata=metadata)),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    project_id = project.project.id
    token = _mcp_token(services, project_id, (McpScope.READ,))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        capabilities = _mcp_tool(client, token, "get_capabilities")["structuredContent"]
        assert capabilities["runtime_status"]["available_for_project"] is (binding == "runtime")
        assert capabilities["runtime_status"]["stale_after_ms"] == 10000
        assert capabilities["granted_scopes"] == ["read"]
        result = _mcp_tool(client, token, "get_runtime_status")
        if binding != "runtime":
            assert result["isError"] is True
            assert "not bound" in result["content"][0]["text"]
        else:
            assert result["isError"] is False
            snapshot = result["structuredContent"]
            assert snapshot["installation_id"] == installation_id
            assert snapshot["source"] == "controller_runtime"
            assert datetime.fromisoformat(snapshot["observed_at"]).tzinfo is not None
            assert snapshot["loaded_catalog"] == {
                "state": "unreported",
                "project_id": None,
                "project_name": None,
                "revision": None,
                "digest_sha256": None,
                "loaded_at": None,
                "generation": None,
                "error": None,
            }
            _login(client)
            rest = client.get("/api/gui/v1/runtime/status")
            assert rest.status_code == 200
            assert rest.json()["loaded_catalog"] == snapshot["loaded_catalog"]
            assert rest.json()["status"] == snapshot["status"]

            def oversized_status() -> dict[str, object]:
                return {"detail": "x" * (200 * 1024)}

            monkeypatch.setattr(runtime, "status", oversized_status)
            oversized = _mcp_tool(client, token, "get_runtime_status")
            assert oversized["isError"] is True
            assert "exceeds 200 KiB" in oversized["content"][0]["text"]
        assert services.repository.get_revision(project_id).revision == 1
        assert runtime.active_scene is None
        assert services.publication.list(project_id) == ()


def test_mcp_usability_baseline_preserves_raw_lighting_through_compile_and_edit(
    tmp_path: Path,
) -> None:
    document = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "usability-authoring.json").read_text()
    )
    baseline = DraftProject.model_validate(document)
    runtime, services, project_id = _mcp_fixture(tmp_path)
    token = _mcp_token(
        services,
        project_id,
        (McpScope.READ, McpScope.DIRECT_EDIT, McpScope.DRAFT_EDIT, McpScope.SIMULATION),
    )

    def compile_frames(
        draft: DraftProject, revision: int
    ) -> tuple[tuple[tuple[int, int, int], ...], ...]:
        compiled = services.compiler.compile(project_id=project_id, revision=revision, draft=draft)
        catalog = load_show_package(compiled.package_root)
        assert tuple(scene.definition for scene in catalog.scenes.values()) == draft.scenes
        output = catalog.require("baseline-timeline").lighting_outputs[0]
        timeline = LightingTimeline(
            pixel_count=output.pixel_count,
            cues=output.lighting_cues,
            brightness_limit=output.definition.brightness_limit,
        )
        return tuple(timeline.render(timestamp) for timestamp in (1000, 17500, 62500, 77500, 92500))

    expected_frames = compile_frames(baseline, 1)
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        schema = _mcp_tool(client, token, "get_authoring_schema")["structuredContent"]
        assert {
            cue["primitive"] for cue in document["scenes"][0]["lighting"][0]["timeline"]
        } == set(schema["$defs"]["LightingCue"]["properties"]["primitive"]["enum"])
        capabilities = _mcp_tool(client, token, "get_capabilities")["structuredContent"]
        representations = capabilities["authoring_representations"]
        assert representations["runtime_scene_lighting"]["read_tool"] == "get_project_draft"
        assert representations["runtime_scene_lighting"]["editor_metadata_required"] is False
        assert representations["runtime_scene_lighting"]["write_requires_scope"] == "direct_edit"
        assert representations["editor_timeline"]["includes_runtime_scene_lighting"] is False
        updated = _mcp_tool(client, token, "update_project_draft", base_revision=1, draft=document)
        assert updated["isError"] is False
        read = _mcp_tool(client, token, "get_project_draft")
        assert read["isError"] is False
        saved = DraftProject.model_validate(read["structuredContent"]["draft"])
        assert saved == baseline
        assert compile_frames(saved, 2) == expected_frames
        editor_range = _mcp_tool(
            client,
            token,
            "get_timeline_range",
            scene_id="baseline-timeline",
            start_ms=0,
            end_ms=180000,
        )
        assert editor_range["isError"] is False
        assert editor_range["structuredContent"]["cues"] == []
        timeline_scene = saved.scenes[0]
        assert isinstance(timeline_scene, SceneDefinitionV2)
        assert len(timeline_scene.lighting[0].timeline) == 10

        unchanged = _mcp_tool(
            client, token, "update_project_draft", base_revision=2, draft=document
        )
        assert unchanged["isError"] is True
        assert services.repository.get_revision(project_id).revision == 2
        assert services.repository.get_revision(project_id).draft == baseline

        changed = copy.deepcopy(document)
        changed["scenes"][0]["lighting"][0]["timeline"][0]["brightness"] = 0.4
        proposal = _mcp_tool(
            client,
            token,
            "propose_project_revision",
            base_revision=2,
            title="Original synthetic brightness edit",
            kind="timeline_change",
            proposed_draft=changed,
        )
        assert proposal["isError"] is False
        proposal_id = proposal["structuredContent"]["proposal_id"]
        validated = _mcp_tool(client, token, "validate_proposal", proposal_id=proposal_id)
        assert validated["isError"] is False
        simulated = _mcp_tool(
            client,
            token,
            "simulate_proposal",
            proposal_id=proposal_id,
            steps=[{"kind": "advance", "delta_ms": 1200}],
            expected={"scene_history": ["baseline-timeline"]},
        )
        assert simulated["isError"] is False
        result = simulated["structuredContent"]["simulation"]
        assert result["passed"] is True, result["mismatches"]
        assert result["hardware_adapters_opened"] is False
        assert result["hardware_commands_sent"] is False
        assert services.repository.get_revision(project_id).revision == 2

        edited = _mcp_tool(client, token, "update_project_draft", base_revision=2, draft=changed)
        assert edited["isError"] is False
        read_back = _mcp_tool(client, token, "get_project_draft")
        saved_edit = DraftProject.model_validate(read_back["structuredContent"]["draft"])
        assert saved_edit == DraftProject.model_validate(changed)
        assert saved_edit.editor_metadata == baseline.editor_metadata
        edited_frames = compile_frames(saved_edit, 3)
        assert edited_frames[0] != expected_frames[0]
        assert runtime.active_scene is None
        assert services.publication.list(project_id) == ()


def test_scene_lighting_preview_matches_runtime_for_saved_unsaved_and_paged_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    document = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "usability-authoring.json").read_text()
    )
    project = services.repository.create_project(
        name="Scene preview fixture",
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    project_id = project.project.id
    token = _mcp_token(services, project_id, (McpScope.READ, McpScope.SIMULATION))
    reader = _mcp_token(services, project_id, (McpScope.READ,))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        body = {
            "source_revision": 1,
            "scene_id": "baseline-timeline",
            "timestamp_ms": 17500,
            "resource": "roofline.demo",
        }
        path = f"/api/gui/v1/projects/{project_id}/scene-lighting-preview"
        assert client.post(path, json=body).status_code == 401
        _login(client)
        assert client.post(path, json=body).status_code == 403
        response = client.post(path, json=body, headers=_mutation_headers(client))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["hardware_commands_sent"] is False
        assert result["unsaved"] is False
        assert result["outputs"][0]["output_position_ms"] == 17540
        compiled = services.compiler.compile(project_id=project_id, revision=1, draft=project.draft)
        loaded = load_show_package(compiled.package_root).require("baseline-timeline")
        output = loaded.lighting_outputs[0]
        expected = evaluate_lighting_output(output, position_ms=17540, duration_ms=180000)
        assert result["outputs"][0]["colors"] == [list(color) for color in expected]
        mcp = _mcp_tool(client, token, "preview_scene_lighting", **body)
        assert mcp["isError"] is False
        assert mcp["structuredContent"] == result
        for timestamp_ms in (179999, 249):
            loop_body = {**body, "timestamp_ms": timestamp_ms}
            http_frame = client.post(path, json=loop_body, headers=_mutation_headers(client))
            assert http_frame.status_code == 200, http_frame.text
            mcp_frame = _mcp_tool(client, token, "preview_scene_lighting", **loop_body)
            assert mcp_frame["isError"] is False
            assert mcp_frame["structuredContent"] == http_frame.json()
            assert http_frame.json()["timestamp_ms"] == timestamp_ms
            assert http_frame.json()["hardware_commands_sent"] is False
        legacy = client.post(
            path,
            json={**body, "scene_id": "legacy-sweep", "timestamp_ms": 1000},
            headers=_mutation_headers(client),
        )
        assert legacy.status_code == 200
        assert legacy.json()["outputs"][0]["limitations"]
        assert max(color[1] for color in legacy.json()["outputs"][0]["colors"]) > 102
        assert _mcp_tool(client, reader, "preview_scene_lighting", **body)["isError"] is True
        page = _mcp_tool(
            client, token, "preview_scene_lighting", **body, pixel_offset=4, pixel_limit=3
        )["structuredContent"]
        assert page["outputs"][0]["colors"] == result["outputs"][0]["colors"][4:7]
        assert page["outputs"][0]["next_pixel_offset"] == 7
        changed = copy.deepcopy(document)
        changed["scenes"][0]["lighting"][0]["timeline"][0]["brightness"] = 0.8
        unsaved = client.post(
            path,
            json={**body, "timestamp_ms": 1000, "proposed_draft": changed},
            headers=_mutation_headers(client),
        )
        saved = client.post(
            path,
            json={**body, "timestamp_ms": 1000},
            headers=_mutation_headers(client),
        )
        assert unsaved.status_code == 200, unsaved.text
        assert unsaved.json()["unsaved"] is True
        assert unsaved.json()["outputs"][0]["colors"] != saved.json()["outputs"][0]["colors"]
        changed["editor_metadata"]["installation_id"] = "unrelated"
        assert (
            client.post(
                path, json={**body, "proposed_draft": changed}, headers=_mutation_headers(client)
            ).status_code
            == 422
        )
        for invalid in (
            {"timestamp_ms": 180000},
            {"timestamp_ms": 249.375},
            {"pixel_limit": 4097},
            {"pixel_offset": 12},
            {"resource": "not-in-scene"},
        ):
            rejected = client.post(
                path, json={**body, **invalid}, headers=_mutation_headers(client)
            )
            assert rejected.status_code == 422
            if "timestamp_ms" in invalid:
                assert (
                    _mcp_tool(client, token, "preview_scene_lighting", **{**body, **invalid})[
                        "isError"
                    ]
                    is True
                )
        huge = copy.deepcopy(document)
        huge["installation"]["resources"][1]["pixel_count"] = 65537
        huge["installation"]["resources"][1]["segments"][1]["end"] = 65537
        rejected = client.post(
            path, json={**body, "proposed_draft": huge}, headers=_mutation_headers(client)
        )
        assert rejected.status_code == 422
        assert "budget" in rejected.json()["detail"]["message"]
        assert client.post(path, json=body, headers=_mutation_headers(client)).status_code == 200
        monkeypatch.setattr(scene_preview, "_PREVIEW_SLOTS", BoundedSemaphore(0))
        assert client.post(path, json=body, headers=_mutation_headers(client)).status_code == 429
        assert services.repository.get_revision(project_id).revision == 1
        assert services.publication.list(project_id) == ()
        assert runtime.active_scene is None


@pytest.mark.parametrize("with_media", [False, True])
def test_mcp_controller_timed_scenes_are_discoverable_authorable_and_simulatable(
    tmp_path: Path,
    with_media: bool,
) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    token = _mcp_token(
        services, project_id, (McpScope.READ, McpScope.DRAFT_EDIT, McpScope.SIMULATION)
    )
    document = _draft()
    scene = document["scenes"][0]
    scene.update(
        {
            "schema_version": 2,
            "mode": "controller_timed",
            "clock_source": "controller",
            "duration_ms": 2000,
            "completion": {"policy": "loop"},
            "resources": ["roofline.demo", "projector.demo"] if with_media else ["roofline.demo"],
            "media": [{"id": "video", "resource": "projector.demo"}] if with_media else [],
            "lighting": [{"id": "leds", "resource": "roofline.demo", "effect": "synthetic_sweep"}],
        }
    )
    if not with_media:
        document["installation"]["resources"] = [
            resource
            for resource in document["installation"]["resources"]
            if resource["kind"] != "media_agent"
        ]
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        capabilities = _mcp_tool(client, token, "get_capabilities")["structuredContent"]
        assert capabilities["features"]["controller_timed_scenes"] is True
        assert capabilities["features"]["led_only_scenes"] is True
        assert capabilities["scene_timing"]["controller_timed"]["clock_source"] == "controller"
        assert capabilities["scene_timing"]["controller_timed"]["requires_media"] is False
        schema = _mcp_tool(client, token, "get_authoring_schema")["structuredContent"]
        definition = schema["$defs"]["SceneDefinitionV2"]
        assert "controller_timed" in definition["properties"]["mode"]["enum"]
        assert "controller" in definition["properties"]["clock_source"]["description"]
        assert "LED-only" in definition["properties"]["media"]["description"]
        proposal = _mcp_tool(
            client,
            token,
            "propose_project_revision",
            base_revision=1,
            title="Controller-timed pre-show",
            kind="scene_change",
            proposed_draft=document,
        )
        assert proposal["isError"] is False
        proposal_id = proposal["structuredContent"]["proposal_id"]
        validated = _mcp_tool(client, token, "validate_proposal", proposal_id=proposal_id)
        assert validated["isError"] is False
        assert validated["structuredContent"]["state"] == "validated"
        simulated = _mcp_tool(
            client,
            token,
            "simulate_proposal",
            proposal_id=proposal_id,
            steps=[{"kind": "advance", "delta_ms": 2500}],
            expected={"scene_history": ["ambient", "ambient"]},
        )
        assert simulated["isError"] is False
        result = simulated["structuredContent"]["simulation"]
        assert result["passed"] is True, result["mismatches"]
        status = result["actual"]["status"]
        assert status["clock_authority"] == "controller"
        assert status["estimated_position_ms"] == 500
        assert status["playback_state"] == "playing"
        assert status["scene_completions"] == 1
        assert result["hardware_adapters_opened"] is False
        assert result["hardware_commands_sent"] is False
        assert result["actual"]["status"]["connected_agents"] == (
            ["fake-media-1"] if with_media else []
        )
        assert services.repository.get_revision(project_id).revision == 1
        editor = _mcp_token(services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT))
        saved = _mcp_tool(client, editor, "update_project_draft", base_revision=1, draft=document)
        assert saved["isError"] is False
        structure = _mcp_tool(client, token, "get_project_structure")["structuredContent"]
        assert structure["scenes"][0]["mode"] == "controller_timed"
        assert structure["scenes"][0]["clock_source"] == "controller"
        assert runtime.active_scene is None


def test_mcp_spatial_context_proposal_and_image_preview_are_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    installation_id = services.repository.get_runtime_installation().id
    document = _draft(
        editor_metadata={
            "installation_id": installation_id,
            "compositions": {
                "front": {
                    "id": "front",
                    "name": "Front of house",
                    "space_kind": "lighting_surface",
                    "target_id": "facade",
                    "width": 4,
                    "height": 2,
                    "duration_ms": 4000,
                    "children": [],
                    "lighting_settings": {
                        "canvas_width": 8,
                        "canvas_height": 4,
                        "sampling": "nearest",
                        "gamma": 1,
                        "brightness": 1,
                    },
                },
            },
            "scene_compositions": {"ambient": ["front"]},
        }
    )
    services.repository.update_draft(
        project_id=project_id,
        expected_revision=1,
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    unbaked = preview_project_scene(
        services,
        project_id,
        SceneLightingPreviewRequest(source_revision=2, scene_id="ambient", timestamp_ms=0),
    )
    assert unbaked["outputs"]
    assert unbaked["limitations"] == [
        "LED compositions not shown: Front of house. Bake them for this saved revision "
        "(Timeline editor > Bake LED animation) to include them in this preview."
    ]
    mapper: dict[str, Any] = {
        "lighting_surfaces": [
            {
                "semantic_id": "facade",
                "name": "Front of house",
                "resource": "roofline.demo",
                "local_width": 4,
                "local_height": 2,
                "stage_matrix": [1, 0, 0, 1, 0, 0],
                "brightness_limit": 0.5,
                "mapping_revision": 1,
                "calibration_id": "calibrated",
                "pixels": [
                    {
                        "output_index": 7,
                        "local_x": 1,
                        "local_y": 1,
                        "stage_x": 1,
                        "stage_y": 1,
                        "footprint_width": 0.1,
                        "footprint_height": 0.1,
                    },
                    {
                        "output_index": 0,
                        "local_x": 3,
                        "local_y": 1,
                        "stage_x": 3,
                        "stage_y": 1,
                        "footprint_width": 0.1,
                        "footprint_height": 0.1,
                    },
                ],
            }
        ],
        "fixtures": [{"semantic_id": "front-door", "name": "Front door", "stage_points": [[1, 1]]}],
        "paths": [],
    }
    monkeypatch.setattr(services.mapper_export, "export", lambda _: mapper)
    token = _mcp_token(
        services,
        project_id,
        (McpScope.READ, McpScope.DRAFT_EDIT, McpScope.SIMULATION),
    )
    read_only = _mcp_token(services, project_id, (McpScope.READ,))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        context = _mcp_tool(client, token, "get_spatial_layout", pixel_limit=1)
        assert context["isError"] is False
        layout = context["structuredContent"]
        assert layout["surfaces"][0]["name"] == "Front of house"
        assert layout["pixel_page"]["next_offset"] == 1
        catalog = _mcp_tool(client, token, "get_spatial_effect_catalog")
        assert "ripple" in catalog["structuredContent"]["effects"]
        arguments = {
            "base_revision": 2,
            "title": "Front door ripple",
            "expected_layout_digest": layout["layout_digest"],
            "composition_ids": ["front"],
            "effect": {
                "primitive": "ripple",
                "coordinate_space": "stage",
                "origin": [1, 1],
                "speed": 1,
                "ring_width": 1,
                "color": "#4080FF",
            },
            "start_ms": 0,
            "duration_ms": 4000,
        }
        assert (
            _mcp_tool(client, read_only, "propose_spatial_effect", **arguments)["isError"] is True
        )
        result = _mcp_tool(client, token, "propose_spatial_effect", **arguments)
        assert result["isError"] is False
        proposal_id = result["structuredContent"]["proposal_id"]
        assert result["structuredContent"]["requires_human_approval"] is True
        assert services.repository.get_revision(project_id).revision == 2
        assert services.repository.get_revision(project_id).draft == DraftProject.model_validate(
            document
        )
        preview = _mcp_tool(
            client,
            token,
            "preview_spatial_proposal",
            proposal_id=proposal_id,
            composition_id="front",
            timestamp_ms=500,
        )
        assert preview["isError"] is False
        assert preview["structuredContent"]["hardware_command_sent"] is False
        assert preview["content"][1]["type"] == "image"
        assert preview["content"][1]["mimeType"] == "image/png"
        canvas = _mcp_tool(
            client,
            token,
            "preview_composition",
            proposal_id=proposal_id,
            composition_id="front",
            timestamp_ms=500,
            pixel_limit=0,
        )
        assert canvas["isError"] is False
        assert canvas["structuredContent"]["samples"] == []
        assert canvas["structuredContent"]["sample_page"]["total"] == 2
        assert canvas["structuredContent"]["image_size"] == [8, 4]
        page = _mcp_tool(
            client,
            token,
            "preview_composition",
            proposal_id=proposal_id,
            composition_id="front",
            timestamp_ms=500,
            pixel_offset=1,
            pixel_limit=1,
        )["structuredContent"]
        assert len(page["samples"]) == 1
        assert page["sample_page"] == {
            "offset": 1,
            "limit": 1,
            "total": 2,
            "next_offset": None,
        }
        assert (
            _mcp_tool(
                client,
                read_only,
                "preview_spatial_proposal",
                proposal_id=proposal_id,
                composition_id="front",
                timestamp_ms=500,
            )["isError"]
            is True
        )
        mapper["lighting_surfaces"][0]["mapping_revision"] = 2
        stale = _mcp_tool(
            client,
            token,
            "preview_composition",
            proposal_id=proposal_id,
            composition_id="front",
            timestamp_ms=500,
        )
        assert stale["isError"] is True
        assert "installation changed" in stale["content"][0]["text"]
        assert (
            _mcp_tool(
                client,
                token,
                "preview_spatial_proposal",
                proposal_id=proposal_id,
                composition_id="front",
                timestamp_ms=500,
            )["isError"]
            is True
        )


def test_mcp_shared_stage_discovery_authoring_preview_and_bake_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    installation_id = services.repository.get_runtime_installation().id
    image = tmp_path / "flag.gif"
    Image.new("RGB", (64, 32), "red").save(
        image,
        save_all=True,
        append_images=[Image.new("RGB", (64, 32), "blue")],
        duration=[500, 500],
        loop=0,
    )
    asset = SimpleNamespace(
        id="flag",
        name="Flag",
        status="ready",
        project_id=project_id,
        media_type="image/gif",
        digest_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
        size_bytes=image.stat().st_size,
        owner=None,
        license=None,
        review_status="approved",
        tags=(),
        metadata={"width": 64, "height": 32, "duration_ms": 1000, "private_path": "/not-exposed"},
        metadata_revision=1,
        draft_usage=(),
        removal_blockers=(),
    )
    monkeypatch.setattr(services.asset_upload, "get_original", lambda _: (asset, image))
    monkeypatch.setattr(services.asset_upload, "list_assets", lambda _, **__: (asset,))
    monkeypatch.setattr(services.render_jobs, "_ensure_worker", lambda: None)
    mapper: dict[str, Any] = {
        "projectors": [
            {
                "geometry_id": "projector-front",
                "resource": "projector.demo",
                "native_width": 64,
                "native_height": 32,
                "stage_extent": [0, 0, 8, 4],
                "surface_model": "planar",
                "mesh_vertices": [],
                "mesh_triangles": [],
                "calibration_revision": 1,
                "brightness_limit": 1,
            }
        ],
        "lighting_surfaces": [
            {
                "semantic_id": "facade",
                "resource": "roofline.demo",
                "name": "Facade",
                "local_width": 8,
                "local_height": 4,
                "local_points": [[0, 0], [8, 4]],
                "stage_matrix": [1, 0, 0, 1, 0, 0],
                "brightness_limit": 1,
                "mapping_revision": 1,
                "calibration_id": "calibrated",
                "pixels": [
                    {
                        "output_index": 0,
                        "local_x": 2,
                        "local_y": 1,
                        "stage_x": 2,
                        "stage_y": 1,
                        "footprint_width": 0.1,
                        "footprint_height": 0.1,
                    }
                ],
            }
        ],
        "fixtures": [],
        "paths": [],
        "zones": [],
    }
    monkeypatch.setattr(services.mapper_export, "export", lambda _: copy.deepcopy(mapper))
    editor = _mcp_token(
        services,
        project_id,
        (McpScope.READ, McpScope.DIRECT_EDIT, McpScope.SIMULATION),
    )
    reader = _mcp_token(services, project_id, (McpScope.READ,))
    proposer = _mcp_token(
        services,
        project_id,
        (McpScope.READ, McpScope.DRAFT_EDIT, McpScope.SIMULATION),
    )
    publisher = _mcp_token(services, project_id, (McpScope.READ, McpScope.PUBLISH))
    foreign = services.repository.create_project(
        name="Other project",
        draft=DraftProject.model_validate(_draft()),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    foreign_token = _mcp_token(
        services,
        foreign.project.id,
        (McpScope.READ, McpScope.DIRECT_EDIT, McpScope.SIMULATION),
    )
    profile = {
        "preset_id": "legacy_h264",
        "container": "mp4",
        "video_codec": "h264",
        "video_profile": "baseline",
        "video_level": "3.1",
        "pixel_format": "yuv420p",
        "max_video_bitrate_kbps": 1000,
        "audio_codec": "aac",
        "audio_sample_rate": 48000,
        "audio_channel_layout": "stereo",
        "loudness_target_lufs": -16,
        "keyframe_interval_frames": 2,
        "b_frames": 0,
        "hardware_decoder_compatibility": "h264_baseline_level_3_1_yuv420p",
    }
    document = _draft(
        editor_metadata={
            "installation_id": installation_id,
            "compositions": {
                "stage": {
                    "id": "stage",
                    "name": "House flag",
                    "space_kind": "stage",
                    "target_id": "shared-stage",
                    "width": 8,
                    "height": 4,
                    "duration_ms": 1000,
                    "stage_lighting_surfaces": ["facade"],
                    "children": [],
                    "render_settings": {
                        "render_pipeline_revision": 1,
                        "render_width": 64,
                        "render_height": 32,
                        "frame_rate_numerator": 2,
                        "frame_rate_denominator": 1,
                        "working_color_space": "linear_srgb",
                        "working_alpha_mode": "premultiplied",
                        "scaling_filter": "lanczos",
                        "output_background_color": "#000000",
                        "media_end_behavior": "hold_last_frame",
                        "output_profile": profile,
                    },
                }
            },
            "scene_compositions": {"ambient": ["stage"]},
            "timeline_tracks": {
                "stage": [
                    {
                        "id": "stage-track",
                        "name": "Overlay",
                        "kind": "group",
                        "resource_binding": "projector.demo",
                    }
                ]
            },
            "timeline_transform_layers": {
                "stage": [
                    {
                        "layer_schema_version": 1,
                        "id": "flag-layer",
                        "name": "Flag",
                        "kind": "video",
                        "source_id": "flag",
                        "track_id": "stage-track",
                        "start_ms": 0,
                        "duration_ms": 1000,
                        "in_point_ms": 0,
                        "out_point_ms": 1000,
                        "z_index": 0,
                        "fade_in_ms": 0,
                        "fade_out_ms": 0,
                        "playback": "loop",
                        "alpha_interpretation": "straight",
                        "source_color_space": "srgb",
                        "scaling_behavior": "stretch",
                        "keyframes": [
                            {
                                "id": "flag-frame",
                                "time_ms": 0,
                                "position": [4, 2],
                                "scale": [1, 1],
                                "rotation_deg": 0,
                                "anchor": [0.5, 0.5],
                                "opacity": 1,
                                "crop": [0, 0, 0, 0],
                                "interpolation": {"kind": "linear"},
                            }
                        ],
                    }
                ]
            },
        }
    )
    document["scenes"] = [
        {
            "schema_version": 2,
            "id": "ambient",
            "name": "Ambient",
            "mode": "media_synchronized",
            "duration_ms": 4000,
            "resources": ["projector.demo", "roofline.demo"],
            "clock_source": "media",
            "media": [
                {
                    "id": "media",
                    "resource": "projector.demo",
                    "asset": None,
                    "required": True,
                    "start_delay_ms": 0,
                }
            ],
            "lighting": [
                {
                    "id": "lights",
                    "resource": "roofline.demo",
                    "required": True,
                    "effect": "synthetic_sweep",
                }
            ],
        }
    ]
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        capabilities = _mcp_tool(client, editor, "get_capabilities")["structuredContent"]
        assert capabilities["mcp_contract_version"] == 2
        assert capabilities["features"]["mixed_output_bakes"] is True
        assert capabilities["permissions"]["can_bake"] is True
        assert not _mcp_tool(client, reader, "get_capabilities")["structuredContent"][
            "permissions"
        ]["can_bake"]
        assert (
            _mcp_tool(
                client, reader, "preview_composition", composition_id="stage", timestamp_ms=0
            )["isError"]
            is True
        )
        schema = _mcp_tool(client, reader, "get_authoring_schema")["structuredContent"]
        assert "compositions" in schema["properties"]["editor_metadata"]["properties"]
        assert "stage_lighting_surfaces" in schema["$defs"]["Composition"]["properties"]
        assert (
            schema["$defs"]["RenderSettings"]["properties"]["output_profile"]["discriminator"][
                "propertyName"
            ]
            == "preset_id"
        )
        from cueverge.gui.authoring_schema import SpatialMetadata

        SpatialMetadata.model_validate(document["editor_metadata"])
        assert schema["$defs"]["MediaLayer"]["properties"]["playback"]["enum"] == [
            "hold",
            "loop",
            "once",
        ]
        metadata = _mcp_tool(client, reader, "list_asset_metadata")["structuredContent"]
        assert metadata["assets"][0]["metadata"] == {"width": 64, "height": 32, "duration_ms": 1000}
        updated = _mcp_tool(
            client,
            editor,
            "update_project_draft",
            base_revision=1,
            draft=document,
        )
        assert updated["isError"] is False
        assert updated["structuredContent"]["revision"] == 2
        layout = _mcp_tool(
            client,
            reader,
            "get_spatial_layout",
            include_photo_geometry=True,
        )["structuredContent"]
        assert layout["stage_bounds"] == [0, 0, 8, 4]
        assert layout["projectors"][0]["calibration_revision"] == 1
        assert layout["existing_media_layers"]["stage"][0]["source_id"] == "flag"
        assert layout["photos"] == []
        assert layout["photo_geometry"] == []
        timeline = _mcp_tool(
            client,
            reader,
            "get_timeline_range",
            scene_id="stage",
            start_ms=100,
            end_ms=600,
        )["structuredContent"]
        assert timeline["transform_layers"][0]["id"] == "flag-layer"
        for resource in (None, "projector.demo"):
            preview = _mcp_tool(
                client,
                editor,
                "preview_composition",
                composition_id="stage",
                timestamp_ms=500,
                resource=resource,
                pixel_limit=1,
            )
            assert preview["isError"] is False
            assert preview["content"][1]["type"] == "image"
            assert preview["content"][1]["mimeType"] == "image/png"
            decoded = Image.open(BytesIO(base64.b64decode(preview["content"][1]["data"])))
            assert decoded.size == (64, 32)
            assert preview["structuredContent"]["surfaces"][0]["samples"][0]["color"] == [0, 0, 255]
            assert preview["structuredContent"]["hardware_command_sent"] is False
        for arguments in (
            {"timestamp_ms": -1},
            {"timestamp_ms": 1000},
            {"timestamp_ms": 0, "resource": "other"},
            {"timestamp_ms": 0, "pixel_limit": 501},
        ):
            assert (
                _mcp_tool(
                    client,
                    editor,
                    "preview_composition",
                    composition_id="stage",
                    **arguments,
                )["isError"]
                is True
            )
        for token in (reader, proposer):
            assert (
                _mcp_tool(
                    client,
                    token,
                    "create_render_job",
                    base_revision=2,
                    composition_id="stage",
                )["isError"]
                is True
            )
        assert (
            _mcp_tool(
                client,
                editor,
                "create_render_job",
                base_revision=1,
                composition_id="stage",
            )["isError"]
            is True
        )
        queued = _mcp_tool(
            client,
            editor,
            "create_render_job",
            base_revision=2,
            composition_id="stage",
        )["structuredContent"]
        job_id = queued["id"]
        assert queued["state"] == "queued"
        assert queued["hardware_command_sent"] is False
        assert (
            _mcp_tool(
                client,
                reader,
                "get_render_job",
                job_id=job_id,
                include_artifact=True,
            )["isError"]
            is True
        )
        for tool_name in ("get_render_job", "cancel_render_job"):
            assert _mcp_tool(client, foreign_token, tool_name, job_id=job_id)["isError"] is True
        assert _mcp_tool(client, reader, "cancel_render_job", job_id=job_id)["isError"] is True
        page = _mcp_tool(client, reader, "get_render_jobs", limit=1)["structuredContent"]
        assert page["total"] == 1
        assert page["jobs"][0]["id"] == job_id
        assert _mcp_tool(client, reader, "get_render_jobs", limit=101)["isError"] is True
        canceled = _mcp_tool(client, publisher, "cancel_render_job", job_id=job_id)
        assert canceled["structuredContent"]["state"] == "canceled"
        queued = _mcp_tool(
            client,
            publisher,
            "create_render_job",
            base_revision=2,
            composition_id="stage",
        )["structuredContent"]
        job = services.render_jobs._next_job()
        assert job is not None
        services.render_jobs._process(job)
        completed = _mcp_tool(
            client,
            reader,
            "get_render_job",
            job_id=queued["id"],
            include_artifact=True,
        )
        assert completed["isError"] is False
        assert completed["structuredContent"]["state"] == "completed"
        artifact = completed["structuredContent"]["artifact"]
        assert artifact["members"][0]["resource"] == "projector.demo"
        assert artifact["encoder"]["identity"].startswith("ffmpeg version ")
        assert "(PyAV " in artifact["encoder"]["identity"]
        assert artifact["members"][0]["streams"]["audio"]["boundary"]["metadata_inspected"]
        assert artifact["lighting_sequences"][0]["resource"] == "roofline.demo"
        assert (
            services.render_jobs.lighting_artifacts(project_id, 2)["stage"].name
            == (completed["structuredContent"]["artifact_id"])
        )
        changed = copy.deepcopy(document)
        changed["editor_metadata"]["timeline_transform_layers"]["stage"][0]["keyframes"][0][
            "opacity"
        ] = 0.5
        proposal = _mcp_tool(
            client,
            proposer,
            "propose_project_revision",
            base_revision=2,
            title="Dim flag",
            kind="timeline_change",
            proposed_draft=changed,
        )["structuredContent"]
        isolated = _mcp_tool(
            client,
            proposer,
            "preview_composition",
            composition_id="stage",
            timestamp_ms=0,
            proposal_id=proposal["proposal_id"],
        )
        assert isolated["isError"] is False
        assert (
            _mcp_tool(
                client,
                proposer,
                "preview_composition",
                composition_id="stage",
                timestamp_ms=0,
                proposal_id=proposal["proposal_id"],
                pixel_limit=501,
            )["isError"]
            is True
        )
        assert services.repository.get_revision(project_id).revision == 2
        assert (
            _mcp_tool(
                client,
                foreign_token,
                "preview_composition",
                composition_id="stage",
                timestamp_ms=0,
                proposal_id=proposal["proposal_id"],
            )["isError"]
            is True
        )
        oversized = copy.deepcopy(changed)
        oversized["editor_metadata"]["compositions"]["stage"]["render_settings"].update(
            {
                "render_width": 2048,
                "render_height": 1025,
            }
        )
        large_proposal = _mcp_tool(
            client,
            proposer,
            "propose_project_revision",
            base_revision=2,
            title="Oversized preview",
            kind="timeline_change",
            proposed_draft=oversized,
        )["structuredContent"]
        rejected = _mcp_tool(
            client,
            proposer,
            "preview_composition",
            composition_id="stage",
            timestamp_ms=0,
            proposal_id=large_proposal["proposal_id"],
        )
        assert rejected["isError"] is True
        assert "2 megapixel" in rejected["content"][0]["text"]
        changed["editor_metadata"]["compositions"]["stage"].update(
            {
                "space_kind": "projector_native",
                "target_id": "projector.demo",
                "stage_lighting_surfaces": [],
            }
        )
        native_proposal = _mcp_tool(
            client,
            proposer,
            "propose_project_revision",
            base_revision=2,
            title="Native preview",
            kind="timeline_change",
            proposed_draft=changed,
        )["structuredContent"]
        native = _mcp_tool(
            client,
            proposer,
            "preview_composition",
            composition_id="stage",
            timestamp_ms=0,
            resource="projector.demo",
            proposal_id=native_proposal["proposal_id"],
        )
        assert native["isError"] is False
        assert native["content"][1]["type"] == "image"
        updated = _mcp_tool(client, editor, "update_project_draft", base_revision=2, draft=changed)
        assert updated["isError"] is False
        assert (
            _mcp_tool(
                client,
                proposer,
                "preview_composition",
                composition_id="stage",
                timestamp_ms=0,
                proposal_id=proposal["proposal_id"],
            )["isError"]
            is True
        )
        actions = {event.action for event in services.repository.list_audit_events()}
        assert {"mcp.render.create", "mcp.render.cancel"}.issubset(actions)


def test_mcp_image_results_fit_exact_png_and_wire_budgets() -> None:
    import random

    from cueverge.gui.mcp_server import _image_result

    image = Image.frombytes("RGBA", (512, 512), random.Random(42).randbytes(512 * 512 * 4))
    result = _image_result(image, {"hardware_command_sent": False})
    encoded = json.loads(result.model_dump_json(by_alias=True))
    assert len(result.model_dump_json(by_alias=True).encode()) <= 256 * 1024
    assert len(base64.b64decode(encoded["content"][1]["data"])) <= 64 * 1024
    assert max(encoded["structuredContent"]["image_size"]) < 512
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="payload budget"):
        _image_result(image, {"samples": ["x" * 200000]})


def test_asset_trash_rest_and_mcp_share_blockers_revisions_and_purge(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    services.asset_upload.object_cleanup_grace_seconds = 0
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        _login(client)
        project = client.post(
            "/api/gui/v1/projects/from-active",
            json={"name": "Trash fixture"},
            headers=_mutation_headers(client),
        ).json()
        project_id = project["project"]["id"]
        content = b"trash-me"
        asset, upload = services.asset_upload.begin(
            project_id=project_id,
            name="Window loop",
            original_filename="loop.mp4",
            media_type="video/mp4",
            total_bytes=len(content),
            expected_sha256=None,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-20T12:00:00Z",
        )
        services.asset_upload.append(
            upload_id=upload.id,
            offset=0,
            chunk=content,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-20T12:00:01Z",
        )
        with sqlite3.connect(services.repository.database_path) as connection:
            connection.execute("UPDATE assets SET status = 'ready' WHERE id = ?", (asset.id,))
        scene = project["draft"]["scenes"][0]
        placed = client.post(
            f"/api/gui/v1/projects/{project_id}/assets/{asset.id}/place",
            json={
                "scene_id": scene["id"],
                "output_id": scene["media"][0]["id"] if scene["schema_version"] == 2 else None,
            },
            headers=_mutation_headers(client, if_match='"1"'),
        )
        assert placed.status_code == 200
        listed = client.get(f"/api/gui/v1/projects/{project_id}/assets").json()[0]
        assert listed["unused"] is False
        assert listed["draft_usage"] == [f'scene "{scene["name"]}"']
        assert "current draft" in listed["removal_blockers"][0]

        blocked = client.post(
            f"/api/gui/v1/projects/{project_id}/assets/{asset.id}/trash",
            headers=_mutation_headers(client, if_match='"1"'),
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "asset_in_use"
        assert blocked.json()["detail"]["blockers"] == listed["removal_blockers"]
        reader = _mcp_token(services, project_id, (McpScope.READ,))
        editor = _mcp_token(services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT))
        mcp_blocked = _mcp_tool(
            client, editor, "trash_asset", asset_id=asset.id, metadata_revision=1
        )
        assert mcp_blocked["isError"] is True
        assert "removal_blockers" in mcp_blocked["content"][0]["text"]
        denied = _mcp_tool(client, reader, "trash_asset", asset_id=asset.id, metadata_revision=1)
        assert denied["isError"] is True
        assert "direct_edit" in denied["content"][0]["text"]

        services.repository.update_draft(
            project_id=project_id,
            expected_revision=2,
            draft=DraftProject.model_validate(project["draft"]),
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-20T12:01:00Z",
        )
        capabilities = _mcp_tool(client, reader, "get_capabilities")["structuredContent"]
        assert capabilities["features"]["asset_trash"] is True
        assert capabilities["asset_lifecycle"]["trash_tool"] == "trash_asset"
        assert capabilities["asset_lifecycle"]["permanent_delete"] == "administrator_gui_only"
        metadata = _mcp_tool(client, reader, "list_asset_metadata")["structuredContent"]
        assert metadata["assets"][0]["metadata_revision"] == 1
        assert metadata["assets"][0]["removal_blockers"] == []
        trashed = _mcp_tool(client, editor, "trash_asset", asset_id=asset.id, metadata_revision=1)
        assert trashed["isError"] is False
        assert trashed["structuredContent"]["metadata_revision"] == 2
        assert trashed["structuredContent"]["purge_after"] is not None
        assert client.get(f"/api/gui/v1/projects/{project_id}/assets").json() == []
        trash = client.get(f"/api/gui/v1/projects/{project_id}/assets/trash").json()
        assert [item["id"] for item in trash] == [asset.id]
        assert trash[0]["trashed_by"] == "admin"
        mcp_trash = _mcp_tool(client, reader, "list_trashed_assets")["structuredContent"]
        assert mcp_trash["retention_days"] == 30
        assert mcp_trash["assets"][0]["id"] == asset.id
        rejected_place = client.post(
            f"/api/gui/v1/projects/{project_id}/assets/{asset.id}/place",
            json={
                "scene_id": scene["id"],
                "output_id": scene["media"][0]["id"] if scene["schema_version"] == 2 else None,
            },
            headers=_mutation_headers(client, if_match='"3"'),
        )
        assert rejected_place.status_code == 409
        assert rejected_place.json()["detail"]["code"] == "asset_trashed"

        stale = client.post(
            f"/api/gui/v1/projects/{project_id}/assets/{asset.id}/restore",
            headers=_mutation_headers(client, if_match='"1"'),
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "asset_metadata_conflict"
        restored = _mcp_tool(
            client, editor, "restore_asset", asset_id=asset.id, metadata_revision=2
        )
        assert restored["isError"] is False
        assert restored["structuredContent"]["metadata_revision"] == 3
        retrashed = client.post(
            f"/api/gui/v1/projects/{project_id}/assets/{asset.id}/trash",
            headers=_mutation_headers(client, if_match='"3"'),
        )
        assert retrashed.status_code == 200
        assert retrashed.headers["etag"] == '"4"'
        mismatch = client.post(
            f"/api/gui/v1/projects/{project_id}/assets/{asset.id}/purge",
            json={"confirmation_name": "window loop"},
            headers=_mutation_headers(client, if_match='"4"'),
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["detail"]["code"] == "asset_purge_confirmation_mismatch"
        purged = client.post(
            f"/api/gui/v1/projects/{project_id}/assets/{asset.id}/purge",
            json={"confirmation_name": "Window loop"},
            headers=_mutation_headers(client, if_match='"4"'),
        )
        assert purged.status_code == 200
        assert purged.json()["released_bytes"] == len(content)
        assert client.get(f"/api/gui/v1/projects/{project_id}/assets/trash").json() == []
        actions = {event.action for event in services.repository.list_audit_events()}
        assert {"asset.trash", "asset.restore", "asset.purge", "mcp.asset.trash"} <= actions


def test_scene_media_preview_rest_and_mcp_render_assigned_media(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime)
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        _login(client)
        project = client.post(
            "/api/gui/v1/projects/from-active",
            json={"name": "Media preview fixture"},
            headers=_mutation_headers(client),
        ).json()
        project_id = project["project"]["id"]
        image = BytesIO()
        Image.new("RGB", (32, 18), (200, 40, 10)).save(image, format="PNG")
        content = image.getvalue()
        asset, upload = services.asset_upload.begin(
            project_id=project_id,
            name="Red card",
            original_filename="red.png",
            media_type="image/png",
            total_bytes=len(content),
            expected_sha256=None,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-20T12:00:00Z",
        )
        services.asset_upload.append(
            upload_id=upload.id,
            offset=0,
            chunk=content,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-20T12:00:01Z",
        )
        with sqlite3.connect(services.repository.database_path) as connection:
            connection.execute("UPDATE assets SET status = 'ready' WHERE id = ?", (asset.id,))
        scene = project["draft"]["scenes"][0]
        output_id = scene["media"][0]["id"] if scene["schema_version"] == 2 else None
        placed = client.post(
            f"/api/gui/v1/projects/{project_id}/assets/{asset.id}/place",
            json={"scene_id": scene["id"], "output_id": output_id},
            headers=_mutation_headers(client, if_match='"1"'),
        )
        assert placed.status_code == 200

        response = client.post(
            f"/api/gui/v1/projects/{project_id}/scene-media-preview",
            json={"source_revision": 2, "scene_id": scene["id"], "timestamp_ms": 0},
            headers=_mutation_headers(client),
        )
        assert response.status_code == 200
        preview = response.json()
        assert preview["hardware_commands_sent"] is False
        output = preview["outputs"][0]
        assert output["state"] == "playing"
        assert output["source"] == {
            "kind": "asset",
            "asset_id": asset.id,
            "name": "Red card",
            "media_type": "image/png",
        }
        decoded = Image.open(BytesIO(base64.b64decode(output["frame"]["data_base64"])))
        assert decoded.size == (32, 18)
        assert decoded.convert("RGB").getpixel((0, 0)) == (200, 40, 10)
        invalid = client.post(
            f"/api/gui/v1/projects/{project_id}/scene-media-preview",
            json={"source_revision": 2, "scene_id": "missing", "timestamp_ms": 0},
            headers=_mutation_headers(client),
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "scene_media_preview_invalid"

        reader = _mcp_token(services, project_id, (McpScope.READ,))
        simulator = _mcp_token(services, project_id, (McpScope.READ, McpScope.SIMULATION))
        capabilities = _mcp_tool(client, reader, "get_capabilities")["structuredContent"]
        assert capabilities["features"]["scene_media_preview"] is True
        assert capabilities["tools"]["scene_media_preview"] == "preview_scene_media"
        assert capabilities["scene_media_preview"]["requires_scope"] == "simulation"
        denied = _mcp_tool(
            client, reader, "preview_scene_media", scene_id=scene["id"], timestamp_ms=0
        )
        assert denied["isError"] is True
        tool = _mcp_tool(
            client, simulator, "preview_scene_media", scene_id=scene["id"], timestamp_ms=0
        )
        assert tool["isError"] is False
        structured = tool["structuredContent"]["outputs"][0]
        assert structured["frame"]["attached_image_index"] == 0
        assert "data_base64" not in structured["frame"]
        assert tool["content"][1]["type"] == "image"
        assert tool["content"][1]["mimeType"] == "image/png"


def _mcp_token(
    services: GuiServices,
    project_id: str,
    scopes: tuple[McpScope, ...],
    *,
    username: str = "admin",
) -> str:
    return services.mcp_store.create_token(
        client_id="chat-fixture",
        username=username,
        project_id=project_id,
        scopes=scopes,
        expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        occurred_at=datetime.now(UTC).isoformat(),
    ).token


def _mcp_tool(client: TestClient, token: str, tool_name: str, **arguments: Any) -> dict[str, Any]:
    response = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json()["result"])


def test_physical_output_test_api_streams_and_disarms_without_publishing(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1)
        runtime = ControllerRuntime(
            sink=FrameSinkRouter(
                {
                    "roofline.demo": DdpUdpSink(
                        "127.0.0.1",
                        port=receiver.getsockname()[1],
                        expected_pixel_count=8,
                    ),
                }
            )
        )
        services = _services(tmp_path, runtime)
        with TestClient(create_app(runtime, gui_services=services)) as client:
            assert client.get("/api/gui/v1/maintenance/output-tests").status_code == 401
            _login(client)
            body = {
                "resource": "roofline.demo",
                "confirmation": "roofline.demo",
                "duration_seconds": 5,
                "speed": 30,
                "use_mapping": False,
            }
            assert (
                client.post(
                    "/api/gui/v1/maintenance/output-tests",
                    json=body,
                    headers={"Origin": ORIGIN},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/api/gui/v1/maintenance/output-tests",
                    json=body,
                    headers=_mutation_headers(client),
                ).status_code
                == 409
            )
            client.post(
                "/api/gui/v1/operate/actions",
                json={"action": "maintenance"},
                headers=_mutation_headers(client),
            )
            available = client.get("/api/gui/v1/maintenance/output-tests").json()
            assert [item["resource"] for item in available["outputs"]] == ["roofline.demo"]
            rejected = client.post(
                "/api/gui/v1/maintenance/output-tests",
                json={**body, "confirmation": "wrong"},
                headers=_mutation_headers(client),
            )
            assert rejected.status_code == 422
            started = client.post(
                "/api/gui/v1/maintenance/output-tests",
                json=body,
                headers=_mutation_headers(client),
            )
            assert started.status_code == 201, started.text
            test = started.json()
            assert test["simulation_only"] is False
            assert test["frames_sent"] >= 1
            assert receiver.recv(1024)[10:13] == bytes([255, 255, 255])
            assert (
                client.post(
                    "/api/gui/v1/maintenance/output-tests",
                    json=body,
                    headers=_mutation_headers(client),
                ).status_code
                == 409
            )
            assert (
                client.post(
                    "/api/gui/v1/maintenance/output-arms",
                    json={
                        key: body[key] for key in ("resource", "confirmation", "duration_seconds")
                    },
                    headers=_mutation_headers(client),
                ).status_code
                == 409
            )
            assert (
                client.post(
                    "/api/gui/v1/maintenance/output-tests/not-the-current-test/stop",
                    headers=_mutation_headers(client),
                ).status_code
                == 404
            )
            stopped = client.post(
                f"/api/gui/v1/maintenance/output-arms/{test['arm_id']}/stop",
                headers=_mutation_headers(client),
            )
            assert stopped.status_code == 200
            assert stopped.json()["hardware_command_sent"] is True
            current = client.get("/api/gui/v1/maintenance/output-tests").json()["test"]
            assert current["state"] == "stopped"
            actions = {event.action for event in services.repository.list_audit_events()}
            assert "maintenance.output_test.started" in actions
            assert "maintenance.output_test.stopped" in actions
            assert not services.repository.list_projects()


@pytest.mark.parametrize("role", [Role.VIEWER, Role.DESIGNER, Role.PUBLISHER])
def test_physical_output_tests_require_operator_role(tmp_path: Path, role: Role) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime, role=role)
    with TestClient(create_app(runtime, gui_services=services)) as client:
        _login(client)
        assert (
            client.post(
                "/api/gui/v1/maintenance/output-tests",
                json={"resource": "roofline.demo", "confirmation": "roofline.demo"},
                headers=_mutation_headers(client),
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/gui/v1/maintenance/output-tests/any/stop",
                headers=_mutation_headers(client),
            ).status_code
            == 403
        )


def test_physical_test_overview_excludes_unconfigured_or_mismatched_drivers(tmp_path: Path) -> None:
    runtime = ControllerRuntime(
        sink=FrameSinkRouter(
            {
                "roofline.demo": DdpUdpSink("127.0.0.1", expected_pixel_count=99),
            }
        )
    )
    with TestClient(create_app(runtime, gui_services=_services(tmp_path, runtime))) as client:
        _login(client)
        assert client.get("/api/gui/v1/maintenance/output-tests").json()["outputs"] == []
        client.post(
            "/api/gui/v1/operate/actions",
            json={"action": "maintenance"},
            headers=_mutation_headers(client),
        )
        rejected = client.post(
            "/api/gui/v1/maintenance/output-tests",
            json={"resource": "roofline.demo", "confirmation": "roofline.demo"},
            headers=_mutation_headers(client),
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == (
            "output_test_driver_missing_or_pixel_count_mismatch"
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
