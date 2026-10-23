import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.loader import load_show_package
from cueverge.core.models import (
    AudiencePresenceDefinition,
    EncounterDefinition,
    OpportunityProgramDefinition,
)
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import (
    DraftProject,
    GuidedResponseRequest,
    InteractionEditRequest,
    InteractionPreviewRequest,
    Role,
)
from cueverge.gui.interaction_preview import preview_interactions
from cueverge.gui.interactions import (
    InteractionError,
    apply_interaction_edit,
    describe_interactions,
)
from cueverge.gui.mcp_authoring import McpScope
from cueverge.gui.passwords import hash_password
from cueverge.gui.service import GuiServices

PASSWORD = "original interaction test password"
PASSWORD_HASH = hash_password(PASSWORD)
ORIGIN = "http://localhost:5173"


def original() -> DraftProject:
    return DraftProject.model_validate_json(
        Path("tests/fixtures/usability-authoring.json").read_text()
    )


def edit(draft: DraftProject, **changes: Any) -> InteractionEditRequest:
    response = GuidedResponseRequest(
        name="Welcome visitors",
        scene_id=draft.scenes[1].id,
        trigger_event="audience.arrived",
        ttl_ms=300000,
        **changes,
    )
    return InteractionEditRequest(
        source_revision=1,
        operation="response",
        response=response,
        visitors=None
        if draft.audience
        else AudiencePresenceDefinition(
            schema_version=1,
            cameras=("porch",),
            zones=("walkway",),
            arrival_confirmation_ms=1000,
            absence_grace_ms=1000,
            observation_freshness_ms=3000,
        ),
    )


