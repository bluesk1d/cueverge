from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from cueverge.core.models import (
    InstallationDefinition,
    SceneDefinitionV1,
    SceneDefinitionV2,
)
from cueverge.protocol.device_v2 import DeviceKind


class GuiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(StrEnum):
    VIEWER = "viewer"
    DESIGNER = "designer"
    OPERATOR = "operator"
    PUBLISHER = "publisher"
    ADMINISTRATOR = "administrator"


class DraftProject(GuiModel):
    schema_version: Literal[1]
    installation: InstallationDefinition
    scenes: tuple[SceneDefinitionV1 | SceneDefinitionV2, ...] = Field(min_length=1)
    editor_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_scenes(self) -> "DraftProject":
        scene_ids = [scene.id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("draft scene IDs must be unique")
        return self


class LoginRequest(GuiModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class SessionResponse(GuiModel):
    username: str
    role: Role


class CreateProjectRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)
    draft: DraftProject


class ProjectSummary(GuiModel):
    id: str
    name: str
    head_revision: int = Field(ge=1)
    created_at: str
    updated_at: str


class ProjectDraftResponse(GuiModel):
    project: ProjectSummary
    revision: int = Field(ge=1)
    draft: DraftProject


class RevisionSummary(GuiModel):
    revision: int = Field(ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    actor: str
    transaction_kind: str
    created_at: str


class RevisionConflictResponse(GuiModel):
    code: Literal["revision_conflict"] = "revision_conflict"
    requested_revision: int = Field(ge=1)
    current_revision: int = Field(ge=1)
    conflicting_paths: tuple[str, ...]


class CompilationResponse(GuiModel):
    project_id: str
    revision: int = Field(ge=1)
    package_digest: str
    files: tuple[str, ...]
    scene_ids: tuple[str, ...]


class InstallationResourceResponse(GuiModel):
    resource: str
    kind: Literal["media_agent", "pixel_output"]
    target: str | None = None
    pixel_count: int | None = Field(default=None, gt=0)


class InstallationStatusResponse(GuiModel):
    installation_id: Literal["active"] = "active"
    name: str
    package_schema: Literal["runtime-v1-v2"] = "runtime-v1-v2"
    resources: tuple[InstallationResourceResponse, ...]


class RuntimeStatusResponse(GuiModel):
    observed_at: str
    source: Literal["controller_runtime"] = "controller_runtime"
    status: dict[str, object]


class OperateActionRequest(GuiModel):
    action: Literal[
        "start",
        "pause",
        "resume",
        "seek",
        "stop",
        "reset",
        "automatic",
        "maintenance",
        "blackout",
    ]
    position_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_position(self) -> "OperateActionRequest":
        if self.action == "seek" and self.position_ms is None:
            raise ValueError("seek action requires position_ms")
        if self.action != "seek" and self.position_ms is not None:
            raise ValueError("position_ms is valid only for seek")
        return self


class OperateActionResponse(GuiModel):
    action: str
    mode: str
    active_scene: str | None


class TopologyNodeResponse(GuiModel):
    id: str
    label: str
    kind: Literal[
        "controller",
        "direct_media_agent",
        "downstream_controller",
        "controller_output",
    ]
    parent_id: str | None = None
    configured: bool
    connection: Literal["connected", "disconnected", "not_independently_detectable"]
    enrollment: Literal[
        "local_controller",
        "enrolled",
        "suspended",
        "revoked",
        "not_applicable",
    ]
    compatibility: Literal["compatible", "conflict", "unknown", "not_applicable"]
    protocol_version: int | None = Field(default=None, ge=1)
    capabilities: tuple[str, ...] = ()
    assigned_resources: tuple[str, ...] = ()
    last_seen_age_ms: int | None = Field(default=None, ge=0)
    software_version: str | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    clock_drift_ms: float | None = None
    connection_policy: Literal["continuous", "on_demand", "not_applicable"] = "not_applicable"
    reported_pixel_count: int | None = Field(default=None, gt=0)
    signal_percent: int | None = Field(default=None, ge=0, le=100)
    output_on: bool | None = None
    diagnostic: str | None = None


class TopologyResponse(GuiModel):
    observed_at: str
    source: Literal["controller_runtime"] = "controller_runtime"
    enrollment_supported: bool
    nodes: tuple[TopologyNodeResponse, ...]


class CreatePairingTokenRequest(GuiModel):
    device_kind: DeviceKind


class PairingTokenResponse(GuiModel):
    id: str
    pairing_token: str
    device_kind: DeviceKind
    expires_at: str


class SetDeviceConnectionPolicyRequest(GuiModel):
    policy: Literal["continuous", "on_demand"]


class DeviceConnectionPolicyResponse(GuiModel):
    device_id: str
    policy: Literal["continuous", "on_demand"]
    updated_at: str


class SetDeviceEnrollmentStateRequest(GuiModel):
    state: Literal["enrolled", "suspended", "revoked"]


class DeviceEnrollmentStateResponse(GuiModel):
    device_id: str
    state: Literal["enrolled", "suspended", "revoked"]
    updated_at: str


class SetResourceAssignmentRequest(GuiModel):
    device_id: str = Field(min_length=1)
    capability_kind: Literal[
        "media_playback",
        "pixel_output",
        "downstream_control",
    ]
    device_output: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=1)


class ResourceAssignmentResponse(GuiModel):
    resource: str
    device_id: str
    capability_kind: str
    device_output: str | None
    revision: int = Field(ge=1)
    actor: str
    created_at: str
    updated_at: str


class ResourceAssignmentConflictResponse(GuiModel):
    code: Literal["resource_assignment_conflict"] = "resource_assignment_conflict"
    requested_revision: int | None
    current_revision: int | None


class AuditEventResponse(GuiModel):
    id: int = Field(ge=1)
    actor: str
    action: str
    target: str
    result: str
    occurred_at: str
    authorization_context: dict[str, JsonValue]
