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
from cueverge.gui.artifact_deployment import ArtifactDeploymentService
from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.auth import AuthService
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.config import GuiConfig
from cueverge.gui.device_ca import DeviceCertificateAuthority
from cueverge.gui.device_enrollment import DeviceEnrollmentService
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.endpoint_discovery import EndpointDiscoveryService
from cueverge.gui.geometry_suggestions import GeometrySuggestionService
from cueverge.gui.installation_geometry import InstallationGeometryService
from cueverge.gui.installation_photos import InstallationPhotoService
from cueverge.gui.lighting_surface import LightingSurfaceService
from cueverge.gui.mapper_export import MapperExportService
from cueverge.gui.output_arming import OutputArmService
from cueverge.gui.projector_mapping import ProjectorMappingService
from cueverge.gui.projector_overlap import ProjectorOverlapService
from cueverge.gui.render_jobs import RenderJobService
from cueverge.gui.repository import GuiRepository
from cueverge.gui.stage_coverage import StageCoverageService
from cueverge.gui.sync_rehearsal import SyncRehearsalService
from cueverge.gui.timing_calibration import TimingCalibrationService
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
    installation_photos: InstallationPhotoService
    installation_geometry: InstallationGeometryService
    projector_mapping: ProjectorMappingService
    lighting_surface: LightingSurfaceService
    timing_calibration: TimingCalibrationService
    projector_overlap: ProjectorOverlapService
    geometry_suggestions: GeometrySuggestionService
    output_arming: OutputArmService
    stage_coverage: StageCoverageService
    mapper_export: MapperExportService
    render_jobs: RenderJobService
    sync_rehearsal: SyncRehearsalService
    artifact_deployment: ArtifactDeploymentService

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
        installation_geometry = InstallationGeometryService(repository)
        lighting_surface = LightingSurfaceService(repository, installation_geometry)
        asset_upload = AssetUploadService(config, repository)
        projector_overlap = ProjectorOverlapService(repository)
        stage_coverage = StageCoverageService(repository)
        mapper_export = MapperExportService(repository, lighting_surface)
        sync_rehearsal = SyncRehearsalService(repository)
        device_sessions = DeviceSessionRegistry()
        return cls(
            config=config,
            repository=repository,
            auth=AuthService(config, repository),
            compiler=DraftCompiler(config.data_root / "compiled"),
            runtime=runtime,
            device_enrollment=device_enrollment,
            device_sessions=device_sessions,
            wled_status=WledStatusObserver(),
            endpoint_discovery=EndpointDiscoveryService(),
            asset_upload=asset_upload,
            installation_photos=InstallationPhotoService(config, repository),
            installation_geometry=installation_geometry,
            projector_mapping=ProjectorMappingService(repository, installation_geometry),
            lighting_surface=lighting_surface,
            timing_calibration=TimingCalibrationService(repository),
            projector_overlap=projector_overlap,
            geometry_suggestions=GeometrySuggestionService(repository),
            output_arming=OutputArmService(repository),
            stage_coverage=stage_coverage,
            mapper_export=mapper_export,
            render_jobs=RenderJobService(
                repository,
                asset_upload,
                config.data_root / "renders",
                mapper_export,
                stage_coverage,
                projector_overlap,
                sync_rehearsal,
            ),
            sync_rehearsal=sync_rehearsal,
            artifact_deployment=ArtifactDeploymentService(
                config,
                repository,
                device_sessions,
            ),
        )
