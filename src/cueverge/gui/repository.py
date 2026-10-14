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
class StoredInstallation:
    id: str
    name: str
    revision: int
    runtime_active: bool
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
    discovered_endpoint_id: str | None


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
    latency_ms: float | None
    clock_offset_ms: float | None


@dataclass(frozen=True)
class StoredDeviceArtifactStatus:
    device_id: str
    observed_at_ms: int
    total_bytes: int
    free_bytes: int
    artifact_count: int
    artifact_bytes: int
    pinned_count: int
    supports_pinning: bool
    supports_resumable_transfer: bool
    transfers: tuple[dict[str, JsonValue], ...]
    faults: tuple[str, ...]


@dataclass(frozen=True)
class StoredDeviceArtifact:
    digest_sha256: str
    size_bytes: int
    pinned: bool


@dataclass(frozen=True)
class StoredDiscoveredEndpoint:
    id: str
    base_url: str
    device_kind: DeviceKind
    software_version: str
    protocol_minimum: int
    protocol_maximum: int
    capability_kinds: tuple[str, ...]
    compatible: bool
    state: Literal["discovered", "pairing", "enrolled", "rejected"]
    enrolled_device_id: str | None
    observed_at: str
    updated_at: str


class ProjectNotFoundError(LookupError):
    pass


class InstallationNotFoundError(LookupError):
    pass


class InstallationConflictError(RuntimeError):
    def __init__(self, current_revision: int | None) -> None:
        super().__init__("installation revision conflict")
        self.current_revision = current_revision


class ActiveInstallationDeleteError(RuntimeError):
    pass


class InstallationNameConflictError(RuntimeError):
    pass


class DeviceNotFoundError(LookupError):
    pass


class PairingTokenNotUsableError(RuntimeError):
    pass


