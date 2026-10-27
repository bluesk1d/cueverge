import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime, ShowMode
from cueverge.core.loader import load_show_package
from cueverge.core.models import MultiLightingLooksDefinition
from cueverge.gui.apply import ApplyError, ApplyRequest
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.mcp_authoring import McpScope
from cueverge.gui.passwords import hash_password
from cueverge.gui.service import GuiServices
from cueverge.lighting.sink import MemoryFrameSink, RenderedFrame
from cueverge.protocol.device_v2 import KeepAwakeStatus
from tests.unit.test_device_players import _connect
from tests.unit.test_device_transport import _enrolled_device

PASSWORD = "original apply regression"
PASSWORD_HASH = hash_password(PASSWORD)
ORIGIN = "http://localhost:5173"


def setup(tmp_path: Path):
    base = ControllerRuntime()
    resource = next(
        item.resource for item in base.catalog.installation.resources if item.kind == "pixel_output"
    )
    draft = DraftProject.model_validate(
        {
            "schema_version": 1,
            "installation": base.catalog.installation.model_dump(mode="json"),
            "scenes": [
                {
                    "schema_version": 2,
                    "id": "ambient",
                    "name": "Original scene",
                    "mode": "controller_timed",
                    "clock_source": "controller",
                    "media": [],
                    "resources": [resource],
                    "duration_ms": 60000,
                    "lighting": [{"id": "leds", "resource": resource, "effect": "synthetic_sweep"}],
                }
            ],
        }
    )
    initial = DraftCompiler(tmp_path / "initial").compile(
        project_id="initial", revision=1, draft=draft
    )
    runtime = ControllerRuntime(catalog=load_show_package(initial.package_root))
    services = GuiServices.create(
        GuiConfig(
            data_root=tmp_path / "gui",
            admin_password_hash=SecretStr(PASSWORD_HASH),
            allowed_origins=(ORIGIN,),
            secure_cookies=False,
        ),
        runtime,
    )
    candidate = draft.model_copy(
        update={
            "scenes": (draft.scenes[0].model_copy(update={"name": "Applied scene"}),),
            "editor_metadata": {
                "installation_id": services.repository.get_runtime_installation().id
            },
        }
    )
    stored = services.repository.create_project(
        name="Apply fixture",
        draft=candidate,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    return runtime, services, stored


async def wait(services: GuiServices, job_id: str):
    for _ in range(100):
        job = services.apply.get(job_id)
        if job["state"] in {"completed", "failed", "cancelled"}:
            return job
        await asyncio.sleep(0.01)
    pytest.fail("Apply did not reach a durable terminal state")


def request(**changes):
    return ApplyRequest(
        source_revision=1,
        expected_head_revision=1,
        confirm_interruption=True,
        approve_release=True,
        starting_scene_id="ambient",
        **changes,
    )


@pytest.mark.parametrize("running", [False, True])
def test_apply_replaces_actual_catalog_and_acknowledges_loaded_identity(
    tmp_path: Path, running: bool
):
    runtime, services, stored = setup(tmp_path)

    async def scenario():
        if running:
            await runtime.start_scene("ambient")
        before = runtime.active_scene.scene_instance_id if runtime.active_scene else None
        job = await services.apply.start(stored.project.id, request(), "admin")
        result = await wait(services, str(job["id"]))
        assert result["state"] == "completed", result
        assert runtime.catalog.require("ambient").definition.name == "Applied scene"
        assert runtime.loaded_catalog["project_id"] == stored.project.id
        assert runtime.loaded_catalog["revision"] == 1
        assert result["loaded_catalog"] == runtime.loaded_catalog
        if running:
            assert runtime.active_scene is not None
            assert runtime.active_scene.scene_instance_id != before
            assert runtime.status()["playback_state"] == "playing"
        else:
            assert runtime.active_scene is None
            assert runtime.show_mode is ShowMode.DISABLED
        assert services.repository.get_revision(stored.project.id).draft == stored.draft

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value",
    [
        ("approve_release", False),
        ("confirm_interruption", False),
        ("expected_head_revision", 2),
        ("source_revision", 2),
        ("starting_scene_id", "missing"),
    ],
)
def test_invalid_apply_never_changes_catalog(tmp_path: Path, field: str, value: object):
    runtime, services, stored = setup(tmp_path)
    original = runtime.catalog

    async def scenario():
        candidate = ApplyRequest.model_validate({**request().model_dump(), field: value})
        try:
            job = await services.apply.start(stored.project.id, candidate, "admin")
        except ApplyError:
            return
        result = await wait(services, str(job["id"]))
        assert result["state"] == "failed", result

    asyncio.run(scenario())
    assert runtime.catalog is original
    assert runtime.loaded_catalog["state"] == "unreported"


