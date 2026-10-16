from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.testclient import WebSocketDisconnect
from starlette.types import ASGIApp, Receive, Scope, Send

from cueverge.controller.runtime import ControllerRuntime
from cueverge.gui.contracts import Role
from cueverge.gui.device_app import create_device_app
from cueverge.gui.device_ca import DeviceCertificateAuthority
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.device_transport import (
    PEER_CERTIFICATE_STATE_KEY,
    peer_device_certificate,
)
from cueverge.gui.repository import GuiRepository
from cueverge.protocol.device_v2 import (
    DeviceCapabilityDocument,
    DeviceKind,
    DownstreamControlCapability,
    EnrollmentState,
    MediaPlaybackCapability,
    ProtocolRange,
)

DEVICE_ID = UUID("9c004799-7704-48f1-b5f4-f083628c7faa")
NOW = datetime(2026, 10, 13, 17, 0, tzinfo=UTC)
TOKEN = "pairing-token-that-is-never-stored-in-plaintext"


def test_authenticated_device_session_is_fenced_and_heartbeat_acknowledged(
    tmp_path: Path,
) -> None:
    repository, certificate_der, capabilities = _enrolled_device(tmp_path)
    sessions = DeviceSessionRegistry()
    app = _with_peer_certificate(
        create_device_app(repository, sessions),
        certificate_der,
    )
    session_id = uuid4()

    with TestClient(app).websocket_connect("/api/device/v2/agent/ws") as websocket:
        websocket.send_json(_hello(session_id, capabilities))
        welcome = websocket.receive_json()
        assert welcome["type"] == "device.welcome"
        assert welcome["device_id"] == str(DEVICE_ID)

        websocket.send_json(
            {
                "type": "device.heartbeat",
                "protocol_version": 2,
                "device_id": str(DEVICE_ID),
                "device_session_id": str(session_id),
                "controller_epoch": welcome["controller_epoch"],
                "sequence": 1,
                "device_monotonic_ms": 100,
                "output_scaling": {"pixels.main": 0.625},
            }
        )
        acknowledgment = websocket.receive_json()
        controller_receive_unix_ms = acknowledgment.pop("controller_receive_unix_ms")
        controller_send_unix_ms = acknowledgment.pop("controller_send_unix_ms")
        assert controller_receive_unix_ms > 0
        assert controller_send_unix_ms >= controller_receive_unix_ms
        assert acknowledgment == {
            "type": "device.heartbeat.ack",
            "protocol_version": 2,
            "sequence": 1,
            "accepted": True,
            "reason": None,
        }
        presence = repository.list_device_presence()
        assert len(presence) == 1
        assert presence[0].session_id == str(session_id)
        assert presence[0].disconnected_at_ms is None
        assert sessions.output_scale(DEVICE_ID, "pixels.main") == 0.625

    assert sessions.connected_device_ids() == ()
    assert repository.list_device_presence()[0].disconnected_at_ms is not None


def test_unknown_suspended_and_mismatched_devices_are_denied(tmp_path: Path) -> None:
    repository, certificate_der, capabilities = _enrolled_device(tmp_path)
    app = _with_peer_certificate(
        create_device_app(repository, DeviceSessionRegistry()),
        certificate_der,
    )

    with (
        pytest.raises(WebSocketDisconnect) as mismatch,
        TestClient(app).websocket_connect("/api/device/v2/agent/ws") as websocket,
    ):
        websocket.send_json(_hello(uuid4(), capabilities, device_id=uuid4()))
        websocket.receive_json()
    assert mismatch.value.code == 4403

    repository.set_device_enrollment_state(
        device_id=str(DEVICE_ID),
        state=EnrollmentState.SUSPENDED,
        actor="admin",
        role=Role.ADMINISTRATOR,
        occurred_at="2026-10-13T17:05:00Z",
    )
    with (
        pytest.raises(WebSocketDisconnect) as suspended,
        TestClient(app).websocket_connect("/api/device/v2/agent/ws"),
    ):
        pass
    assert suspended.value.code == 4403

    unknown_app = create_device_app(repository, DeviceSessionRegistry())
    with (
        pytest.raises(WebSocketDisconnect),
        TestClient(unknown_app).websocket_connect("/api/device/v2/agent/ws"),
    ):
        pass


