import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import Role
from cueverge.gui.mcp_authoring import McpScope
from cueverge.gui.passwords import hash_password
from cueverge.gui.service import GuiServices

ORIGIN = "http://localhost:5173"
PASSWORD = "original optional-photo test"
PASSWORD_HASH = hash_password(PASSWORD)


def image(color="red", size=(12, 8)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def workspace(tmp_path: Path):
    runtime = ControllerRuntime()
    services = GuiServices.create(
        GuiConfig(
            data_root=tmp_path,
            admin_password_hash=SecretStr(PASSWORD_HASH),
            allowed_origins=(ORIGIN,),
            secure_cookies=False,
        ),
        runtime,
    )
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        client.post(
            "/api/gui/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        ).raise_for_status()
        headers = {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["cueverge_csrf"]}
        installation = services.repository.get_runtime_installation().id
        yield services, client, headers, installation


def add_photo(client, headers, installation):
    result = client.post(
        f"/api/gui/v1/installations/{installation}/photos",
        content=image(),
        headers={
            **headers,
            "Content-Type": "image/png",
            "X-Photo-Description": "Original reference",
            "X-Original-Filename": "original.png",
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


def add_geometry(client, headers, installation, photo_id=None, kind="pixel_segment"):
    body = {
        "photo_id": photo_id,
        "kind": kind,
        "name": "Original mapped object",
        "points": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.1}],
        "closed": False,
        "start_index": 0,
        "pixel_count": 8,
    }
    if kind == "projector_surface":
        body.update(
            points=[
                {"x": 0.1, "y": 0.1},
                {"x": 0.8, "y": 0.1},
                {"x": 0.8, "y": 0.8},
                {"x": 0.1, "y": 0.8},
            ],
            closed=True,
            start_index=None,
            pixel_count=None,
        )
    result = client.post(
        f"/api/gui/v1/installations/{installation}/geometry", headers=headers, json=body
    )
    assert result.status_code == 201, result.text
    return result.json()


def test_geometry_without_photos_can_be_created_updated_and_calibrated(workspace):
    services, client, headers, installation = workspace
    geometry = add_geometry(client, headers, installation)
    assert geometry["photo_id"] is None
    result = client.put(
        f"/api/gui/v1/installation-geometry/{geometry['id']}",
        headers={**headers, "If-Match": '"1"'},
        json={
            **{
                key: value
                for key, value in geometry.items()
                if key
                not in {
                    "id",
                    "installation_id",
                    "landmark_ids",
                    "revision",
                    "created_at",
                    "updated_at",
                }
            },
            "name": "Updated without background",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["id"] == geometry["id"]
    surface = client.put(
        f"/api/gui/v1/installation-geometry/{geometry['id']}/lighting-surface",
        headers=headers,
        json={
            "resource": "roofline.demo",
            "local_width": 1,
            "local_height": 1,
            "local_points": [[0, 0], [1, 0]],
            "stage_matrix": [1, 0, 0, 1, 0, 0],
        },
    )
    assert surface.status_code == 200, surface.text
    assert len(surface.json()["pixels"]) == 8
    assert services.installation_photos.list(installation) == ()


def test_migration_preserves_all_columns_ids_and_dependent_calibration(workspace):
    services, client, headers, installation = workspace
    photo = add_photo(client, headers, installation)
    geometry = add_geometry(client, headers, installation, photo["id"])
    surface = client.put(
        f"/api/gui/v1/installation-geometry/{geometry['id']}/lighting-surface",
        headers=headers,
        json={
            "resource": "roofline.demo",
            "local_width": 1,
            "local_height": 1,
            "local_points": [[0, 0], [1, 0]],
            "stage_matrix": [1, 0, 0, 1, 0, 0],
        },
    )
    assert surface.status_code == 200, surface.text
    with sqlite3.connect(services.repository.database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        schema = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='installation_geometry'"
        ).fetchone()[0]
        schema = (
            schema.replace('"installation_geometry"', "legacy_geometry", 1)
            .replace("photo_id TEXT", "photo_id TEXT NOT NULL")
            .replace("ON DELETE SET NULL", "ON DELETE CASCADE")
        )
        connection.execute(schema)
        connection.execute("INSERT INTO legacy_geometry SELECT * FROM installation_geometry")
        connection.execute("DROP TABLE installation_geometry")
        connection.execute("ALTER TABLE legacy_geometry RENAME TO installation_geometry")
        before = connection.execute("SELECT * FROM installation_geometry").fetchall()
        calibration = connection.execute("SELECT * FROM lighting_surfaces").fetchall()
    services.repository.migrate()
    services.repository.migrate()
    with sqlite3.connect(services.repository.database_path) as connection:
        assert connection.execute("SELECT * FROM installation_geometry").fetchall() == before
        assert connection.execute("SELECT * FROM lighting_surfaces").fetchall() == calibration
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    deleted = client.delete(
        f"/api/gui/v1/installation-photos/{photo['id']}", headers={**headers, "If-Match": '"1"'}
    )
    assert deleted.status_code == 204, deleted.text
    remaining = client.get(f"/api/gui/v1/installations/{installation}/geometry").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == geometry["id"]
    assert remaining[0]["photo_id"] is None
    assert remaining[0]["semantic_id"] == geometry["semantic_id"]
    assert remaining[0]["landmark_ids"] == geometry["landmark_ids"]
    assert remaining[0]["points"] == geometry["points"]
    with sqlite3.connect(services.repository.database_path) as connection:
        assert connection.execute("SELECT * FROM lighting_surfaces").fetchall() == calibration
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        client.get(f"/api/gui/v1/installation-geometry/{geometry['id']}/lighting-surface").json()[
            "pixels"
        ]
        == surface.json()["pixels"]
    )


def test_photo_removal_keeps_projector_geometry_and_shared_stage_calibration(workspace):
    services, client, headers, installation = workspace
    photo = add_photo(client, headers, installation)
    geometry = add_geometry(client, headers, installation, photo["id"], kind="projector_surface")
    path = f"/api/gui/v1/installation-geometry/{geometry['id']}/projector-mapping"
    saved = client.put(
        path,
        headers=headers,
        json={
            "resource": "projector.demo",
            "native_width": 1280,
            "native_height": 720,
            "stage_x": 0,
            "stage_y": 0,
            "stage_width": 4,
            "stage_height": 3,
        },
    )
    assert saved.status_code == 200, saved.text
    before = saved.json()
    coverage = services.stage_coverage.analyze(installation)
    deleted = client.delete(
        f"/api/gui/v1/installation-photos/{photo['id']}", headers={**headers, "If-Match": '"1"'}
    )
    assert deleted.status_code == 204, deleted.text
    after = client.get(path).json()
    assert {key: value for key, value in after.items() if key != "photo_views"} == {
        key: value for key, value in before.items() if key != "photo_views"
    }
    assert after["photo_views"][0]["geometry_id"] == geometry["id"]
    assert after["photo_views"][0]["photo_id"] is None
    assert after["photo_views"][0]["homography"] == before["photo_views"][0]["homography"]
    assert services.stage_coverage.analyze(installation) == coverage


def test_photo_removal_preserves_separate_linked_view_coordinate_spaces(workspace):
    services, client, headers, installation = workspace
    photos = [add_photo(client, headers, installation), add_photo(client, headers, installation)]
    mask_id = None
    geometry_ids = []
    for index, photo in enumerate(photos):
        projector = add_geometry(
            client, headers, installation, photo["id"], kind="projector_surface"
        )
        mask_x = 0.2 + index * 0.3
        mask = client.post(
            f"/api/gui/v1/installations/{installation}/geometry",
            headers=headers,
            json={
                "photo_id": photo["id"],
                "kind": "mask",
                "name": "Linked obstruction",
                "semantic_id": mask_id,
                "closed": True,
                "points": [
                    {"x": mask_x, "y": 0.2},
                    {"x": mask_x + 0.1, "y": 0.2},
                    {"x": mask_x, "y": 0.4},
                ],
            },
        )
        assert mask.status_code == 201, mask.text
        mask_id = mask.json()["semantic_id"]
        mapping = client.put(
            f"/api/gui/v1/installation-geometry/{projector['id']}/projector-mapping",
            headers=headers,
            json={
                "resource": f"projector.{index}",
                "native_width": 1280,
                "native_height": 720,
                "stage_x": index * 5,
                "stage_y": 0,
                "stage_width": 4,
                "stage_height": 3,
                "mask_semantic_ids": [mask_id],
            },
        )
        assert mapping.status_code == 200, mapping.text
        geometry_ids.extend([projector["id"], mask.json()["id"]])
    before = services.stage_coverage.analyze(installation)
    assert len([region for region in before.regions if region.kind == "occluded"]) == 2
    for photo in photos:
        result = client.delete(
            f"/api/gui/v1/installation-photos/{photo['id']}", headers={**headers, "If-Match": '"1"'}
        )
        assert result.status_code == 204
    assert services.stage_coverage.analyze(installation) == before
    retained = client.get(f"/api/gui/v1/installations/{installation}/geometry").json()
    assert {item["id"] for item in retained} == set(geometry_ids)
    assert all(item["photo_id"] is None for item in retained)
    assert not any(
        item.code == "ambiguous_semantic_geometry"
        for item in services.mapper_export.validate(installation)
    )


def test_replace_retains_photo_metadata_geometry_and_old_content(workspace):
    services, client, headers, installation = workspace
    photo = add_photo(client, headers, installation)
    geometry = add_geometry(client, headers, installation, photo["id"])
    path = f"/api/gui/v1/installation-photos/{photo['id']}/content"
    replacement = image("blue", (8, 20))
    replacement_headers = {
        **headers,
        "If-Match": '"1"',
        "Content-Type": "image/png",
        "X-Original-Filename": "replacement.png",
        "X-Confirm-Alignment-Review": "true",
    }
    assert client.put(path, content=replacement).status_code == 403
    assert (
        client.put(
            path,
            content=replacement,
            headers={
                **headers,
                "If-Match": '"1"',
                "Content-Type": "image/png",
                "X-Original-Filename": "replacement.png",
            },
        ).status_code
        == 422
    )
    assert client.put(path, content=b"not an image", headers=replacement_headers).status_code == 422
    result = client.put(path, content=replacement, headers=replacement_headers)
    assert result.status_code == 200, result.text
    expected = {
        **photo,
        "original_filename": "replacement.png",
        "digest_sha256": hashlib.sha256(replacement).hexdigest(),
        "size_bytes": len(replacement),
        "revision": 2,
        "updated_at": result.json()["updated_at"],
    }
    assert result.json() == expected
    assert client.get(path).content == replacement
    assert client.get(f"/api/gui/v1/installations/{installation}/geometry").json() == [geometry]
    assert client.put(path, content=image(), headers=replacement_headers).status_code == 409
    original_path = (
        services.installation_photos.object_root
        / photo["digest_sha256"][:2]
        / photo["digest_sha256"]
    )
    assert original_path.read_bytes() == image()


def test_mcp_photo_replace_is_explicitly_scoped_and_preserves_installation_objects(workspace):
    services, client, headers, installation = workspace
    photo = add_photo(client, headers, installation)
    geometry = add_geometry(client, headers, installation, photo["id"])
    project = client.post(
        "/api/gui/v1/projects/from-active",
        headers=headers,
        json={"name": "Original photo replacement"},
    ).json()["project"]
    now = datetime.now(UTC).isoformat()
    content = image("green")
    asset, upload = services.asset_upload.begin(
        project_id=project["id"],
        name="Replacement",
        original_filename="replacement.png",
        media_type="image/png",
        total_bytes=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=now,
    )
    services.asset_upload.append(
        upload_id=upload.id,
        offset=0,
        chunk=content,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=now,
    )
    services.asset_upload.process_asset(asset.id, occurred_at=now)

    def token(scopes):
        return services.mcp_store.create_token(
            client_id="photo-test",
            username="admin",
            project_id=project["id"],
            scopes=scopes,
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            occurred_at=now,
        ).token

    def tool(secret, name, **arguments):
        result = client.post(
            "/mcp/",
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        assert result.status_code == 200, result.text
        return result.json()["result"]

    request = {
        "photo_id": photo["id"],
        "source_revision": 1,
        "asset_id": asset.id,
        "confirm_alignment_review": True,
    }
    old_grant = token((McpScope.READ, McpScope.INSTALLATION_EDIT))
    assert tool(old_grant, "replace_installation_photo", request=request)["isError"]
    granted = token((McpScope.READ, McpScope.PHOTO_REPLACE))
    assert tool(granted, "get_capabilities")["structuredContent"]["installation_setup"][
        "photo_optional"
    ]
    assert tool(
        granted,
        "replace_installation_photo",
        request={**request, "confirm_alignment_review": False},
    )["isError"]
    result = tool(granted, "replace_installation_photo", request=request)["structuredContent"]
    assert result["id"] == photo["id"]
    assert result["geometry_preserved"] and not result["hardware_commands_sent"]
    assert client.get(f"/api/gui/v1/installations/{installation}/geometry").json() == [geometry]
    assert tool(granted, "replace_installation_photo", request=request)["isError"]
