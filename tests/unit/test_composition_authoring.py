import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from cueverge.gui.composition_authoring import (
    CompositionEditError,
    composition_usage,
    delete_composition,
    remove_composition_from_scene,
)
from cueverge.gui.contracts import DraftProject
from tests.unit.test_stage_renderer import _draft, _visual_layer


def _nested_draft() -> dict[str, Any]:
    """Scene root 'stage-composition' nests 'child'; 'child' is also a scene instance."""
    document = _draft()
    metadata = cast(dict[str, Any], document["editor_metadata"])
    compositions = metadata["compositions"]
    child = copy.deepcopy(compositions["stage-composition"])
    child.update({"id": "child", "name": "Child", "children": []})
    compositions["child"] = child
    compositions["stage-composition"]["children"] = ["child"]
    layers = metadata["timeline_transform_layers"]
    nested = _visual_layer()
    nested.update(
        {
            "id": "nested-child",
            "name": "Nested child",
            "kind": "composition",
            "source_id": "child",
            "keyframes": [{**nested["keyframes"][0], "id": "nested-frame"}],
        }
    )
    child_layer = _visual_layer()
    child_layer.update(
        {
            "id": "child-layer",
            "track_id": "child-track",
            "keyframes": [{**child_layer["keyframes"][0], "id": "child-frame"}],
        }
    )
    layers["stage-composition"] = [_visual_layer(), nested]
    layers["child"] = [child_layer]
    metadata["timeline_tracks"] = {
        "stage-composition": [
            {"id": "stage-track", "name": "Media", "kind": "group", "resource_binding": "x"}
        ],
        "child": [{"id": "child-track", "name": "Media", "kind": "group", "resource_binding": "x"}],
    }
    metadata["composition_instances"] = {
        "ambient": [
            {
                "id": "child-instance",
                "composition_id": "child",
                "start_offset_ms": 0,
                "time_scale": 1,
                "parameter_overrides": {},
                "placement": {"x": 0, "y": 0},
            }
        ]
    }
    metadata["timeline_sync_schedule"] = [
        {"scene_id": "ambient"},
        {"scene_id": "ambient", "editing_space_id": "ambient"},
        {"scene_id": "ambient", "editing_space_id": "stage-composition"},
        {"scene_id": "ambient", "editing_space_id": "child"},
    ]
    return document


def test_usage_lists_parents_scenes_instances_and_nested_layers() -> None:
    draft = DraftProject.model_validate(_nested_draft())
    assert composition_usage(draft, "child") == [
        "composition Stage",
        "scene Ambient instance child-instance",
        "composition Stage media layer Nested child",
    ]
    assert composition_usage(draft, "stage-composition") == ["scene Ambient"]
    with pytest.raises(CompositionEditError, match="does not exist"):
        composition_usage(draft, "missing")


def test_delete_removes_every_reference_and_owned_timeline_data() -> None:
    original = DraftProject.model_validate(_nested_draft())
    updated = delete_composition(original, "child")
    metadata = updated.editor_metadata
    assert set(metadata["compositions"]) == {"stage-composition"}
    assert metadata["compositions"]["stage-composition"]["children"] == []
    assert metadata["composition_instances"] == {"ambient": []}
    assert [
        layer["id"] for layer in metadata["timeline_transform_layers"]["stage-composition"]
    ] == ["ghost-layer"]
    assert "child" not in metadata["timeline_transform_layers"]
    assert "child" not in metadata["timeline_tracks"]
    assert metadata["scene_compositions"] == {"ambient": ["stage-composition"]}
    assert metadata["timeline_sync_schedule"] == [
        {"scene_id": "ambient"},
        {"scene_id": "ambient", "editing_space_id": "ambient"},
        {"scene_id": "ambient", "editing_space_id": "stage-composition"},
    ]
    assert updated.scenes == original.scenes
    assert "child" in original.editor_metadata["compositions"]

    root_deleted = delete_composition(updated, "stage-composition")
    assert root_deleted.editor_metadata["compositions"] == {}
    assert root_deleted.editor_metadata["scene_compositions"] == {"ambient": []}
    with pytest.raises(CompositionEditError, match="does not exist"):
        delete_composition(root_deleted, "stage-composition")


