import sqlite3
from pathlib import Path

import pytest

from cueverge.gui.contracts import Role
from cueverge.gui.repository import (
    DeviceCertificateAuthorizationError,
    DeviceStateTransitionError,
    GuiRepository,
    PairingTokenNotUsableError,
    ResourceAssignmentConflictError,
    ResourceAssignmentInUseError,
)
from cueverge.protocol.device_v2 import (
    DeviceCapabilityDocument,
    DeviceKind,
    DownstreamControlCapability,
    EnrollmentState,
    MediaPlaybackCapability,
    ProtocolRange,
)

TOKEN = "pairing-token-that-is-never-stored-in-plaintext"


def test_pairing_token_is_digest_only_single_use_and_kind_scoped(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    pairing = repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        created_by="admin",
        created_at="2026-10-13T17:00:00Z",
        expires_at=1000,
        role=Role.ADMINISTRATOR,
    )

    database_bytes = repository.database_path.read_bytes()
    assert TOKEN.encode() not in database_bytes
    assert pairing.consumed_at is None

    with pytest.raises(PairingTokenNotUsableError):
        _enroll(
            repository,
            token=TOKEN,
            now=900,
            device_kind=DeviceKind.DOWNSTREAM_CONTROLLER,
        )

    enrolled = _enroll(repository, token=TOKEN, now=900)
    assert enrolled.enrollment_state is EnrollmentState.ENROLLED
    assert enrolled.capabilities.device_kind is DeviceKind.DIRECT_MEDIA_AGENT

    with pytest.raises(PairingTokenNotUsableError):
        _enroll(repository, token=TOKEN, now=901, device_id="second-device")


def test_expired_token_cannot_create_device(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        created_by="admin",
        created_at="2026-10-13T17:00:00Z",
        expires_at=1000,
        role=Role.ADMINISTRATOR,
    )

    with pytest.raises(PairingTokenNotUsableError):
        _enroll(repository, token=TOKEN, now=1000)

    assert repository.list_devices() == ()


def test_discovery_link_follows_pairing_token_to_enrolled_device(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    discovered = repository.upsert_discovered_endpoint(
        base_url="https://controller.local",
        device_kind=DeviceKind.DOWNSTREAM_CONTROLLER,
        software_version="0.1.0",
        protocol_minimum=2,
        protocol_maximum=2,
        capability_kinds=("downstream_control",),
        compatible=True,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-13T17:00:00Z",
    )
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DOWNSTREAM_CONTROLLER,
        created_by="admin",
        created_at="2026-10-13T17:00:00Z",
        expires_at=1000,
        role=Role.ADMINISTRATOR,
        discovered_endpoint_id=discovered.id,
    )

    enrolled = _enroll(
        repository,
        token=TOKEN,
        now=900,
        device_kind=DeviceKind.DOWNSTREAM_CONTROLLER,
    )

    linked = repository.get_discovered_endpoint(discovered.id)
    assert linked.state == "enrolled"
    assert linked.enrolled_device_id == enrolled.id


def test_device_lifecycle_is_durable_audited_and_irreversibly_revoked(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        created_by="admin",
        created_at="2026-10-13T17:00:00Z",
        expires_at=1000,
        role=Role.ADMINISTRATOR,
    )
    _enroll(repository, token=TOKEN, now=900)

    suspended = repository.set_device_enrollment_state(
        device_id="device-1",
        state=EnrollmentState.SUSPENDED,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-13T17:02:00Z",
    )
    assert suspended.enrollment_state is EnrollmentState.SUSPENDED

    resumed = repository.set_device_enrollment_state(
        device_id="device-1",
        state=EnrollmentState.ENROLLED,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-13T17:03:00Z",
    )
    assert resumed.enrollment_state is EnrollmentState.ENROLLED

    revoked = repository.set_device_enrollment_state(
        device_id="device-1",
        state=EnrollmentState.REVOKED,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-13T17:04:00Z",
    )
    assert revoked.enrollment_state is EnrollmentState.REVOKED
    with pytest.raises(DeviceStateTransitionError):
        repository.set_device_enrollment_state(
            device_id="device-1",
            state=EnrollmentState.ENROLLED,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-13T17:05:00Z",
        )

    reloaded = GuiRepository(repository.database_path)
    reloaded.migrate()
    assert reloaded.get_device("device-1").enrollment_state is EnrollmentState.REVOKED
    actions = [event.action for event in reloaded.list_audit_events()]
    assert "device.pairing_token.create" in actions
    assert "device.enroll" in actions
    assert "device.suspended" in actions
    assert "device.revoked" in actions

    with sqlite3.connect(repository.database_path) as connection:
        revoked_at = connection.execute(
            "SELECT revoked_at FROM device_certificates WHERE device_id = 'device-1'"
        ).fetchone()
    assert revoked_at == ("2026-10-13T17:04:00Z",)


