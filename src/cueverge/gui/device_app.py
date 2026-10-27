import logging
import time
from asyncio import Lock
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from cueverge.controller.runtime import ControllerRuntime
from cueverge.core.models import ResourceKind
from cueverge.gui.agent_settings import DeviceAgentSettingsService
from cueverge.gui.agent_updates import AgentBundleCatalog
from cueverge.gui.asset_upload import AssetUploadError, AssetUploadService
from cueverge.gui.device_ca import DeviceCertificateAuthority
from cueverge.gui.device_session import DeviceSessionError, DeviceSessionRegistry
from cueverge.gui.device_transport import peer_device_certificate
from cueverge.gui.media_delivery import published_media_member
from cueverge.gui.repository import (
    DeviceCertificateAuthorizationError,
    GuiRepository,
    ProjectNotFoundError,
)
from cueverge.protocol.device_v2 import (
    DeviceAgentSettingsAck,
    DeviceAgentUpdateAck,
    DeviceArtifactActivateAck,
    DeviceArtifactCleanupAck,
    DeviceArtifactInventory,
    DeviceArtifactStageAck,
    DeviceCommandAck,
    DeviceCredentialRotationRequest,
    DeviceCredentialRotationResponse,
    DeviceHeartbeat,
    DeviceHello,
    DeviceKind,
    DeviceMediaCommand,
    DeviceMediaPosition,
    DevicePlayerAck,
    DeviceTimingReport,
)
from cueverge.protocol.models import (
    MEDIA_REPEAT_CAPABILITY,
    MEDIA_SEAMLESS_CAPABILITY,
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
            end_behavior=command.end_behavior,
            target_position_ms=command.target_position_ms,
            repeat_count=command.repeat_count,
            auto_advance=command.auto_advance,
        )
        async with self._send_lock:
            await self.websocket.send_json(message.model_dump(mode="json"))

    async def send_device_message(self, message: object) -> None:
        model_dump = getattr(message, "model_dump", None)
        if not callable(model_dump):
            raise DeviceSessionError("device message is not serializable")
        async with self._send_lock:
            await self.websocket.send_json(model_dump(mode="json"))


