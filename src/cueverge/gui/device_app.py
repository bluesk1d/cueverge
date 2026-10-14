import logging
import time
from asyncio import Lock
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from cueverge.controller.runtime import ControllerRuntime
from cueverge.gui.device_ca import DeviceCertificateAuthority
from cueverge.gui.device_session import DeviceSessionError, DeviceSessionRegistry
from cueverge.gui.device_transport import peer_device_certificate
from cueverge.gui.repository import (
    DeviceCertificateAuthorizationError,
    GuiRepository,
)
from cueverge.protocol.device_v2 import (
    DeviceCommandAck,
    DeviceCredentialRotationRequest,
    DeviceCredentialRotationResponse,
    DeviceHeartbeat,
    DeviceHello,
    DeviceMediaCommand,
    DeviceMediaPosition,
)
from cueverge.protocol.models import (
    AgentCommand,
    AgentHeartbeat,
    AgentHello,
    CommandAck,
    MediaPosition,
)

LOGGER = logging.getLogger(__name__)


class DeviceCommandChannel:
    def __init__(self, websocket: WebSocket, session_id: UUID) -> None:
        self.websocket = websocket
        self.session_id = session_id
        self._send_lock = Lock()

    async def send_json(self, data: object) -> None:
        command = AgentCommand.model_validate(data)
        message = DeviceMediaCommand(
            request_id=UUID(command.request_id),
            device_session_id=self.session_id,
            controller_epoch=UUID(command.controller_epoch),
            command=command.command,
            scene_id=command.scene_id,
            scene_instance_id=UUID(command.scene_instance_id),
            playback_session_id=UUID(command.playback_session_id),
            asset_id=command.asset_id,
            target_position_ms=command.target_position_ms,
        )
        async with self._send_lock:
            await self.websocket.send_json(message.model_dump(mode="json"))