class DiscoveredEndpointNotUsableError(RuntimeError):
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
                CREATE TABLE IF NOT EXISTS installations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    runtime_active INTEGER NOT NULL
                        CHECK (runtime_active IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_runtime_active_installation
                ON installations(runtime_active)
                WHERE runtime_active = 1;
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
                    revoked_at TEXT,
                    discovered_endpoint_id TEXT
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
                        CHECK (disconnected_at_ms IS NULL OR disconnected_at_ms >= 0),
                    latency_ms REAL,
                    clock_offset_ms REAL
                );
                CREATE TABLE IF NOT EXISTS device_connection_policies (
                    device_id TEXT PRIMARY KEY
                        REFERENCES devices(id) ON DELETE CASCADE,
                    policy TEXT NOT NULL
                        CHECK (policy IN ('continuous', 'on_demand')),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_artifact_status (
                    device_id TEXT PRIMARY KEY
                        REFERENCES devices(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL CHECK (observed_at_ms >= 0),
                    total_bytes INTEGER NOT NULL CHECK (total_bytes > 0),
                    free_bytes INTEGER NOT NULL CHECK (free_bytes >= 0),
                    supports_pinning INTEGER NOT NULL DEFAULT 0
                        CHECK (supports_pinning IN (0, 1)),
                    supports_resumable_transfer INTEGER NOT NULL DEFAULT 0
                        CHECK (supports_resumable_transfer IN (0, 1)),
                    transfers_json TEXT NOT NULL DEFAULT '[]',
                    faults_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_artifacts (
                    device_id TEXT NOT NULL
                        REFERENCES devices(id) ON DELETE CASCADE,
                    digest_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
                    PRIMARY KEY(device_id, digest_sha256)
                );
                CREATE TABLE IF NOT EXISTS discovered_endpoints (
                    id TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL UNIQUE,
                    device_kind TEXT NOT NULL,
                    software_version TEXT NOT NULL,
                    protocol_minimum INTEGER NOT NULL,
                    protocol_maximum INTEGER NOT NULL,
                    capability_kinds_json TEXT NOT NULL,
                    compatible INTEGER NOT NULL CHECK (compatible IN (0, 1)),
                    state TEXT NOT NULL CHECK (state IN ('discovered', 'rejected')),
                    pairing_token_id TEXT,
                    enrolled_device_id TEXT,
                    observed_at TEXT NOT NULL,
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
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (6, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                """
            )
            presence_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(device_presence)").fetchall()
            }
            if "latency_ms" not in presence_columns:
                connection.execute("ALTER TABLE device_presence ADD COLUMN latency_ms REAL")
            if "clock_offset_ms" not in presence_columns:
                connection.execute("ALTER TABLE device_presence ADD COLUMN clock_offset_ms REAL")
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (7, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (8, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (9, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            pairing_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(device_pairing_tokens)").fetchall()
            }
            if "discovered_endpoint_id" not in pairing_columns:
                connection.execute(
                    "ALTER TABLE device_pairing_tokens ADD COLUMN discovered_endpoint_id TEXT"
                )
            discovery_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(discovered_endpoints)").fetchall()
            }
            if "pairing_token_id" not in discovery_columns:
                connection.execute(
                    "ALTER TABLE discovered_endpoints ADD COLUMN pairing_token_id TEXT"
                )
            if "enrolled_device_id" not in discovery_columns:
                connection.execute(
                    "ALTER TABLE discovered_endpoints ADD COLUMN enrolled_device_id TEXT"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (10, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )
            artifact_status_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(device_artifact_status)"
                ).fetchall()
            }
            if "supports_pinning" not in artifact_status_columns:
                connection.execute(
                    "ALTER TABLE device_artifact_status "
                    "ADD COLUMN supports_pinning INTEGER NOT NULL DEFAULT 0"
                )
            if "supports_resumable_transfer" not in artifact_status_columns:
                connection.execute(
                    "ALTER TABLE device_artifact_status "
                    "ADD COLUMN supports_resumable_transfer INTEGER NOT NULL DEFAULT 0"
                )
            if "transfers_json" not in artifact_status_columns:
                connection.execute(
                    "ALTER TABLE device_artifact_status "
                    "ADD COLUMN transfers_json TEXT NOT NULL DEFAULT '[]'"
                )
            artifact_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(device_artifacts)").fetchall()
            }
            if "pinned" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE device_artifacts ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (11, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                """
            )

    def upsert_discovered_endpoint(
        self,
        *,
        base_url: str,
        device_kind: DeviceKind,
        software_version: str,
        protocol_minimum: int,
        protocol_maximum: int,
        capability_kinds: tuple[str, ...],
        compatible: bool,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredDiscoveredEndpoint:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM discovered_endpoints WHERE base_url = ?",
                (base_url,),
            ).fetchone()
            endpoint_id = str(row["id"]) if row is not None else str(uuid4())
            connection.execute(
                """
                INSERT INTO discovered_endpoints(
                    id, base_url, device_kind, software_version,
                    protocol_minimum, protocol_maximum, capability_kinds_json,
                    compatible, state, observed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?)
                ON CONFLICT(base_url) DO UPDATE SET
                    device_kind = excluded.device_kind,
                    software_version = excluded.software_version,
                    protocol_minimum = excluded.protocol_minimum,
                    protocol_maximum = excluded.protocol_maximum,
                    capability_kinds_json = excluded.capability_kinds_json,
                    compatible = excluded.compatible,
                    state = 'discovered',
                    observed_at = excluded.observed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    endpoint_id,
                    base_url,
                    device_kind.value,
                    software_version,
                    protocol_minimum,
                    protocol_maximum,
                    json.dumps(capability_kinds, separators=(",", ":")),
                    int(compatible),
                    occurred_at,
                    occurred_at,
                ),
            )
            self._insert_audit(
                connection,
                actor=actor,
                action="device.discovery.probe",
                target=endpoint_id,
                result="compatible" if compatible else "incompatible",
                occurred_at=occurred_at,
                authorization_context={
                    "role": role.value,
                    "base_url": base_url,
                    "protocol_minimum": protocol_minimum,
                    "protocol_maximum": protocol_maximum,
                    "capability_kinds": list(capability_kinds),
                },
            )
        return self.get_discovered_endpoint(endpoint_id)

    def get_discovered_endpoint(self, endpoint_id: str) -> StoredDiscoveredEndpoint:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, base_url, device_kind, software_version,
                       protocol_minimum, protocol_maximum, capability_kinds_json,
                       compatible, state, pairing_token_id, enrolled_device_id,
                       observed_at, updated_at
                FROM discovered_endpoints
                WHERE id = ?
                """,
                (endpoint_id,),
            ).fetchone()
        if row is None:
            raise DeviceNotFoundError(endpoint_id)
        return _stored_discovered_endpoint(row)

    def list_discovered_endpoints(self) -> tuple[StoredDiscoveredEndpoint, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, base_url, device_kind, software_version,
                       protocol_minimum, protocol_maximum, capability_kinds_json,
                       compatible, state, pairing_token_id, enrolled_device_id,
                       observed_at, updated_at
                FROM discovered_endpoints
                ORDER BY updated_at DESC, id
                """
            ).fetchall()
        return tuple(_stored_discovered_endpoint(row) for row in rows)

    def reject_discovered_endpoint(
        self,
        *,
        endpoint_id: str,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredDiscoveredEndpoint:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT pairing_token_id FROM discovered_endpoints WHERE id = ?",
                (endpoint_id,),
            ).fetchone()
            if row is None:
                raise DeviceNotFoundError(endpoint_id)
            if row["pairing_token_id"] is not None:
                connection.execute(
                    """
                    UPDATE device_pairing_tokens
                    SET revoked_at = ?
                    WHERE id = ? AND consumed_at IS NULL AND revoked_at IS NULL
                    """,
                    (occurred_at, str(row["pairing_token_id"])),
                )
            connection.execute(
                """
                UPDATE discovered_endpoints
                SET state = 'rejected', pairing_token_id = NULL, updated_at = ?
                WHERE id = ?
                """,
                (occurred_at, endpoint_id),
            )
            self._insert_audit(
                connection,
                actor=actor,
                action="device.discovery.reject",
                target=endpoint_id,
                result="success",
                occurred_at=occurred_at,
                authorization_context={"role": role.value},
            )
        return self.get_discovered_endpoint(endpoint_id)

    def ensure_runtime_installation(
        self,
        *,
        name: str,
        occurred_at: str,
    ) -> StoredInstallation:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, revision, runtime_active, created_at, updated_at
                FROM installations
                WHERE runtime_active = 1
                """
            ).fetchone()
            if row is not None:
                return _stored_installation(row)
            installation_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO installations(
                    id, name, revision, runtime_active, created_at, updated_at
                ) VALUES (?, ?, 1, 1, ?, ?)
                """,
                (installation_id, name, occurred_at, occurred_at),
            )
        return StoredInstallation(
            id=installation_id,
            name=name,
            revision=1,
            runtime_active=True,
            created_at=occurred_at,
            updated_at=occurred_at,
        )

    def create_installation(
        self,
        *,
        name: str,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredInstallation:
        installation = StoredInstallation(
            id=str(uuid4()),
            name=name,
            revision=1,
            runtime_active=False,
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO installations(
                        id, name, revision, runtime_active, created_at, updated_at
                    ) VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (
                        installation.id,
                        installation.name,
                        installation.revision,
                        installation.created_at,
                        installation.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise InstallationNameConflictError(name) from error
            self._insert_audit(
                connection,
                actor=actor,
                action="installation.create",
                target=installation.id,
                result="success",
                occurred_at=occurred_at,
                authorization_context={"role": role.value, "name": name},
            )
        return installation

    def list_installations(self) -> tuple[StoredInstallation, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, name, revision, runtime_active, created_at, updated_at
                FROM installations
                ORDER BY runtime_active DESC, name, id
                """
            ).fetchall()
        return tuple(_stored_installation(row) for row in rows)

    def get_runtime_installation(self) -> StoredInstallation:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, revision, runtime_active, created_at, updated_at
                FROM installations
                WHERE runtime_active = 1
                """
            ).fetchone()
        if row is None:
            raise InstallationNotFoundError("runtime")
        return _stored_installation(row)

    def get_installation(self, installation_id: str) -> StoredInstallation:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, revision, runtime_active, created_at, updated_at
                FROM installations
                WHERE id = ?
                """,
                (installation_id,),
            ).fetchone()
        if row is None:
            raise InstallationNotFoundError(installation_id)
        return _stored_installation(row)

    def update_installation(
        self,
        *,
        installation_id: str,
        expected_revision: int,
        name: str,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredInstallation:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM installations WHERE id = ?",
                (installation_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise InstallationNotFoundError(installation_id)
            current_revision = int(row["revision"])
            if current_revision != expected_revision:
                connection.rollback()
                raise InstallationConflictError(current_revision)
            next_revision = current_revision + 1
            try:
                connection.execute(
                    """
                    UPDATE installations
                    SET name = ?, revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (name, next_revision, occurred_at, installation_id),
                )
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise InstallationNameConflictError(name) from error
            self._insert_audit(
                connection,
                actor=actor,
                action="installation.update",
                target=installation_id,
                result="success",
                occurred_at=occurred_at,
                authorization_context={
                    "role": role.value,
                    "previous_revision": current_revision,
                    "created_revision": next_revision,
                },
            )
            connection.commit()
        return next(
            installation
            for installation in self.list_installations()
            if installation.id == installation_id
        )

    def delete_installation(
        self,
        *,
        installation_id: str,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision, runtime_active FROM installations WHERE id = ?",
                (installation_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise InstallationNotFoundError(installation_id)
            current_revision = int(row["revision"])
            if current_revision != expected_revision:
                connection.rollback()
                raise InstallationConflictError(current_revision)
            if bool(row["runtime_active"]):
                connection.rollback()
                raise ActiveInstallationDeleteError(
                    "the runtime-active installation cannot be deleted"
                )
            connection.execute("DELETE FROM installations WHERE id = ?", (installation_id,))
            self._insert_audit(
                connection,
                actor=actor,
                action="installation.delete",
                target=installation_id,
                result="success",
                occurred_at=occurred_at,
                authorization_context={
                    "role": role.value,
                    "deleted_revision": current_revision,
                },
            )
            connection.commit()

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
        discovered_endpoint_id: str | None = None,
    ) -> StoredPairingToken:
        token_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if discovered_endpoint_id is not None:
                    endpoint = connection.execute(
                        """
                        SELECT device_kind, compatible, state, enrolled_device_id
                        FROM discovered_endpoints
                        WHERE id = ?
                        """,
                        (discovered_endpoint_id,),
                    ).fetchone()
                    if (
                        endpoint is None
                        or str(endpoint["device_kind"]) != device_kind.value
                        or not bool(endpoint["compatible"])
                        or str(endpoint["state"]) == "rejected"
                        or endpoint["enrolled_device_id"] is not None
                    ):
                        raise DiscoveredEndpointNotUsableError(
                            "discovered endpoint is not compatible and enrollable"
                        )
                connection.execute(
                    """
                    INSERT INTO device_pairing_tokens(
                        id, token_hash, device_kind, created_by, created_at,
                        expires_at, discovered_endpoint_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_id,
                        _digest(token),
                        device_kind.value,
                        created_by,
                        created_at,
                        expires_at,
                        discovered_endpoint_id,
                    ),
                )
                if discovered_endpoint_id is not None:
                    connection.execute(
                        """
                        UPDATE discovered_endpoints
                        SET pairing_token_id = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (token_id, created_at, discovered_endpoint_id),
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
                        "discovered_endpoint_id": discovered_endpoint_id,
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
            discovered_endpoint_id=discovered_endpoint_id,
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
                    SELECT id, device_kind, discovered_endpoint_id
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
                if token_row["discovered_endpoint_id"] is not None:
                    connection.execute(
                        """
                        UPDATE discovered_endpoints
                        SET pairing_token_id = NULL,
                            enrolled_device_id = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            device_id,
                            occurred_at,
                            str(token_row["discovered_endpoint_id"]),
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
                    last_seen_at_ms, disconnected_at_ms,
                    latency_ms, clock_offset_ms
                ) VALUES (?, ?, ?, ?, NULL, NULL, NULL)
                ON CONFLICT(device_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    connected_at_ms = excluded.connected_at_ms,
                    last_seen_at_ms = excluded.last_seen_at_ms,
                    disconnected_at_ms = NULL,
                    latency_ms = NULL,
                    clock_offset_ms = NULL
                """,
                (device_id, session_id, observed_at_ms, observed_at_ms),
            )
            self._insert_audit(
                connection,
                actor=device_id,
                action="device.connected",
                target=device_id,
                result="success",
                occurred_at=_iso_from_ms(observed_at_ms),
                authorization_context={"session_id": session_id},
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
            cursor = connection.execute(
                """
                UPDATE device_presence
                SET disconnected_at_ms = ?
                WHERE device_id = ?
                  AND session_id = ?
                  AND disconnected_at_ms IS NULL
                """,
                (observed_at_ms, device_id, session_id),
            )
            if cursor.rowcount:
                self._insert_audit(
                    connection,
                    actor=device_id,
                    action="device.disconnected",
                    target=device_id,
                    result="success",
                    occurred_at=_iso_from_ms(observed_at_ms),
                    authorization_context={"session_id": session_id},
                )

    def mark_device_timing(
        self,
        *,
        device_id: str,
        session_id: str,
        latency_ms: float,
        clock_offset_ms: float,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE device_presence
                SET latency_ms = ?, clock_offset_ms = ?
                WHERE device_id = ?
                  AND session_id = ?
                  AND disconnected_at_ms IS NULL
                """,
                (latency_ms, clock_offset_ms, device_id, session_id),
            )
        if cursor.rowcount == 0:
            raise DeviceCertificateAuthorizationError("timing report session is not active")

    def list_device_presence(self) -> tuple[StoredDevicePresence, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT device_id, session_id, connected_at_ms,
                       last_seen_at_ms, disconnected_at_ms,
                       latency_ms, clock_offset_ms
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
                latency_ms=(float(row["latency_ms"]) if row["latency_ms"] is not None else None),
                clock_offset_ms=(
                    float(row["clock_offset_ms"]) if row["clock_offset_ms"] is not None else None
                ),
            )
            for row in rows
        )

    def replace_device_artifact_inventory(
        self,
        *,
        device_id: str,
        session_id: str,
        observed_at_ms: int,
        total_bytes: int,
        free_bytes: int,
        supports_pinning: bool,
        supports_resumable_transfer: bool,
        artifacts: tuple[tuple[str, int, bool], ...],
        transfers: tuple[dict[str, JsonValue], ...],
        faults: tuple[str, ...],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT 1
                FROM device_presence
                WHERE device_id = ?
                  AND session_id = ?
                  AND disconnected_at_ms IS NULL
                """,
                (device_id, session_id),
            ).fetchone()
            if active is None:
                connection.rollback()
                raise DeviceCertificateAuthorizationError(
                    "artifact inventory session is not active"
                )
            connection.execute(
                """
                INSERT INTO device_artifact_status(
                    device_id, session_id, observed_at_ms,
                    total_bytes, free_bytes, supports_pinning,
                    supports_resumable_transfer, transfers_json, faults_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    observed_at_ms = excluded.observed_at_ms,
                    total_bytes = excluded.total_bytes,
                    free_bytes = excluded.free_bytes,
                    supports_pinning = excluded.supports_pinning,
                    supports_resumable_transfer = excluded.supports_resumable_transfer,
                    transfers_json = excluded.transfers_json,
                    faults_json = excluded.faults_json
                """,
                (
                    device_id,
                    session_id,
                    observed_at_ms,
                    total_bytes,
                    free_bytes,
                    int(supports_pinning),
                    int(supports_resumable_transfer),
                    json.dumps(transfers, separators=(",", ":")),
                    json.dumps(faults, separators=(",", ":")),
                ),
            )
            connection.execute(
                "DELETE FROM device_artifacts WHERE device_id = ?",
                (device_id,),
            )
            connection.executemany(
                """
                INSERT INTO device_artifacts(
                    device_id, digest_sha256, size_bytes, pinned
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    (device_id, digest_sha256, size_bytes, int(pinned))
                    for digest_sha256, size_bytes, pinned in artifacts
                ),
            )
            connection.commit()

    def list_device_artifact_statuses(self) -> tuple[StoredDeviceArtifactStatus, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.device_id, s.observed_at_ms, s.total_bytes, s.free_bytes,
                       s.supports_pinning, s.supports_resumable_transfer,
                       s.transfers_json, s.faults_json,
                       COUNT(a.digest_sha256) AS artifact_count,
                       COALESCE(SUM(a.size_bytes), 0) AS artifact_bytes,
                       COALESCE(SUM(a.pinned), 0) AS pinned_count
                FROM device_artifact_status AS s
                LEFT JOIN device_artifacts AS a ON a.device_id = s.device_id
                GROUP BY s.device_id
                ORDER BY s.device_id
                """
            ).fetchall()
        return tuple(
            StoredDeviceArtifactStatus(
                device_id=str(row["device_id"]),
                observed_at_ms=int(row["observed_at_ms"]),
                total_bytes=int(row["total_bytes"]),
                free_bytes=int(row["free_bytes"]),
                artifact_count=int(row["artifact_count"]),
                artifact_bytes=int(row["artifact_bytes"]),
                pinned_count=int(row["pinned_count"]),
                supports_pinning=bool(row["supports_pinning"]),
                supports_resumable_transfer=bool(row["supports_resumable_transfer"]),
                transfers=tuple(json.loads(str(row["transfers_json"]))),
                faults=tuple(json.loads(str(row["faults_json"]))),
            )
            for row in rows
        )

    def list_device_artifacts(
        self,
        device_id: str,
    ) -> tuple[StoredDeviceArtifact, ...]:
        self.get_device(device_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT digest_sha256, size_bytes, pinned
                FROM device_artifacts
                WHERE device_id = ?
                ORDER BY digest_sha256
                """,
                (device_id,),
            ).fetchall()
        return tuple(
            StoredDeviceArtifact(
                digest_sha256=str(row["digest_sha256"]),
                size_bytes=int(row["size_bytes"]),
                pinned=bool(row["pinned"]),
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

    def restore_revision(
        self,
        *,
        project_id: str,
        source_revision: int,
        expected_revision: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredRevision:
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
                source_row = connection.execute(
                    """
                    SELECT content_json
                    FROM revisions
                    WHERE project_id = ? AND revision = ?
                    """,
                    (project_id, source_revision),
                ).fetchone()
                if source_row is None:
                    raise ProjectNotFoundError(f"{project_id}@{source_revision}")
                current_row = connection.execute(
                    """
                    SELECT content_json
                    FROM revisions
                    WHERE project_id = ? AND revision = ?
                    """,
                    (project_id, project.head_revision),
                ).fetchone()
                if current_row is None:
                    raise RuntimeError("project head revision is missing")
                source_json = str(source_row["content_json"])
                if expected_revision != project.head_revision:
                    conflicting_paths = _changed_top_level_paths(
                        str(current_row["content_json"]),
                        source_json,
                    )
                    self._insert_audit(
                        connection,
                        actor=actor,
                        action="draft.restore",
                        target=project_id,
                        result="revision_conflict",
                        occurred_at=occurred_at,
                        authorization_context={
                            "role": role.value,
                            "requested_revision": expected_revision,
                            "current_revision": project.head_revision,
                            "source_revision": source_revision,
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
                    ) VALUES (?, ?, ?, ?, ?, 'restore', ?)
                    """,
                    (
                        project_id,
                        next_revision,
                        project.head_revision,
                        source_json,
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
                    action="draft.restore",
                    target=project_id,
                    result="success",
                    occurred_at=occurred_at,
                    authorization_context={
                        "role": role.value,
                        "requested_revision": expected_revision,
                        "source_revision": source_revision,
                        "created_revision": next_revision,
                    },
                )
                connection.commit()
            except RevisionConflictError:
                raise
            except BaseException:
                connection.rollback()
                raise
        restored_draft = DraftProject.model_validate_json(source_json)
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
            "restore",
            occurred_at,
            restored_draft,
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


def _stored_installation(row: sqlite3.Row) -> StoredInstallation:
    return StoredInstallation(
        id=str(row["id"]),
        name=str(row["name"]),
        revision=int(row["revision"]),
        runtime_active=bool(row["runtime_active"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _stored_discovered_endpoint(row: sqlite3.Row) -> StoredDiscoveredEndpoint:
    state_value = str(row["state"])
    enrolled_device_id = (
        str(row["enrolled_device_id"]) if row["enrolled_device_id"] is not None else None
    )
    if state_value == "rejected":
        state: Literal["discovered", "pairing", "enrolled", "rejected"] = "rejected"
    elif enrolled_device_id is not None:
        state = "enrolled"
    elif row["pairing_token_id"] is not None:
        state = "pairing"
    elif state_value == "discovered":
        state = "discovered"
    else:
        raise RuntimeError(f"invalid discovered endpoint state: {state_value}")
    return StoredDiscoveredEndpoint(
        id=str(row["id"]),
        base_url=str(row["base_url"]),
        device_kind=DeviceKind(str(row["device_kind"])),
        software_version=str(row["software_version"]),
        protocol_minimum=int(row["protocol_minimum"]),
        protocol_maximum=int(row["protocol_maximum"]),
        capability_kinds=tuple(json.loads(str(row["capability_kinds_json"]))),
        compatible=bool(row["compatible"]),
        state=state,
        enrolled_device_id=enrolled_device_id,
        observed_at=str(row["observed_at"]),
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


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


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