def test_remove_from_scene_keeps_the_composition_for_reuse() -> None:
    original = DraftProject.model_validate(_nested_draft())
    updated = remove_composition_from_scene(original, "ambient", "child")
    metadata = updated.editor_metadata
    assert set(metadata["compositions"]) == {"stage-composition", "child"}
    assert metadata["composition_instances"] == {"ambient": []}
    # Still reachable through the scene root's nested layer, so its schedule entry stays.
    assert {"scene_id": "ambient", "editing_space_id": "child"} in metadata[
        "timeline_sync_schedule"
    ]
    assert [layer["id"] for layer in metadata["timeline_transform_layers"]["child"]] == [
        "child-layer"
    ]

    root_removed = remove_composition_from_scene(updated, "ambient", "stage-composition")
    root_metadata = root_removed.editor_metadata
    assert root_metadata["scene_compositions"] == {"ambient": []}
    assert set(root_metadata["compositions"]) == {"stage-composition", "child"}
    assert root_metadata["timeline_sync_schedule"] == [
        {"scene_id": "ambient"},
        {"scene_id": "ambient", "editing_space_id": "ambient"},
    ]
    with pytest.raises(CompositionEditError, match="not used directly"):
        remove_composition_from_scene(root_removed, "ambient", "stage-composition")
    with pytest.raises(CompositionEditError, match="Scene does not exist"):
        remove_composition_from_scene(root_removed, "missing", "child")


def test_mcp_composition_usage_remove_and_delete_share_draft_validation(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from cueverge.controller.app import create_app
    from cueverge.gui.contracts import Role
    from cueverge.gui.mcp_authoring import McpScope
    from tests.unit.test_gui_foundation import _mcp_fixture, _mcp_token, _mcp_tool

    runtime, services, _ = _mcp_fixture(tmp_path)
    original = DraftProject.model_validate(_nested_draft())
    project = services.repository.create_project(
        name="Composition delete fixture",
        draft=original,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    project_id = project.project.id
    direct = _mcp_token(services, project_id, (McpScope.READ, McpScope.DIRECT_EDIT))
    reader = _mcp_token(services, project_id, (McpScope.READ, McpScope.DRAFT_EDIT))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        capabilities = _mcp_tool(client, reader, "get_capabilities")["structuredContent"]
        assert capabilities["features"]["composition_deletion"] is True
        tools = capabilities["tools"]
        schema = _mcp_tool(client, reader, "get_authoring_schema")["structuredContent"]
        assert schema["x-composition-deletion"]["scope"] == "direct_edit"
        assert tools["delete_composition"] == "delete_composition"
        assert tools["remove_composition_from_scene"] == "remove_composition_from_scene"
        usage = _mcp_tool(client, reader, "get_composition_usage", composition_id="child")
        assert usage["structuredContent"]["used_by"] == composition_usage(original, "child")
        assert (
            _mcp_tool(
                client, reader, "delete_composition", composition_id="child", base_revision=1
            )["isError"]
            is True
        )
        removed = _mcp_tool(
            client,
            direct,
            "remove_composition_from_scene",
            scene_id="ambient",
            composition_id="child",
            base_revision=1,
        )
        assert removed["isError"] is False
        assert removed["structuredContent"]["revision"] == 2
        assert services.repository.get_revision(project_id).draft == (
            remove_composition_from_scene(original, "ambient", "child")
        )
        stale = _mcp_tool(
            client, direct, "delete_composition", composition_id="child", base_revision=1
        )
        assert stale["isError"] is True
        deleted = _mcp_tool(
            client, direct, "delete_composition", composition_id="child", base_revision=2
        )
        assert deleted["isError"] is False
        assert deleted["structuredContent"]["hardware_commands_sent"] is False
        head = services.repository.get_revision(project_id)
        assert head.revision == 3
        assert "child" not in head.draft.editor_metadata["compositions"]
        assert (
            _mcp_tool(
                client, direct, "delete_composition", composition_id="child", base_revision=3
            )["isError"]
            is True
        )
        actions = {event.action for event in services.repository.list_audit_events()}
        assert {"mcp.composition.remove_from_scene", "mcp.composition.delete"} <= actions


def test_scene_root_with_instances_is_placed_once_per_instance() -> None:
    from cueverge.gui.compiler import _compile_composition_graph

    document = _nested_draft()
    metadata = document["editor_metadata"]
    metadata["composition_instances"]["ambient"].append(
        {
            "id": "stage-instance",
            "composition_id": "stage-composition",
            "start_offset_ms": 0,
            "time_scale": 1,
            "parameter_overrides": {},
            "placement": {"x": 0, "y": 0},
        }
    )
    graph = _compile_composition_graph(DraftProject.model_validate(document))
    contexts = graph["scenes"][0]["contexts"]
    stage_contexts = [
        context["context_id"]
        for context in contexts
        if context["editing_space_id"] == "stage-composition"
    ]
    assert stage_contexts == ["instance:stage-instance"]

    metadata["composition_instances"]["ambient"] = []
    graph = _compile_composition_graph(DraftProject.model_validate(document))
    assert [
        context["context_id"]
        for context in graph["scenes"][0]["contexts"]
        if context["editing_space_id"] == "stage-composition"
    ] == ["scene:ambient>stage-composition"]
