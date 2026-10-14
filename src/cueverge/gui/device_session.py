import threading
import time
from dataclasses import dataclass
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


@dataclass
class ActiveDeviceSession:
    device: StoredDevice
    session_id: UUID
    last_seen_at: float
    last_heartbeat_sequence: int = -1


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
        return DeviceHeartbeatAck(sequence=heartbeat.sequence, accepted=True)

    def disconnect(self, device_id: UUID, session_id: UUID) -> None:
        with self._lock:
            session = self._sessions.get(device_id)
            if session is not None and session.session_id == session_id:
                del self._sessions[device_id]

    def connected_device_ids(self) -> tuple[UUID, ...]:
        self.expire_stale()
        with self._lock:
            return tuple(sorted(self._sessions, key=str))

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