def create_device_app(
    repository: GuiRepository,
    sessions: DeviceSessionRegistry,
    runtime: ControllerRuntime | None = None,
    authority: DeviceCertificateAuthority | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Cueverge Device Transport",
        version="2",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.websocket("/api/device/v2/agent/ws")
    async def device_socket(websocket: WebSocket) -> None:
        try:
            peer = peer_device_certificate(websocket.scope)
            device = repository.authorize_device_certificate(
                device_id=str(peer.device_id),
                certificate_serial=peer.serial,
                certificate_fingerprint_sha256=peer.fingerprint_sha256,
            )
        except (ValueError, DeviceCertificateAuthorizationError):
            await websocket.close(code=4403, reason="device certificate is not authorized")
            return

        await websocket.accept()
        session_id = None
        try:
            hello = DeviceHello.model_validate(await websocket.receive_json())
            if runtime is not None:
                sessions.controller_epoch = UUID(runtime.controller_epoch)
            welcome = sessions.connect(device, hello)
            session_id = hello.device_session_id
            runtime_agent_id = None
            channel = None
            if runtime is not None:
                runtime_agent_id = _assigned_agent_id(repository, runtime, device.id)
                channel = DeviceCommandChannel(websocket, session_id)
                runtime_welcome = runtime.register_agent(
                    AgentHello(
                        agent_id=runtime_agent_id,
                        agent_session_id=str(session_id),
                        capabilities=tuple(
                            capability.kind for capability in hello.capabilities.capabilities
                        ),
                    ),
                    channel,
                )
                LOGGER.info(
                    "registered authenticated device %s as runtime target %s",
                    device.id,
                    runtime_agent_id,
                )
                welcome = welcome.model_copy(
                    update={"reconciliation": runtime_welcome.reconciliation}
                )
            repository.mark_device_connected(
                device_id=device.id,
                session_id=str(session_id),
                observed_at_ms=_now_ms(),
            )
            await websocket.send_json(welcome.model_dump(mode="json"))
            while True:
                payload = await websocket.receive_json()
                repository.authorize_device_certificate(
                    device_id=str(peer.device_id),
                    certificate_serial=peer.serial,
                    certificate_fingerprint_sha256=peer.fingerprint_sha256,
                )
                message_type = payload.get("type") if isinstance(payload, dict) else None
                if message_type == "device.heartbeat":
                    heartbeat = DeviceHeartbeat.model_validate(payload)
                    heartbeat_ack = sessions.heartbeat(heartbeat)
                    if heartbeat_ack.accepted:
                        repository.mark_device_seen(
                            device_id=device.id,
                            session_id=str(heartbeat.device_session_id),
                            observed_at_ms=_now_ms(),
                        )
                        if runtime is not None and runtime_agent_id is not None:
                            runtime.heartbeat(
                                AgentHeartbeat(
                                    agent_id=runtime_agent_id,
                                    agent_session_id=str(heartbeat.device_session_id),
                                    controller_epoch=str(heartbeat.controller_epoch),
                                    sequence=heartbeat.sequence,
                                    agent_monotonic_ms=heartbeat.device_monotonic_ms,
                                )
                            )
                    await websocket.send_json(heartbeat_ack.model_dump(mode="json"))
                    if not heartbeat_ack.accepted:
                        await websocket.close(code=4403, reason="device session was fenced")
                        return
                elif message_type == "device.command.ack" and runtime is not None:
                    if runtime_agent_id is None:
                        raise ValueError("device has no runtime assignment")
                    command_ack = DeviceCommandAck.model_validate(payload)
                    await runtime.acknowledge_command(
                        CommandAck(
                            request_id=str(command_ack.request_id),
                            agent_id=runtime_agent_id,
                            agent_session_id=str(command_ack.device_session_id),
                            controller_epoch=str(command_ack.controller_epoch),
                            status=command_ack.status,
                            error_code=command_ack.error_code,
                            detail=command_ack.detail,
                        )
                    )
                elif message_type == "device.media.position" and runtime is not None:
                    if runtime_agent_id is None:
                        raise ValueError("device has no runtime assignment")
                    position = DeviceMediaPosition.model_validate(payload)
                    runtime.ingest(
                        MediaPosition(
                            agent_id=runtime_agent_id,
                            agent_session_id=str(position.device_session_id),
                            controller_epoch=str(position.controller_epoch),
                            scene_instance_id=str(position.scene_instance_id),
                            playback_session_id=str(position.playback_session_id),
                            sequence=position.sequence,
                            position_ms=position.position_ms,
                            playback_rate=position.playback_rate,
                            state=position.state,
                            duration_ms=position.duration_ms,
                            agent_monotonic_ms=position.device_monotonic_ms,
                            discontinuity=position.discontinuity,
                        )
                    )
                elif message_type == "device.credential.rotate" and authority is not None:
                    rotation = DeviceCredentialRotationRequest.model_validate(payload)
                    if rotation.device_id != peer.device_id:
                        raise ValueError("rotation identity differs from peer certificate")
                    credential = authority.issue_device_certificate(
                        device_id=peer.device_id,
                        certificate_signing_request_pem=rotation.certificate_signing_request_pem,
                    )
                    occurred_at = datetime.now(UTC).isoformat()
                    repository.stage_device_certificate_rotation(
                        device_id=device.id,
                        expected_current_serial=peer.serial,
                        certificate_serial=credential.certificate_serial,
                        certificate_fingerprint_sha256=credential.fingerprint_sha256,
                        certificate_issued_at=credential.issued_at.isoformat(),
                        certificate_expires_at=credential.expires_at.isoformat(),
                        occurred_at=occurred_at,
                    )
                    await websocket.send_json(
                        DeviceCredentialRotationResponse(credential=credential).model_dump(
                            mode="json"
                        )
                    )
                    await websocket.close(code=4001, reason="device credential rotated")
                    return
                else:
                    raise ValueError("unsupported device message")
        except (
            DeviceCertificateAuthorizationError,
            DeviceSessionError,
            ValidationError,
            ValueError,
        ):
            await websocket.close(code=4403, reason="device session is not authorized")
        except WebSocketDisconnect:
            pass
        finally:
            if session_id is not None:
                if runtime is not None and runtime_agent_id is not None and channel is not None:
                    runtime.disconnect_agent(runtime_agent_id, channel)
                    LOGGER.info(
                        "disconnected authenticated runtime target %s",
                        runtime_agent_id,
                    )
                repository.mark_device_disconnected(
                    device_id=device.id,
                    session_id=str(session_id),
                    observed_at_ms=_now_ms(),
                )
                sessions.disconnect(peer.device_id, session_id)

    return app


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _assigned_agent_id(
    repository: GuiRepository,
    runtime: ControllerRuntime,
    device_id: str,
) -> str:
    resources = {
        assignment.resource
        for assignment in repository.list_resource_assignments()
        if assignment.device_id == device_id and assignment.capability_kind == "media_playback"
    }
    agent_ids = {
        output.agent_id
        for scene in runtime.catalog.scenes.values()
        for output in scene.media_outputs
        if output.definition.resource in resources
    }
    if len(agent_ids) != 1:
        raise ValueError("device must have exactly one active media assignment")
    return next(iter(agent_ids))