def create_device_app(
    repository: GuiRepository,
    sessions: DeviceSessionRegistry,
    runtime: ControllerRuntime | None = None,
    authority: DeviceCertificateAuthority | None = None,
    assets: AssetUploadService | None = None,
    agent_bundles: AgentBundleCatalog | None = None,
    agent_settings: DeviceAgentSettingsService | None = None,
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

    @app.get("/api/device/v2/render-artifacts/{artifact_id}/{resource}")
    async def render_artifact_member(
        artifact_id: str,
        resource: str,
        request: Request,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> StreamingResponse:
        try:
            peer = peer_device_certificate(request.scope)
            device = repository.authorize_device_certificate(
                device_id=str(peer.device_id),
                certificate_serial=peer.serial,
                certificate_fingerprint_sha256=peer.fingerprint_sha256,
            )
            assignments = {item.resource: item for item in repository.list_resource_assignments()}
            assignment = assignments.get(resource)
            if assignment is None or assignment.device_id != device.id:
                raise ValueError("artifact resource is not assigned to this device")
            member = repository.get_render_artifact_member(artifact_id, resource)
        except (
            ValueError,
            DeviceCertificateAuthorizationError,
            ProjectNotFoundError,
        ):
            raise HTTPException(status_code=403, detail="artifact access denied") from None
        start = _range_start(range_header, member.size_bytes)
        status_code = 206 if start else 200
        length = member.size_bytes - start
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "ETag": f'"sha256:{member.digest_sha256}"',
            "Cache-Control": "private, immutable",
        }
        if start:
            headers["Content-Range"] = f"bytes {start}-{member.size_bytes - 1}/{member.size_bytes}"
        return StreamingResponse(
            _file_chunks(member.path, start),
            status_code=status_code,
            media_type="video/mp4",
            headers=headers,
        )

    def _authorize_media_agent(request: Request) -> None:
        peer = peer_device_certificate(request.scope)
        device = repository.authorize_device_certificate(
            device_id=str(peer.device_id),
            certificate_serial=peer.serial,
            certificate_fingerprint_sha256=peer.fingerprint_sha256,
        )
        if device.device_kind is not DeviceKind.DIRECT_MEDIA_AGENT:
            raise ValueError("agent bundles are for direct media agents")

    @app.get("/api/device/v2/agent-bundles")
    async def agent_bundle_metadata(request: Request, platform: str) -> dict[str, object]:
        try:
            _authorize_media_agent(request)
        except (ValueError, DeviceCertificateAuthorizationError):
            raise HTTPException(status_code=403, detail="agent bundle access denied") from None
        bundle = agent_bundles.bundles.get(platform) if agent_bundles else None  # type: ignore[call-overload]
        if bundle is None:
            raise HTTPException(status_code=404, detail="no agent bundle for this platform")
        return {
            **bundle.response().model_dump(mode="json"),
            "download_path": f"/api/device/v2/agent-bundles/{bundle.digest_sha256}",
        }

    @app.get("/api/device/v2/agent-bundles/{digest}")
    async def agent_bundle(
        digest: str,
        request: Request,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> StreamingResponse:
        try:
            _authorize_media_agent(request)
            bundle = agent_bundles.by_digest(digest) if agent_bundles else None
            if bundle is None:
                raise ValueError("unknown agent bundle")
        except (ValueError, DeviceCertificateAuthorizationError):
            raise HTTPException(status_code=403, detail="agent bundle access denied") from None
        start = _range_start(range_header, bundle.size_bytes)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(bundle.size_bytes - start),
            "ETag": f'"sha256:{bundle.digest_sha256}"',
            "Cache-Control": "private, immutable",
        }
        if start:
            headers["Content-Range"] = f"bytes {start}-{bundle.size_bytes - 1}/{bundle.size_bytes}"
        return StreamingResponse(
            _file_chunks(bundle.path, start),
            status_code=206 if start else 200,
            media_type="application/zip",
            headers=headers,
        )

    @app.get("/api/device/v2/published-media/{package_id}/{member_id}")
    async def published_media(
        package_id: str,
        member_id: str,
        request: Request,
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> StreamingResponse:
        try:
            peer = peer_device_certificate(request.scope)
            device = repository.authorize_device_certificate(
                device_id=str(peer.device_id),
                certificate_serial=peer.serial,
                certificate_fingerprint_sha256=peer.fingerprint_sha256,
            )
            member = published_media_member(repository, package_id, member_id)
            assignment = next(
                (
                    item
                    for item in repository.list_resource_assignments()
                    if item.resource == member["resource"]
                ),
                None,
            )
            if assignment is None or assignment.device_id != device.id:
                raise ValueError("media is not assigned to this device")
            asset_id, artifact_id = member.get("source_asset_id"), member.get("render_artifact_id")
            if isinstance(asset_id, str) and assets is not None:
                _, path = assets.get_original(asset_id)
            elif isinstance(artifact_id, str):
                path = repository.get_render_artifact_member(
                    artifact_id, str(member["resource"])
                ).path
            else:
                raise ValueError("media source is unavailable")
            size, digest = member.get("size_bytes"), member.get("digest_sha256")
            if (
                not isinstance(size, int)
                or not isinstance(digest, str)
                or path.stat().st_size != size
            ):
                raise ValueError("media metadata is invalid")
        except (
            ValueError,
            OSError,
            AssetUploadError,
            DeviceCertificateAuthorizationError,
            ProjectNotFoundError,
        ):
            raise HTTPException(status_code=403, detail="published media access denied") from None
        start = _range_start(range_header, size)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(size - start),
            "ETag": f'"sha256:{digest}"',
            "Cache-Control": "private, immutable",
        }
        if range_header is not None:
            headers["Content-Range"] = f"bytes {start}-{size - 1}/{size}"
        return StreamingResponse(
            _file_chunks(path, start),
            status_code=206 if range_header is not None else 200,
            media_type="application/octet-stream",
            headers=headers,
        )

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
        target: _RuntimeTarget | None = None
        try:
            hello = DeviceHello.model_validate(await websocket.receive_json())
            if runtime is not None:
                sessions.controller_epoch = UUID(runtime.controller_epoch)
            welcome = sessions.connect(device, hello)
            session_id = hello.device_session_id
            target = None
            if runtime is not None and device.device_kind is DeviceKind.DIRECT_MEDIA_AGENT:
                channel = DeviceCommandChannel(websocket, session_id)
                sessions.attach_channel(UUID(device.id), session_id, channel)
                target = _RuntimeTarget(repository, runtime, sessions, device.id, hello, channel)
                reconciliation = target.sync()
                if reconciliation is not None:
                    welcome = welcome.model_copy(update={"reconciliation": reconciliation})
            repository.mark_device_connected(
                device_id=device.id,
                session_id=str(session_id),
                observed_at_ms=_now_ms(),
            )
            await websocket.send_json(welcome.model_dump(mode="json"))
            if agent_settings is not None and "agent_settings" in hello.media_features:
                agent_settings.sync_on_connect(
                    device.id, hello.agent_settings.revision if hello.agent_settings else None
                )
            while True:
                payload = await websocket.receive_json()
                repository.authorize_device_certificate(
                    device_id=str(peer.device_id),
                    certificate_serial=peer.serial,
                    certificate_fingerprint_sha256=peer.fingerprint_sha256,
                )
                message_type = payload.get("type") if isinstance(payload, dict) else None
                if message_type == "device.heartbeat":
                    controller_receive_unix_ms = _now_ms()
                    heartbeat = DeviceHeartbeat.model_validate(payload)
                    heartbeat_ack = sessions.heartbeat(heartbeat)
                    if heartbeat_ack.accepted:
                        repository.mark_device_seen(
                            device_id=device.id,
                            session_id=str(heartbeat.device_session_id),
                            observed_at_ms=_now_ms(),
                        )
                        if target is not None:
                            target.sync()
                        if (
                            runtime is not None
                            and target is not None
                            and target.agent_id is not None
                        ):
                            runtime.heartbeat(
                                AgentHeartbeat(
                                    agent_id=target.agent_id,
                                    agent_session_id=str(heartbeat.device_session_id),
                                    controller_epoch=str(heartbeat.controller_epoch),
                                    sequence=heartbeat.sequence,
                                    agent_monotonic_ms=heartbeat.device_monotonic_ms,
                                )
                            )
                    heartbeat_ack = heartbeat_ack.model_copy(
                        update={
                            "controller_receive_unix_ms": controller_receive_unix_ms,
                            "controller_send_unix_ms": _now_ms(),
                        }
                    )
                    await websocket.send_json(heartbeat_ack.model_dump(mode="json"))
                    if not heartbeat_ack.accepted:
                        await websocket.close(code=4403, reason="device session was fenced")
                        return
                elif message_type == "device.timing.report":
                    timing = DeviceTimingReport.model_validate(payload)
                    if timing.device_id != peer.device_id:
                        raise ValueError("timing identity differs from peer certificate")
                    if not sessions.session_is_current(
                        timing.device_id,
                        timing.device_session_id,
                        timing.controller_epoch,
                    ):
                        raise DeviceSessionError("timing report session was fenced")
                    repository.mark_device_timing(
                        device_id=device.id,
                        session_id=str(timing.device_session_id),
                        latency_ms=timing.round_trip_ms,
                        clock_offset_ms=timing.clock_offset_ms,
                    )
                elif message_type == "device.artifact.inventory":
                    inventory = DeviceArtifactInventory.model_validate(payload)
                    if inventory.device_id != peer.device_id:
                        raise ValueError("inventory identity differs from peer certificate")
                    if not sessions.session_is_current(
                        inventory.device_id,
                        inventory.device_session_id,
                        inventory.controller_epoch,
                    ):
                        raise DeviceSessionError("artifact inventory session was fenced")
                    repository.replace_device_artifact_inventory(
                        device_id=device.id,
                        session_id=str(inventory.device_session_id),
                        observed_at_ms=_now_ms(),
                        total_bytes=inventory.total_bytes,
                        free_bytes=inventory.free_bytes,
                        supports_pinning=inventory.supports_pinning,
                        supports_resumable_transfer=(inventory.supports_resumable_transfer),
                        artifacts=tuple(
                            (
                                artifact.digest_sha256,
                                artifact.size_bytes,
                                artifact.pinned,
                                artifact.in_use,
                                artifact.state,
                                artifact.last_used_unix_ms,
                            )
                            for artifact in inventory.artifacts
                        ),
                        transfers=tuple(
                            transfer.model_dump(mode="json") for transfer in inventory.transfers
                        ),
                        faults=inventory.faults,
                    )
                elif message_type == "device.artifact.stage.ack":
                    stage_ack = DeviceArtifactStageAck.model_validate(payload)
                    sessions.acknowledge(
                        UUID(device.id),
                        stage_ack.request_id,
                        stage_ack,
                    )
                elif message_type == "device.artifact.activate.ack":
                    activate_ack = DeviceArtifactActivateAck.model_validate(payload)
                    sessions.acknowledge(
                        UUID(device.id),
                        activate_ack.request_id,
                        activate_ack,
                    )
                elif message_type == "device.artifact.cleanup.ack":
                    cleanup_ack = DeviceArtifactCleanupAck.model_validate(payload)
                    sessions.acknowledge(
                        UUID(device.id),
                        cleanup_ack.request_id,
                        cleanup_ack,
                    )
                elif message_type == "device.agent.settings.ack":
                    settings_ack = DeviceAgentSettingsAck.model_validate(payload)
                    if settings_ack.state is not None:
                        sessions.set_agent_settings_state(
                            peer.device_id, session_id, settings_ack.state
                        )
                    with suppress(DeviceSessionError):
                        sessions.acknowledge(UUID(device.id), settings_ack.request_id, settings_ack)
                elif message_type == "device.agent.update.ack":
                    update_ack = DeviceAgentUpdateAck.model_validate(payload)
                    with suppress(DeviceSessionError):
                        sessions.acknowledge(UUID(device.id), update_ack.request_id, update_ack)
                elif message_type == "device.player.ack":
                    player_ack = DevicePlayerAck.model_validate(payload)
                    sessions.set_player_state(peer.device_id, session_id, player_ack.player_state)
                    if target is not None:
                        # Register or release the runtime target before the requester resumes.
                        target.sync()
                    with suppress(DeviceSessionError):
                        # A late acknowledgement after the request timed out is not a fault.
                        sessions.acknowledge(UUID(device.id), player_ack.request_id, player_ack)
                elif message_type == "device.command.ack" and runtime is not None:
                    if target is None or target.agent_id is None:
                        raise ValueError("device has no runtime assignment")
                    runtime_agent_id = target.agent_id
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
                    if target is None or target.agent_id is None:
                        raise ValueError("device has no runtime assignment")
                    runtime_agent_id = target.agent_id
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
        ) as error:
            LOGGER.warning("device %s session rejected: %s", peer.device_id, error)
            await websocket.close(code=4403, reason="device session is not authorized")
        except WebSocketDisconnect:
            pass
        finally:
            if session_id is not None:
                if target is not None:
                    target.release()
                repository.mark_device_disconnected(
                    device_id=device.id,
                    session_id=str(session_id),
                    observed_at_ms=_now_ms(),
                )
                sessions.disconnect(peer.device_id, session_id)

    return app


