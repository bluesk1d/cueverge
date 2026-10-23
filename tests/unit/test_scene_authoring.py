import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.core.models import ResourceBinding, SceneDefinitionV2
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.contracts import (
    AddSceneOutputRequest,
    CreateSceneRequest,
    DraftProject,
    Role,
)
from cueverge.gui.mcp_authoring import McpScope
from cueverge.gui.scene_authoring import add_scene_output, create_scene
from cueverge.simulation import ShowSimulation
from tests.unit.test_gui_foundation import (
    _draft,
    _login,
    _mcp_fixture,
    _mcp_token,
    _mcp_tool,
    _mutation_headers,
    _services,
)


def _create_request(resource: str = "roofline.demo", **changes: Any) -> dict[str, Any]:
    return {
        "scene_id": "halloween-preshow",
        "name": "Halloween preshow",
        "duration_ms": 180000,
        "mode": "controller_timed",
        "completion_policy": "loop",
        "output": {"output_id": "initial", "resource": resource},
        **changes,
    }


@pytest.mark.parametrize("via_mcp", [False, True])
def test_create_and_add_outputs_are_revisioned_lossless_and_discoverable(
    tmp_path: Path, via_mcp: bool
) -> None:
    runtime, services, _ = _mcp_fixture(tmp_path)
    installation_id = services.repository.get_runtime_installation().id
    original = DraftProject.model_validate(
        _draft(
            editor_metadata={
                "installation_id": installation_id,
                "custom_note": "Keep me",
                "timeline_markers": {"ambient": [{"id": "marker", "name": "Keep", "time_ms": 20}]},
            }
        )
    )
    stored = services.repository.create_project(
        name="Scene creation",
        draft=original,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    extra = ResourceBinding(resource="projector.extra", kind="media_agent", target="extra-agent")
    unused = ResourceBinding(
        resource="pixels.extra", kind="pixel_output", target="memory-extra", pixel_count=12
    )
    runtime.catalog = runtime.catalog.model_copy(
        update={
            "installation": runtime.catalog.installation.model_copy(
                update={
                    "resources": (*runtime.catalog.installation.resources, extra, unused),
                }
            ),
        }
    )
    project_id = stored.project.id
    path = f"/api/gui/v1/projects/{project_id}"
    token = _mcp_token(
        services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT, McpScope.SIMULATION)
    )
    read_token = _mcp_token(services, project_id, (McpScope.READ, McpScope.DRAFT_EDIT))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        assert client.get(f"{path}/scene-authoring-options").status_code == 401
        assert client.post(f"{path}/scenes", json=_create_request()).status_code == 401
        _login(client)
        assert client.post(f"{path}/scenes", json=_create_request()).status_code == 403
        assert (
            client.post(
                f"{path}/scenes", json=_create_request(), headers=_mutation_headers(client)
            ).status_code
            == 428
        )
        options = client.get(f"{path}/scene-authoring-options").json()
        assert set(options["importable_resources"]) == {"projector.extra", "pixels.extra"}
        assert (
            options
            == _mcp_tool(client, read_token, "get_scene_authoring_options")["structuredContent"]
        )
        schema = _mcp_tool(client, read_token, "get_authoring_schema")["structuredContent"]
        assert schema["x-scene-authoring"]["write_scope"] == "direct_edit"
        capabilities = _mcp_tool(client, read_token, "get_capabilities")["structuredContent"]
        for feature in ("scene_creation", "scene_output_creation", "media_only_scenes"):
            assert capabilities["features"][feature] is True
        for tool in ("create_scene", "add_scene_output"):
            arguments = {"base_revision": 1, "request": _create_request()}
            if tool == "add_scene_output":
                arguments.update(
                    scene_id="ambient",
                    request={
                        "output_id": "extra",
                        "resource": "projector.extra",
                    },
                )
            assert _mcp_tool(client, read_token, tool, **arguments)["isError"] is True

        def edit(tool: str, revision: int, request: dict[str, Any], scene_id: str | None = None):
            if via_mcp:
                arguments = {"base_revision": revision, "request": request}
                if scene_id is not None:
                    arguments["scene_id"] = scene_id
                result = _mcp_tool(client, token, tool, **arguments)
                assert result["isError"] is False, result
                assert result["structuredContent"]["hardware_commands_sent"] is False
                assert result["structuredContent"]["revision"] == revision + 1
            else:
                url = f"{path}/scenes" if scene_id is None else f"{path}/scenes/{scene_id}/outputs"
                response = client.post(
                    url, json=request, headers=_mutation_headers(client, if_match=f'"{revision}"')
                )
                assert response.status_code == 200, response.text
                assert response.headers["etag"] == f'"{revision + 1}"'
            return services.repository.get_revision(project_id)

        created = edit("create_scene", 1, _create_request())
        assert created.draft.scenes[0] == original.scenes[0]
        assert created.draft.editor_metadata == original.editor_metadata
        preshow = created.draft.scenes[-1]
        assert isinstance(preshow, SceneDefinitionV2)
        assert preshow.media == ()
        assert preshow.lighting[0].timeline[0].primitive == "blackout"
        assert preshow.lighting[0].timeline[0].duration_ms == 180000
        added = edit(
            "add_scene_output",
            2,
            {
                "output_id": "windows",
                "resource": "projector.extra",
            },
            preshow.id,
        )
        mixed = added.draft.scenes[-1]
        assert isinstance(mixed, SceneDefinitionV2)
        assert mixed.lighting == preshow.lighting
        assert mixed.clock_source == "controller"
        assert mixed.mode == "controller_timed"
        assert mixed.duration_ms == 180000
        assert added.draft.editor_metadata == original.editor_metadata
        assert added.draft.installation.resources == (*original.installation.resources, extra)
        assert mixed.resources == ("roofline.demo", "projector.extra")
        changed_clock = edit(
            "add_scene_output",
            3,
            {
                "output_id": "clock",
                "resource": "projector.demo",
                "make_clock_source": True,
            },
            preshow.id,
        )
        assert changed_clock.draft.scenes[-1].mode == "media_synchronized"
        assert changed_clock.draft.scenes[-1].clock_source == "clock"
        compiled = services.compiler.compile(
            project_id=project_id, revision=4, draft=changed_clock.draft
        )
        assert len(load_show_package(compiled.package_root).scenes[preshow.id].media_outputs) == 2
        conflict = client.post(
            f"{path}/scenes",
            json=_create_request(scene_id="stale"),
            headers=_mutation_headers(client, if_match='"1"'),
        )
        assert conflict.status_code == 409
        assert (
            _mcp_tool(
                client,
                token,
                "create_scene",
                base_revision=1,
                request=_create_request(scene_id="stale"),
            )["isError"]
            is True
        )
        for body in (
            {"output_id": "duplicate", "resource": "projector.extra"},
            {"output_id": "windows", "resource": "pixels.extra"},
            {"output_id": "invalid-clock", "resource": "pixels.extra", "make_clock_source": True},
            {"output_id": "unknown", "resource": "foreign.projector"},
        ):
            response = client.post(
                f"{path}/scenes/{preshow.id}/outputs",
                json=body,
                headers=_mutation_headers(client, if_match='"4"'),
            )
            assert response.status_code == 422
            assert (
                _mcp_tool(
                    client,
                    token,
                    "add_scene_output",
                    scene_id=preshow.id,
                    base_revision=4,
                    request=body,
                )["isError"]
                is True
            )
        assert services.repository.get_revision(project_id).revision == 4
        assert runtime.active_scene is None


