import asyncio
import hashlib
import json
import logging
import shutil
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
    DeviceArtifactInventory,
    DeviceArtifactRecord,
    DeviceArtifactTransferStatus,
    DeviceCommandAck,
    DeviceHeartbeat,
    DeviceHeartbeatAck,
    DeviceHello,
    DeviceMediaCommand,
    DeviceMediaPosition,
    DeviceTimingReport,
    DeviceWelcome,
)
from cueverge.protocol.models import AgentCommand

LOGGER = logging.getLogger(__name__)

ArtifactInventorySnapshot = tuple[
    int,
    int,
    bool,
    bool,
    tuple[DeviceArtifactRecord, ...],
    tuple[DeviceArtifactTransferStatus, ...],
    tuple[str, ...],
]


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
        self._heartbeat_sent: dict[int, tuple[float, int]] = {}

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
        self._heartbeat_sent.clear()
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
            asyncio.create_task(self._inventory_once(websocket, send_lock, session_id, welcome)),
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
                sent = self._heartbeat_sent.pop(acknowledgment.sequence, None)
                if sent is None:
                    raise DevicePresenceError("controller acknowledged an unknown heartbeat")
                received_monotonic_ms = time.monotonic() * 1000
                received_unix_ms = time.time_ns() // 1_000_000
                round_trip_ms, clock_offset_ms = calculate_timing_metrics(
                    sent_monotonic_ms=sent[0],
                    sent_unix_ms=sent[1],
                    received_monotonic_ms=received_monotonic_ms,
                    received_unix_ms=received_unix_ms,
                    controller_receive_unix_ms=acknowledgment.controller_receive_unix_ms,
                    controller_send_unix_ms=acknowledgment.controller_send_unix_ms,
                )
                report = DeviceTimingReport(
                    device_id=self.identity.device_id,
                    device_session_id=session_id,
                    controller_epoch=welcome.controller_epoch,
                    heartbeat_sequence=acknowledgment.sequence,
                    round_trip_ms=round(round_trip_ms, 1),
                    clock_offset_ms=round(clock_offset_ms, 1),
                )
                async with send_lock:
                    await websocket.send(report.model_dump_json())
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
            sent_monotonic_ms = time.monotonic() * 1000
            sent_unix_ms = time.time_ns() // 1_000_000
            message = DeviceHeartbeat(
                device_id=self.identity.device_id,
                device_session_id=session_id,
                controller_epoch=welcome.controller_epoch,
                sequence=sequence,
                device_monotonic_ms=round(sent_monotonic_ms),
                device_unix_ms=sent_unix_ms,
            )
            self._heartbeat_sent[sequence] = (sent_monotonic_ms, sent_unix_ms)
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

    async def _inventory_once(
        self,
        websocket: ClientConnection,
        send_lock: asyncio.Lock,
        session_id: UUID,
        welcome: DeviceWelcome,
    ) -> None:
        (
            total_bytes,
            free_bytes,
            supports_pinning,
            supports_resumable_transfer,
            artifacts,
            transfers,
            faults,
        ) = await asyncio.to_thread(
            scan_artifact_inventory,
            self.media_client.config.asset_root,
        )
        inventory = DeviceArtifactInventory(
            device_id=self.identity.device_id,
            device_session_id=session_id,
            controller_epoch=welcome.controller_epoch,
            total_bytes=total_bytes,
            free_bytes=free_bytes,
            supports_pinning=supports_pinning,
            supports_resumable_transfer=supports_resumable_transfer,
            artifacts=artifacts,
            transfers=transfers,
            faults=faults,
        )
        async with send_lock:
            await websocket.send(inventory.model_dump_json())


def calculate_timing_metrics(
    *,
    sent_monotonic_ms: float,
    sent_unix_ms: int,
    received_monotonic_ms: float,
    received_unix_ms: int,
    controller_receive_unix_ms: int,
    controller_send_unix_ms: int,
) -> tuple[float, float]:
    server_processing_ms = controller_send_unix_ms - controller_receive_unix_ms
    round_trip_ms = max(
        0.0,
        received_monotonic_ms - sent_monotonic_ms - server_processing_ms,
    )
    clock_offset_ms = (
        (controller_receive_unix_ms - sent_unix_ms) + (controller_send_unix_ms - received_unix_ms)
    ) / 2
    return round(round_trip_ms, 1), round(clock_offset_ms, 1)


def scan_artifact_inventory(
    asset_root: Path,
    *,
    maximum_files: int = 10_000,
) -> ArtifactInventorySnapshot:
    storage_path = asset_root if asset_root.exists() else asset_root.parent
    usage = shutil.disk_usage(storage_path)
    if not asset_root.exists():
        return usage.total, usage.free, False, False, (), (), ("Asset root does not exist",)
    (
        supports_pinning,
        supports_resumable_transfer,
        pinned_digests,
        transfers,
        cache_faults,
    ) = _load_cache_state(asset_root)
    artifacts: dict[str, int] = {}
    unreadable = 0
    discovered = 0
    truncated = False
    for path in asset_root.rglob("*"):
        if path.name == ".cueverge-cache-state.json" or path.is_symlink() or not path.is_file():
            continue
        discovered += 1
        if discovered > maximum_files:
            truncated = True
            break
        try:
            digest = hashlib.sha256()
            size_bytes = 0
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size_bytes += len(chunk)
            artifacts.setdefault(digest.hexdigest(), size_bytes)
        except OSError:
            unreadable += 1
    faults = list(cache_faults)
    if unreadable:
        faults.append(f"{unreadable} asset(s) could not be read")
    if truncated:
        faults.append(f"Artifact inventory exceeded {maximum_files} files")
    missing_pins = pinned_digests.difference(artifacts)
    if missing_pins:
        faults.append(f"{len(missing_pins)} pinned artifact(s) are missing")
    return (
        usage.total,
        usage.free,
        supports_pinning,
        supports_resumable_transfer,
        tuple(
            DeviceArtifactRecord(
                digest_sha256=digest,
                size_bytes=size,
                pinned=digest in pinned_digests,
            )
            for digest, size in sorted(artifacts.items())
        ),
        transfers,
        tuple(faults),
    )


def _load_cache_state(
    asset_root: Path,
) -> tuple[
    bool,
    bool,
    set[str],
    tuple[DeviceArtifactTransferStatus, ...],
    tuple[str, ...],
]:
    state_path = asset_root / ".cueverge-cache-state.json"
    if not state_path.exists():
        return False, False, set(), (), ()
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("cache state must be an object")
        pinned_value = value.get("pinned_digests", [])
        transfer_value = value.get("transfers", [])
        if not isinstance(pinned_value, list) or not all(
            isinstance(digest, str) for digest in pinned_value
        ):
            raise ValueError("pinned_digests must be a string array")
        if not isinstance(transfer_value, list):
            raise ValueError("transfers must be an array")
        transfers = tuple(
            DeviceArtifactTransferStatus.model_validate(transfer) for transfer in transfer_value
        )
        return (
            bool(value.get("supports_pinning", False)),
            bool(value.get("supports_resumable_transfer", False)),
            set(pinned_value),
            transfers,
            (),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return False, False, set(), (), ("Cache state metadata is invalid",)
