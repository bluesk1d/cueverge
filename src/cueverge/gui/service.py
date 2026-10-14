from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from cueverge.controller.runtime import (
    ActiveScene,
    RuntimeAgentStatus,
    RuntimeLightingTarget,
    ShowMode,
)
from cueverge.core.models import SceneCatalog
from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.auth import AuthService
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.config import GuiConfig
from cueverge.gui.device_ca import DeviceCertificateAuthority
from cueverge.gui.device_enrollment import DeviceEnrollmentService
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.endpoint_discovery import EndpointDiscoveryService
from cueverge.gui.repository import GuiRepository
from cueverge.gui.wled_status import WledStatusObserver


class RuntimeStatusSource(Protocol):
    catalog: SceneCatalog

    def status(self) -> dict[str, object]: ...

    def agent_status(self) -> tuple[RuntimeAgentStatus, ...]: ...

    def lighting_targets(self) -> tuple[RuntimeLightingTarget, ...]: ...

    async def start_show(self) -> ActiveScene: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def seek(self, position_ms: int) -> None: ...

    async def stop(self, *, continue_queue: bool = True) -> None: ...

    async def reset_ambient(self) -> ActiveScene: ...

    async def set_show_mode(self, mode: ShowMode) -> None: ...


@dataclass(frozen=True)
class GuiServices:
    config: GuiConfig
    repository: GuiRepository
    auth: AuthService
    compiler: DraftCompiler
    runtime: RuntimeStatusSource
    device_enrollment: DeviceEnrollmentService | None
    device_sessions: DeviceSessionRegistry
    wled_status: WledStatusObserver
    endpoint_discovery: EndpointDiscoveryService
    asset_upload: AssetUploadService

    @classmethod
    def create(cls, config: GuiConfig, runtime: RuntimeStatusSource) -> "GuiServices":
        repository = GuiRepository(config.data_root / "gui.sqlite3")
        repository.migrate()
        repository.ensure_runtime_installation(
            name=runtime.catalog.package_root.name,
            occurred_at=datetime.now(UTC).isoformat(),
        )
        device_enrollment: DeviceEnrollmentService | None = None
        if config.device_enrollment_enabled:
            password = config.device_ca_key_password
            if password is None:
                raise RuntimeError("validated device CA password is missing")
            authority = DeviceCertificateAuthority(config.data_root / "device-ca", password)
            authority.provision()
            device_enrollment = DeviceEnrollmentService(config, repository, authority)
        return cls(
            config=config,
            repository=repository,
            auth=AuthService(config, repository),
            compiler=DraftCompiler(config.data_root / "compiled"),
            runtime=runtime,
            device_enrollment=device_enrollment,
            device_sessions=DeviceSessionRegistry(),
            wled_status=WledStatusObserver(),
            endpoint_discovery=EndpointDiscoveryService(),
            asset_upload=AssetUploadService(config, repository),
        )
