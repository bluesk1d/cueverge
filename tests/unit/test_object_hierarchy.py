from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cueverge.controller.app import create_app
from cueverge.core.loader import _resolve_target
from cueverge.core.models import (
    LightingTargetDefinition,
    PixelSegment,
    ResourceBinding,
    SegmentReference,
)
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.mcp_authoring import McpScope
from cueverge.gui.object_hierarchy import (
    InheritanceError,
    InstallationObjectsDocument,
    ResourceObjects,
    adopt_draft,
    coarsest_partition,
    scene_objects,
)
from tests.unit.test_gui_foundation import (
    _draft,
    _login,
    _mcp_fixture,
    _mcp_token,
    _mcp_tool,
    _mutation_headers,
)


def test_members_select_disjoint_pixel_ranges_inside_a_segment() -> None:
    def binding(*members: dict[str, Any]) -> ResourceBinding:
        return ResourceBinding.model_validate(
            {
                "resource": "roof",
                "kind": "pixel_output",
                "target": "memory",
                "pixel_count": 8,
                "segments": [
                    {"id": "garage", "start": 0, "end": 3},
                    {"id": "eave", "start": 3, "end": 8},
                ],
                "fixtures": [{"id": "phase", "segments": list(members)}],
            }
        )

    phase = binding(
        {"segment": "eave", "start": 0, "end": 1},
        {"segment": "eave", "start": 3, "end": 5, "reverse": True},
    )
    target = LightingTargetDefinition(kind="fixture", id="phase")
    assert _resolve_target(phase, target, "scene")[0] == (3, 7, 6)
    with pytest.raises(ValueError, match="repeats"):
        binding(
            {"segment": "eave", "start": 0, "end": 3}, {"segment": "eave", "start": 2, "end": 4}
        )
    with pytest.raises(ValueError, match="exceeds"):
        binding({"segment": "garage", "start": 1, "end": 4})
    assert SegmentReference(segment="eave").model_dump() == {"segment": "eave", "reverse": False}


def test_installation_wiring_is_the_coarsest_partition_every_show_refines() -> None:
    whole = (PixelSegment(id="garage", start=0, end=3), PixelSegment(id="eave", start=3, end=8))
    split = (
        PixelSegment(id="garage", start=0, end=3),
        *(PixelSegment(id=f"px-{index}", start=3 + index, end=4 + index) for index in range(5)),
    )
    assert coarsest_partition((split, whole), 8) == whole
    assert [s.id for s in coarsest_partition((split,), 8)] == [
        "garage",
        "px-0",
        "px-1",
        "px-2",
        "px-3",
        "px-4",
    ]


def test_adoption_keeps_every_effect_on_the_same_pixels() -> None:
    store = InstallationObjectsDocument(
        resources=(
            ResourceObjects(
                resource="roofline.demo",
                pixel_count=8,
                segments=(
                    PixelSegment(id="garage", start=0, end=3),
                    PixelSegment(id="eave", start=3, end=8),
                ),
            ),
        )
    )
    document = _draft(editor_metadata={"installation_id": "home"})
    roof = document["installation"]["resources"][1]
    roof["segments"] = [
        {"id": "garage", "start": 0, "end": 3},
        {"id": "px-0", "start": 3, "end": 4},
        {"id": "px-1", "start": 4, "end": 5},
        {"id": "rest", "start": 5, "end": 8},
    ]
    roof["fixtures"] = [
        {"id": "phase", "segments": [{"segment": "rest", "reverse": True}, {"segment": "px-0"}]}
    ]
    document["scenes"][0]["lighting"] = {
        "resource": "roofline.demo",
        "effect": "segment_pulse",
        "target": {"kind": "segment", "id": "px-1"},
        "pulse_times_ms": [0],
    }
    legacy = DraftProject.model_validate(document)

    def indices(draft: DraftProject, kind: str, object_id: str) -> tuple[int, ...]:
        binding = next(b for b in draft.installation.resources if b.resource == "roofline.demo")
        target = LightingTargetDefinition(kind=kind, id=object_id)  # type: ignore[arg-type]
        return _resolve_target(binding, target, "ambient")[0]

    adopted = adopt_draft(legacy, store)
    binding = adopted.installation.resources[1]
    assert [segment.id for segment in binding.segments] == ["garage", "eave"]
    assert (
        indices(adopted, "fixture", "phase") == indices(legacy, "fixture", "phase") == (7, 6, 5, 3)
    )
    # A target on a show-only segment becomes a show fixture with the same ID and pixels.
    assert indices(adopted, "fixture", "px-1") == indices(legacy, "segment", "px-1") == (4,)
    lighting = adopted.scenes[0].model_dump(mode="json")["lighting"]
    assert lighting["target"] == {"kind": "fixture", "id": "px-1"}
    assert [item.key for item in scene_objects(adopted, "ambient")] == [
        ("roofline.demo", "fixture", "px-1")
    ]
    mirror = adopted.editor_metadata["inherited_objects"]
    assert {item["id"] for item in mirror} == {"garage", "eave"}  # type: ignore[union-attr,index]
    with pytest.raises(InheritanceError):
        adopt_draft(
            legacy,
            store.model_copy(
                update={"resources": (store.resources[0].model_copy(update={"pixel_count": 9}),)}
            ),
        )


