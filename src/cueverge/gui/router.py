import asyncio
import time
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Annotated, Literal, cast
from urllib.parse import unquote
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from pydantic import JsonValue, ValidationError

from cueverge.controller.runtime import ShowMode
from cueverge.core.loader import ShowPackageError, load_show_package
from cueverge.gui.apply import ApplyError, ApplyRequest
from cueverge.gui.artifact_deployment import ArtifactDeploymentError
from cueverge.gui.artifact_preview import (
    ArtifactPreviewError,
    extract_artifact_preview_frame,
)
from cueverge.gui.asset_upload import (
    AssetMetadataConflictError,
    AssetMetadataLockedError,
    AssetUploadConflictError,
    AssetUploadError,
    StoredAsset,
    StoredAssetUpload,
)
from cueverge.gui.auth import (
    AuthenticatedSession,
    LoginRateLimitError,
)
from cueverge.gui.compiler import CompilationError
from cueverge.gui.contracts import (
    AcceptedCalibrationResponse,
    ActivationRequest,
    ActivityEventResponse,
    ApplyScenePresetRequest,
    ArtifactDeploymentResponse,
    AssetMetadataConflictResponse,
    AssetResponse,
    AssetSceneReferenceResponse,
    AssetUploadResponse,
    AuditEventResponse,
    BeginAssetUploadRequest,
    BrowserErrorRequest,
    CapabilityReviewIssue,
    CapabilityReviewRequest,
    CapabilityReviewResponse,
    ChangeRequestCreate,
    ChangeRequestResponse,
    ChangeRequestState,
    CleanupDeviceArtifactsRequest,
    CleanupDeviceArtifactsResponse,
    CommentRequest,
    CommentResponse,
    CommentStateRequest,
    CompilationResponse,
    ConfigureInputSourceRequest,
    ConnectionTestCheck,
    CreateInstallationRequest,
    CreateMcpTokenRequest,
    CreateOutputArmRequest,
    CreatePairingTokenRequest,
    CreateProjectRequest,
    CreateRenderJobRequest,
    CreateScenePresetRequest,
    DeviceArtifactResponse,
    DeviceConnectionPolicyResponse,
    DeviceConnectionTestResponse,
    DeviceEnrollmentStateResponse,
    DeviceTransferStatusResponse,
    DiscoveredEndpointResponse,
    DraftProject,
    EvaluateSyncAdmissionRequest,
    GeometryPoint,
    GeometrySuggestionRequest,
    GeometrySuggestionResponse,
    InstallationConflictResponse,
    InstallationGeometryRequest,
    InstallationGeometryResponse,
    InstallationMapperExportResponse,
    InstallationPhotoResponse,
    InstallationResourceResponse,
    InstallationStatusResponse,
    InstallationSummaryResponse,
    InteractionEditRequest,
    InteractionPreviewRequest,
    LightingSurfaceRequest,
    LightingSurfaceResponse,
    LocalUserRequest,
    LocalUserResponse,
    LoginRequest,
    MaintenancePreviewRequest,
    MaintenancePreviewResponse,
    MappedPixelResponse,
    MapperDiagnosticResponse,
    McpProposalDispositionRequest,
    McpProposalResponse,
    McpTokenResponse,
    ObjectOrganizationRequest,
    OperateActionRequest,
    OperateActionResponse,
    OutputArmResponse,
    OutputTestOverviewResponse,
    OutputTestResponse,
    OutputTestSegmentResponse,
    PairingTokenResponse,
    PhotoDescriptionRequest,
    PlaceAssetRequest,
    PresenceHeartbeatRequest,
    PresenceResponse,
    ProbeEndpointRequest,
    ProjectDraftResponse,
    ProjectExportDocument,
    ProjectMembershipRequest,
    ProjectMembershipResponse,
    ProjectMetadataConflictResponse,
    ProjectMetadataRequest,
    ProjectorMappingRequest,
    ProjectorMappingResponse,
    ProjectorOverlapRequest,
    ProjectorOverlapResponse,
    ProjectorPhotoViewResponse,
    ProjectSummary,
    PublicationDiagnosticResponse,
    PublicationValidationResponse,
    PublishedPackageResponse,
    RecordSyncRehearsalRequest,
    RenderJobResponse,
    ResourceAssignmentConflictResponse,
    ResourceAssignmentResponse,
    ReviewCalibrationContextRequest,
    ReviewLinkRequest,
    ReviewLinkResponse,
    ReviewLinkView,
    RevisionConflictResponse,
    RevisionDiffResponse,
    RevisionReleaseResponse,
    RevisionSummary,
    Role,
    RuntimeStatusResponse,
    SaveSimulationScenarioRequest,
    SceneLightingPreviewRequest,
    SessionResponse,
    SetDeviceConnectionPolicyRequest,
    SetDeviceEnrollmentStateRequest,
    SetResourceAssignmentRequest,
    SimulationRunResponse,
    SimulationScenarioResponse,
    StageCoverageResponse,
    StageRegionResponse,
    StartOutputTestRequest,
    StartTimingCalibrationRequest,
    SyncAdmissionResponse,
    SyncRehearsalResponse,
    TimingCalibrationSessionResponse,
    TopologyNodeResponse,
    TopologyResponse,
    UpdateAssetMetadataRequest,
    UpdateInstallationPhotoRequest,
    UpdateInstallationRequest,
    UpdateRevisionReleaseRequest,
    UpdateTimingCalibrationRequest,
)
from cueverge.gui.contracts import (
    PixelPositionOverride as PixelPositionOverrideResponse,
)
from cueverge.gui.device_session import DeviceSessionError
from cueverge.gui.endpoint_discovery import EndpointDiscoveryError
from cueverge.gui.geometry_suggestions import (
    GeometrySuggestionError,
    StoredGeometrySuggestion,
)
from cueverge.gui.input_sources import InputSourceError
from cueverge.gui.installation_geometry import (
    InstallationGeometryConflictError,
    InstallationGeometryError,
    StoredInstallationGeometry,
)
from cueverge.gui.installation_photos import (
    MAX_PHOTO_BYTES,
    InstallationPhotoConflictError,
    InstallationPhotoError,
    StoredInstallationPhoto,
)
from cueverge.gui.interaction_preview import preview_interactions
from cueverge.gui.interactions import (
    InteractionError,
    apply_interaction_edit,
    describe_interactions,
)
from cueverge.gui.lighting_preview import (
    LightingPreviewError,
    evaluate_lighting_preview,
    resolve_lighting_preview_surface,
)
from cueverge.gui.lighting_surface import (
    LightingSurfaceError,
    StoredLightingSurface,
)
from cueverge.gui.lighting_surface import (
    PixelPositionOverride as StoredPixelPositionOverride,
)
from cueverge.gui.mapper_export import MapperExportError
from cueverge.gui.mcp_authoring import (
    McpAuthoringError,
    McpProposal,
    McpScope,
    McpToken,
    allowed_mcp_scopes,
)
from cueverge.gui.output_arming import OutputArmError, StoredOutputArm
from cueverge.gui.output_tests import mapped_test_segments
from cueverge.gui.passwords import hash_password
from cueverge.gui.preview_evaluation import (
    PreviewEvaluationError,
    evaluate_preview_state,
)
from cueverge.gui.projector_mapping import (
    ProjectorMappingConflictError,
    ProjectorMappingError,
    StoredProjectorMapping,
)
from cueverge.gui.projector_overlap import (
    ProjectorOverlapError,
    StoredProjectorOverlap,
)
from cueverge.gui.projector_renderer import (
    ProjectorRenderError,
    preview_projector_composition,
)
from cueverge.gui.projector_simulation import (
    ProjectorSimulationError,
    warp_review_proxy,
)
from cueverge.gui.publication import PublicationError, PublishedPackage
from cueverge.gui.render_jobs import RenderJobError, StoredRenderJob
from cueverge.gui.repository import (
    ActiveInstallationDeleteError,
    DeviceNotFoundError,
    DeviceStateTransitionError,
    DiscoveredEndpointNotUsableError,
    InstallationConflictError,
    InstallationNameConflictError,
    InstallationNotFoundError,
    ProjectMetadataConflictError,
    ProjectNotFoundError,
    ResourceAssignmentConflictError,
    ResourceAssignmentInUseError,
    RevisionConflictError,
    StoredDevicePresence,
    StoredDiscoveredEndpoint,
    StoredInstallation,
    StoredProject,
    StoredResourceAssignment,
    StoredRevision,
)
from cueverge.gui.scene_preview import (
    ScenePreviewBusyError,
    ScenePreviewError,
    preview_project_scene,
)
from cueverge.gui.service import GuiServices
from cueverge.gui.simulation_scenarios import (
    SimulationScenarioError,
    StoredSimulationScenario,
)
from cueverge.gui.stage_renderer import (
    preview_stage_composition_member,
    preview_stage_lighting_samples,
)
from cueverge.gui.sync_rehearsal import (
    StoredSyncRehearsal,
    SyncAdmission,
    SyncRehearsalError,
)
from cueverge.gui.timing_calibration import (
    StoredAcceptedCalibration,
    StoredTimingCalibration,
    TimingCalibrationError,
)
from cueverge.lighting.output_test import OutputTestError, OutputTestStatus
from cueverge.protocol.device_v2 import (
    DEVICE_PROTOCOL_VERSION,
    DeviceKind,
    EnrollmentState,
)

SESSION_COOKIE = "cueverge_session"
CSRF_COOKIE = "cueverge_csrf"