def services_for(tmp_path: Path, draft: DraftProject, role: Role = Role.ADMINISTRATOR):
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
    bound = draft.model_copy(
        update={
            "editor_metadata": {
                **draft.editor_metadata,
                "installation_id": services.repository.get_runtime_installation().id,
            }
        }
    )
    project = services.repository.create_project(
        name="Original visitor workflow",
        draft=bound,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    return runtime, services, project


def test_focused_response_edit_preserves_identifiers_policies_and_unrelated_data() -> None:
    baseline = original()
    configured = apply_interaction_edit(baseline, edit(baseline))
    response = configured.opportunities.opportunities[-1]
    update = edit(configured, id=response.id).model_copy(
        update={
            "response": GuidedResponseRequest(
                id=response.id,
                name="Welcome visitors",
                scene_id=response.scene_id,
                trigger_event=response.trigger_event,
                ttl_ms=45000,
                min_presence_ms=response.min_presence_ms,
                acknowledgement_cooldown_ms=response.acknowledgement_cooldown_ms,
                priority=response.priority,
            ),
        }
    )
    result = apply_interaction_edit(configured, update)
    expected = configured.model_dump(mode="json")
    expected["opportunities"]["opportunities"][-1]["ttl_ms"] = 45000
    assert result.model_dump(mode="json") == expected
    assert result.scenes == baseline.scenes
    assert result.program == baseline.program
    assert response.id == result.opportunities.opportunities[-1].id


def test_first_guided_setup_requires_explicit_shared_completion_consent() -> None:
    baseline = original().model_copy(update={"program": None})
    with pytest.raises(InteractionError, match="confirm"):
        apply_interaction_edit(baseline, edit(baseline))
    request = edit(baseline, fallback_scene_id=baseline.scenes[0].id, prepare_completion=True)
    configured = apply_interaction_edit(baseline, request)
    assert configured.program.initial_scene_id == baseline.scenes[0].id
    assert configured.scenes[0].completion.policy == "select_next"
    assert configured.scenes[1].completion.policy == "select_next"
    assert configured.scenes[2] == baseline.scenes[2]
    for before, after in zip(baseline.scenes, configured.scenes, strict=True):
        assert before.model_dump(exclude={"completion"}) == after.model_dump(exclude={"completion"})


def test_existing_program_weights_and_extra_response_rules_are_preserved() -> None:
    baseline = original()
    configured = apply_interaction_edit(baseline, edit(baseline, prepare_completion=True))
    assert configured.program == baseline.program
    assert configured.scenes[0].completion.policy == "select_next"
    response = configured.opportunities.opportunities[-1].model_copy(
        update={
            "trigger_attributes": {"unexpected": "custom"},
        }
    )
    custom = configured.model_copy(
        update={
            "opportunities": OpportunityProgramDefinition(
                schema_version=1,
                opportunities=(response,),
            )
        }
    )
    card = describe_interactions(custom)["cards"][0]
    assert not card["editable"]
    with pytest.raises(InteractionError, match="advanced"):
        apply_interaction_edit(custom, edit(custom, id=response.id))


def test_guided_response_does_not_replace_an_existing_shared_visitor_area() -> None:
    baseline = original()
    configured = apply_interaction_edit(baseline, edit(baseline))
    request = edit(configured).model_copy(
        update={
            "visitors": configured.audience.model_copy(update={"cameras": ("other",)}),
        }
    )
    with pytest.raises(InteractionError, match="shared visitor area"):
        apply_interaction_edit(configured, request)


def test_existing_response_edit_does_not_add_a_missing_normal_program() -> None:
    baseline = original()
    configured = apply_interaction_edit(baseline, edit(baseline)).model_copy(
        update={"program": None}
    )
    previous = configured.opportunities.opportunities[-1]
    request = edit(configured, id=previous.id).model_copy(
        update={
            "response": GuidedResponseRequest(
                id=previous.id,
                name="Renamed only",
                trigger_event=previous.trigger_event,
                scene_id=previous.scene_id,
                ttl_ms=previous.ttl_ms,
                min_presence_ms=previous.min_presence_ms,
                acknowledgement_cooldown_ms=previous.acknowledgement_cooldown_ms,
                priority=previous.priority,
            ),
        }
    )
    updated = apply_interaction_edit(configured, request)
    assert updated.program is None
    assert updated.scenes == configured.scenes


def test_preview_qualifies_waits_selects_and_returns_using_isolated_runtime(tmp_path: Path) -> None:
    baseline = original()
    configured = apply_interaction_edit(baseline, edit(baseline, prepare_completion=True))
    runtime, services, project = services_for(tmp_path, configured)
    before = runtime.status()
    request = InteractionPreviewRequest.model_validate(
        {
            "source_revision": 1,
            "steps": [
                {"kind": "visitor", "camera": "porch", "zone": "walkway"},
                {"kind": "advance", "delta_ms": 1000},
                {"kind": "advance", "delta_ms": 179000},
                {"kind": "advance", "delta_ms": 4000},
            ],
        }
    )
    result = asyncio.run(preview_interactions(services, project.project.id, request))
    assert result["hardware_commands_sent"] is False
    assert result["hardware_adapters_opened"] is False
    trace = result["trace"]
    assert trace[1]["status"]["audience"]["state"] == "candidate"
    assert trace[2]["status"]["audience"]["state"] == "present"
    assert len(trace[2]["status"]["opportunities"]["pending"]) == 1
    assert trace[3]["status"]["active_scene"] == configured.scenes[1].id
    assert trace[4]["status"]["active_scene"] == configured.scenes[0].id
    assert trace[4]["status"]["opportunities"]["consumed_count"] == 1
    assert result["frame"]["evaluation"] == "production_lighting_renderer"
    assert runtime.status()["active_scene"] == before["active_scene"]
    assert services.repository.get_revision(project.project.id).revision == 1
    again = asyncio.run(preview_interactions(services, project.project.id, request))
    assert result == again


@pytest.mark.parametrize(
    "camera,age,reason",
    [
        ("other", 0, "camera_not_matched"),
        ("porch", 10000, "stale_observation"),
    ],
)
def test_preview_explains_unmatched_and_stale_input(
    tmp_path: Path, camera: str, age: int, reason: str
) -> None:
    baseline = original()
    configured = apply_interaction_edit(baseline, edit(baseline))
    _, services, project = services_for(tmp_path, configured)
    result = asyncio.run(
        preview_interactions(
            services,
            project.project.id,
            InteractionPreviewRequest.model_validate(
                {
                    "source_revision": 1,
                    "steps": [
                        {"kind": "visitor", "camera": camera, "zone": "walkway", "age_ms": age},
                    ],
                }
            ),
        )
    )
    assert reason in {event["reason"] for event in result["trace"][1]["observations"]}
    assert result["trace"][-1]["status"]["audience"]["state"] == "absent"


def test_preview_reports_expiry_missing_input_and_sustained_presence(tmp_path: Path) -> None:
    baseline = original()
    configured = apply_interaction_edit(baseline, edit(baseline, min_presence_ms=2000))
    _, services, project = services_for(tmp_path, configured)
    result = asyncio.run(
        preview_interactions(
            services,
            project.project.id,
            InteractionPreviewRequest.model_validate(
                {
                    "source_revision": 1,
                    "steps": [
                        {"kind": "advance", "delta_ms": 5000},
                        {"kind": "visitor", "camera": "porch", "zone": "walkway"},
                        {"kind": "advance", "delta_ms": 1000},
                        {"kind": "advance", "delta_ms": 1000},
                        {
                            "kind": "visitor",
                            "camera": "porch",
                            "zone": "walkway",
                            "observation": "stay",
                        },
                        {"kind": "advance", "delta_ms": 1000},
                    ],
                }
            ),
        )
    )
    assert result["trace"][1]["status"]["audience"]["state"] == "absent"
    assert (
        "presence_duration"
        in result["trace"][3]["status"]["opportunities"]["qualifying"][0]["blocked_by"]
    )
    assert result["trace"][-1]["status"]["opportunities"]["created_count"] == 1
    expired = apply_interaction_edit(
        configured,
        InteractionEditRequest(
            source_revision=1,
            operation="response",
            response=GuidedResponseRequest(
                id=configured.opportunities.opportunities[-1].id,
                name="Short response",
                trigger_event="audience.arrived",
                scene_id=configured.scenes[1].id,
                ttl_ms=100,
            ),
        ),
    )
    _, expired_services, expired_project = services_for(tmp_path / "expired", expired)
    output = asyncio.run(
        preview_interactions(
            expired_services,
            expired_project.project.id,
            InteractionPreviewRequest.model_validate(
                {
                    "source_revision": 1,
                    "steps": [
                        {"kind": "visitor", "camera": "porch", "zone": "walkway"},
                        {"kind": "advance", "delta_ms": 1500},
                    ],
                }
            ),
        )
    )
    assert output["trace"][-1]["status"]["opportunities"]["expired_count"] == 1
    assert output["trace"][-1]["status"]["opportunities"]["consumed_count"] == 0


def test_preview_candidate_is_unsaved_revision_bound_and_budgeted(tmp_path: Path) -> None:
    baseline = original()
    _, services, project = services_for(tmp_path, baseline)
    candidate = edit(baseline, prepare_completion=True)
    result = asyncio.run(
        preview_interactions(
            services,
            project.project.id,
            InteractionPreviewRequest(
                source_revision=1,
                proposed_edit=candidate,
            ),
        )
    )
    assert result["unsaved"]
    assert result == asyncio.run(
        preview_interactions(
            services,
            project.project.id,
            InteractionPreviewRequest(
                source_revision=1,
                proposed_edit=candidate,
            ),
        )
    )
    assert (
        services.repository.get_revision(project.project.id).draft.opportunities
        == baseline.opportunities
    )
    with pytest.raises(InteractionError, match="ten minutes"):
        asyncio.run(
            preview_interactions(
                services,
                project.project.id,
                InteractionPreviewRequest.model_validate(
                    {
                        "source_revision": 1,
                        "steps": [{"kind": "advance", "delta_ms": 600001}],
                    }
                ),
            )
        )
    with pytest.raises(InteractionError, match="checkpoint"):
        asyncio.run(
            preview_interactions(
                services,
                project.project.id,
                InteractionPreviewRequest(
                    source_revision=1,
                    frame_index=1,
                ),
            )
        )


def test_preview_preserves_competing_priority_and_explains_repetition_wait(tmp_path: Path) -> None:
    baseline = original()
    configured = apply_interaction_edit(baseline, edit(baseline, prepare_completion=True))
    low = configured.opportunities.opportunities[-1]
    high = low.model_copy(
        update={
            "id": "original-higher-priority",
            "priority": 99,
            "scene_id": configured.scenes[2].id,
        }
    )
    configured = configured.model_copy(
        update={
            "opportunities": OpportunityProgramDefinition(
                schema_version=1,
                opportunities=(low, high),
            )
        }
    )
    _, services, project = services_for(tmp_path, configured)
    result = asyncio.run(
        preview_interactions(
            services,
            project.project.id,
            InteractionPreviewRequest.model_validate(
                {
                    "source_revision": 1,
                    "steps": [
                        {"kind": "visitor", "camera": "porch", "zone": "walkway"},
                        {"kind": "advance", "delta_ms": 1000},
                        {"kind": "advance", "delta_ms": 179000},
                        {
                            "kind": "visitor",
                            "camera": "porch",
                            "zone": "walkway",
                            "observation": "leave",
                        },
                        {"kind": "advance", "delta_ms": 1100},
                        {
                            "kind": "visitor",
                            "camera": "porch",
                            "zone": "walkway",
                            "object_id": "second-visitor",
                        },
                        {"kind": "advance", "delta_ms": 1000},
                    ],
                }
            ),
        )
    )
    assert result["trace"][3]["status"]["active_scene"] == high.scene_id
    assert result["trace"][3]["status"]["opportunities"]["consumed_count"] == 1
    qualifying = result["trace"][-1]["status"]["opportunities"]["qualifying"]
    assert any("repeat_wait" in value["blocked_by"] for value in qualifying)


def test_state_sequence_scene_edit_preserves_every_other_transition_field(tmp_path: Path) -> None:
    baseline = original().model_copy(
        update={
            "encounter": EncounterDefinition(
                schema_version=1,
                initial_state="idle",
                cooldown_state="resting",
                ambient_scene_id="baseline-timeline",
                encounter_timeout_ms=30000,
                cooldown_ms=5000,
                debounce_ms=100,
                event_ttl_ms=5000,
                transitions=(
                    {
                        "event_type": "audience.arrived",
                        "from_states": ("idle",),
                        "to_state": "engaged",
                        "scene_id": "legacy-sweep",
                        "min_state_age_ms": 500,
                        "attributes": {"audience": True},
                    },
                ),
            )
        }
    )
    result = apply_interaction_edit(
        baseline,
        InteractionEditRequest(
            source_revision=1,
            operation="transition",
            transition_index=0,
            scene_id="legacy-pulse",
        ),
    )
    expected = baseline.model_dump(mode="json")
    expected["encounter"]["transitions"][0]["scene_id"] = "legacy-pulse"
    assert result.model_dump(mode="json") == expected
    assert describe_interactions(result)["cards"][-1]["kind"] == "transition"


def test_saved_visitor_scenario_reuses_preview_normalization_and_clock(tmp_path: Path) -> None:
    baseline = original()
    configured = apply_interaction_edit(baseline, edit(baseline, prepare_completion=True))
    _, services, project = services_for(tmp_path, configured)
    compiled = DraftCompiler(tmp_path / "test-compile").compile(
        project_id=project.project.id,
        revision=1,
        draft=project.draft,
    )
    steps = (
        {"kind": "visitor", "camera": "porch", "zone": "walkway"},
        {"kind": "advance", "delta_ms": 1000},
    )
    saved = services.simulation_scenarios.save(
        project_id=project.project.id,
        name="Original visitor regression",
        source_revision=1,
        steps=steps,
        expected={},
    )
    result = asyncio.run(
        services.simulation_scenarios.run(saved, load_show_package(compiled.package_root))
    )
    preview = asyncio.run(
        preview_interactions(
            services,
            project.project.id,
            InteractionPreviewRequest.model_validate(
                {
                    "source_revision": 1,
                    "steps": steps,
                }
            ),
        )
    )
    assert result["actual"]["clock_ms"] == 1000
    assert result["actual"]["visitor_checkpoints"][-1]["status"] == preview["trace"][-1]["status"]
    assert result["hardware_commands_sent"] is False


def test_output_offset_does_not_change_reported_scene_clock(tmp_path: Path) -> None:
    baseline = original()
    first = baseline.scenes[0]
    output = first.lighting[0].model_copy(update={"timing_offset_ms": 5000})
    modified = first.model_copy(update={"lighting": (output,)})
    draft = baseline.model_copy(update={"scenes": (modified, *baseline.scenes[1:])})
    _, services, project = services_for(tmp_path, draft)
    result = asyncio.run(
        preview_interactions(
            services,
            project.project.id,
            InteractionPreviewRequest.model_validate(
                {
                    "source_revision": 1,
                    "steps": [{"kind": "advance", "delta_ms": 1000}],
                }
            ),
        )
    )
    assert result["frame"]["timestamp_ms"] == 1000
    assert result["frame"]["outputs"][0]["output_position_ms"] == 0


def test_rest_mcp_guided_edit_preview_and_auth_boundaries_match(tmp_path: Path) -> None:
    baseline = original()
    runtime, services, project = services_for(tmp_path, baseline)

    def token(scopes: tuple[McpScope, ...]) -> str:
        return services.mcp_store.create_token(
            client_id="original-visitor-client",
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

    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        path = f"/api/gui/v1/projects/{project.project.id}"
        assert client.get(path + "/interactions").status_code == 401
        client.post(
            "/api/gui/v1/auth/login",
            json={"username": "admin", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["cueverge_csrf"]}
        request = edit(baseline, prepare_completion=True).model_dump(mode="json")
        assert client.put(path + "/interactions", json=request).status_code == 403
        readonly = token((McpScope.READ,))
        assert tool(client, readonly, "update_interaction", edit=request)["isError"]
        assert tool(
            client, readonly, "preview_interaction_workflow", request={"source_revision": 1}
        )["isError"]
        granted = token((McpScope.READ, McpScope.DIRECT_EDIT, McpScope.SIMULATION))
        response = tool(client, granted, "update_interaction", edit=request)
        assert response["structuredContent"]["revision"] == 2
        overview = client.get(path + "/interactions").json()
        assert overview == tool(client, granted, "get_interactions")["structuredContent"]
        preview = {
            "source_revision": 2,
            "steps": [{"kind": "visitor", "camera": "porch", "zone": "walkway"}],
        }
        rest = client.post(path + "/interaction-preview", json=preview, headers=headers)
        assert rest.status_code == 200
        mcp = tool(client, granted, "preview_interaction_workflow", request=preview)[
            "structuredContent"
        ]
        assert rest.json() == mcp
        assert mcp["hardware_commands_sent"] is False
        assert client.put(path + "/interactions", json=request, headers=headers).status_code == 409
        assert runtime.active_scene is None