@pytest.mark.parametrize("binding", [None, "another-installation"])
def test_new_resource_discovery_does_not_cross_installations(tmp_path: Path, binding: str | None):
    runtime, services, _ = _mcp_fixture(tmp_path)
    stored = services.repository.create_project(
        name="Isolated",
        draft=DraftProject.model_validate(
            _draft(
                editor_metadata={
                    "installation_id": binding,
                }
            )
        ),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    extra = ResourceBinding(resource="private", kind="media_agent", target="private-agent")
    runtime.catalog = runtime.catalog.model_copy(
        update={
            "installation": runtime.catalog.installation.model_copy(update={"resources": (extra,)}),
        }
    )
    token = _mcp_token(services, stored.project.id, (McpScope.READ, McpScope.DIRECT_EDIT))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        options = _mcp_tool(client, token, "get_scene_authoring_options")["structuredContent"]
        assert options["importable_resources"] == []
        assert {item["resource"] for item in options["resources"]} == {
            "projector.demo",
            "roofline.demo",
        }
        assert (
            _mcp_tool(
                client, token, "create_scene", base_revision=1, request=_create_request("private")
            )["isError"]
            is True
        )
        assert services.repository.get_revision(stored.project.id).revision == 1


def test_v1_output_upgrade_preserves_content_and_rejects_duplicate_agents() -> None:
    document = _draft(editor_metadata={"asset_bindings": {"video": 2}})
    document["scenes"][0]["media"]["asset"] = "video"
    original = DraftProject.model_validate(document)
    binding = ResourceBinding(
        resource="pixels.extra", kind="pixel_output", target="new-pixels", pixel_count=8
    )
    upgraded = add_scene_output(
        original,
        "ambient",
        AddSceneOutputRequest(output_id="new-light", resource=binding.resource),
        binding,
    )
    old = original.scenes[0]
    new = upgraded.scenes[0]
    assert isinstance(new, SceneDefinitionV2)
    assert new.clock_source == "media"
    assert (
        new.media[0].model_dump(
            exclude={
                "id",
                "required",
                "start_delay_ms",
                "end_behavior",
            }
        )
        == old.media.model_dump()
    )
    assert (
        new.lighting[0].model_dump(
            exclude={
                "id",
                "required",
                "timing_offset_ms",
            }
        )
        == old.lighting.model_dump()
    )
    for key, value in old.model_dump().items():
        if key not in {"schema_version", "media", "lighting", "resources"}:
            assert new.model_dump()[key] == value
    assert upgraded.editor_metadata == original.editor_metadata
    same_agent = ResourceBinding(
        resource="alias", kind="media_agent", target=original.installation.resources[0].target
    )
    with pytest.raises(ValueError, match="media agent already"):
        add_scene_output(
            original,
            "ambient",
            AddSceneOutputRequest(output_id="alias", resource="alias"),
            same_agent,
        )


@pytest.mark.parametrize("mode", ["controller_timed", "media_synchronized"])
@pytest.mark.parametrize("policy", ["loop", "hold", "stop"])
def test_media_only_compiles_simulates_transport_and_completes(
    tmp_path: Path, mode: str, policy: str
) -> None:
    original = DraftProject.model_validate(_draft())
    created = create_scene(
        original,
        CreateSceneRequest.model_validate(
            _create_request(
                "projector.demo",
                mode=mode,
                completion_policy=policy,
                duration_ms=4000,
            )
        ),
        original.installation.resources[0],
    )
    document = created.model_dump(mode="json")
    document["scenes"] = [document["scenes"][-1]]
    draft = DraftProject.model_validate(document)
    compiled = DraftCompiler(tmp_path).compile(project_id="media-only", revision=1, draft=draft)
    catalog = load_show_package(compiled.package_root)
    scene = catalog.scenes["halloween-preshow"]
    assert scene.lighting_outputs == ()
    assert scene.pixel_count == 0

    async def scenario() -> None:
        simulation = ShowSimulation(catalog, automatic_rendering=False)
        try:
            await simulation.operator_start("halloween-preshow")
            assert simulation.runtime.status()["lighting_outputs"] == []
            await simulation.advance(500)
            assert simulation.runtime.status()["estimated_position_ms"] == 500
            await simulation.runtime.pause()
            await simulation.runtime.seek(600)
            await simulation.runtime.resume()
            await simulation.advance(4000)
            await simulation.runtime.wait_for_lifecycle_idle()
            assert simulation.runtime.scene_completions == 1
            if policy == "stop":
                assert simulation.runtime.active_scene is None
            else:
                assert simulation.runtime.active_scene is not None
            assert simulation.sink.frames == []
            commands = [item["command"] for item in simulation.channels["fake-media-1"].sent]
            assert all(
                command in commands for command in ("prepare", "play", "pause", "seek", "resume")
            )
            await simulation.runtime.stop()
        finally:
            await simulation.runtime.shutdown()

    asyncio.run(scenario())


def test_media_only_mcp_simulation_and_archived_authoring(tmp_path: Path) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    token = _mcp_token(
        services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT, McpScope.SIMULATION)
    )
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        assert (
            _mcp_tool(
                client,
                token,
                "create_scene",
                base_revision=1,
                request=_create_request("projector.demo", mode="media_synchronized"),
            )["isError"]
            is False
        )
        preview = _mcp_tool(
            client,
            token,
            "preview_interaction_workflow",
            request={
                "source_revision": 2,
                "starting_scene_id": "halloween-preshow",
                "steps": [{"kind": "advance", "delta_ms": 500}],
            },
        )
        assert preview["isError"] is False, preview
        result = preview["structuredContent"]
        assert result["hardware_commands_sent"] is False
        assert result["frame"] is None
        assert "no matching LED output" in result["frame_unavailable_reason"]
        services.repository.archive_project(
            project_id=project_id,
            expected_revision=1,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=datetime.now(UTC).isoformat(),
        )
        assert (
            _mcp_tool(
                client,
                token,
                "create_scene",
                base_revision=2,
                request=_create_request(scene_id="archived"),
            )["isError"]
            is True
        )
        assert (
            _mcp_tool(
                client,
                token,
                "add_scene_output",
                scene_id="halloween-preshow",
                base_revision=2,
                request={"output_id": "lights", "resource": "roofline.demo"},
            )["isError"]
            is True
        )
        assert services.repository.get_revision(project_id).revision == 2