def test_resource_assignments_are_revisioned_capability_checked_and_audited(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        created_by="admin",
        created_at="2026-10-13T17:00:00Z",
        expires_at=1000,
        role=Role.ADMINISTRATOR,
    )
    _enroll(repository, token=TOKEN, now=900)

    created = repository.set_resource_assignment(
        resource="projector.window-left",
        device_id="device-1",
        capability_kind="media_playback",
        device_output="HDMI-A-1",
        expected_revision=None,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-13T17:02:00Z",
    )
    assert created.revision == 1
    assert created.device_output == "HDMI-A-1"

    with pytest.raises(ResourceAssignmentConflictError) as conflict:
        repository.set_resource_assignment(
            resource=created.resource,
            device_id="device-1",
            capability_kind="media_playback",
            device_output=None,
            expected_revision=None,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-13T17:03:00Z",
        )
    assert conflict.value.current_revision == 1

    with pytest.raises(ValueError, match="does not declare"):
        repository.set_resource_assignment(
            resource="roofline",
            device_id="device-1",
            capability_kind="pixel_output",
            device_output=None,
            expected_revision=None,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-13T17:03:00Z",
        )

    reloaded = GuiRepository(repository.database_path)
    reloaded.migrate()
    assert reloaded.list_resource_assignments() == (created,)
    reloaded.mark_device_connected(
        device_id="device-1",
        session_id="connected",
        observed_at_ms=1000,
    )
    with pytest.raises(ResourceAssignmentInUseError):
        reloaded.set_resource_assignment(
            resource=created.resource,
            device_id="device-1",
            capability_kind="media_playback",
            device_output=None,
            expected_revision=1,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-13T17:03:30Z",
            disconnected_before_ms=500,
        )
    reloaded.mark_device_disconnected(
        device_id="device-1",
        session_id="connected",
        observed_at_ms=1100,
    )
    reloaded.remove_resource_assignment(
        resource=created.resource,
        expected_revision=1,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-13T17:04:00Z",
    )
    assert reloaded.list_resource_assignments() == ()
    actions = [event.action for event in reloaded.list_audit_events()]
    assert "resource.assign" in actions
    assert "resource.unassign" in actions


def test_suspended_device_cannot_receive_new_resource_assignment(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        created_by="admin",
        created_at="2026-10-13T17:00:00Z",
        expires_at=1000,
        role=Role.ADMINISTRATOR,
    )
    _enroll(repository, token=TOKEN, now=900)
    repository.set_device_enrollment_state(
        device_id="device-1",
        state=EnrollmentState.SUSPENDED,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-13T17:02:00Z",
    )

    with pytest.raises(DeviceStateTransitionError, match="enrolled device"):
        repository.set_resource_assignment(
            resource="projector.window-left",
            device_id="device-1",
            capability_kind="media_playback",
            device_output=None,
            expected_revision=None,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-13T17:03:00Z",
        )


def test_device_presence_is_durable_and_fenced_by_session(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        created_by="admin",
        created_at="2026-10-13T17:00:00Z",
        expires_at=1000,
        role=Role.ADMINISTRATOR,
    )
    _enroll(repository, token=TOKEN, now=900)

    repository.mark_device_connected(
        device_id="device-1",
        session_id="current",
        observed_at_ms=1000,
    )
    repository.mark_device_seen(
        device_id="device-1",
        session_id="stale",
        observed_at_ms=2000,
    )
    repository.mark_device_timing(
        device_id="device-1",
        session_id="current",
        latency_ms=12.5,
        clock_offset_ms=-3.0,
    )
    repository.replace_device_artifact_inventory(
        device_id="device-1",
        session_id="current",
        observed_at_ms=2100,
        total_bytes=1_000_000,
        free_bytes=750_000,
        supports_pinning=True,
        supports_resumable_transfer=True,
        artifacts=(("d" * 64, 1234, True),),
        transfers=(
            {
                "digest_sha256": "e" * 64,
                "received_bytes": 500,
                "total_bytes": 1000,
                "state": "active",
                "fault": None,
            },
        ),
        faults=(),
    )
    assert repository.list_device_presence()[0].last_seen_at_ms == 1000
    repository.mark_device_seen(
        device_id="device-1",
        session_id="current",
        observed_at_ms=2000,
    )
    repository.mark_device_disconnected(
        device_id="device-1",
        session_id="stale",
        observed_at_ms=2500,
    )
    assert repository.list_device_presence()[0].disconnected_at_ms is None
    repository.mark_device_disconnected(
        device_id="device-1",
        session_id="current",
        observed_at_ms=3000,
    )

    reloaded = GuiRepository(repository.database_path)
    reloaded.migrate()
    presence = reloaded.list_device_presence()
    assert len(presence) == 1
    assert presence[0].last_seen_at_ms == 2000
    assert presence[0].disconnected_at_ms == 3000
    assert presence[0].latency_ms == 12.5
    assert presence[0].clock_offset_ms == -3.0
    artifact_status = reloaded.list_device_artifact_statuses()[0]
    assert artifact_status.artifact_count == 1
    assert artifact_status.artifact_bytes == 1234
    assert artifact_status.pinned_count == 1
    assert artifact_status.supports_pinning
    assert artifact_status.supports_resumable_transfer
    assert artifact_status.transfers[0]["state"] == "active"
    assert reloaded.list_device_artifacts("device-1")[0].digest_sha256 == "d" * 64
    assert reloaded.list_device_artifacts("device-1")[0].pinned


