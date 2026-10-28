import asyncio
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cueverge.controller import timing_calibration as calibration_module
from cueverge.controller.runtime import ControllerRuntime, ShowMode
from cueverge.controller.timing_calibration import (
    CalibrationLightTarget,
    CalibrationMediaTarget,
    TimingCalibrationRun,
    TimingCalibrationRunError,
)
from cueverge.core.loader import load_show_package
from cueverge.gui.contracts import Role
from cueverge.gui.installation_timing import (
    InstallationTimingError,
    InstallationTimingService,
    migrate_output_timing,
)
from cueverge.gui.repository import GuiRepository
from cueverge.lighting.sink import MemoryFrameSink
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.models import CommandAck, PlaybackState
from tests.unit.test_multi_output_media import ManualClock, MemoryCommandChannel

NOW = "2026-10-28T00:00:00+00:00"


def test_migration_carries_per_surface_delays_to_each_output_once(tmp_path: Path) -> None:
    database = tmp_path / "gui.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE installations (id TEXT PRIMARY KEY);
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);
            CREATE TABLE installation_geometry (id TEXT PRIMARY KEY, installation_id TEXT);
            CREATE TABLE projector_surface_mappings (
                geometry_id TEXT, resource TEXT, media_start_delay_ms INTEGER);
            CREATE TABLE lighting_surfaces (
                geometry_id TEXT, resource TEXT, timing_offset_ms INTEGER);
            INSERT INTO installations VALUES ('home');
            INSERT INTO installation_geometry VALUES
                ('p1', 'home'), ('p2', 'home'), ('p3', 'home'),
                ('l1', 'home'), ('l2', 'home'), ('l3', 'home');
            INSERT INTO projector_surface_mappings VALUES
                ('p1', 'projector.left', 40), ('p2', 'projector.left', 40),
                ('p3', 'projector.right', -30);
            INSERT INTO lighting_surfaces VALUES
                ('l1', 'wled', -20), ('l2', 'wled', 60), ('l3', 'porch', 0);
            """
        )
    repository: Any = SimpleNamespace(database_path=database)
    migrate_output_timing(repository)

    def stored() -> dict[str, tuple[str, int]]:
        with sqlite3.connect(database) as connection:
            return {
                resource: (kind, delay)
                for resource, kind, delay in connection.execute(
                    "SELECT resource, kind, delay_ms FROM installation_output_timing"
                )
            }

    # Agreeing surfaces keep their value, disagreeing ones take the largest, and projector
    # start delays are never negative (a zero result stores nothing).
    assert stored() == {"projector.left": ("media", 40), "wled": ("lighting", 60)}
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE lighting_surfaces SET timing_offset_ms = 999")
    migrate_output_timing(repository)
    assert stored()["wled"] == ("lighting", 60)


def test_output_timing_is_revisioned_and_range_checked(tmp_path: Path) -> None:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    installation = repository.ensure_runtime_installation(name="home", occurred_at=NOW)
    service = InstallationTimingService(repository)
    outputs = (("projector.left", "media"), ("wled", "lighting"))
    assert [
        (item.resource, item.delay_ms, item.revision)
        for item in service.list(
            installation.id,
            outputs,  # type: ignore[arg-type]
        )
    ] == [("projector.left", 0, 0), ("wled", 0, 0)]

    def set_delay(resource: str, kind: Any, delay: int, revision: int | None) -> Any:
        return service.set(
            installation_id=installation.id,
            resource=resource,
            kind=kind,
            delay_ms=delay,
            expected_revision=revision,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at=NOW,
        )

    assert set_delay("wled", "lighting", -120, 0).revision == 1
    with pytest.raises(InstallationTimingError, match="changed since"):
        set_delay("wled", "lighting", 10, 0)
    with pytest.raises(InstallationTimingError, match="between 0 and 5000"):
        set_delay("projector.left", "media", -1, None)
    set_delay("projector.left", "media", 85, None)
    assert service.delays(installation.id) == {"projector.left": 85, "wled": -120}


def _package(tmp_path: Path, *, media_clock: bool = False, scene_offset: int = 0) -> Path:
    (tmp_path / "scenes").mkdir(parents=True)
    (tmp_path / "installation.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resources": [
                    {
                        "resource": "leds",
                        "kind": "pixel_output",
                        "target": "memory",
                        "pixel_count": 8,
                    },
                    {"resource": "video", "kind": "media_agent", "target": "player"},
                ],
            }
        )
    )
    scene = {
        "schema_version": 2,
        "id": "preshow",
        "name": "Pre-show",
        "mode": "media_synchronized" if media_clock else "controller_timed",
        "clock_source": "video" if media_clock else "controller",
        "duration_ms": 4000,
        "resources": ["leds", "video"],
        "media": [{"id": "video", "resource": "video", "asset": "clip.mp4"}],
        "lighting": [
            {
                "id": "leds",
                "resource": "leds",
                "effect": "synthetic_sweep",
                "timing_offset_ms": scene_offset,
            }
        ],
    }
    (tmp_path / "scenes/preshow.json").write_text(json.dumps(scene))
    (tmp_path / "media").mkdir()
    (tmp_path / "media/clip.mp4").write_bytes(b"clip")
    return tmp_path


def test_installation_timing_stacks_under_scene_delays(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = ManualClock()
        sink = MemoryFrameSink()
        runtime = ControllerRuntime(
            catalog=load_show_package(_package(tmp_path, scene_offset=100)),
            clock=clock,
            sink=sink,
        )
        agent = FakeMediaAgent(agent_id="player")
        channel = MemoryCommandChannel([])
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        runtime.set_output_timing({"leds": 200, "video": 300})
        try:
            active = await runtime.start_scene("preshow")
            [session] = active.media_sessions
            assert (session.start_delay_ms, session.installation_delay_ms) == (300, 300)
            await runtime.acknowledge_command(agent.acknowledge(channel.sent[0]["request_id"]))
            clock.now += 1.0
            assert runtime.render_frame()
            # Scene offset 100 ms + installation offset 200 ms behind scene time.
            assert sink.frames[-1].position_ms == 700
            status = runtime.status()
            assert status["output_timing"] == {"leds": 200, "video": 300}
            [lighting] = status["lighting_outputs"]
            assert (lighting["timing_offset_ms"], lighting["installation_offset_ms"]) == (100, 200)
            assert status["media_outputs"][0]["installation_delay_ms"] == 300
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_media_clock_does_not_drag_leds_by_its_projector_compensation(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = ManualClock()
        sink = MemoryFrameSink()
        runtime = ControllerRuntime(
            catalog=load_show_package(_package(tmp_path, media_clock=True)),
            clock=clock,
            sink=sink,
        )
        agent = FakeMediaAgent(agent_id="player")
        agent.accept_welcome(runtime.register_agent(agent.hello(), MemoryCommandChannel([])))
        runtime.set_output_timing({"video": 250, "leds": 40})
        try:
            active = await runtime.start_scene("preshow")
            agent.attach(active.scene_instance_id, active.playback_session_id)
            assert runtime.ingest(agent.position(1000, state=PlaybackState.PLAYING)).accepted
            assert runtime.render_frame()
            # The clock projector started 250 ms late to compensate its latency; LEDs follow
            # scene time (1000 + 250) minus only their own 40 ms offset.
            assert sink.frames[-1].position_ms == 1210
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


class _Agents:
    """Agents that answer every calibration command straight away."""

    def __init__(self) -> None:
        self.run: TimingCalibrationRun | None = None
        self.sent: list[tuple[float, str, str]] = []

    def connection(self, agent_id: str):  # type: ignore[no-untyped-def]
        async def send(message: Any) -> None:
            self.sent.append((time.monotonic(), agent_id, message["command"]))
            assert self.run is not None
            ack = CommandAck(
                request_id=message["request_id"],
                agent_id=agent_id,
                agent_session_id="session",
                controller_epoch="epoch",
                status="completed",
            )
            asyncio.get_running_loop().call_soon(self.run.acknowledge, ack)

        return ("session", send)


def test_led_beat_follows_each_outputs_live_delay() -> None:
    sink = MemoryFrameSink()
    run = TimingCalibrationRun(
        sink=sink,
        agents=_Agents(),
        controller_epoch="epoch",
        media=(),
        lights=(CalibrationLightTarget("wled", 3), CalibrationLightTarget("porch", 2)),
        delays={"wled": 0, "porch": 150},
        clock=time.monotonic,
        period_ms=1000,
        flash_ms=100,
    )

    def lit(now: float) -> dict[str, bool]:
        run._frame(now, start=10.0)
        latest = {frame.resource: frame for frame in sink.frames[-2:]}
        return {resource: frame.pixels[0] != (0, 0, 0) for resource, frame in latest.items()}

    assert lit(10.05) == {"wled": True, "porch": False}
    assert lit(10.2) == {"wled": False, "porch": True}
    assert lit(11.02) == {"wled": True, "porch": False}
    run.status.state = "active"
    run.set_delay("wled", 500)  # LED changes take effect on the very next frame.
    assert lit(11.02) == {"wled": False, "porch": False}
    assert lit(11.55) == {"wled": True, "porch": False}
    assert len(sink.frames[-1].pixels) == 2


def test_projectors_start_together_offset_by_their_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calibration_module, "START_LEAD_SECONDS", 0.05)
    monkeypatch.setattr(calibration_module, "RESYNC_DEBOUNCE_SECONDS", 0.0)

    async def scenario() -> None:
        agents = _Agents()
        run = TimingCalibrationRun(
            sink=MemoryFrameSink(),
            agents=agents,
            controller_epoch="epoch",
            media=(
                CalibrationMediaTarget("projector.left", "left", "a" * 64),
                CalibrationMediaTarget("projector.right", "right", "a" * 64),
            ),
            lights=(),
            delays={"projector.left": 0, "projector.right": 120},
            clock=time.monotonic,
        )
        agents.run = run
        run.start()
        await asyncio.sleep(0.4)
        plays = {agent: at for at, agent, command in agents.sent if command == "play"}
        assert [command for _, _, command in agents.sent].count("prepare") == 2
        assert 0.09 <= plays["right"] - plays["left"] <= 0.16
        assert run.status.state == "active"
        assert {output.state for output in run.status.outputs.values()} == {"playing"}

        # A projector change restarts the loop so both start together again.
        agents.sent.clear()
        run.set_delay("projector.right", 0)
        await asyncio.sleep(0.4)
        plays = {agent: at for at, agent, command in agents.sent if command == "play"}
        assert abs(plays["right"] - plays["left"]) < 0.04
        assert run.status.cycles == 2

        await run.stop()
        assert run.status.state == "stopped"
        assert [command for _, _, command in agents.sent][-2:] == ["stop", "stop"]
        with pytest.raises(TimingCalibrationRunError, match="ended"):
            run.set_delay("projector.left", 10)

    asyncio.run(scenario())


def test_runtime_calibration_requires_idle_maintenance_and_owns_outputs(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = ControllerRuntime(
            catalog=load_show_package(_package(tmp_path)), sink=MemoryFrameSink()
        )
        channel = MemoryCommandChannel([])
        agent = FakeMediaAgent(agent_id="player")
        agent.accept_welcome(runtime.register_agent(agent.hello(), channel))
        target = (CalibrationMediaTarget("video", "player", "a" * 64),)
        try:
            with pytest.raises(TimingCalibrationRunError, match="idle maintenance"):
                runtime.start_timing_calibration(media=target, lights=(), delays={})
            await runtime.set_show_mode(ShowMode.MAINTENANCE)
            with pytest.raises(TimingCalibrationRunError, match="LED driver"):
                runtime.start_timing_calibration(media=target, lights=("leds",), delays={})
            status = runtime.start_timing_calibration(media=target, lights=(), delays={"video": 80})
            assert runtime.timing_calibration_active
            assert status.outputs["video"].delay_ms == 80
            with pytest.raises(RuntimeError, match="timing calibration"):
                await runtime.start_scene("preshow")
            with pytest.raises(RuntimeError, match="timing calibration"):
                runtime.validate_catalog_handover(runtime.catalog)
            for _ in range(50):
                if channel.sent and channel.sent[-1]["command"] == "prepare":
                    break
                await asyncio.sleep(0.01)
            prepare = channel.sent[-1]
            assert (prepare["command"], prepare["asset_id"], prepare["end_behavior"]) == (
                "prepare",
                "a" * 64,
                "loop",
            )
            # The run, not the show, receives the agent's acknowledgement.
            decision = await runtime.acknowledge_command(
                CommandAck(
                    request_id=prepare["request_id"],
                    agent_id="player",
                    agent_session_id=agent.agent_session_id,
                    controller_epoch=runtime.controller_epoch,
                    status="completed",
                )
            )
            assert decision.accepted
            assert runtime.status()["timing_calibration"]["state"] in {"starting", "active"}
            await runtime.set_show_mode(ShowMode.DISABLED)
            assert not runtime.timing_calibration_active
            assert runtime.status()["timing_calibration"]["state"] == "stopped"
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_calibration_media_download_requires_an_enrolled_device(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from cueverge.gui.device_app import create_device_app
    from cueverge.gui.device_session import DeviceSessionRegistry
    from tests.unit.test_device_transport import _enrolled_device, _with_peer_certificate

    repository, certificate, _ = _enrolled_device(tmp_path)
    root = tmp_path / "clips"
    root.mkdir()
    digest = "c" * 64
    (root / digest).write_bytes(b"reference clip")
    app = create_device_app(repository, DeviceSessionRegistry(), calibration_media=root)
    url = f"/api/device/v2/calibration-media/{digest}"
    with TestClient(app) as client:
        assert client.get(url).status_code == 403
    with TestClient(_with_peer_certificate(app, certificate)) as client:
        result = client.get(url)
        assert (result.status_code, result.content) == (200, b"reference clip")
        assert client.get(url, headers={"Range": "bytes=10-"}).content == b"clip"
        assert client.get("/api/device/v2/calibration-media/..%2Fgui.sqlite3").status_code in {
            403,
            404,
        }
        assert client.get(f"/api/device/v2/calibration-media/{'d' * 64}").status_code == 403


def test_calibration_service_stages_starts_players_and_saves_accepted_delays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from cueverge.gui.device_players import DevicePlayerService
    from cueverge.gui.device_session import DeviceSessionRegistry
    from cueverge.gui.installation_calibration import (
        InstallationCalibrationError,
        InstallationCalibrationService,
        MediaChoice,
    )
    from cueverge.protocol.device_v2 import (
        DeviceArtifactStageCommand,
        DeviceHello,
        DevicePlayerAck,
        DevicePlayerCommand,
    )
    from tests.unit.test_device_transport import DEVICE_ID, _enrolled_device, _hello

    monkeypatch.setattr(calibration_module, "START_LEAD_SECONDS", 0.05)
    repository, _, capabilities = _enrolled_device(tmp_path)
    repository.set_resource_assignment(
        resource="video",
        device_id=str(DEVICE_ID),
        capability_kind="media_playback",
        device_output=None,
        expected_revision=None,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at=NOW,
    )
    installation = repository.ensure_runtime_installation(name="home", occurred_at=NOW)
    sessions = DeviceSessionRegistry()
    device_messages: list[Any] = []

    class DeviceChannel:
        async def send_device_message(self, message: Any) -> None:
            device_messages.append(message)
            if isinstance(message, DeviceArtifactStageCommand):
                ack: Any = {
                    "request_id": str(message.request_id),
                    "device_session_id": str(message.device_session_id),
                    "controller_epoch": str(message.controller_epoch),
                    "activation_identity": str(message.activation_identity),
                    "digest_sha256": message.digest_sha256,
                    "status": "completed",
                }
            else:
                assert isinstance(message, DevicePlayerCommand)
                ack = DevicePlayerAck(
                    request_id=message.request_id,
                    device_session_id=message.device_session_id,
                    controller_epoch=message.controller_epoch,
                    status="completed",
                    player_state="ready",
                )
                sessions.set_player_state(DEVICE_ID, message.device_session_id, "ready")
            asyncio.get_running_loop().call_soon(
                sessions.acknowledge, DEVICE_ID, message.request_id, ack
            )

    session_id = uuid4()
    sessions.connect(
        repository.get_device(str(DEVICE_ID)),
        DeviceHello.model_validate(
            {
                **_hello(session_id, capabilities),
                "media_features": ["player_control"],
                "player_state": "stopped",
            }
        ),
    )
    sessions.attach_channel(DEVICE_ID, session_id, DeviceChannel())

    async def scenario() -> None:
        runtime = ControllerRuntime(
            catalog=load_show_package(_package(tmp_path / "show")), sink=MemoryFrameSink()
        )
        agent = FakeMediaAgent(agent_id="player")

        class AnsweringAgent:
            async def send_json(self, data: Any) -> None:
                asyncio.get_running_loop().create_task(
                    runtime.acknowledge_command(agent.acknowledge(data["request_id"]))
                )

        agent.accept_welcome(runtime.register_agent(agent.hello(), AnsweringAgent()))
        service = InstallationCalibrationService(
            repository=repository,
            runtime=runtime,
            sessions=sessions,
            players=DevicePlayerService(repository, sessions, runtime),
            timing=InstallationTimingService(repository),
            clip_root=tmp_path / "clips",
            public_url="wss://controller.example:7443/api/device/v2/agent/ws",
        )
        start = {
            "installation_id": installation.id,
            "media": (MediaChoice("video"),),
            "lights": (),
            "actor": "operator",
            "role": Role.OPERATOR,
            "occurred_at": NOW,
        }
        try:
            with pytest.raises(InstallationCalibrationError, match="maintenance"):
                await service.start(**start)
            await runtime.set_show_mode(ShowMode.MAINTENANCE)
            with pytest.raises(InstallationCalibrationError, match="not an LED output"):
                await service.start(**{**start, "lights": ("video",)})
            run = await service.start(**start)
            stage = next(
                item for item in device_messages if isinstance(item, DeviceArtifactStageCommand)
            )
            assert str(stage.download_url) == (
                "https://controller.example:7443/api/device/v2/calibration-media/"
                f"{stage.digest_sha256}"
            )
            assert stage.digest_sha256 in stage.protected_digests
            assert (tmp_path / "clips" / stage.digest_sha256).is_file()
            assert any(isinstance(item, DevicePlayerCommand) for item in device_messages)
            for _ in range(100):
                if run.outputs["video"].state == "playing":
                    break
                await asyncio.sleep(0.02)
            assert run.outputs["video"].state == "playing"

            with pytest.raises(InstallationCalibrationError, match="between 0 and 5000"):
                service.adjust("video", -10)
            service.adjust("video", 120)
            stopped, saved = await service.finish(
                accept=True, actor="operator", role=Role.OPERATOR, occurred_at=NOW
            )
            assert stopped.state == "stopped"
            assert [(item.resource, item.delay_ms) for item in saved] == [("video", 120)]
            assert runtime.output_delay_ms("video") == 120
            assert service.saved(installation.id)[1].delay_ms == 120

            await service.start(**start)
            service.adjust("video", 400)
            await service.finish(
                accept=False, actor="operator", role=Role.OPERATOR, occurred_at=NOW
            )
            assert runtime.output_delay_ms("video") == 120
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


def test_gui_and_mcp_read_and_save_installation_timing(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    from cueverge.controller.app import create_app
    from cueverge.gui.mcp_authoring import McpScope
    from cueverge.gui.service import GuiServices
    from tests.unit.test_device_enrollment_api import _config, _login, _mutation_headers

    runtime = ControllerRuntime()
    services = GuiServices.create(_config(tmp_path), runtime)
    installation_id = services.repository.get_runtime_installation().id
    now = datetime.now(UTC)

    def token(scopes: tuple[McpScope, ...]) -> str:
        return services.mcp_store.create_installation_token(
            client_id="timing",
            username="admin",
            installation_id=installation_id,
            scopes=scopes,
            expires_at=(now + timedelta(hours=1)).isoformat(),
            occurred_at=now.isoformat(),
        ).token

    read, live = token((McpScope.READ,)), token((McpScope.READ, McpScope.LIVE_OPERATION))
    with TestClient(
        create_app(runtime, gui_services=services), base_url="http://localhost"
    ) as client:

        def tool(secret: str, name: str, **arguments: object) -> dict[str, Any]:
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert response.status_code == 200, response.text
            return dict(response.json()["result"])

        timing = tool(read, "get_installation_timing")["structuredContent"]
        assert [
            (item["resource"], item["kind"], item["delay_ms"]) for item in timing["outputs"]
        ] == [
            ("projector.demo", "media", 0),
            ("roofline.demo", "lighting", 0),
        ]
        assert timing["calibration"] is None
        arguments = {"resource": "roofline.demo", "delay_ms": -60}
        assert tool(read, "set_installation_output_timing", **arguments)["isError"]
        assert tool(live, "set_installation_output_timing", **arguments)["isError"]
        saved = tool(
            live, "set_installation_output_timing", **arguments, confirm_physical_output=True
        )["structuredContent"]
        assert (saved["delay_ms"], saved["revision"]) == (-60, 1)
        assert runtime.output_delay_ms("roofline.demo") == -60
        stale = tool(
            live,
            "set_installation_output_timing",
            **arguments,
            expected_revision=3,
            confirm_physical_output=True,
        )
        assert stale["isError"]
        refused = tool(
            live,
            "start_timing_calibration",
            leds=["roofline.demo"],
            confirm_physical_output=True,
        )
        assert refused["isError"]
        assert "maintenance" in refused["content"][0]["text"]
        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={
                "Authorization": f"Bearer {read}",
                "Accept": "application/json, text/event-stream",
            },
        ).json()["result"]["tools"]
        names = {item["name"] for item in listed}
        assert {
            "get_installation_timing",
            "set_installation_output_timing",
            "start_timing_calibration",
            "adjust_timing_calibration",
            "resync_timing_calibration",
            "finish_timing_calibration",
        } <= names

        _login(client)
        url = f"/api/gui/v1/installations/{installation_id}/output-timing"
        assert client.get(url).json()["outputs"][1]["delay_ms"] == -60
        conflict = client.put(
            f"{url}/roofline.demo",
            json={"delay_ms": 10},
            headers={**_mutation_headers(client), "If-Match": '"7"'},
        )
        assert conflict.status_code == 409
        assert (
            client.put(
                f"{url}/projector.demo", json={"delay_ms": 40}, headers=_mutation_headers(client)
            ).json()["delay_ms"]
            == 40
        )
        assert runtime.output_delay_ms("projector.demo") == 40

    actions = [event.action for event in services.repository.list_audit_events()]
    assert actions.count("installation.output_timing.set") == 2
    assert "mcp.installation.timing.calibration.start" in actions


def test_projector_changes_from_another_thread_resync_on_the_runs_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(calibration_module, "START_LEAD_SECONDS", 0.02)
    monkeypatch.setattr(calibration_module, "RESYNC_DEBOUNCE_SECONDS", 0.0)

    async def scenario() -> None:
        agents = _Agents()
        run = TimingCalibrationRun(
            sink=MemoryFrameSink(),
            agents=agents,
            controller_epoch="epoch",
            media=(CalibrationMediaTarget("projector.left", "left", "a" * 64),),
            lights=(),
            delays={},
            clock=time.monotonic,
        )
        agents.run = run
        run.start()
        await asyncio.sleep(0.2)
        assert run.status.cycles == 1
        # Synchronous web handlers run on worker threads without an event loop.
        await asyncio.to_thread(run.set_delay, "projector.left", 50)
        await asyncio.sleep(0.3)
        assert run.status.cycles == 2
        assert run.status.outputs["projector.left"].delay_ms == 50
        await run.stop()

    asyncio.run(scenario())
