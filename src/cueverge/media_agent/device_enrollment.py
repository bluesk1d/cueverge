import argparse
import getpass
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, SecretStr

from cueverge.protocol.device_v2 import (
    DEVICE_PROTOCOL_VERSION,
    DeviceCapabilityDocument,
    DeviceEnrollmentResponse,
)

EnrollmentTransport = Callable[[str, dict[str, Any]], bytes]


class StoredDeviceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    device_id: UUID
    enrollment_url: str
    capabilities: DeviceCapabilityDocument


def enroll_device(
    *,
    enrollment_url: str,
    pairing_token: SecretStr,
    display_name: str,
    capabilities: DeviceCapabilityDocument,
    credential_root: Path,
    transport: EnrollmentTransport | None = None,
) -> StoredDeviceIdentity:
    _validate_enrollment_url(enrollment_url)
    if credential_root.exists():
        raise FileExistsError(f"credential directory already exists: {credential_root}")
    credential_root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{credential_root.name}-",
            dir=credential_root.parent,
        )
    )
    os.chmod(staging, 0o700)
    try:
        private_key = ec.generate_private_key(ec.SECP384R1())
        _write_file(
            staging / "private-key.pem",
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ),
            mode=0o600,
        )
        certificate_request = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([]))
            .sign(private_key, hashes.SHA384())
            .public_bytes(serialization.Encoding.PEM)
            .decode("ascii")
        )
        payload = {
            "protocol_version": DEVICE_PROTOCOL_VERSION,
            "pairing_token": pairing_token.get_secret_value(),
            "display_name": display_name,
            "certificate_signing_request_pem": certificate_request,
            "capabilities": capabilities.model_dump(mode="json"),
        }
        response_bytes = (transport or _post_json)(enrollment_url, payload)
        response = DeviceEnrollmentResponse.model_validate_json(response_bytes)
        certificate = x509.load_pem_x509_certificate(
            response.credential.certificate_pem.encode("ascii")
        )
        _validate_issued_certificate(
            certificate=certificate,
            private_key=private_key,
            device_id=response.credential.device_id,
            trust_chain_pem=response.credential.trust_chain_pem,
        )
        identity = StoredDeviceIdentity(
            device_id=response.credential.device_id,
            enrollment_url=enrollment_url,
            capabilities=capabilities,
        )
        _write_file(
            staging / "certificate.pem",
            response.credential.certificate_pem.encode("ascii"),
            mode=0o644,
        )
        _write_file(
            staging / "ca-certificates.pem",
            "".join(response.credential.trust_chain_pem).encode("ascii"),
            mode=0o644,
        )
        _write_file(
            staging / "device.json",
            identity.model_dump_json(indent=2).encode("utf-8") + b"\n",
            mode=0o600,
        )
        staging.rename(credential_root)
        return identity
    except BaseException:
        shutil.rmtree(staging)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enroll this host as a Cueverge protocol-v2 device.",
    )
    parser.add_argument("--enrollment-url", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--capabilities", required=True, type=Path)
    parser.add_argument(
        "--credential-root",
        type=Path,
        default=Path.home() / ".config" / "cueverge" / "device-v2",
    )
    args = parser.parse_args()
    capabilities = DeviceCapabilityDocument.model_validate_json(
        args.capabilities.read_text(encoding="utf-8")
    )
    pairing_token = SecretStr(getpass.getpass("Single-use pairing token: "))
    if not pairing_token.get_secret_value():
        parser.error("pairing token must not be empty")
    try:
        identity = enroll_device(
            enrollment_url=args.enrollment_url,
            pairing_token=pairing_token,
            display_name=args.display_name,
            capabilities=capabilities,
            credential_root=args.credential_root,
        )
    except (OSError, ValueError, urllib.error.URLError) as error:
        print(f"Enrollment failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(f"Enrolled device {identity.device_id}")
    print(f"Credentials written to {args.credential_root}")


def _validate_enrollment_url(enrollment_url: str) -> None:
    parsed = urlparse(enrollment_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("enrollment URL must be an HTTPS URL without userinfo or a fragment")


def _post_json(url: str, payload: dict[str, Any]) -> bytes:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = bytes(response.read(1_048_577))
    if len(body) > 1_048_576:
        raise ValueError("enrollment response exceeds one MiB")
    return body


def _validate_issued_certificate(
    *,
    certificate: x509.Certificate,
    private_key: ec.EllipticCurvePrivateKey,
    device_id: UUID,
    trust_chain_pem: tuple[str, ...],
) -> None:
    issued_public_key = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    expected_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if issued_public_key != expected_public_key:
        raise ValueError("issued certificate does not match the generated private key")
    try:
        uri_names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.UniformResourceIdentifier)
    except x509.ExtensionNotFound as error:
        raise ValueError("issued certificate has no subject alternative name") from error
    if uri_names != [f"urn:cueverge:device:{device_id}"]:
        raise ValueError("issued certificate has an unexpected device identity")
    authorities = x509.load_pem_x509_certificates("".join(trust_chain_pem).encode("ascii"))
    if not authorities:
        raise ValueError("enrollment response has no certificate authority")
    try:
        certificate.verify_directly_issued_by(authorities[0])
    except ValueError as error:
        raise ValueError("issued certificate does not match the returned trust chain") from error


def _write_file(path: Path, content: bytes, *, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


if __name__ == "__main__":
    main()
