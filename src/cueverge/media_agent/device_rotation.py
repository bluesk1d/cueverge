import argparse
import asyncio
import os
import shutil
import ssl
import tempfile
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import AnyUrl, TypeAdapter
from websockets.asyncio.client import connect

from cueverge.media_agent.device_enrollment import (
    StoredDeviceIdentity,
    _validate_issued_certificate,
    _write_file,
)
from cueverge.protocol.device_v2 import (
    DeviceCredentialRotationRequest,
    DeviceCredentialRotationResponse,
    DeviceHello,
    DeviceWelcome,
)


def rotate_device_credentials(
    *,
    transport_url: str,
    credential_root: Path,
) -> StoredDeviceIdentity:
    parsed_url = TypeAdapter(AnyUrl).validate_python(transport_url)
    if parsed_url.scheme != "wss":
        raise ValueError("device transport URL must use wss")
    identity = StoredDeviceIdentity.model_validate_json(
        (credential_root / "device.json").read_text(encoding="utf-8")
    )
    private_key = ec.generate_private_key(ec.SECP384R1())
    certificate_request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .sign(private_key, hashes.SHA384())
        .public_bytes(serialization.Encoding.PEM)
        .decode("ascii")
    )
    response = asyncio.run(
        _request_rotation(
            transport_url=transport_url,
            credential_root=credential_root,
            identity=identity,
            certificate_signing_request_pem=certificate_request,
        )
    )
    certificate = x509.load_pem_x509_certificate(
        response.credential.certificate_pem.encode("ascii")
    )
    _validate_issued_certificate(
        certificate=certificate,
        private_key=private_key,
        device_id=identity.device_id,
        trust_chain_pem=response.credential.trust_chain_pem,
    )
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{credential_root.name}-rotation-",
            dir=credential_root.parent,
        )
    )
    backup = credential_root.with_name(f".{credential_root.name}-previous")
    try:
        os.chmod(staging, 0o700)
        _write_file(
            staging / "private-key.pem",
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            mode=0o600,
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
        if backup.exists():
            raise FileExistsError(f"rotation backup already exists: {backup}")
        credential_root.rename(backup)
        try:
            staging.rename(credential_root)
        except BaseException:
            backup.rename(credential_root)
            raise
        shutil.rmtree(backup)
        return identity
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


async def _request_rotation(
    *,
    transport_url: str,
    credential_root: Path,
    identity: StoredDeviceIdentity,
    certificate_signing_request_pem: str,
) -> DeviceCredentialRotationResponse:
    context = ssl.create_default_context(cafile=str(credential_root / "ca-certificates.pem"))
    context.load_cert_chain(
        credential_root / "certificate.pem",
        credential_root / "private-key.pem",
    )
    async with connect(transport_url, ssl=context) as websocket:
        hello = DeviceHello(
            device_id=identity.device_id,
            device_session_id=uuid4(),
            capabilities=identity.capabilities,
        )
        await websocket.send(hello.model_dump_json())
        DeviceWelcome.model_validate_json(await websocket.recv())
        await websocket.send(
            DeviceCredentialRotationRequest(
                device_id=identity.device_id,
                certificate_signing_request_pem=certificate_signing_request_pem,
            ).model_dump_json()
        )
        return DeviceCredentialRotationResponse.model_validate_json(await websocket.recv())


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate this Cueverge device credential.")
    parser.add_argument("--transport-url", required=True)
    parser.add_argument(
        "--credential-root",
        type=Path,
        default=Path.home() / ".config" / "cueverge" / "device-v2",
    )
    args = parser.parse_args()
    identity = rotate_device_credentials(
        transport_url=args.transport_url,
        credential_root=args.credential_root,
    )
    print(f"Rotated credentials for device {identity.device_id}")


if __name__ == "__main__":
    main()
