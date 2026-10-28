import hashlib
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import JsonValue

from cueverge.config import FrigateMqttConfig
from cueverge.controller.runtime import (
    ActiveScene,
    RuntimeAgentStatus,
    RuntimeLightingTarget,
    ShowMode,
)
from cueverge.controller.timing_calibration import (
    CalibrationMediaTarget,
    TimingCalibrationRunStatus,
)
from cueverge.core.encounter import EventOutcome, ObservedEventOutcome
from cueverge.core.events import ExternalEvent
from cueverge.core.models import SceneCatalog
from cueverge.gui.agent_settings import DeviceAgentSettingsService
from cueverge.gui.agent_updates import AgentBundleCatalog, DeviceAgentUpdateService
from cueverge.gui.apply import ApplyService
from cueverge.gui.artifact_deployment import ArtifactDeploymentService
from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.auth import AuthService
from cueverge.gui.collaboration import CollaborationStore
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import OperateActionRequest, Role, RuntimeStatusResponse
from cueverge.gui.device_ca import DeviceCertificateAuthority
from cueverge.gui.device_enrollment import DeviceEnrollmentService
from cueverge.gui.device_players import DevicePlayerError, DevicePlayerService
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.endpoint_discovery import EndpointDiscoveryService
from cueverge.gui.geometry_suggestions import GeometrySuggestionService
from cueverge.gui.home_assistant import HomeAssistantBridge
from cueverge.gui.input_sources import InputSourceService
from cueverge.gui.installation_calibration import (
    InstallationCalibrationService,
    reference_clip_root,
)
from cueverge.gui.installation_geometry import InstallationGeometryService
from cueverge.gui.installation_photos import InstallationPhotoService
from cueverge.gui.installation_timing import InstallationTimingService
from cueverge.gui.lighting_surface import LightingSurfaceService
from cueverge.gui.mapper_export import MapperExportService
from cueverge.gui.mcp_authoring import (
    McpAuthoringService,
    McpAuthoringStore,
    McpScope,
    allowed_mcp_scopes,
)
from cueverge.gui.observability import GuiObservability
from cueverge.gui.output_arming import OutputArmService
from cueverge.gui.projector_mapping import ProjectorMappingService
from cueverge.gui.projector_overlap import ProjectorOverlapService
from cueverge.gui.publication import PublicationService
from cueverge.gui.render_jobs import RenderJobService
from cueverge.gui.repository import GuiRepository
from cueverge.gui.simulation_scenarios import SimulationScenarioService
from cueverge.gui.stage_coverage import StageCoverageService
from cueverge.gui.stage_reference import StageReferenceService
from cueverge.gui.sync_rehearsal import SyncRehearsalService
from cueverge.gui.wled_status import WledStatusObserver
from cueverge.lighting.output_test import OutputTestSegment, OutputTestStatus


class RuntimeStatusSource(Protocol):
    catalog: SceneCatalog
    loaded_catalog: dict[str, object]

    def validate_catalog_handover(self, catalog: SceneCatalog) -> None: ...

    async def replace_catalog(
        self,
        catalog: SceneCatalog,
        *,
        identity: dict[str, object],
        start_scene_id: str | None = None,
        before_load: Callable[[], Awaitable[object]] | None = None,
    ) -> None: ...

    def status(self) -> dict[str, object]: ...

    def output_monitor(
        self, resource: str | None = None, pixel_offset: int = 0, pixel_limit: int = 1000
    ) -> dict[str, object]: ...

    async def start_scene(self, scene_id: str) -> ActiveScene: ...

    async def ingest_event(self, event: ExternalEvent) -> EventOutcome: ...

    async def ingest_event_with_presence(self, event: ExternalEvent) -> ObservedEventOutcome: ...

    def agent_status(self) -> tuple[RuntimeAgentStatus, ...]: ...

    def lighting_targets(self) -> tuple[RuntimeLightingTarget, ...]: ...

    async def start_show(self) -> ActiveScene: ...

    async def pause(self) -> None: ...

    async def resume(self) -> None: ...

    async def seek(self, position_ms: int) -> None: ...

    async def stop(self, *, continue_queue: bool = True) -> None: ...

    async def reset_ambient(self) -> ActiveScene: ...

    async def set_show_mode(self, mode: ShowMode) -> None: ...

    def output_test_status(self) -> OutputTestStatus | None: ...

    def start_output_test(
        self,
        *,
        resource: str,
        pixel_count: int,
        speed: int,
        duration_seconds: float,
        arm_id: str,
        segments: tuple[OutputTestSegment, ...],
        on_finish: Callable[[OutputTestStatus], None],
    ) -> OutputTestStatus: ...

    async def stop_output_test(self, *, reason: str = "operator") -> OutputTestStatus | None: ...

    def set_output_timing(self, delays: Mapping[str, int]) -> None: ...

    def timing_calibration_status(self) -> TimingCalibrationRunStatus | None: ...

    def start_timing_calibration(
        self,
        *,
        media: tuple[CalibrationMediaTarget, ...],
        lights: tuple[str, ...],
        delays: Mapping[str, int],
        on_finish: Callable[[TimingCalibrationRunStatus], None] | None = None,
    ) -> TimingCalibrationRunStatus: ...

    def set_timing_calibration_delay(
        self, resource: str, delay_ms: int
    ) -> TimingCalibrationRunStatus: ...

    def resync_timing_calibration(self) -> TimingCalibrationRunStatus: ...

    async def stop_timing_calibration(
        self, *, reason: str = "operator"
    ) -> TimingCalibrationRunStatus | None: ...


