import asyncio
import time
from collections.abc import Callable, Collection, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from cueverge.controller.runtime import ShowMode
from cueverge.gui.auth import (
    AuthenticatedSession,
    LoginRateLimitError,
)
from cueverge.gui.compiler import CompilationError
from cueverge.gui.contracts import (
    ActivityEventResponse,
    AuditEventResponse,
    CapabilityReviewIssue,
    CapabilityReviewRequest,
    CapabilityReviewResponse,
    CompilationResponse,
    ConnectionTestCheck,
    CreateInstallationRequest,
    CreatePairingTokenRequest,
    CreateProjectRequest,
    DeviceArtifactResponse,
    DeviceConnectionPolicyResponse,
    DeviceConnectionTestResponse,
    DeviceEnrollmentStateResponse,
    DeviceTransferStatusResponse,
    DiscoveredEndpointResponse,
    DraftProject,
    InstallationConflictResponse,
    InstallationResourceResponse,
    InstallationStatusResponse,
    InstallationSummaryResponse,
    LoginRequest,
    MaintenancePreviewRequest,
    MaintenancePreviewResponse,
    OperateActionRequest,
    OperateActionResponse,
    PairingTokenResponse,
    ProbeEndpointRequest,
    ProjectDraftResponse,
    ProjectSummary,
    ResourceAssignmentConflictResponse,
    ResourceAssignmentResponse,
    RevisionConflictResponse,
    RevisionSummary,
    Role,
    RuntimeStatusResponse,
    SessionResponse,
    SetDeviceConnectionPolicyRequest,
    SetDeviceEnrollmentStateRequest,
    SetResourceAssignmentRequest,
    TopologyNodeResponse,
    TopologyResponse,
    UpdateInstallationRequest,
)
from cueverge.gui.endpoint_discovery import EndpointDiscoveryError
from cueverge.gui.repository import (
    ActiveInstallationDeleteError,
    DeviceNotFoundError,
    DeviceStateTransitionError,
    DiscoveredEndpointNotUsableError,
    InstallationConflictError,
    InstallationNameConflictError,
    InstallationNotFoundError,
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
from cueverge.gui.service import GuiServices
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
        "/projects",
        response_model=list[ProjectSummary],
        dependencies=[reader],
    )
    def projects() -> list[ProjectSummary]:
        return [_project_summary(project) for project in services.repository.list_projects()]

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
        response.headers["ETag"] = _etag(revision.revision)
        return _project_draft(revision)

    @router.get(
        "/projects/{project_id}/draft",
        response_model=ProjectDraftResponse,
        dependencies=[reader],
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
        identity: Annotated[AuthenticatedSession, author],
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
        dependencies=[reader],
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
        identity: Annotated[AuthenticatedSession, author],
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
        identity: Annotated[AuthenticatedSession, author],
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
        try:
            compiled = services.compiler.compile(
                project_id=project_id,
                revision=revision.revision,
                draft=revision.draft,
            )
        except CompilationError as error:
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
                detail={"code": "compilation_failed", "message": str(error)},
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
        "/installations",
        response_model=list[InstallationSummaryResponse],
        dependencies=[reader],
    )
    def installations() -> list[InstallationSummaryResponse]:
        return [
            _installation_summary(installation)
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
        return _installation_summary(installation)

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
        return _installation_summary(installation)

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
        return _installation_summary(installation)

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

    @router.get(
        "/runtime/status",
        response_model=RuntimeStatusResponse,
        dependencies=[reader],
    )
    def runtime_status() -> RuntimeStatusResponse:
        return RuntimeStatusResponse(
            observed_at=_now_iso(),
            status=services.runtime.status(),
        )

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

    @router.post("/operate/actions", response_model=OperateActionResponse)
    async def operate(
        body: OperateActionRequest,
        identity: Annotated[AuthenticatedSession, operator_mutation],
    ) -> OperateActionResponse:
        occurred_at = _now_iso()
        try:
            if body.action == "start":
                await services.runtime.start_show()
            elif body.action == "pause":
                await services.runtime.pause()
            elif body.action == "resume":
                await services.runtime.resume()
            elif body.action == "seek":
                assert body.position_ms is not None
                await services.runtime.seek(body.position_ms)
            elif body.action == "stop":
                await services.runtime.stop()
            elif body.action == "reset":
                await services.runtime.reset_ambient()
            elif body.action == "automatic":
                await services.runtime.set_show_mode(ShowMode.AUTOMATIC)
            elif body.action == "maintenance":
                await services.runtime.set_show_mode(ShowMode.MAINTENANCE)
            else:
                await services.runtime.set_show_mode(ShowMode.BLACKOUT)
        except RuntimeError as error:
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
            )
            for artifact in artifacts
        ]

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


def _project_summary(project: StoredProject) -> ProjectSummary:
    return ProjectSummary(
        id=project.id,
        name=project.name,
        head_revision=project.head_revision,
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
) -> InstallationSummaryResponse:
    return InstallationSummaryResponse(
        id=installation.id,
        name=installation.name,
        revision=installation.revision,
        runtime_active=installation.runtime_active,
        created_at=installation.created_at,
        updated_at=installation.updated_at,
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
