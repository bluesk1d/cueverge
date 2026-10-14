import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import JsonValue

from cueverge.gui.contracts import DraftProject, Role
from cueverge.protocol.device_v2 import (
    DeviceCapabilityDocument,
    DeviceKind,
    EnrollmentState,
)


@dataclass(frozen=True)
class StoredProject:
    id: str
    name: str
    head_revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredRevision:
    project: StoredProject
    revision: int
    parent_revision: int | None
    actor: str
    transaction_kind: str
    created_at: str
    draft: DraftProject


@dataclass(frozen=True)
class StoredSession:
    username: str
    role: Role
    csrf_hash: str
    expires_at: int


@dataclass(frozen=True)
class StoredAuditEvent:
    id: int
    actor: str
    action: str
    target: str
    result: str
    occurred_at: str
    authorization_context: dict[str, JsonValue]


@dataclass(frozen=True)
class StoredPairingToken:
    id: str
    device_kind: DeviceKind
    created_by: str
    created_at: str
    expires_at: int
    consumed_at: str | None
    revoked_at: str | None


@dataclass(frozen=True)
class StoredDevice:
    id: str
    display_name: str
    device_kind: DeviceKind
    enrollment_state: EnrollmentState
    capabilities: DeviceCapabilityDocument
    active_certificate_serial: str
    certificate_fingerprint_sha256: str
    certificate_issued_at: str
    certificate_expires_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredResourceAssignment:
    resource: str
    device_id: str
    capability_kind: str
    device_output: str | None
    revision: int
    actor: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredDevicePresence:
    device_id: str
    session_id: str
    connected_at_ms: int
    last_seen_at_ms: int
    disconnected_at_ms: int | None


class ProjectNotFoundError(LookupError):
    pass


class DeviceNotFoundError(LookupError):
    pass


class PairingTokenNotUsableError(RuntimeError):
    pass


class DeviceStateTransitionError(RuntimeError):
    pass


class DeviceCertificateAuthorizationError(RuntimeError):
    pass


class ResourceAssignmentConflictError(RuntimeError):
    def __init__(self, current_revision: int | None) -> None:
        super().__init__("resource assignment revision conflict")
        self.current_revision = current_revision


class ResourceAssignmentInUseError(RuntimeError):
    pass


class RevisionConflictError(RuntimeError):
    def __init__(
        self,
        *,
        requested_revision: int,
        current_revision: int,
        conflicting_paths: tuple[str, ...],
    ) -> None:
        super().__init__("draft revision conflict")
        self.requested_revision = requested_revision
        self.current_revision = current_revision
        self.conflicting_paths = conflicting_paths


class GuiRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def migrate(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    head_revision INTEGER NOT NULL CHECK (head_revision >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    parent_revision INTEGER,
                    content_json TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    transaction_kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, revision)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    csrf_hash TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    result TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    authorization_context_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_pairing_tokens (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    device_kind TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at TEXT,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    device_kind TEXT NOT NULL,
                    enrollment_state TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    active_certificate_serial TEXT NOT NULL UNIQUE,
                    certificate_fingerprint_sha256 TEXT NOT NULL UNIQUE,
                    certificate_issued_at TEXT NOT NULL,
                    certificate_expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_certificates (
                    serial TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                    fingerprint_sha256 TEXT NOT NULL UNIQUE,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    superseded_at TEXT,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS resource_assignments (
                    resource TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
                    capability_kind TEXT NOT NULL,
                    device_output TEXT,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_presence (
                    device_id TEXT PRIMARY KEY
                        REFERENCES devices(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL,
                    connected_at_ms INTEGER NOT NULL CHECK (connected_at_ms >= 0),
                    last_seen_at_ms INTEGER NOT NULL CHECK (last_seen_at_ms >= 0),
                    disconnected_at_ms INTEGER
                        CHECK (disconnected_at_ms IS NULL OR disconnected_at_ms >= 0)
                );
                CREATE TABLE IF NOT EXISTS device_connection_policies (
                    device_id TEXT PRIMARY KEY
                        REFERENCES devices(id) ON DELETE CASCADE,
                    policy TEXT NOT NULL
                        CHECK (policy IN ('continuous', 'on_demand')),
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (4, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (5, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                """
            )

    def create_session(
        self,
        *,
        token: str,
        username: str,
        role: Role,
        csrf_token: str,
        expires_at: int,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    token_hash, username, role, csrf_hash, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _digest(token),
                    username,
                    role.value,
                    _digest(csrf_token),
                    expires_at,
                    created_at,
                ),
            )

    def get_session(self, token: str, *, now: int) -> StoredSession | None:
        token_hash = _digest(token)
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            row = connection.execute(
                """
                SELECT username, role, csrf_hash, expires_at
                FROM sessions
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        return StoredSession(
            username=str(row["username"]),
            role=Role(str(row["role"])),
            csrf_hash=str(row["csrf_hash"]),
            expires_at=int(row["expires_at"]),
        )

    def delete_session(self, token: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))

    def create_pairing_token(
        self,
        *,
        token: str,
        device_kind: DeviceKind,
        created_by: str,
        created_at: str,
        expires_at: int,
        role: Role,
    ) -> StoredPairingToken:
        token_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO device_pairing_tokens(
                        id, token_hash, device_kind, created_by, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_id,
                        _digest(token),
                        device_kind.value,
                        created_by,
                        created_at,
                        expires_at,
                    ),
                )
                self._insert_audit(
                    connection,
                    actor=created_by,
                    action="device.pairing_token.create",
                    target=token_id,
                    result="success",
                    occurred_at=created_at,
                    authorization_context={
                        "role": role.value,
                        "device_kind": device_kind.value,
                        "expires_at": expires_at,
                    },
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return StoredPairingToken(
            id=token_id,
            device_kind=device_kind,
            created_by=created_by,
            created_at=created_at,
            expires_at=expires_at,
            consumed_at=None,
            revoked_at=None,
        )

    def consume_pairing_token_and_create_device(
        self,
        *,
        token: str,
        now: int,
        occurred_at: str,
        device_id: str,
        display_name: str,
        device_kind: DeviceKind,
        capabilities: DeviceCapabilityDocument,
        certificate_serial: str,
        certificate_fingerprint_sha256: str,
        certificate_issued_at: str,
        certificate_expires_at: str,
    ) -> StoredDevice:
        if capabilities.device_kind is not device_kind:
            raise ValueError("capability document device kind does not match enrollment request")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                token_row = connection.execute(
                    """
                    SELECT id, device_kind
                    FROM device_pairing_tokens
                    WHERE token_hash = ?
                      AND consumed_at IS NULL
                      AND revoked_at IS NULL
                      AND expires_at > ?
                    """,
                    (_digest(token), now),
                ).fetchone()
                if token_row is None or str(token_row["device_kind"]) != device_kind.value:
                    raise PairingTokenNotUsableError("pairing token is invalid, expired, or used")
                connection.execute(
                    """
                    UPDATE device_pairing_tokens
                    SET consumed_at = ?
                    WHERE id = ? AND consumed_at IS NULL AND revoked_at IS NULL
                    """,
                    (occurred_at, str(token_row["id"])),
                )
                capability_json = capabilities.model_dump_json()
                connection.execute(
                    """
                    INSERT INTO devices(
                        id, display_name, device_kind, enrollment_state,
                        capabilities_json, active_certificate_serial,
                        certificate_fingerprint_sha256, certificate_issued_at,
                        certificate_expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'enrolled', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        display_name,
                        device_kind.value,
                        capability_json,
                        certificate_serial,
                        certificate_fingerprint_sha256,
                        certificate_issued_at,
                        certificate_expires_at,
                        occurred_at,
                        occurred_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO device_certificates(
                        serial, device_id, fingerprint_sha256, issued_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        certificate_serial,
                        device_id,
                        certificate_fingerprint_sha256,
                        certificate_issued_at,
                        certificate_expires_at,
                    ),
                )
                self._insert_audit(
                    connection,
                    actor=f"device:{device_id}",
                    action="device.enroll",
                    target=device_id,
                    result="success",
                    occurred_at=occurred_at,
                    authorization_context={
                        "device_kind": device_kind.value,
                        "pairing_token_id": str(token_row["id"]),
                        "certificate_serial": certificate_serial,
                    },
                )
                connection.commit()
            except PairingTokenNotUsableError:
                connection.rollback()
                raise
            except BaseException:
                connection.rollback()
                raise
        return StoredDevice(
            id=device_id,
            display_name=display_name,
            device_kind=device_kind,
            enrollment_state=EnrollmentState.ENROLLED,
            capabilities=capabilities,
            active_certificate_serial=certificate_serial,
            certificate_fingerprint_sha256=certificate_fingerprint_sha256,
            certificate_issued_at=certificate_issued_at,
            certificate_expires_at=certificate_expires_at,
            created_at=occurred_at,
            updated_at=occurred_at,
        )

    def pairing_token_is_usable(
        self,
        *,
        token: str,
        now: int,
        device_kind: DeviceKind,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM device_pairing_tokens
                WHERE token_hash = ?
                  AND device_kind = ?
                  AND consumed_at IS NULL
                  AND revoked_at IS NULL
                  AND expires_at > ?
                """,
                (_digest(token), device_kind.value, now),
            ).fetchone()
        return row is not None

    def get_device(self, device_id: str) -> StoredDevice:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, display_name, device_kind, enrollment_state,
                       capabilities_json, active_certificate_serial,
                       certificate_fingerprint_sha256, certificate_issued_at,
                       certificate_expires_at, created_at, updated_at
                FROM devices
                WHERE id = ?
                """,
                (device_id,),
            ).fetchone()
        if row is None:
            raise DeviceNotFoundError(device_id)
        return _stored_device(row)

    def authorize_device_certificate(
        self,
        *,
        device_id: str,
        certificate_serial: str,
        certificate_fingerprint_sha256: str,
    ) -> StoredDevice:
        occurred_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = connection.execute(
                """
                SELECT d.active_certificate_serial, d.enrollment_state,
                       c.issued_at, c.expires_at
                FROM devices AS d
                JOIN device_certificates AS c ON c.device_id = d.id
                WHERE d.id = ?
                  AND c.serial = ?
                  AND c.fingerprint_sha256 = ?
                  AND c.revoked_at IS NULL
                  AND c.superseded_at IS NULL
                """,
                (device_id, certificate_serial, certificate_fingerprint_sha256),
            ).fetchone()
            if (
                candidate is not None
                and EnrollmentState(str(candidate["enrollment_state"])) is EnrollmentState.ENROLLED
                and str(candidate["active_certificate_serial"]) != certificate_serial
            ):
                previous_serial = str(candidate["active_certificate_serial"])
                connection.execute(
                    """
                    UPDATE device_certificates
                    SET superseded_at = ?
                    WHERE device_id = ? AND serial = ?
                    """,
                    (occurred_at, device_id, previous_serial),
                )
                connection.execute(
                    """
                    UPDATE devices
                    SET active_certificate_serial = ?,
                        certificate_fingerprint_sha256 = ?,
                        certificate_issued_at = ?,
                        certificate_expires_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        certificate_serial,
                        certificate_fingerprint_sha256,
                        str(candidate["issued_at"]),
                        str(candidate["expires_at"]),
                        occurred_at,
                        device_id,
                    ),
                )
                self._insert_audit(
                    connection,
                    actor=device_id,
                    action="device.certificate.rotate.activate",
                    target=device_id,
                    result="success",
                    occurred_at=occurred_at,
                    authorization_context={
                        "authentication": "mtls",
                        "previous_serial": previous_serial,
                        "activated_serial": certificate_serial,
                    },
                )
            connection.commit()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT d.id, d.display_name, d.device_kind, d.enrollment_state,
                       d.capabilities_json, d.active_certificate_serial,
                       d.certificate_fingerprint_sha256, d.certificate_issued_at,
                       d.certificate_expires_at, d.created_at, d.updated_at
                FROM devices AS d
                JOIN device_certificates AS c
                  ON c.device_id = d.id
                 AND c.serial = ?
                 AND c.fingerprint_sha256 = ?
                WHERE d.id = ?
                  AND d.enrollment_state = 'enrolled'
                  AND d.active_certificate_serial = c.serial
                  AND c.revoked_at IS NULL
                  AND c.superseded_at IS NULL
                """,
                (
                    certificate_serial,
                    certificate_fingerprint_sha256,
                    device_id,
                ),
            ).fetchone()
        if row is None:
            raise DeviceCertificateAuthorizationError(
                "device certificate is unknown, inactive, or revoked"
            )
        return _stored_device(row)

    def stage_device_certificate_rotation(
        self,
        *,
        device_id: str,
        expected_current_serial: str,
        certificate_serial: str,
        certificate_fingerprint_sha256: str,
        certificate_issued_at: str,
        certificate_expires_at: str,
        occurred_at: str,
    ) -> StoredDevice:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT enrollment_state, active_certificate_serial
                    FROM devices
                    WHERE id = ?
                    """,
                    (device_id,),
                ).fetchone()
                if row is None:
                    raise DeviceNotFoundError(device_id)
                if EnrollmentState(str(row["enrollment_state"])) is not EnrollmentState.ENROLLED:
                    raise DeviceCertificateAuthorizationError(
                        "only enrolled devices may rotate credentials"
                    )
                if str(row["active_certificate_serial"]) != expected_current_serial:
                    raise DeviceCertificateAuthorizationError(
                        "active certificate changed during rotation"
                    )
                connection.execute(
                    """
                    INSERT INTO device_certificates(
                        serial, device_id, fingerprint_sha256,
                        issued_at, expires_at, superseded_at, revoked_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, NULL)
                    """,
                    (
                        certificate_serial,
                        device_id,
                        certificate_fingerprint_sha256,
                        certificate_issued_at,
                        certificate_expires_at,
                    ),
                )
                self._insert_audit(
                    connection,
                    actor=device_id,
                    action="device.certificate.rotate.stage",
                    target=device_id,
                    result="success",
                    occurred_at=occurred_at,
                    authorization_context={
                        "authentication": "mtls",
                        "previous_serial": expected_current_serial,
                        "created_serial": certificate_serial,
                    },
                )
                connection.commit()
            except (
                DeviceNotFoundError,
                DeviceCertificateAuthorizationError,
                sqlite3.IntegrityError,
            ):
                connection.rollback()
                raise
            except BaseException:
                connection.rollback()
                raise
        return self.get_device(device_id)

    def list_devices(self) -> tuple[StoredDevice, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, display_name, device_kind, enrollment_state,
                       capabilities_json, active_certificate_serial,
                       certificate_fingerprint_sha256, certificate_issued_at,
                       certificate_expires_at, created_at, updated_at
                FROM devices
                ORDER BY display_name, id
                """
            ).fetchall()
        return tuple(_stored_device(row) for row in rows)

    def set_resource_assignment(
        self,
        *,
        resource: str,
        device_id: str,
        capability_kind: str,
        device_output: str | None,
        expected_revision: int | None,
        actor: str,
        role: Role,
        occurred_at: str,
        disconnected_before_ms: int | None = None,
    ) -> StoredResourceAssignment:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                device = connection.execute(
                    """
                    SELECT enrollment_state, capabilities_json
                    FROM devices
                    WHERE id = ?
                    """,
                    (device_id,),
                ).fetchone()
                if device is None:
                    raise DeviceNotFoundError(device_id)
                if EnrollmentState(str(device["enrollment_state"])) is not EnrollmentState.ENROLLED:
                    raise DeviceStateTransitionError(
                        "resource assignments require an enrolled device"
                    )
                capabilities = DeviceCapabilityDocument.model_validate_json(
                    str(device["capabilities_json"])
                )
                if capability_kind not in {
                    capability.kind for capability in capabilities.capabilities
                }:
                    raise ValueError("device does not declare the assigned capability")
                current = connection.execute(
                    """
                    SELECT revision, created_at
                    FROM resource_assignments
                    WHERE resource = ?
                    """,
                    (resource,),
                ).fetchone()
                current_revision = int(current["revision"]) if current is not None else None
                if current_revision != expected_revision:
                    raise ResourceAssignmentConflictError(current_revision)
                guarded_device_ids = {device_id}
                if current is not None:
                    current_device = connection.execute(
                        "SELECT device_id FROM resource_assignments WHERE resource = ?",
                        (resource,),
                    ).fetchone()
                    if current_device is not None:
                        guarded_device_ids.add(str(current_device["device_id"]))
                if disconnected_before_ms is not None and _has_connected_presence(
                    connection,
                    guarded_device_ids,
                    disconnected_before_ms,
                ):
                    raise ResourceAssignmentInUseError(
                        "resource assignment devices must be disconnected"
                    )
                next_revision = (current_revision or 0) + 1
                created_at = str(current["created_at"]) if current is not None else occurred_at
                connection.execute(
                    """
                    INSERT INTO resource_assignments(
                        resource, device_id, capability_kind, device_output, revision,
                        actor, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(resource) DO UPDATE SET
                        device_id = excluded.device_id,
                        capability_kind = excluded.capability_kind,
                        device_output = excluded.device_output,
                        revision = excluded.revision,
                        actor = excluded.actor,
                        updated_at = excluded.updated_at
                    """,
                    (
                        resource,
                        device_id,
                        capability_kind,
                        device_output,
                        next_revision,
                        actor,
                        created_at,
                        occurred_at,
                    ),
                )
                self._insert_audit(
                    connection,
                    actor=actor,
                    action="resource.assign",
                    target=resource,
                    result="success",
                    occurred_at=occurred_at,
                    authorization_context={
                        "role": role.value,
                        "device_id": device_id,
                        "capability_kind": capability_kind,
                        "device_output": device_output,
                        "previous_revision": current_revision,
                        "created_revision": next_revision,
                    },
                )
                connection.commit()
            except (
                DeviceNotFoundError,
                DeviceStateTransitionError,
                ResourceAssignmentConflictError,
                ResourceAssignmentInUseError,
                ValueError,
            ):
                connection.rollback()
                raise
            except BaseException:
                connection.rollback()
                raise
        return StoredResourceAssignment(
            resource=resource,
            device_id=device_id,
            capability_kind=capability_kind,
            device_output=device_output,
            revision=next_revision,
            actor=actor,
            created_at=created_at,
            updated_at=occurred_at,
        )

    def list_resource_assignments(self) -> tuple[StoredResourceAssignment, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT resource, device_id, capability_kind, device_output, revision,
                       actor, created_at, updated_at
                FROM resource_assignments
                ORDER BY resource
                """
            ).fetchall()
        return tuple(
            StoredResourceAssignment(
                resource=str(row["resource"]),
                device_id=str(row["device_id"]),
                capability_kind=str(row["capability_kind"]),
                device_output=(
                    str(row["device_output"]) if row["device_output"] is not None else None
                ),
                revision=int(row["revision"]),
                actor=str(row["actor"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        )

    def mark_device_connected(
        self,
        *,
        device_id: str,
        session_id: str,
        observed_at_ms: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO device_presence(
                    device_id, session_id, connected_at_ms,
                    last_seen_at_ms, disconnected_at_ms
                ) VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(device_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    connected_at_ms = excluded.connected_at_ms,
                    last_seen_at_ms = excluded.last_seen_at_ms,
                    disconnected_at_ms = NULL
                """,
                (device_id, session_id, observed_at_ms, observed_at_ms),
            )

    def mark_device_seen(
        self,
        *,
        device_id: str,
        session_id: str,
        observed_at_ms: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE device_presence
                SET last_seen_at_ms = ?
                WHERE device_id = ?
                  AND session_id = ?
                  AND disconnected_at_ms IS NULL
                """,
                (observed_at_ms, device_id, session_id),
            )

    def mark_device_disconnected(
        self,
        *,
        device_id: str,
        session_id: str,
        observed_at_ms: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE device_presence
                SET disconnected_at_ms = ?
                WHERE device_id = ?
                  AND session_id = ?
                  AND disconnected_at_ms IS NULL
                """,
                (observed_at_ms, device_id, session_id),
            )

    def list_device_presence(self) -> tuple[StoredDevicePresence, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT device_id, session_id, connected_at_ms,
                       last_seen_at_ms, disconnected_at_ms
                FROM device_presence
                ORDER BY device_id
                """
            ).fetchall()
        return tuple(
            StoredDevicePresence(
                device_id=str(row["device_id"]),
                session_id=str(row["session_id"]),
                connected_at_ms=int(row["connected_at_ms"]),
                last_seen_at_ms=int(row["last_seen_at_ms"]),
                disconnected_at_ms=(
                    int(row["disconnected_at_ms"])
                    if row["disconnected_at_ms"] is not None
                    else None
                ),
            )
            for row in rows
        )

    def set_device_connection_policy(
        self,
        *,
        device_id: str,
        policy: str,
        actor: str,
        occurred_at: str,
    ) -> None:
        if policy not in {"continuous", "on_demand"}:
            raise ValueError("invalid device connection policy")
        with self._connect() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM devices WHERE id = ?",
                    (device_id,),
                ).fetchone()
                is None
            ):
                raise DeviceNotFoundError(device_id)
            connection.execute(
                """
                INSERT INTO device_connection_policies(device_id, policy, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    policy = excluded.policy,
                    updated_at = excluded.updated_at
                """,
                (device_id, policy, occurred_at),
            )
            self._insert_audit(
                connection,
                actor=actor,
                action="device.connection_policy.update",
                target=device_id,
                result="success",
                occurred_at=occurred_at,
                authorization_context={"policy": policy},
            )

    def list_device_connection_policies(
        self,
    ) -> dict[str, Literal["continuous", "on_demand"]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT device_id, policy FROM device_connection_policies"
            ).fetchall()
        policies: dict[str, Literal["continuous", "on_demand"]] = {}
        for row in rows:
            policy = str(row["policy"])
            if policy == "continuous":
                policies[str(row["device_id"])] = "continuous"
            elif policy == "on_demand":
                policies[str(row["device_id"])] = "on_demand"
            else:
                raise RuntimeError(f"invalid stored device connection policy: {policy}")
        return policies

    def remove_resource_assignment(
        self,
        *,
        resource: str,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
        disconnected_before_ms: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = connection.execute(
                    """
                    SELECT revision, device_id, capability_kind, device_output
                    FROM resource_assignments
                    WHERE resource = ?
                    """,
                    (resource,),
                ).fetchone()
                current_revision = int(current["revision"]) if current is not None else None
                if current_revision != expected_revision:
                    raise ResourceAssignmentConflictError(current_revision)
                if (
                    disconnected_before_ms is not None
                    and current is not None
                    and _has_connected_presence(
                        connection,
                        {str(current["device_id"])},
                        disconnected_before_ms,
                    )
                ):
                    raise ResourceAssignmentInUseError(
                        "resource assignment device must be disconnected"
                    )
                connection.execute(
                    "DELETE FROM resource_assignments WHERE resource = ?",
                    (resource,),
                )
                self._insert_audit(
                    connection,
                    actor=actor,
                    action="resource.unassign",
                    target=resource,
                    result="success",
                    occurred_at=occurred_at,
                    authorization_context={
                        "role": role.value,
                        "device_id": str(current["device_id"]),
                        "capability_kind": str(current["capability_kind"]),
                        "device_output": (
                            str(current["device_output"])
                            if current["device_output"] is not None
                            else None
                        ),
                        "removed_revision": current_revision,
                    },
                )
                connection.commit()
            except (ResourceAssignmentConflictError, ResourceAssignmentInUseError):
                connection.rollback()
                raise
            except BaseException:
                connection.rollback()
                raise

    def set_device_enrollment_state(
        self,
        *,
        device_id: str,
        state: EnrollmentState,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredDevice:
        allowed = {
            EnrollmentState.ENROLLED: {
                EnrollmentState.SUSPENDED,
                EnrollmentState.REVOKED,
            },
            EnrollmentState.SUSPENDED: {
                EnrollmentState.ENROLLED,
                EnrollmentState.REVOKED,
            },
            EnrollmentState.REVOKED: set(),
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT enrollment_state FROM devices WHERE id = ?",
                    (device_id,),
                ).fetchone()
                if row is None:
                    raise DeviceNotFoundError(device_id)
                current = EnrollmentState(str(row["enrollment_state"]))
                if state not in allowed.get(current, set()):
                    raise DeviceStateTransitionError(f"cannot transition {current} to {state}")
                connection.execute(
                    """
                    UPDATE devices
                    SET enrollment_state = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (state.value, occurred_at, device_id),
                )
                if state is EnrollmentState.REVOKED:
                    connection.execute(
                        """
                        UPDATE device_certificates
                        SET revoked_at = ?
                        WHERE device_id = ? AND revoked_at IS NULL
                        """,
                        (occurred_at, device_id),
                    )
                self._insert_audit(
                    connection,
                    actor=actor,
                    action=f"device.{state.value}",
                    target=device_id,
                    result="success",
                    occurred_at=occurred_at,
                    authorization_context={
                        "role": role.value,
                        "previous_state": current.value,
                    },
                )
                connection.commit()
            except (DeviceNotFoundError, DeviceStateTransitionError):
                connection.rollback()
                raise
            except BaseException:
                connection.rollback()
                raise
        return self.get_device(device_id)

    def create_project(
        self,
        *,
        name: str,
        draft: DraftProject,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredRevision:
        project_id = str(uuid4())
        content_json = _draft_json(draft)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO projects(id, name, head_revision, created_at, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (project_id, name, occurred_at, occurred_at),
                )
                connection.execute(
                    """
                    INSERT INTO revisions(
                        project_id, revision, parent_revision, content_json,
                        actor, transaction_kind, created_at
                    ) VALUES (?, 1, NULL, ?, ?, 'create', ?)
                    """,
                    (project_id, content_json, actor, occurred_at),
                )
                self._insert_audit(
                    connection,
                    actor=actor,
                    action="project.create",
                    target=project_id,
                    result="success",
                    occurred_at=occurred_at,
                    authorization_context={"role": role.value},
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        project = StoredProject(project_id, name, 1, occurred_at, occurred_at)
        return StoredRevision(project, 1, None, actor, "create", occurred_at, draft)

    def list_projects(self) -> tuple[StoredProject, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name, head_revision, created_at, updated_at
                FROM projects
                ORDER BY updated_at DESC, id
                """
            ).fetchall()
        return tuple(_stored_project(row) for row in rows)

    def get_revision(self, project_id: str, revision: int | None = None) -> StoredRevision:
        with self._connect() as connection:
            project_row = connection.execute(
                """
                SELECT id, name, head_revision, created_at, updated_at
                FROM projects WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
            if project_row is None:
                raise ProjectNotFoundError(project_id)
            project = _stored_project(project_row)
            selected_revision = revision or project.head_revision
            revision_row = connection.execute(
                """
                SELECT revision, parent_revision, content_json, actor,
                       transaction_kind, created_at
                FROM revisions
                WHERE project_id = ? AND revision = ?
                """,
                (project_id, selected_revision),
            ).fetchone()
        if revision_row is None:
            raise ProjectNotFoundError(f"{project_id}@{selected_revision}")
        return _stored_revision(project, revision_row)

    def list_revisions(self, project_id: str) -> tuple[StoredRevision, ...]:
        project = self.get_revision(project_id).project
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT revision, parent_revision, content_json, actor,
                       transaction_kind, created_at
                FROM revisions
                WHERE project_id = ?
                ORDER BY revision DESC
                """,
                (project_id,),
            ).fetchall()
        return tuple(_stored_revision(project, row) for row in rows)

    def update_draft(
        self,
        *,
        project_id: str,
        expected_revision: int,
        draft: DraftProject,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredRevision:
        proposed_json = _draft_json(draft)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                project_row = connection.execute(
                    """
                    SELECT id, name, head_revision, created_at, updated_at
                    FROM projects WHERE id = ?
                    """,
                    (project_id,),
                ).fetchone()
                if project_row is None:
                    raise ProjectNotFoundError(project_id)
                project = _stored_project(project_row)
                current_row = connection.execute(
                    """
                    SELECT content_json FROM revisions
                    WHERE project_id = ? AND revision = ?
                    """,
                    (project_id, project.head_revision),
                ).fetchone()
                if current_row is None:
                    raise RuntimeError("project head revision is missing")
                current_json = str(current_row["content_json"])
                if expected_revision != project.head_revision:
                    conflicting_paths = _changed_top_level_paths(current_json, proposed_json)
                    self._insert_audit(
                        connection,
                        actor=actor,
                        action="draft.update",
                        target=project_id,
                        result="revision_conflict",
                        occurred_at=occurred_at,
                        authorization_context={
                            "role": role.value,
                            "requested_revision": expected_revision,
                            "current_revision": project.head_revision,
                        },
                    )
                    connection.commit()
                    raise RevisionConflictError(
                        requested_revision=expected_revision,
                        current_revision=project.head_revision,
                        conflicting_paths=conflicting_paths,
                    )
                next_revision = project.head_revision + 1
                connection.execute(
                    """
                    INSERT INTO revisions(
                        project_id, revision, parent_revision, content_json,
                        actor, transaction_kind, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'update', ?)
                    """,
                    (
                        project_id,
                        next_revision,
                        project.head_revision,
                        proposed_json,
                        actor,
                        occurred_at,
                    ),
                )
                connection.execute(
                    """
                    UPDATE projects
                    SET head_revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (next_revision, occurred_at, project_id),
                )
                self._insert_audit(
                    connection,
                    actor=actor,
                    action="draft.update",
                    target=project_id,
                    result="success",
                    occurred_at=occurred_at,
                    authorization_context={
                        "role": role.value,
                        "requested_revision": expected_revision,
                        "created_revision": next_revision,
                    },
                )
                connection.commit()
            except RevisionConflictError:
                raise
            except BaseException:
                connection.rollback()
                raise
        updated_project = StoredProject(
            project.id,
            project.name,
            next_revision,
            project.created_at,
            occurred_at,
        )
        return StoredRevision(
            updated_project,
            next_revision,
            expected_revision,
            actor,
            "update",
            occurred_at,
            draft,
        )

    def append_audit(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        result: str,
        occurred_at: str,
        authorization_context: dict[str, JsonValue],
    ) -> None:
        with self._connect() as connection:
            self._insert_audit(
                connection,
                actor=actor,
                action=action,
                target=target,
                result=result,
                occurred_at=occurred_at,
                authorization_context=authorization_context,
            )

    def list_audit_events(self, *, limit: int = 100) -> tuple[StoredAuditEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, actor, action, target, result, occurred_at,
                       authorization_context_json
                FROM audit_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(
            StoredAuditEvent(
                id=int(row["id"]),
                actor=str(row["actor"]),
                action=str(row["action"]),
                target=str(row["target"]),
                result=str(row["result"]),
                occurred_at=str(row["occurred_at"]),
                authorization_context=json.loads(str(row["authorization_context_json"])),
            )
            for row in rows
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, isolation_level=None, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        target: str,
        result: str,
        occurred_at: str,
        authorization_context: dict[str, JsonValue],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                actor, action, target, result, occurred_at, authorization_context_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actor,
                action,
                target,
                result,
                occurred_at,
                json.dumps(authorization_context, sort_keys=True, separators=(",", ":")),
            ),
        )


def _stored_project(row: sqlite3.Row) -> StoredProject:
    return StoredProject(
        id=str(row["id"]),
        name=str(row["name"]),
        head_revision=int(row["head_revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _stored_revision(project: StoredProject, row: sqlite3.Row) -> StoredRevision:
    return StoredRevision(
        project=project,
        revision=int(row["revision"]),
        parent_revision=(
            int(row["parent_revision"]) if row["parent_revision"] is not None else None
        ),
        actor=str(row["actor"]),
        transaction_kind=str(row["transaction_kind"]),
        created_at=str(row["created_at"]),
        draft=DraftProject.model_validate_json(str(row["content_json"])),
    )


def _stored_device(row: sqlite3.Row) -> StoredDevice:
    return StoredDevice(
        id=str(row["id"]),
        display_name=str(row["display_name"]),
        device_kind=DeviceKind(str(row["device_kind"])),
        enrollment_state=EnrollmentState(str(row["enrollment_state"])),
        capabilities=DeviceCapabilityDocument.model_validate_json(str(row["capabilities_json"])),
        active_certificate_serial=str(row["active_certificate_serial"]),
        certificate_fingerprint_sha256=str(row["certificate_fingerprint_sha256"]),
        certificate_issued_at=str(row["certificate_issued_at"]),
        certificate_expires_at=str(row["certificate_expires_at"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _draft_json(draft: DraftProject) -> str:
    return json.dumps(draft.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _changed_top_level_paths(current_json: str, proposed_json: str) -> tuple[str, ...]:
    current = json.loads(current_json)
    proposed = json.loads(proposed_json)
    keys = sorted(set(current).union(proposed))
    return tuple(f"/{key}" for key in keys if current.get(key) != proposed.get(key))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _has_connected_presence(
    connection: sqlite3.Connection,
    device_ids: set[str],
    disconnected_before_ms: int,
) -> bool:
    if not device_ids:
        return False
    placeholders = ",".join("?" for _ in device_ids)
    row = connection.execute(
        f"""
        SELECT 1
        FROM device_presence
        WHERE device_id IN ({placeholders})
          AND disconnected_at_ms IS NULL
          AND last_seen_at_ms >= ?
        LIMIT 1
        """,
        (*sorted(device_ids), disconnected_before_ms),
    ).fetchone()
    return row is not None
