from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from cueverge.protocol.models import Discontinuity, PlaybackState

DEVICE_PROTOCOL_VERSION: Literal[2] = 2


class DeviceProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceKind(StrEnum):
    DIRECT_MEDIA_AGENT = "direct_media_agent"
    DOWNSTREAM_CONTROLLER = "downstream_controller"


class EnrollmentState(StrEnum):
    DISCOVERED = "discovered"
    PAIRING = "pairing"
    ENROLLED = "enrolled"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class ProtocolRange(DeviceProtocolModel):
    minimum: int = Field(ge=2)
    maximum: int = Field(ge=2)

    @model_validator(mode="after")
    def validate_range(self) -> "ProtocolRange":
        if self.maximum < self.minimum:
            raise ValueError("protocol maximum must be greater than or equal to minimum")
        if not self.minimum <= DEVICE_PROTOCOL_VERSION <= self.maximum:
            raise ValueError("capability document must support device protocol version 2")
        return self


class MediaPlaybackCapability(DeviceProtocolModel):
    kind: Literal["media_playback"] = "media_playback"
    codecs: tuple[str, ...] = Field(min_length=1)
    containers: tuple[str, ...] = Field(min_length=1)
    max_outputs: int = Field(default=1, ge=1)
    supports_pause: bool = True
    supports_seek: bool = True

    @model_validator(mode="after")
    def validate_unique_formats(self) -> "MediaPlaybackCapability":
        if len(self.codecs) != len(set(self.codecs)):
            raise ValueError("media playback codecs must be unique")
        if len(self.containers) != len(set(self.containers)):
            raise ValueError("media playback containers must be unique")
        return self


class ArtifactCacheCapability(DeviceProtocolModel):
    kind: Literal["artifact_cache"] = "artifact_cache"
    digest: Literal["sha256"] = "sha256"
    total_bytes: int = Field(gt=0)
    free_bytes: int = Field(ge=0)
    supports_pinning: bool
    supports_resumable_transfer: bool

    @model_validator(mode="after")
    def validate_capacity(self) -> "ArtifactCacheCapability":
        if self.free_bytes > self.total_bytes:
            raise ValueError("artifact cache free bytes cannot exceed total bytes")
        return self


class ClockCapability(DeviceProtocolModel):
    kind: Literal["clock"] = "clock"
    monotonic: bool
    reports_media_position: bool
    reports_drift: bool
    resolution_ms: float = Field(gt=0)


class PixelOutputCapability(DeviceProtocolModel):
    kind: Literal["pixel_output"] = "pixel_output"
    max_pixels: int = Field(gt=0)
    channel_format: Literal["rgb"]
    supports_test_pattern: bool = False


class ObservationCapability(DeviceProtocolModel):
    kind: Literal["observation"] = "observation"
    event_types: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_event_types(self) -> "ObservationCapability":
        if len(self.event_types) != len(set(self.event_types)):
            raise ValueError("observation event types must be unique")
        return self


