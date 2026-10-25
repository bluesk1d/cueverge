import asyncio
import hashlib
import json
import logging
import os
import shutil
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AnyUrl, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from cueverge.media_agent.client import MediaAgentClient
from cueverge.media_agent.device_enrollment import StoredDeviceIdentity
from cueverge.protocol.device_v2 import (
    DeviceArtifactActivateAck,
    DeviceArtifactActivateCommand,
    DeviceArtifactCleanupAck,
    DeviceArtifactCleanupCommand,
    DeviceArtifactInventory,
    DeviceArtifactRecord,
    DeviceArtifactStageAck,
    DeviceArtifactStageCommand,
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
            media_features=self.media_client.media_features,
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
                media_command = DeviceMediaCommand.model_validate(message)
                internal = AgentCommand(
                    request_id=str(media_command.request_id),
                    command=media_command.command,
                    agent_id=self.media_client.runtime_identity,
                    agent_session_id=str(session_id),
                    controller_epoch=str(welcome.controller_epoch),
                    scene_id=media_command.scene_id,
                    scene_instance_id=str(media_command.scene_instance_id),
                    playback_session_id=str(media_command.playback_session_id),
                    asset_id=media_command.asset_id,
                    end_behavior=media_command.end_behavior,
                    target_position_ms=media_command.target_position_ms,
                    repeat_count=media_command.repeat_count,
                    auto_advance=media_command.auto_advance,
                )
                result = await self.media_client.handle_command(internal)
                response = DeviceCommandAck(
                    request_id=media_command.request_id,
                    device_session_id=session_id,
                    controller_epoch=welcome.controller_epoch,
                    status=result.status,
                    error_code=result.error_code,
                    detail=result.detail,
                )
                async with send_lock:
                    await websocket.send(response.model_dump_json())
            elif message_type == "device.artifact.stage":
                stage_command = DeviceArtifactStageCommand.model_validate(message)
                _validate_deployment_command(stage_command, session_id, welcome)
                try:
                    await asyncio.to_thread(self._stage_artifact, stage_command)
                    stage_ack = DeviceArtifactStageAck(
                        request_id=stage_command.request_id,
                        device_session_id=session_id,
                        controller_epoch=welcome.controller_epoch,
                        activation_identity=stage_command.activation_identity,
                        digest_sha256=stage_command.digest_sha256,
                        status="completed",
                    )
                except (OSError, ValueError, DevicePresenceError) as error:
                    stage_ack = DeviceArtifactStageAck(
                        request_id=stage_command.request_id,
                        device_session_id=session_id,
                        controller_epoch=welcome.controller_epoch,
                        activation_identity=stage_command.activation_identity,
                        digest_sha256=stage_command.digest_sha256,
                        status="error",
                        error=str(error),
                    )
                async with send_lock:
                    await websocket.send(stage_ack.model_dump_json())
            elif message_type == "device.artifact.activate":
                activate_command = DeviceArtifactActivateCommand.model_validate(message)
                _validate_deployment_command(activate_command, session_id, welcome)
                try:
                    self._activate_artifact(activate_command)
                    activation_ack = DeviceArtifactActivateAck(
                        request_id=activate_command.request_id,
                        device_session_id=session_id,
                        controller_epoch=welcome.controller_epoch,
                        activation_identity=activate_command.activation_identity,
                        status="completed",
                    )
                except (OSError, ValueError, DevicePresenceError) as error:
                    activation_ack = DeviceArtifactActivateAck(
                        request_id=activate_command.request_id,
                        device_session_id=session_id,
                        controller_epoch=welcome.controller_epoch,
                        activation_identity=activate_command.activation_identity,
                        status="error",
                        error=str(error),
                    )
                async with send_lock:
                    await websocket.send(activation_ack.model_dump_json())
            elif message_type == "device.artifact.cleanup":
                cleanup_command = DeviceArtifactCleanupCommand.model_validate(message)
                _validate_deployment_command(cleanup_command, session_id, welcome)
                removed, rejected = await asyncio.to_thread(
                    self._cleanup_artifacts,
                    cleanup_command,
                )
                cleanup_ack = DeviceArtifactCleanupAck(
                    request_id=cleanup_command.request_id,
                    device_session_id=session_id,
                    controller_epoch=welcome.controller_epoch,
                    removed_digests=removed,
                    rejected_digests=rejected,
                )
                async with send_lock:
                    await websocket.send(cleanup_ack.model_dump_json())
            else:
                raise DevicePresenceError(f"unsupported controller message: {message_type}")

    def _stage_artifact(self, command: DeviceArtifactStageCommand) -> None:
        asset_root = self.media_client.config.asset_root
        asset_root.mkdir(parents=True, exist_ok=True)
        target = asset_root / command.digest_sha256
        if target.is_file():
            if (
                target.stat().st_size == command.size_bytes
                and _sha256_file(target) == command.digest_sha256
            ):
                _update_cache_state(
                    asset_root,
                    command.digest_sha256,
                    protected_digests=command.protected_digests,
                )
                return
            target.unlink()
        partial = asset_root / f".{command.digest_sha256}.part"
        received = partial.stat().st_size if partial.exists() else 0
        if received > command.size_bytes:
            partial.unlink()
            received = 0
        _ensure_cache_capacity(
            asset_root,
            required_bytes=command.size_bytes - received,
            protected_digests=set(command.protected_digests) | {command.digest_sha256},
        )
        _set_transfer_state(
            asset_root,
            command.digest_sha256,
            received,
            command.size_bytes,
        )
        headers = {"Range": f"bytes={received}-"} if received else {}
        request = urllib.request.Request(
            str(command.download_url),
            headers=headers,
        )
        with urllib.request.urlopen(
            request,
            context=self.ssl_context,
            timeout=60,
        ) as response:
            if received and response.status != 206:
                partial.unlink(missing_ok=True)
                received = 0
            mode = "ab" if received else "wb"
            with partial.open(mode) as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    _set_transfer_state(
                        asset_root,
                        command.digest_sha256,
                        output.tell(),
                        command.size_bytes,
                    )
        if partial.stat().st_size != command.size_bytes:
            raise DevicePresenceError("staged artifact size does not match manifest")
        if _sha256_file(partial) != command.digest_sha256:
            raise DevicePresenceError("staged artifact checksum does not match manifest")
        os.replace(partial, target)
        _update_cache_state(
            asset_root,
            command.digest_sha256,
            protected_digests=command.protected_digests,
        )

    def _activate_artifact(self, command: DeviceArtifactActivateCommand) -> None:
        asset_root = self.media_client.config.asset_root
        pending = asset_root / ".cueverge-activation.pending.json"
        previous = asset_root / ".cueverge-activation.previous.json"
        active = asset_root / ".cueverge-activation.json"
        if command.phase == "rollback":
            pending.unlink(missing_ok=True)
            if previous.exists():
                os.replace(previous, active)
            return
        target = asset_root / command.digest_sha256
        if not target.is_file() or _sha256_file(target) != command.digest_sha256:
            raise DevicePresenceError("activation artifact is not staged")
        state = {
            "activation_identity": str(command.activation_identity),
            "resource": command.resource,
            "digest_sha256": command.digest_sha256,
            "media_end_behavior": command.media_end_behavior,
            "duration_ms": command.duration_ms,
            "frame_rate": {
                "numerator": command.frame_rate_numerator,
                "denominator": command.frame_rate_denominator,
            },
            "frame_time_origin_ms": command.frame_time_origin_ms,
            "activated_at_unix_ms": time.time_ns() // 1_000_000,
        }
        if command.phase == "prepare":
            state["activation_at_unix_ms"] = command.activation_at_unix_ms
            pending.write_text(
                json.dumps(state, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            return
        if command.activation_at_unix_ms is not None:
            if not pending.exists():
                raise DevicePresenceError("activation was not prepared")
            prepared = json.loads(pending.read_text(encoding="utf-8"))
            if (
                not isinstance(prepared, dict)
                or prepared.get("activation_identity") != str(command.activation_identity)
                or prepared.get("digest_sha256") != command.digest_sha256
                or prepared.get("activation_at_unix_ms") != command.activation_at_unix_ms
            ):
                raise DevicePresenceError("prepared activation does not match commit")
            delay_ms = command.activation_at_unix_ms - time.time_ns() // 1_000_000
            if delay_ms > 0:
                time.sleep(delay_ms / 1000)
        temporary = asset_root / ".cueverge-activation.tmp"
        temporary.write_text(
            json.dumps(state, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        if active.exists():
            shutil.copy2(active, previous)
        os.replace(temporary, active)
        pending.unlink(missing_ok=True)

    def _cleanup_artifacts(
        self,
        command: DeviceArtifactCleanupCommand,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        asset_root = self.media_client.config.asset_root
        _, _, pinned, _, _ = _load_cache_state(asset_root)
        active = _active_digest(asset_root)
        removed: list[str] = []
        rejected: list[str] = []
        for digest in command.digests:
            if digest in pinned or digest == active:
                rejected.append(digest)
                continue
            path = asset_root / digest
            if path.is_file():
                path.unlink()
                removed.append(digest)
        return tuple(removed), tuple(rejected)

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
    artifacts: dict[str, tuple[int, Literal["verified", "corrupt"], int]] = {}
    unreadable = 0
    discovered = 0
    truncated = False
    for path in asset_root.rglob("*"):
        if (
            path.name.startswith(".")
            or path.name == ".cueverge-activation.json"
            or path.is_symlink()
            or not path.is_file()
        ):
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
            computed_digest = digest.hexdigest()
            filename_is_digest = len(path.name) == 64 and all(
                character in "0123456789abcdef" for character in path.name
            )
            inventory_digest = path.name if filename_is_digest else computed_digest
            state: Literal["verified", "corrupt"] = (
                "corrupt"
                if filename_is_digest and computed_digest != inventory_digest
                else "verified"
            )
            artifacts.setdefault(
                inventory_digest,
                (size_bytes, state, path.stat().st_mtime_ns // 1_000_000),
            )
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
                size_bytes=value[0],
                pinned=digest in pinned_digests,
                in_use=digest == _active_digest(asset_root),
                state=value[1],
                last_used_unix_ms=value[2],
            )
            for digest, value in sorted(artifacts.items())
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_deployment_command(
    command: (
        DeviceArtifactStageCommand | DeviceArtifactActivateCommand | DeviceArtifactCleanupCommand
    ),
    session_id: UUID,
    welcome: DeviceWelcome,
) -> None:
    if command.device_session_id != session_id:
        raise DevicePresenceError("artifact command device session was fenced")
    if command.controller_epoch != welcome.controller_epoch:
        raise DevicePresenceError("artifact command controller epoch was fenced")


def _update_cache_state(
    asset_root: Path,
    digest: str,
    *,
    protected_digests: tuple[str, ...] = (),
) -> None:
    state_path = asset_root / ".cueverge-cache-state.json"
    value: dict[str, object] = {}
    if state_path.exists():
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise DevicePresenceError("cache state metadata is invalid")
        value = loaded
    pins = value.get("pinned_digests", [])
    if not isinstance(pins, list) or not all(isinstance(item, str) for item in pins):
        raise DevicePresenceError("cache pin metadata is invalid")
    active_digest = _active_digest(asset_root)
    value.update(
        {
            "supports_pinning": True,
            "supports_resumable_transfer": True,
            "pinned_digests": sorted(
                set(protected_digests) | {digest} | ({active_digest} if active_digest else set())
            ),
            "transfers": [],
        }
    )
    temporary = asset_root / ".cueverge-cache-state.tmp"
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, state_path)


def _set_transfer_state(
    asset_root: Path,
    digest: str,
    received_bytes: int,
    total_bytes: int,
) -> None:
    state_path = asset_root / ".cueverge-cache-state.json"
    value: dict[str, object] = {}
    if state_path.exists():
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise DevicePresenceError("cache state metadata is invalid")
        value = loaded
    value.update(
        {
            "supports_pinning": True,
            "supports_resumable_transfer": True,
            "pinned_digests": value.get("pinned_digests", []),
            "transfers": [
                {
                    "digest_sha256": digest,
                    "received_bytes": received_bytes,
                    "total_bytes": total_bytes,
                    "state": "active",
                    "fault": None,
                }
            ],
        }
    )
    temporary = asset_root / ".cueverge-cache-state.tmp"
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, state_path)


def _ensure_cache_capacity(
    asset_root: Path,
    *,
    required_bytes: int,
    protected_digests: set[str],
) -> None:
    if required_bytes <= 0:
        return
    free = shutil.disk_usage(asset_root).free
    if free >= required_bytes:
        return
    active_digest = _active_digest(asset_root)
    protected = protected_digests | ({active_digest} if active_digest else set())
    candidates = sorted(
        (
            path
            for path in asset_root.iterdir()
            if path.is_file() and len(path.name) == 64 and path.name not in protected
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    reclaimed = 0
    for path in candidates:
        size = path.stat().st_size
        path.unlink()
        reclaimed += size
        if free + reclaimed >= required_bytes:
            return
    raise DevicePresenceError(
        "insufficient safe cache space; active, rollback, staged, and playing artifacts are pinned"
    )


def _active_digest(asset_root: Path) -> str:
    path = asset_root / ".cueverge-activation.json"
    if not path.exists():
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        digest = value.get("digest_sha256") if isinstance(value, dict) else None
        return digest if isinstance(digest, str) else ""
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
