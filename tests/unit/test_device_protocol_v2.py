from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from cueverge.protocol.device_v2 import (
    ArtifactCacheCapability,
    DeviceArtifactActivateAck,
    DeviceArtifactActivateCommand,
    DeviceArtifactStageAck,
    DeviceArtifactStageCommand,
    DeviceCapabilityDocument,
    DeviceCertificate,
    DeviceEnrollmentRequest,
    DeviceHeartbeat,
    DeviceIdentity,
    DeviceKind,
    DeviceMediaCommand,
    DownstreamControlCapability,
    EnrollmentState,
    MediaPlaybackCapability,
    ProtocolRange,
    TimingAdjustment,
    TimingCalibrationCapability,
)

DEVICE_ID = UUID("9c004799-7704-48f1-b5f4-f083628c7faa")


def test_direct_media_agent_capabilities_are_typed_and_versioned() -> None:
    document = _media_capabilities()

    assert document.protocol.minimum == 2
    assert document.device_kind is DeviceKind.DIRECT_MEDIA_AGENT
    assert [capability.kind for capability in document.capabilities] == [
        "media_playback",
        "artifact_cache",
    ]


def test_pairing_token_is_masked_when_serialized() -> None:
    request = DeviceEnrollmentRequest(
        pairing_token="single-use-secret",
        display_name="CuevergeClient01",
        certificate_signing_request_pem="-----BEGIN CERTIFICATE REQUEST-----",
        capabilities=_media_capabilities(),
    )

    assert request.model_dump()["pairing_token"] != "single-use-secret"
    assert "single-use-secret" not in request.model_dump_json()


def test_generic_timing_calibration_capability_is_typed_and_bounded() -> None:
    capability = TimingCalibrationCapability(
        actuator_kinds=("media", "audio", "pixels", "relay"),
        onset=TimingAdjustment(
            minimum_ms=-5000,
            maximum_ms=5000,
            resolution_ms=5,
            adjustable_while_active=True,
        ),
        release=TimingAdjustment(
            minimum_ms=-1000,
            maximum_ms=1000,
            resolution_ms=10,
        ),
        safe_test_procedure="maintenance_reference",
        supports_ab_comparison=True,
    )
    document = _media_capabilities().model_copy(
        update={"capabilities": (*_media_capabilities().capabilities, capability)}
    )

    reloaded = DeviceCapabilityDocument.model_validate_json(document.model_dump_json())
    timing = reloaded.capabilities[-1]
    assert timing.kind == "timing_calibration"
    assert timing.onset is not None
    assert timing.onset.adjustable_while_active is True

    with pytest.raises(ValidationError, match="divisible"):
        TimingAdjustment(minimum_ms=0, maximum_ms=100, resolution_ms=30)


def test_device_reported_output_scaling_is_bounded() -> None:
    values = {
        "device_id": DEVICE_ID,
        "device_session_id": uuid4(),
        "controller_epoch": uuid4(),
        "sequence": 1,
        "device_monotonic_ms": 100,
        "output_scaling": {"pixels.main": 0.625},
    }

    heartbeat = DeviceHeartbeat.model_validate(values)

    assert heartbeat.output_scaling == {"pixels.main": 0.625}
    values["output_scaling"] = {"pixels.main": 1.1}
    with pytest.raises(ValidationError):
        DeviceHeartbeat.model_validate(values)


def test_device_kind_restricts_authority_capabilities() -> None:
    with pytest.raises(ValidationError, match="cannot declare downstream control"):
        DeviceCapabilityDocument(
            protocol=ProtocolRange(minimum=2, maximum=2),
            device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
            software_version="0.1.0",
            capabilities=(DownstreamControlCapability(operations=("query_status",)),),
        )

    with pytest.raises(ValidationError, match="must declare downstream control"):
        DeviceCapabilityDocument(
            protocol=ProtocolRange(minimum=2, maximum=2),
            device_kind=DeviceKind.DOWNSTREAM_CONTROLLER,
            software_version="0.1.0",
            capabilities=(MediaPlaybackCapability(codecs=("h264",), containers=("mp4",)),),
        )


