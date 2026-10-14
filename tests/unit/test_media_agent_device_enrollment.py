import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import SecretStr

from cueverge.media_agent.device_enrollment import enroll_device
from cueverge.protocol.device_v2 import (
    DeviceCapabilityDocument,
    DeviceKind,
    MediaPlaybackCapability,
    ProtocolRange,
)


def test_enrollment_generates_local_key_and_persists_verified_credentials(
    tmp_path: Path,
) -> None:
    credential_root = tmp_path / "device-v2"
    captured: dict[str, Any] = {}

    def transport(url: str, payload: dict[str, Any]) -> bytes:
        captured.update(payload)
        request = x509.load_pem_x509_csr(
            str(payload["certificate_signing_request_pem"]).encode("ascii")
        )
        response = _enrollment_response(request)
        return response

    identity = enroll_device(
        enrollment_url="https://cueverge.example/api/device/v2/enroll",
        pairing_token=SecretStr("single-use-secret"),
        display_name="CuevergeClient01",
        capabilities=_capabilities(),
        credential_root=credential_root,
        transport=transport,
    )

    assert captured["pairing_token"] == "single-use-secret"
    assert identity.device_id
    assert {path.name for path in credential_root.iterdir()} == {
        "private-key.pem",
        "certificate.pem",
        "ca-certificates.pem",
        "device.json",
    }
    assert credential_root.stat().st_mode & 0o777 == 0o700
    assert (credential_root / "private-key.pem").stat().st_mode & 0o777 == 0o600
    assert b"single-use-secret" not in b"".join(
        path.read_bytes() for path in credential_root.iterdir()
    )
    private_key = serialization.load_pem_private_key(
        (credential_root / "private-key.pem").read_bytes(),
        password=None,
    )
    certificate = x509.load_pem_x509_certificate((credential_root / "certificate.pem").read_bytes())
    assert certificate.public_key().public_numbers() == private_key.public_key().public_numbers()


def test_enrollment_refuses_existing_directory_before_consuming_token(
    tmp_path: Path,
) -> None:
    credential_root = tmp_path / "device-v2"
    credential_root.mkdir()
    called = False

    def transport(url: str, payload: dict[str, Any]) -> bytes:
        nonlocal called
        called = True
        raise AssertionError("transport must not be called")

    with pytest.raises(FileExistsError):
        enroll_device(
            enrollment_url="https://cueverge.example/api/device/v2/enroll",
            pairing_token=SecretStr("single-use-secret"),
            display_name="CuevergeClient01",
            capabilities=_capabilities(),
            credential_root=credential_root,
            transport=transport,
        )
    assert called is False


def test_enrollment_requires_server_authenticated_https(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        enroll_device(
            enrollment_url="http://192.168.1.10:8000/api/device/v2/enroll",
            pairing_token=SecretStr("single-use-secret"),
            display_name="CuevergeClient01",
            capabilities=_capabilities(),
            credential_root=tmp_path / "device-v2",
            transport=lambda _url, _payload: b"",
        )


def _capabilities() -> DeviceCapabilityDocument:
    return DeviceCapabilityDocument(
        protocol=ProtocolRange(minimum=2, maximum=2),
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        software_version="0.1.0",
        capabilities=(
            MediaPlaybackCapability(
                codecs=("h264",),
                containers=("mp4",),
                max_outputs=1,
            ),
        ),
    )


def _enrollment_response(request: x509.CertificateSigningRequest) -> bytes:
    now = datetime.now(UTC)
    device_id = uuid4()
    ca_key = ec.generate_private_key(ec.SECP384R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA384())
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(device_id))]))
        .issuer_name(ca_name)
        .public_key(request.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"urn:cueverge:device:{device_id}")]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=True,
        )
        .sign(ca_key, hashes.SHA384())
    )
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    ca_pem = ca_certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return (
        "{"
        '"protocol_version":2,'
        '"enrollment_state":"enrolled",'
        '"credential":{'
        f'"device_id":"{device_id}",'
        f'"certificate_serial":"{certificate.serial_number:x}",'
        f'"fingerprint_sha256":"{certificate.fingerprint(hashes.SHA256()).hex()}",'
        f'"certificate_pem":{_json_string(certificate_pem)},'
        f'"trust_chain_pem":[{_json_string(ca_pem)}],'
        f'"issued_at":"{now.isoformat()}",'
        f'"expires_at":"{(now + timedelta(days=30)).isoformat()}"'
        "}}"
    ).encode()


def _json_string(value: str) -> str:
    return json.dumps(value)
