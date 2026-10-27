"""Controller-managed media agent updates: serve bundles and drive per-device installs."""

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, get_args
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from pydantic import AnyUrl

from cueverge.agent_bundle import AgentBundleError, read_manifest, sha256_file
from cueverge.gui.contracts import (
    UPDATABLE_AGENT_STATES,
    AgentBundleResponse,
    AgentUpdatesResponse,
    DeviceAgentUpdateResponse,
)
from cueverge.gui.device_session import DeviceSessionError, DeviceSessionRegistry
from cueverge.gui.repository import GuiRepository, StoredDevice
from cueverge.protocol.device_v2 import (
    AgentPlatform,
    DeviceAgentUpdateAck,
    DeviceAgentUpdateCommand,
    DeviceKind,
)

LOGGER = logging.getLogger(__name__)
# Download, unpack and self-check of a ~75 MB bundle on a slow host.
AGENT_UPDATE_REQUEST_TIMEOUT_SECONDS = 900.0
# The launcher's 180 s trial plus service restart delays.
AGENT_UPDATE_RECONNECT_TIMEOUT_SECONDS = 300.0


class AgentUpdateError(RuntimeError):
    pass


class AgentUpdatePreconditionError(AgentUpdateError):
    pass


class UpdateRuntime(Protocol):
    def status(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class AgentBundle:
    platform: AgentPlatform
    version: str
    build: str
    digest_sha256: str
    size_bytes: int
    path: Path

    def response(self) -> AgentBundleResponse:
        return AgentBundleResponse(
            platform=self.platform,
            version=self.version,
            build=self.build,
            digest_sha256=self.digest_sha256,
            size_bytes=self.size_bytes,
        )


class AgentBundleCatalog:
    """The bundles under CUEVERGE_AGENT_BUNDLE_ROOT, at most one per platform."""

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self.bundles: dict[AgentPlatform, AgentBundle] = {}
        faults: list[str] = []
        if root is not None:
            if not root.is_dir():
                faults.append(f"Agent bundle directory {root} doesn't exist.")
            else:
                for path in sorted(root.glob("*.zip")):
                    try:
                        manifest = read_manifest(path)
                    except AgentBundleError as error:
                        faults.append(str(error))
                        continue
                    if manifest.platform in self.bundles:
                        faults.append(
                            f"More than one bundle for {manifest.platform}; {path.name} ignored."
                        )
                        continue
                    self.bundles[manifest.platform] = AgentBundle(
                        platform=manifest.platform,
                        version=manifest.version,
                        build=manifest.build,
                        digest_sha256=sha256_file(path),
                        size_bytes=path.stat().st_size,
                        path=path,
                    )
        self.faults = tuple(faults)
        for fault in self.faults:
            LOGGER.warning("%s", fault)

    def by_digest(self, digest: str) -> AgentBundle | None:
        return next(
            (bundle for bundle in self.bundles.values() if bundle.digest_sha256 == digest), None
        )


@dataclass
class _Pending:
    digest: str
    started_at: float
    phase: str = "installing"
    error: str | None = None


class DeviceAgentUpdateService:
    def __init__(
        self,
        repository: GuiRepository,
        sessions: DeviceSessionRegistry,
        runtime: UpdateRuntime,
        catalog: AgentBundleCatalog,
        public_url: AnyUrl | None,
        *,
        request_timeout_seconds: float = AGENT_UPDATE_REQUEST_TIMEOUT_SECONDS,
        reconnect_timeout_seconds: float = AGENT_UPDATE_RECONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self.repository = repository
        self.sessions = sessions
        self.runtime = runtime
        self.catalog = catalog
        self.public_url = public_url
        self.request_timeout_seconds = request_timeout_seconds
        self.reconnect_timeout_seconds = reconnect_timeout_seconds
        self._pending: dict[str, _Pending] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def overview(self) -> AgentUpdatesResponse:
        return AgentUpdatesResponse(
            bundles=tuple(
                self.catalog.bundles[platform].response()
                for platform in get_args(AgentPlatform)
                if platform in self.catalog.bundles
            ),
            bundle_faults=self.catalog.faults,
            devices=self.statuses(),
        )

    def statuses(self) -> tuple[DeviceAgentUpdateResponse, ...]:
        return tuple(
            self._status(device)
            for device in self.repository.list_devices()
            if device.device_kind is DeviceKind.DIRECT_MEDIA_AGENT
        )

    def status(self, device_id: str) -> DeviceAgentUpdateResponse | None:
        return next((status for status in self.statuses() if status.device_id == device_id), None)

    async def start(
        self, device_ids: Sequence[str] | None = None
    ) -> tuple[DeviceAgentUpdateResponse, ...]:
        """Start installs on the given devices, or every device with an update available."""
        self._require_idle_maintenance()
        statuses = {status.device_id: status for status in self.statuses()}
        if device_ids is None:
            targets = [
                status for status in statuses.values() if status.state in UPDATABLE_AGENT_STATES
            ]
        else:
            targets = []
            for device_id in dict.fromkeys(device_ids):
                status = statuses.get(device_id)
                if status is None:
                    raise AgentUpdateError(f"Media agent {device_id} not found")
                if status.state not in UPDATABLE_AGENT_STATES:
                    raise AgentUpdateError(
                        f"{status.display_name} can't be updated: "
                        f"{status.detail or status.state.replace('_', ' ')}"
                    )
                targets.append(status)
        if self.public_url is None:
            raise AgentUpdateError("Agent updates need the device transport public URL")
        for status in targets:
            assert status.available is not None
            bundle = self.catalog.bundles[status.available.platform]
            self._pending[status.device_id] = _Pending(bundle.digest_sha256, time.monotonic())
            task = asyncio.create_task(self._install(status.device_id, bundle))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return tuple(
            status
            for status in self.statuses()
            if status.device_id in {t.device_id for t in targets}
        )

    def download_url(self, bundle: AgentBundle) -> str:
        assert self.public_url is not None
        parsed = urlsplit(str(self.public_url))
        path = f"/api/device/v2/agent-bundles/{bundle.digest_sha256}"
        return urlunsplit(("https", parsed.netloc, path, "", ""))

    async def _install(self, device_id: str, bundle: AgentBundle) -> None:
        pending = self._pending[device_id]
        command = DeviceAgentUpdateCommand(
            request_id=uuid4(),
            device_session_id=UUID(int=0),
            controller_epoch=self.sessions.controller_epoch,
            version=bundle.version,
            platform=bundle.platform,
            digest_sha256=bundle.digest_sha256,
            size_bytes=bundle.size_bytes,
            download_url=AnyUrl(self.download_url(bundle)),
        )
        try:
            acknowledgment = await self.sessions.request(
                UUID(device_id),
                command.request_id,
                command,
                timeout_seconds=self.request_timeout_seconds,
            )
        except (DeviceSessionError, TimeoutError) as error:
            pending.error = str(error) or "The media host didn't answer the update request."
        else:
            if not isinstance(acknowledgment, DeviceAgentUpdateAck):
                pending.error = "The media host sent an unexpected answer."
            elif acknowledgment.status == "error":
                pending.error = acknowledgment.error
            else:
                pending.phase = "restarting"
                pending.started_at = time.monotonic()
        outcome = "error" if pending.error else "restarting"
        self.repository.append_audit(
            actor="controller",
            action="device.agent_update.install",
            target=device_id,
            result="error" if pending.error else "success",
            occurred_at=_now_iso(),
            authorization_context={
                "bundle_digest": bundle.digest_sha256,
                "outcome": outcome,
                **({"message": pending.error} if pending.error else {}),
            },
        )

    def _require_idle_maintenance(self) -> None:
        status = self.runtime.status()
        operations = status.get("operations")
        mode = operations.get("mode") if isinstance(operations, dict) else None
        if mode != "maintenance" or status.get("active_scene") is not None:
            raise AgentUpdatePreconditionError(
                "Switch to maintenance mode and stop the show before updating media agents."
            )

    def _status(self, device: StoredDevice) -> DeviceAgentUpdateResponse:
        reported = self.sessions.agent_release(UUID(device.id))
        supports_update, release = reported if reported is not None else (False, None)
        base = DeviceAgentUpdateResponse(
            device_id=device.id,
            display_name=device.display_name,
            connected=reported is not None,
            state="disconnected",
            managed=release.managed if release else False,
            platform=release.platform if release else None,
            running_version=release.version if release else None,
            running_digest=release.bundle_digest if release else None,
        )
        bundle = (
            self.catalog.bundles.get(release.platform) if release and release.platform else None
        )
        if bundle is not None:
            base = base.model_copy(update={"available": bundle.response()})
        pending = self._pending.get(device.id)
        if pending is not None and release is not None and release.bundle_digest == pending.digest:
            del self._pending[device.id]
            pending = None
        if pending is not None:
            if pending.error is not None:
                return base.model_copy(update={"state": "failed", "detail": pending.error})
            if pending.phase == "installing":
                return base.model_copy(
                    update={"state": "updating", "detail": "Downloading and installing."}
                )
            last = release.last_update if release else None
            if (
                last is not None
                and last.bundle_digest == pending.digest
                and last.outcome == "rolled_back"
            ):
                del self._pending[device.id]
            elif time.monotonic() - pending.started_at > self.reconnect_timeout_seconds:
                pending.error = "The media host didn't reconnect after restarting into the update."
                return base.model_copy(update={"state": "failed", "detail": pending.error})
            else:
                return base.model_copy(
                    update={"state": "updating", "detail": "Restarting into the new version."}
                )
        if reported is None:
            return base
        if release is None or not release.managed or not supports_update:
            return base.model_copy(
                update={
                    "state": "unmanaged",
                    "detail": "Install once with cueverge-agent-install to enable managed updates.",
                }
            )
        if release.platform is None:
            return base.model_copy(
                update={
                    "state": "unsupported_platform",
                    "detail": "No bundles exist for this host.",
                }
            )
        if bundle is None:
            return base.model_copy(
                update={
                    "state": "no_bundle",
                    "detail": f"This controller has no {release.platform} agent bundle.",
                }
            )
        if release.bundle_digest == bundle.digest_sha256:
            return base.model_copy(update={"state": "current"})
        last = release.last_update
        if (
            last is not None
            and last.bundle_digest == bundle.digest_sha256
            and last.outcome == "rolled_back"
        ):
            return base.model_copy(update={"state": "rolled_back", "detail": last.detail})
        return base.model_copy(update={"state": "available"})


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
