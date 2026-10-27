import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from cueverge.protocol.models import Discontinuity, MediaCommandName, PlaybackState

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


MediaFeature = Literal[
    "seamless_transition", "repeat_count", "player_control", "agent_update", "agent_settings"
]
AgentPlatform = Literal["linux_x86_64", "linux_aarch64", "windows_amd64"]
SHA256_PATTERN = r"^[0-9a-f]{64}$"
PlayerState = Literal["stopped", "starting", "ready", "stopping", "failed"]
KeepAwakeMode = Literal["system", "display"]


KeepAwakeSetting = Literal["off", "system", "display"]
PlayerMode = Literal["always", "on_demand"]
AgentSettingName = Literal[
    "keep_awake",
    "player_mode",
    "fullscreen_screen",
    "audio_device",
    "audio_delay_ms",
    "telemetry_hz",
]
AGENT_SETTING_NAMES: tuple[AgentSettingName, ...] = (
    "keep_awake",
    "player_mode",
    "fullscreen_screen",
    "audio_device",
    "audio_delay_ms",
    "telemetry_hz",
)


class AgentSettings(DeviceProtocolModel):
    """Controller overrides for a media agent; a null field uses the host's env default."""

    keep_awake: KeepAwakeSetting | None = None
    player_mode: PlayerMode | None = None
    fullscreen_screen: int | None = Field(default=None, ge=0, le=63)
    audio_device: str | None = Field(default=None, min_length=1, max_length=512)
    audio_delay_ms: int | None = Field(default=None, ge=-5000, le=5000)
    telemetry_hz: float | None = Field(default=None, ge=1.0, le=60.0)


class AgentEffectiveSettings(DeviceProtocolModel):
    """Concrete settings an agent runs with; null display/audio means mpv's own default."""

    keep_awake: KeepAwakeSetting = "off"
    player_mode: PlayerMode = "always"
    fullscreen_screen: int | None = Field(default=None, ge=0, le=63)
    audio_device: str | None = Field(default=None, min_length=1, max_length=512)
    audio_delay_ms: int = Field(default=0, ge=-5000, le=5000)
    telemetry_hz: float = Field(default=15.0, ge=1.0, le=60.0)

    def merged(self, overrides: AgentSettings) -> "AgentEffectiveSettings":
        values = overrides.model_dump(exclude_none=True)
        return self.model_copy(update=values)


class AgentDisplay(DeviceProtocolModel):
    """A display as the host reports it; index is the likely mpv --fs-screen value."""

    index: int = Field(ge=0, le=63)
    name: str = Field(min_length=1, max_length=128)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    primary: bool | None = None


class AgentAudioDevice(DeviceProtocolModel):
    name: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=256)


class AgentSettingsState(DeviceProtocolModel):
    """What an agent runs with, its host defaults, and the choices the host offers.

    revision is the controller settings revision applied (0 before any). Settings in
    pending_player_restart are saved but take effect when the player next starts.
    """

    revision: int = Field(default=0, ge=0)
    effective: AgentEffectiveSettings
    host_defaults: AgentEffectiveSettings
    pending_player_restart: tuple[AgentSettingName, ...] = ()
    displays: tuple[AgentDisplay, ...] = Field(default=(), max_length=32)
    audio_devices: tuple[AgentAudioDevice, ...] = Field(default=(), max_length=64)


class KeepAwakeStatus(DeviceProtocolModel):
    """Host sleep prevention configured locally on a media agent with CUEVERGE_KEEP_AWAKE.

    system blocks automatic sleep; display also blocks screen blanking. active is false when
    the operating system or desktop refused the request; detail then explains why.
    """

    mode: KeepAwakeMode
    active: bool
    detail: str | None = Field(default=None, max_length=300)


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


class TimingAdjustment(DeviceProtocolModel):
    minimum_ms: int = Field(ge=-60_000, le=60_000)
    maximum_ms: int = Field(ge=-60_000, le=60_000)
    resolution_ms: int = Field(gt=0, le=10_000)
    adjustable_while_active: bool = False

    @model_validator(mode="after")
    def validate_adjustment(self) -> "TimingAdjustment":
        if self.maximum_ms < self.minimum_ms:
            raise ValueError("timing maximum must be greater than or equal to minimum")
        if (self.maximum_ms - self.minimum_ms) % self.resolution_ms:
            raise ValueError("timing range must be divisible by its resolution")
        return self


