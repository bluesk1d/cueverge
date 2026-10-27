"""Start and stop media players on agents that keep device presence without a running player."""

import asyncio
import time
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID, uuid4

from cueverge.core.models import SceneCatalog
from cueverge.gui.device_session import DeviceSessionError, DeviceSessionRegistry
from cueverge.gui.repository import DeviceNotFoundError, GuiRepository
from cueverge.protocol.device_v2 import (
    DeviceKind,
    DevicePlayerAck,
    DevicePlayerCommand,
    KeepAwakeStatus,
    PlayerState,
)

PLAYER_REQUEST_TIMEOUT_SECONDS = 30.0
PLAYER_IDLE_STOP_SECONDS = 600.0


class DevicePlayerError(RuntimeError):
    pass


class PlayerRuntime(Protocol):
    catalog: SceneCatalog

    def status(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class DevicePlayerStatus:
    device_id: str
    display_name: str
    resources: tuple[str, ...]
    connection_policy: Literal["continuous", "on_demand"]
    connected: bool
    player_control: bool
    player_state: PlayerState | None
    keep_awake: KeepAwakeStatus | None = None


class DevicePlayerService:
    def __init__(
        self,
        repository: GuiRepository,
        sessions: DeviceSessionRegistry,
        runtime: PlayerRuntime,
        *,
        idle_stop_seconds: float = PLAYER_IDLE_STOP_SECONDS,
        request_timeout_seconds: float = PLAYER_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.repository = repository
        self.sessions = sessions
        self.runtime = runtime
        self.idle_stop_seconds = idle_stop_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self._idle_since: dict[str, float] = {}

    def list(self) -> tuple[DevicePlayerStatus, ...]:
        resources: dict[str, list[str]] = {}
        for assignment in self.repository.list_resource_assignments():
            if assignment.capability_kind == "media_playback":
                resources.setdefault(assignment.device_id, []).append(assignment.resource)
        policies = self.repository.list_device_connection_policies()
        statuses = []
        for device in self.repository.list_devices():
            if device.device_kind is not DeviceKind.DIRECT_MEDIA_AGENT:
                continue
            player = self.sessions.player_status(UUID(device.id))
            statuses.append(
                DevicePlayerStatus(
                    device_id=device.id,
                    display_name=device.display_name,
                    resources=tuple(sorted(resources.get(device.id, ()))),
                    connection_policy=policies.get(device.id, "continuous"),
                    connected=player is not None,
                    player_control=player is not None and player[0],
                    player_state=player[1] if player is not None else None,
                    keep_awake=self.sessions.keep_awake(UUID(device.id)),
                )
            )
        return tuple(statuses)

    async def start(self, device_id: str) -> PlayerState:
        return await self._request(device_id, "device.player.start")

    async def stop(self, device_id: str) -> PlayerState:
        self._idle_since.pop(device_id, None)
        return await self._request(device_id, "device.player.stop")

    async def ensure_ready_for_loaded_show(self) -> tuple[str, ...]:
        """Start stopped players whose projectors the loaded show uses; return their names."""
        used = _media_resources(self.runtime.catalog)
        pending = [
            status
            for status in self.list()
            if status.connected
            and status.player_control
            and status.player_state != "ready"
            and used.intersection(status.resources)
        ]
        results = await asyncio.gather(
            *(self.start(status.device_id) for status in pending),
            return_exceptions=True,
        )
        failures = [
            f"{status.display_name}: {result}"
            for status, result in zip(pending, results, strict=True)
            if isinstance(result, BaseException)
        ]
        if failures:
            raise DevicePlayerError("Media player did not start. " + "; ".join(failures))
        return tuple(status.display_name for status in pending)

    async def stop_idle_players(self, *, now: float | None = None) -> tuple[str, ...]:
        """Stop on-demand players the active scene hasn't used for the idle period."""
        current = time.monotonic() if now is None else now
        status = self.runtime.status()
        active_scene = status.get("active_scene")
        playing = (
            _scene_media_resources(self.runtime.catalog, active_scene)
            if isinstance(active_scene, str)
            else set()
        )
        stopped: list[str] = []
        idle_ids = set()
        for player in self.list():
            if (
                player.connection_policy != "on_demand"
                or not player.player_control
                or player.player_state != "ready"
                or playing.intersection(player.resources)
            ):
                continue
            idle_ids.add(player.device_id)
            since = self._idle_since.setdefault(player.device_id, current)
            if current - since >= self.idle_stop_seconds:
                try:
                    await self.stop(player.device_id)
                except DevicePlayerError:
                    continue
                stopped.append(player.display_name)
        for device_id in set(self._idle_since).difference(idle_ids):
            del self._idle_since[device_id]
        return tuple(stopped)

    async def _request(
        self,
        device_id: str,
        kind: Literal["device.player.start", "device.player.stop"],
    ) -> PlayerState:
        try:
            device = self.repository.get_device(device_id)
        except DeviceNotFoundError as error:
            raise DevicePlayerError("Device not found") from error
        if device.device_kind is not DeviceKind.DIRECT_MEDIA_AGENT:
            raise DevicePlayerError(f"{device.display_name} is not a media player")
        player = self.sessions.player_status(UUID(device.id))
        if player is None:
            raise DevicePlayerError(f"{device.display_name} is not connected")
        if not player[0]:
            raise DevicePlayerError(
                f"{device.display_name} runs an agent that can't start or stop its player; "
                "update the agent"
            )
        request_id = uuid4()
        command = DevicePlayerCommand(
            type=kind,
            request_id=request_id,
            device_session_id=UUID(int=0),
            controller_epoch=UUID(int=0),
        )
        try:
            response = await self.sessions.request(
                UUID(device.id),
                request_id,
                command,
                timeout_seconds=self.request_timeout_seconds,
            )
        except (DeviceSessionError, TimeoutError) as error:
            raise DevicePlayerError(f"{device.display_name}: {error or 'no response'}") from error
        acknowledgment = DevicePlayerAck.model_validate(response)
        if acknowledgment.status == "error":
            raise DevicePlayerError(f"{device.display_name}: {acknowledgment.error}")
        return acknowledgment.player_state


def _media_resources(catalog: SceneCatalog) -> set[str]:
    return {
        output.definition.resource
        for scene in catalog.scenes.values()
        for output in scene.media_outputs
    }


def _scene_media_resources(catalog: SceneCatalog, scene_id: str) -> set[str]:
    scene = catalog.scenes.get(scene_id)
    if scene is None:
        return set()
    return {output.definition.resource for output in scene.media_outputs}
