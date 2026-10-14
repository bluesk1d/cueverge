import asyncio
import json
import socket
import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import SecretStr
from websockets.asyncio.client import connect

from cueverge.gui.contracts import Role
from cueverge.gui.device_app import create_device_app
from cueverge.gui.device_ca import DeviceCertificateAuthority
from cueverge.gui.device_session import DeviceSessionRegistry
from cueverge.gui.device_uvicorn import PeerCertificateWebSocketProtocol
from cueverge.gui.repository import GuiRepository
from cueverge.protocol.device_v2 import (
    DeviceCapabilityDocument,
    DeviceCertificate,
    DeviceKind,
    MediaPlaybackCapability,
    ProtocolRange,
)

DEVICE_ID = UUID("9c004799-7704-48f1-b5f4-f083628c7faa")
TOKEN = "pairing-token-that-is-never-stored-in-plaintext"


def test_listener_requires_and_exposes_verified_device_certificate(
    tmp_path: Path,
) -> None:
    asyncio.run(_listener_scenario(tmp_path))


async def _listener_scenario(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    authority = DeviceCertificateAuthority(tmp_path / "device-ca", SecretStr("ca-password"))
    authority.provision(now=now)
    server_certificate, server_key = authority.provision_server_certificate(
        ("127.0.0.1",),
        now=now,
    )
    client_key = ec.generate_private_key(ec.SECP256R1())
    certificate_request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .sign(client_key, hashes.SHA256())
    )
    credential = authority.issue_device_certificate(
        device_id=DEVICE_ID,
        certificate_signing_request_pem=certificate_request.public_bytes(
            serialization.Encoding.PEM
        ).decode("ascii"),
        now=now,
    )
    capabilities = _capabilities()
    repository = _repository(tmp_path, credential, capabilities, now)
    app = create_device_app(repository, DeviceSessionRegistry())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    host, port = cast(tuple[str, int], listener.getsockname())
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            ws=PeerCertificateWebSocketProtocol,
            lifespan="off",
            log_level="critical",
            server_header=False,
            proxy_headers=False,
            ssl_certfile=server_certificate,
            ssl_keyfile=server_key,
            ssl_keyfile_password="ca-password",
            ssl_cert_reqs=ssl.CERT_REQUIRED,
            ssl_ca_certs=authority.certificate_path,
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        client_certificate = tmp_path / "client-certificate.pem"
        client_private_key = tmp_path / "client-key.pem"
        client_certificate.write_text(credential.certificate_pem, encoding="ascii")
        client_private_key.write_bytes(
            client_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH,
            cafile=str(authority.certificate_path),
        )
        context.load_cert_chain(client_certificate, client_private_key)
        session_id = uuid4()
        async with connect(
            f"wss://{host}:{port}/api/device/v2/agent/ws",
            ssl=context,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "device.hello",
                        "protocol_version": 2,
                        "device_id": str(DEVICE_ID),
                        "device_session_id": str(session_id),
                        "capabilities": capabilities.model_dump(mode="json"),
                    }
                )
            )
            welcome = json.loads(await websocket.recv())
            assert welcome["type"] == "device.welcome"
            assert welcome["device_id"] == str(DEVICE_ID)
            await websocket.send(
                json.dumps(
                    {
                        "type": "device.heartbeat",
                        "protocol_version": 2,
                        "device_id": str(DEVICE_ID),
                        "device_session_id": str(session_id),
                        "controller_epoch": welcome["controller_epoch"],
                        "sequence": 0,
                        "device_monotonic_ms": 100,
                    }
                )
            )
            acknowledgment = json.loads(await websocket.recv())
            assert acknowledgment["accepted"] is True
            presence = repository.list_device_presence()
            assert len(presence) == 1
            assert presence[0].disconnected_at_ms is None
        for _ in range(100):
            if repository.list_device_presence()[0].disconnected_at_ms is not None:
                break
            await asyncio.sleep(0.01)
        assert repository.list_device_presence()[0].disconnected_at_ms is not None
    finally:
        server.should_exit = True
        await server_task
        listener.close()


def _repository(
    tmp_path: Path,
    credential: DeviceCertificate,
    capabilities: DeviceCapabilityDocument,
    now: datetime,
) -> GuiRepository:
    repository = GuiRepository(tmp_path / "gui.sqlite3")
    repository.migrate()
    repository.create_pairing_token(
        token=TOKEN,
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        created_by="admin",
        created_at=now.isoformat(),
        expires_at=int(now.timestamp()) + 300,
        role=Role.ADMINISTRATOR,
    )
    repository.consume_pairing_token_and_create_device(
        token=TOKEN,
        now=int(now.timestamp()),
        occurred_at=now.isoformat(),
        device_id=str(DEVICE_ID),
        display_name="CuevergeClient01",
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        capabilities=capabilities,
        certificate_serial=credential.certificate_serial,
        certificate_fingerprint_sha256=credential.fingerprint_sha256,
        certificate_issued_at=credential.issued_at.isoformat(),
        certificate_expires_at=credential.expires_at.isoformat(),
    )
    return repository


def _capabilities() -> DeviceCapabilityDocument:
    return DeviceCapabilityDocument(
        protocol=ProtocolRange(minimum=2, maximum=2),
        device_kind=DeviceKind.DIRECT_MEDIA_AGENT,
        software_version="0.1.0",
        capabilities=(MediaPlaybackCapability(codecs=("h264",), containers=("mp4",)),),
    )
