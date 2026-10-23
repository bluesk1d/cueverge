from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.installation_objects import installation_object_inventory
from cueverge.gui.mcp_authoring import McpScope
from tests.unit.test_gui_foundation import (
    _draft,
    _login,
    _mcp_fixture,
    _mcp_token,
    _mcp_tool,
    _mutation_headers,
    _services,
)


def definition(**changes: Any) -> dict[str, Any]:
    return {
        "resource": "roofline.demo",
        "kind": "fixture",
        "id": "porch",
        "name": "Porch outline",
        "segments": [{"segment": "left", "reverse": False}],
        "precedence": 50,
        "geometry_semantic_ids": [],
        **changes,
    }


@pytest.mark.parametrize("via_mcp", [False, True])
def test_objects_are_discoverable_revisioned_and_shared_by_api_mcp_and_runtime(
    tmp_path: Path, via_mcp: bool
) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    installation_id = services.repository.get_runtime_installation().id
    document = _draft(editor_metadata={"installation_id": installation_id, "custom": "keep"})
    document["installation"]["resources"][1]["segments"] = [
        {"id": "left", "start": 0, "end": 3},
        {"id": "right", "start": 3, "end": 8},
    ]
    original = DraftProject.model_validate(document)
    stored = services.repository.create_project(
        name="Object authoring",
        draft=original,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    project_id = stored.project.id
    url = f"/api/gui/v1/projects/{project_id}/installation-objects"
    token = _mcp_token(
        services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT, McpScope.SIMULATION)
    )
    read_token = _mcp_token(services, project_id, (McpScope.READ, McpScope.DRAFT_EDIT))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        assert client.get(url).status_code == 401
        assert client.post(f"{url}/create", json=definition()).status_code == 401
        _login(client)
        assert client.post(f"{url}/create", json=definition()).status_code == 403
        assert (
            client.post(
                f"{url}/create", json=definition(), headers=_mutation_headers(client)
            ).status_code
            == 428
        )
        inventory = client.get(url).json()
        assert (
            inventory
            == _mcp_tool(client, read_token, "get_installation_objects")["structuredContent"]
        )
        assert len(inventory["objects"]) == 2
        assert inventory["editable_kinds"] == ["fixture", "path"]
        assert inventory["layout_available"] is True
        schema = _mcp_tool(client, read_token, "get_authoring_schema")["structuredContent"]
        assert schema["x-installation-objects"]["write_scope"] == "direct_edit"
        capabilities = _mcp_tool(client, read_token, "get_capabilities")["structuredContent"]
        assert capabilities["features"]["installation_objects"] is True
        assert capabilities["tools"]["edit_installation_object"] == "edit_installation_object"
        assert (
            _mcp_tool(
                client,
                read_token,
                "edit_installation_object",
                action="create",
                base_revision=1,
                request=definition(),
            )["isError"]
            is True
        )

        def edit(action: str, revision: int, request: dict[str, Any]) -> int:
            if via_mcp:
                result = _mcp_tool(
                    client,
                    token,
                    "edit_installation_object",
                    action=action,
                    base_revision=revision,
                    request=request,
                )
                assert result["isError"] is False, result
                assert result["structuredContent"]["hardware_commands_sent"] is False
                assert result["structuredContent"]["revision"] == revision + 1
            else:
                response = client.post(
                    f"{url}/{action}",
                    json=request,
                    headers=_mutation_headers(client, if_match=f'"{revision}"'),
                )
                assert response.status_code == 200, response.text
                assert response.headers["etag"] == f'"{revision + 1}"'
            return revision + 1

        revision = edit("create", 1, definition())
        inventory = client.get(url).json()
        fixture = next(item for item in inventory["objects"] if item["kind"] == "fixture")
        assert fixture["name"] == "Porch outline"
        assert fixture["geometry_ids"] == []
        assert fixture["references"] == []
        assert fixture["pixel_count"] == 3
        assert fixture["traversal"] == [
            {
                "segment": "left",
                "first_pixel": 0,
                "last_pixel": 2,
                "pixel_count": 3,
                "reverse": False,
            }
        ]
        # Creation through either interface is immediately readable through the other.
        assert (
            inventory == _mcp_tool(client, token, "get_installation_objects")["structuredContent"]
        )
        for body in (
            definition(),
            definition(id="bad", segments=[{"segment": "unknown"}]),
            definition(id="bad", segments=[{"segment": "left"}, {"segment": "left"}]),
            definition(id="bad", geometry_semantic_ids=["unrelated-installation-shape"]),
            definition(id="bad", kind="path", precedence=20),
            definition(id="bad", name=" "),
        ):
            assert (
                client.post(
                    f"{url}/create",
                    json=body,
                    headers=_mutation_headers(client, if_match=f'"{revision}"'),
                ).status_code
                == 422
            )
        assert (
            client.post(
                f"{url}/update",
                json=definition(),
                headers=_mutation_headers(client, if_match='"1"'),
            ).status_code
            == 409
        )
        assert (
            _mcp_tool(
                client,
                token,
                "edit_installation_object",
                action="update",
                base_revision=1,
                request=definition(),
            )["isError"]
            is True
        )
        revised = definition(
            name="Porch and eave",
            precedence=100,
            segments=[{"segment": "right", "reverse": True}, {"segment": "left", "reverse": False}],
        )
        revision = edit("update", revision, revised)
        updated = services.repository.get_revision(project_id)
        assert updated.draft.scenes == original.scenes
        assert updated.draft.editor_metadata["custom"] == "keep"
        assert (
            updated.draft.installation.resources[1].segments
            == original.installation.resources[1].segments
        )
        # An effect targeting this fixture resolves the newly saved membership and precedence.
        targeted = updated.draft.model_dump(mode="json")
        targeted["scenes"][0]["lighting"] = {
            "resource": "roofline.demo",
            "effect": "segment_pulse",
            "target": {"kind": "fixture", "id": "porch"},
            "pulse_times_ms": [0],
        }
        saved = services.repository.update_draft(
            project_id=project_id,
            expected_revision=revision,
            draft=DraftProject.model_validate(targeted),
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=datetime.now(UTC).isoformat(),
        )
        revision = saved.revision
        package = services.compiler.compile(
            project_id=project_id, revision=revision, draft=saved.draft
        )
        loaded = load_show_package(package.package_root).scenes["ambient"]
        assert loaded.lighting_target_indices == (7, 6, 5, 4, 3, 0, 1, 2)
        assert loaded.lighting_target_precedence == 100
        preview = _mcp_tool(
            client,
            token,
            "preview_scene_lighting",
            source_revision=revision,
            scene_id="ambient",
            timestamp_ms=0,
            resource="roofline.demo",
            pixel_limit=8,
        )
        assert preview["isError"] is False, preview
        assert preview["structuredContent"]["hardware_commands_sent"] is False
        assert preview["structuredContent"]["source_revision"] == revision
        assert len(preview["structuredContent"]["outputs"][0]["colors"]) == 8
        fixture = next(
            item for item in client.get(url).json()["objects"] if item["kind"] == "fixture"
        )
        assert fixture["references"] == ["/scenes/ambient/lighting"]
        deletion = {"resource": "roofline.demo", "kind": "fixture", "id": "porch"}
        result = client.post(
            f"{url}/delete",
            json=deletion,
            headers=_mutation_headers(client, if_match=f'"{revision}"'),
        )
        assert result.status_code == 422
        assert "/scenes/ambient/lighting" in result.text
        assert (
            _mcp_tool(
                client,
                token,
                "edit_installation_object",
                action="delete",
                base_revision=revision,
                request=deletion,
            )["isError"]
            is True
        )
        revision = edit(
            "create",
            revision,
            definition(
                id="walk",
                kind="path",
                name="Ordered walk",
                precedence=0,
            ),
        )
        revision = edit(
            "delete",
            revision,
            {
                "resource": "roofline.demo",
                "kind": "path",
                "id": "walk",
            },
        )
        assert not any(item["kind"] == "path" for item in client.get(url).json()["objects"])
        assert services.repository.get_revision(project_id).revision == revision
        services.repository.archive_project(
            project_id=project_id,
            expected_revision=1,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=datetime.now(UTC).isoformat(),
        )
        assert client.get(url).json()["archived"] is True
        assert (
            client.post(
                f"{url}/create",
                json=definition(id="archived"),
                headers=_mutation_headers(client, if_match=f'"{revision}"'),
            ).status_code
            == 422
        )
        assert (
            _mcp_tool(
                client,
                token,
                "edit_installation_object",
                action="create",
                base_revision=revision,
                request=definition(id="archived"),
            )["isError"]
            is True
        )


