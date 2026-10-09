from dataclasses import dataclass
from time import sleep
from typing import Any

from fastapi.testclient import TestClient

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.media_agent.fake import FakeMediaAgent
from cueverge.protocol.models import Discontinuity, PlaybackState


@dataclass
class ManualClock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now


@dataclass
class MemoryCommandChannel:
    sent: list[dict[str, Any]]

    async def send_json(self, data: Any) -> None:
        assert isinstance(data, dict)
        self.sent.append(data)


@dataclass
class FakeEventIngress:
    started: bool = False
    stopped: bool = False

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def test_event_ingress_follows_application_lifespan() -> None:
    ingress = FakeEventIngress()

    with TestClient(create_app(ControllerRuntime(), ingress)):
        assert ingress.started
        assert not ingress.stopped

    assert ingress.stopped


def test_api_agent_timecode_rendering_and_disconnect() -> None:
    clock = ManualClock()
    runtime = ControllerRuntime(clock=clock)
    agent = FakeMediaAgent()
    commands = MemoryCommandChannel(sent=[])
    agent.accept_welcome(runtime.register_agent(agent.hello(), commands))

    with TestClient(create_app(runtime)) as client:
        response = client.post("/api/v1/scenes/synthetic-sweep/start")
        assert response.status_code == 200
        started = response.json()
        assert [command["command"] for command in commands.sent] == ["prepare"]
        assert commands.sent[0]["playback_session_id"] == started["playback_session_id"]
        agent.attach(started["scene_instance_id"], started["playback_session_id"])

        with client.websocket_connect("/api/v1/agent/ws") as socket:
            agent.reconnect()
            socket.send_json(agent.hello(agent.snapshot(0)).model_dump(mode="json"))
            welcome = socket.receive_json()
            assert welcome["type"] == "agent.welcome"
            assert welcome["reconciliation"] == "continue"

            socket.send_json(agent.heartbeat(100).model_dump(mode="json"))
            heartbeat_ack = socket.receive_json()
            assert heartbeat_ack["type"] == "agent.heartbeat.ack"
            assert heartbeat_ack["accepted"]

            socket.send_json(agent.position(1000).model_dump(mode="json"))
            assert socket.receive_json()["accepted"]
            sleep(0.06)
            assert runtime.sink.frames[-1].position_ms == 1000
            assert runtime.sink.frames[-1].pixels[2] == (0, 255, 96)

            clock.now += 0.08
            status = runtime.status()
            assert status["estimated_position_ms"] == 1080
            assert not status["degraded"]

            paused = agent.position(1200, state=PlaybackState.PAUSED)
            socket.send_json(paused.model_dump(mode="json"))
            assert socket.receive_json()["accepted"]
            clock.now += 0.2
            sleep(0.06)
            assert runtime.status()["estimated_position_ms"] == 1200

            seek = agent.position(3200, discontinuity=Discontinuity.SEEK)
            socket.send_json(seek.model_dump(mode="json"))
            assert socket.receive_json()["accepted"]
            sleep(0.06)
            loop = agent.position(50, discontinuity=Discontinuity.LOOP)
            socket.send_json(loop.model_dump(mode="json"))
            assert socket.receive_json()["accepted"]
            sleep(0.06)

            socket.send_json(loop.model_dump(mode="json"))
            duplicate_ack = socket.receive_json()
            assert not duplicate_ack["accepted"]
            assert duplicate_ack["reason"] == "out_of_order"

            old_session = agent.position(70).model_copy(
                update={"playback_session_id": "old-session"}
            )
            socket.send_json(old_session.model_dump(mode="json"))
            assert socket.receive_json()["reason"] == "session_fence"

        disconnected = client.get("/api/v1/status").json()
        assert disconnected["degraded"]
        assert disconnected["degraded_reason"] == "agent_disconnected"
        assert not disconnected["render_loop_running"]
        assert disconnected["rendered_frames"] >= 4
        assert disconnected["render_ticks_without_timecode"] >= 1
        assert disconnected["rejected_telemetry"] == 2
        assert disconnected["reconciliations"] == 1


def test_start_requires_known_scene_and_connected_agent() -> None:
    client = TestClient(create_app())

    assert client.post("/api/v1/scenes/missing/start").status_code == 404
    response = client.post("/api/v1/scenes/synthetic-sweep/start")
    assert response.status_code == 409
    assert "not connected" in response.json()["detail"]
    assert client.post("/api/v1/show/start").status_code == 409


def test_media_control_api_dispatches_fenced_agent_commands() -> None:
    runtime = ControllerRuntime()
    agent = FakeMediaAgent()
    commands = MemoryCommandChannel(sent=[])
    agent.accept_welcome(runtime.register_agent(agent.hello(), commands))

    with TestClient(create_app(runtime)) as client:
        started = client.post("/api/v1/scenes/synthetic-sweep/start")
        assert started.status_code == 200
        prepare = commands.sent[-1]
        agent.attach(
            started.json()["scene_instance_id"],
            started.json()["playback_session_id"],
        )
        assert client.post("/api/v1/pause").json()["status"] == "pause_requested"
        assert client.post("/api/v1/seek", json={"position_ms": 2500}).json() == {
            "status": "seek_requested",
            "position_ms": 2500,
        }
        assert client.post("/api/v1/resume").json()["status"] == "resume_requested"
        assert client.post("/api/v1/stop").json()["status"] == "stopped"

    assert [command["command"] for command in commands.sent] == [
        "prepare",
        "pause",
        "seek",
        "resume",
        "stop",
    ]
    assert commands.sent[2]["target_position_ms"] == 2500
    assert all(
        command["agent_session_id"] == prepare["agent_session_id"] for command in commands.sent
    )
    assert runtime.active_scene is None
    assert not runtime.render_loop_running
    status = runtime.status()
    assert status["estimated_position_ms"] is None
    assert status["playback_state"] is None
    assert not status["degraded"]


def test_media_control_api_requires_active_scene() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/pause").status_code == 409
        assert client.post("/api/v1/resume").status_code == 409
        assert client.post("/api/v1/seek", json={"position_ms": 0}).status_code == 409
        assert client.post("/api/v1/seek", json={"position_ms": -1}).status_code == 422


def test_generic_event_api_starts_declarative_encounter() -> None:
    event_clock = ManualClock(now=10.0)
    runtime = ControllerRuntime(event_clock=lambda: round(event_clock.now * 1000))
    agent = FakeMediaAgent()
    commands = MemoryCommandChannel(sent=[])
    agent.accept_welcome(runtime.register_agent(agent.hello(), commands))

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/api/v1/events",
            json={
                "event_id": "event-1",
                "event_type": "person.entered",
                "occurred_at_ms": 10_000,
                "attributes": {"zone": "demo"},
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "accepted": True,
            "reason": None,
            "state": "suspicious",
            "scene_decision": "start",
        }
        status = client.get("/api/v1/status").json()
        assert status["active_scene"] == "encounter-suspicious"
        assert status["encounter"]["state"] == "suspicious"
        assert status["encounter_loop_running"]