def test_objects_stay_in_their_scope_across_gui_and_mcp(tmp_path: Path) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    installation = services.repository.get_runtime_installation().id
    legacy = services.repository.create_project(
        name="Legacy",
        draft=DraftProject.model_validate(
            _draft(editor_metadata={"installation_id": installation})
        ),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        _login(client)
        base = f"/api/gui/v1/installations/{installation}/objects"
        seeded = client.get(base).json()
        wiring = client.put(
            f"{base}/segments",
            json={
                "resource": "roofline.demo",
                "segments": [
                    {"id": "left", "start": 0, "end": 3},
                    {"id": "right", "start": 3, "end": 8},
                ],
                "names": {"left": "Garage"},
            },
            headers=_mutation_headers(client, if_match=f'"{seeded["revision"]}"'),
        )
        assert wiring.status_code == 200, wiring.text
        revision = wiring.json()["revision"]

        def new_show(name: str) -> dict[str, Any]:
            created = client.post(
                "/api/gui/v1/projects", json={"name": name}, headers=_mutation_headers(client)
            )
            assert created.status_code == 201, created.text
            project = created.json()
            scene = client.post(
                f"/api/gui/v1/projects/{project['project']['id']}/scenes",
                json={
                    "scene_id": "main",
                    "name": "Main",
                    "duration_ms": 3000,
                    "output": {"output_id": "roof", "resource": "roofline.demo"},
                },
                headers=_mutation_headers(client, if_match=f'"{project["revision"]}"'),
            )
            assert scene.status_code == 200, scene.text
            return scene.json()

        halloween, holidays = new_show("Halloween"), new_show("Holidays")
        a, b = halloween["project"]["id"], holidays["project"]["id"]
        roof = next(
            r
            for r in halloween["draft"]["installation"]["resources"]
            if r["resource"] == "roofline.demo"
        )
        assert [s["id"] for s in roof["segments"]] == ["left", "right"] and roof["fixtures"] == []

        # A show fixture created from the scene picker stays in that show and scene.
        mouth = {
            "resource": "roofline.demo",
            "kind": "fixture",
            "id": "klown-mouth",
            "name": "Klown mouth",
            "segments": [{"segment": "right", "start": 1, "end": 3}],
            "scene_id": "main",
        }
        created = client.post(
            f"/api/gui/v1/projects/{a}/objects/create",
            json=mouth,
            headers=_mutation_headers(client, if_match=f'"{halloween["revision"]}"'),
        )
        assert created.status_code == 200, created.text
        a_revision = created.json()["revision"]
        scene_view = client.get(f"/api/gui/v1/projects/{a}/scenes/main/objects").json()
        assert [o["id"] for o in scene_view["objects"]] == ["klown-mouth"]
        holidays_ids = {
            o["id"] for o in client.get(f"/api/gui/v1/projects/{b}/objects").json()["objects"]
        }
        assert holidays_ids == {"left", "right"}
        assert (
            client.get(f"/api/gui/v1/projects/{b}/scenes/main/objects").json()["available"][0][
                "owner"
            ]
            == "installation"
        )

        # Installation objects are inherited by every adopting show, read-only there.
        porch = {
            "resource": "roofline.demo",
            "kind": "fixture",
            "id": "porch",
            "name": "Porch",
            "segments": [{"segment": "left"}],
        }
        clash = client.post(
            f"{base}/create",
            json={**mouth, "scene_id": None},
            headers=_mutation_headers(client, if_match=f'"{revision}"'),
        )
        assert clash.status_code == 422 and "promote" in clash.text
        added = client.post(
            f"{base}/create",
            json=porch,
            headers=_mutation_headers(client, if_match=f'"{revision}"'),
        )
        assert added.status_code == 200, added.text
        revision = added.json()["revision"]
        a_objects = {
            o["id"]: o for o in client.get(f"/api/gui/v1/projects/{a}/objects").json()["objects"]
        }
        assert (
            a_objects["porch"]["owner"] == "installation"
            and a_objects["klown-mouth"]["owner"] == "show"
        )
        a_revision = client.get(f"/api/gui/v1/projects/{a}/draft").json()["revision"]
        refused = client.post(
            f"/api/gui/v1/projects/{a}/objects/update",
            json=porch,
            headers=_mutation_headers(client, if_match=f'"{a_revision}"'),
        )
        assert refused.status_code == 422 and "installation" in refused.text
        legacy_roof = services.repository.get_revision(
            legacy.project.id
        ).draft.installation.resources[1]
        assert legacy_roof.fixtures == () and legacy_roof.segments == ()

        # Scenes add installation objects without changing the installation.
        listed = client.put(
            f"/api/gui/v1/projects/{a}/scenes/main/objects",
            json={
                "objects": [
                    {"resource": "roofline.demo", "kind": "fixture", "id": "klown-mouth"},
                    {"resource": "roofline.demo", "kind": "fixture", "id": "porch"},
                ]
            },
            headers=_mutation_headers(client, if_match=f'"{a_revision}"'),
        )
        assert listed.status_code == 200, listed.text
        assert client.get(base).json()["revision"] == revision
        draft = listed.json()["draft"]
        draft["scenes"][0]["lighting"][0]["timeline"][0]["target"] = {
            "kind": "fixture",
            "id": "porch",
        }
        saved = client.put(
            f"/api/gui/v1/projects/{a}/draft",
            json=draft,
            headers=_mutation_headers(client, if_match=f'"{listed.json()["revision"]}"'),
        )
        assert saved.status_code == 200, saved.text
        blocked = client.put(
            f"/api/gui/v1/projects/{a}/scenes/main/objects",
            json={"objects": []},
            headers=_mutation_headers(client, if_match=f'"{saved.json()["revision"]}"'),
        )
        assert blocked.status_code == 422 and "porch" in blocked.text
        summary = client.get(f"/api/gui/v1/projects/{a}/object-summary").json()
        assert summary["devices"] == [
            {"resource": "roofline.demo", "kind": "pixel_output", "scene_ids": ["main"]}
        ]
        assert {(o["id"], o["owner"]) for o in summary["objects"]} == {
            ("klown-mouth", "show"),
            ("porch", "installation"),
        }

        # Promotion makes the show fixture reusable; the other show then inherits it.
        promoted = client.post(
            f"/api/gui/v1/projects/{a}/object-actions/promote",
            json={"resource": "roofline.demo", "kind": "fixture", "id": "klown-mouth"},
            headers=_mutation_headers(client, if_match=f'"{saved.json()["revision"]}"'),
        )
        assert promoted.status_code == 200, promoted.text
        assert {
            o["id"]: o["owner"]
            for o in client.get(f"/api/gui/v1/projects/{b}/objects").json()["objects"]
        }["klown-mouth"] == "installation"
        usage = next(o for o in client.get(base).json()["objects"] if o["id"] == "porch")["used_by"]
        assert usage == [{"project_id": a, "project_name": "Halloween", "scene_ids": ["main"]}]

        # Shows cannot rewrite inherited wiring directly.
        current = services.repository.get_revision(a)
        document = current.draft.model_dump(mode="json")
        document["installation"]["resources"][1]["segments"] = [
            {"id": "left", "start": 0, "end": 4},
            {"id": "right", "start": 4, "end": 8},
        ]
        with pytest.raises(InheritanceError):
            services.repository.update_draft(
                project_id=a,
                expected_revision=current.revision,
                draft=DraftProject.model_validate(document),
                actor="admin",
                role=Role.ADMINISTRATOR,
                occurred_at=datetime.now(UTC).isoformat(),
            )

        # MCP mirrors every scope with the same boundaries.
        read = _mcp_token(services, b, (McpScope.READ,))
        editor = _mcp_token(services, b, (McpScope.READ, McpScope.DIRECT_EDIT))
        installer = _mcp_token(services, b, (McpScope.READ, McpScope.INSTALLATION_EDIT))
        assert (
            _mcp_tool(client, read, "get_installation_objects")["structuredContent"]["revision"]
            > revision
        )
        assert {
            o["id"]
            for o in _mcp_tool(client, read, "get_show_objects")["structuredContent"]["objects"]
        } >= {"porch", "klown-mouth"}
        assert (
            _mcp_tool(client, read, "get_show_object_summary")["structuredContent"]["scenes"][0][
                "scene_id"
            ]
            == "main"
        )
        walk = {
            "resource": "roofline.demo",
            "kind": "path",
            "id": "walk",
            "name": "Walk",
            "segments": [{"segment": "left"}],
        }
        installation_revision = client.get(base).json()["revision"]
        assert _mcp_tool(
            client,
            editor,
            "edit_installation_object",
            action="create",
            base_revision=installation_revision,
            request=walk,
        )["isError"]
        made = _mcp_tool(
            client,
            installer,
            "edit_installation_object",
            action="create",
            base_revision=installation_revision,
            request=walk,
        )
        assert made["isError"] is False, made
        b_revision = services.repository.get_revision(b).revision
        scene = _mcp_tool(
            client,
            editor,
            "set_scene_objects",
            scene_id="main",
            base_revision=b_revision,
            objects=[{"resource": "roofline.demo", "kind": "path", "id": "walk"}],
        )
        assert scene["isError"] is False, scene
        assert [
            o["id"]
            for o in _mcp_tool(client, read, "get_scene_objects", scene_id="main")[
                "structuredContent"
            ]["objects"]
        ] == ["walk"]
        assert _mcp_tool(
            client,
            read,
            "set_scene_objects",
            scene_id="main",
            base_revision=b_revision + 1,
            objects=[],
        )["isError"]


def test_startup_seeds_wiring_from_existing_shows_and_adopts_them(tmp_path: Path) -> None:
    import sqlite3

    from cueverge.gui.installation_objects import ensure_installation_objects

    _, services, _ = _mcp_fixture(tmp_path)
    installation = services.repository.get_runtime_installation().id
    with sqlite3.connect(services.repository.database_path) as connection:
        connection.execute("DELETE FROM installation_objects")
    ids = []
    for name, segments in (
        ("Whole", [{"id": "garage", "start": 0, "end": 3}, {"id": "eave", "start": 3, "end": 8}]),
        (
            "Split",
            [
                {"id": "garage", "start": 0, "end": 3},
                {"id": "px-0", "start": 3, "end": 4},
                {"id": "rest", "start": 4, "end": 8},
            ],
        ),
    ):
        document = _draft(editor_metadata={"installation_id": installation})
        document["installation"]["resources"][1]["segments"] = segments
        document["installation"]["resources"][1]["fixtures"] = [
            {"id": "klown-mouth", "segments": [{"segment": segments[1]["id"]}]}
        ]
        ids.append(
            services.repository.create_project(
                name=name,
                draft=DraftProject.model_validate(document),
                actor="admin",
                role=Role.ADMINISTRATOR,
                occurred_at=datetime.now(UTC).isoformat(),
            ).project.id
        )
    ensure_installation_objects(services)
    revision, store = services.repository.get_installation_objects(installation) or (0, None)
    assert revision == 1 and store is not None
    entry = store.entry("roofline.demo")
    assert entry is not None and [s.id for s in entry.segments] == ["garage", "eave"]
    assert entry.fixtures == ()  # show fixtures stay show-owned until promoted
    for project_id in ids:
        draft = services.repository.get_revision(project_id).draft
        assert draft.editor_metadata["object_inheritance"] == 1
        assert [s.id for s in draft.installation.resources[1].segments] == ["garage", "eave"]
    split = services.repository.get_revision(ids[1]).draft.installation.resources[1]
    assert split.fixtures[0].segments == (SegmentReference(segment="eave", start=0, end=1),)