class TimingCalibrationCapability(DeviceProtocolModel):
    kind: Literal["timing_calibration"] = "timing_calibration"
    actuator_kinds: tuple[
        Literal[
            "media",
            "audio",
            "pixels",
            "network_light",
            "smart_switch",
            "relay",
            "physical_effect",
        ],
        ...,
    ] = Field(min_length=1)
    onset: TimingAdjustment | None = None
    release: TimingAdjustment | None = None
    safe_test_procedure: Literal[
        "maintenance_reference",
        "armed_trials",
        "simulation_only",
    ]
    supports_ab_comparison: bool = False

    @model_validator(mode="after")
    def validate_timing_capability(self) -> "TimingCalibrationCapability":
        if len(self.actuator_kinds) != len(set(self.actuator_kinds)):
            raise ValueError("timing actuator kinds must be unique")
        if self.onset is None and self.release is None:
            raise ValueError("timing calibration must declare onset or release adjustment")
        return self


DeviceCapability = Annotated[
    MediaPlaybackCapability
    | ArtifactCacheCapability
    | ClockCapability
    | PixelOutputCapability
    | ObservationCapability
    | DownstreamControlCapability
    | TimingCalibrationCapability,
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


class DiscoveryProtocolRange(DeviceProtocolModel):
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "DiscoveryProtocolRange":
        if self.maximum < self.minimum:
            raise ValueError("protocol maximum must be greater than or equal to minimum")
        return self


class ControllerDiscoveryDocument(DeviceProtocolModel):
    service: Literal["cueverge"] = "cueverge"
    schema_version: Literal[1] = 1
    device_kind: Literal[DeviceKind.DOWNSTREAM_CONTROLLER] = DeviceKind.DOWNSTREAM_CONTROLLER
    software_version: str = Field(min_length=1, max_length=128)
    protocol: DiscoveryProtocolRange
    capabilities: tuple[Literal["downstream_control"], ...] = ("downstream_control",)


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


class AgentUpdateResult(DeviceProtocolModel):
    """Outcome of the most recent controller-managed update, recorded by the agent launcher."""

    bundle_digest: str = Field(pattern=SHA256_PATTERN)
    outcome: Literal["installed", "rolled_back"]
    detail: str | None = Field(default=None, max_length=500)
    finished_at_unix_ms: int = Field(ge=0)


class AgentRelease(DeviceProtocolModel):
    """The agent software actually running, as opposed to the enrolled capability document.

    bundle_digest is the SHA-256 of the controller-supplied bundle the agent runs, or null for
    agents run from a source checkout. managed is true when a launcher can install updates.
    """

    version: str = Field(min_length=1, max_length=128)
    platform: AgentPlatform | None = None
    bundle_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    managed: bool = False
    last_update: AgentUpdateResult | None = None


class DeviceHello(DeviceProtocolModel):
    type: Literal["device.hello"] = "device.hello"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    device_id: UUID
    device_session_id: UUID
    capabilities: DeviceCapabilityDocument
    media_features: tuple[MediaFeature, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
        description=(
            "Player behaviors implemented by this agent build. They do not grant trust, so "
            "they are reported per connection rather than in the enrolled capability document."
        ),
    )
    player_state: PlayerState | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Player process state for agents that advertise player_control.",
    )
    keep_awake: KeepAwakeStatus | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Host sleep prevention; omitted when the agent's keep-awake option is off.",
    )
    agent_release: AgentRelease | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Running agent software; reported by agents that advertise agent_update.",
    )
    agent_settings: AgentSettingsState | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Effective settings and host choices; agents advertising agent_settings.",
    )


class DevicePlayerCommand(DeviceProtocolModel):
    """Start or stop the media player process of an agent that advertises player_control."""

    type: Literal["device.player.start", "device.player.stop"]
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID


class DevicePlayerAck(DeviceProtocolModel):
    type: Literal["device.player.ack"] = "device.player.ack"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    status: Literal["completed", "error"]
    player_state: PlayerState
    error: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_error(self) -> "DevicePlayerAck":
        if (self.status == "error") != (self.error is not None):
            raise ValueError("player error detail must match status")
        return self


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
    device_unix_ms: int = Field(default=0, ge=0)
    output_scaling: dict[
        str,
        Annotated[float, Field(ge=0, le=1)],
    ] = Field(default_factory=dict)
    player_state: PlayerState | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    keep_awake: KeepAwakeStatus | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    agent_release: AgentRelease | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    agent_settings: AgentSettingsState | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Sent only when the agent's settings state changed since its last report.",
    )


class DeviceAgentSettingsCommand(DeviceProtocolModel):
    """Replace the controller overrides of an agent that advertises agent_settings.

    The agent stores them, applies what it can live, and restarts its player for the rest
    only when allow_player_restart is true (the controller is in idle maintenance).
    """

    type: Literal["device.agent.settings"] = "device.agent.settings"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    revision: int = Field(ge=1)
    settings: AgentSettings
    allow_player_restart: bool = False


