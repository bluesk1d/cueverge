from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cueverge.controller.runtime import ControllerRuntime
from cueverge.protocol.models import (
    AgentHeartbeat,
    AgentHello,
    CommandAck,
    HeartbeatAck,
    MediaPosition,
    TelemetryAck,
)


class StartSceneResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    scene_instance_id: str
    playback_session_id: str


class StatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controller_epoch: str
    active_scene: str | None
    scene_instance_id: str | None
    playback_session_id: str | None
    estimated_position_ms: int | None
    playback_state: str | None
    degraded: bool
    degraded_reason: str | None
    connected_agents: list[str]
    pending_commands: int = Field(ge=0)
    command_errors: int = Field(ge=0)
    command_timeouts: int = Field(ge=0)
    abandoned_commands: int = Field(ge=0)
    heartbeat_timeouts: int = Field(ge=0)
    reconciliations: int = Field(ge=0)
    rejected_protocol_messages: int = Field(ge=0)
    rendered_frames: int = Field(ge=0)
    rejected_telemetry: int = Field(ge=0)
    render_target_fps: float = Field(gt=0)
    render_loop_running: bool
    render_ticks: int = Field(ge=0)
    render_ticks_without_timecode: int = Field(ge=0)
    stale_frames: int = Field(ge=0)
    skipped_render_deadlines: int = Field(ge=0)
    mean_render_interval_ms: float | None = Field(default=None, ge=0)


def create_app(runtime: ControllerRuntime | None = None) -> FastAPI:
    controller = runtime or ControllerRuntime()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await controller.shutdown()

    app = FastAPI(title="Cueverge Controller", version="0.1.0", lifespan=lifespan)
    app.state.runtime = controller

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready(request: Request) -> dict[str, object]:
        controller: ControllerRuntime = request.app.state.runtime
        controller.check_timeouts()
        return {"status": "ready", "connected_agents": controller.connected_agent_count}

    @app.get("/api/v1/scenes")
    async def scenes(request: Request) -> list[dict[str, str]]:
        controller: ControllerRuntime = request.app.state.runtime
        return [
            {"id": scene.definition.id, "name": scene.definition.name}
            for scene in controller.catalog.scenes.values()
        ]

    @app.post("/api/v1/scenes/{scene_id}/start", response_model=StartSceneResponse)
    async def start_scene(scene_id: str, request: Request) -> StartSceneResponse:
        controller: ControllerRuntime = request.app.state.runtime
        try:
            active = await controller.start_scene(scene_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return StartSceneResponse(
            scene_id=active.definition.id,
            scene_instance_id=active.scene_instance_id,
            playback_session_id=active.playback_session_id,
        )

    @app.get("/api/v1/status", response_model=StatusResponse)
    async def status(request: Request) -> dict[str, object]:
        controller: ControllerRuntime = request.app.state.runtime
        return controller.status()

    @app.post("/api/v1/stop")
    async def stop(request: Request) -> dict[str, str]:
        controller: ControllerRuntime = request.app.state.runtime
        await controller.stop()
        return {"status": "stopped"}

    @app.websocket("/api/v1/agent/ws")
    async def agent_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        controller: ControllerRuntime = websocket.app.state.runtime
        agent_id: str | None = None
        try:
            hello = AgentHello.model_validate(await websocket.receive_json())
            agent_id = hello.agent_id
            welcome = controller.register_agent(hello, websocket)
            await websocket.send_json(welcome.model_dump(mode="json"))
            while True:
                payload = await websocket.receive_json()
                try:
                    if not isinstance(payload, dict):
                        raise ValueError("protocol message must be a JSON object")
                    message_type = payload.get("type")
                    response: BaseModel | dict[str, object]
                    if message_type == "media.position":
                        telemetry = MediaPosition.model_validate(payload)
                        decision = controller.ingest(telemetry)
                        response = TelemetryAck(
                            sequence=telemetry.sequence,
                            accepted=decision.accepted,
                            reason=decision.reason,
                        )
                    elif message_type == "agent.heartbeat":
                        heartbeat = AgentHeartbeat.model_validate(payload)
                        decision = controller.heartbeat(heartbeat)
                        response = HeartbeatAck(
                            sequence=heartbeat.sequence,
                            accepted=decision.accepted,
                            reason=decision.reason,
                        )
                    elif message_type == "command.ack":
                        command_ack = CommandAck.model_validate(payload)
                        decision = controller.acknowledge_command(command_ack)
                        response = {
                            "type": "command.ack.received",
                            "protocol_version": 1,
                            "request_id": command_ack.request_id,
                            "accepted": decision.accepted,
                            "reason": decision.reason,
                        }
                    else:
                        raise ValueError(f"unsupported message type: {message_type}")
                    if isinstance(response, BaseModel):
                        await websocket.send_json(response.model_dump(mode="json"))
                    else:
                        await websocket.send_json(response)
                except ValidationError as error:
                    await websocket.send_json(
                        {
                            "type": "protocol.error",
                            "protocol_version": 1,
                            "detail": str(error),
                        }
                    )
                except ValueError as error:
                    await websocket.send_json(
                        {
                            "type": "protocol.error",
                            "protocol_version": 1,
                            "detail": str(error),
                        }
                    )
        except (WebSocketDisconnect, ValidationError):
            pass
        finally:
            if agent_id is not None:
                controller.disconnect_agent(agent_id, websocket)

    return app


app = create_app()
