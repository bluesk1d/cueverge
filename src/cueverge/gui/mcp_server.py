import base64
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from io import BytesIO
from threading import Lock
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent
from PIL import Image
from pydantic import AnyHttpUrl, JsonValue, ValidationError
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cueverge.core.loader import ShowPackageError
from cueverge.gui.apply import ApplyError, ApplyRequest
from cueverge.gui.artifact_deployment import ArtifactDeploymentError
from cueverge.gui.asset_upload import AssetUploadError
from cueverge.gui.authoring_schema import authoring_schema
from cueverge.gui.compiler import CompilationError
from cueverge.gui.contracts import (
    AddSceneOutputRequest,
    ConfigureInputSourceRequest,
    CreateSceneRequest,
    DeleteSceneRequest,
    DraftProject,
    InstallationObjectDeleteRequest,
    InstallationObjectEditRequest,
    InteractionEditRequest,
    InteractionPreviewRequest,
    OperateActionRequest,
    OutputMonitorRequest,
    PhotoDescriptionRequest,
    RemoveSceneOutputRequest,
    RenameSceneOutputRequest,
    ReplaceInstallationPhotoRequest,
    Role,
    SceneLightingPreviewRequest,
    ShowPlanEditRequest,
    StageReferenceRequest,
    UpdateRevisionReleaseRequest,
)
from cueverge.gui.device_session import DeviceSessionError
from cueverge.gui.input_sources import InputSourceError
from cueverge.gui.installation_objects import (
    installation_object_inventory,
    save_installation_object,
)
from cueverge.gui.installation_photos import MAX_PHOTO_BYTES, InstallationPhotoError
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
from cueverge.gui.mapper_export import MapperExportError
from cueverge.gui.mcp_authoring import (
    MAX_ASSET_RESULTS,
    MAX_MCP_PAYLOAD_BYTES,
    MAX_TIMELINE_RANGE_MS,
    McpAuthoringError,
    McpProposal,
    McpScope,
    McpToken,
    allowed_mcp_scopes,
)
from cueverge.gui.projector_renderer import ProjectorRenderError, preview_projector_composition
from cueverge.gui.publication import ApprovalState, PublicationError, PublishedPackage
from cueverge.gui.render_jobs import RenderJobError, StoredRenderJob
from cueverge.gui.repository import ProjectNotFoundError, RevisionConflictError
from cueverge.gui.scene_authoring import delete_scene as delete_draft_scene
from cueverge.gui.scene_authoring import (
    save_scene_edit,
    save_scene_output_change,
    scene_authoring_options,
)
from cueverge.gui.scene_preview import ScenePreviewError, preview_project_scene
from cueverge.gui.service import GuiServices
from cueverge.gui.show_plan import ShowPlanError, apply_show_plan_edit, describe_show_plan
from cueverge.gui.spatial_authoring import (
    layout_digest,
    lighting_preview_png,
    propose_effect_draft,
    spatial_context,
)
from cueverge.gui.spatial_effects import SpatialRipple, spatial_effect_catalog
from cueverge.gui.stage_reference import (
    StageReferenceConflictError,
    StageReferenceError,
    stage_map_response,
)
from cueverge.gui.stage_renderer import (
    preview_stage_composition_member,
    preview_stage_lighting_samples,
)
from cueverge.integrations.mqtt import MAX_INPUT_PAYLOAD_BYTES, MAX_INPUT_RECENT_EVENTS

MAX_CONCURRENT_CALLS_PER_TOKEN = 4