def test_downstream_session_does_not_register_as_media_agent(tmp_path: Path) -> None:
    repository, certificate_der, capabilities = _enrolled_device(
        tmp_path,
        device_kind=DeviceKind.DOWNSTREAM_CONTROLLER,
    )
    runtime = ControllerRuntime()
    app = _with_peer_certificate(
        create_device_app(repository, DeviceSessionRegistry(), runtime),
        certificate_der,
    )

    with TestClient(app).websocket_connect("/api/device/v2/agent/ws") as websocket:
        websocket.send_json(_hello(uuid4(), capabilities))
        welcome = websocket.receive_json()
        assert welcome["type"] == "device.welcome"
        assert runtime.connected_agent_count == 0


def test_suspension_terminates_an_existing_session_on_next_message(
    tmp_path: Path,
) -> None:
    repository, certificate_der, capabilities = _enrolled_device(tmp_path)
    sessions = DeviceSessionRegistry()
    app = _with_peer_certificate(
        create_device_app(repository, sessions),
        certificate_der,
    )
    session_id = uuid4()

    with (
        pytest.raises(WebSocketDisconnect) as disconnected,
        TestClient(app).websocket_connect("/api/device/v2/agent/ws") as websocket,
    ):
        websocket.send_json(_hello(session_id, capabilities))
        welcome = websocket.receive_json()
        repository.set_device_enrollment_state(
            device_id=str(DEVICE_ID),
            state=EnrollmentState.SUSPENDED,
            actor="admin",
            role=Role.ADMINISTRATOR,
            occurred_at="2026-10-13T17:05:00Z",
        )
        websocket.send_json(
            {
                "type": "device.heartbeat",
                "protocol_version": 2,
                "device_id": str(DEVICE_ID),
                "device_session_id": str(session_id),
                "controller_epoch": welcome["controller_epoch"],
                "sequence": 1,
                "device_monotonic_ms": 100,
            }
        )
        websocket.receive_json()
    assert disconnected.value.code == 4403
    assert sessions.connected_device_ids() == ()


def test_peer_certificate_requires_exactly_one_device_uri(tmp_path: Path) -> None:
    _, certificate_der, _ = _enrolled_device(tmp_path)
    peer = peer_device_certificate({"state": {PEER_CERTIFICATE_STATE_KEY: certificate_der}})
    assert peer.device_id == DEVICE_ID

    with pytest.raises(ValueError, match="unavailable"):
        peer_device_certificate({"state": {}})


def _enrolled_device(
    tmp_path: Path,
    *,
    device_kind: DeviceKind = DeviceKind.DIRECT_MEDIA_AGENT,
) -> tuple[GuiRepository, bytes, DeviceCapabilityDocument]:
    authority = DeviceCertificateAuthority(tmp_path / "device-ca", SecretStr("ca-password"))
    authority.provision(now=NOW)
    private_key = ec.generate_private_key(ec.SECP256R1())
    request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .sign(private_key, hashes.SHA256())
    )
    credential = authority.issue_device_certificate(
        device_id=DEVICE_ID,
        certificate_signing_request_pem=request.public_bytes(serialization.Encoding.PEM).decode(
            "ascii"
        ),
        now=NOW,
    )
    capabilities = _capabilities(device_kind)
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=device_kind,
        created_by="admin",
        created_at=NOW.isoformat(),
        expires_at=int(NOW.timestamp()) + 300,
        role=Role.ADMINISTRATOR,
    )
    repository.consume_pairing_token_and_create_device(
        token=TOKEN,
        now=int(NOW.timestamp()),
        occurred_at=NOW.isoformat(),
        device_id=str(DEVICE_ID),
        display_name="CuevergeClient01",
        device_kind=device_kind,
        capabilities=capabilities,
        certificate_serial=credential.certificate_serial,
        certificate_fingerprint_sha256=credential.fingerprint_sha256,
        certificate_issued_at=credential.issued_at.isoformat(),
        certificate_expires_at=credential.expires_at.isoformat(),
    )
    certificate = x509.load_pem_x509_certificate(credential.certificate_pem.encode("ascii"))
    return (
        repository,
        certificate.public_bytes(serialization.Encoding.DER),
        capabilities,
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


def _hello(
    session_id: UUID,
    capabilities: DeviceCapabilityDocument,
    *,
    device_id: UUID = DEVICE_ID,
) -> dict[str, Any]:
    return {
        "type": "device.hello",
        "protocol_version": 2,
        "device_id": str(device_id),
        "device_session_id": str(session_id),
        "capabilities": capabilities.model_dump(mode="json"),
    }


def _with_peer_certificate(app: ASGIApp, certificate_der: bytes) -> ASGIApp:
    async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        updated_scope = dict(scope)
        state = dict(scope.get("state", {}))
        state[PEER_CERTIFICATE_STATE_KEY] = certificate_der
        updated_scope["state"] = state
        await app(updated_scope, receive, send)

    return wrapped