def create_gui_router(services: GuiServices) -> APIRouter:
    router = APIRouter(prefix="/api/gui/v1", tags=["gui"])

    def current_session(
        cueverge_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> AuthenticatedSession:
        session = services.auth.authenticate(cueverge_session)
        if session is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "authentication_required"},
            )
        return session

    def require_roles(
        *allowed_roles: Role,
    ) -> Callable[[AuthenticatedSession], AuthenticatedSession]:
        def dependency(
            session: Annotated[AuthenticatedSession, Depends(current_session)],
        ) -> AuthenticatedSession:
            if session.role not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "insufficient_role"},
                )
            return session

        return dependency

    def mutation_session(
        request: Request,
        session: Annotated[AuthenticatedSession, Depends(current_session)],
        csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
        csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    ) -> AuthenticatedSession:
        _require_allowed_origin(request, services)
        if csrf_header is None or csrf_cookie is None or csrf_header != csrf_cookie:
            raise HTTPException(status_code=403, detail={"code": "csrf_failed"})
        if not services.auth.verify_csrf(session, csrf_header):
            raise HTTPException(status_code=403, detail={"code": "csrf_failed"})
        return session

    def require_mutation_roles(
        *allowed_roles: Role,
    ) -> Callable[..., AuthenticatedSession]:
        def dependency(
            session: Annotated[AuthenticatedSession, Depends(mutation_session)],
        ) -> AuthenticatedSession:
            if session.role not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "insufficient_role"},
                )
            return session

        return dependency

    reader = Depends(current_session)
    author = Depends(require_mutation_roles(Role.DESIGNER, Role.PUBLISHER, Role.ADMINISTRATOR))
    administrator = Depends(require_roles(Role.ADMINISTRATOR))
    administrator_mutation = Depends(require_mutation_roles(Role.ADMINISTRATOR))
    operator_mutation = Depends(require_mutation_roles(Role.OPERATOR, Role.ADMINISTRATOR))

    def live_output_session(
        session: Annotated[AuthenticatedSession, Depends(mutation_session)],
    ) -> AuthenticatedSession:
        if session.role is Role.ADMINISTRATOR:
            return session
        if session.role is not Role.OPERATOR:
            raise HTTPException(
                status_code=403,
                detail={"code": "insufficient_role"},
            )
        if not services.collaboration.has_live_output_permission(session.username):
            raise HTTPException(
                status_code=403,
                detail={"code": "live_output_permission_required"},
            )
        return session

    live_output_mutation = Depends(live_output_session)

    def require_project_roles(
        *allowed_roles: Role,
    ) -> Callable[..., AuthenticatedSession]:
        def dependency(
            project_id: str,
            session: Annotated[AuthenticatedSession, Depends(mutation_session)],
        ) -> AuthenticatedSession:
            if session.role is Role.ADMINISTRATOR:
                return session
            membership = services.collaboration.get_membership(project_id, session.username)
            if membership is None or membership.role not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "insufficient_project_role"},
                )
            return session

        return dependency

    def project_reader_session(
        project_id: str,
        session: Annotated[AuthenticatedSession, Depends(current_session)],
    ) -> AuthenticatedSession:
        if session.role is Role.ADMINISTRATOR:
            return session
        if services.collaboration.get_membership(project_id, session.username) is None:
            raise HTTPException(
                status_code=403,
                detail={"code": "project_membership_required"},
            )
        return session

    project_reader = Depends(project_reader_session)
    project_commenter = Depends(
        require_project_roles(
            Role.VIEWER,
            Role.DESIGNER,
            Role.OPERATOR,
            Role.PUBLISHER,
            Role.ADMINISTRATOR,
        )
    )
    project_designer = Depends(
        require_project_roles(Role.DESIGNER, Role.PUBLISHER, Role.ADMINISTRATOR)
    )
    project_simulator = Depends(
        require_project_roles(Role.DESIGNER, Role.OPERATOR, Role.PUBLISHER, Role.ADMINISTRATOR)
    )
    project_publisher = Depends(require_project_roles(Role.PUBLISHER, Role.ADMINISTRATOR))
    project_administrator = Depends(require_project_roles(Role.ADMINISTRATOR))

    @router.post("/auth/login", response_model=SessionResponse)
    def login(body: LoginRequest, request: Request, response: Response) -> SessionResponse:
        _require_allowed_origin(request, services)
        remote_key = request.client.host if request.client is not None else "unknown"
        try:
            new_session = services.auth.login(
                body.username,
                body.password,
                remote_key=remote_key,
            )
        except LoginRateLimitError as error:
            raise HTTPException(
                status_code=429,
                detail={"code": "login_rate_limited"},
            ) from error
        if new_session is None:
            raise HTTPException(status_code=401, detail={"code": "invalid_credentials"})
        response.set_cookie(
            SESSION_COOKIE,
            new_session.token,
            httponly=True,
            secure=services.config.secure_cookies,
            samesite="strict",
            max_age=services.config.session_ttl_seconds,
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            new_session.csrf_token,
            httponly=False,
            secure=services.config.secure_cookies,
            samesite="strict",
            max_age=services.config.session_ttl_seconds,
            path="/",
        )
        return SessionResponse(
            username=new_session.identity.username,
            role=new_session.identity.role,
        )

    @router.get("/auth/session", response_model=SessionResponse, dependencies=[reader])
    def session(
        identity: Annotated[AuthenticatedSession, Depends(current_session)],
    ) -> SessionResponse:
        return SessionResponse(username=identity.username, role=identity.role)

    @router.post("/auth/logout", status_code=204)
    def logout(
        response: Response,
        identity: Annotated[AuthenticatedSession, Depends(mutation_session)],
        session_token: Annotated[str, Cookie(alias=SESSION_COOKIE)],
    ) -> None:
        services.auth.logout(session_token, identity)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")

    @router.get(
        "/users",
        response_model=list[LocalUserResponse],
        dependencies=[administrator],
    )
    def users() -> list[LocalUserResponse]:
        return [
            LocalUserResponse(
                username=user.username,
                role=user.role,
                disabled=user.disabled,
                created_at=user.created_at,
            )
            for user in services.collaboration.list_users()
        ]

    @router.put("/users/{username}", response_model=LocalUserResponse)
    def put_user(
        username: str,
        body: LocalUserRequest,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> LocalUserResponse:
        if username != body.username or username == services.config.admin_username:
            raise HTTPException(
                status_code=409,
                detail={"code": "reserved_or_mismatched_username"},
            )
        occurred_at = _now_iso()
        user = services.collaboration.upsert_user(
            username,
            hash_password(body.password),
            body.role,
            body.disabled,
            occurred_at,
        )
        services.repository.append_audit(
            actor=identity.username,
            action="user.upsert",
            target=username,
            result="success",
            occurred_at=occurred_at,
            authorization_context={"role": body.role.value, "disabled": body.disabled},
        )
        return LocalUserResponse(
            username=user.username,
            role=user.role,
            disabled=user.disabled,
            created_at=user.created_at,
        )

    @router.get(
        "/projects",
        response_model=list[ProjectSummary],
        dependencies=[reader],
    )
    def projects(
        identity: Annotated[AuthenticatedSession, Depends(current_session)],
    ) -> list[ProjectSummary]:
        visible_ids = (
            None
            if identity.role is Role.ADMINISTRATOR
            else services.collaboration.project_ids_for_user(identity.username)
        )
        return [
            _project_summary(project)
            for project in services.repository.list_projects()
            if visible_ids is None or project.id in visible_ids
        ]

    @router.get(
        "/projects/{project_id}/members",
        response_model=list[ProjectMembershipResponse],
        dependencies=[project_reader],
    )
    def project_members(project_id: str) -> list[ProjectMembershipResponse]:
        return [
            ProjectMembershipResponse(
                project_id=item.project_id,
                username=item.username,
                role=item.role,
                can_live_output=item.can_live_output,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in services.collaboration.list_memberships(project_id)
        ]

    @router.put(
        "/projects/{project_id}/members/{username}",
        response_model=ProjectMembershipResponse,
    )
    def put_project_member(
        project_id: str,
        username: str,
        body: ProjectMembershipRequest,
        identity: Annotated[AuthenticatedSession, project_administrator],
    ) -> ProjectMembershipResponse:
        if (
            username != services.config.admin_username
            and services.collaboration.get_user(username) is None
        ):
            raise HTTPException(status_code=404, detail={"code": "user_not_found"})
        occurred_at = _now_iso()
        membership = services.collaboration.put_membership(
            project_id,
            username,
            body.role,
            body.can_live_output,
            occurred_at,
        )
        services.repository.append_audit(
            actor=identity.username,
            action="project.member.upsert",
            target=f"{project_id}:{username}",
            result="success",
            occurred_at=occurred_at,
            authorization_context={
                "project_role": body.role.value,
                "can_live_output": body.can_live_output,
            },
        )
        return ProjectMembershipResponse(
            project_id=membership.project_id,
            username=membership.username,
            role=membership.role,
            can_live_output=membership.can_live_output,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
        )

    @router.get(
        "/projects/{project_id}/comments",
        response_model=list[CommentResponse],
        dependencies=[project_reader],
    )
    def comments(project_id: str) -> list[CommentResponse]:
        return [
            CommentResponse.model_validate(item)
            for item in services.collaboration.list_comments(project_id)
        ]

    @router.post(
        "/projects/{project_id}/comments",
        response_model=CommentResponse,
        status_code=201,
    )
    def create_comment(
        project_id: str,
        body: CommentRequest,
        identity: Annotated[AuthenticatedSession, project_commenter],
    ) -> CommentResponse:
        services.repository.get_revision(project_id, body.revision)
        occurred_at = _now_iso()
        comment = services.collaboration.create_comment(
            project_id,
            body.revision,
            body.object_id,
            body.body,
            identity.username,
            occurred_at,
        )
        services.repository.append_audit(
            actor=identity.username,
            action="comment.create",
            target=str(comment["id"]),
            result="success",
            occurred_at=occurred_at,
            authorization_context={
                "project_id": project_id,
                "revision": body.revision,
            },
        )
        return CommentResponse.model_validate(comment)

    @router.put(
        "/projects/{project_id}/comments/{comment_id}",
        response_model=CommentResponse,
    )
    def update_comment(
        project_id: str,
        comment_id: str,
        body: CommentStateRequest,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> CommentResponse:
        if not any(
            item["id"] == comment_id for item in services.collaboration.list_comments(project_id)
        ):
            raise HTTPException(status_code=404, detail={"code": "comment_not_found"})
        occurred_at = _now_iso()
        comment = services.collaboration.set_comment_state(
            comment_id,
            body.state,
            identity.username,
            occurred_at,
        )
        services.repository.append_audit(
            actor=identity.username,
            action="comment.state",
            target=comment_id,
            result=body.state,
            occurred_at=occurred_at,
            authorization_context={"project_id": project_id},
        )
        return CommentResponse.model_validate(comment)

    @router.get(
        "/projects/{project_id}/change-requests",
        response_model=list[ChangeRequestResponse],
        dependencies=[project_reader],
    )
    def change_requests(project_id: str) -> list[ChangeRequestResponse]:
        return [
            ChangeRequestResponse.model_validate(item)
            for item in services.collaboration.list_change_requests(project_id)
        ]

    @router.post(
        "/projects/{project_id}/change-requests",
        response_model=ChangeRequestResponse,
        status_code=201,
    )
    def create_change_request(
        project_id: str,
        body: ChangeRequestCreate,
        identity: Annotated[AuthenticatedSession, project_commenter],
    ) -> ChangeRequestResponse:
        services.repository.get_revision(project_id, body.revision)
        occurred_at = _now_iso()
        change_request = services.collaboration.create_change_request(
            project_id,
            body.revision,
            body.object_id,
            body.title,
            body.body,
            identity.username,
            occurred_at,
        )
        services.repository.append_audit(
            actor=identity.username,
            action="change_request.create",
            target=str(change_request["id"]),
            result="success",
            occurred_at=occurred_at,
            authorization_context={"project_id": project_id},
        )
        return ChangeRequestResponse.model_validate(change_request)

    @router.put(
        "/projects/{project_id}/change-requests/{request_id}",
        response_model=ChangeRequestResponse,
    )
    def update_change_request(
        project_id: str,
        request_id: str,
        body: ChangeRequestState,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> ChangeRequestResponse:
        if not any(
            item["id"] == request_id
            for item in services.collaboration.list_change_requests(project_id)
        ):
            raise HTTPException(
                status_code=404,
                detail={"code": "change_request_not_found"},
            )
        occurred_at = _now_iso()
        change_request = services.collaboration.set_change_request_state(
            request_id,
            body.state,
            identity.username,
            occurred_at,
        )
        services.repository.append_audit(
            actor=identity.username,
            action="change_request.state",
            target=request_id,
            result=body.state,
            occurred_at=occurred_at,
            authorization_context={"project_id": project_id},
        )
        return ChangeRequestResponse.model_validate(change_request)

    @router.post(
        "/projects/{project_id}/review-links",
        response_model=ReviewLinkResponse,
        status_code=201,
    )
    def create_review_link(
        project_id: str,
        body: ReviewLinkRequest,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> ReviewLinkResponse:
        services.repository.get_revision(project_id, body.revision)
        try:
            expires_at = datetime.fromisoformat(body.expires_at)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_review_expiry"},
            ) from error
        if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_review_expiry"},
            )
        occurred_at = _now_iso()
        link, token = services.collaboration.create_review_link(
            project_id,
            body.revision,
            expires_at.isoformat(),
            identity.username,
            occurred_at,
        )
        services.repository.append_audit(
            actor=identity.username,
            action="review_link.create",
            target=str(link["id"]),
            result="success",
            occurred_at=occurred_at,
            authorization_context={"project_id": project_id},
        )
        return ReviewLinkResponse(
            id=str(link["id"]),
            project_id=project_id,
            revision=body.revision,
            expires_at=str(link["expires_at"]),
            created_by=identity.username,
            created_at=str(link["created_at"]),
            token=token,
        )

    @router.get("/review-links/{token}", response_model=ReviewLinkView)
    def review_link(token: str) -> ReviewLinkView:
        link = services.collaboration.resolve_review_link(token, _now_iso())
        if link is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "review_link_not_found"},
            )
        revision = services.repository.get_revision(
            str(link["project_id"]),
            cast(int, link["revision"]),
        )
        return ReviewLinkView(
            project_id=revision.project.id,
            project_name=revision.project.name,
            revision=revision.revision,
            expires_at=str(link["expires_at"]),
        )

    @router.post("/projects/{project_id}/presence", status_code=204)
    def presence_heartbeat(
        project_id: str,
        body: PresenceHeartbeatRequest,
        identity: Annotated[AuthenticatedSession, project_commenter],
    ) -> Response:
        services.collaboration.heartbeat_presence(
            project_id,
            identity.username,
            body.location,
            _now_iso(),
        )
        return Response(status_code=204)

    @router.get(
        "/projects/{project_id}/presence",
        response_model=list[PresenceResponse],
        dependencies=[project_reader],
    )
    def presence(project_id: str) -> list[PresenceResponse]:
        active_after = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        return [
            PresenceResponse.model_validate(item)
            for item in services.collaboration.list_presence(project_id, active_after)
        ]

    @router.get(
        "/projects/{project_id}/mcp-tokens",
        response_model=list[McpTokenResponse],
        dependencies=[project_reader],
    )
    def list_mcp_tokens(project_id: str) -> list[McpTokenResponse]:
        return [_mcp_token_response(item) for item in services.mcp_store.list_tokens(project_id)]

    @router.post(
        "/projects/{project_id}/mcp-tokens",
        response_model=McpTokenResponse,
        status_code=201,
    )
    def create_mcp_token(
        project_id: str,
        body: CreateMcpTokenRequest,
        identity: Annotated[AuthenticatedSession, project_commenter],
    ) -> McpTokenResponse:
        try:
            expires_at = datetime.fromisoformat(body.expires_at)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_mcp_token_expiry"},
            ) from error
        now = datetime.now(UTC)
        if expires_at.tzinfo is None or expires_at <= now or expires_at > now + timedelta(days=90):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_mcp_token_expiry"},
            )
        requested_scopes = tuple(McpScope(scope) for scope in body.scopes)
        if McpScope.READ not in requested_scopes:
            raise HTTPException(
                status_code=422,
                detail={"code": "mcp_read_scope_required"},
            )
        membership = services.collaboration.get_membership(
            project_id,
            identity.username,
        )
        allowed_scopes = allowed_mcp_scopes(
            identity.role,
            membership.role if membership is not None else None,
            membership.can_live_output if membership is not None else True,
        )
        if not set(requested_scopes).issubset(allowed_scopes):
            raise HTTPException(
                status_code=403,
                detail={"code": "mcp_scope_not_permitted"},
            )
        occurred_at = _now_iso()
        created = services.mcp_store.create_token(
            client_id=body.client_id,
            username=identity.username,
            project_id=project_id,
            scopes=requested_scopes,
            expires_at=expires_at.isoformat(),
            occurred_at=occurred_at,
        )
        record = created.record
        services.repository.append_audit(
            actor=identity.username,
            action="mcp.token.create",
            target=record.id,
            result="success",
            occurred_at=occurred_at,
            authorization_context={
                "project_id": project_id,
                "client_id": body.client_id,
                "scopes": list(body.scopes),
            },
        )
        return _mcp_token_response(record, created.token)

    @router.delete("/projects/{project_id}/mcp-tokens/{token_id}", status_code=204)
    def revoke_mcp_token(
        project_id: str,
        token_id: str,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> Response:
        token = services.mcp_store.get_token(token_id)
        if token is None or token.project_id != project_id:
            raise HTTPException(
                status_code=404,
                detail={"code": "mcp_token_not_found"},
            )
        occurred_at = _now_iso()
        services.mcp_store.revoke_token(token_id, occurred_at)
        services.repository.append_audit(
            actor=identity.username,
            action="mcp.token.revoke",
            target=token_id,
            result="success",
            occurred_at=occurred_at,
            authorization_context={
                "project_id": project_id,
                "client_id": token.client_id,
            },
        )
        return Response(status_code=204)

    @router.get(
        "/projects/{project_id}/mcp-proposals",
        response_model=list[McpProposalResponse],
        dependencies=[project_reader],
    )
    def list_mcp_proposals(project_id: str) -> list[McpProposalResponse]:
        return [
            _mcp_proposal_response(item) for item in services.mcp_store.list_proposals(project_id)
        ]

    @router.get(
        "/projects/{project_id}/mcp-proposals/{proposal_id}",
        response_model=McpProposalResponse,
        dependencies=[project_reader],
    )
    def get_mcp_proposal(
        project_id: str,
        proposal_id: str,
    ) -> McpProposalResponse:
        proposal = services.mcp_store.require_proposal(proposal_id)
        if proposal.project_id != project_id:
            raise HTTPException(
                status_code=404,
                detail={"code": "mcp_proposal_not_found"},
            )
        return _mcp_proposal_response(proposal)

    @router.post(
        "/projects/{project_id}/mcp-proposals/{proposal_id}/validate",
        response_model=McpProposalResponse,
    )
    def validate_mcp_proposal(
        project_id: str,
        proposal_id: str,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> McpProposalResponse:
        proposal = _require_project_proposal(services, project_id, proposal_id)
        try:
            updated = services.mcp_authoring.validate_proposal(proposal.id)
        except McpAuthoringError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "mcp_proposal_validation_failed", "message": str(error)},
            ) from error
        _audit_mcp_proposal(services, identity, updated, "validate")
        return _mcp_proposal_response(updated)

    @router.post(
        "/projects/{project_id}/mcp-proposals/{proposal_id}/approve",
        response_model=McpProposalResponse,
    )
    def approve_mcp_proposal(
        project_id: str,
        proposal_id: str,
        body: McpProposalDispositionRequest,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> McpProposalResponse:
        proposal = _require_project_proposal(services, project_id, proposal_id)
        try:
            updated = services.mcp_authoring.approve(
                proposal.id,
                identity.username,
                body.reason,
            )
        except McpAuthoringError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "mcp_proposal_approval_failed", "message": str(error)},
            ) from error
        _audit_mcp_proposal(services, identity, updated, "approve")
        return _mcp_proposal_response(updated)

    @router.post(
        "/projects/{project_id}/mcp-proposals/{proposal_id}/reject",
        response_model=McpProposalResponse,
    )
    def reject_mcp_proposal(
        project_id: str,
        proposal_id: str,
        body: McpProposalDispositionRequest,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> McpProposalResponse:
        proposal = _require_project_proposal(services, project_id, proposal_id)
        try:
            updated = services.mcp_authoring.reject(
                proposal.id,
                identity.username,
                body.reason,
            )
        except McpAuthoringError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "mcp_proposal_rejection_failed", "message": str(error)},
            ) from error
        _audit_mcp_proposal(services, identity, updated, "reject")
        return _mcp_proposal_response(updated)

    @router.post(
        "/projects/{project_id}/mcp-proposals/{proposal_id}/merge",
        response_model=McpProposalResponse,
    )
    def merge_mcp_proposal(
        project_id: str,
        proposal_id: str,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> McpProposalResponse:
        proposal = _require_project_proposal(services, project_id, proposal_id)
        try:
            updated = services.mcp_authoring.merge(
                proposal.id,
                identity.username,
                identity.role,
            )
        except McpAuthoringError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "mcp_proposal_merge_failed", "message": str(error)},
            ) from error
        _audit_mcp_proposal(services, identity, updated, "merge")
        return _mcp_proposal_response(updated)

    @router.post("/projects", response_model=ProjectDraftResponse, status_code=201)
    def create_project(
        body: CreateProjectRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, author],
    ) -> ProjectDraftResponse:
        revision = services.repository.create_project(
            name=body.name,
            draft=body.draft,
            actor=identity.username,
            role=identity.role,
            occurred_at=_now_iso(),
        )
        services.collaboration.put_membership(
            revision.project.id,
            identity.username,
            Role.ADMINISTRATOR,
            True,
            _now_iso(),
        )
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.post(
        "/projects/from-active",
        response_model=ProjectDraftResponse,
        status_code=201,
    )
    def create_project_from_active(
        body: ProjectMetadataRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, author],
    ) -> ProjectDraftResponse:
        draft = DraftProject(
            schema_version=1,
            installation=services.runtime.catalog.installation,
            scenes=tuple(
                services.runtime.catalog.scenes[scene_id].definition
                for scene_id in sorted(services.runtime.catalog.scenes)
            ),
            editor_metadata={"installation_id": services.repository.get_runtime_installation().id},
        )
        revision = services.repository.create_project(
            name=body.name,
            draft=draft,
            actor=identity.username,
            role=identity.role,
            occurred_at=_now_iso(),
        )
        services.collaboration.put_membership(
            revision.project.id,
            identity.username,
            Role.ADMINISTRATOR,
            True,
            _now_iso(),
        )
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.put("/projects/{project_id}", response_model=ProjectSummary)
    def update_project_metadata(
        project_id: str,
        body: ProjectMetadataRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, project_designer],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectSummary | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            project = services.repository.update_project_metadata(
                project_id=project_id,
                expected_revision=expected_revision,
                name=body.name,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        except ProjectMetadataConflictError as error:
            return JSONResponse(
                status_code=409,
                content=ProjectMetadataConflictResponse(
                    requested_revision=expected_revision,
                    current_revision=error.current_revision,
                ).model_dump(mode="json"),
            )
        response.headers["ETag"] = _etag(project.metadata_revision)
        return _project_summary(project)

    @router.post(
        "/projects/{project_id}/duplicate",
        response_model=ProjectDraftResponse,
        status_code=201,
    )
    def duplicate_project(
        project_id: str,
        body: ProjectMetadataRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> ProjectDraftResponse:
        try:
            source = services.repository.get_revision(project_id)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        duplicated = services.repository.create_project(
            name=body.name,
            draft=source.draft,
            actor=identity.username,
            role=identity.role,
            occurred_at=_now_iso(),
        )
        services.collaboration.put_membership(
            duplicated.project.id,
            identity.username,
            Role.ADMINISTRATOR,
            True,
            _now_iso(),
        )
        services.repository.append_audit(
            actor=identity.username,
            action="project.duplicate",
            target=duplicated.project.id,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={
                "role": identity.role.value,
                "source_project_id": project_id,
                "source_revision": source.revision,
            },
        )
        response.headers["ETag"] = _etag(duplicated.revision)
        return _project_draft(duplicated)

    @router.post(
        "/projects/{project_id}/archive",
        response_model=ProjectSummary,
    )
    def archive_project(
        project_id: str,
        response: Response,
        identity: Annotated[AuthenticatedSession, project_designer],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectSummary | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            project = services.repository.archive_project(
                project_id=project_id,
                expected_revision=expected_revision,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        except ProjectMetadataConflictError as error:
            return JSONResponse(
                status_code=409,
                content=ProjectMetadataConflictResponse(
                    requested_revision=expected_revision,
                    current_revision=error.current_revision,
                ).model_dump(mode="json"),
            )
        except ValueError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "project_already_archived"},
            ) from error
        response.headers["ETag"] = _etag(project.metadata_revision)
        return _project_summary(project)

    @router.get(
        "/projects/{project_id}/export",
        response_model=ProjectExportDocument,
        dependencies=[project_reader],
    )
    def export_project(project_id: str) -> ProjectExportDocument:
        try:
            return services.repository.export_project(project_id)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error

    @router.post(
        "/projects/import",
        response_model=ProjectDraftResponse,
        status_code=201,
    )
    def import_project(
        body: ProjectExportDocument,
        response: Response,
        identity: Annotated[AuthenticatedSession, author],
    ) -> ProjectDraftResponse:
        revision = services.repository.import_project(
            body,
            actor=identity.username,
            role=identity.role,
            occurred_at=_now_iso(),
        )
        services.collaboration.put_membership(
            revision.project.id,
            identity.username,
            Role.ADMINISTRATOR,
            True,
            _now_iso(),
        )
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.put(
        "/projects/{project_id}/scenes/{scene_id}/organization",
        response_model=ProjectDraftResponse,
    )
    def organize_scene(
        project_id: str,
        scene_id: str,
        body: ObjectOrganizationRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, project_designer],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectDraftResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            current = services.repository.get_revision(project_id)
            draft = _organize_scene(current.draft, scene_id, body)
            revision = services.repository.update_draft(
                project_id=project_id,
                expected_revision=expected_revision,
                draft=draft,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except ProjectNotFoundError as error:
            raise HTTPException(status_code=404, detail={"code": "project_not_found"}) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "scene_organization_invalid", "message": str(error)},
            ) from error
        except RevisionConflictError as error:
            return _revision_conflict_response(error)
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.post(
        "/projects/{project_id}/presets",
        response_model=ProjectDraftResponse,
        status_code=201,
    )
    def create_scene_preset(
        project_id: str,
        body: CreateScenePresetRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, project_designer],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectDraftResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            current = services.repository.get_revision(project_id)
            draft = _create_scene_preset(current.draft, body)
            revision = services.repository.update_draft(
                project_id=project_id,
                expected_revision=expected_revision,
                draft=draft,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except ProjectNotFoundError as error:
            raise HTTPException(status_code=404, detail={"code": "project_not_found"}) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "preset_invalid", "message": str(error)},
            ) from error
        except RevisionConflictError as error:
            return _revision_conflict_response(error)
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.post(
        "/projects/{project_id}/presets/{preset_id}/apply",
        response_model=ProjectDraftResponse,
    )
    def apply_scene_preset(
        project_id: str,
        preset_id: str,
        body: ApplyScenePresetRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, project_designer],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectDraftResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            current = services.repository.get_revision(project_id)
            draft = _apply_scene_preset(current.draft, preset_id, body.scene_id)
            revision = services.repository.update_draft(
                project_id=project_id,
                expected_revision=expected_revision,
                draft=draft,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except ProjectNotFoundError as error:
            raise HTTPException(status_code=404, detail={"code": "project_not_found"}) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "preset_application_invalid", "message": str(error)},
            ) from error
        except RevisionConflictError as error:
            return _revision_conflict_response(error)
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.get(
        "/projects/{project_id}/assets",
        response_model=list[AssetResponse],
        dependencies=[project_reader],
    )
    def assets(project_id: str) -> list[AssetResponse]:
        try:
            stored = services.asset_upload.list_assets(project_id)
        except AssetUploadError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        return [_asset_response(asset) for asset in stored]

    @router.get(
        "/assets/image-library",
        response_model=list[AssetResponse],
        dependencies=[reader],
    )
    def image_asset_library() -> list[AssetResponse]:
        assets = (
            asset
            for project in services.repository.list_projects()
            for asset in services.asset_upload.list_assets(project.id)
        )
        return [
            _asset_response(asset)
            for asset in assets
            if asset.status == "ready"
            and asset.original_file_status == "available"
            and asset.media_type is not None
            and asset.media_type.startswith("image/")
        ]

    @router.get(
        "/assets/{asset_id}/artifacts/{kind}",
        response_class=FileResponse,
        dependencies=[reader],
    )
    def asset_artifact(asset_id: str, kind: str) -> FileResponse:
        try:
            path, media_type = services.asset_upload.get_artifact(asset_id, kind)
        except AssetUploadError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "asset_artifact_not_found", "message": str(error)},
            ) from error
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @router.put("/assets/{asset_id}/metadata", response_model=AssetResponse)
    def update_asset_metadata(
        asset_id: str,
        body: UpdateAssetMetadataRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, author],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> AssetResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            asset = services.asset_upload.update_metadata(
                asset_id=asset_id,
                expected_revision=expected_revision,
                owner=body.owner,
                license_name=body.license,
                provenance=body.provenance,
                review_status=body.review_status,
                expires_on=body.expires_on.isoformat() if body.expires_on is not None else None,
                folder=body.folder,
                tags=body.tags,
                favorite=body.favorite,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except AssetMetadataConflictError as error:
            return JSONResponse(
                status_code=409,
                content=AssetMetadataConflictResponse(
                    requested_revision=expected_revision,
                    current_revision=error.current_revision,
                ).model_dump(mode="json"),
            )
        except AssetMetadataLockedError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "asset_metadata_locked", "message": str(error)},
            ) from error
        except AssetUploadError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "asset_not_found"},
            ) from error
        response.headers["ETag"] = _etag(asset.metadata_revision)
        return _asset_response(asset)

    @router.post(
        "/projects/{project_id}/assets/{asset_id}/place",
        response_model=ProjectDraftResponse,
    )
    def place_asset(
        project_id: str,
        asset_id: str,
        body: PlaceAssetRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, project_designer],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectDraftResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            asset = services.asset_upload.get_asset(asset_id)
            revision = services.repository.get_revision(project_id)
        except (AssetUploadError, ProjectNotFoundError) as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "asset_or_project_not_found"},
            ) from error
        if asset.project_id != project_id:
            raise HTTPException(
                status_code=422,
                detail={"code": "asset_project_mismatch"},
            )
        if revision.project.archived_at is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "project_archived"},
            )
        if asset.status != "ready" or asset.original_file_status != "available":
            raise HTTPException(
                status_code=409,
                detail={"code": "asset_not_placeable", "status": asset.status},
            )
        if asset.current_revision is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "asset_revision_unavailable"},
            )
        try:
            draft = _place_asset(
                revision.draft,
                asset_id=asset.id,
                asset_revision=asset.current_revision,
                scene_id=body.scene_id,
                output_id=body.output_id,
            )
            updated = services.repository.update_draft(
                project_id=project_id,
                expected_revision=expected_revision,
                draft=draft,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "asset_placement_invalid", "message": str(error)},
            ) from error
        except RevisionConflictError as error:
            return JSONResponse(
                status_code=409,
                content=RevisionConflictResponse(
                    requested_revision=error.requested_revision,
                    current_revision=error.current_revision,
                    conflicting_paths=error.conflicting_paths,
                ).model_dump(mode="json"),
            )
        response.headers["ETag"] = _etag(updated.revision)
        return _project_draft(updated)

    @router.post(
        "/projects/{project_id}/assets/uploads",
        response_model=AssetUploadResponse,
        status_code=201,
    )
    def begin_asset_upload(
        project_id: str,
        body: BeginAssetUploadRequest,
        identity: Annotated[AuthenticatedSession, author],
    ) -> AssetUploadResponse:
        try:
            asset, upload = services.asset_upload.begin(
                project_id=project_id,
                name=body.name,
                original_filename=body.original_filename,
                media_type=body.media_type,
                total_bytes=body.total_bytes,
                expected_sha256=body.expected_sha256,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except AssetUploadError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "asset_upload_rejected", "message": str(error)},
            ) from error
        return _asset_upload_response(
            asset,
            upload,
            services.config.asset_chunk_max_bytes,
        )

    @router.get(
        "/assets/uploads/{upload_id}",
        response_model=AssetUploadResponse,
        dependencies=[reader],
    )
    def get_asset_upload(upload_id: str) -> AssetUploadResponse:
        try:
            upload = services.asset_upload.get_upload(upload_id)
            asset = services.asset_upload.get_asset(upload.asset_id)
        except AssetUploadError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "asset_upload_not_found"},
            ) from error
        return _asset_upload_response(
            asset,
            upload,
            services.config.asset_chunk_max_bytes,
        )

    @router.put(
        "/assets/uploads/{upload_id}",
        response_model=AssetUploadResponse,
    )
    async def append_asset_upload(
        upload_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        identity: Annotated[AuthenticatedSession, author],
        upload_offset: Annotated[int, Header(alias="Upload-Offset")],
    ) -> AssetUploadResponse:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > services.config.asset_chunk_max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "asset_chunk_too_large"},
                )
        try:
            asset, upload = services.asset_upload.append(
                upload_id=upload_id,
                offset=upload_offset,
                chunk=bytes(body),
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except AssetUploadConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "asset_upload_offset_conflict",
                    "received_bytes": error.received_bytes,
                },
            ) from error
        except AssetUploadError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "asset_upload_rejected", "message": str(error)},
            ) from error
        if upload.state == "completed" and asset.status == "uploading":
            background_tasks.add_task(
                services.asset_upload.process_asset,
                asset.id,
                occurred_at=_now_iso(),
            )
        return _asset_upload_response(
            asset,
            upload,
            services.config.asset_chunk_max_bytes,
        )

    @router.get(
        "/projects/{project_id}/draft",
        response_model=ProjectDraftResponse,
        dependencies=[project_reader],
    )
    def get_draft(project_id: str, response: Response) -> ProjectDraftResponse:
        try:
            revision = services.repository.get_revision(project_id)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.put("/projects/{project_id}/draft", response_model=ProjectDraftResponse)
    def update_draft(
        project_id: str,
        draft: DraftProject,
        response: Response,
        identity: Annotated[AuthenticatedSession, project_designer],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectDraftResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            revision = services.repository.update_draft(
                project_id=project_id,
                expected_revision=expected_revision,
                draft=draft,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        except RevisionConflictError as error:
            conflict = RevisionConflictResponse(
                requested_revision=error.requested_revision,
                current_revision=error.current_revision,
                conflicting_paths=error.conflicting_paths,
            )
            return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.get(
        "/projects/{project_id}/revisions",
        response_model=list[RevisionSummary],
        dependencies=[project_reader],
    )
    def revisions(project_id: str) -> list[RevisionSummary]:
        try:
            stored = services.repository.list_revisions(project_id)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        return [
            RevisionSummary(
                revision=revision.revision,
                parent_revision=revision.parent_revision,
                actor=revision.actor,
                transaction_kind=revision.transaction_kind,
                created_at=revision.created_at,
            )
            for revision in stored
        ]

    @router.post(
        "/projects/{project_id}/revisions/{source_revision}/restore",
        response_model=ProjectDraftResponse,
    )
    def restore_revision(
        project_id: str,
        source_revision: int,
        response: Response,
        identity: Annotated[AuthenticatedSession, project_designer],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectDraftResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            revision = services.repository.restore_revision(
                project_id=project_id,
                source_revision=source_revision,
                expected_revision=expected_revision,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_or_revision_not_found"},
            ) from error
        except RevisionConflictError as error:
            return JSONResponse(
                status_code=409,
                content=RevisionConflictResponse(
                    requested_revision=error.requested_revision,
                    current_revision=error.current_revision,
                    conflicting_paths=error.conflicting_paths,
                ).model_dump(mode="json"),
            )
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.post(
        "/projects/{project_id}/compile",
        response_model=CompilationResponse,
    )
    def compile_project(
        project_id: str,
        identity: Annotated[AuthenticatedSession, project_designer],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> CompilationResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            revision = services.repository.get_revision(project_id)
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        if revision.revision != expected_revision:
            raise HTTPException(
                status_code=409,
                detail=RevisionConflictResponse(
                    requested_revision=expected_revision,
                    current_revision=revision.revision,
                    conflicting_paths=(),
                ).model_dump(mode="json"),
            )
        installation_id = revision.draft.editor_metadata.get("installation_id")
        if isinstance(installation_id, str):
            diagnostics = services.mapper_export.validate(installation_id)
            if diagnostics:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "mapper_validation_failed",
                        "diagnostics": [
                            {
                                "code": diagnostic.code,
                                "message": diagnostic.message,
                                "object_ids": diagnostic.object_ids,
                            }
                            for diagnostic in diagnostics
                        ],
                    },
                )
        try:
            compiled = services.compiler.compile(
                project_id=project_id,
                revision=revision.revision,
                draft=revision.draft,
                lighting_artifacts=services.render_jobs.lighting_artifacts(
                    project_id,
                    revision.revision,
                ),
            )
        except (CompilationError, RenderJobError, MapperExportError, AssetUploadError) as error:
            services.repository.append_audit(
                actor=identity.username,
                action="project.compile",
                target=project_id,
                result="validation_failed",
                occurred_at=_now_iso(),
                authorization_context={
                    "role": identity.role.value,
                    "revision": revision.revision,
                },
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "compilation_failed",
                    "message": str(error),
                    "diagnostics": [
                        {
                            "code": diagnostic.code,
                            "message": diagnostic.message,
                            "object_ids": diagnostic.object_ids,
                        }
                        for diagnostic in (
                            error.diagnostics if isinstance(error, CompilationError) else ()
                        )
                    ],
                },
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="project.compile",
            target=project_id,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={
                "role": identity.role.value,
                "revision": revision.revision,
                "package_digest": compiled.digest,
            },
        )
        return CompilationResponse(
            project_id=project_id,
            revision=revision.revision,
            package_digest=compiled.digest,
            files=compiled.files,
            scene_ids=compiled.scene_ids,
        )

    @router.get(
        "/projects/{project_id}/revisions/{revision}/release",
        response_model=RevisionReleaseResponse,
        dependencies=[project_reader],
    )
    def revision_release(project_id: str, revision: int) -> RevisionReleaseResponse:
        try:
            release = services.publication.release(project_id, revision)
        except ProjectNotFoundError as error:
            raise HTTPException(status_code=404, detail={"code": "revision_not_found"}) from error
        return RevisionReleaseResponse.model_validate(release.__dict__)

    @router.put(
        "/projects/{project_id}/revisions/{revision}/release",
        response_model=RevisionReleaseResponse,
    )
    def update_revision_release(
        project_id: str,
        revision: int,
        body: UpdateRevisionReleaseRequest,
        identity: Annotated[AuthenticatedSession, project_publisher],
    ) -> RevisionReleaseResponse:
        occurred_at = _now_iso()
        try:
            release = services.publication.update_release(
                project_id=project_id,
                revision=revision,
                name=body.name,
                summary=body.summary,
                approval_state=body.approval_state,
                actor=identity.username,
                occurred_at=occurred_at,
            )
        except ProjectNotFoundError as error:
            raise HTTPException(status_code=404, detail={"code": "revision_not_found"}) from error
        services.repository.append_audit(
            actor=identity.username,
            action="revision.release.update",
            target=f"{project_id}@{revision}",
            result="success",
            occurred_at=occurred_at,
            authorization_context={
                "role": identity.role.value,
                "approval_state": release.approval_state,
            },
        )
        return RevisionReleaseResponse.model_validate(release.__dict__)

    @router.get(
        "/projects/{project_id}/revision-diff",
        response_model=RevisionDiffResponse,
        dependencies=[project_reader],
    )
    def revision_diff(
        project_id: str,
        base_revision: int,
        candidate_revision: int,
    ) -> RevisionDiffResponse:
        try:
            changed = services.publication.diff(project_id, base_revision, candidate_revision)
        except ProjectNotFoundError as error:
            raise HTTPException(status_code=404, detail={"code": "revision_not_found"}) from error
        return RevisionDiffResponse(
            project_id=project_id,
            base_revision=base_revision,
            candidate_revision=candidate_revision,
            changed_paths=changed,
        )

    @router.get(
        "/projects/{project_id}/revisions/{revision}/publication-validation",
        response_model=PublicationValidationResponse,
        dependencies=[project_reader],
    )
    def publication_validation(
        project_id: str,
        revision: int,
    ) -> PublicationValidationResponse:
        try:
            diagnostics = services.publication.validate(project_id, revision)
        except ProjectNotFoundError as error:
            raise HTTPException(status_code=404, detail={"code": "revision_not_found"}) from error
        responses = tuple(
            PublicationDiagnosticResponse.model_validate(item.__dict__) for item in diagnostics
        )
        return PublicationValidationResponse(
            project_id=project_id,
            revision=revision,
            passed=not any(item.severity == "error" for item in diagnostics),
            diagnostics=responses,
        )

    @router.get(
        "/projects/{project_id}/published-packages",
        response_model=list[PublishedPackageResponse],
        dependencies=[project_reader],
    )
    def published_packages(project_id: str) -> list[PublishedPackageResponse]:
        return [_published_package(item) for item in services.publication.list(project_id)]

    @router.post(
        "/projects/{project_id}/revisions/{revision}/publish",
        response_model=PublishedPackageResponse,
        status_code=201,
    )
    def publish_revision(
        project_id: str,
        revision: int,
        identity: Annotated[AuthenticatedSession, project_publisher],
    ) -> PublishedPackageResponse:
        occurred_at = _now_iso()
        try:
            package = services.publication.publish(
                project_id=project_id,
                revision=revision,
                actor=identity.username,
                occurred_at=occurred_at,
            )
        except (ProjectNotFoundError, PublicationError, CompilationError) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "publication_failed", "message": str(error)},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="package.publish",
            target=package.id,
            result="success",
            occurred_at=occurred_at,
            authorization_context={
                "role": identity.role.value,
                "project_id": project_id,
                "revision": revision,
                "package_digest": package.digest_sha256,
            },
        )
        return _published_package(package)

    @router.post(
        "/published-packages/{package_id}/{action}",
        response_model=PublishedPackageResponse,
    )
    async def transition_published_package(
        package_id: str,
        action: Literal["stage", "activate", "deactivate", "rollback"],
        body: ActivationRequest,
        identity: Annotated[AuthenticatedSession, Depends(mutation_session)],
    ) -> PublishedPackageResponse:
        occurred_at = _now_iso()
        try:
            candidate = services.publication.get(package_id)
            if identity.role is not Role.ADMINISTRATOR:
                membership = services.collaboration.get_membership(
                    candidate.project_id,
                    identity.username,
                )
                if membership is None or membership.role not in {
                    Role.PUBLISHER,
                    Role.ADMINISTRATOR,
                }:
                    raise HTTPException(
                        status_code=403,
                        detail={"code": "insufficient_project_role"},
                    )
                if action in {"activate", "deactivate", "rollback"} and not (
                    membership.can_live_output
                ):
                    raise HTTPException(
                        status_code=403,
                        detail={"code": "live_output_permission_required"},
                    )
            if action == "stage":
                package = await services.publication.stage(package_id, occurred_at)
            elif action == "activate":
                package = await services.publication.activate(
                    package_id,
                    occurred_at=occurred_at,
                    simulation_mode=body.simulation_mode,
                )
            elif action == "rollback":
                package = await services.publication.rollback(
                    package_id,
                    occurred_at=occurred_at,
                    simulation_mode=body.simulation_mode,
                )
            else:
                package = services.publication.deactivate(package_id, occurred_at)
        except (PublicationError, ArtifactDeploymentError, DeviceSessionError) as error:
            raise HTTPException(
                status_code=409,
                detail={"code": f"package_{action}_failed", "message": str(error)},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action=f"package.{action}",
            target=package.id,
            result="success",
            occurred_at=occurred_at,
            authorization_context={
                "role": identity.role.value,
                "project_id": package.project_id,
                "revision": package.revision,
                "package_digest": package.digest_sha256,
            },
        )
        return _published_package(package)

    @router.get(
        "/projects/{project_id}/compositions/{composition_id}/preview-frame",
        dependencies=[project_reader],
    )
    def projector_composition_preview_frame(
        project_id: str,
        composition_id: str,
        timestamp_ms: int,
        resource: str | None = None,
    ) -> Response:
        try:
            revision = services.repository.get_revision(project_id)
            composition = revision.draft.editor_metadata.get("compositions", {})
            raw_composition = (
                composition.get(composition_id) if isinstance(composition, dict) else None
            )
            if isinstance(raw_composition, dict) and raw_composition.get("space_kind") == "stage":
                installation_id = revision.draft.editor_metadata.get("installation_id")
                if not isinstance(installation_id, str) or resource is None:
                    raise ProjectorRenderError(
                        "stage preview requires installation and projector resource"
                    )
                frame = preview_stage_composition_member(
                    project_id=project_id,
                    source_revision=revision.revision,
                    draft=revision.draft,
                    composition_id=composition_id,
                    assets=services.asset_upload,
                    mapper_export=services.mapper_export.export(installation_id),
                    coverage=services.stage_coverage.analyze(installation_id),
                    overlaps=services.projector_overlap.list(installation_id),
                    resource=resource,
                    timestamp_ms=timestamp_ms,
                )
            else:
                frame = preview_projector_composition(
                    project_id=project_id,
                    draft=revision.draft,
                    composition_id=composition_id,
                    timestamp_ms=timestamp_ms,
                    assets=services.asset_upload,
                )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        except (
            ProjectorRenderError,
            AssetUploadError,
            MapperExportError,
        ) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "preview_frame_invalid", "message": str(error)},
            ) from error
        output = BytesIO()
        Image.fromarray(frame, mode="RGBA").save(output, format="PNG")
        return Response(
            content=output.getvalue(),
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Cueverge-Render-Pipeline-Revision": "1",
            },
        )

    @router.get(
        "/projects/{project_id}/compositions/{composition_id}/lighting-preview",
        dependencies=[project_reader],
    )
    def lighting_composition_preview(
        project_id: str,
        composition_id: str,
        timestamp_ms: int,
        include_canvas: bool = False,
    ) -> dict[str, object]:
        try:
            revision = services.repository.get_revision(project_id)
            installation_id = revision.draft.editor_metadata.get("installation_id")
            if not isinstance(installation_id, str):
                raise LightingPreviewError("lighting preview requires installation ID")
            mapper_export = services.mapper_export.export(installation_id)
            surface = resolve_lighting_preview_surface(
                draft=revision.draft,
                composition_id=composition_id,
                mapper_export=mapper_export,
            )
            resource = surface.get("resource")
            assignment = next(
                (
                    candidate
                    for candidate in services.repository.list_resource_assignments()
                    if candidate.resource == resource
                ),
                None,
            )
            device_reported_scale = (
                services.device_sessions.output_scale(
                    UUID(assignment.device_id),
                    assignment.device_output or assignment.resource,
                )
                if assignment is not None
                else None
            )
            return evaluate_lighting_preview(
                project_id=project_id,
                draft=revision.draft,
                composition_id=composition_id,
                timestamp_ms=timestamp_ms,
                assets=services.asset_upload,
                mapper_export=mapper_export,
                device_reported_scale=device_reported_scale,
                include_canvas=include_canvas,
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        except (
            LightingPreviewError,
            ProjectorRenderError,
            AssetUploadError,
            MapperExportError,
            ValueError,
        ) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "lighting_preview_invalid", "message": str(error)},
            ) from error

    @router.get(
        "/projects/{project_id}/compositions/{composition_id}/stage-lighting-preview",
        dependencies=[project_reader],
    )
    def stage_lighting_preview(
        project_id: str,
        composition_id: str,
        timestamp_ms: int,
    ) -> dict[str, object]:
        try:
            revision = services.repository.get_revision(project_id)
            installation_id = revision.draft.editor_metadata.get("installation_id")
            if not isinstance(installation_id, str):
                raise ProjectorRenderError("stage lighting preview requires installation ID")
            mapper_export = services.mapper_export.export(installation_id)
            assignments = {
                assignment.resource: assignment
                for assignment in services.repository.list_resource_assignments()
            }
            device_scales: dict[str, float] = {}
            raw_surfaces = mapper_export.get("lighting_surfaces")
            for value in raw_surfaces if isinstance(raw_surfaces, list) else []:
                if not isinstance(value, dict):
                    continue
                resource = value.get("resource")
                if not isinstance(resource, str):
                    continue
                assignment = assignments.get(resource)
                if assignment is None:
                    continue
                scale = services.device_sessions.output_scale(
                    UUID(assignment.device_id),
                    assignment.device_output or assignment.resource,
                )
                if scale is not None:
                    device_scales[resource] = scale
            return preview_stage_lighting_samples(
                project_id=project_id,
                draft=revision.draft,
                composition_id=composition_id,
                timestamp_ms=timestamp_ms,
                assets=services.asset_upload,
                mapper_export=mapper_export,
                device_reported_scales=device_scales,
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        except (ProjectorRenderError, AssetUploadError, MapperExportError) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "stage_lighting_preview_invalid", "message": str(error)},
            ) from error

    @router.get(
        "/projects/{project_id}/preview-evaluation",
        dependencies=[project_reader],
    )
    def preview_evaluation(
        project_id: str,
        space_id: str,
        timestamp_ms: int,
    ) -> dict[str, JsonValue]:
        try:
            revision = services.repository.get_revision(project_id)
            return evaluate_preview_state(
                project_id=project_id,
                source_revision=revision.revision,
                draft=revision.draft,
                space_id=space_id,
                timestamp_ms=timestamp_ms,
                assets=services.asset_upload,
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error

        except PreviewEvaluationError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "preview_evaluation_invalid", "message": str(error)},
            ) from error
        except (
            LightingPreviewError,
            ProjectorRenderError,
            AssetUploadError,
            MapperExportError,
        ) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "lighting_preview_invalid", "message": str(error)},
            ) from error

    @router.post("/projects/{project_id}/scene-lighting-preview")
    def scene_lighting_preview(
        project_id: str,
        body: SceneLightingPreviewRequest,
        identity: Annotated[AuthenticatedSession, project_commenter],
    ) -> dict[str, object]:
        try:
            return preview_project_scene(services, project_id, body)
        except ScenePreviewBusyError as error:
            raise HTTPException(
                status_code=429, detail={"code": "scene_preview_busy", "message": str(error)}
            ) from error
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404, detail={"code": "project_or_revision_not_found"}
            ) from error
        except (ScenePreviewError, CompilationError, ShowPackageError) as error:
            raise HTTPException(
                status_code=422, detail={"code": "scene_preview_invalid", "message": str(error)}
            ) from error

    @router.post(
        "/projects/{project_id}/render-jobs",
        response_model=RenderJobResponse,
        status_code=202,
    )
    def create_render_job(
        project_id: str,
        body: CreateRenderJobRequest,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> RenderJobResponse:
        try:
            job = services.render_jobs.create(
                project_id=project_id,
                composition_id=body.composition_id,
                actor=identity.username,
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        except RenderJobError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "render_job_invalid", "message": str(error)},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="render_job.create",
            target=job.id,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={
                "role": identity.role.value,
                "project_id": project_id,
                "composition_id": body.composition_id,
                "cache_key": job.cache_key,
            },
        )
        return _render_job(job)

    @router.get(
        "/projects/{project_id}/render-jobs",
        response_model=list[RenderJobResponse],
        dependencies=[project_reader],
    )
    def render_jobs(project_id: str) -> list[RenderJobResponse]:
        return [_render_job(job) for job in services.render_jobs.list(project_id)]

    @router.post("/projects/{project_id}/apply", status_code=202)
    async def apply_saved_show(
        project_id: str,
        body: ApplyRequest,
        identity: Annotated[AuthenticatedSession, project_publisher],
    ) -> dict[str, object]:
        if not services.apply.authorize(
            project_id, identity.username, None, body.restore_package_id is not None
        ):
            raise HTTPException(403, detail={"code": "live_output_permission_required"})
        try:
            return await services.apply.start(project_id, body, identity.username)
        except (ApplyError, ProjectNotFoundError) as error:
            raise HTTPException(
                409, detail={"code": "apply_not_started", "message": str(error)}
            ) from error

    @router.get("/projects/{project_id}/apply-jobs", dependencies=[project_reader])
    def apply_jobs(project_id: str) -> list[dict[str, object]]:
        return services.apply.list(project_id)

    @router.get("/projects/{project_id}/apply-jobs/{job_id}", dependencies=[project_reader])
    def apply_job(project_id: str, job_id: str) -> dict[str, object]:
        try:
            job = services.apply.get(job_id)
        except ApplyError as error:
            raise HTTPException(404, detail={"code": "apply_job_not_found"}) from error
        if job["project_id"] != project_id:
            raise HTTPException(403, detail={"code": "apply_job_outside_project"})
        return job

    @router.post("/projects/{project_id}/apply-jobs/{job_id}/cancel")
    def cancel_apply(
        project_id: str,
        job_id: str,
        identity: Annotated[AuthenticatedSession, project_publisher],
    ) -> dict[str, object]:
        job = services.apply.get(job_id)
        if job["project_id"] != project_id or not services.apply.authorize(
            project_id, identity.username, None, True
        ):
            raise HTTPException(403, detail={"code": "live_output_permission_required"})
        try:
            return services.apply.cancel(job_id)
        except ApplyError as error:
            raise HTTPException(
                409, detail={"code": "apply_cancel_failed", "message": str(error)}
            ) from error

    @router.get("/projects/{project_id}/interactions", dependencies=[project_reader])
    def interactions(project_id: str) -> dict[str, object]:
        stored = services.repository.get_revision(project_id)
        return {"source_revision": stored.revision, **describe_interactions(stored.draft)}

    @router.put("/projects/{project_id}/interactions", response_model=ProjectDraftResponse)
    def edit_interaction(
        project_id: str,
        body: InteractionEditRequest,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> ProjectDraftResponse:
        stored = services.repository.get_revision(project_id)
        if stored.revision != body.source_revision:
            raise HTTPException(
                409,
                detail={
                    "code": "revision_conflict",
                    "message": "This show changed. Reload saved data before editing.",
                },
            )
        try:
            candidate = apply_interaction_edit(stored.draft, body)
            updated = services.mcp_authoring.update_project_draft(
                project_id=project_id,
                base_revision=stored.revision,
                proposed_document=candidate.model_dump(mode="json"),
                username=identity.username,
                role=identity.role,
            )
        except (InteractionError, McpAuthoringError) as error:
            raise HTTPException(
                422, detail={"code": "interaction_invalid", "message": str(error)}
            ) from error
        return _project_draft(updated)

    @router.post("/projects/{project_id}/interaction-preview")
    async def interaction_preview(
        project_id: str,
        body: InteractionPreviewRequest,
        identity: Annotated[AuthenticatedSession, project_simulator],
    ) -> dict[str, object]:
        try:
            return await preview_interactions(services, project_id, body)
        except (
            InteractionError,
            CompilationError,
            ShowPackageError,
            ValueError,
            LookupError,
        ) as error:
            raise HTTPException(
                422, detail={"code": "interaction_preview_invalid", "message": str(error)}
            ) from error

    @router.get(
        "/projects/{project_id}/simulation-scenarios",
        response_model=list[SimulationScenarioResponse],
        dependencies=[project_reader],
    )
    def simulation_scenarios(project_id: str) -> list[SimulationScenarioResponse]:
        try:
            services.repository.get_revision(project_id)
            return [
                _simulation_scenario(scenario)
                for scenario in services.simulation_scenarios.list(project_id)
            ]
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error

    @router.post(
        "/projects/{project_id}/simulation-scenarios",
        response_model=SimulationScenarioResponse,
        status_code=201,
    )
    def save_simulation_scenario(
        project_id: str,
        body: SaveSimulationScenarioRequest,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> SimulationScenarioResponse:
        try:
            revision = services.repository.get_revision(project_id)
            scenario = services.simulation_scenarios.save(
                project_id=project_id,
                name=body.name,
                source_revision=revision.revision,
                steps=tuple(
                    cast(dict[str, JsonValue], step.model_dump(mode="json")) for step in body.steps
                ),
                expected=body.expected,
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found"},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="simulation_scenario.save",
            target=scenario.id,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={
                "role": identity.role.value,
                "project_id": project_id,
                "source_revision": scenario.source_revision,
                "hardware_command_sent": False,
            },
        )
        return _simulation_scenario(scenario)

    @router.post(
        "/projects/{project_id}/simulation-scenarios/{scenario_id}/run",
        response_model=SimulationRunResponse,
    )
    async def run_simulation_scenario(
        project_id: str,
        scenario_id: str,
        identity: Annotated[AuthenticatedSession, project_designer],
    ) -> SimulationRunResponse:
        try:
            scenario = services.simulation_scenarios.get(project_id, scenario_id)
            revision = services.repository.get_revision(
                project_id,
                scenario.source_revision,
            )
            compiled = services.compiler.compile(
                project_id=project_id,
                revision=revision.revision,
                draft=revision.draft,
                lighting_artifacts=services.render_jobs.lighting_artifacts(
                    project_id,
                    revision.revision,
                ),
            )
            result = await services.simulation_scenarios.run(
                scenario,
                load_show_package(compiled.package_root),
            )
        except (
            ProjectNotFoundError,
            SimulationScenarioError,
            RenderJobError,
            MapperExportError,
            AssetUploadError,
            CompilationError,
        ) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "simulation_scenario_invalid", "message": str(error)},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="simulation_scenario.run",
            target=scenario.id,
            result="success" if result["passed"] else "failure",
            occurred_at=_now_iso(),
            authorization_context={
                "role": identity.role.value,
                "project_id": project_id,
                "source_revision": scenario.source_revision,
                "hardware_command_sent": False,
            },
        )
        return SimulationRunResponse.model_validate(result)

    @router.get(
        "/render-jobs/{job_id}",
        response_model=RenderJobResponse,
        dependencies=[reader],
    )
    def render_job(job_id: str) -> RenderJobResponse:
        try:
            return _render_job(services.render_jobs.get(job_id))
        except RenderJobError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "render_job_not_found"},
            ) from error

    @router.get(
        "/render-artifacts/{artifact_id}/preview-frame",
        dependencies=[reader],
    )
    def render_artifact_preview_frame(
        artifact_id: str,
        resource: str,
        timestamp_ms: int,
    ) -> Response:
        try:
            manifest = services.repository.get_render_artifact_manifest(artifact_id)
            duration_ms = manifest.get("duration_ms")
            if not isinstance(duration_ms, int):
                raise ArtifactPreviewError("artifact duration is invalid")
            member = services.repository.get_render_artifact_member(artifact_id, resource)
            frame = extract_artifact_preview_frame(
                member.path,
                timestamp_ms=timestamp_ms,
                duration_ms=duration_ms,
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "render_artifact_not_found"},
            ) from error
        except (ArtifactPreviewError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "render_artifact_preview_invalid", "message": str(error)},
            ) from error
        return Response(
            content=frame,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Cueverge-Artifact-Id": artifact_id,
                "X-Cueverge-Artifact-Digest": member.digest_sha256,
            },
        )

    @router.post(
        "/render-jobs/{job_id}/cancel",
        response_model=RenderJobResponse,
    )
    def cancel_render_job(
        job_id: str,
        identity: Annotated[AuthenticatedSession, author],
    ) -> RenderJobResponse:
        try:
            job = services.render_jobs.cancel(job_id)
        except RenderJobError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "render_job_not_cancelable", "message": str(error)},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="render_job.cancel",
            target=job_id,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={"role": identity.role.value},
        )
        return _render_job(job)

    @router.post(
        "/projector-sync-rehearsals",
        response_model=SyncRehearsalResponse,
        status_code=201,
    )
    def record_projector_sync_rehearsal(
        body: RecordSyncRehearsalRequest,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> SyncRehearsalResponse:
        try:
            rehearsal = services.sync_rehearsal.record(
                resources=body.resources,
                start_error_ms=body.start_error_ms,
                drift_ms_per_minute=body.drift_ms_per_minute,
                measurement_duration_seconds=body.measurement_duration_seconds,
                context=services.render_jobs.resource_context(body.resources),
                notes=body.notes,
                actor=identity.username,
                measured_at=body.measured_at,
            )
        except (SyncRehearsalError, RenderJobError) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "sync_rehearsal_invalid", "message": str(error)},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="projector_sync_rehearsal.record",
            target=rehearsal.id,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={
                "role": identity.role.value,
                "resources": list(rehearsal.resources),
                "hardware_command_sent": False,
            },
        )
        return _sync_rehearsal(rehearsal)

    @router.post(
        "/projector-sync-rehearsals/evaluate",
        response_model=SyncAdmissionResponse,
        dependencies=[reader],
    )
    def evaluate_projector_sync_rehearsal(
        body: EvaluateSyncAdmissionRequest,
    ) -> SyncAdmissionResponse:
        try:
            admission = services.sync_rehearsal.evaluate(
                resources=body.resources,
                current_context=services.render_jobs.resource_context(body.resources),
                now=_now_iso(),
            )
        except RenderJobError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "sync_admission_invalid", "message": str(error)},
            ) from error
        return _sync_admission(admission)

    @router.post(
        "/render-artifacts/{artifact_id}/deploy",
        response_model=ArtifactDeploymentResponse,
    )
    async def deploy_render_artifact(
        artifact_id: str,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> ArtifactDeploymentResponse:
        try:
            result = await services.artifact_deployment.deploy(artifact_id)
        except (ArtifactDeploymentError, DeviceSessionError, ValueError) as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "artifact_deployment_failed",
                    "message": str(error),
                },
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="render_artifact.deploy",
            target=artifact_id,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={
                "role": identity.role.value,
                "activation_identity": result.activation_identity,
                "resources": list(result.resources),
            },
        )
        return ArtifactDeploymentResponse(
            activation_identity=result.activation_identity,
            artifact_id=result.artifact_id,
            resources=result.resources,
            staged_device_ids=result.staged_device_ids,
            activated_device_ids=result.activated_device_ids,
        )

    @router.get(
        "/installations",
        response_model=list[InstallationSummaryResponse],
        dependencies=[reader],
    )
    def installations() -> list[InstallationSummaryResponse]:
        return [
            _installation_summary(
                installation,
                services.repository.installation_project_references(installation.id),
            )
            for installation in services.repository.list_installations()
        ]

    @router.post(
        "/installations",
        response_model=InstallationSummaryResponse,
        status_code=201,
    )
    def create_installation(
        body: CreateInstallationRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> InstallationSummaryResponse:
        try:
            installation = services.repository.create_installation(
                name=body.name,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationNameConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "installation_name_conflict"},
            ) from error
        response.headers["ETag"] = _etag(installation.revision)
        return _installation_summary(installation, ())

    @router.get(
        "/installations/{installation_id}",
        response_model=InstallationSummaryResponse,
        dependencies=[reader],
    )
    def get_installation(installation_id: str, response: Response) -> InstallationSummaryResponse:
        try:
            installation = services.repository.get_installation(installation_id)
        except InstallationNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "installation_not_found"},
            ) from error
        response.headers["ETag"] = _etag(installation.revision)
        return _installation_summary(
            installation,
            services.repository.installation_project_references(installation.id),
        )

    @router.put(
        "/installations/{installation_id}",
        response_model=InstallationSummaryResponse,
    )
    def update_installation(
        installation_id: str,
        body: UpdateInstallationRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> InstallationSummaryResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            installation = services.repository.update_installation(
                installation_id=installation_id,
                expected_revision=expected_revision,
                name=body.name,
                folder=body.folder,
                tags=body.tags,
                favorite=body.favorite,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "installation_not_found"},
            ) from error
        except InstallationNameConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "installation_name_conflict"},
            ) from error
        except InstallationConflictError as error:
            return JSONResponse(
                status_code=409,
                content=InstallationConflictResponse(
                    requested_revision=expected_revision,
                    current_revision=error.current_revision,
                ).model_dump(mode="json"),
            )
        response.headers["ETag"] = _etag(installation.revision)
        return _installation_summary(
            installation,
            services.repository.installation_project_references(installation.id),
        )

    @router.get(
        "/installations/{installation_id}/photos",
        response_model=list[InstallationPhotoResponse],
        dependencies=[reader],
    )
    def installation_photos(installation_id: str) -> list[InstallationPhotoResponse]:
        return [
            _installation_photo(photo)
            for photo in services.installation_photos.list(installation_id)
        ]

    @router.post(
        "/installations/{installation_id}/photos",
        response_model=InstallationPhotoResponse,
        status_code=201,
    )
    async def create_installation_photo(
        installation_id: str,
        request: Request,
        identity: Annotated[AuthenticatedSession, author],
        original_filename: Annotated[str, Header(alias="X-Original-Filename")],
        content_type: Annotated[str, Header(alias="Content-Type")],
        photo_name: Annotated[str | None, Header(alias="X-Photo-Name")] = None,
        viewpoint: Annotated[str | None, Header(alias="X-Photo-Viewpoint")] = None,
        description: Annotated[str | None, Header(alias="X-Photo-Description")] = None,
        metadata_encoding: Annotated[
            Literal["uri"] | None, Header(alias="X-Photo-Metadata-Encoding")
        ] = None,
    ) -> InstallationPhotoResponse:
        try:
            if metadata_encoding == "uri":
                original_filename = unquote(original_filename, errors="strict")
                photo_name = (
                    unquote(photo_name, errors="strict") if photo_name is not None else None
                )
                viewpoint = unquote(viewpoint, errors="strict") if viewpoint is not None else None
                description = (
                    unquote(description, errors="strict") if description is not None else None
                )
            label = PhotoDescriptionRequest(
                description=description if description is not None else photo_name or "",
            ).description
        except (ValidationError, UnicodeError):
            raise HTTPException(
                422,
                detail={
                    "code": "photo_description_required",
                    "message": "Describe what the photo shows or is used for (1-200 characters).",
                },
            ) from None
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_PHOTO_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "installation_photo_too_large"},
                )
        try:
            photo = services.installation_photos.create(
                installation_id=installation_id,
                name=label,
                viewpoint=viewpoint if viewpoint is not None else label,
                original_filename=original_filename,
                media_type=content_type,
                content=bytes(content),
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationPhotoError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "installation_photo_rejected", "message": str(error)},
            ) from error
        return _installation_photo(photo)

    @router.get(
        "/installation-photos/{photo_id}/content",
        response_class=FileResponse,
        dependencies=[reader],
    )
    def installation_photo_content(photo_id: str) -> FileResponse:
        try:
            path, media_type = services.installation_photos.content(photo_id)
        except InstallationPhotoError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "installation_photo_not_found"},
            ) from error
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private"})

    @router.patch(
        "/installation-photos/{photo_id}/description", response_model=InstallationPhotoResponse
    )
    def describe_installation_photo(
        photo_id: str,
        body: PhotoDescriptionRequest,
        identity: Annotated[AuthenticatedSession, author],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> InstallationPhotoResponse:
        try:
            photo = services.installation_photos.describe(
                photo_id=photo_id,
                expected_revision=_parse_if_match(if_match),
                description=body.description,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationPhotoConflictError as error:
            raise HTTPException(
                409,
                detail={
                    "code": "installation_photo_conflict",
                    "message": "This photo changed. Reopen its details before saving.",
                },
            ) from error
        except InstallationPhotoError as error:
            raise HTTPException(404, detail={"code": "installation_photo_not_found"}) from error
        return _installation_photo(photo)

    @router.put(
        "/installation-photos/{photo_id}",
        response_model=InstallationPhotoResponse,
    )
    def update_installation_photo(
        photo_id: str,
        body: UpdateInstallationPhotoRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, author],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> InstallationPhotoResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            photo = services.installation_photos.update(
                photo_id=photo_id,
                expected_revision=expected_revision,
                name=body.name,
                viewpoint=body.viewpoint,
                orientation_degrees=body.orientation_degrees,
                crop_x=body.crop_x,
                crop_y=body.crop_y,
                crop_width=body.crop_width,
                crop_height=body.crop_height,
                scale_hint=body.scale_hint,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationPhotoConflictError as error:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "installation_photo_conflict",
                    "requested_revision": expected_revision,
                    "current_revision": error.current_revision,
                },
            )
        except InstallationPhotoError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "installation_photo_not_found"},
            ) from error
        response.headers["ETag"] = _etag(photo.revision)
        return _installation_photo(photo)

    @router.delete(
        "/installation-photos/{photo_id}",
        status_code=204,
        response_class=Response,
        response_model=None,
    )
    def delete_installation_photo(
        photo_id: str,
        identity: Annotated[AuthenticatedSession, author],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> Response | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            services.installation_photos.delete(
                photo_id=photo_id,
                expected_revision=expected_revision,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationPhotoConflictError as error:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "installation_photo_conflict",
                    "requested_revision": expected_revision,
                    "current_revision": error.current_revision,
                },
            )
        except InstallationPhotoError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "installation_photo_not_found"},
            ) from error
        return Response(status_code=204)

    @router.get(
        "/installations/{installation_id}/geometry",
        response_model=list[InstallationGeometryResponse],
        dependencies=[reader],
    )
    def installation_geometry(
        installation_id: str,
        photo_id: str | None = None,
    ) -> list[InstallationGeometryResponse]:
        return [
            _installation_geometry(item)
            for item in services.installation_geometry.list(installation_id, photo_id)
        ]

    @router.post(
        "/installations/{installation_id}/geometry",
        response_model=InstallationGeometryResponse,
        status_code=201,
    )
    def create_installation_geometry(
        installation_id: str,
        body: InstallationGeometryRequest,
        identity: Annotated[AuthenticatedSession, author],
    ) -> InstallationGeometryResponse:
        try:
            geometry = services.installation_geometry.create(
                installation_id=installation_id,
                photo_id=body.photo_id,
                kind=body.kind,
                name=body.name,
                points=tuple((point.x, point.y) for point in body.points),
                closed=body.closed,
                semantic_id=body.semantic_id,
                color=body.color,
                label_visible=body.label_visible,
                locked=body.locked,
                hidden=body.hidden,
                z_index=body.z_index,
                direction=body.direction,
                start_index=body.start_index,
                pixel_count=body.pixel_count,
                stage_points=body.stage_points,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationGeometryError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "installation_geometry_rejected", "message": str(error)},
            ) from error
        return _installation_geometry(geometry)

    @router.put(
        "/installation-geometry/{geometry_id}",
        response_model=InstallationGeometryResponse,
    )
    def update_installation_geometry(
        geometry_id: str,
        body: InstallationGeometryRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, author],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> InstallationGeometryResponse | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            geometry = services.installation_geometry.update(
                geometry_id=geometry_id,
                expected_revision=expected_revision,
                photo_id=body.photo_id,
                kind=body.kind,
                name=body.name,
                points=tuple((point.x, point.y) for point in body.points),
                closed=body.closed,
                color=body.color,
                label_visible=body.label_visible,
                locked=body.locked,
                hidden=body.hidden,
                z_index=body.z_index,
                direction=body.direction,
                start_index=body.start_index,
                pixel_count=body.pixel_count,
                stage_points=body.stage_points,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationGeometryConflictError as error:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "installation_geometry_conflict",
                    "requested_revision": expected_revision,
                    "current_revision": error.current_revision,
                },
            )
        except InstallationGeometryError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "installation_geometry_rejected", "message": str(error)},
            ) from error
        response.headers["ETag"] = _etag(geometry.revision)
        return _installation_geometry(geometry)

    @router.delete(
        "/installation-geometry/{geometry_id}",
        status_code=204,
        response_class=Response,
        response_model=None,
    )
    def delete_installation_geometry(
        geometry_id: str,
        identity: Annotated[AuthenticatedSession, author],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> Response | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            services.installation_geometry.delete(
                geometry_id=geometry_id,
                expected_revision=expected_revision,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationGeometryConflictError as error:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "installation_geometry_conflict",
                    "requested_revision": expected_revision,
                    "current_revision": error.current_revision,
                },
            )
        except InstallationGeometryError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "installation_geometry_not_found"},
            ) from error
        return Response(status_code=204)

    @router.get(
        "/installations/{installation_id}/geometry-suggestions",
        response_model=list[GeometrySuggestionResponse],
        dependencies=[reader],
    )
    def geometry_suggestions(
        installation_id: str,
    ) -> list[GeometrySuggestionResponse]:
        return [
            _geometry_suggestion(suggestion)
            for suggestion in services.geometry_suggestions.list(installation_id)
        ]

    @router.post(
        "/installations/{installation_id}/geometry-suggestions",
        response_model=GeometrySuggestionResponse,
        status_code=201,
    )
    def create_geometry_suggestion(
        installation_id: str,
        body: GeometrySuggestionRequest,
        identity: Annotated[AuthenticatedSession, author],
    ) -> GeometrySuggestionResponse:
        try:
            suggestion = services.geometry_suggestions.create(
                installation_id=installation_id,
                photo_id=body.photo_id,
                kind=body.kind,
                name=body.name,
                points=tuple((point.x, point.y) for point in body.points),
                closed=body.closed,
                confidence=body.confidence,
                evidence=body.evidence,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except GeometrySuggestionError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "geometry_suggestion_invalid", "message": str(error)},
            ) from error
        return _geometry_suggestion(suggestion)

    @router.post(
        "/geometry-suggestions/{suggestion_id}/{action}",
        response_model=GeometrySuggestionResponse,
    )
    def review_geometry_suggestion(
        suggestion_id: str,
        action: Literal["review", "reject"],
        identity: Annotated[AuthenticatedSession, author],
    ) -> GeometrySuggestionResponse:
        try:
            suggestion = services.geometry_suggestions.review(
                suggestion_id,
                state="reviewed" if action == "review" else "rejected",
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except GeometrySuggestionError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "geometry_suggestion_not_pending"},
            ) from error
        return _geometry_suggestion(suggestion)

    @router.get(
        "/installation-geometry/{geometry_id}/projector-mapping",
        response_model=ProjectorMappingResponse,
        dependencies=[reader],
    )
    def projector_mapping(geometry_id: str) -> ProjectorMappingResponse:
        try:
            return _projector_mapping(services.projector_mapping.get(geometry_id))
        except ProjectorMappingError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "projector_mapping_not_found"},
            ) from error

    @router.get(
        "/installation-geometry/{geometry_id}/projector-simulation-overlay",
        dependencies=[reader],
    )
    def projector_simulation_overlay(
        geometry_id: str,
        photo_id: str,
    ) -> Response:
        try:
            mapping = services.projector_mapping.get(geometry_id)
            if mapping.content_asset_id is None:
                raise ProjectorSimulationError("projector mapping has no managed review content")
            photo_view = next(
                (view for view in mapping.photo_views if view.photo_id == photo_id),
                None,
            )
            if photo_view is None:
                raise ProjectorSimulationError(
                    "projector surface is not mapped into the selected photo"
                )
            artifact_path, _ = services.asset_upload.get_artifact(
                mapping.content_asset_id,
                "review",
            )
            with Image.open(artifact_path) as source:
                overlay = warp_review_proxy(
                    source,
                    output_width=1000,
                    output_height=600,
                    native_width=mapping.native_width,
                    native_height=mapping.native_height,
                    homography=photo_view.homography,
                    mesh_vertices=mapping.mesh_vertices,
                    mesh_triangles=mapping.mesh_triangles,
                    destination_points=photo_view.points,
                    orientation_degrees=mapping.orientation_degrees,
                    mirror_x=mapping.mirror_x,
                    mirror_y=mapping.mirror_y,
                )
        except ProjectorMappingError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "projector_mapping_not_found"},
            ) from error
        except (AssetUploadError, OSError, ProjectorSimulationError) as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "projector_simulation_overlay_invalid",
                    "message": str(error),
                },
            ) from error
        output = BytesIO()
        overlay.save(output, format="PNG")
        return Response(
            content=output.getvalue(),
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Cueverge-Approximation": (
                    "false" if mapping.calibration_source == "measured_grid" else "true"
                ),
                "X-Cueverge-Calibration-Source": mapping.calibration_source,
                "X-Cueverge-Calibration-Revision": str(mapping.calibration_revision),
            },
        )

    @router.put(
        "/installation-geometry/{geometry_id}/projector-mapping",
        response_model=ProjectorMappingResponse,
    )
    def update_projector_mapping(
        geometry_id: str,
        body: ProjectorMappingRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, author],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectorMappingResponse | JSONResponse:
        expected_revision = _parse_optional_if_match(if_match)
        try:
            mapping = services.projector_mapping.upsert(
                geometry_id=geometry_id,
                expected_revision=expected_revision,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
                **body.model_dump(),
            )
        except ProjectorMappingConflictError as error:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "projector_mapping_conflict",
                    "requested_revision": expected_revision,
                    "current_revision": error.current_revision,
                },
            )
        except ProjectorMappingError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "projector_mapping_invalid", "message": str(error)},
            ) from error
        response.headers["ETag"] = _etag(mapping.revision)
        return _projector_mapping(mapping)

    @router.get(
        "/installations/{installation_id}/projector-overlaps",
        response_model=list[ProjectorOverlapResponse],
        dependencies=[reader],
    )
    def projector_overlaps(
        installation_id: str,
    ) -> list[ProjectorOverlapResponse]:
        return [
            _projector_overlap(overlap)
            for overlap in services.projector_overlap.list(installation_id)
        ]

    @router.get(
        "/installations/{installation_id}/stage-coverage",
        response_model=StageCoverageResponse,
        dependencies=[reader],
    )
    def stage_coverage(installation_id: str) -> StageCoverageResponse:
        coverage = services.stage_coverage.analyze(installation_id)
        return StageCoverageResponse(
            installation_id=coverage.installation_id,
            minimum_x=coverage.minimum_x,
            minimum_y=coverage.minimum_y,
            maximum_x=coverage.maximum_x,
            maximum_y=coverage.maximum_y,
            regions=tuple(
                StageRegionResponse(
                    kind=region.kind,
                    points=region.points,
                    resources=region.resources,
                    source_ids=region.source_ids,
                )
                for region in coverage.regions
            ),
        )

    @router.get(
        "/installations/{installation_id}/mapper-export",
        response_model=InstallationMapperExportResponse,
        dependencies=[reader],
    )
    def mapper_export(installation_id: str) -> InstallationMapperExportResponse:
        runtime_installation = services.repository.get_runtime_installation()
        if installation_id != runtime_installation.id:
            raise HTTPException(
                status_code=409,
                detail={"code": "installation_not_runtime_backed"},
            )
        try:
            document = services.mapper_export.export(installation_id)
        except MapperExportError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "mapper_export_invalid",
                    "diagnostics": [
                        {
                            "code": diagnostic.code,
                            "message": diagnostic.message,
                            "object_ids": diagnostic.object_ids,
                        }
                        for diagnostic in error.diagnostics
                    ],
                },
            ) from error
        resources = _installation_resources(services)
        for section in ("projectors", "lighting_surfaces"):
            values = document.get(section)
            if not isinstance(values, list):
                raise RuntimeError(f"mapper export {section} has invalid shape")
            for value in values:
                if not isinstance(value, dict) or value.get("resource") not in resources:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "mapper_export_unknown_resource",
                            "section": section,
                        },
                    )
        return InstallationMapperExportResponse.model_validate(
            {
                **document,
                "resources": list(resources.values()),
            }
        )

    @router.get(
        "/installations/{installation_id}/mapper-validation",
        response_model=list[MapperDiagnosticResponse],
        dependencies=[reader],
    )
    def mapper_validation(
        installation_id: str,
    ) -> list[MapperDiagnosticResponse]:
        return [
            MapperDiagnosticResponse(
                code=diagnostic.code,
                message=diagnostic.message,
                object_ids=diagnostic.object_ids,
            )
            for diagnostic in services.mapper_export.validate(installation_id)
        ]

    @router.put(
        "/installations/{installation_id}/projector-overlaps",
        response_model=ProjectorOverlapResponse,
    )
    def update_projector_overlap(
        installation_id: str,
        body: ProjectorOverlapRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, author],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ProjectorOverlapResponse:
        try:
            overlap = services.projector_overlap.upsert(
                overlap_id=body.id,
                expected_revision=_parse_optional_if_match(if_match),
                installation_id=installation_id,
                first_geometry_id=body.first_geometry_id,
                second_geometry_id=body.second_geometry_id,
                stage_points=body.stage_points,
                ramp_width=body.ramp_width,
                overlap_gamma=body.overlap_gamma,
                first_brightness=body.first_brightness,
                second_brightness=body.second_brightness,
                first_white=body.first_white,
                second_white=body.second_white,
                first_color_matrix=body.first_color_matrix,
                second_color_matrix=body.second_color_matrix,
                first_black_level=body.first_black_level,
                second_black_level=body.second_black_level,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except ProjectorOverlapError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "projector_overlap_invalid", "message": str(error)},
            ) from error
        response.headers["ETag"] = _etag(overlap.revision)
        return _projector_overlap(overlap)

    @router.get(
        "/installation-geometry/{geometry_id}/lighting-surface",
        response_model=LightingSurfaceResponse,
        dependencies=[reader],
    )
    def lighting_surface(geometry_id: str) -> LightingSurfaceResponse:
        try:
            return _lighting_surface(services.lighting_surface.get(geometry_id))
        except LightingSurfaceError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "lighting_surface_not_found"},
            ) from error

    @router.put(
        "/installation-geometry/{geometry_id}/lighting-surface",
        response_model=LightingSurfaceResponse,
    )
    def update_lighting_surface(
        geometry_id: str,
        body: LightingSurfaceRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, author],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> LightingSurfaceResponse:
        try:
            values = body.model_dump(exclude={"pixel_overrides"})
            surface = services.lighting_surface.upsert(
                geometry_id=geometry_id,
                expected_revision=_parse_optional_if_match(if_match),
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
                pixel_overrides=tuple(
                    StoredPixelPositionOverride(
                        output_index=override.output_index,
                        local_x=override.local_x,
                        local_y=override.local_y,
                    )
                    for override in body.pixel_overrides
                ),
                **values,
            )
        except LightingSurfaceError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "lighting_surface_invalid", "message": str(error)},
            ) from error
        response.headers["ETag"] = _etag(surface.revision)
        return _lighting_surface(surface)

    @router.delete(
        "/installations/{installation_id}",
        status_code=204,
        response_class=Response,
        response_model=None,
    )
    def delete_installation(
        installation_id: str,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> Response | JSONResponse:
        expected_revision = _parse_if_match(if_match)
        try:
            services.repository.delete_installation(
                installation_id=installation_id,
                expected_revision=expected_revision,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except InstallationNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "installation_not_found"},
            ) from error
        except ActiveInstallationDeleteError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "active_installation_cannot_be_deleted"},
            ) from error
        except InstallationConflictError as error:
            return JSONResponse(
                status_code=409,
                content=InstallationConflictResponse(
                    requested_revision=expected_revision,
                    current_revision=error.current_revision,
                ).model_dump(mode="json"),
            )
        return Response(status_code=204)

    @router.get(
        "/installation",
        response_model=InstallationStatusResponse,
        dependencies=[reader],
    )
    def installation() -> InstallationStatusResponse:
        resources = _installation_resources(services)
        installation = services.repository.get_runtime_installation()
        return InstallationStatusResponse(
            installation_id=installation.id,
            name=installation.name,
            revision=installation.revision,
            runtime_active=True,
            resources=tuple(resources[key] for key in sorted(resources)),
        )

    @router.get("/input-source", dependencies=[reader])
    def input_source() -> dict[str, object]:
        return services.input_source.snapshot()

    @router.put("/input-source")
    async def configure_input_source(
        body: dict[str, JsonValue],
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> dict[str, object]:
        try:
            request = ConfigureInputSourceRequest.model_validate(body)
        except ValidationError:
            raise HTTPException(
                422,
                detail={
                    "code": "invalid_input_settings",
                    "message": (
                        "Check the broker hostname, port, topic, labels and credential action."
                    ),
                },
            ) from None
        try:
            result = await services.input_source.configure(request)
        except InputSourceError as error:
            raise HTTPException(
                409, detail={"code": "input_configuration_failed", "message": str(error)}
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="input.configure",
            target="frigate_mqtt",
            result="success",
            occurred_at=_now_iso(),
            authorization_context={"management": "gui", "enabled": request.enabled},
        )
        return result

    @router.post("/input-source/validate")
    def validate_input_source(
        body: dict[str, JsonValue],
        identity: Annotated[AuthenticatedSession, Depends(mutation_session)],
    ) -> dict[str, object]:
        try:
            return services.input_source.validate_payload(body)
        except InputSourceError as error:
            raise HTTPException(
                422, detail={"code": "invalid_input_payload", "message": str(error)}
            ) from error

    @router.get(
        "/runtime/status",
        response_model=RuntimeStatusResponse,
        dependencies=[reader],
    )
    async def runtime_status() -> RuntimeStatusResponse:
        return services.runtime_snapshot()

    @router.get("/runtime/output-monitor", dependencies=[reader])
    async def output_monitor(
        resource: str | None = None,
        pixel_offset: int = Query(default=0, ge=0),
        pixel_limit: int = Query(default=1000, ge=1, le=1000),
    ) -> dict[str, object]:
        try:
            return services.runtime.output_monitor(resource, pixel_offset, pixel_limit)
        except ValueError as error:
            raise HTTPException(
                422, detail={"code": "invalid_monitor_request", "message": str(error)}
            ) from error

    @router.post(
        "/maintenance/output-arms",
        response_model=OutputArmResponse,
        status_code=201,
    )
    def create_output_arm(
        body: CreateOutputArmRequest,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> OutputArmResponse:
        status = services.runtime.status()
        operations = status.get("operations")
        mode = operations.get("mode") if isinstance(operations, dict) else None
        if mode != ShowMode.MAINTENANCE.value or status.get("active_scene") is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "output_arm_requires_idle_maintenance"},
            )
        running_test = services.runtime.output_test_status()
        if running_test is not None and running_test.state == "active":
            raise HTTPException(status_code=409, detail={"code": "output_test_already_active"})
        resource = _installation_resources(services).get(body.resource)
        if resource is None or resource.kind == "downstream_controller":
            raise HTTPException(
                status_code=422,
                detail={"code": "output_arm_resource_not_supported"},
            )
        assignment = next(
            (
                item
                for item in services.repository.list_resource_assignments()
                if item.resource == body.resource
            ),
            None,
        )
        if assignment is not None:
            device = services.repository.get_device(assignment.device_id)
            device_identity = f"{device.display_name} ({device.id})"
        else:
            device_identity = resource.target or f"controller-owned {body.resource}"
        try:
            arm = services.output_arming.create(
                resource=body.resource,
                resource_kind=resource.kind,
                device_identity=device_identity,
                stop_action=(
                    "stop media playback"
                    if resource.kind == "media_agent"
                    else "stop realtime pixel output"
                ),
                confirmation=body.confirmation,
                duration_seconds=body.duration_seconds,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except OutputArmError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "output_arm_invalid", "message": str(error)},
            ) from error
        return _output_arm(arm)

    @router.post(
        "/maintenance/output-arms/{arm_id}/stop",
        response_model=OutputArmResponse,
    )
    async def stop_output_arm(
        arm_id: str,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> OutputArmResponse:
        test = services.runtime.output_test_status()
        if test is not None and test.arm_id == arm_id and test.state == "active":
            await services.runtime.stop_output_test(reason="disarmed")
            response = _output_arm(services.output_arming.get(arm_id, _now_iso()))
            response.hardware_command_sent = True
            return response
        try:
            arm = services.output_arming.stop(
                arm_id,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except OutputArmError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "output_arm_not_active"},
            ) from error
        return _output_arm(arm)

    @router.get(
        "/maintenance/output-tests",
        response_model=OutputTestOverviewResponse,
        dependencies=[reader],
    )
    def output_test_overview() -> OutputTestOverviewResponse:
        status = services.runtime.status()
        operations = status.get("operations")
        mode = operations.get("mode") if isinstance(operations, dict) else None
        targets = {target.resource: target for target in services.runtime.lighting_targets()}
        test = services.runtime.output_test_status()
        return OutputTestOverviewResponse(
            mode=str(mode),
            playback_active=status.get("active_scene") is not None,
            outputs=tuple(
                resource
                for resource in _installation_resources(services).values()
                if resource.kind == "pixel_output"
                and resource.resource in targets
                and resource.pixel_count is not None
                and targets[resource.resource].expected_pixel_count == resource.pixel_count
            ),
            test=_output_test_response(test) if test is not None else None,
        )

    @router.post(
        "/maintenance/output-tests",
        response_model=OutputTestResponse,
        status_code=201,
    )
    async def start_output_test(
        body: StartOutputTestRequest,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> OutputTestResponse:
        resource = _installation_resources(services).get(body.resource)
        if resource is None or resource.kind != "pixel_output" or resource.pixel_count is None:
            raise HTTPException(status_code=422, detail={"code": "output_test_resource_invalid"})
        installation = services.repository.get_runtime_installation()
        if body.installation_id is not None and body.installation_id != installation.id:
            raise HTTPException(
                status_code=422, detail={"code": "output_test_requires_runtime_installation"}
            )
        overview = output_test_overview()
        if overview.mode != "maintenance" or overview.playback_active:
            raise HTTPException(
                status_code=409, detail={"code": "output_test_requires_idle_maintenance"}
            )
        if overview.test is not None and overview.test.state == "active":
            raise HTTPException(status_code=409, detail={"code": "output_test_already_active"})
        if body.resource not in {output.resource for output in overview.outputs}:
            raise HTTPException(
                status_code=409,
                detail={"code": "output_test_driver_missing_or_pixel_count_mismatch"},
            )
        try:
            segments = (
                mapped_test_segments(
                    services.installation_geometry,
                    services.lighting_surface,
                    installation.id,
                    body.resource,
                    resource.pixel_count,
                )
                if body.use_mapping
                else ()
            )
        except (OutputTestError, LightingSurfaceError) as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "output_test_mapping_invalid", "message": str(error)},
            ) from error
        try:
            arm = services.output_arming.create(
                resource=body.resource,
                resource_kind="pixel_output",
                device_identity=resource.target or f"controller-owned {body.resource}",
                stop_action="send black frame and release realtime output",
                confirmation=body.confirmation,
                duration_seconds=body.duration_seconds,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except OutputArmError as error:
            raise HTTPException(
                status_code=422, detail={"code": "output_arm_invalid", "message": str(error)}
            ) from error

        def finished(test: OutputTestStatus) -> None:
            stored = services.output_arming.get(test.arm_id, _now_iso())
            if stored.state == "active":
                services.output_arming.stop(
                    test.arm_id,
                    actor=identity.username,
                    role=identity.role,
                    occurred_at=_now_iso(),
                )
            services.repository.append_audit(
                actor=identity.username,
                action=f"maintenance.output_test.{test.state}",
                target=test.resource,
                result="failed" if test.state == "failed" else "success",
                occurred_at=_now_iso(),
                authorization_context={
                    "test_id": test.id,
                    "arm_id": test.arm_id,
                    "reason": test.stop_reason,
                    "error": test.error,
                    "frames_sent": test.frames_sent,
                    "simulation_only": False,
                },
            )

        try:
            remaining = (datetime.fromisoformat(arm.expires_at) - datetime.now(UTC)).total_seconds()
            test = services.runtime.start_output_test(
                resource=body.resource,
                pixel_count=resource.pixel_count,
                speed=body.speed,
                duration_seconds=remaining,
                arm_id=arm.id,
                segments=segments,
                on_finish=finished,
            )
        except OutputTestError as error:
            if services.output_arming.get(arm.id, _now_iso()).state == "active":
                services.output_arming.stop(
                    arm.id,
                    actor=identity.username,
                    role=identity.role,
                    occurred_at=_now_iso(),
                )
            raise HTTPException(
                status_code=409,
                detail={"code": "output_test_rejected", "message": str(error)},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="maintenance.output_test.started",
            target=test.resource,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={
                "test_id": test.id,
                "arm_id": arm.id,
                "speed": test.speed,
                "duration_seconds": body.duration_seconds,
                "simulation_only": False,
                "use_mapping": body.use_mapping,
            },
        )
        return _output_test_response(test)

    @router.post(
        "/maintenance/output-tests/{test_id}/stop",
        response_model=OutputTestResponse,
    )
    async def stop_output_test(
        test_id: str,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> OutputTestResponse:
        test = services.runtime.output_test_status()
        if test is None or test.id != test_id:
            raise HTTPException(status_code=404, detail={"code": "output_test_not_found"})
        await services.runtime.stop_output_test()
        services.repository.append_audit(
            actor=identity.username,
            action="maintenance.output_test.stop_requested",
            target=test.resource,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={"test_id": test.id},
        )
        return _output_test_response(test)

    @router.post(
        "/maintenance/test-pattern-preview",
        response_model=MaintenancePreviewResponse,
    )
    def maintenance_test_pattern_preview(
        body: MaintenancePreviewRequest,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> MaintenancePreviewResponse:
        status = services.runtime.status()
        operations = status.get("operations")
        mode = operations.get("mode") if isinstance(operations, dict) else None
        if mode != ShowMode.MAINTENANCE.value:
            raise HTTPException(
                status_code=409,
                detail={"code": "maintenance_mode_required"},
            )
        if status.get("active_scene") is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "maintenance_preview_requires_idle_runtime"},
            )
        resource = _installation_resources(services).get(body.resource)
        if resource is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "installation_resource_not_found"},
            )
        if resource.kind == "downstream_controller":
            raise HTTPException(
                status_code=422,
                detail={"code": "maintenance_preview_not_supported"},
            )
        pattern: Literal["alignment_grid", "pixel_index_chase"] = (
            "alignment_grid" if resource.kind == "media_agent" else "pixel_index_chase"
        )
        started_at = datetime.now(UTC)
        expires_at = started_at + timedelta(seconds=body.duration_seconds)
        services.repository.append_audit(
            actor=identity.username,
            action="maintenance.preview",
            target=body.resource,
            result="success",
            occurred_at=started_at.isoformat(),
            authorization_context={
                "role": identity.role.value,
                "pattern": pattern,
                "duration_seconds": body.duration_seconds,
                "simulation_only": True,
            },
        )
        return MaintenancePreviewResponse(
            resource=body.resource,
            resource_kind=resource.kind,
            pattern=pattern,
            started_at=started_at.isoformat(),
            expires_at=expires_at.isoformat(),
        )

    @router.post(
        "/timing-calibrations",
        response_model=TimingCalibrationSessionResponse,
        status_code=201,
    )
    def start_timing_calibration(
        body: StartTimingCalibrationRequest,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> TimingCalibrationSessionResponse:
        status = services.runtime.status()
        operations = status.get("operations")
        mode = operations.get("mode") if isinstance(operations, dict) else None
        if mode != ShowMode.MAINTENANCE.value or status.get("active_scene") is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "timing_calibration_requires_idle_maintenance"},
            )
        try:
            session = services.timing_calibration.start(
                target_kind=body.target_kind,
                geometry_id=body.geometry_id,
                adjustment_kind=body.adjustment_kind,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
                test_asset_id=body.test_asset_id,
                test_asset_version=body.test_asset_version,
                observed_variability_ms=body.observed_variability_ms,
                notes=body.notes,
                context=body.context,
            )
        except TimingCalibrationError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "timing_calibration_invalid", "message": str(error)},
            ) from error
        return _timing_calibration(session)

    @router.post(
        "/accepted-calibrations/review",
        response_model=list[AcceptedCalibrationResponse],
        dependencies=[reader],
    )
    def accepted_calibrations(
        body: ReviewCalibrationContextRequest,
    ) -> list[AcceptedCalibrationResponse]:
        return [
            _accepted_calibration(calibration)
            for calibration in services.timing_calibration.accepted(
                body.target_kind,
                body.geometry_id,
                body.context,
            )
        ]

    @router.put(
        "/timing-calibrations/{session_id}",
        response_model=TimingCalibrationSessionResponse,
    )
    def update_timing_calibration(
        session_id: str,
        body: UpdateTimingCalibrationRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, operator_mutation],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TimingCalibrationSessionResponse:
        try:
            session = services.timing_calibration.update(
                session_id,
                expected_revision=_parse_if_match(if_match),
                proposed_ms=body.proposed_ms,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except TimingCalibrationError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "timing_calibration_conflict", "message": str(error)},
            ) from error
        response.headers["ETag"] = _etag(session.revision)
        return _timing_calibration(session)

    @router.post(
        "/timing-calibrations/{session_id}/{action}",
        response_model=TimingCalibrationSessionResponse,
    )
    def finish_timing_calibration(
        session_id: str,
        action: Literal["accept", "cancel"],
        response: Response,
        identity: Annotated[AuthenticatedSession, operator_mutation],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TimingCalibrationSessionResponse:
        try:
            session = services.timing_calibration.finish(
                session_id,
                expected_revision=_parse_if_match(if_match),
                accept=action == "accept",
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except TimingCalibrationError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "timing_calibration_conflict", "message": str(error)},
            ) from error
        response.headers["ETag"] = _etag(session.revision)
        return _timing_calibration(session)

    @router.post("/operate/actions", response_model=OperateActionResponse)
    async def operate(
        body: OperateActionRequest,
        identity: Annotated[AuthenticatedSession, live_output_mutation],
    ) -> OperateActionResponse:
        occurred_at = _now_iso()
        try:
            await services.operate(body)
        except (RuntimeError, LookupError) as error:
            services.repository.append_audit(
                actor=identity.username,
                action=f"operate.{body.action}",
                target="controller",
                result="error",
                occurred_at=occurred_at,
                authorization_context={
                    "role": identity.role.value,
                    "message": str(error),
                },
            )
            raise HTTPException(
                status_code=409,
                detail={"code": "operation_rejected", "message": str(error)},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action=f"operate.{body.action}",
            target="controller",
            result="success",
            occurred_at=occurred_at,
            authorization_context={"role": identity.role.value},
        )
        status = services.runtime.status()
        operations = status.get("operations")
        if not isinstance(operations, dict) or not isinstance(operations.get("mode"), str):
            raise RuntimeError("controller status did not include an operation mode")
        active_scene = status.get("active_scene")
        if active_scene is not None and not isinstance(active_scene, str):
            raise RuntimeError("controller status included an invalid active scene")
        return OperateActionResponse(
            action=body.action,
            mode=operations["mode"],
            active_scene=active_scene,
        )

    @router.post(
        "/devices/pairing-tokens",
        response_model=PairingTokenResponse,
        status_code=201,
    )
    def create_pairing_token(
        body: CreatePairingTokenRequest,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> PairingTokenResponse:
        if services.device_enrollment is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "device_enrollment_disabled"},
            )
        try:
            return services.device_enrollment.create_pairing_token(
                device_kind=body.device_kind,
                actor=identity.username,
                role=identity.role,
                discovered_endpoint_id=body.discovered_endpoint_id,
            )
        except DiscoveredEndpointNotUsableError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "discovered_endpoint_not_enrollable"},
            ) from error

    @router.post(
        "/devices/capabilities/review",
        response_model=CapabilityReviewResponse,
    )
    def review_device_capabilities(
        body: CapabilityReviewRequest,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> CapabilityReviewResponse:
        issues: list[CapabilityReviewIssue] = []
        kinds = tuple(str(capability.get("kind", "unknown")) for capability in body.capabilities)
        if body.schema_version != 1:
            issues.append(
                CapabilityReviewIssue(
                    code="unsupported_schema",
                    message=f"Schema version {body.schema_version} is not supported",
                )
            )
        if not (body.protocol.minimum <= DEVICE_PROTOCOL_VERSION <= body.protocol.maximum):
            issues.append(
                CapabilityReviewIssue(
                    code="protocol_mismatch",
                    message=(
                        f"Candidate supports protocol v{body.protocol.minimum}-"
                        f"v{body.protocol.maximum}; controller requires "
                        f"v{DEVICE_PROTOCOL_VERSION}"
                    ),
                )
            )
        if len(kinds) != len(set(kinds)):
            issues.append(
                CapabilityReviewIssue(
                    code="duplicate_capability",
                    message="Capability kinds must be unique",
                )
            )
        if body.device_kind is DeviceKind.DIRECT_MEDIA_AGENT:
            if "media_playback" not in kinds:
                issues.append(
                    CapabilityReviewIssue(
                        code="missing_media_playback",
                        message="Direct media agents must declare media_playback",
                    )
                )
            if "downstream_control" in kinds:
                issues.append(
                    CapabilityReviewIssue(
                        code="direct_agent_claims_downstream_control",
                        message="Direct media agents cannot claim downstream_control",
                    )
                )
        elif "downstream_control" not in kinds:
            issues.append(
                CapabilityReviewIssue(
                    code="missing_downstream_control",
                    message="Downstream controllers must declare downstream_control",
                )
            )
        services.repository.append_audit(
            actor=identity.username,
            action="device.capabilities.review",
            target=body.device_kind.value,
            result="compatible" if not issues else "incompatible",
            occurred_at=_now_iso(),
            authorization_context={
                "role": identity.role.value,
                "schema_version": body.schema_version,
                "protocol_minimum": body.protocol.minimum,
                "protocol_maximum": body.protocol.maximum,
                "capability_kinds": list(kinds),
                "issue_codes": [issue.code for issue in issues],
            },
        )
        return CapabilityReviewResponse(
            compatible=not issues,
            controller_protocol_version=DEVICE_PROTOCOL_VERSION,
            capability_kinds=kinds,
            issues=tuple(issues),
        )

    @router.get(
        "/discovery/endpoints",
        response_model=list[DiscoveredEndpointResponse],
        dependencies=[reader],
    )
    def discovered_endpoints() -> list[DiscoveredEndpointResponse]:
        return [
            _discovered_endpoint(endpoint)
            for endpoint in services.repository.list_discovered_endpoints()
        ]

    @router.post(
        "/discovery/probe",
        response_model=DiscoveredEndpointResponse,
    )
    async def probe_endpoint(
        body: ProbeEndpointRequest,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> DiscoveredEndpointResponse:
        try:
            discovered = await asyncio.to_thread(
                services.endpoint_discovery.probe,
                body.address,
            )
        except EndpointDiscoveryError as error:
            services.repository.append_audit(
                actor=identity.username,
                action="device.discovery.probe",
                target=body.address,
                result="error",
                occurred_at=_now_iso(),
                authorization_context={
                    "role": identity.role.value,
                    "message": str(error),
                },
            )
            raise HTTPException(
                status_code=422,
                detail={"code": "endpoint_discovery_failed", "message": str(error)},
            ) from error
        compatible = (
            discovered.document.protocol.minimum
            <= DEVICE_PROTOCOL_VERSION
            <= discovered.document.protocol.maximum
            and "downstream_control" in discovered.document.capabilities
        )
        stored = services.repository.upsert_discovered_endpoint(
            base_url=discovered.base_url,
            device_kind=discovered.document.device_kind,
            software_version=discovered.document.software_version,
            protocol_minimum=discovered.document.protocol.minimum,
            protocol_maximum=discovered.document.protocol.maximum,
            capability_kinds=tuple(discovered.document.capabilities),
            compatible=compatible,
            actor=identity.username,
            role=identity.role,
            occurred_at=_now_iso(),
        )
        return _discovered_endpoint(stored)

    @router.post(
        "/discovery/endpoints/{endpoint_id}/reject",
        response_model=DiscoveredEndpointResponse,
    )
    def reject_discovered_endpoint(
        endpoint_id: str,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> DiscoveredEndpointResponse:
        try:
            endpoint = services.repository.reject_discovered_endpoint(
                endpoint_id=endpoint_id,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
            )
        except DeviceNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "discovered_endpoint_not_found"},
            ) from error
        return _discovered_endpoint(endpoint)

    @router.put(
        "/devices/{device_id}/connection-policy",
        response_model=DeviceConnectionPolicyResponse,
    )
    def set_device_connection_policy(
        device_id: str,
        body: SetDeviceConnectionPolicyRequest,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> DeviceConnectionPolicyResponse:
        occurred_at = _now_iso()
        try:
            services.repository.set_device_connection_policy(
                device_id=device_id,
                policy=body.policy,
                actor=identity.username,
                occurred_at=occurred_at,
            )
        except DeviceNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "device_not_found"},
            ) from error
        return DeviceConnectionPolicyResponse(
            device_id=device_id,
            policy=body.policy,
            updated_at=occurred_at,
        )

    @router.put(
        "/devices/{device_id}/enrollment-state",
        response_model=DeviceEnrollmentStateResponse,
    )
    def set_device_enrollment_state(
        device_id: str,
        body: SetDeviceEnrollmentStateRequest,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> DeviceEnrollmentStateResponse:
        occurred_at = _now_iso()
        try:
            device = services.repository.set_device_enrollment_state(
                device_id=device_id,
                state=EnrollmentState(body.state),
                actor=identity.username,
                role=identity.role,
                occurred_at=occurred_at,
            )
        except DeviceNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "device_not_found"},
            ) from error
        except DeviceStateTransitionError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "device_state_transition_rejected"},
            ) from error
        return DeviceEnrollmentStateResponse(
            device_id=device.id,
            state=_managed_enrollment_state(device.enrollment_state),
            updated_at=device.updated_at,
        )

    @router.post(
        "/devices/{device_id}/connection-test",
        response_model=DeviceConnectionTestResponse,
    )
    def test_device_connection(
        device_id: str,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> DeviceConnectionTestResponse:
        try:
            device = services.repository.get_device(device_id)
        except DeviceNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "device_not_found"},
            ) from error
        presence = next(
            (
                item
                for item in services.repository.list_device_presence()
                if item.device_id == device_id
            ),
            None,
        )
        assignments = tuple(
            assignment.resource
            for assignment in services.repository.list_resource_assignments()
            if assignment.device_id == device_id
        )
        observed_at_ms = time.time_ns() // 1_000_000
        heartbeat_fresh = (
            presence is not None
            and presence.disconnected_at_ms is None
            and observed_at_ms - presence.last_seen_at_ms
            <= services.device_sessions.heartbeat_timeout_ms
        )
        timing_available = (
            presence is not None
            and presence.latency_ms is not None
            and presence.clock_offset_ms is not None
        )
        checks = (
            ConnectionTestCheck(
                name="enrollment",
                passed=device.enrollment_state is EnrollmentState.ENROLLED,
                detail=(
                    "Certificate identity is enrolled"
                    if device.enrollment_state is EnrollmentState.ENROLLED
                    else f"Device state is {device.enrollment_state.value}"
                ),
            ),
            ConnectionTestCheck(
                name="heartbeat",
                passed=heartbeat_fresh,
                detail=(
                    f"Heartbeat observed {observed_at_ms - presence.last_seen_at_ms} ms ago"
                    if heartbeat_fresh and presence is not None
                    else "No fresh authenticated heartbeat"
                ),
            ),
            ConnectionTestCheck(
                name="timing",
                passed=timing_available,
                detail=(
                    f"{presence.latency_ms:.1f} ms round trip; "
                    f"{presence.clock_offset_ms:+.1f} ms clock offset"
                    if timing_available and presence is not None
                    else "No timing report from the active session"
                ),
            ),
            ConnectionTestCheck(
                name="assignment",
                passed=bool(assignments),
                detail=(
                    f"Assigned to {', '.join(assignments)}"
                    if assignments
                    else "No logical resource assignment"
                ),
            ),
        )
        passed = all(check.passed for check in checks)
        occurred_at = _now_iso()
        services.repository.append_audit(
            actor=identity.username,
            action="device.connection_test",
            target=device_id,
            result="success" if passed else "failed",
            occurred_at=occurred_at,
            authorization_context={
                "role": identity.role.value,
                "checks": {check.name: check.passed for check in checks},
            },
        )
        return DeviceConnectionTestResponse(
            device_id=device_id,
            observed_at=occurred_at,
            result="passed" if passed else "failed",
            checks=checks,
            latency_ms=presence.latency_ms if presence is not None else None,
            clock_offset_ms=presence.clock_offset_ms if presence is not None else None,
        )

    @router.get(
        "/devices/{device_id}/artifacts",
        response_model=list[DeviceArtifactResponse],
        dependencies=[reader],
    )
    def device_artifacts(device_id: str) -> list[DeviceArtifactResponse]:
        try:
            artifacts = services.repository.list_device_artifacts(device_id)
        except DeviceNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "device_not_found"},
            ) from error
        return [
            DeviceArtifactResponse(
                digest_sha256=artifact.digest_sha256,
                size_bytes=artifact.size_bytes,
                pinned=artifact.pinned,
                in_use=artifact.in_use,
                state=artifact.state,
                last_used_unix_ms=artifact.last_used_unix_ms,
            )
            for artifact in artifacts
        ]

    @router.post(
        "/devices/{device_id}/artifacts/cleanup",
        response_model=CleanupDeviceArtifactsResponse,
    )
    async def cleanup_device_artifacts(
        device_id: str,
        body: CleanupDeviceArtifactsRequest,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> CleanupDeviceArtifactsResponse:
        occurred_at = _now_iso()
        try:
            acknowledgment = await services.artifact_deployment.cleanup(
                UUID(device_id),
                body.digests,
            )
        except (ValueError, DeviceSessionError) as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "artifact_cleanup_failed", "message": str(error)},
            ) from error
        services.repository.append_audit(
            actor=identity.username,
            action="device.artifact.cleanup",
            target=device_id,
            result="success",
            occurred_at=occurred_at,
            authorization_context={
                "role": identity.role.value,
                "removed_digests": list(acknowledgment.removed_digests),
                "rejected_digests": list(acknowledgment.rejected_digests),
            },
        )
        return CleanupDeviceArtifactsResponse(
            removed_digests=acknowledgment.removed_digests,
            rejected_digests=acknowledgment.rejected_digests,
        )

    @router.get(
        "/devices/{device_id}/transfers",
        response_model=list[DeviceTransferStatusResponse],
        dependencies=[reader],
    )
    def device_transfers(device_id: str) -> list[DeviceTransferStatusResponse]:
        try:
            status = next(
                (
                    item
                    for item in services.repository.list_device_artifact_statuses()
                    if item.device_id == device_id
                ),
                None,
            )
            services.repository.get_device(device_id)
        except DeviceNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "device_not_found"},
            ) from error
        if status is None:
            return []
        return [
            DeviceTransferStatusResponse.model_validate(transfer) for transfer in status.transfers
        ]

    @router.get(
        "/resource-assignments",
        response_model=list[ResourceAssignmentResponse],
        dependencies=[reader],
    )
    def resource_assignments() -> list[ResourceAssignmentResponse]:
        return [
            _resource_assignment(assignment)
            for assignment in services.repository.list_resource_assignments()
        ]

    @router.put(
        "/resource-assignments/{resource:path}",
        response_model=ResourceAssignmentResponse,
    )
    def set_resource_assignment(
        resource: str,
        body: SetResourceAssignmentRequest,
        response: Response,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
    ) -> ResourceAssignmentResponse | JSONResponse:
        if services.runtime.status().get("active_scene") is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "assignment_change_requires_idle_runtime"},
            )
        installation_resource = _installation_resources(services).get(resource)
        if installation_resource is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "installation_resource_not_found"},
            )
        allowed_capabilities = _allowed_assignment_capabilities(installation_resource.kind)
        if body.capability_kind not in allowed_capabilities:
            raise HTTPException(
                status_code=422,
                detail={"code": "resource_capability_incompatible"},
            )
        try:
            assignment = services.repository.set_resource_assignment(
                resource=resource,
                device_id=body.device_id,
                capability_kind=body.capability_kind,
                device_output=body.device_output,
                expected_revision=body.expected_revision,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
                disconnected_before_ms=(
                    time.time_ns() // 1_000_000 - services.device_sessions.heartbeat_timeout_ms
                ),
            )
        except DeviceNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "device_not_found"},
            ) from error
        except DeviceStateTransitionError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "device_not_assignable"},
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={"code": "device_capability_not_declared"},
            ) from error
        except ResourceAssignmentConflictError as error:
            return JSONResponse(
                status_code=409,
                content=ResourceAssignmentConflictResponse(
                    requested_revision=body.expected_revision,
                    current_revision=error.current_revision,
                ).model_dump(mode="json"),
            )
        except ResourceAssignmentInUseError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "assignment_device_connected"},
            ) from error
        response.headers["ETag"] = _etag(assignment.revision)
        return _resource_assignment(assignment)

    @router.delete(
        "/resource-assignments/{resource:path}",
        status_code=204,
        response_class=Response,
        response_model=None,
    )
    def remove_resource_assignment(
        resource: str,
        identity: Annotated[AuthenticatedSession, administrator_mutation],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> Response | JSONResponse:
        if services.runtime.status().get("active_scene") is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "assignment_change_requires_idle_runtime"},
            )
        expected_revision = _parse_if_match(if_match)
        try:
            services.repository.remove_resource_assignment(
                resource=resource,
                expected_revision=expected_revision,
                actor=identity.username,
                role=identity.role,
                occurred_at=_now_iso(),
                disconnected_before_ms=(
                    time.time_ns() // 1_000_000 - services.device_sessions.heartbeat_timeout_ms
                ),
            )
        except ResourceAssignmentConflictError as error:
            return JSONResponse(
                status_code=409,
                content=ResourceAssignmentConflictResponse(
                    requested_revision=expected_revision,
                    current_revision=error.current_revision,
                ).model_dump(mode="json"),
            )
        except ResourceAssignmentInUseError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "assignment_device_connected"},
            ) from error
        return Response(status_code=204)

    @router.get(
        "/topology",
        response_model=TopologyResponse,
        dependencies=[reader],
    )
    async def topology() -> TopologyResponse:
        pixel_resources: set[str] = set()
        for scene in services.runtime.catalog.scenes.values():
            for lighting_output in scene.lighting_outputs:
                pixel_resources.add(lighting_output.definition.resource)

        nodes = [
            TopologyNodeResponse(
                id="controller",
                label="Cueverge Controller",
                kind="controller",
                configured=True,
                connection="connected",
                enrollment="local_controller",
                compatibility="not_applicable",
            )
        ]
        durable_assignments: dict[str, list[StoredResourceAssignment]] = {}
        for assignment in services.repository.list_resource_assignments():
            durable_assignments.setdefault(assignment.device_id, []).append(assignment)
        presence_by_device = {
            presence.device_id: presence for presence in services.repository.list_device_presence()
        }
        artifact_status_by_device = {
            status.device_id: status
            for status in services.repository.list_device_artifact_statuses()
        }
        connection_policies = services.repository.list_device_connection_policies()
        observed_at_ms = time.time_ns() // 1_000_000
        nodes.extend(
            TopologyNodeResponse(
                id=f"device:{device.id}",
                label=device.display_name,
                kind=device.device_kind.value,
                parent_id="controller",
                configured=device.id in durable_assignments,
                connection=(
                    "connected"
                    if _device_is_connected(
                        device.enrollment_state,
                        presence_by_device.get(device.id),
                        observed_at_ms=observed_at_ms,
                        timeout_ms=services.device_sessions.heartbeat_timeout_ms,
                    )
                    else "disconnected"
                ),
                enrollment=_topology_enrollment(device.enrollment_state),
                compatibility=_device_compatibility(
                    device.enrollment_state,
                    durable_assignments.get(device.id, ()),
                    _installation_resources(services),
                ),
                compatibility_detail=_device_compatibility_detail(
                    device.enrollment_state,
                    device.capabilities.protocol.minimum,
                    device.capabilities.protocol.maximum,
                    durable_assignments.get(device.id, ()),
                    _installation_resources(services),
                ),
                protocol_version=2,
                capabilities=tuple(
                    capability.kind for capability in device.capabilities.capabilities
                ),
                assigned_resources=tuple(
                    sorted(
                        assignment.resource for assignment in durable_assignments.get(device.id, ())
                    )
                ),
                software_version=device.capabilities.software_version,
                capability_document=device.capabilities,
                certificate_expires_at=device.certificate_expires_at,
                storage_total_bytes=(
                    artifact_status_by_device[device.id].total_bytes
                    if device.id in artifact_status_by_device
                    else None
                ),
                storage_free_bytes=(
                    artifact_status_by_device[device.id].free_bytes
                    if device.id in artifact_status_by_device
                    else None
                ),
                artifact_count=(
                    artifact_status_by_device[device.id].artifact_count
                    if device.id in artifact_status_by_device
                    else None
                ),
                artifact_bytes=(
                    artifact_status_by_device[device.id].artifact_bytes
                    if device.id in artifact_status_by_device
                    else None
                ),
                pinned_artifact_count=(
                    artifact_status_by_device[device.id].pinned_count
                    if device.id in artifact_status_by_device
                    else None
                ),
                supports_pinning=(
                    artifact_status_by_device[device.id].supports_pinning
                    if device.id in artifact_status_by_device
                    else None
                ),
                supports_resumable_transfer=(
                    artifact_status_by_device[device.id].supports_resumable_transfer
                    if device.id in artifact_status_by_device
                    else None
                ),
                active_transfers=(
                    sum(
                        transfer.get("state") == "active"
                        for transfer in artifact_status_by_device[device.id].transfers
                    )
                    if device.id in artifact_status_by_device
                    else None
                ),
                failed_transfers=(
                    sum(
                        transfer.get("state") == "failed"
                        for transfer in artifact_status_by_device[device.id].transfers
                    )
                    if device.id in artifact_status_by_device
                    else None
                ),
                artifact_faults=(
                    artifact_status_by_device[device.id].faults
                    if device.id in artifact_status_by_device
                    else ()
                ),
                latency_ms=(
                    presence_by_device[device.id].latency_ms
                    if device.id in presence_by_device
                    else None
                ),
                clock_drift_ms=(
                    presence_by_device[device.id].clock_offset_ms
                    if device.id in presence_by_device
                    else None
                ),
                connection_policy=connection_policies.get(device.id, "continuous"),
                last_seen_age_ms=(
                    max(
                        0,
                        observed_at_ms - presence_by_device[device.id].last_seen_at_ms,
                    )
                    if device.id in presence_by_device
                    else None
                ),
            )
            for device in services.repository.list_devices()
        )
        targets = {target.resource: target for target in services.runtime.lighting_targets()}
        target_resources = sorted(pixel_resources & targets.keys())
        statuses = await asyncio.gather(
            *(services.wled_status.observe(targets[resource].host) for resource in target_resources)
        )
        status_by_resource = dict(zip(target_resources, statuses, strict=True))
        for resource in sorted(pixel_resources):
            target = targets.get(resource)
            status = status_by_resource.get(resource)
            expected_pixels = target.expected_pixel_count if target is not None else None
            compatible = (
                status is not None
                and status.connected
                and expected_pixels is not None
                and status.reported_pixel_count == expected_pixels
            )
            nodes.append(
                TopologyNodeResponse(
                    id=f"output:{resource}",
                    label=resource,
                    kind="controller_output",
                    parent_id="controller",
                    configured=target is not None,
                    connection=(
                        "connected"
                        if status is not None and status.connected
                        else "disconnected"
                        if status is not None
                        else "not_independently_detectable"
                    ),
                    enrollment="not_applicable",
                    compatibility=(
                        "compatible"
                        if compatible
                        else "conflict"
                        if status is not None and status.connected
                        else "unknown"
                    ),
                    assigned_resources=(resource,),
                    software_version=(status.software_version if status is not None else None),
                    latency_ms=status.latency_ms if status is not None else None,
                    reported_pixel_count=(
                        status.reported_pixel_count if status is not None else None
                    ),
                    signal_percent=status.signal_percent if status is not None else None,
                    output_on=status.output_on if status is not None else None,
                    diagnostic=status.diagnostic if status is not None else None,
                )
            )
        return TopologyResponse(
            observed_at=_now_iso(),
            enrollment_supported=services.device_enrollment is not None,
            nodes=tuple(nodes),
        )

    @router.get(
        "/activity",
        response_model=list[ActivityEventResponse],
        dependencies=[reader],
    )
    def activity() -> list[ActivityEventResponse]:
        device_names = {
            device.id: device.display_name for device in services.repository.list_devices()
        }
        return [
            ActivityEventResponse(
                id=event.id,
                occurred_at=event.occurred_at,
                category=_activity_category(event.action),
                action=event.action,
                actor=device_names.get(event.actor, event.actor),
                target=device_names.get(event.target, event.target),
                result=event.result,
            )
            for event in services.repository.list_audit_events(limit=50)
            if not (event.action == "auth.login" and event.result == "success")
        ]

    @router.get(
        "/audit",
        response_model=list[AuditEventResponse],
        dependencies=[administrator],
    )
    def audit() -> list[AuditEventResponse]:
        return [
            AuditEventResponse(
                id=event.id,
                actor=event.actor,
                action=event.action,
                target=event.target,
                result=event.result,
                occurred_at=event.occurred_at,
                authorization_context=event.authorization_context,
            )
            for event in services.repository.list_audit_events()
        ]

    @router.get(
        "/observability",
        response_model=dict[str, JsonValue],
        dependencies=[administrator],
    )
    def observability() -> dict[str, object]:
        return services.observability.snapshot()

    @router.post("/browser-errors", status_code=204)
    def browser_error(
        body: BrowserErrorRequest,
        session: Annotated[AuthenticatedSession, Depends(mutation_session)],
    ) -> Response:
        services.observability.record_browser_error(
            body.message,
            body.source,
            body.occurred_at,
        )
        services.repository.append_audit(
            actor=session.username,
            action="browser.error",
            target=body.source,
            result="reported",
            occurred_at=body.occurred_at,
            authorization_context={"message": body.message},
        )
        return Response(status_code=204)

    return router