def test_outputless_scene_is_still_invalid() -> None:
    with pytest.raises(ValueError, match="at least one media or lighting output"):
        SceneDefinitionV2.model_validate(
            {
                "schema_version": 2,
                "id": "empty",
                "name": "Empty",
                "mode": "controller_timed",
                "clock_source": "controller",
                "duration_ms": 1000,
                "resources": ["roofline.demo"],
                "media": [],
                "lighting": [],
            }
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"duration_ms": 0},
        {"duration_ms": 1.5},
        {"name": "   "},
        {"scene_id": "ambient"},
        {"scene_id": "../escape"},
        {"mode": "media_synchronized"},
        {"output": {"output_id": "invalid id", "resource": "roofline.demo"}},
    ],
)
def test_invalid_creation_is_rejected_by_http_and_mcp(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    runtime, services, project_id = _mcp_fixture(tmp_path)
    token = _mcp_token(services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT))
    request = _create_request(**changes)
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        _login(client)
        response = client.post(
            f"/api/gui/v1/projects/{project_id}/scenes",
            json=request,
            headers=_mutation_headers(client, if_match='"1"'),
        )
        assert response.status_code == 422
        assert (
            _mcp_tool(client, token, "create_scene", base_revision=1, request=request)["isError"]
            is True
        )
        assert services.repository.get_revision(project_id).revision == 1