class _RuntimeTarget:
    """Keeps an authenticated media agent registered with the runtime while it can play.

    Agents that report player state are registered only while their player is ready, so a
    presence-only connection never receives media commands.
    """

    def __init__(
        self,
        repository: GuiRepository,
        runtime: ControllerRuntime,
        sessions: DeviceSessionRegistry,
        device_id: str,
        hello: DeviceHello,
        channel: DeviceCommandChannel,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.sessions = sessions
        self.device_id = device_id
        self.hello = hello
        self.channel = channel
        self.agent_id: str | None = None
        self._idle_reason: str | None = None

    def sync(self) -> Literal["idle", "continue", "stop"] | None:
        ready = self.sessions.player_ready(UUID(self.device_id))
        if self.agent_id is not None:
            if not ready:
                self.release()
                self._log_idle("player is not running")
            return None
        if not ready:
            self._log_idle("player is not running")
            return None
        agent_id = _assigned_agent_id(self.repository, self.runtime, self.device_id)
        if agent_id is None:
            self._log_idle("loaded show has no media target")
            return None
        welcome = self.runtime.register_agent(
            AgentHello(
                agent_id=agent_id,
                agent_session_id=str(self.hello.device_session_id),
                capabilities=_runtime_capabilities(self.hello),
            ),
            self.channel,
        )
        self.agent_id = agent_id
        self._idle_reason = None
        LOGGER.info(
            "registered authenticated device %s as runtime target %s", self.device_id, agent_id
        )
        return welcome.reconciliation

    def release(self) -> None:
        if self.agent_id is not None:
            self.runtime.disconnect_agent(self.agent_id, self.channel)
            LOGGER.info("released authenticated runtime target %s", self.agent_id)
            self.agent_id = None

    def _log_idle(self, reason: str) -> None:
        if reason != self._idle_reason:
            self._idle_reason = reason
            LOGGER.info("authenticated device %s connected idle: %s", self.device_id, reason)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _range_start(value: str | None, size: int) -> int:
    if value is None:
        return 0
    if not value.startswith("bytes=") or "," in value:
        raise HTTPException(status_code=416, detail="unsupported range")
    start_text, separator, end_text = value[6:].partition("-")
    if not separator or not start_text or end_text:
        raise HTTPException(status_code=416, detail="only open-ended ranges are supported")
    try:
        start = int(start_text)
    except ValueError as error:
        raise HTTPException(status_code=416, detail="invalid range") from error
    if start < 0 or start >= size:
        raise HTTPException(status_code=416, detail="range is outside artifact")
    return start


def _file_chunks(path: Path, start: int) -> Iterator[bytes]:
    with path.open("rb") as source:
        source.seek(start)
        while chunk := source.read(1024 * 1024):
            yield chunk


def _assigned_agent_id(
    repository: GuiRepository,
    runtime: ControllerRuntime,
    device_id: str,
) -> str | None:
    """The runtime agent for a device's media assignment, or None while it has no target.

    Scene media outputs take precedence; otherwise the loaded installation's media-agent
    binding names the target, so an assigned agent stays registered when the loaded show
    doesn't use its output.
    """
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
    if not agent_ids:
        agent_ids = {
            binding.target
            for binding in runtime.catalog.installation.resources
            if binding.resource in resources and binding.kind is ResourceKind.MEDIA_AGENT
        }
    if len(agent_ids) > 1:
        raise ValueError("device media assignments map to more than one runtime target")
    return next(iter(agent_ids), None)


def _runtime_capabilities(hello: DeviceHello) -> tuple[str, ...]:
    """Capability kinds plus the media player features the controller runtime checks."""
    features = {
        "seamless_transition": MEDIA_SEAMLESS_CAPABILITY,
        "repeat_count": MEDIA_REPEAT_CAPABILITY,
    }
    return (
        *(capability.kind for capability in hello.capabilities.capabilities),
        *(features[feature] for feature in hello.media_features if feature in features),
    )