def _require_allowed_origin(request: Request, services: GuiServices) -> None:
    origin = request.headers.get("Origin")
    if origin is None or origin not in services.config.allowed_origins:
        raise HTTPException(status_code=403, detail={"code": "origin_not_allowed"})


def _parse_if_match(value: str | None) -> int:
    if value is None:
        raise HTTPException(status_code=428, detail={"code": "if_match_required"})
    normalized = value.strip()
    if normalized.startswith('W/"') and normalized.endswith('"'):
        normalized = normalized[3:-1]
    elif normalized.startswith('"') and normalized.endswith('"'):
        normalized = normalized[1:-1]
    try:
        revision = int(normalized)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "invalid_if_match"}) from error
    if revision < 1:
        raise HTTPException(status_code=400, detail={"code": "invalid_if_match"})
    return revision


def _parse_optional_if_match(value: str | None) -> int | None:
    return _parse_if_match(value) if value is not None else None


def _project_summary(project: StoredProject) -> ProjectSummary:
    return ProjectSummary(
        id=project.id,
        name=project.name,
        head_revision=project.head_revision,
        metadata_revision=project.metadata_revision,
        archived_at=project.archived_at,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _project_draft(revision: StoredRevision) -> ProjectDraftResponse:
    return ProjectDraftResponse(
        project=_project_summary(revision.project),
        revision=revision.revision,
        draft=revision.draft,
    )


def _resource_assignment(
    assignment: StoredResourceAssignment,
) -> ResourceAssignmentResponse:
    return ResourceAssignmentResponse(
        resource=assignment.resource,
        device_id=assignment.device_id,
        capability_kind=assignment.capability_kind,
        device_output=assignment.device_output,
        revision=assignment.revision,
        actor=assignment.actor,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


def _installation_summary(
    installation: StoredInstallation,
    used_by_projects: tuple[str, ...],
) -> InstallationSummaryResponse:
    return InstallationSummaryResponse(
        id=installation.id,
        name=installation.name,
        revision=installation.revision,
        runtime_active=installation.runtime_active,
        folder=installation.folder,
        tags=installation.tags,
        favorite=installation.favorite,
        used_by_projects=used_by_projects,
        created_at=installation.created_at,
        updated_at=installation.updated_at,
    )


def _installation_photo(photo: StoredInstallationPhoto) -> InstallationPhotoResponse:
    return InstallationPhotoResponse(
        id=photo.id,
        installation_id=photo.installation_id,
        name=photo.name,
        viewpoint=photo.viewpoint,
        original_filename=photo.original_filename,
        media_type=photo.media_type,
        digest_sha256=photo.digest_sha256,
        size_bytes=photo.size_bytes,
        orientation_degrees=photo.orientation_degrees,
        crop_x=photo.crop_x,
        crop_y=photo.crop_y,
        crop_width=photo.crop_width,
        crop_height=photo.crop_height,
        scale_hint=photo.scale_hint,
        revision=photo.revision,
        created_at=photo.created_at,
        updated_at=photo.updated_at,
    )


def _installation_geometry(
    geometry: StoredInstallationGeometry,
) -> InstallationGeometryResponse:
    return InstallationGeometryResponse(
        id=geometry.id,
        installation_id=geometry.installation_id,
        photo_id=geometry.photo_id,
        kind=geometry.kind,
        name=geometry.name,
        semantic_id=geometry.semantic_id,
        landmark_ids=geometry.landmark_ids,
        color=geometry.color,
        label_visible=geometry.label_visible,
        locked=geometry.locked,
        hidden=geometry.hidden,
        z_index=geometry.z_index,
        direction=geometry.direction,
        start_index=geometry.start_index,
        pixel_count=geometry.pixel_count,
        stage_points=geometry.stage_points,
        points=tuple(GeometryPoint(x=point[0], y=point[1]) for point in geometry.points),
        closed=geometry.closed,
        revision=geometry.revision,
        created_at=geometry.created_at,
        updated_at=geometry.updated_at,
    )


def _geometry_suggestion(
    suggestion: StoredGeometrySuggestion,
) -> GeometrySuggestionResponse:
    return GeometrySuggestionResponse(
        id=suggestion.id,
        installation_id=suggestion.installation_id,
        photo_id=suggestion.photo_id,
        kind=suggestion.kind,
        name=suggestion.name,
        points=tuple(GeometryPoint(x=point[0], y=point[1]) for point in suggestion.points),
        closed=suggestion.closed,
        confidence=suggestion.confidence,
        evidence=suggestion.evidence,
        state=suggestion.state,
        created_by=suggestion.created_by,
        reviewed_by=suggestion.reviewed_by,
        created_at=suggestion.created_at,
        updated_at=suggestion.updated_at,
    )


def _projector_mapping(mapping: StoredProjectorMapping) -> ProjectorMappingResponse:
    return ProjectorMappingResponse(
        geometry_id=mapping.geometry_id,
        resource=mapping.resource,
        native_width=mapping.native_width,
        native_height=mapping.native_height,
        stage_x=mapping.stage_x,
        stage_y=mapping.stage_y,
        stage_width=mapping.stage_width,
        stage_height=mapping.stage_height,
        crop_x=mapping.crop_x,
        crop_y=mapping.crop_y,
        crop_width=mapping.crop_width,
        crop_height=mapping.crop_height,
        content_asset_id=mapping.content_asset_id,
        surface_model=mapping.surface_model,
        fit_mode=mapping.fit_mode,
        overscan=mapping.overscan,
        brightness_limit=mapping.brightness_limit,
        media_start_delay_ms=mapping.media_start_delay_ms,
        media_release_delay_ms=mapping.media_release_delay_ms,
        orientation_degrees=mapping.orientation_degrees,
        mirror_x=mapping.mirror_x,
        mirror_y=mapping.mirror_y,
        mask_semantic_ids=mapping.mask_semantic_ids,
        calibration_source=mapping.calibration_source,
        calibration_revision=mapping.calibration_revision,
        mesh_vertices=mapping.mesh_vertices,
        mesh_triangles=mapping.mesh_triangles,
        homography=mapping.homography,
        diagnostic=mapping.diagnostic,
        fit_diagnostics=mapping.fit_diagnostics,
        photo_views=tuple(
            ProjectorPhotoViewResponse(
                geometry_id=view.geometry_id,
                photo_id=view.photo_id,
                points=tuple(GeometryPoint(x=point[0], y=point[1]) for point in view.points),
                homography=view.homography,
            )
            for view in mapping.photo_views
        ),
        revision=mapping.revision,
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


def _render_job(job: StoredRenderJob) -> RenderJobResponse:
    return RenderJobResponse(
        id=job.id,
        project_id=job.project_id,
        source_revision=job.source_revision,
        composition_id=job.composition_id,
        cache_key=job.cache_key,
        state=job.state,
        progress=job.progress,
        reserved_bytes=job.reserved_bytes,
        error=job.error,
        artifact_id=job.artifact_id,
        cancel_requested=job.cancel_requested,
        created_by=job.created_by,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
    )


def _published_package(package: PublishedPackage) -> PublishedPackageResponse:
    return PublishedPackageResponse(
        id=package.id,
        project_id=package.project_id,
        revision=package.revision,
        digest_sha256=package.digest_sha256,
        state=package.state,
        manifest=package.manifest,
        created_by=package.created_by,
        created_at=package.created_at,
        staged_at=package.staged_at,
        activated_at=package.activated_at,
        deactivated_at=package.deactivated_at,
    )


def _simulation_scenario(
    scenario: StoredSimulationScenario,
) -> SimulationScenarioResponse:
    return SimulationScenarioResponse(
        id=scenario.id,
        project_id=scenario.project_id,
        name=scenario.name,
        source_revision=scenario.source_revision,
        steps=scenario.steps,
        expected=scenario.expected,
        created_at=scenario.created_at,
        updated_at=scenario.updated_at,
    )


def _sync_rehearsal(rehearsal: StoredSyncRehearsal) -> SyncRehearsalResponse:
    return SyncRehearsalResponse(
        id=rehearsal.id,
        resources=rehearsal.resources,
        start_error_ms=rehearsal.start_error_ms,
        drift_ms_per_minute=rehearsal.drift_ms_per_minute,
        measurement_duration_seconds=rehearsal.measurement_duration_seconds,
        context=cast(dict[str, JsonValue], rehearsal.context),
        notes=rehearsal.notes,
        actor=rehearsal.actor,
        measured_at=rehearsal.measured_at,
    )


def _sync_admission(admission: SyncAdmission) -> SyncAdmissionResponse:
    return SyncAdmissionResponse(
        allowed=admission.allowed,
        code=admission.code,
        message=admission.message,
        rehearsal=(
            _sync_rehearsal(admission.rehearsal) if admission.rehearsal is not None else None
        ),
    )


def _lighting_surface(surface: StoredLightingSurface) -> LightingSurfaceResponse:
    return LightingSurfaceResponse(
        geometry_id=surface.geometry_id,
        resource=surface.resource,
        local_width=surface.local_width,
        local_height=surface.local_height,
        local_points=surface.local_points,
        stage_matrix=surface.stage_matrix,
        fixture_id=surface.fixture_id,
        group_id=surface.group_id,
        path_id=surface.path_id,
        strand_id=surface.strand_id,
        calibration_id=surface.calibration_id,
        pixel_footprint_width=surface.pixel_footprint_width,
        pixel_footprint_height=surface.pixel_footprint_height,
        brightness_limit=surface.brightness_limit,
        timing_offset_ms=surface.timing_offset_ms,
        release_offset_ms=surface.release_offset_ms,
        pixel_overrides=tuple(
            PixelPositionOverrideResponse(
                output_index=override.output_index,
                local_x=override.local_x,
                local_y=override.local_y,
            )
            for override in surface.pixel_overrides
        ),
        pixels=tuple(
            MappedPixelResponse(
                output_index=pixel.output_index,
                local_x=pixel.local_x,
                local_y=pixel.local_y,
                stage_x=pixel.stage_x,
                stage_y=pixel.stage_y,
                footprint_width=pixel.footprint_width,
                footprint_height=pixel.footprint_height,
            )
            for pixel in surface.pixels
        ),
        revision=surface.revision,
        created_at=surface.created_at,
        updated_at=surface.updated_at,
    )


def _timing_calibration(
    session: StoredTimingCalibration,
) -> TimingCalibrationSessionResponse:
    return TimingCalibrationSessionResponse(
        id=session.id,
        target_kind=session.target_kind,
        geometry_id=session.geometry_id,
        adjustment_kind=session.adjustment_kind,
        baseline_ms=session.baseline_ms,
        proposed_ms=session.proposed_ms,
        state=session.state,
        revision=session.revision,
        expires_at=session.expires_at,
    )


def _output_test_response(test: OutputTestStatus) -> OutputTestResponse:
    return OutputTestResponse(
        id=test.id,
        arm_id=test.arm_id,
        resource=test.resource,
        pixel_count=test.pixel_count,
        speed=test.speed,
        state=test.state,
        pixel_index=test.pixel_index,
        frames_sent=test.frames_sent,
        started_at=test.started_at,
        expires_at=test.expires_at,
        error=test.error,
        stop_reason=test.stop_reason,
        segments=tuple(
            OutputTestSegmentResponse(
                name=segment.name,
                start=segment.start,
                end=segment.end,
                color=segment.color,
            )
            for segment in test.segments
        ),
    )


def _output_arm(arm: StoredOutputArm) -> OutputArmResponse:
    return OutputArmResponse(
        id=arm.id,
        resource=arm.resource,
        resource_kind=arm.resource_kind,
        device_identity=arm.device_identity,
        stop_action=arm.stop_action,
        state=arm.state,
        expires_at=arm.expires_at,
    )


def _accepted_calibration(
    calibration: StoredAcceptedCalibration,
) -> AcceptedCalibrationResponse:
    return AcceptedCalibrationResponse(
        id=calibration.id,
        target_kind=calibration.target_kind,
        geometry_id=calibration.geometry_id,
        adjustment_kind=calibration.adjustment_kind,
        accepted_ms=calibration.accepted_ms,
        resource=calibration.resource,
        device_id=calibration.device_id,
        software_version=calibration.software_version,
        test_asset_id=calibration.test_asset_id,
        test_asset_version=calibration.test_asset_version,
        observed_variability_ms=calibration.observed_variability_ms,
        notes=calibration.notes,
        context=calibration.context,
        actor=calibration.actor,
        accepted_at=calibration.accepted_at,
        stale_reasons=calibration.stale_reasons,
    )


def _projector_overlap(
    overlap: StoredProjectorOverlap,
) -> ProjectorOverlapResponse:
    return ProjectorOverlapResponse(
        id=overlap.id,
        installation_id=overlap.installation_id,
        first_geometry_id=overlap.first_geometry_id,
        second_geometry_id=overlap.second_geometry_id,
        stage_points=overlap.stage_points,
        ramp_width=overlap.ramp_width,
        overlap_gamma=overlap.overlap_gamma,
        first_brightness=overlap.first_brightness,
        second_brightness=overlap.second_brightness,
        first_white=overlap.first_white,
        second_white=overlap.second_white,
        first_color_matrix=overlap.first_color_matrix,
        second_color_matrix=overlap.second_color_matrix,
        first_black_level=overlap.first_black_level,
        second_black_level=overlap.second_black_level,
        diagnostics=overlap.diagnostics,
        revision=overlap.revision,
        created_at=overlap.created_at,
        updated_at=overlap.updated_at,
    )


def _asset_response(asset: StoredAsset) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        name=asset.name,
        status=asset.status,
        current_revision=asset.current_revision,
        metadata_revision=asset.metadata_revision,
        owner=asset.owner,
        license=asset.license,
        provenance=asset.provenance,
        review_status=asset.review_status,
        expires_on=asset.expires_on,
        folder=asset.folder,
        tags=asset.tags,
        favorite=asset.favorite,
        error=asset.error,
        digest_sha256=asset.digest_sha256,
        size_bytes=asset.size_bytes,
        media_type=asset.media_type,
        original_filename=asset.original_filename,
        scan_status=asset.scan_status,
        metadata=asset.metadata,
        original_file_status=asset.original_file_status,
        artifact_faults=asset.artifact_faults,
        references=tuple(
            AssetSceneReferenceResponse(
                scene_id=reference.scene_id,
                scene_name=reference.scene_name,
                output_id=reference.output_id,
            )
            for reference in asset.references
        ),
        unused=asset.unused,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


def _asset_upload_response(
    asset: StoredAsset,
    upload: StoredAssetUpload,
    chunk_max_bytes: int,
) -> AssetUploadResponse:
    return AssetUploadResponse(
        id=upload.id,
        asset_id=upload.asset_id,
        total_bytes=upload.total_bytes,
        received_bytes=upload.received_bytes,
        state=upload.state,
        error=upload.error,
        chunk_max_bytes=chunk_max_bytes,
        asset=_asset_response(asset),
    )


def _place_asset(
    draft: DraftProject,
    *,
    asset_id: str,
    asset_revision: int,
    scene_id: str,
    output_id: str | None,
) -> DraftProject:
    scenes = list(draft.scenes)
    for index, scene in enumerate(scenes):
        if scene.id != scene_id:
            continue
        if scene.schema_version == 1:
            if output_id is not None:
                raise ValueError("schema-v1 scenes do not accept a media output ID")
            scenes[index] = scene.model_copy(
                update={"media": scene.media.model_copy(update={"asset": asset_id})}
            )
        else:
            if output_id is None:
                raise ValueError("schema-v2 scenes require a media output ID")
            outputs = list(scene.media)
            for output_index, output in enumerate(outputs):
                if output.id == output_id:
                    outputs[output_index] = output.model_copy(update={"asset": asset_id})
                    break
            else:
                raise ValueError("scene media output does not exist")
            scenes[index] = scene.model_copy(update={"media": tuple(outputs)})
        break
    else:
        raise ValueError("scene does not exist")
    editor_metadata = dict(draft.editor_metadata)
    existing_bindings = editor_metadata.get("asset_bindings", {})
    if not isinstance(existing_bindings, dict):
        raise ValueError("draft asset bindings are invalid")
    bindings = dict(existing_bindings)
    bindings[asset_id] = asset_revision
    editor_metadata["asset_bindings"] = bindings
    return draft.model_copy(
        update={
            "scenes": tuple(scenes),
            "editor_metadata": editor_metadata,
        }
    )


def _organize_scene(
    draft: DraftProject,
    scene_id: str,
    organization: ObjectOrganizationRequest,
) -> DraftProject:
    if not any(scene.id == scene_id for scene in draft.scenes):
        raise ValueError("scene does not exist")
    editor_metadata = dict(draft.editor_metadata)
    current = editor_metadata.get("scene_organization", {})
    if not isinstance(current, dict):
        raise ValueError("scene organization metadata is invalid")
    organized = dict(current)
    organized[scene_id] = organization.model_dump(mode="json")
    editor_metadata["scene_organization"] = organized
    return draft.model_copy(update={"editor_metadata": editor_metadata})


def _create_scene_preset(
    draft: DraftProject,
    body: CreateScenePresetRequest,
) -> DraftProject:
    scene = next((candidate for candidate in draft.scenes if candidate.id == body.scene_id), None)
    if scene is None:
        raise ValueError("scene does not exist")
    editor_metadata = dict(draft.editor_metadata)
    current = editor_metadata.get("scene_presets", {})
    if not isinstance(current, dict):
        raise ValueError("scene preset metadata is invalid")
    presets = dict(current)
    preset_id = str(uuid4())
    presets[preset_id] = {
        "id": preset_id,
        "name": body.name,
        "folder": body.folder,
        "tags": list(body.tags),
        "favorite": body.favorite,
        "schema_version": scene.schema_version,
        "template": scene.model_dump(mode="json"),
    }
    editor_metadata["scene_presets"] = presets
    return draft.model_copy(update={"editor_metadata": editor_metadata})


def _apply_scene_preset(
    draft: DraftProject,
    preset_id: str,
    scene_id: str,
) -> DraftProject:
    presets = draft.editor_metadata.get("scene_presets", {})
    if not isinstance(presets, dict):
        raise ValueError("scene preset metadata is invalid")
    preset = presets.get(preset_id)
    if not isinstance(preset, dict):
        raise ValueError("preset does not exist")
    template = preset.get("template")
    if not isinstance(template, dict):
        raise ValueError("preset does not exist")
    scenes = list(draft.scenes)
    for index, scene in enumerate(scenes):
        if scene.id != scene_id:
            continue
        if preset.get("schema_version") != scene.schema_version:
            raise ValueError("preset schema does not match target scene")
        values = dict(template)
        values["id"] = scene.id
        values["name"] = scene.name
        scenes[index] = type(scene).model_validate(values)
        break
    else:
        raise ValueError("scene does not exist")
    editor_metadata = dict(draft.editor_metadata)
    current_usage = editor_metadata.get("scene_preset_usage", {})
    if not isinstance(current_usage, dict):
        raise ValueError("scene preset usage metadata is invalid")
    usage = dict(current_usage)
    usage[scene_id] = preset_id
    editor_metadata["scene_preset_usage"] = usage
    return DraftProject.model_validate(
        {
            **draft.model_dump(mode="json"),
            "scenes": [scene.model_dump(mode="json") for scene in scenes],
            "editor_metadata": editor_metadata,
        }
    )


def _revision_conflict_response(error: RevisionConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content=RevisionConflictResponse(
            requested_revision=error.requested_revision,
            current_revision=error.current_revision,
            conflicting_paths=error.conflicting_paths,
        ).model_dump(mode="json"),
    )


def _discovered_endpoint(
    endpoint: StoredDiscoveredEndpoint,
) -> DiscoveredEndpointResponse:
    return DiscoveredEndpointResponse(
        id=endpoint.id,
        base_url=endpoint.base_url,
        device_kind=endpoint.device_kind,
        software_version=endpoint.software_version,
        protocol_minimum=endpoint.protocol_minimum,
        protocol_maximum=endpoint.protocol_maximum,
        capability_kinds=endpoint.capability_kinds,
        compatible=endpoint.compatible,
        state=endpoint.state,
        enrolled_device_id=endpoint.enrolled_device_id,
        observed_at=endpoint.observed_at,
        updated_at=endpoint.updated_at,
    )


def _installation_resources(
    services: GuiServices,
) -> dict[str, InstallationResourceResponse]:
    return {
        binding.resource: InstallationResourceResponse(
            resource=binding.resource,
            kind=binding.kind.value,
            target=binding.target,
            pixel_count=binding.pixel_count,
        )
        for binding in services.runtime.catalog.installation.resources
    }


def _device_compatibility(
    enrollment_state: EnrollmentState,
    assignments: Collection[StoredResourceAssignment],
    resources: Mapping[str, InstallationResourceResponse],
) -> Literal["compatible", "conflict", "unknown"]:
    if enrollment_state is not EnrollmentState.ENROLLED:
        return "conflict" if assignments else "unknown"
    if not assignments:
        return "unknown"
    for assignment in assignments:
        resource = resources.get(assignment.resource)
        if resource is None:
            return "conflict"
        allowed_capabilities = _allowed_assignment_capabilities(resource.kind)
        if assignment.capability_kind not in allowed_capabilities:
            return "conflict"
    return "compatible"


def _device_compatibility_detail(
    enrollment_state: EnrollmentState,
    protocol_minimum: int,
    protocol_maximum: int,
    assignments: Collection[StoredResourceAssignment],
    resources: Mapping[str, InstallationResourceResponse],
) -> str:
    if enrollment_state is not EnrollmentState.ENROLLED:
        return f"Device state is {enrollment_state.value}; operational commands are denied"
    if not protocol_minimum <= DEVICE_PROTOCOL_VERSION <= protocol_maximum:
        return (
            f"Device supports protocol v{protocol_minimum}-v{protocol_maximum}; "
            f"controller requires v{DEVICE_PROTOCOL_VERSION}"
        )
    if not assignments:
        return "No logical resource is assigned"
    for assignment in assignments:
        resource = resources.get(assignment.resource)
        if resource is None:
            return f"Assigned resource {assignment.resource} is absent from the active installation"
        allowed_capabilities = _allowed_assignment_capabilities(resource.kind)
        if assignment.capability_kind not in allowed_capabilities:
            return (
                f"{assignment.capability_kind} cannot satisfy "
                f"{resource.kind} resource {assignment.resource}"
            )
    return f"Protocol v{DEVICE_PROTOCOL_VERSION} and assigned resource capabilities are compatible"


def _allowed_assignment_capabilities(resource_kind: str) -> set[str]:
    if resource_kind == "media_agent":
        return {"media_playback"}
    if resource_kind == "pixel_output":
        return {"pixel_output"}
    if resource_kind == "downstream_controller":
        return {"downstream_control"}
    raise ValueError(f"unsupported installation resource kind: {resource_kind}")


def _topology_enrollment(
    state: EnrollmentState,
) -> Literal["enrolled", "suspended", "revoked"]:
    if state is EnrollmentState.ENROLLED:
        return "enrolled"
    if state is EnrollmentState.SUSPENDED:
        return "suspended"
    if state is EnrollmentState.REVOKED:
        return "revoked"
    raise ValueError(f"device registry contains non-terminal enrollment state: {state.value}")


def _managed_enrollment_state(
    state: EnrollmentState,
) -> Literal["enrolled", "suspended", "revoked"]:
    if state is EnrollmentState.ENROLLED:
        return "enrolled"
    if state is EnrollmentState.SUSPENDED:
        return "suspended"
    if state is EnrollmentState.REVOKED:
        return "revoked"
    raise ValueError(f"device registry contains unmanaged enrollment state: {state.value}")


def _device_is_connected(
    enrollment_state: EnrollmentState,
    presence: StoredDevicePresence | None,
    *,
    observed_at_ms: int,
    timeout_ms: int,
) -> bool:
    return (
        enrollment_state is EnrollmentState.ENROLLED
        and presence is not None
        and presence.disconnected_at_ms is None
        and observed_at_ms - presence.last_seen_at_ms <= timeout_ms
    )


def _activity_category(
    action: str,
) -> Literal["device", "operation", "configuration", "project", "security"]:
    if action.startswith("device."):
        return "device"
    if action.startswith(("operate.", "maintenance.")):
        return "operation"
    if action.startswith("resource."):
        return "configuration"
    if action.startswith(("project.", "draft.")):
        return "project"
    return "security"


def _etag(revision: int) -> str:
    return f'"{revision}"'


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _mcp_token_response(
    token: McpToken,
    secret: str | None = None,
) -> McpTokenResponse:
    return McpTokenResponse(
        id=token.id,
        client_id=token.client_id,
        username=token.username,
        project_id=token.project_id,
        scopes=tuple(scope.value for scope in token.scopes),
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
        created_at=token.created_at,
        token=secret,
    )


def _mcp_proposal_response(proposal: McpProposal) -> McpProposalResponse:
    return McpProposalResponse(
        id=proposal.id,
        project_id=proposal.project_id,
        base_revision=proposal.base_revision,
        title=proposal.title,
        kind=proposal.kind,
        changed_paths=proposal.changed_paths,
        state=proposal.state.value,
        client_id=proposal.client_id,
        username=proposal.username,
        validation=proposal.validation,
        simulation=proposal.simulation,
        approved_by=proposal.approved_by,
        disposition_reason=proposal.disposition_reason,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


def _require_project_proposal(
    services: GuiServices,
    project_id: str,
    proposal_id: str,
) -> McpProposal:
    try:
        proposal = services.mcp_store.require_proposal(proposal_id)
    except McpAuthoringError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "mcp_proposal_not_found"},
        ) from error
    if proposal.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "mcp_proposal_not_found"},
        )
    return proposal


def _audit_mcp_proposal(
    services: GuiServices,
    identity: AuthenticatedSession,
    proposal: McpProposal,
    action: str,
) -> None:
    services.repository.append_audit(
        actor=identity.username,
        action=f"mcp.proposal.{action}",
        target=proposal.id,
        result=proposal.state.value,
        occurred_at=_now_iso(),
        authorization_context={
            "project_id": proposal.project_id,
            "base_revision": proposal.base_revision,
            "client_id": proposal.client_id,
            "model_username": proposal.username,
        },
    )
