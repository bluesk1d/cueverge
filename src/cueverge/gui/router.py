from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from cueverge.gui.auth import (
    AuthenticatedSession,
    LoginRateLimitError,
)
from cueverge.gui.compiler import CompilationError
from cueverge.gui.contracts import (
    AuditEventResponse,
    CompilationResponse,
    CreateProjectRequest,
    DraftProject,
    InstallationResourceResponse,
    InstallationStatusResponse,
    LoginRequest,
    ProjectDraftResponse,
    ProjectSummary,
    RevisionConflictResponse,
    RevisionSummary,
    Role,
    RuntimeStatusResponse,
    SessionResponse,
)
from cueverge.gui.repository import (
    ProjectNotFoundError,
    RevisionConflictError,
    StoredProject,
    StoredRevision,
)
from cueverge.gui.service import GuiServices

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
        "/installation",
        response_model=InstallationStatusResponse,
        dependencies=[reader],
    )
    def installation() -> InstallationStatusResponse:
        resources: dict[str, InstallationResourceResponse] = {}
        for scene in services.runtime.catalog.scenes.values():
            for media_output in scene.media_outputs:
                resources.setdefault(
                    media_output.definition.resource,
                    InstallationResourceResponse(
                        resource=media_output.definition.resource,
                        kind="media_agent",
                        target=media_output.agent_id,
                    ),
                )
            for lighting_output in scene.lighting_outputs:
                resources.setdefault(
                    lighting_output.definition.resource,
                    InstallationResourceResponse(
                        resource=lighting_output.definition.resource,
                        kind="pixel_output",
                        pixel_count=lighting_output.pixel_count,
                    ),
                )
        return InstallationStatusResponse(
            name=services.runtime.catalog.package_root.name,
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


def _etag(revision: int) -> str:
    return f'"{revision}"'


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