def test_capability_documents_reject_invalid_ranges_and_capacity() -> None:
    with pytest.raises(ValidationError, match="must support device protocol version 2"):
        ProtocolRange(minimum=3, maximum=4)

    with pytest.raises(ValidationError, match="free bytes cannot exceed total bytes"):
        ArtifactCacheCapability(
            total_bytes=100,
            free_bytes=101,
            supports_pinning=True,
            supports_resumable_transfer=True,
        )


def test_media_commands_are_session_fenced_and_command_specific() -> None:
    values = {
        "request_id": uuid4(),
        "device_session_id": uuid4(),
        "controller_epoch": uuid4(),
        "scene_id": "ambient",
        "scene_instance_id": uuid4(),
        "playback_session_id": uuid4(),
    }
    command = DeviceMediaCommand(
        **values,
        command="prepare",
        asset_id="media/ambient.mp4",
        end_behavior="hold_last_frame",
    )
    assert command.protocol_version == 2
    assert "agent_id" not in command.model_dump()

    with pytest.raises(ValidationError, match="requires target_position_ms"):
        DeviceMediaCommand(**values, command="seek")
    with pytest.raises(ValidationError, match="valid only for prepare"):
        DeviceMediaCommand(**values, command="play", asset_id="media/ambient.mp4")
    with pytest.raises(ValidationError, match="requires end_behavior"):
        DeviceMediaCommand(**values, command="prepare", asset_id="media/ambient.mp4")


def test_artifact_stage_and_activation_messages_require_matching_error_state() -> None:
    values = {
        "request_id": uuid4(),
        "device_session_id": uuid4(),
        "controller_epoch": uuid4(),
        "activation_identity": uuid4(),
    }
    stage = DeviceArtifactStageCommand(
        **values,
        resource="projector.left",
        digest_sha256="a" * 64,
        size_bytes=1024,
        download_url="https://controller.example/artifact",
    )
    activate = DeviceArtifactActivateCommand(
        **values,
        resource=stage.resource,
        digest_sha256=stage.digest_sha256,
        media_end_behavior="hold_last_frame",
        duration_ms=1000,
        frame_rate_numerator=30,
        frame_rate_denominator=1,
        frame_time_origin_ms=0,
    )
    assert stage.protocol_version == 2
    assert activate.media_end_behavior == "hold_last_frame"
    with pytest.raises(ValidationError, match="must match status"):
        DeviceArtifactStageAck(
            **values,
            digest_sha256=stage.digest_sha256,
            status="error",
        )
    with pytest.raises(ValidationError, match="must match status"):
        DeviceArtifactActivateAck(
            **values,
            status="completed",
            error="unexpected",
        )


def test_certificate_and_enrolled_identity_require_complete_valid_state() -> None:
    issued_at = datetime(2026, 10, 13, tzinfo=UTC)
    certificate = DeviceCertificate(
        device_id=DEVICE_ID,
        certificate_serial="01",
        fingerprint_sha256="a" * 64,
        certificate_pem="-----BEGIN CERTIFICATE-----",
        trust_chain_pem=("-----BEGIN CERTIFICATE-----",),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(days=30),
    )
    identity = DeviceIdentity(
        device_id=DEVICE_ID,
        display_name="CuevergeClient01",
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        enrollment_state=EnrollmentState.ENROLLED,
        active_certificate_serial=certificate.certificate_serial,
        capabilities=_media_capabilities(),
    )

    assert identity.device_id == certificate.device_id

    with pytest.raises(ValidationError, match="expiry must be after issuance"):
        DeviceCertificate.model_validate(
            {
                **certificate.model_dump(),
                "expires_at": issued_at - timedelta(seconds=1),
            }
        )

    with pytest.raises(ValidationError, match="require an active certificate"):
        DeviceIdentity(
            device_id=DEVICE_ID,
            display_name="Incomplete",
            device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
            enrollment_state=EnrollmentState.ENROLLED,
        )


def _media_capabilities() -> DeviceCapabilityDocument:
    return DeviceCapabilityDocument(
        protocol=ProtocolRange(minimum=2, maximum=2),
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        software_version="0.1.0",
        capabilities=(
            MediaPlaybackCapability(
                codecs=("h264", "hevc"),
                containers=("mp4", "matroska"),
            ),
            ArtifactCacheCapability(
                total_bytes=500_000_000_000,
                free_bytes=400_000_000_000,
                supports_pinning=True,
                supports_resumable_transfer=True,
            ),
        ),
    )