def test_media_only_preserves_persistent_safe_state_without_rendering_a_baseline(tmp_path: Path):
    original = DraftProject.model_validate(_draft())
    created = create_scene(
        original,
        CreateSceneRequest.model_validate(_create_request("projector.demo")),
        original.installation.resources[0],
    )
    document = created.model_dump(mode="json")
    document["scenes"] = [document["scenes"][-1]]
    compiled = DraftCompiler(tmp_path).compile(
        project_id="media-only-safe", revision=1, draft=DraftProject.model_validate(document)
    )
    (compiled.package_root / "looks.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resource": "roofline.demo",
                "cycle_ms": 4000,
                "baseline": [
                    {
                        "id": "baseline",
                        "primitive": "solid",
                        "start_ms": 0,
                        "duration_ms": 4000,
                        "color": "#FF0000",
                    }
                ],
                "safe_state": [
                    {
                        "id": "safe",
                        "primitive": "blackout",
                        "start_ms": 0,
                        "duration_ms": 1,
                    }
                ],
            }
        )
    )
    catalog = load_show_package(compiled.package_root)

    async def scenario():
        simulation = ShowSimulation(catalog, automatic_rendering=False)
        try:
            await simulation.operator_start("halloween-preshow")
            await simulation.advance(1000)
            assert simulation.sinks["roofline.demo"].frames == []
            await simulation.runtime.stop()
            assert len(simulation.sinks["roofline.demo"].frames) == 1
            assert simulation.sinks["roofline.demo"].frames[0].pixels == ((0, 0, 0),) * 8
            assert simulation.runtime.lighting_output_error_count == 0
        finally:
            await simulation.runtime.shutdown()

    asyncio.run(scenario())


def test_creation_and_output_addition_enforce_current_project_permissions(tmp_path: Path):
    runtime = ControllerRuntime()
    services = _services(tmp_path, runtime, role=Role.VIEWER)
    stored = services.repository.create_project(
        name="Read-only authoring",
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
    path = f"/api/gui/v1/projects/{project_id}"
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        _login(client)
        assert client.get(f"{path}/scene-authoring-options").status_code == 200
        headers = _mutation_headers(client, if_match='"1"')
        assert (
            client.post(f"{path}/scenes", json=_create_request(), headers=headers).status_code
            == 403
        )
        assert (
            client.post(
                f"{path}/scenes/ambient/outputs",
                json={"output_id": "new", "resource": "roofline.demo"},
                headers=headers,
            ).status_code
            == 403
        )
        for tool, arguments in (
            ("create_scene", {"base_revision": 1, "request": _create_request()}),
            (
                "add_scene_output",
                {
                    "base_revision": 1,
                    "scene_id": "ambient",
                    "request": {"output_id": "new", "resource": "roofline.demo"},
                },
            ),
        ):
            response = client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert response.status_code == 401
        assert services.repository.get_revision(project_id).revision == 1