def test_certificate_rotation_activates_only_after_replacement_connects(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        created_by="admin",
        created_at="2026-10-13T17:00:00Z",
        expires_at=1000,
        role=Role.ADMINISTRATOR,
    )
    original = _enroll(repository, token=TOKEN, now=900)

    staged = repository.stage_device_certificate_rotation(
        device_id=original.id,
        expected_current_serial=original.active_certificate_serial,
        certificate_serial="serial-rotated",
        certificate_fingerprint_sha256="c" * 64,
        certificate_issued_at="2026-10-20T17:00:00Z",
        certificate_expires_at="2026-11-19T17:00:00Z",
        occurred_at="2026-10-20T17:00:00Z",
    )

    assert staged.active_certificate_serial == original.active_certificate_serial
    assert (
        repository.authorize_device_certificate(
            device_id=original.id,
            certificate_serial=original.active_certificate_serial,
            certificate_fingerprint_sha256=original.certificate_fingerprint_sha256,
        ).id
        == original.id
    )
    assert (
        repository.authorize_device_certificate(
            device_id=original.id,
            certificate_serial="serial-rotated",
            certificate_fingerprint_sha256="c" * 64,
        ).id
        == original.id
    )
    with pytest.raises(DeviceCertificateAuthorizationError):
        repository.authorize_device_certificate(
            device_id=original.id,
            certificate_serial=original.active_certificate_serial,
            certificate_fingerprint_sha256=original.certificate_fingerprint_sha256,
        )
    assert [event.action for event in repository.list_audit_events()[:2]] == [
        "device.certificate.rotate.activate",
        "device.certificate.rotate.stage",
    ]


def test_device_connection_policy_defaults_outside_store_and_is_durable(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        created_by="admin",
        created_at="2026-10-13T17:00:00Z",
        expires_at=1000,
        role=Role.ADMINISTRATOR,
    )
    _enroll(repository, token=TOKEN, now=900)
    assert repository.list_device_connection_policies() == {}

    repository.set_device_connection_policy(
        device_id="device-1",
        policy="on_demand",
        actor="admin",
        occurred_at="2026-10-13T17:10:00Z",
    )

    reloaded = GuiRepository(repository.database_path)
    reloaded.migrate()
    assert reloaded.list_device_connection_policies() == {"device-1": "on_demand"}


def _repository(tmp_path: Path) -> GuiRepository:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    return repository


def _enroll(
    repository: GuiRepository,
    *,
    token: str,
    now: int,
    device_id: str = "device-1",
    device_kind: DeviceKind = DeviceKind.DIRECT_MEDIA_AGENT,
):
    return repository.consume_pairing_token_and_create_device(
        token=token,
        now=now,
        occurred_at="2026-10-13T17:01:00Z",
        device_id=device_id,
        display_name="CuevergeClient01",
        device_kind=device_kind,
        capabilities=_capabilities(device_kind),
        certificate_serial=f"serial-{device_id}",
        certificate_fingerprint_sha256="a" * 64 if device_id == "device-1" else "b" * 64,
        certificate_issued_at="2026-10-13T17:01:00Z",
        certificate_expires_at="2026-11-12T17:01:00Z",
    )


def _capabilities(
    device_kind: DeviceKind = DeviceKind.DIRECT_MEDIA_AGENT,
) -> DeviceCapabilityDocument:
    capabilities = (
        (MediaPlaybackCapability(codecs=("h264",), containers=("mp4",)),)
        if device_kind is DeviceKind.DIRECT_MEDIA_AGENT
        else (DownstreamControlCapability(operations=("query_status",)),)
    )
    return DeviceCapabilityDocument(
        protocol=ProtocolRange(minimum=2, maximum=2),
        device_kind=device_kind,
        software_version="0.1.0",
        capabilities=capabilities,
    )