@pytest.mark.parametrize("change", ["cancel", "stop", "authorization", "dependencies"])
def test_staging_rechecks_cancellation_permissions_and_dependencies(
    tmp_path: Path, change: str, monkeypatch
):
    runtime, services, stored = setup(tmp_path)
    original = runtime.catalog
    stage = services.publication.stage_runtime
    authorized = True
    digest = "before"
    services.apply.authorize = lambda *_: authorized
    services.apply.dependency_digest = lambda *_: digest

    async def changed_stage(*args, **kwargs):
        nonlocal authorized, digest
        result = await stage(*args, **kwargs)
        active = services.apply.active()
        assert active is not None
        if change == "cancel":
            services.apply.cancel(str(active["id"]))
        elif change == "stop":
            services.apply.stop_requested()
            await runtime.set_show_mode(ShowMode.DISABLED)
        elif change == "authorization":
            authorized = False
        else:
            digest = "after"
        return result

    monkeypatch.setattr(services.publication, "stage_runtime", changed_stage)

    async def scenario():
        job = await services.apply.start(stored.project.id, request(), "admin")
        result = await wait(services, str(job["id"]))
        assert result["state"] in {"failed", "cancelled"}, result
        assert not result["interrupted"]

    asyncio.run(scenario())
    assert runtime.catalog is original


def test_recovery_verifies_retained_files_and_never_autostarts(tmp_path: Path):
    runtime, services, stored = setup(tmp_path)

    async def scenario():
        job = await services.apply.start(stored.project.id, request(), "admin")
        result = await wait(services, str(job["id"]))
        assert result["state"] == "completed", result
        await services.apply.recover()
        assert runtime.active_scene is None
        assert runtime.show_mode is ShowMode.DISABLED
        assert runtime.loaded_catalog["state"] == "loaded"
        package = services.publication.get(str(result["package_id"]))
        root = services.apply._verified_root(package)
        (root / "scenes" / next((root / "scenes").iterdir()).name).write_text("{}")
        await services.apply.recover()
        assert runtime.loaded_catalog["state"] == "recovery_required"
        assert runtime.active_scene is None

    asyncio.run(scenario())


