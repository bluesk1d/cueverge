from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.mcp_authoring import McpScope, allowed_mcp_scopes
from cueverge.gui.passwords import hash_password
from cueverge.gui.service import GuiServices

ORIGIN = "http://localhost:5173"
PASSWORD = "original setup regression password"
PASSWORD_HASH = hash_password(PASSWORD)


def setup(tmp_path: Path, role: Role = Role.ADMINISTRATOR):
    runtime = ControllerRuntime()
    services = GuiServices.create(
        GuiConfig(
            data_root=tmp_path,
            admin_password_hash=SecretStr(PASSWORD_HASH),
            admin_role=role,
            allowed_origins=(ORIGIN,),
            secure_cookies=False,
        ),
        runtime,
    )
    return runtime, services


def login(client: TestClient) -> dict[str, str]:
    result = client.post(
        "/api/gui/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert result.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["cueverge_csrf"]}


def image() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (12, 8), "#123456").save(stream, format="PNG")
    return stream.getvalue()


def test_description_upload_supports_unicode_and_keeps_legacy_headers(tmp_path: Path) -> None:
    runtime, services = setup(tmp_path)
    installation = services.repository.get_runtime_installation().id
    with TestClient(create_app(runtime, gui_services=services)) as client:
        headers = login(client)
        description = "Front porch \U0001f383"
        filename = "front-\u00e9.png"
        result = client.post(
            f"/api/gui/v1/installations/{installation}/photos",
            content=image(),
            headers={
                **headers,
                "Content-Type": "image/png",
                "X-Photo-Description": quote(description),
                "X-Photo-Metadata-Encoding": "uri",
                "X-Original-Filename": quote(filename),
            },
        )
        assert result.status_code == 201
        assert result.json()["description"] == description
        assert result.json()["name"] == description
        assert result.json()["viewpoint"] == description
        assert result.json()["original_filename"] == filename
        legacy = client.post(
            f"/api/gui/v1/installations/{installation}/photos",
            content=image(),
            headers={
                **headers,
                "Content-Type": "image/png",
                "X-Original-Filename": "old.png",
                "X-Photo-Name": "Legacy photo",
                "X-Photo-Viewpoint": "Independent legacy view",
            },
        )
        assert legacy.status_code == 201
        assert legacy.json()["description"] == "Legacy photo"
        assert legacy.json()["viewpoint"] == "Independent legacy view"


def test_photo_description_preserves_view_geometry_crop_and_requires_revision_csrf(
    tmp_path: Path,
) -> None:
    runtime, services = setup(tmp_path)
    installation = services.repository.get_runtime_installation().id
    with TestClient(create_app(runtime, gui_services=services)) as client:
        headers = login(client)
        result = client.post(
            f"/api/gui/v1/installations/{installation}/photos",
            content=image(),
            headers={
                **headers,
                "Content-Type": "image/png",
                "X-Original-Filename": "original.png",
                "X-Photo-Name": "Old label",
                "X-Photo-Viewpoint": "Stable independent view",
            },
        )
        photo = result.json()
        changed = client.put(
            f"/api/gui/v1/installation-photos/{photo['id']}",
            headers={**headers, "If-Match": '"1"'},
            json={
                "name": photo["name"],
                "viewpoint": photo["viewpoint"],
                "orientation_degrees": 90,
                "crop_x": 0.1,
                "crop_y": 0.2,
                "crop_width": 0.6,
                "crop_height": 0.5,
                "scale_hint": 1e-10,
            },
        )
        assert changed.status_code == 200
        before = changed.json()
        geometry = client.post(
            f"/api/gui/v1/installations/{installation}/geometry",
            headers=headers,
            json={
                "photo_id": photo["id"],
                "kind": "fixture",
                "name": "Porch fixture",
                "points": [{"x": 0.2, "y": 0.3}],
                "closed": False,
            },
        )
        assert geometry.status_code == 201
        path = f"/api/gui/v1/installation-photos/{photo['id']}/description"
        assert client.patch(path, json={"description": "No CSRF"}).status_code == 403
        renamed = client.patch(
            path, headers={**headers, "If-Match": '"2"'}, json={"description": "Front porch layout"}
        )
        assert renamed.status_code == 200
        after = renamed.json()
        expected = {
            **before,
            "name": "Front porch layout",
            "description": "Front porch layout",
            "revision": 3,
            "updated_at": after["updated_at"],
        }
        assert after == expected
        assert client.get(f"/api/gui/v1/installations/{installation}/geometry").json() == [
            geometry.json()
        ]
        assert (
            client.patch(
                path, headers={**headers, "If-Match": '"2"'}, json={"description": "Stale"}
            ).status_code
            == 409
        )
        assert (
            client.patch(
                path, headers={**headers, "If-Match": '"3"'}, json={"description": " "}
            ).status_code
            == 422
        )
        assert (
            client.get(f"/api/gui/v1/installation-photos/{photo['id']}/content").content == image()
        )


