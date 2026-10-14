from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes

PEER_CERTIFICATE_STATE_KEY = "cueverge.tls_peer_certificate_der"
DEVICE_IDENTITY_URI_PREFIX = "urn:cueverge:device:"


@dataclass(frozen=True)
class PeerDeviceCertificate:
    device_id: UUID
    serial: str
    fingerprint_sha256: str


def peer_device_certificate(scope: Mapping[str, Any]) -> PeerDeviceCertificate:
    state = scope.get("state")
    if not isinstance(state, dict):
        raise ValueError("verified TLS peer certificate is unavailable")
    certificate_der = state.get(PEER_CERTIFICATE_STATE_KEY)
    if not isinstance(certificate_der, bytes):
        raise ValueError("verified TLS peer certificate is unavailable")
    try:
        certificate = x509.load_der_x509_certificate(certificate_der)
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.UniformResourceIdentifier)
    except (ValueError, x509.ExtensionNotFound) as error:
        raise ValueError("TLS peer certificate has no valid device identity") from error
    device_names = [
        name.removeprefix(DEVICE_IDENTITY_URI_PREFIX)
        for name in names
        if name.startswith(DEVICE_IDENTITY_URI_PREFIX)
    ]
    if len(device_names) != 1:
        raise ValueError("TLS peer certificate must contain one device identity")
    try:
        device_id = UUID(device_names[0])
    except ValueError as error:
        raise ValueError("TLS peer certificate contains an invalid device identity") from error
    return PeerDeviceCertificate(
        device_id=device_id,
        serial=format(certificate.serial_number, "x"),
        fingerprint_sha256=certificate.fingerprint(hashes.SHA256()).hex(),
    )
