from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.models import (
    CompletionDefinition,
    OpportunityDefinition,
    OpportunityProgramDefinition,
    ProgramScene,
)
from cueverge.gui.authoring_schema import authoring_schema
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import DraftProject, Role, ShowPlanEditRequest
from cueverge.gui.mcp_authoring import McpScope
from cueverge.gui.passwords import hash_password
from cueverge.gui.service import GuiServices
from cueverge.gui.show_plan import ShowPlanError, apply_show_plan_edit, describe_show_plan

PASSWORD = "original show plan test password"
PASSWORD_HASH = hash_password(PASSWORD)
ORIGIN = "http://localhost:5173"


def original() -> DraftProject:
    return DraftProject.model_validate_json(
        Path("tests/fixtures/usability-authoring.json").read_text()
    )


def with_completion(draft: DraftProject, **completions: CompletionDefinition) -> DraftProject:
    return draft.model_copy(
        update={
            "scenes": tuple(
                scene.model_copy(update={"completion": completions[scene.id]})
                if scene.id in completions
                else scene
                for scene in draft.scenes
            )
        }
    )


def request(**fields: Any) -> ShowPlanEditRequest:
    return ShowPlanEditRequest(source_revision=1, **fields)


def test_plan_describes_start_ending_shuffle_list_and_unused_scenes() -> None:
    plan = describe_show_plan(original())
    assert plan["starting_scene_id"] == "baseline-timeline"
    main = plan["main_show"]
    assert [scene["id"] for scene in main["scenes"]] == ["baseline-timeline"]
    assert main["scenes"][0]["ending"] == {
        "kind": "repeat",
        "next_scene_id": None,
        "label": "Repeats",
    }
    assert main["end"] == {"kind": "repeat", "scene_id": "baseline-timeline"}
    assert plan["shuffle_list"]["pool_id"] == "background"
    assert [(entry["id"], entry["chance_percent"]) for entry in plan["shuffle_list"]["scenes"]] == [
        ("baseline-timeline", 100)
    ]
    # legacy-sweep is the baseline's failure fallback, so only legacy-pulse is unused.
    assert [scene["id"] for scene in plan["unused_scenes"]] == ["legacy-pulse"]
    assert plan["warnings"] == []


def test_play_next_chain_is_followed_and_reports_where_it_returns() -> None:
    draft = with_completion(
        original(),
        **{
            "baseline-timeline": CompletionDefinition(
                policy="transition_to", target_scene_id="legacy-sweep"
            ),
            "legacy-sweep": CompletionDefinition(
                policy="transition_to", target_scene_id="baseline-timeline"
            ),
        },
    )
    plan = describe_show_plan(draft)
    assert [scene["id"] for scene in plan["main_show"]["scenes"]] == [
        "baseline-timeline",
        "legacy-sweep",
    ]
    assert plan["main_show"]["scenes"][0]["ending"]["label"] == (
        "Then plays Original synthetic legacy sweep"
    )
    assert plan["main_show"]["end"] == {"kind": "back_to", "scene_id": "baseline-timeline"}
    assert [scene["id"] for scene in plan["unused_scenes"]] == ["legacy-pulse"]


def test_scene_ending_edit_changes_only_that_scene_completion() -> None:
    baseline = original()
    result = apply_show_plan_edit(
        baseline,
        request(
            operation="scene_ending",
            scene_id="baseline-timeline",
            ending="play_next",
            next_scene_id="legacy-pulse",
        ),
    )
    expected = baseline.model_dump(mode="json")
    expected["scenes"][0]["completion"] = {
        "policy": "transition_to",
        "target_scene_id": "legacy-pulse",
    }
    assert result.model_dump(mode="json") == expected
    for ending, policy in (
        ("repeat", "loop"),
        ("shuffle", "select_next"),
        ("freeze", "hold"),
        ("stop", "stop"),
    ):
        changed = apply_show_plan_edit(
            baseline, request(operation="scene_ending", scene_id="legacy-sweep", ending=ending)
        )
        assert changed.scenes[1].completion == CompletionDefinition(policy=policy)


