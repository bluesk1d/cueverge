import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import PairingTokenResponse, Role
from cueverge.gui.device_ca import (
    DeviceCertificateAuthority,
    DeviceCertificateAuthorityError,
)
from cueverge.gui.repository import GuiRepository, PairingTokenNotUsableError
from cueverge.protocol.device_v2 import (
    DeviceEnrollmentRequest,
    DeviceEnrollmentResponse,
    DeviceKind,
)


class EnrollmentRateLimitError(RuntimeError):
    pass


@dataclass
class DeviceEnrollmentService:
    config: GuiConfig
    repository: GuiRepository
    authority: DeviceCertificateAuthority

    def __post_init__(self) -> None:
        self._attempts: dict[str, list[float]] = {}
        self._attempt_lock = threading.Lock()

    def create_pairing_token(
        self,
        *,
        device_kind: DeviceKind,
        actor: str,
        role: Role,
        discovered_endpoint_id: str | None = None,
    ) -> PairingTokenResponse:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self.config.device_pairing_ttl_seconds)
        stored = self.repository.create_pairing_token(
            token=token,
            device_kind=device_kind,
            created_by=actor,
            created_at=now.isoformat(),
            expires_at=int(expires_at.timestamp()),
            role=role,
            discovered_endpoint_id=discovered_endpoint_id,
        )
        transport_url = self.config.device_listener_public_url
        if transport_url is None:
            raise RuntimeError("validated device listener public URL is missing")
        return PairingTokenResponse(
            id=stored.id,
            pairing_token=token,
            device_kind=device_kind,
            expires_at=expires_at.isoformat(),
            device_transport_url=str(transport_url),
            discovered_endpoint_id=stored.discovered_endpoint_id,
        )

    def enroll(
        self,
        request: DeviceEnrollmentRequest,
        *,
        remote_key: str,
    ) -> DeviceEnrollmentResponse:
        self._check_rate_limit(remote_key)
        token = request.pairing_token.get_secret_value()
        now = datetime.now(UTC)
        now_epoch = int(now.timestamp())
        device_kind = request.capabilities.device_kind
        if not self.repository.pairing_token_is_usable(
            token=token,
            now=now_epoch,
            device_kind=device_kind,
        ):
            self._audit_denied(remote_key, "pairing_token_rejected")
            raise PairingTokenNotUsableError("pairing token is invalid, expired, or used")

        device_id = uuid4()
        try:
            credential = self.authority.issue_device_certificate(
                device_id=device_id,
                certificate_signing_request_pem=request.certificate_signing_request_pem,
                now=now,
            )
        except DeviceCertificateAuthorityError:
            self._audit_denied(remote_key, "certificate_request_rejected")
            raise
        self.repository.consume_pairing_token_and_create_device(
            token=token,
            now=now_epoch,
            occurred_at=now.isoformat(),
            device_id=str(device_id),
            display_name=request.display_name,
            device_kind=device_kind,
            capabilities=request.capabilities,
            certificate_serial=credential.certificate_serial,
            certificate_fingerprint_sha256=credential.fingerprint_sha256,
            certificate_issued_at=credential.issued_at.isoformat(),
            certificate_expires_at=credential.expires_at.isoformat(),
        )
        return DeviceEnrollmentResponse(credential=credential)

    def _check_rate_limit(self, remote_key: str) -> None:
        now = time.monotonic()
        with self._attempt_lock:
            recent = [
                attempted_at
                for attempted_at in self._attempts.get(remote_key, [])
                if now - attempted_at < 60
            ]
            if len(recent) >= self.config.device_enrollment_attempts_per_minute:
                raise EnrollmentRateLimitError("too many enrollment attempts")
            recent.append(now)
            self._attempts[remote_key] = recent

    def _audit_denied(self, remote_key: str, reason: str) -> None:
        self.repository.append_audit(
            actor="unauthenticated_device",
            action="device.enroll",
            target="device_enrollment",
            result="denied",
            occurred_at=datetime.now(UTC).isoformat(),
            authorization_context={"remote_key": remote_key, "reason": reason},
        )
