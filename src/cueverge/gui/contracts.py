from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from cueverge.core.models import (
    InstallationDefinition,
    SceneDefinitionV1,
    SceneDefinitionV2,
)
from cueverge.protocol.device_v2 import DeviceCapabilityDocument, DeviceKind


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
    kind: Literal["media_agent", "pixel_output", "downstream_controller"]
    target: str | None = None
    pixel_count: int | None = Field(default=None, gt=0)


class InstallationStatusResponse(GuiModel):
    installation_id: str
    name: str
    revision: int = Field(ge=1)
    runtime_active: bool
    package_schema: Literal["runtime-v1-v2"] = "runtime-v1-v2"
    resources: tuple[InstallationResourceResponse, ...]


class InstallationSummaryResponse(GuiModel):
    id: str
    name: str
    revision: int = Field(ge=1)
    runtime_active: bool
    created_at: str
    updated_at: str


class CreateInstallationRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)


class UpdateInstallationRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)


class InstallationConflictResponse(GuiModel):
    code: Literal["installation_conflict"] = "installation_conflict"
    requested_revision: int
    current_revision: int | None


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


class MaintenancePreviewRequest(GuiModel):
    resource: str = Field(min_length=1)
    duration_seconds: int = Field(default=15, ge=5, le=30)


class MaintenancePreviewResponse(GuiModel):
    resource: str
    resource_kind: Literal["media_agent", "pixel_output"]
    pattern: Literal["alignment_grid", "pixel_index_chase"]
    simulation_only: Literal[True] = True
    started_at: str
    expires_at: str


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
    compatibility_detail: str | None = None
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
    capability_document: DeviceCapabilityDocument | None = None
    certificate_expires_at: str | None = None
    storage_total_bytes: int | None = Field(default=None, gt=0)
    storage_free_bytes: int | None = Field(default=None, ge=0)
    artifact_count: int | None = Field(default=None, ge=0)
    artifact_bytes: int | None = Field(default=None, ge=0)
    pinned_artifact_count: int | None = Field(default=None, ge=0)
    supports_pinning: bool | None = None
    supports_resumable_transfer: bool | None = None
    active_transfers: int | None = Field(default=None, ge=0)
    failed_transfers: int | None = Field(default=None, ge=0)
    artifact_faults: tuple[str, ...] = ()


class DeviceArtifactResponse(GuiModel):
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    pinned: bool


class DeviceTransferStatusResponse(GuiModel):
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    received_bytes: int = Field(ge=0)
    total_bytes: int = Field(gt=0)
    state: Literal["active", "failed"]
    fault: str | None = None


class TopologyResponse(GuiModel):
    observed_at: str
    source: Literal["controller_runtime"] = "controller_runtime"
    enrollment_supported: bool
    nodes: tuple[TopologyNodeResponse, ...]


class CreatePairingTokenRequest(GuiModel):
    device_kind: DeviceKind
    discovered_endpoint_id: str | None = None


class PairingTokenResponse(GuiModel):
    id: str
    pairing_token: str
    device_kind: DeviceKind
    expires_at: str
    device_transport_url: str
    discovered_endpoint_id: str | None = None


class ObservedProtocolRange(GuiModel):
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "ObservedProtocolRange":
        if self.maximum < self.minimum:
            raise ValueError("protocol maximum must be greater than or equal to minimum")
        return self


class CapabilityReviewRequest(GuiModel):
    schema_version: int = Field(ge=1)
    protocol: ObservedProtocolRange
    device_kind: DeviceKind
    software_version: str = Field(min_length=1, max_length=128)
    capabilities: tuple[dict[str, JsonValue], ...] = Field(min_length=1)


class CapabilityReviewIssue(GuiModel):
    code: Literal[
        "unsupported_schema",
        "protocol_mismatch",
        "duplicate_capability",
        "missing_media_playback",
        "missing_downstream_control",
        "direct_agent_claims_downstream_control",
    ]
    message: str


class CapabilityReviewResponse(GuiModel):
    compatible: bool
    controller_protocol_version: int
    capability_kinds: tuple[str, ...]
    issues: tuple[CapabilityReviewIssue, ...]


class ProbeEndpointRequest(GuiModel):
    address: str = Field(min_length=1, max_length=2048)


class DiscoveredEndpointResponse(GuiModel):
    id: str
    base_url: str
    device_kind: DeviceKind
    software_version: str
    protocol_minimum: int = Field(ge=1)
    protocol_maximum: int = Field(ge=1)
    capability_kinds: tuple[str, ...]
    compatible: bool
    state: Literal["discovered", "pairing", "enrolled", "rejected"]
    enrolled_device_id: str | None
    observed_at: str
    updated_at: str


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


class ConnectionTestCheck(GuiModel):
    name: Literal["enrollment", "heartbeat", "timing", "assignment"]
    passed: bool
    detail: str


class DeviceConnectionTestResponse(GuiModel):
    device_id: str
    observed_at: str
    result: Literal["passed", "failed"]
    checks: tuple[ConnectionTestCheck, ...]
    latency_ms: float | None = Field(default=None, ge=0)
    clock_offset_ms: float | None = None


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


class ActivityEventResponse(GuiModel):
    id: int = Field(ge=1)
    occurred_at: str
    category: Literal["device", "operation", "configuration", "project", "security"]
    action: str
    actor: str
    target: str
    result: str
