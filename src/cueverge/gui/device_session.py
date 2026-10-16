import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

from cueverge.gui.repository import StoredDevice
from cueverge.protocol.device_v2 import (
    DeviceHeartbeat,
    DeviceHeartbeatAck,
    DeviceHello,
    DeviceWelcome,
)


class DeviceSessionError(RuntimeError):
    pass


class DeviceOutboundChannel(Protocol):
    async def send_device_message(self, message: object) -> None: ...


@dataclass
class ActiveDeviceSession:
    device: StoredDevice
    session_id: UUID
    last_seen_at: float
    last_heartbeat_sequence: int = -1
    output_scaling: dict[str, float] = field(default_factory=dict)
    channel: DeviceOutboundChannel | None = None


class DeviceSessionRegistry:
    def __init__(
        self,
        *,
        heartbeat_interval_ms: int = 1000,
        heartbeat_timeout_ms: int = 3000,
    ) -> None:
        if heartbeat_interval_ms <= 0 or heartbeat_timeout_ms <= heartbeat_interval_ms:
            raise ValueError("heartbeat timeout must be greater than the positive interval")
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.heartbeat_timeout_ms = heartbeat_timeout_ms
        self.controller_epoch = uuid4()
        self._sessions: dict[UUID, ActiveDeviceSession] = {}
        self._pending: dict[UUID, tuple[UUID, asyncio.Future[object]]] = {}
        self._lock = threading.Lock()

    def connect(self, device: StoredDevice, hello: DeviceHello) -> DeviceWelcome:
        if hello.device_id != UUID(device.id):
            raise DeviceSessionError("device identity does not match its certificate")
        if hello.capabilities != device.capabilities:
            raise DeviceSessionError("device capabilities differ from the enrolled document")
        with self._lock:
            self._sessions[hello.device_id] = ActiveDeviceSession(
                device=device,
                session_id=hello.device_session_id,
                last_seen_at=time.monotonic(),
            )
        return DeviceWelcome(
            device_id=hello.device_id,
            controller_epoch=self.controller_epoch,
            heartbeat_interval_ms=self.heartbeat_interval_ms,
            heartbeat_timeout_ms=self.heartbeat_timeout_ms,
        )

    def attach_channel(
        self,
        device_id: UUID,
        session_id: UUID,
        channel: DeviceOutboundChannel,
    ) -> None:
        with self._lock:
            session = self._sessions.get(device_id)
            if session is None or session.session_id != session_id:
                raise DeviceSessionError("device session is not current")
            session.channel = channel

    async def request(
        self,
        device_id: UUID,
        request_id: UUID,
        message: object,
        *,
        timeout_seconds: float = 300,
    ) -> object:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        with self._lock:
            session = self._sessions.get(device_id)
            if session is None or session.channel is None:
                raise DeviceSessionError("device is not connected for deployment")
            if request_id in self._pending:
                raise DeviceSessionError("device request ID is already pending")
            self._pending[request_id] = (device_id, future)
            channel = session.channel
            model_copy = getattr(message, "model_copy", None)
            if callable(model_copy):
                message = model_copy(
                    update={
                        "device_session_id": session.session_id,
                        "controller_epoch": self.controller_epoch,
                    }
                )
        try:
            await channel.send_device_message(message)
            return await asyncio.wait_for(future, timeout_seconds)
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def acknowledge(
        self,
        device_id: UUID,
        request_id: UUID,
        payload: object,
    ) -> None:
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or pending[0] != device_id:
                raise DeviceSessionError("device acknowledgment is not pending")
            future = pending[1]
        if not future.done():
            future.set_result(payload)

    def heartbeat(self, heartbeat: DeviceHeartbeat) -> DeviceHeartbeatAck:
        with self._lock:
            session = self._sessions.get(heartbeat.device_id)
            if session is None:
                return DeviceHeartbeatAck(
                    sequence=heartbeat.sequence,
                    accepted=False,
                    reason="device_identity_fence",
                )
            if heartbeat.device_session_id != session.session_id:
                return DeviceHeartbeatAck(
                    sequence=heartbeat.sequence,
                    accepted=False,
                    reason="device_session_fence",
                )
            if heartbeat.controller_epoch != self.controller_epoch:
                return DeviceHeartbeatAck(
                    sequence=heartbeat.sequence,
                    accepted=False,
                    reason="controller_epoch_fence",
                )
            if heartbeat.sequence <= session.last_heartbeat_sequence:
                return DeviceHeartbeatAck(
                    sequence=heartbeat.sequence,
                    accepted=False,
                    reason="out_of_order",
                )
            session.last_heartbeat_sequence = heartbeat.sequence
            session.last_seen_at = time.monotonic()
            session.output_scaling = dict(heartbeat.output_scaling)
        return DeviceHeartbeatAck(sequence=heartbeat.sequence, accepted=True)

    def output_scale(self, device_id: UUID, output_id: str) -> float | None:
        self.expire_stale()
        with self._lock:
            session = self._sessions.get(device_id)
            if session is None:
                return None
            return session.output_scaling.get(output_id)

    def disconnect(self, device_id: UUID, session_id: UUID) -> None:
        with self._lock:
            session = self._sessions.get(device_id)
            if session is not None and session.session_id == session_id:
                del self._sessions[device_id]
                pending = [
                    (request_id, future)
                    for request_id, (pending_device, future) in self._pending.items()
                    if pending_device == device_id
                ]
                for request_id, future in pending:
                    self._pending.pop(request_id, None)
                    if not future.done():
                        future.set_exception(
                            DeviceSessionError("device disconnected during request")
                        )

    def connected_device_ids(self) -> tuple[UUID, ...]:
        self.expire_stale()
        with self._lock:
            return tuple(sorted(self._sessions, key=str))

    def session_is_current(
        self,
        device_id: UUID,
        session_id: UUID,
        controller_epoch: UUID,
    ) -> bool:
        with self._lock:
            session = self._sessions.get(device_id)
            return (
                session is not None
                and session.session_id == session_id
                and controller_epoch == self.controller_epoch
            )

    def expire_stale(self) -> None:
        now = time.monotonic()
        timeout = self.heartbeat_timeout_ms / 1000
        with self._lock:
            expired = [
                device_id
                for device_id, session in self._sessions.items()
                if now - session.last_seen_at > timeout
            ]
            for device_id in expired:
                del self._sessions[device_id]