class DeviceAgentSettingsAck(DeviceProtocolModel):
    type: Literal["device.agent.settings.ack"] = "device.agent.settings.ack"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    revision: int = Field(ge=1)
    status: Literal["completed", "error"]
    state: AgentSettingsState | None = None
    error: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_error(self) -> "DeviceAgentSettingsAck":
        if (self.status == "error") != (self.error is not None):
            raise ValueError("agent settings error detail must match status")
        return self


class DeviceAgentUpdateCommand(DeviceProtocolModel):
    """Install a controller-supplied agent bundle, then restart into it as a trial.

    Sent only to agents that advertise agent_update. The device downloads download_url over
    its mTLS identity and must verify size_bytes and digest_sha256 before installing.
    """

    type: Literal["device.agent.update"] = "device.agent.update"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    version: str = Field(min_length=1, max_length=128)
    platform: AgentPlatform
    digest_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(gt=0)
    download_url: AnyUrl

    @model_validator(mode="after")
    def validate_download_url(self) -> "DeviceAgentUpdateCommand":
        if self.download_url.scheme != "https":
            raise ValueError("agent bundle download URL must use https")
        return self


class DeviceAgentUpdateAck(DeviceProtocolModel):
    """restarting: installed and verified; the agent is about to exit into the new version."""

    type: Literal["device.agent.update.ack"] = "device.agent.update.ack"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    digest_sha256: str = Field(pattern=SHA256_PATTERN)
    status: Literal["restarting", "error"]
    error: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_error(self) -> "DeviceAgentUpdateAck":
        if (self.status == "error") != (self.error is not None):
            raise ValueError("agent update error detail must match status")
        return self


class DeviceHeartbeatAck(DeviceProtocolModel):
    type: Literal["device.heartbeat.ack"] = "device.heartbeat.ack"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    sequence: int = Field(ge=0)
    accepted: bool
    controller_receive_unix_ms: int = Field(default=0, ge=0)
    controller_send_unix_ms: int = Field(default=0, ge=0)
    reason: (
        Literal[
            "device_identity_fence",
            "device_session_fence",
            "controller_epoch_fence",
            "out_of_order",
        ]
        | None
    ) = None


class DeviceTimingReport(DeviceProtocolModel):
    type: Literal["device.timing.report"] = "device.timing.report"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    device_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    heartbeat_sequence: int = Field(ge=0)
    round_trip_ms: float = Field(ge=0)
    clock_offset_ms: float


class DeviceArtifactRecord(DeviceProtocolModel):
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    pinned: bool = False
    in_use: bool = False
    state: Literal["verified", "corrupt"] = "verified"
    last_used_unix_ms: int = Field(default=0, ge=0)


class DeviceArtifactTransferStatus(DeviceProtocolModel):
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    received_bytes: int = Field(ge=0)
    total_bytes: int = Field(gt=0)
    state: Literal["active", "failed"]
    fault: str | None = None

    @model_validator(mode="after")
    def validate_progress(self) -> "DeviceArtifactTransferStatus":
        if self.received_bytes > self.total_bytes:
            raise ValueError("received_bytes cannot exceed total_bytes")
        if self.state == "failed" and not self.fault:
            raise ValueError("failed transfer requires a fault")
        if self.state == "active" and self.fault is not None:
            raise ValueError("active transfer cannot include a fault")
        return self


class DeviceArtifactInventory(DeviceProtocolModel):
    type: Literal["device.artifact.inventory"] = "device.artifact.inventory"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    device_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    total_bytes: int = Field(gt=0)
    free_bytes: int = Field(ge=0)
    supports_pinning: bool
    supports_resumable_transfer: bool
    artifacts: tuple[DeviceArtifactRecord, ...]
    transfers: tuple[DeviceArtifactTransferStatus, ...] = ()
    faults: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_storage(self) -> "DeviceArtifactInventory":
        if self.free_bytes > self.total_bytes:
            raise ValueError("free_bytes cannot exceed total_bytes")
        digests = [artifact.digest_sha256 for artifact in self.artifacts]
        if len(digests) != len(set(digests)):
            raise ValueError("artifact inventory digests must be unique")
        transfer_digests = [transfer.digest_sha256 for transfer in self.transfers]
        if len(transfer_digests) != len(set(transfer_digests)):
            raise ValueError("artifact transfer digests must be unique")
        return self


class DeviceArtifactStageCommand(DeviceProtocolModel):
    type: Literal["device.artifact.stage"] = "device.artifact.stage"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    activation_identity: UUID
    resource: str = Field(min_length=1, max_length=200)
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    download_url: AnyUrl
    protected_digests: tuple[str, ...] = ()
    eviction_policy: Literal["lru"] = "lru"

    @field_validator("protected_digests")
    @classmethod
    def validate_protected_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("protected artifact digests must be unique")
        if any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in value):
            raise ValueError("protected artifact digests must be SHA-256 values")
        return value