def test_shuffle_list_edit_preserves_existing_rules_and_adds_scenes() -> None:
    baseline = original()
    pool = baseline.program.pools[0]
    ruled = baseline.model_copy(
        update={
            "program": baseline.program.model_copy(
                update={
                    "pools": (
                        pool.model_copy(
                            update={
                                "scenes": (
                                    ProgramScene(
                                        scene_id="baseline-timeline",
                                        cooldown_ms=60000,
                                        conditions={"audience": False},
                                    ),
                                )
                            }
                        ),
                    )
                }
            )
        }
    )
    result = apply_show_plan_edit(
        ruled,
        request(
            operation="shuffle_list",
            shuffle=[
                {"scene_id": "legacy-sweep", "weight": 3},
                {"scene_id": "baseline-timeline", "weight": 2},
            ],
        ),
    )
    entries = result.program.pools[0].scenes
    assert entries == (
        ProgramScene(scene_id="legacy-sweep", weight=3),
        ProgramScene(
            scene_id="baseline-timeline",
            weight=2,
            cooldown_ms=60000,
            conditions={"audience": False},
        ),
    )
    assert result.scenes == ruled.scenes
    assert result.program.initial_scene_id == ruled.program.initial_scene_id
    plan = describe_show_plan(result)
    assert [entry["chance_percent"] for entry in plan["shuffle_list"]["scenes"]] == [60, 40]
    assert plan["shuffle_list"]["scenes"][1]["has_extra_rules"] is True


def test_starting_scene_creates_minimal_program_only_when_missing() -> None:
    baseline = original()
    moved = apply_show_plan_edit(
        baseline, request(operation="starting_scene", scene_id="legacy-sweep")
    )
    assert moved.program == baseline.program.model_copy(update={"initial_scene_id": "legacy-sweep"})
    empty = baseline.model_copy(update={"program": None})
    assert "Choose a starting scene" in describe_show_plan(empty)["warnings"][0]
    created = apply_show_plan_edit(
        empty, request(operation="starting_scene", scene_id="legacy-sweep")
    )
    assert created.program.initial_scene_id == "legacy-sweep"
    assert created.program.default_pool_id == "main"
    assert created.program.pools[0].scenes == (ProgramScene(scene_id="legacy-sweep"),)
    assert created.scenes == empty.scenes


def test_invalid_show_plan_edits_are_rejected_with_plain_messages() -> None:
    baseline = original()
    empty = baseline.model_copy(update={"program": None})
    with pytest.raises(ShowPlanError, match="starting scene first"):
        apply_show_plan_edit(
            empty, request(operation="scene_ending", scene_id="legacy-sweep", ending="shuffle")
        )
    with pytest.raises(ShowPlanError, match="Repeat this scene"):
        apply_show_plan_edit(
            baseline,
            request(
                operation="scene_ending",
                scene_id="legacy-sweep",
                ending="play_next",
                next_scene_id="legacy-sweep",
            ),
        )
    with pytest.raises(ShowPlanError, match="does not exist"):
        apply_show_plan_edit(baseline, request(operation="starting_scene", scene_id="missing"))
    with pytest.raises(ShowPlanError, match="Unknown scenes"):
        apply_show_plan_edit(
            baseline, request(operation="shuffle_list", shuffle=[{"scene_id": "missing"}])
        )
    with pytest.raises(ValidationError, match="at least one"):
        request(operation="shuffle_list", shuffle=[])
    with pytest.raises(ValidationError, match="only once"):
        request(
            operation="shuffle_list",
            shuffle=[{"scene_id": "legacy-sweep"}, {"scene_id": "legacy-sweep"}],
        )
    with pytest.raises(ValidationError, match="which scene plays next"):
        request(operation="scene_ending", scene_id="legacy-sweep", ending="play_next")


