import time
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from cueverge.controller.app import create_app
from cueverge.controller.runtime import ControllerRuntime
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import Role
from cueverge.gui.passwords import hash_password
from cueverge.gui.service import GuiServices

ORIGIN = "https://cueverge.test"
PASSWORD = "correct horse battery staple"
PASSWORD_HASH = hash_password(PASSWORD, salt=b"0123456789abcdef")


def test_administrator_can_create_token_and_enroll_device_once(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = GuiServices.create(_config(tmp_path), runtime)
    client = TestClient(create_app(runtime, gui_services=services))

    assert (
        client.post(
            "/api/gui/v1/devices/pairing-tokens",
            json={"device_kind": "direct_media_agent"},
        ).status_code
        == 401
    )
    _login(client)
    without_csrf = client.post(
        "/api/gui/v1/devices/pairing-tokens",
        json={"device_kind": "direct_media_agent"},
        headers={"Origin": ORIGIN},
    )
    assert without_csrf.status_code == 403

    pairing = client.post(
        "/api/gui/v1/devices/pairing-tokens",
        json={"device_kind": "direct_media_agent"},
        headers=_mutation_headers(client),
    )
    assert pairing.status_code == 201
    pairing_token = pairing.json()["pairing_token"]
    assert len(pairing_token) >= 32

    private_key = ec.generate_private_key(ec.SECP256R1())
    request = _enrollment_request(pairing_token, private_key)
    enrolled = client.post("/api/device/v2/enroll", json=request)

    assert enrolled.status_code == 201
    credential = enrolled.json()["credential"]
    certificate = x509.load_pem_x509_certificate(credential["certificate_pem"].encode("ascii"))
    assert credential["device_id"] in certificate.subject.rfc4514_string()
    stored = services.repository.get_device(credential["device_id"])
    assert stored.display_name == "CuevergeClient01"
    assert stored.active_certificate_serial == credential["certificate_serial"]

    policy = client.put(
        f"/api/gui/v1/devices/{credential['device_id']}/connection-policy",
        json={"policy": "on_demand"},
        headers=_mutation_headers(client),
    )
    assert policy.status_code == 200
    assert policy.json()["policy"] == "on_demand"
    assert services.repository.list_device_connection_policies() == {
        credential["device_id"]: "on_demand"
    }

    reused = client.post("/api/device/v2/enroll", json=request)
    assert reused.status_code == 401
    assert reused.json()["detail"]["code"] == "pairing_failed"

    installation = client.get("/api/gui/v1/installation").json()
    media_resource = next(
        resource for resource in installation["resources"] if resource["kind"] == "media_agent"
    )
    assignment = client.put(
        f"/api/gui/v1/resource-assignments/{media_resource['resource']}",
        json={
            "device_id": credential["device_id"],
            "capability_kind": "media_playback",
            "device_output": "HDMI-A-1",
            "expected_revision": None,
        },
        headers=_mutation_headers(client),
    )
    assert assignment.status_code == 200
    assert assignment.headers["etag"] == '"1"'
    assert assignment.json()["revision"] == 1

    conflict = client.put(
        f"/api/gui/v1/resource-assignments/{media_resource['resource']}",
        json={
            "device_id": credential["device_id"],
            "capability_kind": "media_playback",
            "expected_revision": None,
        },
        headers=_mutation_headers(client),
    )
    assert conflict.status_code == 409
    assert conflict.json()["current_revision"] == 1

    services.repository.mark_device_connected(
        device_id=credential["device_id"],
        session_id="listener-session",
        observed_at_ms=time.time_ns() // 1_000_000,
    )
    topology = client.get("/api/gui/v1/topology").json()
    enrolled_node = next(
        node for node in topology["nodes"] if node["id"] == f"device:{credential['device_id']}"
    )
    assert topology["enrollment_supported"] is True
    assert enrolled_node["connection"] == "connected"
    assert enrolled_node["enrollment"] == "enrolled"
    assert enrolled_node["compatibility"] == "compatible"
    assert enrolled_node["assigned_resources"] == [media_resource["resource"]]

    services.repository.mark_device_disconnected(
        device_id=credential["device_id"],
        session_id="listener-session",
        observed_at_ms=time.time_ns() // 1_000_000,
    )
    removed = client.delete(
        f"/api/gui/v1/resource-assignments/{media_resource['resource']}",
        headers={**_mutation_headers(client), "If-Match": '"1"'},
    )
    assert removed.status_code == 204
    assert client.get("/api/gui/v1/resource-assignments").json() == []

    suspended = client.put(
        f"/api/gui/v1/devices/{credential['device_id']}/enrollment-state",
        json={"state": "suspended"},
        headers=_mutation_headers(client),
    )
    assert suspended.status_code == 200
    assert suspended.json()["state"] == "suspended"
    resumed = client.put(
        f"/api/gui/v1/devices/{credential['device_id']}/enrollment-state",
        json={"state": "enrolled"},
        headers=_mutation_headers(client),
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "enrolled"


def test_enrollment_is_disabled_by_default_and_requires_tls_and_ca_password(
    tmp_path: Path,
) -> None:
    runtime = ControllerRuntime()
    disabled = GuiServices.create(
        _config(
            tmp_path,
            device_enrollment_enabled=False,
            device_enrollment_tls_terminated=False,
            device_ca_key_password=None,
        ),
        runtime,
    )
    client = TestClient(create_app(runtime, gui_services=disabled))
    assert client.post("/api/device/v2/enroll", json={}).status_code == 404
    _login(client)
    pairing = client.post(
        "/api/gui/v1/devices/pairing-tokens",
        json={"device_kind": "direct_media_agent"},
        headers=_mutation_headers(client),
    )
    assert pairing.status_code == 404
    assert pairing.json()["detail"]["code"] == "device_enrollment_disabled"

    for tls_terminated, password in [
        (False, SecretStr("ca-password")),
        (True, None),
    ]:
        try:
            _config(
                tmp_path,
                device_enrollment_enabled=True,
                device_enrollment_tls_terminated=tls_terminated,
                device_ca_key_password=password,
            )
        except ValidationError:
            pass
        else:
            raise AssertionError("unsafe enrollment configuration was accepted")

    try:
        _config(
            tmp_path,
            device_listener_enabled=True,
            device_listener_identities=(),
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("device listener without certificate identities was accepted")


def test_enrollment_attempts_are_rate_limited(tmp_path: Path) -> None:
    runtime = ControllerRuntime()
    services = GuiServices.create(
        _config(tmp_path, device_enrollment_attempts_per_minute=1),
        runtime,
    )
    client = TestClient(create_app(runtime, gui_services=services))
    private_key = ec.generate_private_key(ec.SECP256R1())
    request = _enrollment_request("invalid-pairing-token-value-00000000", private_key)

    assert client.post("/api/device/v2/enroll", json=request).status_code == 401
    limited = client.post("/api/device/v2/enroll", json=request)
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "enrollment_rate_limited"


def _config(tmp_path: Path, **changes: object) -> GuiConfig:
    values: dict[str, object] = {
        "data_root": tmp_path,
        "admin_username": "admin",
        "admin_password_hash": SecretStr(PASSWORD_HASH),
        "admin_role": Role.ADMINISTRATOR,
        "allowed_origins": (ORIGIN,),
        "secure_cookies": False,
        "device_enrollment_enabled": True,
        "device_enrollment_tls_terminated": True,
        "device_ca_key_password": SecretStr("ca-password"),
    }
    values.update(changes)
    return GuiConfig.model_validate(values)


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/gui/v1/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


def _mutation_headers(client: TestClient) -> dict[str, str]:
    csrf_token = client.cookies.get("cueverge_csrf")
    assert csrf_token is not None
    return {"Origin": ORIGIN, "X-CSRF-Token": csrf_token}


def _enrollment_request(
    pairing_token: str,
    private_key: ec.EllipticCurvePrivateKey,
) -> dict[str, object]:
    certificate_request = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([]))
        .sign(private_key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode("ascii")
    )
    return {
        "protocol_version": 2,
        "pairing_token": pairing_token,
        "display_name": "CuevergeClient01",
        "certificate_signing_request_pem": certificate_request,
        "capabilities": {
            "schema_version": 1,
            "protocol": {"minimum": 2, "maximum": 2},
            "device_kind": "direct_media_agent",
            "software_version": "0.1.0",
            "capabilities": [
                {
                    "kind": "media_playback",
                    "codecs": ["h264"],
                    "containers": ["mp4"],
                    "max_outputs": 2,
                    "supports_pause": True,
                    "supports_seek": True,
                }
            ],
        },
    }
