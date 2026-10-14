from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

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


def _normalize_folder(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().strip("/")
    parts = normalized.split("/") if normalized else []
    if any(part in {"", ".", ".."} for part in parts) or "\\" in normalized:
        raise ValueError("folder must contain plain slash-separated names")
    return normalized or None


def _normalize_tags(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(tag.strip() for tag in value)
    if any(not tag or len(tag) > 50 for tag in normalized):
        raise ValueError("tags must contain between 1 and 50 characters")
    if len({tag.casefold() for tag in normalized}) != len(normalized):
        raise ValueError("tags must be unique ignoring case")
    return normalized


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
    metadata_revision: int = Field(ge=1)
    archived_at: str | None
    created_at: str
    updated_at: str


class ProjectDraftResponse(GuiModel):
    project: ProjectSummary
    revision: int = Field(ge=1)
    draft: DraftProject


class ProjectMetadataRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectMetadataConflictResponse(GuiModel):
    code: Literal["project_metadata_conflict"] = "project_metadata_conflict"
    requested_revision: int = Field(ge=1)
    current_revision: int = Field(ge=1)


class PortableAssetRevision(GuiModel):
    revision: int = Field(ge=1)
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=200)
    original_filename: str = Field(min_length=1, max_length=255)
    scan_status: Literal["unavailable", "clean", "quarantined"]
    metadata: dict[str, JsonValue]
    created_at: str

    @field_validator("original_filename")
    @classmethod
    def validate_original_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or "\x00" in value or value in {".", ".."}:
            raise ValueError("portable original filename must be a plain filename")
        return value

    @model_validator(mode="after")
    def reject_storage_paths(self) -> "PortableAssetRevision":
        if _contains_storage_path(self.metadata):
            raise ValueError("portable asset metadata cannot contain storage paths")
        return self


class PortableAsset(GuiModel):
    source_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    current_revision: int = Field(ge=1)
    owner: str | None = Field(default=None, max_length=200)
    license: str | None = Field(default=None, max_length=500)
    provenance: str | None = Field(default=None, max_length=4000)
    review_status: Literal["unreviewed", "approved", "rejected"]
    expires_on: date | None
    folder: str | None = Field(default=None, max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    favorite: bool
    revisions: tuple[PortableAssetRevision, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_revisions(self) -> "PortableAsset":
        revision_numbers = [revision.revision for revision in self.revisions]
        if len(revision_numbers) != len(set(revision_numbers)):
            raise ValueError("portable asset revision numbers must be unique")
        if self.current_revision not in revision_numbers:
            raise ValueError("portable asset current revision must exist")
        return self


class ProjectExportDocument(GuiModel):
    schema_version: Literal[1, 2] = 2
    name: str = Field(min_length=1, max_length=200)
    draft: DraftProject
    assets: tuple[PortableAsset, ...] = ()

    @model_validator(mode="after")
    def validate_assets(self) -> "ProjectExportDocument":
        source_ids = [asset.source_id for asset in self.assets]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("portable asset source IDs must be unique")
        if self.schema_version == 1 and self.assets:
            raise ValueError("schema-v1 exports cannot contain asset manifests")
        return self


def _contains_storage_path(value: JsonValue) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = key.casefold().replace("-", "_")
            if normalized_key in {
                "path",
                "absolute_path",
                "object_path",
                "source_path",
                "temporary_path",
            }:
                return True
            if _contains_storage_path(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_storage_path(item) for item in value)
    return False


class BeginAssetUploadRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)
    original_filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=200)
    total_bytes: int = Field(gt=0)
    expected_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class AssetSceneReferenceResponse(GuiModel):
    scene_id: str
    scene_name: str
    output_id: str | None


class UpdateAssetMetadataRequest(GuiModel):
    owner: str | None = Field(default=None, max_length=200)
    license: str | None = Field(default=None, max_length=500)
    provenance: str | None = Field(default=None, max_length=4000)
    review_status: Literal["unreviewed", "approved", "rejected"]
    expires_on: date | None = None
    folder: str | None = Field(default=None, max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    favorite: bool = False

    @field_validator("owner", "license", "provenance")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("folder")
    @classmethod
    def validate_folder(cls, value: str | None) -> str | None:
        return _normalize_folder(value)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_tags(value)


class AssetMetadataConflictResponse(GuiModel):
    code: Literal["asset_metadata_conflict"] = "asset_metadata_conflict"
    requested_revision: int = Field(ge=1)
    current_revision: int = Field(ge=1)


class PlaceAssetRequest(GuiModel):
    scene_id: str = Field(min_length=1)
    output_id: str | None = Field(default=None, min_length=1)


class ObjectOrganizationRequest(GuiModel):
    folder: str | None = Field(default=None, max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    favorite: bool = False

    @field_validator("folder")
    @classmethod
    def validate_folder(cls, value: str | None) -> str | None:
        return _normalize_folder(value)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_tags(value)


class CreateScenePresetRequest(ObjectOrganizationRequest):
    scene_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)


class ApplyScenePresetRequest(GuiModel):
    scene_id: str = Field(min_length=1)


class AssetResponse(GuiModel):
    id: str
    project_id: str
    name: str
    status: str
    current_revision: int | None
    metadata_revision: int = Field(ge=1)
    owner: str | None
    license: str | None
    provenance: str | None
    review_status: Literal["unreviewed", "approved", "rejected"]
    expires_on: str | None
    folder: str | None
    tags: tuple[str, ...]
    favorite: bool
    error: str | None
    digest_sha256: str | None
    size_bytes: int | None
    media_type: str | None
    original_filename: str | None
    scan_status: str | None
    metadata: dict[str, object] | None
    original_file_status: Literal["pending", "available", "missing", "size_mismatch"]
    artifact_faults: tuple[str, ...]
    references: tuple[AssetSceneReferenceResponse, ...]
    unused: bool
    created_at: str
    updated_at: str


class AssetUploadResponse(GuiModel):
    id: str
    asset_id: str
    total_bytes: int = Field(gt=0)
    received_bytes: int = Field(ge=0)
    state: str
    error: str | None
    chunk_max_bytes: int = Field(gt=0)
    asset: AssetResponse


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
    folder: str | None
    tags: tuple[str, ...]
    favorite: bool
    used_by_projects: tuple[str, ...]
    created_at: str
    updated_at: str


class CreateInstallationRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)


class UpdateInstallationRequest(GuiModel):
    name: str = Field(min_length=1, max_length=200)
    folder: str | None = Field(default=None, max_length=200)
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    favorite: bool = False

    @field_validator("folder")
    @classmethod
    def validate_folder(cls, value: str | None) -> str | None:
        return _normalize_folder(value)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _normalize_tags(value)


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
