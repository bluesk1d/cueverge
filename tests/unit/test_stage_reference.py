import math
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cueverge.gui.frame_compositor import render_stage_to_projector
from cueverge.gui.stage_geometry import (
    apply_homography,
    area,
    fit_affine,
    homography_mesh,
    intersect_convex,
    oriented,
    subtract_convex,
    units_from_measurement,
)
from tests.unit.test_optional_photos import add_photo
from tests.unit.test_optional_photos import workspace as workspace

LEFT = [(0.1, 0.2), (0.5, 0.2), (0.5, 0.7), (0.1, 0.7)]
RIGHT = [(0.4, 0.25), (0.9, 0.2), (0.9, 0.75), (0.4, 0.7)]


def _geometry(client, headers, installation, photo_id, kind, points, **extra):
    body = {
        "photo_id": photo_id,
        "kind": kind,
        "name": extra.pop("name", kind),
        "points": [{"x": x, "y": y} for x, y in points],
        "closed": kind in {"projector_surface", "mask"},
        **extra,
    }
    result = client.post(
        f"/api/gui/v1/installations/{installation}/geometry", headers=headers, json=body
    )
    assert result.status_code == 201, result.text
    return result.json()


def _projector(client, headers, installation, photo_id, points, resource):
    geometry = _geometry(
        client, headers, installation, photo_id, "projector_surface", points, name=resource
    )
    mapped = client.put(
        f"/api/gui/v1/installation-geometry/{geometry['id']}/projector-mapping",
        headers=headers,
        json={
            "resource": resource,
            "native_width": 1280,
            "native_height": 720,
            "stage_x": 0,
            "stage_y": 0,
            "stage_width": 4,
            "stage_height": 2.25,
        },
    )
    assert mapped.status_code == 200, mapped.text
    return geometry


def _stage_installation(client, headers, installation):
    photo = add_photo(client, headers, installation)
    left = _projector(client, headers, installation, photo["id"], LEFT, "projector.left")
    right = _projector(client, headers, installation, photo["id"], RIGHT, "projector.right")
    fixture = _geometry(
        client, headers, installation, photo["id"], "fixture", [(0.95, 0.1)], name="Star"
    )
    strand = _geometry(
        client,
        headers,
        installation,
        photo["id"],
        "pixel_segment",
        [(0.05, 0.05), (0.95, 0.05)],
        name="Roofline",
        start_index=0,
        pixel_count=10,
    )
    surface = client.put(
        f"/api/gui/v1/installation-geometry/{strand['id']}/lighting-surface",
        headers=headers,
        json={
            "resource": "roofline.demo",
            "local_width": 1,
            "local_height": 1,
            "local_points": [[0.05, 0.05], [0.95, 0.05]],
            "stage_matrix": [1, 0, 0, 1, 0, 0],
        },
    )
    assert surface.status_code == 200, surface.text
    return photo, left, right, fixture, strand


def _reference_path(installation):
    return f"/api/gui/v1/installations/{installation}/stage-reference"