class DeviceArtifactStageAck(DeviceProtocolModel):
    type: Literal["device.artifact.stage.ack"] = "device.artifact.stage.ack"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    activation_identity: UUID
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["completed", "error"]
    error: str | None = None

    @model_validator(mode="after")
    def validate_error(self) -> "DeviceArtifactStageAck":
        if (self.status == "error") != (self.error is not None):
            raise ValueError("artifact stage error detail must match status")
        return self


class DeviceArtifactCleanupCommand(DeviceProtocolModel):
    type: Literal["device.artifact.cleanup"] = "device.artifact.cleanup"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    digests: tuple[str, ...] = Field(min_length=1)

    @field_validator("digests")
    @classmethod
    def validate_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("cleanup artifact digests must be unique")
        if any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in value):
            raise ValueError("cleanup artifact digests must be SHA-256 values")
        return value


class DeviceArtifactCleanupAck(DeviceProtocolModel):
    type: Literal["device.artifact.cleanup.ack"] = "device.artifact.cleanup.ack"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    removed_digests: tuple[str, ...]
    rejected_digests: tuple[str, ...]


class DeviceArtifactActivateCommand(DeviceProtocolModel):
    type: Literal["device.artifact.activate"] = "device.artifact.activate"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    activation_identity: UUID
    resource: str = Field(min_length=1, max_length=200)
    digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_end_behavior: Literal["stop", "hold_last_frame", "loop"]
    duration_ms: int = Field(gt=0)
    frame_rate_numerator: int = Field(gt=0)
    frame_rate_denominator: int = Field(gt=0)
    frame_time_origin_ms: int = Field(ge=0)
    phase: Literal["prepare", "commit", "rollback"] = "commit"
    activation_at_unix_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_activation_phase(self) -> "DeviceArtifactActivateCommand":
        if self.phase == "commit" and self.activation_at_unix_ms is None:
            return self
        if self.phase == "prepare" and self.activation_at_unix_ms is None:
            raise ValueError("prepared activation requires a shared activation timestamp")
        if self.phase == "rollback" and self.activation_at_unix_ms is not None:
            raise ValueError("rollback activation cannot define an activation timestamp")
        return self


class DeviceArtifactActivateAck(DeviceProtocolModel):
    type: Literal["device.artifact.activate.ack"] = "device.artifact.activate.ack"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    activation_identity: UUID
    status: Literal["completed", "error"]
    error: str | None = None

    @model_validator(mode="after")
    def validate_error(self) -> "DeviceArtifactActivateAck":
        if (self.status == "error") != (self.error is not None):
            raise ValueError("artifact activation error detail must match status")
        return self


class DeviceMediaCommand(DeviceProtocolModel):
    type: Literal["device.media.command"] = "device.media.command"
    protocol_version: Literal[2] = DEVICE_PROTOCOL_VERSION
    request_id: UUID
    device_session_id: UUID
    controller_epoch: UUID
    command: MediaCommandName
    scene_id: str = Field(min_length=1, max_length=128)
    scene_instance_id: UUID
    playback_session_id: UUID
    asset_id: str | None = None
    end_behavior: Literal["stop", "hold_last_frame", "loop"] | None = None
    target_position_ms: int | None = Field(default=None, ge=0)
    # Omitted when unset so agents that predate these fields still accept commands.
    repeat_count: int | None = Field(
        default=None, ge=1, le=10000, exclude_if=lambda value: value is None
    )
    auto_advance: bool | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_command_fields(self) -> "DeviceMediaCommand":
        loads_media = self.command in {"prepare", "queue"}
        if self.command == "seek" and self.target_position_ms is None:
            raise ValueError("seek command requires target_position_ms")
        if self.command != "seek" and self.target_position_ms is not None:
            raise ValueError("target_position_ms is valid only for seek")
        if not loads_media and self.asset_id is not None:
            raise ValueError("asset_id is valid only for prepare or queue")
        if loads_media and self.end_behavior is None:
            raise ValueError(f"{self.command} command requires end_behavior")
        if not loads_media and self.end_behavior is not None:
            raise ValueError("end_behavior is valid only for prepare or queue")
        if not loads_media and self.repeat_count is not None:
            raise ValueError("repeat_count is valid only for prepare or queue")
        if self.repeat_count is not None and self.end_behavior == "loop":
            raise ValueError("repeat_count cannot be combined with end_behavior loop")
        if (self.command == "queue") != (self.auto_advance is not None):
            raise ValueError("auto_advance is required for queue and valid only for queue")
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