def test_visitor_responses_are_listed_and_unreachable_responses_warned() -> None:
    baseline = original()
    draft = baseline.model_copy(
        update={
            "opportunities": OpportunityProgramDefinition(
                schema_version=1,
                opportunities=(
                    OpportunityDefinition(
                        id="welcome",
                        scene_id="legacy-pulse",
                        trigger_event="audience.arrived",
                        ttl_ms=30000,
                    ),
                ),
            )
        }
    )
    plan = describe_show_plan(draft)
    assert plan["visitor_responses"][0]["when"] == "Visitors arrive"
    assert plan["visitor_responses"][0]["scene_id"] == "legacy-pulse"
    assert plan["unused_scenes"] == []
    assert any("visitor responses will not play" in item for item in plan["warnings"])
    shuffled = with_completion(
        draft, **{"baseline-timeline": CompletionDefinition(policy="select_next")}
    )
    assert describe_show_plan(shuffled)["warnings"] == []


def services_for(tmp_path: Path, draft: DraftProject):
    runtime = ControllerRuntime()
    services = GuiServices.create(
        GuiConfig(
            data_root=tmp_path,
            admin_password_hash=SecretStr(PASSWORD_HASH),
            admin_role=Role.ADMINISTRATOR,
            allowed_origins=(ORIGIN,),
            secure_cookies=False,
        ),
        runtime,
    )
    bound = draft.model_copy(
        update={
            "editor_metadata": {
                **draft.editor_metadata,
                "installation_id": services.repository.get_runtime_installation().id,
            }
        }
    )
    project = services.repository.create_project(
        name="Show plan",
        draft=bound,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    return runtime, services, project


def test_rest_and_mcp_show_plan_match_and_respect_authorization(tmp_path: Path) -> None:
    runtime, services, project = services_for(tmp_path, original())

    def token(scopes: tuple[McpScope, ...]) -> str:
        return services.mcp_store.create_token(
            client_id="show-plan-client",
            username="admin",
            project_id=project.project.id,
            scopes=scopes,
            expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            occurred_at=datetime.now(UTC).isoformat(),
        ).token

    def tool(client: TestClient, credential: str, name: str, **arguments: Any):
        response = client.post(
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
        assert response.status_code == 200
        return response.json()["result"]

    edit = {
        "source_revision": 1,
        "operation": "scene_ending",
        "scene_id": "baseline-timeline",
        "ending": "play_next",
        "next_scene_id": "legacy-sweep",
    }
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        path = f"/api/gui/v1/projects/{project.project.id}/show-plan"
        assert client.get(path).status_code == 401
        client.post(
            "/api/gui/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["cueverge_csrf"]}
        assert client.put(path, json=edit).status_code == 403
        readonly = token((McpScope.READ,))
        assert tool(client, readonly, "update_show_plan", edit=edit)["isError"]
        rest_plan = client.get(path).json()
        assert rest_plan == tool(client, readonly, "get_show_plan")["structuredContent"]
        assert rest_plan["source_revision"] == 1

        granted = token((McpScope.READ, McpScope.DIRECT_EDIT))
        result = tool(client, granted, "update_show_plan", edit=edit)["structuredContent"]
        assert result == {
            "project_id": project.project.id,
            "revision": 2,
            "hardware_commands_sent": False,
        }
        assert client.put(path, json=edit, headers=headers).status_code == 409
        updated = client.get(path).json()
        assert [scene["id"] for scene in updated["main_show"]["scenes"]] == [
            "baseline-timeline",
            "legacy-sweep",
        ]
        invalid = {**edit, "source_revision": 2, "next_scene_id": "missing"}
        response = client.put(path, json=invalid, headers=headers)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "show_plan_invalid"
        start = {"source_revision": 2, "operation": "starting_scene", "scene_id": "legacy-sweep"}
        assert client.put(path, json=start, headers=headers).status_code == 200
        assert client.get(path).json()["starting_scene_id"] == "legacy-sweep"

        capabilities = tool(client, readonly, "get_capabilities")["structuredContent"]
        assert capabilities["show_plan"]["edit_tool"] == "update_show_plan"
        assert capabilities["show_plan"]["edit_scope"] == "direct_edit"
    assert authoring_schema()["x-show-plan"]["read_tool"] == "get_show_plan"
    assert runtime.active_scene is None