class DownstreamControlCapability(DeviceProtocolModel):
    kind: Literal["downstream_control"] = "downstream_control"
    operations: tuple[
        Literal[
            "prepare_release",
            "activate_release",
            "start_scene",
            "pause_scene",
            "resume_scene",
            "seek_scene",
            "stop_scene",
            "query_status",
        ],
        ...,
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_operations(self) -> "DownstreamControlCapability":
        if len(self.operations) != len(set(self.operations)):
            raise ValueError("downstream control operations must be unique")
        return self


DeviceCapability = Annotated[
    MediaPlaybackCapability
    | ArtifactCacheCapability
    | ClockCapability
    | PixelOutputCapability
    | ObservationCapability
    | DownstreamControlCapability,
    Field(discriminator="kind"),
]


class DeviceCapabilityDocument(DeviceProtocolModel):
    schema_version: Literal[1] = 1
    protocol: ProtocolRange
    device_kind: DeviceKind
    software_version: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$",
    )
    capabilities: tuple[DeviceCapability, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_capabilities(self) -> "DeviceCapabilityDocument":
        kinds = [capability.kind for capability in self.capabilities]
        if len(kinds) != len(set(kinds)):
            raise ValueError("device capability kinds must be unique")
        has_downstream_control = "downstream_control" in kinds
        if self.device_kind is DeviceKind.DIRECT_MEDIA_AGENT and has_downstream_control:
            raise ValueError("direct media agents cannot declare downstream control")
        if self.device_kind is DeviceKind.DOWNSTREAM_CONTROLLER and not has_downstream_control:
            raise ValueError("downstream controllers must declare downstream control")
        return self


class DeviceEnrollmentRequest(DeviceProtocolModel):
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    pairing_token: SecretStr
    display_name: str = Field(min_length=1, max_length=128)
    certificate_signing_request_pem: str = Field(min_length=1)
    capabilities: DeviceCapabilityDocument


class DeviceCertificate(DeviceProtocolModel):
    device_id: UUID
    certificate_serial: str = Field(min_length=1, max_length=128)
    fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    certificate_pem: str = Field(min_length=1)
    trust_chain_pem: tuple[str, ...] = Field(min_length=1)
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_validity(self) -> "DeviceCertificate":
        if self.expires_at <= self.issued_at:
            raise ValueError("device certificate expiry must be after issuance")
        return self


class DeviceEnrollmentResponse(DeviceProtocolModel):
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    enrollment_state: Literal[EnrollmentState.ENROLLED] = EnrollmentState.ENROLLED
    credential: DeviceCertificate


class DeviceCredentialRotationRequest(DeviceProtocolModel):
    type: Literal["device.credential.rotate"] = "device.credential.rotate"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    device_id: UUID
    certificate_signing_request_pem: str = Field(min_length=1)


class DeviceCredentialRotationResponse(DeviceProtocolModel):
    type: Literal["device.credential.rotated"] = "device.credential.rotated"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    credential: DeviceCertificate


class DeviceIdentity(DeviceProtocolModel):
    device_id: UUID
    display_name: str = Field(min_length=1, max_length=128)
    device_kind: DeviceKind
    enrollment_state: EnrollmentState
    active_certificate_serial: str | None = Field(default=None, min_length=1, max_length=128)
    capabilities: DeviceCapabilityDocument | None = None

    @model_validator(mode="after")
    def validate_enrolled_identity(self) -> "DeviceIdentity":
        if self.enrollment_state is EnrollmentState.ENROLLED and (
            self.active_certificate_serial is None or self.capabilities is None
        ):
            raise ValueError("enrolled devices require an active certificate and capabilities")
        return self


class DeviceHello(DeviceProtocolModel):
    type: Literal["device.hello"] = "device.hello"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    device_id: UUID
    device_session_id: UUID
    capabilities: DeviceCapabilityDocument


class DeviceWelcome(DeviceProtocolModel):
    type: Literal["device.welcome"] = "device.welcome"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    device_id: UUID
    controller_epoch: UUID
    heartbeat_interval_ms: int = Field(gt=0)
    heartbeat_timeout_ms: int = Field(gt=0)
    reconciliation: Literal["idle", "continue", "stop"] = "idle"


class DeviceHeartbeat(DeviceProtocolModel):
    type: Literal["device.heartbeat"] = "device.heartbeat"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    device_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    sequence: int = Field(ge=0)
    device_monotonic_ms: int = Field(ge=0)


class DeviceHeartbeatAck(DeviceProtocolModel):
    type: Literal["device.heartbeat.ack"] = "device.heartbeat.ack"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    sequence: int = Field(ge=0)
    accepted: bool
    reason: (
        Literal[
            "device_identity_fence",
            "device_session_fence",
            "controller_epoch_fence",
            "out_of_order",
        ]
        | None
    ) = None


class DeviceMediaCommand(DeviceProtocolModel):
    type: Literal["device.media.command"] = "device.media.command"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    command: Literal["prepare", "play", "pause", "resume", "seek", "stop", "get_status"]
    scene_id: str = Field(min_length=1, max_length=128)
    scene_instance_id: UUID
    playback_session_id: UUID
    asset_id: str | None = None
    target_position_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_command_fields(self) -> "DeviceMediaCommand":
        if self.command == "seek" and self.target_position_ms is None:
            raise ValueError("seek command requires target_position_ms")
        if self.command != "seek" and self.target_position_ms is not None:
            raise ValueError("target_position_ms is valid only for seek")
        if self.command != "prepare" and self.asset_id is not None:
            raise ValueError("asset_id is valid only for prepare")
        return self


class DeviceCommandAck(DeviceProtocolModel):
    type: Literal["device.command.ack"] = "device.command.ack"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    status: Literal["completed", "error"]
    error_code: str | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def validate_error(self) -> "DeviceCommandAck":
        if self.status == "completed" and (self.error_code is not None or self.detail is not None):
            raise ValueError("completed acknowledgement cannot include an error")
        if self.status == "error" and self.error_code is None:
            raise ValueError("error acknowledgement requires error_code")
        return self


class DeviceMediaPosition(DeviceProtocolModel):
    type: Literal["device.media.position"] = "device.media.position"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    device_session_id: UUID
    controller_epoch: UUID
    scene_instance_id: UUID
    playback_session_id: UUID
    sequence: int = Field(ge=0)
    position_ms: int = Field(ge=0)
    playback_rate: float = Field(default=1.0, ge=0.1, le=4.0)
    state: PlaybackState
    duration_ms: int | None = Field(default=None, gt=0)
    device_monotonic_ms: int = Field(ge=0)
    discontinuity: Discontinuity | None = None