def test_apply_rest_requires_csrf_and_mcp_requires_both_explicit_scopes(tmp_path: Path):
    runtime, services, stored = setup(tmp_path)
    current = datetime.now(UTC)
    token = services.mcp_store.create_token(
        client_id="apply-test",
        username="admin",
        project_id=stored.project.id,
        scopes=(McpScope.READ, McpScope.ACTIVATE),
        expires_at=(current + timedelta(hours=1)).isoformat(),
        occurred_at=current.isoformat(),
    )
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:
        assert (
            client.post(
                "/api/gui/v1/auth/login",
                json={"username": "admin", "password": PASSWORD},
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        path = f"/api/gui/v1/projects/{stored.project.id}/apply"
        assert client.post(path, json=request().model_dump()).status_code == 403
        result = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "apply_saved_show",
                    "arguments": {"request": request().model_dump()},
                },
            },
            headers={
                "Authorization": f"Bearer {token.token}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert result.status_code == 200
        assert result.json()["result"]["isError"]
        assert "publish" in str(result.json()["result"])
        transport = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "operate_loaded_show",
                    "arguments": {
                        "request": {"action": "stop"},
                        "confirm_physical_output": True,
                    },
                },
            },
            headers={
                "Authorization": f"Bearer {token.token}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert transport.json()["result"]["isError"]
        assert "live_operation" in str(transport.json()["result"])
        result = client.post(
            path,
            json=request().model_dump(),
            headers={"Origin": ORIGIN, "X-CSRF-Token": client.cookies["cueverge_csrf"]},
        )
        assert result.status_code == 202, result.text
        job_id = result.json()["id"]
        for _ in range(100):
            status = client.get(f"{path}-jobs/{job_id}").json()
            if status["state"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert status["state"] == "completed", status
        observed = client.get("/api/gui/v1/runtime/status").json()
        assert observed["loaded_catalog"]["project_id"] == stored.project.id


def test_monitor_records_only_successful_sends_caps_capture_and_reports_failures(tmp_path: Path):
    runtime, _, _ = setup(tmp_path)
    now = [10.0]
    runtime.clock = lambda: now[0]
    resource = runtime.catalog.require("ambient").lighting_outputs[0].definition.resource
    red = RenderedFrame("test", 0, ((255, 0, 0),) * 5000, resource)
    runtime._send_output_frame(red)
    value = runtime.output_monitor()
    assert value["source"] == "successful_controller_sink_frames"
    output = value["outputs"][0]
    assert output["colors"] == [list(color) for color in red.pixels[:1000]]
    assert output["held"]
    assert output["next_pixel_offset"] == 1000
    now[0] += 0.1
    green = RenderedFrame("test", 1, ((0, 255, 0),) * 5000, resource)
    runtime._send_output_frame(green)
    assert runtime.output_monitor()["outputs"][0]["colors"] == [
        list(color) for color in red.pixels[:1000]
    ]
    now[0] += 0.11
    assert runtime.output_monitor()["outputs"][0]["colors"] == [
        list(color) for color in green.pixels[:1000]
    ]
    assert runtime.output_monitor(resource, 1000)["outputs"][0]["pixel_offset"] == 1000
    assert isinstance(runtime.sink, MemoryFrameSink)
    runtime.sink.failure = "original send failure"
    with pytest.raises(OSError, match="original send failure"):
        runtime._send_output_frame(red)
    assert runtime._last_output_frames[resource][1] is green
    assert len(runtime.sink.frames) == 2
    for limit in [0, 1001]:
        with pytest.raises(ValueError):
            runtime.output_monitor(pixel_limit=limit)


@pytest.mark.parametrize("preserve", [False, True])
def test_apply_preserves_controller_looks_unless_explicitly_cleared(tmp_path: Path, preserve: bool):
    runtime, services, stored = setup(tmp_path)
    resource = stored.draft.scenes[0].lighting[0].resource
    looks = MultiLightingLooksDefinition.model_validate(
        {
            "schema_version": 2,
            "outputs": [
                {
                    "resource": resource,
                    "cycle_ms": 1000,
                    "baseline": [
                        {
                            "id": "baseline",
                            "primitive": "solid",
                            "start_ms": 0,
                            "duration_ms": 1000,
                            "color": "#123456",
                        }
                    ],
                    "safe_state": [
                        {"id": "safe", "primitive": "blackout", "start_ms": 0, "duration_ms": 1000}
                    ],
                }
            ],
        }
    )
    initial = services.compiler.compile(
        project_id="looks", revision=1, draft=stored.draft, lighting_looks=looks
    )
    runtime.catalog = load_show_package(initial.package_root)

    async def scenario():
        job = await services.apply.start(
            stored.project.id, request(preserve_controller_looks=preserve), "admin"
        )
        result = await wait(services, str(job["id"]))
        assert result["state"] == "completed", result
        assert bool(runtime.catalog.lighting_looks_by_resource) is preserve
        if preserve:
            assert (
                runtime.catalog.lighting_looks_by_resource[resource].definition == looks.outputs[0]
            )

    asyncio.run(scenario())


def test_retained_restore_loads_previous_version_without_overwriting_saved_head(tmp_path: Path):
    runtime, services, stored = setup(tmp_path)

    async def scenario():
        job = await services.apply.start(stored.project.id, request(), "admin")
        first = await wait(services, str(job["id"]))
        assert first["state"] == "completed", first
        updated = services.repository.update_draft(
            project_id=stored.project.id,
            expected_revision=1,
            draft=stored.draft.model_copy(
                update={
                    "scenes": (
                        stored.draft.scenes[0].model_copy(update={"name": "Second version"}),
                    )
                }
            ),
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=datetime.now(UTC).isoformat(),
        )
        job = await services.apply.start(
            stored.project.id,
            ApplyRequest(
                source_revision=2,
                expected_head_revision=2,
                approve_release=True,
                confirm_interruption=True,
            ),
            "admin",
        )
        assert (await wait(services, str(job["id"])))["state"] == "completed"
        assert runtime.catalog.require("ambient").definition.name == "Second version"
        job = await services.apply.start(
            stored.project.id,
            ApplyRequest(
                source_revision=1,
                expected_head_revision=2,
                restore_package_id=str(first["package_id"]),
                confirm_interruption=True,
            ),
            "admin",
        )
        restored = await wait(services, str(job["id"]))
        assert restored["state"] == "completed", restored
        assert runtime.catalog.require("ambient").definition.name == "Applied scene"
        assert runtime.loaded_catalog["revision"] == 1
        assert services.repository.get_revision(stored.project.id).draft == updated.draft

    asyncio.run(scenario())


def test_stop_during_handover_callback_cannot_restart_the_show(tmp_path: Path):
    runtime, _, _ = setup(tmp_path)
    original = runtime.catalog

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def before_load():
            entered.set()
            await release.wait()

        task = asyncio.create_task(
            runtime.replace_catalog(
                original,
                identity={"state": "unreported"},
                start_scene_id="ambient",
                before_load=before_load,
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        await runtime.set_show_mode(ShowMode.DISABLED)
        release.set()
        with pytest.raises(RuntimeError, match="cancelled by Stop"):
            await task
        assert runtime.catalog is original
        assert runtime.active_scene is None

    asyncio.run(scenario())


def test_managed_original_media_binds_to_digest_and_missing_optional_device_is_explicit(
    tmp_path: Path,
):
    runtime, services, stored = setup(tmp_path)
    stream = BytesIO()
    Image.new("RGB", (8, 8), "#123456").save(stream, format="PNG")
    content = stream.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    occurred = datetime.now(UTC).isoformat()
    asset, upload = services.asset_upload.begin(
        project_id=stored.project.id,
        name="Original synthetic media",
        original_filename="original.png",
        media_type="image/png",
        total_bytes=len(content),
        expected_sha256=digest,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=occurred,
    )
    services.asset_upload.append(
        upload_id=upload.id,
        offset=0,
        chunk=content,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=occurred,
    )
    services.asset_upload.process_asset(asset.id, occurred_at=occurred)
    media_resource = next(
        item.resource for item in stored.draft.installation.resources if item.kind == "media_agent"
    )
    document = stored.draft.model_dump(mode="json")
    document["scenes"][0]["resources"].append(media_resource)
    document["scenes"][0]["media"] = [
        {"id": "optional-media", "resource": media_resource, "asset": asset.id, "required": False}
    ]
    candidate = services.repository.update_draft(
        project_id=stored.project.id,
        expected_revision=1,
        draft=DraftProject.model_validate(document),
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=occurred,
    )

    async def scenario():
        job = await services.apply.start(
            stored.project.id,
            ApplyRequest(
                source_revision=2,
                expected_head_revision=2,
                confirm_interruption=True,
                approve_release=True,
            ),
            "admin",
        )
        result = await wait(services, str(job["id"]))
        assert result["state"] == "completed", result
        assert result["warnings"]
        assert runtime.catalog.require("ambient").media_outputs[0].asset_id == digest
        assert services.repository.get_revision(stored.project.id).draft == candidate.draft
        assert candidate.draft.scenes[0].media[0].asset == asset.id
        package = services.publication.get(str(result["package_id"]))
        member = package.manifest["media_members"][0]
        assert member["source_asset_id"] == asset.id
        assert member["digest_sha256"] == digest

    asyncio.run(scenario())


def test_paused_apply_remains_stopped_and_partial_commit_failure_restores_old_catalog(
    tmp_path: Path, monkeypatch
):
    runtime, services, stored = setup(tmp_path)

    async def scenario():
        await runtime.start_scene("ambient")
        await runtime.pause()
        assert runtime.status()["playback_state"] == "paused"
        job = await services.apply.start(stored.project.id, request(), "admin")
        result = await wait(services, str(job["id"]))
        assert result["state"] == "completed", result
        assert not result["running"]
        assert runtime.active_scene is None
        original = runtime.catalog
        previous = services.apply._current()

        def fail(*_):
            raise RuntimeError("original metadata commit failure")

        monkeypatch.setattr(services.publication, "record_runtime_load", fail)
        job = await services.apply.start(stored.project.id, request(), "admin")
        failed = await wait(services, str(job["id"]))
        assert failed["state"] == "failed"
        assert "original metadata commit failure" in failed["error"]
        assert runtime.catalog is original
        assert runtime.active_scene is None
        assert services.apply._current() == previous

    asyncio.run(scenario())


def test_mcp_apply_monitor_and_explicit_live_transport_end_to_end(tmp_path: Path):
    runtime, services, stored = setup(tmp_path)
    now = datetime.now(UTC)

    def token(scopes, project_id=stored.project.id):
        return services.mcp_store.create_token(
            client_id="end-to-end",
            username="admin",
            project_id=project_id,
            scopes=scopes,
            expires_at=(now + timedelta(hours=1)).isoformat(),
            occurred_at=now.isoformat(),
        )

    granted = token((McpScope.READ, McpScope.PUBLISH, McpScope.ACTIVATE, McpScope.LIVE_OPERATION))
    read = token((McpScope.READ,))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:

        def tool(secret, name, **arguments):
            result = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers={
                    "Authorization": f"Bearer {secret.token}",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert result.status_code == 200, result.text
            return result.json()["result"]

        assert tool(read, "get_output_monitor")["isError"]
        accepted = tool(granted, "apply_saved_show", request=request().model_dump())[
            "structuredContent"
        ]
        for _ in range(100):
            result = services.apply.get(accepted["id"])
            if result["state"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert result["state"] == "completed", result
        foreign = services.repository.create_project(
            name="Other original show",
            draft=stored.draft,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=now.isoformat(),
        )
        foreign_token = token((McpScope.READ,), project_id=foreign.project.id)
        assert tool(foreign_token, "get_output_monitor")["isError"]
        capabilities = tool(read, "get_capabilities")["structuredContent"]
        assert capabilities["output_monitor"]["capture_maximum_hz"] == 5
        assert capabilities["live_transport"]["physical_confirmation_required"]
        players = capabilities["device_players"]
        assert players["control_tool"] == "control_device_player"
        assert players["scope"] == "live_operation"
        assert capabilities["features"]["device_player_control"] is True
        _, _, device_capabilities = _enrolled_device(tmp_path / "gui")
        channel = _connect(services.repository, services.device_sessions, device_capabilities)
        listed = tool(read, "get_device_players")["structuredContent"]
        assert listed["players"][0]["player_state"] == "stopped"
        assert listed["players"][0]["player_control"] is True
        assert listed["players"][0]["keep_awake"] is None
        assert players["keep_awake"]["configured_on_host"] == "CUEVERGE_KEEP_AWAKE"
        assert players["keep_awake"]["controller_can_change"] is False
        updates = capabilities["agent_updates"]
        assert (updates["read_tool"], updates["update_tool"]) == (
            "get_agent_updates",
            "update_device_agents",
        )
        assert updates["scope"] == "device_update"
        assert updates["requires_idle_maintenance"] is True
        assert capabilities["features"]["managed_agent_updates"] is True
        _connect(
            services.repository,
            services.device_sessions,
            device_capabilities,
            keep_awake=KeepAwakeStatus(mode="system", active=False, detail="logind refused"),
        )
        listed = tool(read, "get_device_players")["structuredContent"]
        assert listed["players"][0]["keep_awake"] == {
            "mode": "system",
            "active": False,
            "detail": "logind refused",
        }
        channel = _connect(services.repository, services.device_sessions, device_capabilities)
        device_id = listed["players"][0]["device_id"]
        for secret, confirm in ((read, True), (granted, False)):
            assert tool(
                secret,
                "control_device_player",
                device_id=device_id,
                action="start",
                confirm_physical_output=confirm,
            )["isError"]
        assert channel.commands == []
        player = tool(
            granted,
            "control_device_player",
            device_id=device_id,
            action="start",
            confirm_physical_output=True,
        )
        assert player["structuredContent"]["player_state"] == "ready", player
        player = tool(
            granted,
            "control_device_player",
            device_id=device_id,
            action="stop",
            confirm_physical_output=True,
        )
        assert player["structuredContent"]["player_state"] == "stopped", player
        assert channel.commands == ["device.player.start", "device.player.stop"]
        missing = tool(
            granted,
            "control_device_player",
            device_id=str(uuid4()),
            action="start",
            confirm_physical_output=True,
        )
        assert "Device not found" in missing["content"][0]["text"]
        audited = {
            (event.action, event.result) for event in services.repository.list_audit_events()
        }
        assert ("mcp.device.player.start", "success") in audited
        assert ("mcp.device.player.stop", "success") in audited
        assert ("mcp.device.player.start", "error") in audited
        assert tool(
            read, "operate_loaded_show", request={"action": "start"}, confirm_physical_output=True
        )["isError"]
        assert tool(
            granted, "operate_loaded_show", request={"action": "start", "scene_id": "ambient"}
        )["isError"]
        started = tool(
            granted,
            "operate_loaded_show",
            request={"action": "start", "scene_id": "ambient"},
            confirm_physical_output=True,
        )
        assert not started.get("isError"), started
        time.sleep(0.25)
        snapshot = tool(read, "get_output_monitor")["structuredContent"]
        assert snapshot["hardware_commands_sent"] is False
        assert snapshot["outputs"][0]["source"] == "scene"
        assert snapshot["outputs"][0]["colors"]
        stopped = tool(
            granted, "operate_loaded_show", request={"action": "stop"}, confirm_physical_output=True
        )
        assert not stopped.get("isError"), stopped
        assert runtime.active_scene is None
        assert runtime.show_mode is ShowMode.DISABLED
        services.mcp_store.revoke_token(granted.record.id, now.isoformat())
        revoked = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": f"Bearer {granted.token}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert revoked.status_code == 401


@pytest.mark.parametrize("change", ["head", "token"])
def test_real_saved_head_and_mcp_revocation_are_rechecked_after_transfer(
    tmp_path: Path, monkeypatch, change: str
):
    runtime, services, stored = setup(tmp_path)
    now = datetime.now(UTC)
    token = services.mcp_store.create_token(
        client_id="staging-recheck",
        username="admin",
        project_id=stored.project.id,
        scopes=(McpScope.READ, McpScope.PUBLISH, McpScope.ACTIVATE),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        occurred_at=now.isoformat(),
    )
    stage = services.publication.stage_runtime
    original = runtime.catalog

    async def changed_stage(*args, **kwargs):
        result = await stage(*args, **kwargs)
        if change == "token":
            services.mcp_store.revoke_token(token.record.id, now.isoformat())
        else:
            services.repository.update_draft(
                project_id=stored.project.id,
                expected_revision=1,
                draft=stored.draft.model_copy(
                    update={
                        "scenes": (
                            stored.draft.scenes[0].model_copy(update={"name": "Concurrent edit"}),
                        )
                    }
                ),
                actor="admin",
                role=Role.ADMINISTRATOR,
                occurred_at=now.isoformat(),
            )
        return result

    monkeypatch.setattr(services.publication, "stage_runtime", changed_stage)

    async def scenario():
        job = await services.apply.start(stored.project.id, request(), "admin", token.record.id)
        result = await wait(services, str(job["id"]))
        assert result["state"] == "failed", result
        assert not result["interrupted"]
        assert runtime.catalog is original

    asyncio.run(scenario())


def test_background_visitor_timers_cannot_restart_a_disabled_show(tmp_path: Path):
    runtime, _, _ = setup(tmp_path)
    timer = AsyncMock()
    runtime.encounter = SimpleNamespace(tick=timer)

    async def scenario():
        for mode in (ShowMode.DISABLED, ShowMode.MAINTENANCE, ShowMode.BLACKOUT):
            await runtime.set_show_mode(mode)
            await runtime.tick_encounter()
        timer.assert_not_awaited()
        assert runtime.active_scene is None
        await runtime.set_show_mode(ShowMode.AUTOMATIC)
        await runtime.tick_encounter()
        timer.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["identity", "record"])
def test_startup_derives_identity_from_verified_package_and_reports_corrupt_pointer(
    tmp_path: Path, corruption: str
):
    runtime, services, stored = setup(tmp_path)

    async def scenario():
        job = await services.apply.start(stored.project.id, request(), "admin")
        result = await wait(services, str(job["id"]))
        assert result["state"] == "completed", result
        if corruption == "identity":
            services.apply._set_current(
                {
                    "package_id": result["package_id"],
                    "identity": {"state": "loaded", "project_id": "wrong-project", "revision": 999},
                }
            )
        else:
            with services.apply._connect() as connection:
                connection.execute("UPDATE applied_catalog SET document='[]' WHERE singleton=1")
        await services.apply.recover()
        assert runtime.active_scene is None
        assert runtime.show_mode is ShowMode.DISABLED
        if corruption == "identity":
            assert runtime.loaded_catalog["project_id"] == stored.project.id
            assert runtime.loaded_catalog["revision"] == 1
        else:
            assert runtime.loaded_catalog["state"] == "recovery_required"
            with pytest.raises(ApplyError, match="consistent controller backup"):
                await services.apply.start(stored.project.id, request(), "admin")

    asyncio.run(scenario())


def test_default_controller_history_is_bounded_but_explicit_simulation_history_is_preserved(
    tmp_path: Path,
):
    runtime, _, _ = setup(tmp_path)
    assert isinstance(runtime.sink, MemoryFrameSink)
    simulation = MemoryFrameSink()
    for index in range(200):
        frame = RenderedFrame("history", index, ((0, 0, 0),), "synthetic")
        runtime.sink.send(frame)
        simulation.send(frame)
    assert len(runtime.sink.frames) == 120
    assert runtime.sink.frames[0].position_ms == 80
    assert len(simulation.frames) == 200