def test_layout_links_and_mapped_members_never_duplicate_or_delete_geometry(tmp_path: Path) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    installation = services.repository.get_runtime_installation().id
    document = _draft(editor_metadata={"installation_id": installation})
    document["installation"]["resources"][1]["segments"] = [{"id": "left", "start": 0, "end": 8}]
    stored = services.repository.create_project(
        name="Layout links",
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    token = _mcp_token(services, stored.project.id, (McpScope.READ, McpScope.DIRECT_EDIT))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        _login(client)
        shape = client.post(
            f"/api/gui/v1/installations/{installation}/geometry",
            json={
                "kind": "fixture",
                "name": "Porch marker",
                "photo_id": None,
                "points": [{"x": 0.4, "y": 0.6}],
                "closed": False,
            },
            headers=_mutation_headers(client),
        )
        assert shape.status_code == 201, shape.text
        geometry = shape.json()
        result = _mcp_tool(
            client,
            token,
            "edit_installation_object",
            action="create",
            base_revision=1,
            request=definition(geometry_semantic_ids=[geometry["semantic_id"]]),
        )
        assert result["isError"] is False, result
        inventory = _mcp_tool(client, token, "get_installation_objects")["structuredContent"]
        fixture = next(item for item in inventory["objects"] if item["kind"] == "fixture")
        assert fixture["geometry_ids"] == [geometry["id"]]
        assert len(services.installation_geometry.list(installation)) == 1
        # Member geometry is discoverable without a separate fixture marker or explicit link.
        segment = client.post(
            f"/api/gui/v1/installations/{installation}/geometry",
            json={
                "kind": "pixel_segment",
                "name": "Mapped member",
                "photo_id": None,
                "points": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.1}],
                "closed": False,
                "start_index": 0,
                "pixel_count": 8,
            },
            headers=_mutation_headers(client),
        )
        assert segment.status_code == 201, segment.text
        segment_id = segment.json()["id"]
        surface = client.put(
            f"/api/gui/v1/installation-geometry/{segment_id}/lighting-surface",
            headers=_mutation_headers(client),
            json={
                "resource": "roofline.demo",
                "local_width": 1,
                "local_height": 1,
                "local_points": [[0, 0], [1, 0]],
                "stage_matrix": [1, 0, 0, 1, 0, 0],
            },
        )
        assert surface.status_code == 200, surface.text
        result = _mcp_tool(client, token, "get_installation_objects")["structuredContent"]
        fixture = next(item for item in result["objects"] if item["kind"] == "fixture")
        assert set(fixture["geometry_ids"]) == {geometry["id"], segment_id}
        assert (
            _mcp_tool(
                client,
                token,
                "edit_installation_object",
                action="update",
                base_revision=2,
                request=definition(),
            )["isError"]
            is False
        )
        assert services.installation_geometry.get(geometry["id"]).revision == geometry["revision"]
        assert (
            _mcp_tool(
                client,
                token,
                "edit_installation_object",
                action="delete",
                base_revision=3,
                request={"resource": "roofline.demo", "kind": "fixture", "id": "porch"},
            )["isError"]
            is False
        )
        assert len(services.installation_geometry.list(installation)) == 2


