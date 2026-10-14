import asyncio
import json
import logging
import ssl
import time
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import AnyUrl, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from cueverge.media_agent.client import MediaAgentClient
from cueverge.media_agent.device_enrollment import StoredDeviceIdentity
from cueverge.protocol.device_v2 import (
    DeviceCommandAck,
    DeviceHeartbeat,
    DeviceHeartbeatAck,
    DeviceHello,
    DeviceMediaCommand,
    DeviceMediaPosition,
    DeviceWelcome,
)
from cueverge.protocol.models import AgentCommand

LOGGER = logging.getLogger(__name__)


class DevicePresenceError(RuntimeError):
    pass


class DevicePresenceClient:
    def __init__(
        self,
        transport_url: AnyUrl,
        credential_root: Path,
        *,
        reconnect_delay_ms: int,
        media_client: MediaAgentClient,
    ) -> None:
        self.transport_url = transport_url
        self.credential_root = credential_root
        self.reconnect_delay_ms = reconnect_delay_ms
        self.media_client = media_client
        self.identity = StoredDeviceIdentity.model_validate_json(
            (credential_root / "device.json").read_text(encoding="utf-8")
        )
        self.ssl_context = ssl.create_default_context(
            cafile=str(credential_root / "ca-certificates.pem")
        )
        self.ssl_context.load_cert_chain(
            credential_root / "certificate.pem",
            credential_root / "private-key.pem",
        )

    async def run_forever(self) -> None:
        while True:
            try:
                LOGGER.info("connecting authenticated device presence to %s", self.transport_url)
                async with connect(
                    str(self.transport_url),
                    ssl=self.ssl_context,
                ) as websocket:
                    LOGGER.info(
                        "authenticated device presence connected for %s",
                        self.identity.device_id,
                    )
                    await self._run_connection(websocket)
            except (
                OSError,
                TimeoutError,
                ValidationError,
                WebSocketException,
                DevicePresenceError,
            ) as error:
                LOGGER.warning("authenticated device presence failed: %s", error)
                await asyncio.sleep(self.reconnect_delay_ms / 1000)

    async def _run_connection(self, websocket: ClientConnection) -> None:
        session_id = uuid4()
        hello = DeviceHello(
            device_id=self.identity.device_id,
            device_session_id=session_id,
            capabilities=self.identity.capabilities,
        )
        await websocket.send(hello.model_dump_json())
        welcome = DeviceWelcome.model_validate_json(await websocket.recv())
        if welcome.device_id != self.identity.device_id:
            raise DevicePresenceError("controller welcomed a different device identity")
        self.media_client.agent_session_id = str(session_id)
        self.media_client.controller_epoch = str(welcome.controller_epoch)
        if welcome.reconciliation == "stop":
            await self.media_client.player.stop()
            self.media_client._clear_playback()
        send_lock = asyncio.Lock()
        tasks = (
            asyncio.create_task(self._heartbeat_loop(websocket, send_lock, welcome, session_id)),
            asyncio.create_task(self._telemetry_loop(websocket, send_lock)),
        )
        try:
            await self._receive(websocket, send_lock, session_id, welcome)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _receive(
        self,
        websocket: ClientConnection,
        send_lock: asyncio.Lock,
        session_id: UUID,
        welcome: DeviceWelcome,
    ) -> None:
        async for payload in websocket:
            if not isinstance(payload, str):
                raise DevicePresenceError("controller sent a non-text message")
            message = json.loads(payload)
            message_type = message.get("type") if isinstance(message, dict) else None
            if message_type == "device.heartbeat.ack":
                acknowledgment = DeviceHeartbeatAck.model_validate(message)
                if not acknowledgment.accepted:
                    raise DevicePresenceError(
                        f"controller fenced device presence: {acknowledgment.reason}"
                    )
            elif message_type == "device.media.command":
                command = DeviceMediaCommand.model_validate(message)
                internal = AgentCommand(
                    request_id=str(command.request_id),
                    command=command.command,
                    agent_id=self.media_client.runtime_identity,
                    agent_session_id=str(session_id),
                    controller_epoch=str(welcome.controller_epoch),
                    scene_id=command.scene_id,
                    scene_instance_id=str(command.scene_instance_id),
                    playback_session_id=str(command.playback_session_id),
                    asset_id=command.asset_id,
                    target_position_ms=command.target_position_ms,
                )
                result = await self.media_client.handle_command(internal)
                response = DeviceCommandAck(
                    request_id=command.request_id,
                    device_session_id=session_id,
                    controller_epoch=welcome.controller_epoch,
                    status=result.status,
                    error_code=result.error_code,
                    detail=result.detail,
                )
                async with send_lock:
                    await websocket.send(response.model_dump_json())
            else:
                raise DevicePresenceError(f"unsupported controller message: {message_type}")

    async def _heartbeat_loop(
        self,
        websocket: ClientConnection,
        send_lock: asyncio.Lock,
        welcome: DeviceWelcome,
        session_id: UUID,
    ) -> None:
        sequence = 0
        while True:
            await asyncio.sleep(welcome.heartbeat_interval_ms / 1000)
            message = DeviceHeartbeat(
                device_id=self.identity.device_id,
                device_session_id=session_id,
                controller_epoch=welcome.controller_epoch,
                sequence=sequence,
                device_monotonic_ms=round(time.monotonic() * 1000),
            )
            async with send_lock:
                await websocket.send(message.model_dump_json())
            sequence += 1

    async def _telemetry_loop(
        self,
        websocket: ClientConnection,
        send_lock: asyncio.Lock,
    ) -> None:
        while True:
            telemetry = await self.media_client.telemetry()
            if telemetry is not None:
                message = DeviceMediaPosition(
                    device_session_id=UUID(telemetry.agent_session_id),
                    controller_epoch=UUID(telemetry.controller_epoch),
                    scene_instance_id=UUID(telemetry.scene_instance_id),
                    playback_session_id=UUID(telemetry.playback_session_id),
                    sequence=telemetry.sequence,
                    position_ms=telemetry.position_ms,
                    playback_rate=telemetry.playback_rate,
                    state=telemetry.state,
                    duration_ms=telemetry.duration_ms,
                    device_monotonic_ms=telemetry.agent_monotonic_ms,
                    discontinuity=telemetry.discontinuity,
                )
                async with send_lock:
                    await websocket.send(message.model_dump_json())
            await asyncio.sleep(1 / self.media_client.config.telemetry_hz)