def test_stage_reference_derives_placement_from_photo_view(workspace):
    services, client, headers, installation = workspace
    photo, left, _right, fixture, strand = _stage_installation(client, headers, installation)
    initial = client.get(_reference_path(installation))
    assert initial.status_code == 200
    assert initial.json()["reference"] is None
    # Drawn surfaces place projectors at the default scale before a stage view exists.
    initial_overlap = initial.json()["projector_overlaps"][0]["fraction"]
    assert 0 < initial_overlap < 0.5
    left_mapping = client.get(
        f"/api/gui/v1/installation-geometry/{left['id']}/projector-mapping"
    ).json()
    assert left_mapping["stage_placement"] == "surface"
    assert left_mapping["stage_placement_basis"] == "default_scale"
    assert left_mapping["stage_x"] == pytest.approx(1)
    assert left_mapping["stage_width"] == pytest.approx(4)
    assert left_mapping["stage_height"] == pytest.approx(3)

    saved = client.put(
        _reference_path(installation),
        headers=headers,
        json={"photo_id": photo["id"], "unit_label": "ft", "stage_width": 50},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["reference"]["stage_width"] == pytest.approx(50)
    assert body["reference"]["stage_height"] == pytest.approx(30)
    assert body["needs_update"] is True
    assert body["counts"]["needs_update"] == 4
    assert saved.headers["ETag"] == '"1"'

    stale = client.post(
        f"{_reference_path(installation)}/apply", headers={**headers, "If-Match": '"2"'}
    )
    assert stale.status_code == 409
    applied = client.post(
        f"{_reference_path(installation)}/apply", headers={**headers, "If-Match": '"1"'}
    )
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["needs_update"] is False
    assert result["counts"]["placed"] == 4
    assert result["reference"]["applied_at"] is not None
    overlap = result["projector_overlaps"][0]
    assert set(overlap["resources"]) == {"projector.left", "projector.right"}
    assert 0 < overlap["fraction"] < 0.5
    assert overlap["fraction"] == pytest.approx(initial_overlap, abs=1e-3)

    geometry = {
        item["id"]: item
        for item in client.get(f"/api/gui/v1/installations/{installation}/geometry").json()
    }
    assert geometry[fixture["id"]]["stage_points"] == [pytest.approx([47.5, 3.0])]
    assert geometry[fixture["id"]]["revision"] == fixture["revision"] + 1

    mapping = client.get(f"/api/gui/v1/installation-geometry/{left['id']}/projector-mapping").json()
    homography = mapping["stage_homography"]
    assert homography is not None
    for (u, v), (x, y) in zip(((0, 0), (1, 0), (1, 1), (0, 1)), LEFT, strict=True):
        assert apply_homography(homography, u, v) == pytest.approx((x * 50, y * 30))
    assert mapping["stage_x"] == pytest.approx(5)
    assert mapping["stage_width"] == pytest.approx(20)

    pixels = client.get(
        f"/api/gui/v1/installation-geometry/{strand['id']}/lighting-surface"
    ).json()["pixels"]
    assert pixels[0]["stage_x"] == pytest.approx(2.5)
    assert pixels[-1]["stage_x"] == pytest.approx(47.5)
    assert all(pixel["stage_y"] == pytest.approx(1.5) for pixel in pixels)

    export = services.mapper_export.export(installation)
    assert export["stage"]["width"] == pytest.approx(50)
    assert export["stage"]["unit"] == "ft"
    projector = next(item for item in export["projectors"] if item["resource"] == "projector.left")
    assert len(projector["mesh_vertices"]) == 81
    assert len(projector["mesh_triangles"]) == 128

    coverage = services.stage_coverage.analyze(installation)
    kinds = {region.kind for region in coverage.regions}
    assert {"covered", "overlap"} <= kinds
    overlap_area = sum(
        area(region.points) for region in coverage.regions if region.kind == "overlap"
    )
    covered_area = sum(
        area(region.points) for region in coverage.regions if region.kind == "covered"
    )
    assert overlap_area == pytest.approx(
        area(intersect_convex(oriented(_scaled(LEFT)), oriented(_scaled(RIGHT)))), rel=1e-6
    )
    assert covered_area + overlap_area == pytest.approx(
        area(_scaled(LEFT)) + area(_scaled(RIGHT)) - overlap_area, rel=1e-6
    )


def _scaled(points):
    return tuple((x * 50, y * 30) for x, y in points)


def test_manual_extent_override_and_photo_removal_keep_explicit_stage_data(workspace):
    _, client, headers, installation = workspace
    photo, left, _, fixture, _ = _stage_installation(client, headers, installation)
    client.put(
        _reference_path(installation),
        headers=headers,
        json={"photo_id": photo["id"], "unit_label": "m", "stage_width": 10},
    ).raise_for_status()
    client.post(
        f"{_reference_path(installation)}/apply", headers={**headers, "If-Match": '"1"'}
    ).raise_for_status()

    path = f"/api/gui/v1/installation-geometry/{left['id']}/projector-mapping"
    mapping = client.get(path).json()
    unchanged = client.put(
        path,
        headers={**headers, "If-Match": f'"{mapping["revision"]}"'},
        json={
            key: mapping[key]
            for key in (
                "resource",
                "native_width",
                "native_height",
                "stage_x",
                "stage_y",
                "stage_width",
                "stage_height",
            )
        },
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["stage_homography"] == mapping["stage_homography"]

    deleted = client.delete(
        f"/api/gui/v1/installation-photos/{photo['id']}", headers={**headers, "If-Match": '"1"'}
    )
    assert deleted.status_code == 204, deleted.text
    status = client.get(_reference_path(installation)).json()
    assert status["reference"]["photo_id"] is None
    retained = {
        item["id"]: item
        for item in client.get(f"/api/gui/v1/installations/{installation}/geometry").json()
    }
    assert retained[fixture["id"]]["stage_points"] == [pytest.approx([9.5, 0.6])]
    assert client.get(path).json()["stage_homography"] == mapping["stage_homography"]

    manual = client.put(
        path,
        headers={**headers, "If-Match": f'"{unchanged.json()["revision"]}"'},
        json={
            **{
                key: unchanged.json()[key]
                for key in ("resource", "native_width", "native_height", "stage_y", "stage_height")
            },
            "stage_placement": "manual",
            "stage_x": 0,
            "stage_width": 3,
        },
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["stage_homography"] is None
    assert manual.json()["stage_placement"] == "manual"
    assert manual.json()["stage_x"] == 0
    assert manual.json()["stage_width"] == 3
    assert "manually" in manual.json()["stage_placement_message"]
    assert client.get(path).json()["stage_placement"] == "manual"

    # Without the stage view photo, surface placement falls back to saved stage points.
    surface = client.put(
        path,
        headers={**headers, "If-Match": f'"{manual.json()["revision"]}"'},
        json={
            key: unchanged.json()[key]
            for key in ("resource", "native_width", "native_height", "stage_placement")
        },
    )
    assert surface.status_code == 200, surface.text
    assert surface.json()["stage_placement_basis"] == "stage_points"
    assert surface.json()["stage_homography"] == pytest.approx(mapping["stage_homography"])
    assert surface.json()["stage_x"] == pytest.approx(mapping["stage_x"])


def test_surface_placement_follows_redrawn_surface_and_manual_is_kept(workspace):
    _, client, headers, installation = workspace
    photo, left, right, _, _ = _stage_installation(client, headers, installation)
    client.put(
        _reference_path(installation),
        headers=headers,
        json={"photo_id": photo["id"], "unit_label": "ft", "stage_width": 50},
    ).raise_for_status()
    client.post(
        f"{_reference_path(installation)}/apply", headers={**headers, "If-Match": '"1"'}
    ).raise_for_status()
    left_path = f"/api/gui/v1/installation-geometry/{left['id']}/projector-mapping"
    right_path = f"/api/gui/v1/installation-geometry/{right['id']}/projector-mapping"
    before = client.get(left_path).json()
    assert before["stage_placement_basis"] == "stage_view"

    geometry = {
        item["id"]: item
        for item in client.get(f"/api/gui/v1/installations/{installation}/geometry").json()
    }[left["id"]]
    moved = [(x + 0.05, y) for x, y in LEFT]
    redrawn = client.put(
        f"/api/gui/v1/installation-geometry/{left['id']}",
        headers={**headers, "If-Match": f'"{geometry["revision"]}"'},
        json={
            "photo_id": photo["id"],
            "kind": "projector_surface",
            "name": geometry["name"],
            "points": [{"x": x, "y": y} for x, y in moved],
            "closed": True,
            "stage_points": geometry["stage_points"],
        },
    )
    assert redrawn.status_code == 200, redrawn.text
    after = client.get(left_path).json()
    assert after["revision"] == before["revision"] + 1
    assert after["stage_x"] == pytest.approx(moved[0][0] * 50)
    for (u, v), (x, y) in zip(((0, 0), (1, 0), (1, 1), (0, 1)), moved, strict=True):
        assert apply_homography(after["stage_homography"], u, v) == pytest.approx((x * 50, y * 30))

    right_mapping = client.get(right_path).json()
    manual = client.put(
        right_path,
        headers={**headers, "If-Match": f'"{right_mapping["revision"]}"'},
        json={
            "resource": "projector.right",
            "native_width": 1280,
            "native_height": 720,
            "stage_placement": "manual",
        },
    )
    assert manual.status_code == 200, manual.text
    assert manual.json()["stage_x"] == 0
    assert "default 4 x 2.25" in manual.json()["stage_placement_message"]
    status = client.put(
        _reference_path(installation),
        headers={**headers, "If-Match": '"1"'},
        json={"photo_id": photo["id"], "unit_label": "ft", "stage_width": 60},
    ).json()
    item = next(entry for entry in status["items"] if entry["resource"] == "projector.right")
    assert "manual" in item["message"]
    client.post(
        f"{_reference_path(installation)}/apply", headers={**headers, "If-Match": '"2"'}
    ).raise_for_status()
    assert client.get(right_path).json()["stage_x"] == 0
    assert client.get(right_path).json()["stage_homography"] is None
    assert client.get(left_path).json()["stage_x"] == pytest.approx(moved[0][0] * 60)


def test_stage_placement_migration_keeps_entered_extents_and_places_defaults(workspace):
    import sqlite3

    services, client, headers, installation = workspace
    photo = add_photo(client, headers, installation)
    left = _projector(client, headers, installation, photo["id"], LEFT, "projector.left")
    right = _projector(client, headers, installation, photo["id"], RIGHT, "projector.right")
    database = services.repository.database_path
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE projector_surface_mappings SET stage_x = 0, stage_y = 0, "
            "stage_width = 4, stage_height = 2.25, stage_homography_json = NULL "
            "WHERE geometry_id = ?",
            (left["id"],),
        )
        connection.execute(
            "UPDATE projector_surface_mappings SET stage_x = 7, stage_y = 1, "
            "stage_width = 3, stage_height = 2, stage_homography_json = NULL "
            "WHERE geometry_id = ?",
            (right["id"],),
        )
        connection.execute("ALTER TABLE projector_surface_mappings DROP COLUMN stage_placement")
        connection.execute("DELETE FROM schema_migrations WHERE version = 43")
    services.repository.migrate()

    path = "/api/gui/v1/installation-geometry/{}/projector-mapping"
    placed = client.get(path.format(left["id"])).json()
    assert placed["stage_placement"] == "surface"
    assert placed["stage_x"] == pytest.approx(1)
    assert placed["stage_homography"] is not None
    kept = client.get(path.format(right["id"])).json()
    assert kept["stage_placement"] == "manual"
    assert (kept["stage_x"], kept["stage_width"]) == (7, 3)
    assert kept["stage_homography"] is None


def test_default_scale_placement_waits_for_stage_view_when_other_stage_data_exists(workspace):
    _, client, headers, installation = workspace
    photo = add_photo(client, headers, installation)
    _geometry(
        client,
        headers,
        installation,
        photo["id"],
        "fixture",
        [(0.5, 0.5)],
        name="Hand placed star",
        stage_points=[[0.5, 0.5]],
    )
    geometry = _geometry(
        client, headers, installation, photo["id"], "projector_surface", LEFT, name="left"
    )
    mapped = client.put(
        f"/api/gui/v1/installation-geometry/{geometry['id']}/projector-mapping",
        headers=headers,
        json={
            "resource": "projector.left",
            "native_width": 1280,
            "native_height": 720,
            "stage_x": 0.1,
            "stage_y": 0.2,
            "stage_width": 0.4,
            "stage_height": 0.5,
        },
    )
    assert mapped.status_code == 200, mapped.text
    body = mapped.json()
    assert body["stage_placement"] == "surface"
    assert body["stage_placement_basis"] is None
    assert body["stage_homography"] is None
    assert (body["stage_x"], body["stage_width"]) == (0.1, 0.4)
    assert "Choose a stage view" in body["stage_placement_message"]


def test_stage_reference_validation_and_measured_scale(workspace):
    _, client, headers, installation = workspace
    photo = add_photo(client, headers, installation)
    missing = client.put(
        _reference_path(installation),
        headers=headers,
        json={"photo_id": "missing", "stage_width": 10},
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "stage_reference_invalid"
    both = client.put(
        _reference_path(installation),
        headers=headers,
        json={
            "photo_id": photo["id"],
            "stage_width": 10,
            "scale_points": [{"x": 0.1, "y": 0.5}, {"x": 0.3, "y": 0.5}],
            "scale_distance": 16,
        },
    )
    assert both.status_code == 422
    measured = client.put(
        _reference_path(installation),
        headers=headers,
        json={
            "photo_id": photo["id"],
            "unit_label": "ft",
            "scale_points": [{"x": 0.1, "y": 0.5}, {"x": 0.3, "y": 0.5}],
            "scale_distance": 16,
        },
    )
    assert measured.status_code == 200, measured.text
    assert measured.json()["reference"]["stage_width"] == pytest.approx(80)
    conflict = client.put(
        _reference_path(installation),
        headers={**headers, "If-Match": '"5"'},
        json={"photo_id": photo["id"], "stage_width": 10},
    )
    assert conflict.status_code == 409
    unauthenticated = TestClient(client.app, base_url="http://localhost")
    denied = unauthenticated.put(
        _reference_path(installation),
        headers={"Origin": headers["Origin"]},
        json={"photo_id": photo["id"], "stage_width": 10},
    )
    assert denied.status_code in {401, 403}


def test_stage_geometry_helpers_are_exact():
    square = oriented(((0, 0), (2, 0), (2, 2), (0, 2)))
    shifted = oriented(((1, 1), (3, 1), (3, 3), (1, 3)))
    assert area(intersect_convex(square, shifted)) == pytest.approx(1)
    assert sum(area(piece) for piece in subtract_convex(square, shifted)) == pytest.approx(3)
    affine = fit_affine(((0, 0), (1, 0), (0, 1)), ((5, 5), (7, 5), (5, 8)))
    assert affine == pytest.approx((2, 0, 0, 3, 5, 5))
    similarity = fit_affine(((0, 0), (1, 0)), ((1, 1), (1, 3)))
    assert similarity[0] * 1 + similarity[4] == pytest.approx(1)
    assert similarity[1] * 1 + similarity[5] == pytest.approx(3)
    assert units_from_measurement((0, 0), (0.5, 0), 10) == pytest.approx(0.02)
    with pytest.raises(ValueError):
        units_from_measurement((0, 0), (0.001, 0), 10)


def test_perspective_mesh_renders_stage_content_into_projector_corners(tmp_path: Path):
    corners = ((10.0, 10.0), (90.0, 20.0), (80.0, 90.0), (20.0, 80.0))
    from cueverge.gui.projector_mapping import _homography
    from cueverge.gui.stage_geometry import stage_homography

    photo = _homography(64, 36, tuple((x / 100, y / 100) for x, y in corners), 0, False, False)
    matrix = stage_homography(photo, 64, 36, 0.1)
    vertices, triangles = homography_mesh(matrix)
    stage = np.zeros((60, 100, 4), dtype=np.float32)
    stage[..., 3] = 1
    stage[:, :50, 0] = 1
    stage[:, 50:, 2] = 1
    output = render_stage_to_projector(
        stage,
        stage_bounds=(0, 0, 100, 60),
        projector_width=64,
        projector_height=36,
        mesh_vertices=vertices,
        mesh_triangles=triangles,
    )
    left_x, _ = apply_homography(matrix, 0.05, 0.5)
    right_x, _ = apply_homography(matrix, 0.95, 0.5)
    assert left_x < 50 < right_x
    assert output[18, 3, 0] > 0.9 and output[18, 3, 2] < 0.1
    assert output[18, 60, 2] > 0.9 and output[18, 60, 0] < 0.1
    assert math.isfinite(float(output.sum()))


def test_mcp_stage_map_requires_installation_edit_and_derives_placement(tmp_path: Path):
    from datetime import UTC, datetime, timedelta

    from cueverge.controller.app import create_app
    from cueverge.gui.contracts import DraftProject, ProjectorMappingRequest, Role
    from cueverge.gui.mcp_authoring import McpScope
    from tests.unit.test_installation_usability import image, setup

    runtime, services = setup(tmp_path)
    installation = services.repository.get_runtime_installation().id
    now = datetime.now(UTC).isoformat()
    photo = services.installation_photos.create(
        installation_id=installation,
        name="House front",
        viewpoint="Street",
        original_filename="house.png",
        media_type="image/png",
        content=image(),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=now,
    )
    services.installation_geometry.create(
        installation_id=installation,
        photo_id=photo.id,
        kind="fixture",
        name="Porch star",
        points=((0.5, 0.5),),
        closed=False,
        semantic_id=None,
        color="#ffffff",
        label_visible=True,
        locked=False,
        hidden=False,
        z_index=0,
        direction="forward",
        start_index=None,
        pixel_count=None,
        stage_points=None,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=now,
    )
    surface = services.installation_geometry.create(
        installation_id=installation,
        photo_id=photo.id,
        kind="projector_surface",
        name="Garage door",
        points=tuple(LEFT),
        closed=True,
        semantic_id=None,
        color="#ffffff",
        label_visible=True,
        locked=False,
        hidden=False,
        z_index=0,
        direction="forward",
        start_index=None,
        pixel_count=None,
        stage_points=None,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=now,
    )
    services.projector_mapping.upsert(
        geometry_id=surface.id,
        expected_revision=None,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=now,
        **ProjectorMappingRequest.model_validate(
            {"resource": "projector.left", "native_width": 1280, "native_height": 720}
        ).model_dump(),
    )
    draft = DraftProject.model_validate_json(
        Path("tests/fixtures/usability-authoring.json").read_text()
    )
    draft = draft.model_copy(
        update={"editor_metadata": {**draft.editor_metadata, "installation_id": installation}}
    )
    project = services.repository.create_project(
        name="Stage map fixture",
        draft=draft,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=now,
    )

    def token(scopes):
        return services.mcp_store.create_token(
            client_id="stage-fixture",
            username="admin",
            project_id=project.project.id,
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

    request = {"photo_id": photo.id, "unit_label": "ft", "stage_width": 40}
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        reader = token((McpScope.READ, McpScope.DIRECT_EDIT))
        capabilities = tool(client, reader, "get_capabilities")["structuredContent"]
        stage = capabilities["installation_setup"]["stage_reference"]
        assert stage["update_tool"] == "update_stage_reference"
        assert stage["update_scope"] == "installation_edit"
        assert capabilities["features"]["stage_reference"] is True
        placement = capabilities["installation_setup"]["projector_stage_placement"]
        assert placement["default"] == "surface"
        assert placement["modes"] == ["surface", "manual"]
        assert placement["write_surface"] == "administrator_gui_only"
        initial = tool(client, reader, "get_stage_map")["structuredContent"]
        assert initial["reference"] is None
        assert initial["counts"]["unplaced"] == 1
        assert initial["counts"]["manual"] == 1
        assert tool(client, reader, "update_stage_reference", request=request)["isError"]
        assert services.stage_reference.get(installation) is None

        editor = token((McpScope.READ, McpScope.INSTALLATION_EDIT))
        invalid = tool(
            client,
            editor,
            "update_stage_reference",
            request={**request, "scale_distance": 3},
        )
        assert invalid["isError"]
        result = tool(client, editor, "update_stage_reference", request=request)[
            "structuredContent"
        ]
        assert result["applied"] is True
        assert result["hardware_commands_sent"] is False
        assert result["counts"]["placed"] == 2
        assert result["items"][0]["stage_points"] == [[20.0, 12.0]]
        layout = tool(client, reader, "get_spatial_layout")
        print(layout)
        layout = layout["structuredContent"]
        projector = next(
            item for item in layout["projectors"] if item["resource"] == "projector.left"
        )
        assert projector["stage_placement"] == "surface"
        assert projector["stage_extent"] == pytest.approx([4.0, 4.8, 16.0, 12.0])
        stale = tool(client, editor, "update_stage_reference", request=request)
        assert stale["isError"]
        assert services.repository.get_revision(project.project.id).revision == 1


def _solid(width, height, rgb):
    image = np.zeros((height, width, 4), dtype=np.float32)
    image[..., :3] = rgb
    image[..., 3] = 1
    return image


def test_corner_pin_and_feathered_mask_shape_stage_layers():
    from cueverge.gui.frame_compositor import (
        LayerMask,
        PreparedVisualLayer,
        compose_visual_frame,
        layer_mask_alpha,
        render_corner_pinned_layer,
        scale_layer_geometry,
    )

    corners = ((10.0, 10.0), (50.0, 5.0), (55.0, 35.0), (5.0, 30.0))
    pinned = render_corner_pinned_layer(
        _solid(8, 8, (1, 0, 0)),
        output_width=60,
        output_height=40,
        corners=corners,
        crop=(0, 0, 0, 0),
        opacity=0.5,
    )
    assert pinned[20, 30, 0] == pytest.approx(0.5)
    assert pinned[2, 2].sum() == 0
    assert pinned[38, 58].sum() == 0

    alpha = layer_mask_alpha(((0, 0), (40, 0), (40, 40), (0, 40)), 10.0, False, 40, 40)
    assert alpha[20, 20] == pytest.approx(1)
    assert 0 < alpha[20, 2] < 0.5
    inverted = layer_mask_alpha(((0, 0), (20, 0), (20, 40), (0, 40)), 0.0, True, 40, 40)
    assert inverted[10, 5] == 0 and inverted[10, 30] == 1

    layer = PreparedVisualLayer(
        id="flag",
        source=_solid(8, 8, (0, 0, 1)),
        source_kind="image",
        start_ms=0,
        duration_ms=1000,
        in_point_ms=0,
        z_index=0,
        fade_in_ms=0,
        fade_out_ms=0,
        keyframes=(
            {
                "time_ms": 0,
                "position": [0, 0],
                "scale": [1, 1],
                "rotation_deg": 0,
                "anchor": [0, 0],
                "opacity": 1,
                "crop": [0, 0, 0, 0],
                "interpolation": {"kind": "linear"},
            },
        ),
        corner_pin=((0, 0), (6, 0), (6, 4), (0, 4)),
        mask=LayerMask(points=((0, 0), (3, 0), (3, 4), (0, 4))),
    )
    scaled = scale_layer_geometry(layer, 10, 10)
    assert scaled.corner_pin == ((0, 0), (60, 0), (60, 40), (0, 40))
    frame, _ = compose_visual_frame(
        (scaled,), timestamp_ms=0, output_width=60, output_height=40, background_rgb=(0, 0, 0)
    )
    assert frame[20, 10, 2] > 0.9
    assert frame[20, 50, 2] == pytest.approx(0)


def test_layer_geometry_and_projector_selection_validation():
    from cueverge.gui.contracts import _validate_layer_geometry
    from cueverge.gui.projector_renderer import ProjectorRenderError
    from cueverge.gui.stage_renderer import selected_stage_projectors

    _validate_layer_geometry(
        {
            "corner_pin": [[0, 0], [4, 0], [4, 3], [0, 3]],
            "mask": {"points": [[0, 0], [1, 0], [1, 1]], "feather": 0.2, "invert": False},
        },
        "video",
    )
    for layer, kind in (
        ({"corner_pin": [[0, 0], [4, 0], [0, 3], [4, 3]]}, "image"),
        ({"corner_pin": [[0, 0], [4, 0], [4, 3]]}, "image"),
        ({"mask": {"points": [[0, 0], [1, 0]]}}, "image"),
        ({"mask": {"points": [[0, 0], [1, 0], [1, 1]], "feather": -1}}, "image"),
        ({"mask": {"points": [[0, 0], [1, 0], [1, 1]], "blur": 1}}, "image"),
        ({"mask": {"points": [[0, 0], [1, 0], [1, 1]]}}, "audio"),
    ):
        with pytest.raises(ValueError):
            _validate_layer_geometry(layer, kind)

    mapper = {"projectors": [{"resource": "projector.left"}, {"resource": "projector.right"}]}
    assert len(selected_stage_projectors({}, mapper)) == 2
    assert selected_stage_projectors(
        {"stage_projector_resources": ["projector.right"]}, mapper
    ) == ({"resource": "projector.right"},)
    with pytest.raises(ProjectorRenderError):
        selected_stage_projectors({"stage_projector_resources": ["missing"]}, mapper)
    assert selected_stage_projectors({"stage_projector_resources": []}, mapper) == ()
    with pytest.raises(ProjectorRenderError):
        selected_stage_projectors({"stage_projector_resources": ["projector.left"] * 2}, mapper)