def test_object_edits_enforce_current_permissions(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime, role=Role.VIEWER)
    stored = services.repository.create_project(
        name="Read-only objects",
        draft=DraftProject.model_validate(_draft()),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    project_id = stored.project.id
    services.collaboration.put_membership(
        project_id, "admin", Role.VIEWER, False, datetime.now(UTC).isoformat()
    )
    token = _mcp_token(services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        _login(client)
        url = f"/api/gui/v1/projects/{project_id}/installation-objects"
        assert client.get(url).status_code == 200
        assert (
            client.post(
                f"{url}/create",
                json=definition(),
                headers=_mutation_headers(client, if_match='"1"'),
            ).status_code
            == 403
        )
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "edit_installation_object",
                    "arguments": {"action": "create", "base_revision": 1, "request": definition()},
                },
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert response.status_code == 401
        assert services.repository.get_revision(project_id).revision == 1


def test_reference_discovery_respects_outputs_and_preserves_authoring_presets(
    tmp_path: Path,
) -> None:
    _, services, _ = _mcp_fixture(tmp_path)
    document = _draft()
    resource = document["installation"]["resources"][1]
    resource.update(
        segments=[{"id": "left", "start": 0, "end": 8}],
        fixtures=[{"id": "porch", "segments": [{"segment": "left"}]}],
    )
    document["installation"]["resources"].append({**resource, "resource": "other"})
    document["scenes"][0]["lighting"] = {
        "resource": "roofline.demo",
        "effect": "segment_pulse",
        "target": {"kind": "fixture", "id": "porch"},
        "pulse_times_ms": [0],
    }
    stored = services.repository.create_project(
        name="Reference scope",
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    inventory = installation_object_inventory(services, stored.project.id)
    fixtures = {item.resource: item for item in inventory.objects if item.kind == "fixture"}
    assert fixtures["roofline.demo"].references == ("/scenes/ambient/lighting",)
    assert fixtures["other"].references == ()
    document["editor_metadata"]["timeline_cue_presets"] = [
        {
            "id": "saved-effect",
            "name": "Saved porch effect",
            "lighting": {
                "primitive": "solid",
                "duration_ms": 100,
                "target": {"kind": "fixture", "id": "porch"},
            },
        }
    ]
    services.repository.update_draft(
        project_id=stored.project.id,
        expected_revision=1,
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    inventory = installation_object_inventory(services, stored.project.id)
    assert all(
        "/editor_metadata/timeline_cue_presets/0/lighting" in item.references
        for item in inventory.objects
        if item.kind == "fixture"
    )
