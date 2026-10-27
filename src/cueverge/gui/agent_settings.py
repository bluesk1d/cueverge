"""Controller-managed media agent settings: store overrides and push them to agents."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from cueverge.gui.contracts import DeviceAgentSettingsResponse
from cueverge.gui.device_session import DeviceSessionError, DeviceSessionRegistry
from cueverge.gui.repository import (
    DeviceAgentSettingsConflictError,
    DeviceNotFoundError,
    GuiRepository,
    StoredDevice,
)
from cueverge.protocol.device_v2 import (
    AgentSettings,
    DeviceAgentSettingsAck,
    DeviceAgentSettingsCommand,
    DeviceKind,
)

LOGGER = logging.getLogger(__name__)
# Includes a player restart and audio device discovery on the host.
AGENT_SETTINGS_TIMEOUT_SECONDS = 60.0


class AgentSettingsError(RuntimeError):
    pass


class SettingsRuntime(Protocol):
    def status(self) -> dict[str, object]: ...


@dataclass
class _PushFailure:
    revision: int
    detail: str


class DeviceAgentSettingsService:
    def __init__(
        self,
        repository: GuiRepository,
        sessions: DeviceSessionRegistry,
        runtime: SettingsRuntime,
        *,
        timeout_seconds: float = AGENT_SETTINGS_TIMEOUT_SECONDS,
    ) -> None:
        self.repository = repository
        self.sessions = sessions
        self.runtime = runtime
        self.timeout_seconds = timeout_seconds
        self._failures: dict[str, _PushFailure] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def view(self, device_id: str) -> DeviceAgentSettingsResponse:
        device = self._media_agent(device_id)
        revision, overrides = self.repository.get_device_agent_settings(device.id)
        reported = self.sessions.agent_settings(UUID(device.id))
        supported, state = reported if reported is not None else (False, None)
        response = DeviceAgentSettingsResponse(
            device_id=device.id,
            display_name=device.display_name,
            connected=reported is not None,
            supported=supported,
            revision=revision,
            overrides=overrides,
            sync="offline",
            applied_revision=state.revision if state else None,
            effective=state.effective if state else None,
            host_defaults=state.host_defaults if state else None,
            pending_player_restart=state.pending_player_restart if state else (),
            displays=state.displays if state else (),
            audio_devices=state.audio_devices if state else (),
        )
        if reported is None:
            detail = "Saved settings apply when the device connects." if revision else None
            return response.model_copy(update={"detail": detail})
        if not supported or state is None:
            return response.model_copy(
                update={
                    "sync": "unsupported",
                    "detail": "Update this agent to manage its settings from the controller.",
                }
            )
        failure = self._failures.get(device.id)
        if failure is not None and failure.revision == revision:
            return response.model_copy(update={"sync": "failed", "detail": failure.detail})
        if state.revision == revision:
            detail = (
                "Some settings apply when the player restarts: switch to maintenance mode "
                "with nothing playing and choose Apply now, or restart the player."
                if state.pending_player_restart
                else None
            )
            return response.model_copy(update={"sync": "applied", "detail": detail})
        return response.model_copy(update={"sync": "pending", "detail": "Sending to the agent."})

    async def update(
        self,
        device_id: str,
        settings: AgentSettings,
        *,
        expected_revision: int,
        actor: str,
    ) -> DeviceAgentSettingsResponse:
        device = self._media_agent(device_id)
        try:
            self.repository.set_device_agent_settings(
                device_id=device.id,
                settings=settings,
                expected_revision=expected_revision,
                actor=actor,
                occurred_at=datetime.now(UTC).isoformat(),
            )
        except DeviceAgentSettingsConflictError as error:
            raise AgentSettingsError(
                f"{error}; reload the device settings and try again"
            ) from error
        await self.push(device.id)
        return self.view(device.id)

    async def push(self, device_id: str) -> None:
        """Send the stored settings to a connected agent and wait for its answer."""
        revision, settings = self.repository.get_device_agent_settings(device_id)
        reported = self.sessions.agent_settings(UUID(device_id))
        if revision == 0 or reported is None or not reported[0]:
            return
        command = DeviceAgentSettingsCommand(
            request_id=uuid4(),
            device_session_id=UUID(int=0),
            controller_epoch=self.sessions.controller_epoch,
            revision=revision,
            settings=settings,
            allow_player_restart=self._idle_maintenance(),
        )
        try:
            acknowledgment = await self.sessions.request(
                UUID(device_id),
                command.request_id,
                command,
                timeout_seconds=self.timeout_seconds,
            )
        except (DeviceSessionError, TimeoutError) as error:
            self._failures[device_id] = _PushFailure(
                revision, str(error) or "The media host didn't answer in time."
            )
            return
        if not isinstance(acknowledgment, DeviceAgentSettingsAck):
            self._failures[device_id] = _PushFailure(revision, "Unexpected answer from the host.")
        elif acknowledgment.status == "error":
            self._failures[device_id] = _PushFailure(
                revision, acknowledgment.error or "The host refused the settings."
            )
        else:
            self._failures.pop(device_id, None)

    def sync_on_connect(self, device_id: str, reported_revision: int | None) -> None:
        """Send stored settings to an agent that connected with an older revision."""
        revision, _ = self.repository.get_device_agent_settings(device_id)
        if revision == 0 or reported_revision == revision:
            return
        task = asyncio.create_task(self.push(device_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _idle_maintenance(self) -> bool:
        status = self.runtime.status()
        operations = status.get("operations")
        mode = operations.get("mode") if isinstance(operations, dict) else None
        return mode == "maintenance" and status.get("active_scene") is None

    def _media_agent(self, device_id: str) -> StoredDevice:
        try:
            device = self.repository.get_device(device_id)
        except DeviceNotFoundError as error:
            raise AgentSettingsError(f"Device {device_id} not found") from error
        if device.device_kind is not DeviceKind.DIRECT_MEDIA_AGENT:
            raise AgentSettingsError("Only media agents have agent settings")
        return device
