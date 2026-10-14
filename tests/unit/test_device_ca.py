import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID
from pydantic import SecretStr

from cueverge.gui.device_ca import (
    DeviceCertificateAuthority,
    DeviceCertificateAuthorityError,
)

DEVICE_ID = UUID("9c004799-7704-48f1-b5f4-f083628c7faa")
NOW = datetime(2026, 10, 13, 17, 0, tzinfo=UTC)


def test_ca_is_encrypted_protected_and_stable(tmp_path: Path) -> None:
    authority = DeviceCertificateAuthority(tmp_path / "device-ca", SecretStr("ca-password"))

    first = authority.provision(now=NOW)
    second = authority.provision(now=NOW + timedelta(days=1))

    assert first.fingerprint(hashes.SHA256()) == second.fingerprint(hashes.SHA256())
    assert b"ENCRYPTED PRIVATE KEY" in authority.key_path.read_bytes()
    assert stat.S_IMODE(authority.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(authority.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(authority.certificate_path.stat().st_mode) == 0o644


def test_issued_certificate_is_client_only_and_bound_to_device_uuid(
    tmp_path: Path,
) -> None:
    authority = DeviceCertificateAuthority(tmp_path / "device-ca", SecretStr("ca-password"))
    ca_certificate = authority.provision(now=NOW)
    private_key = ec.generate_private_key(ec.SECP256R1())

    credential = authority.issue_device_certificate(
        device_id=DEVICE_ID,
        certificate_signing_request_pem=_csr(private_key),
        now=NOW,
    )

    certificate = x509.load_pem_x509_certificate(credential.certificate_pem.encode())
    ca_certificate.public_key().verify(
        certificate.signature,
        certificate.tbs_certificate_bytes,
        ec.ECDSA(certificate.signature_hash_algorithm),
    )
    assert credential.device_id == DEVICE_ID
    assert credential.expires_at == NOW + timedelta(days=30)
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert certificate.extensions.get_extension_for_class(
        x509.ExtendedKeyUsage
    ).value == x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH])
    assert certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.UniformResourceIdentifier) == [
        f"urn:cueverge:device:{DEVICE_ID}"
    ]
    assert (
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        not in credential.certificate_pem.encode()
    )


def test_ca_rejects_invalid_csr_weak_key_and_wrong_password(tmp_path: Path) -> None:
    root = tmp_path / "device-ca"
    authority = DeviceCertificateAuthority(root, SecretStr("ca-password"))
    authority.provision(now=NOW)

    with pytest.raises(DeviceCertificateAuthorityError, match="invalid PEM"):
        authority.issue_device_certificate(
            device_id=DEVICE_ID,
            certificate_signing_request_pem="not a CSR",
            now=NOW,
        )

    weak_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(DeviceCertificateAuthorityError, match="at least 3072 bits"):
        authority.issue_device_certificate(
            device_id=DEVICE_ID,
            certificate_signing_request_pem=_csr(weak_key),
            now=NOW,
        )

    wrong_password = DeviceCertificateAuthority(root, SecretStr("wrong-password"))
    with pytest.raises(DeviceCertificateAuthorityError, match="could not be loaded"):
        wrong_password.provision(now=NOW)


def test_ca_fails_closed_on_partial_or_mismatched_state(tmp_path: Path) -> None:
    partial = DeviceCertificateAuthority(tmp_path / "partial", SecretStr("ca-password"))
    partial.root.mkdir()
    partial.key_path.write_text("orphaned", encoding="ascii")
    with pytest.raises(DeviceCertificateAuthorityError, match="must either both exist"):
        partial.provision(now=NOW)

    first = DeviceCertificateAuthority(tmp_path / "first", SecretStr("ca-password"))
    second = DeviceCertificateAuthority(tmp_path / "second", SecretStr("ca-password"))
    first.provision(now=NOW)
    second.provision(now=NOW)
    first.certificate_path.write_bytes(second.certificate_path.read_bytes())
    with pytest.raises(DeviceCertificateAuthorityError, match="does not match"):
        first.provision(now=NOW)


def test_listener_certificate_contains_only_configured_server_identities(
    tmp_path: Path,
) -> None:
    authority = DeviceCertificateAuthority(tmp_path / "device-ca", SecretStr("ca-password"))
    authority.provision(now=NOW)

    certificate_path, key_path = authority.provision_server_certificate(
        ("controller.example.test", "192.168.1.10"),
        now=NOW,
    )

    certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
    alternative_names = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    assert alternative_names.get_values_for_type(x509.DNSName) == ["controller.example.test"]
    assert [str(value) for value in alternative_names.get_values_for_type(x509.IPAddress)] == [
        "192.168.1.10"
    ]
    assert certificate.extensions.get_extension_for_class(
        x509.ExtendedKeyUsage
    ).value == x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH])
    assert b"ENCRYPTED PRIVATE KEY" in key_path.read_bytes()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    assert authority.provision_server_certificate(
        ("controller.example.test", "192.168.1.10"),
        now=NOW + timedelta(days=1),
    ) == (certificate_path, key_path)
    with pytest.raises(DeviceCertificateAuthorityError, match="do not match"):
        authority.provision_server_certificate(
            ("other.example.test",),
            now=NOW + timedelta(days=1),
        )


def test_listener_certificate_rejects_ambiguous_identities(tmp_path: Path) -> None:
    authority = DeviceCertificateAuthority(tmp_path / "device-ca", SecretStr("ca-password"))
    authority.provision(now=NOW)

    for identities in [
        (),
        ("localhost",),
        ("*.example.test",),
        ("controller.example.test", "CONTROLLER.EXAMPLE.TEST"),
    ]:
        with pytest.raises(ValueError):
            authority.provision_server_certificate(identities, now=NOW)


def _csr(
    private_key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey,
) -> str:
    request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .sign(private_key, hashes.SHA256())
    )
    return request.public_bytes(serialization.Encoding.PEM).decode("ascii")