@dataclass(frozen=True)
class GuiServices:
    config: GuiConfig
    repository: GuiRepository
    auth: AuthService
    collaboration: CollaborationStore
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
    installation_timing: InstallationTimingService
    installation_calibration: InstallationCalibrationService
    projector_overlap: ProjectorOverlapService
    geometry_suggestions: GeometrySuggestionService
    output_arming: OutputArmService
    stage_coverage: StageCoverageService
    stage_reference: StageReferenceService
    mapper_export: MapperExportService
    mcp_authoring: McpAuthoringService
    mcp_store: McpAuthoringStore
    render_jobs: RenderJobService
    sync_rehearsal: SyncRehearsalService
    artifact_deployment: ArtifactDeploymentService
    publication: PublicationService
    observability: GuiObservability
    simulation_scenarios: SimulationScenarioService
    input_source: InputSourceService
    apply: ApplyService
    device_players: DevicePlayerService
    agent_updates: DeviceAgentUpdateService
    agent_settings: DeviceAgentSettingsService
    home_assistant: HomeAssistantBridge | None = None

    def runtime_snapshot(self) -> RuntimeStatusResponse:
        inputs = self.input_source.snapshot()
        active_apply = self.apply.active()
        return RuntimeStatusResponse(
            observed_at=datetime.now(UTC).isoformat(),
            installation_id=self.repository.get_runtime_installation().id,
            status=self.runtime.status(),
            apply_job={key: active_apply[key] for key in ("id", "project_id", "state")}
            if active_apply
            else None,
            input_health={
                **{
                    key: inputs[key]
                    for key in (
                        "observed_at_ms",
                        "state",
                        "freshness",
                        "error",
                        "last_received_at_ms",
                        "events_delivered",
                        "audience_matches",
                    )
                },
                "visitor_rules_loaded": bool(
                    self.runtime.catalog.audience
                    or self.runtime.catalog.encounter
                    or self.runtime.catalog.opportunities
                ),
            },
        )

    async def operate(self, body: OperateActionRequest) -> None:
        if self.apply.active() is not None and body.action not in {"stop", "blackout"}:
            raise RuntimeError("Apply is in progress. Stop and Blackout remain available.")
        if body.action in {"stop", "blackout"}:
            self.apply.stop_requested()
        if self.runtime.loaded_catalog.get("state") == "recovery_required" and body.action not in {
            "stop",
            "blackout",
        }:
            raise RuntimeError("A verified catalog must be recovered before playback.")
        if body.action == "start":
            try:
                await self.device_players.ensure_ready_for_loaded_show()
            except DevicePlayerError as error:
                raise RuntimeError(str(error)) from error
            await self.runtime.set_show_mode(ShowMode.AUTOMATIC)
            try:
                if body.scene_id is not None:
                    await self.runtime.start_scene(body.scene_id)
                else:
                    await self.runtime.start_show()
            except (RuntimeError, LookupError):
                await self.runtime.set_show_mode(ShowMode.DISABLED)
                raise
        elif body.action == "pause":
            await self.runtime.pause()
        elif body.action == "resume":
            await self.runtime.resume()
        elif body.action == "seek":
            assert body.position_ms is not None
            await self.runtime.seek(body.position_ms)
        elif body.action == "stop":
            await self.runtime.set_show_mode(ShowMode.DISABLED)
        elif body.action == "reset":
            await self.runtime.reset_ambient()
        elif body.action == "automatic":
            await self.runtime.set_show_mode(ShowMode.AUTOMATIC)
        elif body.action == "maintenance":
            await self.runtime.set_show_mode(ShowMode.MAINTENANCE)
        else:
            await self.runtime.set_show_mode(ShowMode.BLACKOUT)

    @classmethod
    def create(cls, config: GuiConfig, runtime: RuntimeStatusSource) -> "GuiServices":
        repository = GuiRepository(config.data_root / "gui.sqlite3")
        repository.migrate()
        collaboration = CollaborationStore(repository.database_path)
        collaboration.migrate()
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
        artifact_deployment = ArtifactDeploymentService(
            config,
            repository,
            device_sessions,
        )
        compiler = DraftCompiler(config.data_root / "compiled")
        mcp_store = McpAuthoringStore(repository.database_path)
        mcp_store.migrate()
        render_jobs = RenderJobService(
            repository,
            asset_upload,
            config.data_root / "renders",
            mapper_export,
            stage_coverage,
            projector_overlap,
            sync_rehearsal,
        )
        publication = PublicationService(
            repository=repository,
            compiler=compiler,
            assets=asset_upload,
            render_jobs=render_jobs,
            deployment=artifact_deployment,
            output_root=config.data_root / "published",
        )

        def authorize_apply(
            project_id: str, actor: str, token_id: str | None, restoring: bool
        ) -> bool:
            if actor == config.admin_username:
                role = config.admin_role
            else:
                user = collaboration.get_user(actor)
                if user is None or user.disabled:
                    return False
                role = user.role
            membership = collaboration.get_membership(project_id, actor)
            if role is not Role.ADMINISTRATOR and (
                membership is None
                or membership.role not in {Role.PUBLISHER, Role.ADMINISTRATOR}
                or not membership.can_live_output
            ):
                return False
            if token_id is not None:
                token = mcp_store.get_token(token_id)
                if (
                    token is None
                    or token.revoked_at is not None
                    or token.username != actor
                    or token.project_id != project_id
                ):
                    return False
                if (
                    datetime.fromisoformat(token.expires_at).timestamp()
                    <= datetime.now(UTC).timestamp()
                ):
                    return False
                required = (
                    {McpScope.ACTIVATE} if restoring else {McpScope.PUBLISH, McpScope.ACTIVATE}
                )
                if not required.issubset(token.scopes) or not set(token.scopes).issubset(
                    allowed_mcp_scopes(
                        role,
                        membership.role if membership else None,
                        membership.can_live_output if membership else True,
                    )
                ):
                    return False
            return True

        def apply_dependencies(project_id: str, revision: int) -> str:
            draft = repository.get_revision(project_id, revision).draft
            used = {resource for scene in draft.scenes for resource in scene.resources}
            assignments = [
                item for item in repository.list_resource_assignments() if item.resource in used
            ]
            device_ids = {item.device_id for item in assignments}
            devices = [
                {
                    "id": item.id,
                    "enrollment": item.enrollment_state,
                    "capabilities": item.capabilities.model_dump(mode="json"),
                    "certificate": item.active_certificate_serial,
                }
                for item in repository.list_devices()
                if item.id in device_ids
            ]
            installation_id = repository.get_runtime_installation().id
            with sqlite3.connect(repository.database_path) as connection:
                geometry = connection.execute(
                    "SELECT id,revision FROM installation_geometry "
                    "WHERE installation_id=? ORDER BY id",
                    (installation_id,),
                ).fetchall()
                photos = connection.execute(
                    "SELECT id,digest_sha256,orientation_degrees,crop_x,crop_y,"
                    "crop_width,crop_height,scale_hint FROM installation_photos "
                    "WHERE installation_id=? ORDER BY id",
                    (installation_id,),
                ).fetchall()
                surfaces = connection.execute(
                    "SELECT s.geometry_id,s.revision FROM lighting_surfaces s "
                    "JOIN installation_geometry g ON g.id=s.geometry_id "
                    "WHERE g.installation_id=? ORDER BY s.geometry_id",
                    (installation_id,),
                ).fetchall()
                projectors = connection.execute(
                    "SELECT s.geometry_id,s.revision FROM projector_surface_mappings s "
                    "JOIN installation_geometry g ON g.id=s.geometry_id "
                    "WHERE g.installation_id=? ORDER BY s.geometry_id",
                    (installation_id,),
                ).fetchall()
            assets = [
                {
                    "id": item.id,
                    "revision": item.metadata_revision,
                    "digest": item.digest_sha256,
                    "status": item.status,
                    "review": item.review_status,
                    "scan": item.scan_status,
                }
                for item in asset_upload.list_assets(project_id)
            ]
            renders = [
                {
                    "id": item.id,
                    "artifact": item.artifact_id,
                    "cache_key": item.cache_key,
                }
                for item in render_jobs.list(project_id)
                if item.source_revision == revision and item.state == "completed"
            ]
            return hashlib.sha256(
                json.dumps(
                    {
                        "assignments": [asdict(item) for item in assignments],
                        "devices": devices,
                        "geometry": geometry,
                        "photos": photos,
                        "surfaces": surfaces,
                        "projectors": projectors,
                        "assets": assets,
                        "renders": renders,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()

        input_source = InputSourceService(
            config.data_root / "input-source",
            runtime,
            mode=config.input_management,
            environment=FrigateMqttConfig.from_environment()
            if config.input_management == "environment"
            else None,
            handler=runtime.ingest_event_with_presence,
            installation_id=repository.get_runtime_installation().id,
        )
        created: list[GuiServices] = []

        async def operate(body: OperateActionRequest) -> None:
            await created[0].operate(body)

        def audit(action: str, result: str, context: dict[str, JsonValue]) -> None:
            repository.append_audit(
                actor="home-assistant",
                action=action,
                target="controller",
                result=result,
                occurred_at=datetime.now(UTC).isoformat(),
                authorization_context={**context, "source": "mqtt"},
            )

        home_assistant = HomeAssistantBridge(
            config.data_root / "home-assistant",
            installation_id=repository.get_runtime_installation().id,
            installation_name=lambda: repository.get_runtime_installation().name,
            status=runtime.status,
            operate=operate,
            broker=input_source.broker_config,
            audit=audit,
        )
        device_players = DevicePlayerService(repository, device_sessions, runtime)
        installation_timing = InstallationTimingService(repository)
        installation_calibration = InstallationCalibrationService(
            repository=repository,
            runtime=runtime,
            sessions=device_sessions,
            players=device_players,
            timing=installation_timing,
            clip_root=reference_clip_root(config.data_root),
            public_url=str(config.device_listener_public_url)
            if config.device_listener_public_url
            else None,
        )
        installation_calibration.push_to_runtime()
        services = cls(
            home_assistant=home_assistant,
            apply=ApplyService(
                repository, publication, runtime, authorize_apply, apply_dependencies
            ),
            input_source=input_source,
            device_players=device_players,
            agent_settings=DeviceAgentSettingsService(repository, device_sessions, runtime),
            agent_updates=DeviceAgentUpdateService(
                repository,
                device_sessions,
                runtime,
                AgentBundleCatalog(config.agent_bundle_root),
                config.device_listener_public_url,
            ),
            config=config,
            repository=repository,
            auth=AuthService(config, repository, collaboration),
            collaboration=collaboration,
            compiler=compiler,
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
            installation_timing=installation_timing,
            installation_calibration=installation_calibration,
            projector_overlap=projector_overlap,
            geometry_suggestions=GeometrySuggestionService(repository),
            output_arming=OutputArmService(repository),
            stage_coverage=stage_coverage,
            stage_reference=StageReferenceService(repository),
            mapper_export=mapper_export,
            mcp_authoring=McpAuthoringService(
                mcp_store,
                repository,
                compiler,
                SimulationScenarioService(config.data_root / "simulation-scenarios"),
            ),
            mcp_store=mcp_store,
            render_jobs=render_jobs,
            sync_rehearsal=sync_rehearsal,
            artifact_deployment=artifact_deployment,
            publication=publication,
            observability=GuiObservability(repository, str(config.data_root)),
            simulation_scenarios=SimulationScenarioService(
                config.data_root / "simulation-scenarios"
            ),
        )
        created.append(services)
        return services