class McpRequestGuard(BaseHTTPMiddleware):
    def __init__(self, app: Any, services: GuiServices) -> None:
        super().__init__(app)
        self.services = services
        self._lock = Lock()
        self._active: defaultdict[str, int] = defaultdict(int)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > MAX_MCP_PAYLOAD_BYTES:
            return JSONResponse({"error": "MCP request body is too large"}, status_code=413)
        record = self._resolve_token(request)
        is_tool_call = False
        tool_name = "unknown"
        argument_digest: str | None = None
        argument_keys: list[str] = []
        if request.method == "POST" and record is not None:
            body = await request.body()
            if len(body) > MAX_MCP_PAYLOAD_BYTES:
                return JSONResponse(
                    {"error": "MCP request body is too large"},
                    status_code=413,
                )
            try:
                message = json.loads(body)
            except json.JSONDecodeError:
                message = None
            if isinstance(message, dict) and message.get("method") == "tools/call":
                is_tool_call = True
                params = message.get("params")
                if isinstance(params, dict):
                    if isinstance(params.get("name"), str):
                        tool_name = params["name"]
                    arguments = params.get("arguments")
                    if isinstance(arguments, dict):
                        encoded = json.dumps(
                            arguments,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                        argument_digest = hashlib.sha256(encoded).hexdigest()
                        argument_keys = sorted(str(key) for key in arguments)
        acquired = False
        if is_tool_call and record is not None:
            with self._lock:
                if self._active[record.id] >= MAX_CONCURRENT_CALLS_PER_TOKEN:
                    return JSONResponse(
                        {"error": "MCP concurrent call limit exceeded"},
                        status_code=429,
                    )
                self._active[record.id] += 1
                acquired = True
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            if acquired and record is not None:
                with self._lock:
                    self._active[record.id] -= 1
                    if self._active[record.id] == 0:
                        del self._active[record.id]
                authorization_context: dict[str, JsonValue] = {
                    "mcp_client_id": record.client_id,
                    "mcp_token_id": record.id,
                    "project_id": record.project_id,
                    "scopes": [scope.value for scope in record.scopes],
                    "argument_digest_sha256": argument_digest,
                    "argument_keys": cast(list[JsonValue], argument_keys),
                    "http_status": status_code,
                }
                self.services.repository.append_audit(
                    actor=record.username,
                    action="mcp.tool.call",
                    target=tool_name,
                    result="completed" if status_code < 400 else "failed",
                    occurred_at=_now_iso(),
                    authorization_context=authorization_context,
                )

    def _resolve_token(self, request: Request) -> McpToken | None:
        scheme, _, secret = request.headers.get("authorization", "").partition(" ")
        if scheme.casefold() != "bearer" or not secret:
            return None
        token = self.services.mcp_store.verify_token(secret, _now_iso())
        if token is None or not _token_currently_authorized(self.services, token):
            return None
        return token


class CuevergeTokenVerifier(TokenVerifier):
    def __init__(self, services: GuiServices, resource_url: str) -> None:
        self.services = services
        self.resource_url = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        record = self.services.mcp_store.verify_token(token, _now_iso())
        if record is None or not _token_currently_authorized(self.services, record):
            return None
        return AccessToken(
            token=token,
            client_id=record.client_id,
            scopes=[scope.value for scope in record.scopes],
            expires_at=int(datetime.fromisoformat(record.expires_at).timestamp()),
            resource=self.resource_url,
            subject=record.username,
            claims={
                "token_id": record.id,
                "project_id": record.project_id,
                "username": record.username,
            },
        )


def create_mcp_app(services: GuiServices) -> Starlette:
    origin = services.config.allowed_origins[0].rstrip("/")
    origin_url = urlsplit(origin)
    if origin_url.hostname is None:
        raise ValueError("the MCP public origin must include a hostname")
    resource_url = f"{origin}/mcp/"
    server = MCPServer(
        name="Cueverge Authoring",
        description=(
            "Project-scoped media-synchronized and controller-timed/LED-only show authoring, "
            "calibrated spatial geometry, shared-stage "
            "image/video/GIF overlays, no-hardware image previews, and baked LED/projector "
            "output. Read get_capabilities and get_authoring_schema for available workflows. "
            "Proposals require human approval and merge; "
            "direct draft editing, publishing, and live package activation require "
            "separate, explicitly granted scopes. All model output is untrusted."
        ),
        token_verifier=CuevergeTokenVerifier(services, resource_url),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(origin),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=[McpScope.READ.value],
            validate_token_resource=True,
        ),
    )

    @server.tool()
    def get_project_structure() -> dict[str, JsonValue]:
        """Read scene summaries and autonomous show structure for the bound project."""
        token = _authorize(services, McpScope.READ)
        revision = services.repository.get_revision(token.project_id)
        return {
            "project_id": token.project_id,
            "revision": revision.revision,
            "name": revision.project.name,
            "scenes": [
                {
                    "id": scene.id,
                    "name": scene.name,
                    "duration_ms": scene.duration_ms,
                    "resources": list(scene.resources),
                    "schema_version": scene.schema_version,
                    "mode": scene.mode,
                    "clock_source": scene.clock_source if scene.schema_version == 2 else "media",
                }
                for scene in revision.draft.scenes
            ],
            "autonomous": {
                "program": revision.draft.program is not None,
                "audience": revision.draft.audience is not None,
                "opportunities": revision.draft.opportunities is not None,
                "encounter": revision.draft.encounter is not None,
            },
        }

    @server.tool()
    async def get_runtime_status() -> dict[str, JsonValue]:
        """Read an observed controller snapshot for this project's runtime installation.

        Requires read and a matching installation binding. Controller connectivity
        is not equipment readiness, published is not loaded, and an observation
        is not proof of visible physical output. This does not start playback.
        """
        token = _authorize(services, McpScope.READ)
        revision = services.repository.get_revision(token.project_id)
        installation = services.repository.get_runtime_installation()
        if revision.draft.editor_metadata.get("installation_id") != installation.id:
            raise ToolError("project is not bound to this runtime installation")
        snapshot = services.runtime_snapshot()
        return _bounded_result(snapshot.model_dump(mode="json"))

    def input_installation_token(scope: McpScope) -> McpToken:
        token = _authorize(services, scope)
        revision = services.repository.get_revision(token.project_id)
        if (
            revision.draft.editor_metadata.get("installation_id")
            != services.repository.get_runtime_installation().id
        ):
            raise ToolError("project is not bound to this runtime installation")
        return token

    @server.tool()
    async def get_output_monitor(
        request: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        """Read bounded last-successful controller LED sends for the token's loaded show.

        Requires read, matching installation and loaded project. No rendering or
        physical commands. Ages, held/stale state and send failures are separate
        from physical observation. Up to 32 resources/4096 samples; select a resource
        and pixel_offset for coverage. Capture is capped at 5 Hz; MCP token rate
        limits still apply. Media playback reports are in get_runtime_status.
        """
        token = input_installation_token(McpScope.READ)
        if services.runtime.loaded_catalog.get("project_id") != token.project_id:
            raise ToolError("Apply this project before reading its loaded output monitor")
        try:
            parsed = OutputMonitorRequest.model_validate(request or {})
            return _bounded_result(
                services.runtime.output_monitor(
                    parsed.resource,
                    parsed.pixel_offset,
                    parsed.pixel_limit,
                )
            )
        except (ValueError, ValidationError) as error:
            raise ToolError(str(error)) from error

    @server.tool()
    async def operate_loaded_show(
        request: dict[str, JsonValue],
        confirm_physical_output: bool = False,
    ) -> dict[str, JsonValue]:
        """Operate only the token's actually loaded show, not a working draft.

        Requires explicit live_operation, current operator/admin with live-output
        permission, matching runtime installation/project and physical confirmation.
        Start may select a loaded scene (including LED-only shows without a program).
        Stop/Blackout cancel Apply and take priority. Stale/revoked grants are rejected.
        This is not simulation: commands can affect real equipment.
        """
        token = input_installation_token(McpScope.LIVE_OPERATION)
        if services.runtime.loaded_catalog.get("project_id") != token.project_id:
            raise ToolError("This token does not own the currently loaded show")
        if not confirm_physical_output:
            raise ToolError("Explicit physical-output confirmation is required")
        try:
            parsed = OperateActionRequest.model_validate(request)
            await services.operate(parsed)
        except (ValidationError, RuntimeError, LookupError) as error:
            services.repository.append_audit(
                actor=token.username,
                action="mcp.operate",
                target=token.project_id,
                result="error",
                occurred_at=_now_iso(),
                authorization_context={"error": str(error), "token_id": token.id},
            )
            raise ToolError(str(error)) from error
        services.repository.append_audit(
            actor=token.username,
            action=f"mcp.operate.{parsed.action}",
            target=token.project_id,
            result="success",
            occurred_at=_now_iso(),
            authorization_context={"token_id": token.id, "scope": "live_operation"},
        )
        return _bounded_result(
            {
                "source": "controller_runtime",
                "status": services.runtime.status(),
                "hardware_operation_requested": True,
            }
        )

    @server.tool()
    def get_installation_setup(
        photo_id: str | None = None, offset: int = 0, limit: int = 50
    ) -> dict[str, JsonValue]:
        """Read photo-first setup, stable photo/layout IDs and enrollment boundaries.

        Requires read and this runtime's installation binding. Geometry is read-only;
        2D photo overlap is not a physical wiring error. Device enrollment/assignment
        still require the administrator GUI, origin/CSRF and existing trust checks.
        """
        input_installation_token(McpScope.READ)
        if offset < 0 or not 1 <= limit <= 100:
            raise ToolError("Setup offset must be nonnegative and limit between 1 and 100")
        installation = services.repository.get_runtime_installation()
        photos = services.installation_photos.list(installation.id)
        if photo_id is not None and not any(photo.id == photo_id for photo in photos):
            raise ToolError("Photo is outside this runtime installation")
        geometry = [
            item
            for item in services.installation_geometry.list(installation.id)
            if photo_id is None or item.photo_id == photo_id
        ]
        return _bounded_result(
            {
                "installation_id": installation.id,
                "photos": [{**asdict(photo), "description": photo.name} for photo in photos],
                "geometry_owner": "installation",
                "photo_optional": True,
                "layout_objects": [
                    {
                        "id": item.id,
                        "photo_id": item.photo_id,
                        "semantic_id": item.semantic_id,
                        "name": item.name,
                        "kind": item.kind,
                        "revision": item.revision,
                        "point_count": len(item.points),
                        "locked": item.locked,
                        "hidden": item.hidden,
                        "start_index": item.start_index,
                        "pixel_count": item.pixel_count,
                    }
                    for item in geometry[offset : offset + limit]
                ],
                "next_offset": offset + limit if offset + limit < len(geometry) else None,
                "total_objects": len(geometry),
                "observed_agents": [asdict(agent) for agent in services.runtime.agent_status()],
                "device_setup": {
                    "enrollment_enabled": services.config.device_enrollment_enabled,
                    "interface": "Installation > Devices > Add a device",
                    "authorization": (
                        "global_administrator_gui_with_origin_csrf_and_protocol_validation"
                    ),
                    "mcp_device_writes": False,
                },
                "description_tool": "describe_installation_photo",
                "layout_guidance": (
                    "Select is the default. Add explicitly to draw; bounds/draw precedence "
                    "are optional photo diagnostics, not proof of wiring conflicts."
                ),
            }
        )

    @server.tool()
    def describe_installation_photo(
        photo_id: str, source_revision: int, description: str
    ) -> dict[str, JsonValue]:
        """Edit only a photo's description, retaining view ID, geometry, crop and calibration.

        Requires explicit installation_edit, an installation author role, and matching
        runtime binding. Project direct_edit alone is insufficient. Re-read on revision
        conflict. Does not upload files, enroll devices or change physical output.
        """
        token = input_installation_token(McpScope.INSTALLATION_EDIT)
        try:
            parsed = PhotoDescriptionRequest(description=description)
            photo = services.installation_photos.get(photo_id)
            if photo.installation_id != services.repository.get_runtime_installation().id:
                raise ToolError("Photo is outside this runtime installation")
            updated = services.installation_photos.describe(
                photo_id=photo_id,
                expected_revision=source_revision,
                description=parsed.description,
                actor=token.username,
                role=_token_role(services, token),
                occurred_at=_now_iso(),
            )
        except (ValidationError, InstallationPhotoError) as error:
            raise ToolError(str(error)) from error
        return _bounded_result(
            {**asdict(updated), "description": updated.name, "hardware_commands_sent": False}
        )

    @server.tool()
    def replace_installation_photo(request: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Replace a reference image from a verified original image asset in this project.

        Requires separately opted-in photo_replace and current installation/project
        author permission. installation_edit alone does not authorize replacement.
        Retains photo ID, all installation geometry, linked views, wiring and calibration.
        Geometry is not repositioned: explicitly confirm a review of background alignment.
        Existing photo metadata is retained. No file paths, arbitrary URLs or hardware control.
        """
        token = input_installation_token(McpScope.PHOTO_REPLACE)
        try:
            parsed = ReplaceInstallationPhotoRequest.model_validate(request)
            photo = services.installation_photos.get(parsed.photo_id)
            if photo.installation_id != services.repository.get_runtime_installation().id:
                raise ToolError("Photo is outside the bound runtime installation")
            asset = services.asset_upload.get_asset(parsed.asset_id)
            if asset.project_id != token.project_id:
                raise ToolError("Replacement asset is outside the token project")
            if asset.status != "ready":
                raise ToolError("Replacement asset must be ready and not quarantined or failed")
            if asset.size_bytes is None or not 0 < asset.size_bytes <= MAX_PHOTO_BYTES:
                raise ToolError("Replacement asset exceeds the 25 MB photo limit")
            if asset.original_filename is None or asset.media_type is None:
                raise ToolError("Replacement asset has no verified original image metadata")
            _, path = services.asset_upload.get_original(asset.id)
            updated = services.installation_photos.replace(
                photo_id=photo.id,
                expected_revision=parsed.source_revision,
                original_filename=asset.original_filename,
                media_type=asset.media_type,
                content=path.read_bytes(),
                actor=token.username,
                role=_token_role(services, token),
                occurred_at=_now_iso(),
            )
        except (ValidationError, InstallationPhotoError, AssetUploadError) as error:
            raise ToolError(str(error)) from error
        return _bounded_result(
            {
                **asdict(updated),
                "description": updated.name,
                "geometry_preserved": True,
                "alignment_review_required": True,
                "hardware_commands_sent": False,
            }
        )

    @server.tool()
    def get_stage_map() -> dict[str, JsonValue]:
        """Read the stage reference view, scale and per-object shared-stage placement.

        Requires read and this runtime's installation binding. Status is placed,
        needs_update (reference view drawing differs from saved stage data), manual
        (stage data not derived from the reference view, including manually placed
        projectors) or unplaced. Surface-placed projectors follow their drawn surface
        automatically; get_spatial_layout reports each projector's stage_placement.
        Read-only.
        """
        input_installation_token(McpScope.READ)
        installation = services.repository.get_runtime_installation()
        return _bounded_result(
            {
                **stage_map_response(services.stage_reference.status(installation.id)).model_dump(
                    mode="json"
                ),
                "hardware_commands_sent": False,
            }
        )

    @server.tool()
    def update_stage_reference(
        request: dict[str, JsonValue],
        source_revision: int | None = None,
        apply: bool = True,
    ) -> dict[str, JsonValue]:
        """Choose the stage reference photo view and scale, then optionally re-derive placement.

        Requires explicit installation_edit, an installation author role and the
        runtime installation binding. source_revision is the current stage reference
        revision (omit only when none exists). apply=True rewrites stage points, LED
        stage transforms and surface-placed projector stage homographies for objects
        drawn on the reference view; manually placed projectors, photo calibration,
        wiring and native pixels are unchanged.
        Sends no hardware commands. Re-read with get_stage_map on conflict.
        """
        token = input_installation_token(McpScope.INSTALLATION_EDIT)
        installation = services.repository.get_runtime_installation()
        role = _token_role(services, token)
        try:
            parsed = StageReferenceRequest.model_validate(request)
            reference = services.stage_reference.update(
                installation_id=installation.id,
                expected_revision=source_revision,
                photo_id=parsed.photo_id,
                unit_label=parsed.unit_label,
                stage_width=parsed.stage_width,
                scale_points=(
                    (
                        (parsed.scale_points[0].x, parsed.scale_points[0].y),
                        (parsed.scale_points[1].x, parsed.scale_points[1].y),
                    )
                    if parsed.scale_points is not None
                    else None
                ),
                scale_distance=parsed.scale_distance,
                actor=token.username,
                role=role,
                occurred_at=_now_iso(),
            )
            stage_map = (
                services.stage_reference.apply(
                    installation_id=installation.id,
                    expected_revision=reference.revision,
                    actor=token.username,
                    role=role,
                    occurred_at=_now_iso(),
                )
                if apply
                else services.stage_reference.status(installation.id)
            )
        except StageReferenceConflictError as error:
            raise ToolError(
                f"Stage reference revision conflict; current revision {error.current_revision}"
            ) from error
        except (ValidationError, StageReferenceError) as error:
            raise ToolError(str(error)) from error
        return _bounded_result(
            {
                **stage_map_response(stage_map).model_dump(mode="json"),
                "applied": apply,
                "hardware_commands_sent": False,
            }
        )

    @server.tool()
    def get_input_source() -> dict[str, JsonValue]:
        """Read secret-free Frigate/MQTT settings, connection and event/visitor health.

        Requires read and the runtime installation binding. Connection, fresh input,
        accepted input, visitor matching and scene playback are distinct evidence.
        """
        input_installation_token(McpScope.READ)
        return _bounded_result(services.input_source.snapshot())

    @server.tool()
    async def configure_input_source(configuration: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Configure this installation's Frigate/MQTT ingress; may trigger loaded show responses.

        Requires explicit input_config and a current global administrator, never
        direct_edit alone. Only GUI-managed sources are writable. Password is
        write-only; omit to retain or use clear_password to remove it. Settings
        persist separately from shows. Save/reconnect is not connection success.
        """
        token = input_installation_token(McpScope.INPUT_CONFIG)
        try:
            request = ConfigureInputSourceRequest.model_validate(configuration)
        except ValidationError:
            raise ToolError(
                "Invalid broker settings or credential action; "
                "inspect the input configuration schema"
            ) from None
        try:
            result = await services.input_source.configure(request)
        except InputSourceError as error:
            raise ToolError(str(error)) from error
        services.repository.append_audit(
            actor=token.username,
            action="input.configure",
            target="frigate_mqtt",
            result="success",
            occurred_at=_now_iso(),
            authorization_context={"management": "gui", "enabled": request.enabled},
        )
        return _bounded_result(result)

    @server.tool()
    def validate_input_source_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Safely normalize a Frigate example and check loaded visitor rules in isolation.

        Requires simulation and runtime installation binding. Never injects a live
        event, connects MQTT, mutates audience state or commands hardware. This
        does not prove real reception, qualification timing or scene playback.
        Compare validation_context_digest with current input health before reuse.
        """
        input_installation_token(McpScope.SIMULATION)
        try:
            return _bounded_result(services.input_source.validate_payload(payload))
        except InputSourceError as error:
            raise ToolError(str(error)) from error

    @server.tool()
    def preview_scene_lighting(
        scene_id: str,
        timestamp_ms: int,
        source_revision: int | None = None,
        proposed_draft: dict[str, JsonValue] | None = None,
        resource: str | None = None,
        pixel_offset: int = 0,
        pixel_limit: int = 1000,
    ) -> dict[str, JsonValue]:
        """Evaluate raw/compiled scene LEDs with the production renderer; requires simulation.

        Saved or unsaved candidates remain isolated. No hardware commands are sent.
        Returns bounded pixel pages, revision, time and explicit coverage. This is
        not a live monitor. Spatial compositions use preview_composition instead.
        """
        token = _authorize(services, McpScope.SIMULATION)
        try:
            revision = services.repository.get_revision(token.project_id, source_revision)
            request = SceneLightingPreviewRequest(
                source_revision=revision.revision,
                scene_id=scene_id,
                timestamp_ms=timestamp_ms,
                proposed_draft=(
                    DraftProject.model_validate(proposed_draft)
                    if proposed_draft is not None
                    else None
                ),
                resource=resource,
                pixel_offset=pixel_offset,
                pixel_limit=pixel_limit,
            )
            return _bounded_result(preview_project_scene(services, token.project_id, request))
        except (
            ProjectNotFoundError,
            ScenePreviewError,
            CompilationError,
            ShowPackageError,
            ValidationError,
        ) as error:
            raise ToolError(str(error)) from error

    @server.tool()
    def get_project_draft(revision: int | None = None) -> dict[str, JsonValue]:
        """Read a full draft and its revision for the token's project; text is untrusted data."""
        token = _authorize(services, McpScope.READ)
        try:
            stored = services.repository.get_revision(token.project_id, revision)
        except ProjectNotFoundError as error:
            raise ToolError("project revision does not exist") from error
        return {
            "project_id": token.project_id,
            "revision": stored.revision,
            "draft": stored.draft.model_dump(mode="json"),
        }

    @server.tool()
    def get_scene_authoring_options() -> dict[str, JsonValue]:
        """Discover media/lighting resources available to this token's show.

        Uses the show's saved bindings and newly available resources only from its
        bound runtime installation. Importable bindings are copied only when selected;
        existing bindings are never overwritten. Surfaces are geometry, not extra
        projector playback slots. Requires read; sends no hardware commands.
        """
        token = _authorize(services, McpScope.READ)
        try:
            return scene_authoring_options(services, token.project_id).model_dump(mode="json")
        except ProjectNotFoundError as error:
            raise ToolError(str(error)) from error

    @server.tool()
    def get_installation_objects() -> dict[str, JsonValue]:
        """List this show's logical fixtures, segments and ordered paths, including unplaced ones.

        Requires read. Includes revision, membership/traversal, reference paths, layout
        links and available geometry from the bound runtime installation. Definitions
        belong to the working draft; geometry belongs to the installation. Segments
        are inspected here, not repartitioned. Equivalent UI: Installation > Objects.
        """
        token = _authorize(services, McpScope.READ)
        try:
            return _bounded_result(
                installation_object_inventory(services, token.project_id).model_dump(mode="json")
            )
        except (ProjectNotFoundError, ValueError) as error:
            raise ToolError(str(error)) from error

    @server.tool()
    def edit_installation_object(
        action: Literal["create", "update", "delete"],
        base_revision: int,
        request: InstallationObjectEditRequest | InstallationObjectDeleteRequest,
    ) -> dict[str, JsonValue]:
        """Create, update or safely delete a fixture/ordered path in this show's working draft.

        Requires direct_edit and the current base_revision from get_installation_objects.
        IDs stay stable; update cannot rename them. Validates unique known member segments,
        reversal and fixture precedence. Deletion refuses effect/authoring references.
        Optional geometry_semantic_ids link existing geometry in the bound installation;
        clearing links or deleting a definition never deletes installation geometry.
        Does not edit wiring, place new geometry, publish, apply or send hardware commands.
        Equivalent UI: Installation > Objects > properties and layout links.
        """
        token = _authorize(services, McpScope.DIRECT_EDIT)
        try:
            updated = save_installation_object(
                services,
                project_id=token.project_id,
                base_revision=base_revision,
                request=request,
                action=action,
                actor=token.username,
                role=_token_role(services, token),
            )
        except (ProjectNotFoundError, ValueError, RevisionConflictError) as error:
            raise ToolError(str(error)) from error
        _audit(
            services,
            token,
            f"mcp.installation.object.{action}",
            request.id,
            "success",
            details={"base_revision": base_revision, "created_revision": updated.revision},
        )
        return {
            "project_id": token.project_id,
            "revision": updated.revision,
            "hardware_commands_sent": False,
            "requires_human_approval": False,
        }

    @server.tool()
    def create_scene(base_revision: int, request: CreateSceneRequest) -> dict[str, JsonValue]:
        """Add a saved scene with one initial media or lighting output (direct_edit).

        Read get_scene_authoring_options first. Media-only scenes are supported.
        Lighting starts with a full-duration blackout cue; assign media separately.
        Choose controller_timed or media_synchronized explicitly. Does not add program
        entries, surface compositions, publish, apply, or send hardware commands.
        """
        token = _authorize(services, McpScope.DIRECT_EDIT)
        try:
            updated = save_scene_edit(
                services,
                project_id=token.project_id,
                base_revision=base_revision,
                request=request,
                actor=token.username,
                role=_token_role(services, token),
            )
        except (ProjectNotFoundError, ValueError, RevisionConflictError) as error:
            raise ToolError(str(error)) from error
        _audit(
            services,
            token,
            "mcp.scene.create",
            request.scene_id,
            "success",
            details={"base_revision": base_revision, "created_revision": updated.revision},
        )
        return {
            "project_id": token.project_id,
            "scene_id": request.scene_id,
            "output_id": request.output.output_id,
            "base_revision": base_revision,
            "revision": updated.revision,
            "hardware_commands_sent": False,
            "requires_human_approval": False,
        }

    @server.tool()
    def add_scene_output(
        scene_id: str, base_revision: int, request: AddSceneOutputRequest
    ) -> dict[str, JsonValue]:
        """Add a saved media or lighting output and its resource claim (direct_edit).

        Discover resources with get_scene_authoring_options. Preserves existing
        content, bindings and clock unless make_clock_source explicitly selects the
        new required media output. Converts v1 to v2 using existing IDs media/lighting.
        Refuses duplicate output IDs, resources or media agents. New lighting starts
        black; new media awaits asset placement. No publish, apply or hardware commands.
        """
        token = _authorize(services, McpScope.DIRECT_EDIT)
        try:
            updated = save_scene_edit(
                services,
                project_id=token.project_id,
                scene_id=scene_id,
                base_revision=base_revision,
                request=request,
                actor=token.username,
                role=_token_role(services, token),
            )
        except (ProjectNotFoundError, ValueError, RevisionConflictError) as error:
            raise ToolError(str(error)) from error
        _audit(
            services,
            token,
            "mcp.scene.output.add",
            scene_id,
            "success",
            details={"base_revision": base_revision, "created_revision": updated.revision},
        )
        return {
            "project_id": token.project_id,
            "scene_id": scene_id,
            "output_id": request.output_id,
            "base_revision": base_revision,
            "revision": updated.revision,
            "hardware_commands_sent": False,
            "requires_human_approval": False,
        }

    def _change_scene_output(
        scene_id: str,
        base_revision: int,
        request: RenameSceneOutputRequest | RemoveSceneOutputRequest,
        action: str,
    ) -> dict[str, JsonValue]:
        token = _authorize(services, McpScope.DIRECT_EDIT)
        try:
            updated = save_scene_output_change(
                services,
                project_id=token.project_id,
                scene_id=scene_id,
                base_revision=base_revision,
                request=request,
                actor=token.username,
                role=_token_role(services, token),
            )
        except (ProjectNotFoundError, ValueError, RevisionConflictError) as error:
            raise ToolError(str(error)) from error
        _audit(
            services,
            token,
            action,
            scene_id,
            "success",
            details={
                "base_revision": base_revision,
                "created_revision": updated.revision,
                "output_id": request.output_id,
            },
        )
        return {
            "project_id": token.project_id,
            "scene_id": scene_id,
            "output_id": request.output_id,
            "base_revision": base_revision,
            "revision": updated.revision,
            "hardware_commands_sent": False,
            "requires_human_approval": False,
        }

    @server.tool()
    def rename_scene_output(
        scene_id: str, output_id: str, new_output_id: str, base_revision: int
    ) -> dict[str, JsonValue]:
        """Rename a media or lighting output's stable ID in one revision (direct_edit).

        Updates the scene clock source and the scene's media layer, synchronized cue and
        sync schedule track references (media:<id> / lighting:<id>). Refuses duplicate IDs.
        Upgrades v1 scenes to v2 (IDs media/lighting) first. No publish, apply or hardware.
        """
        try:
            request = RenameSceneOutputRequest(output_id=output_id, new_output_id=new_output_id)
        except ValueError as error:
            raise ToolError(str(error)) from error
        return _change_scene_output(scene_id, base_revision, request, "mcp.scene.output.rename")

    @server.tool()
    def remove_scene_output(
        scene_id: str, output_id: str, base_revision: int
    ) -> dict[str, JsonValue]:
        """Remove a media or lighting output from a scene (direct_edit).

        Refuses the scene's last output, the media clock source of a media-synchronized
        scene, and outputs still used by media layers or synchronized cues; the error
        lists what to change first. Drops the resource claim when no other output uses it.
        Never deletes assets or other work. No publish, apply or hardware commands.
        """
        return _change_scene_output(
            scene_id,
            base_revision,
            RemoveSceneOutputRequest(output_id=output_id),
            "mcp.scene.output.remove",
        )

    @server.tool()
    def delete_scene(scene_id: str, base_revision: int) -> dict[str, JsonValue]:
        """Delete one saved scene using direct_edit and the current base_revision.

        Refuses the last scene or incoming fallback, completion, program, visitor
        response or encounter references; update those references first. Removes
        scene-owned editor metadata, retaining reusable compositions, presets,
        media assets and revision history. Does not publish or change live output.
        """
        token = _authorize(services, McpScope.DIRECT_EDIT)
        try:
            request = DeleteSceneRequest(scene_id=scene_id, base_revision=base_revision)
            stored = services.repository.get_revision(token.project_id, request.base_revision)
            if stored.project.archived_at is not None:
                raise ValueError("Archived shows cannot be edited.")
            candidate = delete_draft_scene(stored.draft, request.scene_id)
            updated = services.mcp_authoring.update_project_draft(
                project_id=token.project_id,
                base_revision=request.base_revision,
                proposed_document=candidate.model_dump(mode="json"),
                username=token.username,
                role=_token_role(services, token),
            )
        except (ProjectNotFoundError, ValueError, McpAuthoringError) as error:
            raise ToolError(str(error)) from error
        _audit(
            services,
            token,
            "mcp.scene.delete",
            scene_id,
            "success",
            details={"base_revision": base_revision, "created_revision": updated.revision},
        )
        return {
            "project_id": token.project_id,
            "scene_id": scene_id,
            "base_revision": base_revision,
            "revision": updated.revision,
            "hardware_commands_sent": False,
            "requires_human_approval": False,
        }

    @server.tool()
    def update_project_draft(
        base_revision: int,
        draft: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Save a complete working draft immediately, without proposal approval.

        Requires direct_edit. Read get_project_draft and get_authoring_schema first.
        The base revision must still be current; never overwrite a conflict blindly.
        Schema validation applies; publication checks run separately. Does not publish
        or affect the running show.
        """
        token = _authorize(services, McpScope.DIRECT_EDIT)
        try:
            updated = services.mcp_authoring.update_project_draft(
                project_id=token.project_id,
                base_revision=base_revision,
                proposed_document=draft,
                username=token.username,
                role=_token_role(services, token),
            )
        except McpAuthoringError as error:
            raise ToolError(str(error)) from error
        paths = services.publication.diff(token.project_id, base_revision, updated.revision)
        _audit(
            services,
            token,
            "mcp.draft.update",
            token.project_id,
            "success",
            details={
                "base_revision": base_revision,
                "created_revision": updated.revision,
                "changed_paths": list(paths),
            },
        )
        return {
            "project_id": token.project_id,
            "base_revision": base_revision,
            "revision": updated.revision,
            "changed_paths": list(paths),
            "requires_human_approval": False,
        }

    @server.tool()
    async def apply_saved_show(request: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Prepare, verify, transfer and load an exact saved show into the real controller.

        Live operation: requires publish AND activate, current publisher/admin and
        live-output grant, installation binding and explicit interruption confirmation.
        Approval is explicit for this revision only. Playing restarts the selected/
        starting scene from zero; paused/stopped stays stopped. Inspect the durable
        job and observed loaded identity, not an artifact activation label.
        """
        token = _authorize(services, McpScope.ACTIVATE)
        try:
            parsed = ApplyRequest.model_validate(request)
            if parsed.restore_package_id is None:
                _authorize(services, McpScope.PUBLISH)
            job = await services.apply.start(token.project_id, parsed, token.username, token.id)
        except (ValidationError, ApplyError, ProjectNotFoundError) as error:
            raise ToolError(str(error)) from error
        return _bounded_result(job)

    @server.tool()
    def get_apply_jobs(job_id: str | None = None) -> dict[str, JsonValue]:
        """Read durable Apply/recovery progress for the token project; requires read."""
        token = _authorize(services, McpScope.READ)
        if job_id is None:
            return _bounded_result({"jobs": services.apply.list(token.project_id)})
        try:
            job = services.apply.get(job_id)
        except ApplyError as error:
            raise ToolError(str(error)) from error
        if job["project_id"] != token.project_id:
            raise ToolError("Apply job is outside the token project")
        return _bounded_result(job)

    @server.tool()
    def cancel_apply(job_id: str) -> dict[str, JsonValue]:
        """Request cancellation before interruption; activate/live authorization required."""
        token = _authorize(services, McpScope.ACTIVATE)
        try:
            job = services.apply.get(job_id)
            if job["project_id"] != token.project_id or not services.apply.authorize(
                token.project_id, token.username, token.id, True
            ):
                raise ToolError("Apply cancellation is outside this live-output grant")
            return _bounded_result(services.apply.cancel(job_id))
        except ApplyError as error:
            raise ToolError(str(error)) from error

    @server.tool()
    def get_interactions() -> dict[str, JsonValue]:
        """Read visitor responses, state steps and normal-playback groups; read scope."""
        token = _authorize(services, McpScope.READ)
        stored = services.repository.get_revision(token.project_id)
        return _bounded_result(
            {"source_revision": stored.revision, **describe_interactions(stored.draft)}
        )

    @server.tool()
    def update_interaction(edit: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Losslessly edit guided responses/shared visitor rules or a state step's scene.

        Requires direct_edit and a current source_revision. Shared completion changes
        require prepare_completion; advanced matching/state choreography is preserved.
        Saves only: never publishes, applies, injects live input or commands hardware.
        """
        token = _authorize(services, McpScope.DIRECT_EDIT)
        try:
            request = InteractionEditRequest.model_validate(edit)
            stored = services.repository.get_revision(token.project_id)
            if stored.revision != request.source_revision:
                raise InteractionError("Source revision changed; reload before editing")
            candidate = apply_interaction_edit(stored.draft, request)
            updated = services.mcp_authoring.update_project_draft(
                project_id=token.project_id,
                base_revision=stored.revision,
                proposed_document=candidate.model_dump(mode="json"),
                username=token.username,
                role=_token_role(services, token),
            )
        except (ValidationError, InteractionError, McpAuthoringError) as error:
            raise ToolError(str(error)) from error
        return {
            "project_id": token.project_id,
            "revision": updated.revision,
            "hardware_commands_sent": False,
        }

    @server.tool()
    def get_show_plan() -> dict[str, JsonValue]:
        """Read how scenes are put together: starting scene, main-show order, scene
        endings, shuffle list (default program pool), visitor responses and unused scenes.
        Read scope. Plain-language projection of program/completion/opportunity data.
        """
        token = _authorize(services, McpScope.READ)
        stored = services.repository.get_revision(token.project_id)
        return _bounded_result(
            {"source_revision": stored.revision, **describe_show_plan(stored.draft)}
        )

    @server.tool()
    def update_show_plan(edit: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Set the starting scene, one scene's ending, or the shuffle list.

        Requires direct_edit and a current source_revision. Endings map to scene
        completion: repeat=loop, play_next=transition_to, shuffle=select_next,
        freeze=hold, stop=stop. Shuffle-list edits preserve existing cooldowns,
        conditions and availability. Saves only: never publishes, applies or
        commands hardware.
        """
        token = _authorize(services, McpScope.DIRECT_EDIT)
        try:
            request = ShowPlanEditRequest.model_validate(edit)
            stored = services.repository.get_revision(token.project_id)
            if stored.revision != request.source_revision:
                raise ShowPlanError("Source revision changed; reload before editing")
            candidate = apply_show_plan_edit(stored.draft, request)
            updated = services.mcp_authoring.update_project_draft(
                project_id=token.project_id,
                base_revision=stored.revision,
                proposed_document=candidate.model_dump(mode="json"),
                username=token.username,
                role=_token_role(services, token),
            )
        except (ValidationError, ValueError, McpAuthoringError) as error:
            raise ToolError(str(error)) from error
        return {
            "project_id": token.project_id,
            "revision": updated.revision,
            "hardware_commands_sent": False,
        }

    @server.tool()
    async def preview_interaction_workflow(request: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Run a bounded isolated visitor test with production qualification/arbitration/rendering.

        Requires simulation. Read/proposed candidates never change the saved show or
        live controller. Returns action checkpoints, scene changes, waiting/expiry/
        cooldown evidence, and one sampled frame. No real adapters or commands.
        """
        token = _authorize(services, McpScope.SIMULATION)
        try:
            parsed = InteractionPreviewRequest.model_validate(request)
            result = await preview_interactions(services, token.project_id, parsed)
        except (
            ValidationError,
            InteractionError,
            CompilationError,
            ShowPackageError,
            ValueError,
            LookupError,
        ) as error:
            raise ToolError(str(error)) from error
        return _bounded_result(result)

    @server.tool()
    def get_capabilities() -> dict[str, JsonValue]:
        """Discover scene timing, authoring/deletion, preview/bake tools and grants."""
        token = _authorize(services, McpScope.READ)
        revision = services.repository.get_revision(token.project_id)
        return cast(
            dict[str, JsonValue],
            {
                "project_id": token.project_id,
                "mcp_contract_version": 2,
                "revision": revision.revision,
                "installation_id": revision.draft.editor_metadata.get("installation_id"),
                "granted_scopes": [scope.value for scope in token.scopes],
                "runtime_apply": {
                    "tool": "apply_saved_show",
                    "status_tool": "get_apply_jobs",
                    "cancel_tool": "cancel_apply",
                    "scopes": ["publish", "activate"],
                    "retained_recovery_scope": "activate",
                    "request_schema": ApplyRequest.model_json_schema(),
                    "approval": "explicit_exact_revision",
                    "save_applies_live": False,
                    "loaded_identity": "controller_acknowledged_catalog_generation",
                },
                "output_monitor": {
                    "tool": "get_output_monitor",
                    "scope": "read",
                    "request_schema": OutputMonitorRequest.model_json_schema(),
                    "capture_maximum_hz": 5,
                    "gui_poll_maximum_hz": 2,
                    "maximum_resources": 32,
                    "maximum_samples": 4096,
                    "source": "successful_controller_sink_frames",
                    "physical_output_verified": False,
                    "hardware_commands_sent": False,
                    "requires_loaded_project_match": True,
                },
                "live_transport": {
                    "tool": "operate_loaded_show",
                    "scope": "live_operation",
                    "request_schema": OperateActionRequest.model_json_schema(),
                    "physical_confirmation_required": True,
                    "requires_loaded_project_match": True,
                    "authorization": "current_operator_or_administrator_and_live_output_grant",
                },
                "installation_setup": {
                    "geometry_owner": "installation",
                    "photo_optional": True,
                    "photo_removal": "detach_reference_preserve_geometry_wiring_and_calibration",
                    "replacement_tool": "replace_installation_photo",
                    "replacement_scope": "photo_replace",
                    "replacement_schema": ReplaceInstallationPhotoRequest.model_json_schema(),
                    "available_for_project": (
                        revision.draft.editor_metadata.get("installation_id")
                        == services.repository.get_runtime_installation().id
                    ),
                    "read_tool": "get_installation_setup",
                    "description_tool": "describe_installation_photo",
                    "description_scope": "installation_edit",
                    "description_schema": PhotoDescriptionRequest.model_json_schema(),
                    "description_authority": "legacy_photo_name",
                    "viewpoint_identity_preserved": True,
                    "geometry_editing": False,
                    "stage_reference": {
                        "read_tool": "get_stage_map",
                        "update_tool": "update_stage_reference",
                        "update_scope": "installation_edit",
                        "request_schema": StageReferenceRequest.model_json_schema(),
                        "statuses": ["placed", "needs_update", "manual", "unplaced"],
                        "coordinates": (
                            "stage = layout point x 1000 x units_per_canvas, "
                            "y x 600 x units_per_canvas"
                        ),
                        "writes": (
                            "geometry stage points, LED stage transforms and projector "
                            "stage homographies only; manually placed projectors are skipped"
                        ),
                    },
                    "projector_stage_placement": {
                        "modes": ["surface", "manual"],
                        "default": "surface",
                        "read_tools": ["get_stage_map", "get_spatial_layout"],
                        "write_surface": "administrator_gui_only",
                        "surface_basis_order": [
                            "stage_view",
                            "stage_points",
                            "default_scale",
                        ],
                        "semantics": (
                            "surface placement derives each projector's stage extent and "
                            "homography from its drawn projector surface and re-derives it "
                            "when the surface is redrawn; manual keeps an entered rectangle. "
                            "Shared-stage compositions sample each projector from this "
                            "placement."
                        ),
                    },
                    "device_writes": "administrator_gui_only",
                    "safe_defaults": {"layout_tool": "select", "details": "contextual"},
                },
                "interactions": {
                    "read_tool": "get_interactions",
                    "edit_tool": "update_interaction",
                    "preview_tool": "preview_interaction_workflow",
                    "edit_scope": "direct_edit",
                    "preview_scope": "simulation",
                    "edit_schema": InteractionEditRequest.model_json_schema(),
                    "preview_schema": InteractionPreviewRequest.model_json_schema(),
                    "limits": {
                        "steps": 200,
                        "duration_ms": 600000,
                        "samples": 4096,
                        "pixel_work": 20000000,
                    },
                    "preview_hardware_commands": False,
                },
                "show_plan": {
                    "read_tool": "get_show_plan",
                    "edit_tool": "update_show_plan",
                    "edit_scope": "direct_edit",
                    "edit_schema": ShowPlanEditRequest.model_json_schema(),
                    "endings": {
                        "repeat": "loop",
                        "play_next": "transition_to",
                        "shuffle": "select_next",
                        "freeze": "hold",
                        "stop": "stop",
                    },
                    "shuffle_list": "program default pool",
                    "preview_tool": "preview_interaction_workflow",
                    "hardware_commands_sent": False,
                },
                "input_source": {
                    "available_for_project": (
                        revision.draft.editor_metadata.get("installation_id")
                        == services.repository.get_runtime_installation().id
                    ),
                    "integration": "frigate_mqtt",
                    "max_payload_bytes": MAX_INPUT_PAYLOAD_BYTES,
                    "recent_event_limit": MAX_INPUT_RECENT_EVENTS,
                    "read_tool": "get_input_source",
                    "configure_tool": "configure_input_source",
                    "configure_scope": "input_config",
                    "configure_role": "global_administrator",
                    "management": services.input_source.mode,
                    "validation_tool": "validate_input_source_payload",
                    "validation_scope": "simulation",
                    "configuration_schema": ConfigureInputSourceRequest.model_json_schema(),
                    "secrets": "write_only_controller_local_not_exported",
                    "configuration_can_trigger_loaded_show": True,
                    "validation_sends_hardware_commands": False,
                },
                "composition_spaces": ["lighting_surface", "projector_native", "stage"],
                "features": {
                    "installation_objects": True,
                    "spatial_layout": True,
                    "projector_geometry": True,
                    "shared_stage_overlays": True,
                    "stage_reference": True,
                    "image_video_gif": True,
                    "mixed_output_bakes": True,
                    "controller_timed_scenes": True,
                    "led_only_scenes": True,
                    "scene_lighting_preview": True,
                    "scene_deletion": True,
                    "scene_creation": True,
                    "scene_output_creation": True,
                    "scene_output_rename": True,
                    "scene_output_removal": True,
                    "media_only_scenes": True,
                    "geometry_editing": False,
                    "live_media_rendering": False,
                },
                "scene_timing": {
                    "media_synchronized": {
                        "clock_source": "required_media_output_id",
                        "requires_media": True,
                    },
                    "controller_timed": {
                        "schema_version": 2,
                        "clock_source": "controller",
                        "requires_media": False,
                        "starts_after_prepare": True,
                        "completion": "duration_ms",
                        "transport": ["pause", "resume", "seek", "stop"],
                    },
                    "automatic_clock_failover": False,
                    "media_drift_correction": False,
                },
                "tools": {
                    "installation_objects": "get_installation_objects",
                    "stage_map": "get_stage_map",
                    "update_stage_reference": "update_stage_reference",
                    "edit_installation_object": "edit_installation_object",
                    "layout": "get_spatial_layout",
                    "schema": "get_authoring_schema",
                    "read_draft": "get_project_draft",
                    "propose": "propose_project_revision",
                    "direct_edit": "update_project_draft",
                    "delete_scene": "delete_scene",
                    "scene_authoring_options": "get_scene_authoring_options",
                    "create_scene": "create_scene",
                    "add_scene_output": "add_scene_output",
                    "rename_scene_output": "rename_scene_output",
                    "remove_scene_output": "remove_scene_output",
                    "preview": "preview_composition",
                    "scene_lighting_preview": "preview_scene_lighting",
                    "bake": "create_render_job",
                    "bake_list": "get_render_jobs",
                    "bake_status": "get_render_job",
                    "bake_cancel": "cancel_render_job",
                },
                "authoring_representations": {
                    "runtime_scene_media": {
                        "read_tool": "get_project_draft",
                        "write_tool": "update_project_draft",
                        "write_requires_scope": "direct_edit",
                        "schema": ["MediaDefinition", "MediaOutputDefinition"],
                        "editor_metadata_required": False,
                        "ui_workflow": "Scenes > Edit timeline > select media clip > Inspector",
                    },
                    "runtime_scene_lighting": {
                        "read_tool": "get_project_draft",
                        "write_tool": "update_project_draft",
                        "write_requires_scope": "direct_edit",
                        "schema": ["LightingDefinition", "LightingOutputDefinition"],
                        "timeline_cue_schema": "LightingCue",
                        "editor_metadata_required": False,
                    },
                    "editor_timeline": {
                        "range_tool": "get_timeline_range",
                        "source": "editor_metadata",
                        "includes_runtime_scene_lighting": False,
                        "includes_runtime_scene_media": False,
                    },
                },
                "runtime_status": {
                    "read_tool": "get_runtime_status",
                    "requires_scope": "read",
                    "available_for_project": revision.draft.editor_metadata.get("installation_id")
                    == services.repository.get_runtime_installation().id,
                    "source": "controller_runtime",
                    "recommended_refresh_ms": 2000,
                    "stale_after_ms": 10000,
                    "loaded_catalog_identity": "unreported",
                    "connectivity_is_readiness": False,
                },
                "scene_lighting_preview": {
                    "tool": "preview_scene_lighting",
                    "requires_scope": "simulation",
                    "saved_and_unsaved_candidates": True,
                    "hardware_commands_sent": False,
                    "max_samples": 4096,
                    "max_installation_pixels": 65536,
                    "max_index_work": 2000000,
                    "concurrent_evaluations": 2,
                    "spatial_compositions": "use preview_composition",
                    "evaluation": "production_lighting_renderer",
                },
                "permissions": {
                    "preview": "simulation",
                    "bake_and_cancel": ["direct_edit", "publish"],
                    "can_preview": McpScope.SIMULATION in token.scopes,
                    "can_bake": any(
                        scope in token.scopes for scope in (McpScope.DIRECT_EDIT, McpScope.PUBLISH)
                    ),
                },
                "workflow": [
                    "Choose media_synchronized or controller_timed scene time; LED-only "
                    "scenes use schema v2, clock_source controller, media [] and lighting claims.",
                    "Read layout, schema, assets and current draft.",
                    "Author media once in stage space; stage_lighting_surfaces selects LED IDs.",
                    "Propose and human-merge changes, or save with explicit direct_edit.",
                    "Preview the saved draft or an isolated proposal without hardware output.",
                    "Queue a bake for the saved revision and poll its job until completed.",
                    "Publish the matching LED sequences and projector videos with publish scope.",
                ],
                "limitations": [
                    "Controller-timed media receives transport commands, but playback telemetry "
                    "does not drive scene time; no live scene transport is exposed over MCP.",
                    "Calibration editing remains in the GUI; geometry tools are read-only.",
                    "Baking sends no hardware commands and does not approve or merge proposals.",
                    "Controller package handover/restart and hardware latency rehearsal "
                    "remain required.",
                ],
                "resources": [
                    resource.model_dump(mode="json")
                    for resource in revision.draft.installation.resources
                ],
            },
        )

    @server.tool()
    def get_spatial_layout(
        pixel_offset: int = 0,
        pixel_limit: int = 0,
        include_photo_geometry: bool = False,
    ) -> dict[str, JsonValue]:
        """Read LED/projector stage extents, meshes, calibration and optional photo geometry.

        Each projector reports stage_placement: surface (follows its drawn surface) or
        manual (entered rectangle). Logical LED samples are opt-in/paginated (maximum
        500). Geometry is read-only.
        """
        token = _authorize(services, McpScope.READ)
        revision = services.repository.get_revision(token.project_id)
        try:
            context = spatial_context(
                revision.draft,
                _spatial_mapper(services, revision.draft),
                pixel_offset=pixel_offset,
                pixel_limit=pixel_limit,
            )
        except McpAuthoringError as error:
            raise ToolError(str(error)) from error
        result: dict[str, object] = {
            "project_id": token.project_id,
            "revision": revision.revision,
            **context,
        }
        if include_photo_geometry:
            installation_id = revision.draft.editor_metadata["installation_id"]
            if not isinstance(installation_id, str):
                raise ToolError("project has no mapped installation")
            result["photos"] = [
                {
                    "id": photo.id,
                    "name": photo.name,
                    "viewpoint": photo.viewpoint,
                    "revision": photo.revision,
                }
                for photo in services.installation_photos.list(installation_id)
            ]
            result["photo_geometry"] = [
                asdict(geometry)
                for geometry in services.installation_geometry.list(installation_id)
            ]
        return _bounded_result(result)

    @server.tool()
    def get_spatial_effect_catalog() -> dict[str, object]:
        """Read typed procedural effect schemas; never invent effect fields or distance units."""
        _authorize(services, McpScope.READ)
        return spatial_effect_catalog()

    @server.tool()
    def propose_spatial_effect(
        base_revision: int,
        title: str,
        expected_layout_digest: str,
        composition_ids: list[str],
        effect: SpatialRipple,
        start_ms: int,
        duration_ms: int,
        blend_mode: Literal["replace", "add", "max", "multiply"] = "add",
        opacity: float = 1,
    ) -> dict[str, JsonValue]:
        """Propose a geometric ripple without changing the draft or sending hardware commands."""
        token = _authorize(services, McpScope.DRAFT_EDIT)
        revision = services.repository.get_revision(token.project_id)
        if revision.revision != base_revision:
            raise McpAuthoringError("working revision changed; fetch project context again")
        proposed = propose_effect_draft(
            revision.draft,
            _spatial_mapper(services, revision.draft),
            expected_layout_digest=expected_layout_digest,
            composition_ids=composition_ids,
            effect=effect,
            start_ms=start_ms,
            duration_ms=duration_ms,
            blend_mode=blend_mode,
            opacity=opacity,
        )
        proposal = services.mcp_authoring.propose_project_revision(
            project_id=token.project_id,
            base_revision=base_revision,
            title=title,
            kind="timeline_change",
            proposed_document=proposed.model_dump(mode="json"),
            client_id=token.client_id,
            username=token.username,
        )
        _audit(services, token, "mcp.proposal.create", proposal.id, "success")
        return _proposal_result(proposal)

    @server.tool()
    def preview_spatial_proposal(
        proposal_id: str,
        composition_id: str,
        timestamp_ms: int,
    ) -> CallToolResult:
        """Preview isolated lighting as a logical LED image; never send live output."""
        token = _authorize(services, McpScope.SIMULATION)
        proposal = _proposal_for_token(services, token, proposal_id)
        current = services.repository.get_revision(token.project_id)
        if current.revision != proposal.base_revision:
            raise McpAuthoringError("proposal is stale relative to the working revision")
        draft = proposal.proposed_draft
        if draft.editor_metadata.get("installation_id") != current.draft.editor_metadata.get(
            "installation_id"
        ):
            raise McpAuthoringError("proposal cannot preview another installation")
        mapper = _spatial_mapper(services, current.draft)
        expected = draft.editor_metadata.get("spatial_layout_digest")
        if expected is not None and expected != layout_digest(mapper):
            raise McpAuthoringError("installation changed since proposal creation")
        preview = evaluate_lighting_preview(
            project_id=token.project_id,
            draft=draft,
            composition_id=composition_id,
            timestamp_ms=timestamp_ms,
            assets=services.asset_upload,
            mapper_export=mapper,
        )
        if len(json.dumps(preview).encode()) > 200 * 1024:
            raise McpAuthoringError("preview exceeds the 200 KiB response budget")
        surface = resolve_lighting_preview_surface(
            draft=draft,
            composition_id=composition_id,
            mapper_export=mapper,
        )
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(preview)),
                ImageContent(
                    type="image",
                    mime_type="image/png",
                    data=base64.b64encode(lighting_preview_png(preview, surface)).decode("ascii"),
                ),
            ],
            structured_content=preview,
        )

    @server.tool()
    def get_authoring_schema() -> dict[str, Any]:
        """Read scene timing and draft/composition/media schemas, including LED-only scenes.

        Schema-v2 controller_timed scenes use clock_source controller and may use media [].
        Media-synchronized scenes retain a required media clock output.
        """
        _authorize(services, McpScope.READ)
        return authoring_schema()

    @server.tool()
    def preview_composition(
        composition_id: str,
        timestamp_ms: int,
        resource: str | None = None,
        proposal_id: str | None = None,
        pixel_offset: int = 0,
        pixel_limit: int = 100,
    ) -> CallToolResult:
        """Preview shared-stage, projector or LED content as PNG plus sample diagnostics.

        Requires simulation; sends no hardware commands. Stage previews without a resource
        show the shared canvas and LED samples; supply a mapped projector resource for its
        warped output. Optional proposals stay isolated and must match current revision/layout.
        """
        token = _authorize(services, McpScope.SIMULATION)
        if pixel_offset < 0 or not 0 <= pixel_limit <= 500:
            raise ToolError("pixel offset must be nonnegative and limit must be 0..500")
        current = services.repository.get_revision(token.project_id)
        draft = current.draft
        if proposal_id is not None:
            proposal = _proposal_for_token(services, token, proposal_id)
            if proposal.base_revision != current.revision:
                raise ToolError("proposal is stale relative to the working revision")
            draft = proposal.proposed_draft
            if draft.editor_metadata.get("installation_id") != current.draft.editor_metadata.get(
                "installation_id"
            ):
                raise ToolError("proposal cannot preview another installation")
        raw = draft.editor_metadata.get("compositions", {})
        composition = raw.get(composition_id) if isinstance(raw, dict) else None
        if not isinstance(composition, dict):
            raise ToolError("composition does not exist")
        settings_key = (
            "lighting_settings"
            if composition.get("space_kind") == "lighting_surface"
            else "render_settings"
        )
        settings = composition.get(settings_key)
        if not isinstance(settings, dict):
            raise ToolError("composition has no render settings")
        width_key, height_key = (
            ("canvas_width", "canvas_height")
            if settings_key == "lighting_settings"
            else ("render_width", "render_height")
        )
        width, height = settings.get(width_key), settings.get(height_key)
        if not isinstance(width, int) or not isinstance(height, int) or width * height > 2097152:
            raise ToolError("MCP preview exceeds the 2 megapixel budget; lower render resolution")
        diagnostics: dict[str, object] = {
            "project_id": token.project_id,
            "revision": current.revision,
            "composition_id": composition_id,
            "timestamp_ms": timestamp_ms,
            "proposal_id": proposal_id,
            "hardware_command_sent": False,
        }
        try:
            space_kind = composition.get("space_kind")
            if space_kind == "projector_native":
                if resource is not None and resource != composition.get("target_id"):
                    raise ToolError("resource is not the composition's projector")
                frame = preview_projector_composition(
                    project_id=token.project_id,
                    draft=draft,
                    composition_id=composition_id,
                    timestamp_ms=timestamp_ms,
                    assets=services.asset_upload,
                )
                image = Image.fromarray(frame)
            else:
                mapper = _spatial_mapper(services, current.draft)
                expected = draft.editor_metadata.get("spatial_layout_digest")
                if expected is not None and expected != layout_digest(mapper):
                    raise ToolError("installation changed since proposal creation")
                if space_kind == "stage":
                    if resource is not None:
                        projectors = cast(list[dict[str, object]], mapper.get("projectors", []))
                        native_pixels = sum(
                            int(cast(int, projector["native_width"]))
                            * int(cast(int, projector["native_height"]))
                            for projector in projectors
                        )
                        if native_pixels > 8388608:
                            raise ToolError(
                                "MCP projector preview exceeds the 8 megapixel output-set budget; "
                                "use a shared-canvas preview or the GUI"
                            )
                    sampled = preview_stage_lighting_samples(
                        project_id=token.project_id,
                        draft=draft,
                        composition_id=composition_id,
                        timestamp_ms=timestamp_ms,
                        assets=services.asset_upload,
                        mapper_export=mapper,
                        device_reported_scales={},
                        include_canvas=resource is None,
                    )
                    if resource is None:
                        image = Image.open(
                            BytesIO(
                                base64.b64decode(
                                    str(sampled.pop("canvas_png_base64")),
                                )
                            )
                        )
                    else:
                        image = Image.fromarray(
                            preview_stage_composition_member(
                                project_id=token.project_id,
                                source_revision=current.revision,
                                draft=draft,
                                composition_id=composition_id,
                                timestamp_ms=timestamp_ms,
                                assets=services.asset_upload,
                                mapper_export=mapper,
                                coverage=services.stage_coverage.analyze(
                                    cast(str, current.draft.editor_metadata["installation_id"]),
                                ),
                                overlaps=services.projector_overlap.list(
                                    cast(str, current.draft.editor_metadata["installation_id"]),
                                ),
                                resource=resource,
                            )
                        )
                    diagnostics.update(sampled)
                elif space_kind == "lighting_surface":
                    sampled = evaluate_lighting_preview(
                        project_id=token.project_id,
                        draft=draft,
                        composition_id=composition_id,
                        timestamp_ms=timestamp_ms,
                        assets=services.asset_upload,
                        mapper_export=mapper,
                        include_canvas=True,
                    )
                    if resource is not None and resource != sampled["surface_resource"]:
                        raise ToolError("resource is not the composition's lighting output")
                    image = Image.open(
                        BytesIO(
                            base64.b64decode(
                                str(sampled.pop("canvas_png_base64")),
                            )
                        )
                    )
                    diagnostics.update(sampled)
                else:
                    raise ToolError("unsupported composition space")
        except (
            ProjectorRenderError,
            LightingPreviewError,
            AssetUploadError,
            MapperExportError,
        ) as error:
            raise ToolError(str(error)) from error
        diagnostics["resource"] = resource
        _page_preview_samples(diagnostics, pixel_offset, pixel_limit)
        return _image_result(image, diagnostics)

    @server.tool()
    def create_render_job(base_revision: int, composition_id: str) -> dict[str, JsonValue]:
        """Queue a saved-revision projector/LED/mixed bake; requires direct_edit or publish.

        Does not merge/approve proposals, publish, activate, or send hardware commands.
        Save or human-merge edits first. Poll get_render_job until completed; stale inputs fail.
        """
        token = _authorize(services, McpScope.DIRECT_EDIT, McpScope.PUBLISH)
        try:
            job = services.render_jobs.create(
                project_id=token.project_id,
                composition_id=composition_id,
                actor=token.username,
                expected_revision=base_revision,
            )
        except (RenderJobError, ProjectNotFoundError, AssetUploadError, MapperExportError) as error:
            raise ToolError(str(error)) from error
        _audit(services, token, "mcp.render.create", job.id, "success")
        return _bounded_result({**asdict(job), "hardware_command_sent": False})

    @server.tool()
    def get_render_jobs(
        offset: int = 0,
        limit: int = 20,
        composition_id: str | None = None,
    ) -> dict[str, JsonValue]:
        """List paginated bake jobs for the token project, optionally filtered by composition."""
        token = _authorize(services, McpScope.READ)
        if offset < 0 or limit < 1 or limit > 100:
            raise ToolError("job offset must be nonnegative and limit must be 1..100")
        jobs = [
            job
            for job in services.render_jobs.list(token.project_id)
            if composition_id is None or job.composition_id == composition_id
        ]
        return _bounded_result(
            {
                "project_id": token.project_id,
                "total": len(jobs),
                "next_offset": offset + limit if offset + limit < len(jobs) else None,
                "jobs": [asdict(job) for job in jobs[offset : offset + limit]],
            }
        )

    @server.tool()
    def get_render_job(job_id: str, include_artifact: bool = False) -> dict[str, JsonValue]:
        """Read project-bound bake status, progress, errors and optional artifact manifest."""
        token = _authorize(services, McpScope.READ)
        job = _render_job_for_token(services, token, job_id)
        result = asdict(job)
        if include_artifact:
            if job.state != "completed" or job.artifact_id is None:
                raise ToolError("bake has no completed artifact")
            result["artifact"] = services.repository.get_render_artifact_manifest(job.artifact_id)
        return _bounded_result(result)

    @server.tool()
    def cancel_render_job(job_id: str) -> dict[str, JsonValue]:
        """Cancel a project bake; requires direct_edit or publish. No hardware output."""
        token = _authorize(services, McpScope.DIRECT_EDIT, McpScope.PUBLISH)
        _render_job_for_token(services, token, job_id)
        try:
            job = services.render_jobs.cancel(job_id)
        except RenderJobError as error:
            raise ToolError(str(error)) from error
        _audit(services, token, "mcp.render.cancel", job.id, "success")
        return _bounded_result(asdict(job))

    @server.tool()
    def list_asset_metadata(query: str = "", limit: int = 50) -> dict[str, JsonValue]:
        """List bounded asset metadata for the bound project; never returns storage paths."""
        token = _authorize(services, McpScope.READ)
        safe_limit = max(1, min(limit, MAX_ASSET_RESULTS))
        normalized = query.strip().casefold()
        assets = services.asset_upload.list_assets(token.project_id)
        selected = [
            asset
            for asset in assets
            if not normalized
            or normalized in asset.name.casefold()
            or any(normalized in tag.casefold() for tag in asset.tags)
        ][:safe_limit]
        return {
            "project_id": token.project_id,
            "assets": [
                {
                    "id": asset.id,
                    "name": asset.name,
                    "status": asset.status,
                    "media_type": asset.media_type,
                    "size_bytes": asset.size_bytes,
                    "owner": asset.owner,
                    "license": asset.license,
                    "review_status": asset.review_status,
                    "tags": list(asset.tags),
                    "metadata": {
                        key: value
                        for key, value in (asset.metadata or {}).items()
                        if key
                        in {
                            "width",
                            "height",
                            "duration_ms",
                            "frame_rate_numerator",
                            "frame_rate_denominator",
                            "frame_count",
                        }
                        and (value is None or isinstance(value, (int, float, str)))
                    },
                }
                for asset in selected
            ],
            "truncated": len(selected) < len(assets),
        }

    @server.tool()
    def get_timeline_range(
        scene_id: str,
        start_ms: int,
        end_ms: int,
    ) -> dict[str, JsonValue]:
        """Read editor markers, synchronized cues and composition layers in a time range.

        This reads editor_metadata, not raw runtime scene media or lighting.
        Use get_project_draft for scene media outputs, legacy effects and lighting.timeline cues.
        """
        token = _authorize(services, McpScope.READ)
        if start_ms < 0 or end_ms <= start_ms or end_ms - start_ms > MAX_TIMELINE_RANGE_MS:
            raise ToolError("timeline range is invalid or exceeds five minutes")
        revision = services.repository.get_revision(token.project_id)
        metadata = revision.draft.editor_metadata
        compositions = metadata.get("compositions", {})
        is_composition = isinstance(compositions, dict) and scene_id in compositions
        if not is_composition and not any(scene.id == scene_id for scene in revision.draft.scenes):
            raise ToolError("scene or composition does not exist")
        markers = _bounded_timeline_items(
            metadata.get("timeline_markers"),
            scene_id,
            start_ms,
            end_ms,
            "time_ms",
        )
        cues = _bounded_timeline_items(
            metadata.get("timeline_cues"),
            scene_id,
            start_ms,
            end_ms,
            "time_ms",
        )
        return _bounded_result(
            {
                "project_id": token.project_id,
                "revision": revision.revision,
                "scene_id": scene_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "markers": markers,
                "cues": cues,
                "transform_layers": _timeline_layers(
                    metadata.get("timeline_transform_layers"),
                    scene_id,
                    start_ms,
                    end_ms,
                ),
                "lighting_author_layers": _timeline_layers(
                    metadata.get("lighting_author_layers"),
                    scene_id,
                    start_ms,
                    end_ms,
                ),
            }
        )

    @server.tool()
    def get_publication_status() -> dict[str, JsonValue]:
        """Read release validation and immutable publication status."""
        token = _authorize(services, McpScope.PUBLISH_PREPARE, McpScope.PUBLISH, McpScope.ACTIVATE)
        revision = services.repository.get_revision(token.project_id)
        release = services.publication.release(token.project_id, revision.revision)
        diagnostics = services.publication.validate(token.project_id, revision.revision)
        packages = services.publication.list(token.project_id)
        return {
            "project_id": token.project_id,
            "working_revision": revision.revision,
            "release": {
                "name": release.name,
                "summary": release.summary,
                "approval_state": release.approval_state,
                "approved_by": release.approved_by,
                "approved_at": release.approved_at,
            },
            "validation": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "message": item.message,
                }
                for item in diagnostics
            ],
            "packages": [
                {
                    "id": package.id,
                    "revision": package.revision,
                    "state": package.state,
                    "digest_sha256": package.digest_sha256,
                }
                for package in packages
            ],
        }

    @server.tool()
    def update_revision_release(
        revision: int,
        approval_state: ApprovalState,
        name: str | None = None,
        summary: str | None = None,
    ) -> dict[str, JsonValue]:
        """Set release metadata/approval for an explicit revision; requires publish.

        Approving a release authorizes its later publication, not proposal merge.
        It does not publish, stage, activate, or bypass publication validation.
        """
        token = _authorize(services, McpScope.PUBLISH)
        try:
            body = UpdateRevisionReleaseRequest(
                name=name, summary=summary, approval_state=approval_state
            )
            release = services.publication.update_release(
                project_id=token.project_id,
                revision=revision,
                name=body.name,
                summary=body.summary,
                approval_state=body.approval_state,
                actor=token.username,
                occurred_at=_now_iso(),
            )
        except (ProjectNotFoundError, ValidationError) as error:
            raise ToolError(str(error)) from error
        _audit(
            services,
            token,
            "mcp.revision.release.update",
            f"{token.project_id}@{revision}",
            "success",
            details={"revision": revision, "approval_state": release.approval_state},
        )
        return {
            "project_id": token.project_id,
            "revision": revision,
            "name": release.name,
            "summary": release.summary,
            "approval_state": release.approval_state,
            "approved_by": release.approved_by,
            "approved_at": release.approved_at,
        }

    @server.tool()
    def publish_revision(revision: int) -> dict[str, JsonValue]:
        """Publish an explicitly approved revision as an immutable package.

        Requires publish and all existing publication validation. Does not stage,
        activate, or replace the running controller's scene catalog.
        """
        token = _authorize(services, McpScope.PUBLISH)
        try:
            package = services.publication.publish(
                project_id=token.project_id,
                revision=revision,
                actor=token.username,
                occurred_at=_now_iso(),
            )
        except (ProjectNotFoundError, PublicationError, CompilationError) as error:
            raise ToolError(str(error)) from error
        _audit(
            services,
            token,
            "mcp.package.publish",
            package.id,
            "success",
            details={"revision": revision, "package_digest": package.digest_sha256},
        )
        return _package_result(package)

    @server.tool()
    async def stage_published_package(package_id: str) -> dict[str, JsonValue]:
        """Stage a published package's artifacts on devices; requires publish.

        Only packages belonging to the token's project are permitted.
        """
        token = _authorize(services, McpScope.PUBLISH)
        _package_for_token(services, token, package_id)
        try:
            package = await services.publication.stage(package_id, _now_iso())
        except (PublicationError, ArtifactDeploymentError, DeviceSessionError) as error:
            raise ToolError(str(error)) from error
        _audit(services, token, "mcp.package.stage", package.id, "success")
        return _package_result(package)

    @server.tool()
    async def activate_published_package(
        package_id: str,
        simulation_mode: bool = False,
    ) -> dict[str, JsonValue]:
        """Activate a staged package's artifacts; may change physical device output.

        Requires activate and current publisher/admin plus live-output permission.
        Simulation mode forbids activation. Does not start scenes or replace the
        running controller's scene catalog.
        """
        token = _authorize(services, McpScope.ACTIVATE)
        _package_for_token(services, token, package_id)
        try:
            package = await services.publication.activate(
                package_id, occurred_at=_now_iso(), simulation_mode=simulation_mode
            )
        except (PublicationError, ArtifactDeploymentError, DeviceSessionError) as error:
            raise ToolError(str(error)) from error
        _audit(services, token, "mcp.package.activate", package.id, "success")
        return _package_result(package)

    @server.tool()
    def summarize_revision_difference(
        base_revision: int,
        candidate_revision: int,
    ) -> dict[str, JsonValue]:
        """Summarize authoritative changed paths between two project revisions."""
        token = _authorize(services, McpScope.READ)
        return {
            "project_id": token.project_id,
            "base_revision": base_revision,
            "candidate_revision": candidate_revision,
            "changed_paths": list(
                services.publication.diff(
                    token.project_id,
                    base_revision,
                    candidate_revision,
                )
            ),
        }

    @server.tool()
    def propose_scene_outline(
        base_revision: int,
        template_scene_id: str,
        scene_id: str,
        name: str,
        duration_ms: int,
    ) -> dict[str, JsonValue]:
        """Create an isolated scene proposal by cloning an approved project template."""
        token = _authorize(services, McpScope.DRAFT_EDIT)
        proposal = services.mcp_authoring.propose_scene_outline(
            project_id=token.project_id,
            base_revision=base_revision,
            template_scene_id=template_scene_id,
            scene_id=scene_id,
            name=name,
            duration_ms=duration_ms,
            client_id=token.client_id,
            username=token.username,
        )
        _audit(services, token, "mcp.proposal.create", proposal.id, "success")
        return _proposal_result(proposal)

    @server.tool()
    def propose_project_revision(
        base_revision: int,
        title: str,
        kind: str,
        proposed_draft: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Propose scene, cue, mapping, or autonomous-logic changes in isolation."""
        token = _authorize(services, McpScope.DRAFT_EDIT)
        if kind not in {
            "scene_change",
            "timeline_change",
            "mapping_suggestion",
            "autonomous_logic",
        }:
            raise McpAuthoringError("proposal kind is not supported")
        proposal = services.mcp_authoring.propose_project_revision(
            project_id=token.project_id,
            base_revision=base_revision,
            title=title,
            kind=kind,
            proposed_document=proposed_draft,
            client_id=token.client_id,
            username=token.username,
        )
        _audit(services, token, "mcp.proposal.create", proposal.id, "success")
        return _proposal_result(proposal)

    @server.tool()
    def validate_proposal(proposal_id: str) -> dict[str, JsonValue]:
        """Compile an isolated proposal and return structured diagnostics."""
        token = _authorize(services, McpScope.DRAFT_EDIT)
        proposal = _proposal_for_token(services, token, proposal_id)
        validated = services.mcp_authoring.validate_proposal(proposal.id)
        _audit(
            services,
            token,
            "mcp.proposal.validate",
            proposal.id,
            validated.state.value,
        )
        return _proposal_result(validated)

    @server.tool()
    async def simulate_proposal(
        proposal_id: str,
        steps: list[dict[str, JsonValue]],
        expected: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        """Run a bounded no-hardware simulation against a validated proposal."""
        token = _authorize(services, McpScope.SIMULATION)
        proposal = _proposal_for_token(services, token, proposal_id)
        simulated = await services.mcp_authoring.simulate_proposal(
            proposal.id,
            tuple(steps),
            expected or {},
        )
        _audit(services, token, "mcp.proposal.simulate", proposal.id, "success")
        return _proposal_result(simulated)

    @server.tool()
    def diagnose_proposal(proposal_id: str) -> dict[str, JsonValue]:
        """Explain current validation and simulation failures without mutating state."""
        token = _authorize(services, McpScope.READ)
        proposal = _proposal_for_token(services, token, proposal_id)
        return {
            "proposal_id": proposal.id,
            "state": proposal.state.value,
            "validation": list(proposal.validation),
            "simulation_mismatches": (
                proposal.simulation.get("mismatches", []) if proposal.simulation is not None else []
            ),
            "next_action": (
                "human approval required"
                if proposal.state.value == "validated"
                else "revise or reject the proposal"
            ),
        }

    @server.tool()
    def generate_interaction_test_scenario(
        scene_id: str,
        duration_ms: int = 1000,
    ) -> dict[str, JsonValue]:
        """Generate bounded deterministic scenario steps for operator review."""
        token = _authorize(services, McpScope.SIMULATION)
        revision = services.repository.get_revision(token.project_id)
        if not any(scene.id == scene_id for scene in revision.draft.scenes):
            raise McpAuthoringError("scene does not exist")
        duration = max(1, min(duration_ms, MAX_TIMELINE_RANGE_MS))
        return {
            "name": f"MCP interaction path for {scene_id}",
            "steps": [
                {"kind": "operator_start", "scene_id": scene_id},
                {"kind": "advance", "delta_ms": duration},
            ],
            "expected": {},
            "requires_human_review": True,
        }

    app = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=MAX_MCP_PAYLOAD_BYTES,
        max_sessions=32,
        session_idle_timeout=300,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                origin_url.netloc,
                origin_url.hostname,
                "localhost",
                "localhost:*",
                "127.0.0.1",
                "127.0.0.1:*",
            ],
            allowed_origins=list(services.config.allowed_origins),
        ),
        host=origin_url.hostname,
    )
    app.add_middleware(McpRequestGuard, services=services)
    return app


def _authorize(
    services: GuiServices,
    required_scope: McpScope,
    *alternative_scopes: McpScope,
) -> McpToken:
    access = get_access_token()
    if access is None:
        raise ToolError("MCP authentication is required")
    token_id = access.claims.get("token_id") if access.claims else None
    if not isinstance(token_id, str):
        raise ToolError("MCP token identity is invalid")
    token = services.mcp_store.get_token(token_id)
    if (
        token is None
        or token.revoked_at is not None
        or not _token_currently_authorized(services, token)
    ):
        raise ToolError("MCP token is no longer valid")
    accepted_scopes = (required_scope, *alternative_scopes)
    if not any(scope in token.scopes for scope in accepted_scopes):
        raise ToolError(
            "MCP scope required: " + " or ".join(scope.value for scope in accepted_scopes)
        )
    try:
        services.mcp_authoring.check_rate_limit(token.id)
    except McpAuthoringError as error:
        raise ToolError(str(error)) from error
    return token


def _proposal_for_token(
    services: GuiServices,
    token: McpToken,
    proposal_id: str,
) -> McpProposal:
    try:
        proposal = services.mcp_store.require_proposal(proposal_id)
    except McpAuthoringError as error:
        raise ToolError(str(error)) from error
    if proposal.project_id != token.project_id:
        raise ToolError("proposal is outside the token project")
    return proposal


def _timeline_layers(
    raw: JsonValue,
    space_id: str,
    start_ms: int,
    end_ms: int,
) -> list[JsonValue]:
    layers = raw.get(space_id, []) if isinstance(raw, dict) else []
    if not isinstance(layers, list):
        raise ToolError("timeline layers must be a list")
    selected: list[JsonValue] = []
    for layer in layers:
        if not isinstance(layer, dict):
            raise ToolError("timeline layer must be an object")
        start, duration = layer.get("start_ms"), layer.get("duration_ms")
        if not isinstance(start, int) or not isinstance(duration, int):
            raise ToolError("timeline layer interval is invalid")
        if layer.get("persistent") is True or (start < end_ms and start + duration > start_ms):
            selected.append(layer)
    return selected


def _bounded_result(result: dict[str, object]) -> dict[str, JsonValue]:
    if len(json.dumps(result).encode()) > 200 * 1024:
        raise ToolError("MCP result exceeds 200 KiB; request a smaller page or omit details")
    return cast(dict[str, JsonValue], result)


def _page_preview_samples(result: dict[str, object], offset: int, limit: int) -> None:
    surfaces = result.get("surfaces")
    records = cast(list[dict[str, object]], surfaces) if isinstance(surfaces, list) else [result]
    total = 0
    for record in records:
        samples = record.get("samples")
        if not isinstance(samples, list):
            continue
        start = max(0, offset - total)
        end = max(0, offset + limit - total)
        record["samples"] = samples[start:end]
        total += len(samples)
    result["sample_page"] = {
        "offset": offset,
        "limit": limit,
        "total": total,
        "next_offset": offset + limit if limit and offset + limit < total else None,
    }


def _image_result(image: Image.Image, diagnostics: dict[str, object]) -> CallToolResult:
    preview = image.convert("RGBA")
    preview.thumbnail((512, 512))
    while True:
        output = BytesIO()
        preview.save(output, format="PNG")
        encoded = output.getvalue()
        if len(encoded) <= 64 * 1024:
            break
        preview.thumbnail((max(1, preview.width // 2), max(1, preview.height // 2)))
    diagnostics["image_size"] = [preview.width, preview.height]
    result = CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(_bounded_result(diagnostics))),
            ImageContent(
                type="image", mime_type="image/png", data=base64.b64encode(encoded).decode("ascii")
            ),
        ],
        structured_content=diagnostics,
    )
    if len(result.model_dump_json(by_alias=True).encode()) > MAX_MCP_PAYLOAD_BYTES:
        raise ToolError("preview exceeds the MCP payload budget; request fewer samples")
    return result


def _render_job_for_token(
    services: GuiServices,
    token: McpToken,
    job_id: str,
) -> StoredRenderJob:
    try:
        job = services.render_jobs.get(job_id)
    except RenderJobError as error:
        raise ToolError(str(error)) from error
    if job.project_id != token.project_id:
        raise ToolError("render job is outside the token project")
    return job


def _spatial_mapper(services: GuiServices, draft: DraftProject) -> dict[str, object]:
    installation_id = draft.editor_metadata.get("installation_id")
    if not isinstance(installation_id, str):
        raise ToolError("project has no mapped installation")
    try:
        return services.mapper_export.export(installation_id)
    except MapperExportError as error:
        raise ToolError(str(error)) from error


def _token_currently_authorized(
    services: GuiServices,
    token: McpToken,
) -> bool:
    if token.username == services.config.admin_username:
        role = services.config.admin_role
    else:
        user = services.collaboration.get_user(token.username)
        if user is None or user.disabled:
            return False
        role = user.role
    membership = services.collaboration.get_membership(
        token.project_id,
        token.username,
    )
    if membership is None and role is not Role.ADMINISTRATOR:
        return False
    allowed = allowed_mcp_scopes(
        role,
        membership.role if membership is not None else None,
        membership.can_live_output if membership is not None else True,
    )
    return set(token.scopes).issubset(allowed)


def _token_role(services: GuiServices, token: McpToken) -> Role:
    if token.username == services.config.admin_username:
        return services.config.admin_role
    user = services.collaboration.get_user(token.username)
    if user is None or user.disabled:
        raise ToolError("MCP token user is no longer active")
    return user.role


def _package_for_token(services: GuiServices, token: McpToken, package_id: str) -> PublishedPackage:
    try:
        package = services.publication.get(package_id)
    except PublicationError as error:
        raise ToolError(str(error)) from error
    if package.project_id != token.project_id:
        raise ToolError("published package is outside the token project")
    return package


def _package_result(package: PublishedPackage) -> dict[str, JsonValue]:
    return {
        "id": package.id,
        "project_id": package.project_id,
        "revision": package.revision,
        "state": package.state,
        "digest_sha256": package.digest_sha256,
        "created_by": package.created_by,
        "staged_at": package.staged_at,
        "activated_at": package.activated_at,
    }


def _proposal_result(proposal: McpProposal) -> dict[str, JsonValue]:
    return {
        "proposal_id": proposal.id,
        "project_id": proposal.project_id,
        "base_revision": proposal.base_revision,
        "title": proposal.title,
        "kind": proposal.kind,
        "state": proposal.state.value,
        "changed_paths": list(proposal.changed_paths),
        "validation": list(proposal.validation),
        "simulation": proposal.simulation,
        "requires_human_approval": proposal.state.value != "merged",
    }


def _bounded_timeline_items(
    value: JsonValue | None,
    scene_id: str,
    start_ms: int,
    end_ms: int,
    time_key: str,
) -> list[JsonValue]:
    if not isinstance(value, dict):
        return []
    items = value.get(scene_id)
    if not isinstance(items, list):
        return []
    selected: list[JsonValue] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        timestamp = item.get(time_key)
        if isinstance(timestamp, int) and start_ms <= timestamp <= end_ms:
            selected.append(item)
            if len(selected) >= 1000:
                break
    return selected


def _audit(
    services: GuiServices,
    token: McpToken,
    action: str,
    target: str,
    result: str,
    *,
    details: dict[str, JsonValue] | None = None,
) -> None:
    services.repository.append_audit(
        actor=token.username,
        action=action,
        target=target,
        result=result,
        occurred_at=_now_iso(),
        authorization_context={
            **(details or {}),
            "mcp_client_id": token.client_id,
            "mcp_token_id": token.id,
            "project_id": token.project_id,
            "scopes": [scope.value for scope in token.scopes],
        },
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