def test_mcp_setup_is_bound_paginated_and_photo_writes_need_separate_scope(tmp_path: Path) -> None:
    runtime, services = setup(tmp_path)
    installation = services.repository.get_runtime_installation().id
    photo = services.installation_photos.create(
        installation_id=installation,
        name="Original photo",
        viewpoint="Legacy view",
        original_filename="original.png",
        media_type="image/png",
        content=image(),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    draft = DraftProject.model_validate_json(
        Path("tests/fixtures/usability-authoring.json").read_text()
    )
    draft = draft.model_copy(
        update={"editor_metadata": {**draft.editor_metadata, "installation_id": installation}}
    )
    project = services.repository.create_project(
        name="Original setup fixture",
        draft=draft,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )

    def token(scopes, project_id=project.project.id):
        return services.mcp_store.create_token(
            client_id="setup-fixture",
            username="admin",
            project_id=project_id,
            scopes=scopes,
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            occurred_at=datetime.now(UTC).isoformat(),
        ).token

    def tool(client, credential, name, **arguments):
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
        direct = token((McpScope.READ, McpScope.DIRECT_EDIT))
        assert tool(
            client,
            direct,
            "describe_installation_photo",
            photo_id=photo.id,
            source_revision=1,
            description="Blocked",
        )["isError"]
        granted = token((McpScope.READ, McpScope.INSTALLATION_EDIT))
        metadata = tool(client, granted, "get_installation_setup")["structuredContent"]
        assert metadata["photos"][0]["description"] == "Original photo"
        assert metadata["device_setup"]["mcp_device_writes"] is False
        result = tool(
            client,
            granted,
            "describe_installation_photo",
            photo_id=photo.id,
            source_revision=1,
            description="Renamed description",
        )["structuredContent"]
        assert result["description"] == "Renamed description"
        assert result["viewpoint"] == "Legacy view"
        assert result["hardware_commands_sent"] is False
        assert services.repository.get_revision(project.project.id).revision == 1
        assert tool(client, granted, "get_installation_setup", limit=0)["isError"]
        assert tool(client, granted, "get_installation_setup", photo_id="unrelated-photo")[
            "isError"
        ]
        assert tool(
            client,
            granted,
            "describe_installation_photo",
            photo_id=photo.id,
            source_revision=1,
            description="Stale",
        )["isError"]
    assert McpScope.INSTALLATION_EDIT not in allowed_mcp_scopes(
        Role.VIEWER, Role.ADMINISTRATOR, True
    )
    assert McpScope.INSTALLATION_EDIT not in allowed_mcp_scopes(Role.DESIGNER, Role.VIEWER, True)


def test_viewer_cannot_mutate_photo_description(tmp_path: Path) -> None:
    runtime, services = setup(tmp_path, Role.VIEWER)
    photo = services.installation_photos.create(
        installation_id=services.repository.get_runtime_installation().id,
        name="Original",
        viewpoint="Stable",
        original_filename="original.png",
        media_type="image/png",
        content=image(),
        actor="seed",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    with TestClient(create_app(runtime, gui_services=services)) as client:
        headers = login(client)
        response = client.patch(
            f"/api/gui/v1/installation-photos/{photo.id}/description",
            headers={**headers, "If-Match": '"1"'},
            json={"description": "Denied"},
        )
        assert response.status_code == 403
