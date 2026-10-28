import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cueverge.gui.contracts import Role


@dataclass(frozen=True)
class LocalUser:
    username: str
    password_hash: str
    role: Role
    disabled: bool
    created_at: str


@dataclass(frozen=True)
class ProjectMembership:
    project_id: str
    username: str
    role: Role
    can_live_output: bool
    created_at: str
    updated_at: str


class CollaborationStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS local_users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (
                        role IN ('viewer', 'designer', 'operator', 'publisher', 'administrator')
                    ),
                    disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_memberships (
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (
                        role IN ('viewer', 'designer', 'operator', 'publisher', 'administrator')
                    ),
                    can_live_output INTEGER NOT NULL DEFAULT 0
                        CHECK (can_live_output IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, username)
                );
                CREATE TABLE IF NOT EXISTS project_comments (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    object_id TEXT,
                    body TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('open', 'resolved')),
                    created_by TEXT NOT NULL,
                    resolved_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_change_requests (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    object_id TEXT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('open', 'accepted', 'closed')),
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_review_links (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_presence (
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    username TEXT NOT NULL,
                    location TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, username)
                );
                """
            )

    def get_user(self, username: str) -> LocalUser | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT username, password_hash, role, disabled, created_at
                FROM local_users WHERE username = ?
                """,
                (username,),
            ).fetchone()
        return self._user(row) if row is not None else None

    def list_users(self) -> tuple[LocalUser, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT username, password_hash, role, disabled, created_at
                FROM local_users ORDER BY username COLLATE NOCASE
                """
            ).fetchall()
        return tuple(self._user(row) for row in rows)

    def upsert_user(
        self,
        username: str,
        password_hash: str,
        role: Role,
        disabled: bool,
        occurred_at: str,
    ) -> LocalUser:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO local_users(
                    username, password_hash, role, disabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    role = excluded.role,
                    disabled = excluded.disabled,
                    updated_at = excluded.updated_at
                """,
                (username, password_hash, role.value, int(disabled), occurred_at, occurred_at),
            )
        user = self.get_user(username)
        if user is None:
            raise RuntimeError("local user write did not persist")
        return user

    def put_membership(
        self,
        project_id: str,
        username: str,
        role: Role,
        can_live_output: bool,
        occurred_at: str,
    ) -> ProjectMembership:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_memberships(
                    project_id, username, role, can_live_output, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, username) DO UPDATE SET
                    role = excluded.role,
                    can_live_output = excluded.can_live_output,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    username,
                    role.value,
                    int(can_live_output),
                    occurred_at,
                    occurred_at,
                ),
            )
        membership = self.get_membership(project_id, username)
        if membership is None:
            raise RuntimeError("project membership write did not persist")
        return membership

    def get_membership(self, project_id: str, username: str) -> ProjectMembership | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT project_id, username, role, can_live_output, created_at, updated_at
                FROM project_memberships
                WHERE project_id = ? AND username = ?
                """,
                (project_id, username),
            ).fetchone()
        return self._membership(row) if row is not None else None

    def list_memberships(self, project_id: str) -> tuple[ProjectMembership, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT project_id, username, role, can_live_output, created_at, updated_at
                FROM project_memberships
                WHERE project_id = ?
                ORDER BY username COLLATE NOCASE
                """,
                (project_id,),
            ).fetchall()
        return tuple(self._membership(row) for row in rows)

    def project_ids_for_user(self, username: str) -> frozenset[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT project_id FROM project_memberships WHERE username = ?",
                (username,),
            ).fetchall()
        return frozenset(str(row["project_id"]) for row in rows)

    def has_live_output_permission(self, username: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM project_memberships
                WHERE username = ? AND can_live_output = 1
                LIMIT 1
                """,
                (username,),
            ).fetchone()
        return row is not None

    def create_comment(
        self,
        project_id: str,
        revision: int,
        object_id: str | None,
        body: str,
        actor: str,
        occurred_at: str,
    ) -> dict[str, object]:
        identifier = secrets.token_urlsafe(12)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_comments(
                    id, project_id, revision, object_id, body, state,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                (
                    identifier,
                    project_id,
                    revision,
                    object_id,
                    body,
                    actor,
                    occurred_at,
                    occurred_at,
                ),
            )
        return self._get_record("project_comments", identifier)

    def list_comments(self, project_id: str) -> tuple[dict[str, object], ...]:
        return self._list_records("project_comments", project_id)

    def set_comment_state(
        self, identifier: str, state: str, actor: str, occurred_at: str
    ) -> dict[str, object]:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE project_comments
                SET state = ?, resolved_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (state, actor if state == "resolved" else None, occurred_at, identifier),
            )
        if cursor.rowcount != 1:
            raise LookupError("comment not found")
        return self._get_record("project_comments", identifier)

    def create_change_request(
        self,
        project_id: str,
        revision: int,
        object_id: str | None,
        title: str,
        body: str,
        actor: str,
        occurred_at: str,
    ) -> dict[str, object]:
        identifier = secrets.token_urlsafe(12)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_change_requests(
                    id, project_id, revision, object_id, title, body, state,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
                """,
                (
                    identifier,
                    project_id,
                    revision,
                    object_id,
                    title,
                    body,
                    actor,
                    actor,
                    occurred_at,
                    occurred_at,
                ),
            )
        return self._get_record("project_change_requests", identifier)

    def list_change_requests(self, project_id: str) -> tuple[dict[str, object], ...]:
        return self._list_records("project_change_requests", project_id)

    def set_change_request_state(
        self, identifier: str, state: str, actor: str, occurred_at: str
    ) -> dict[str, object]:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE project_change_requests
                SET state = ?, updated_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (state, actor, occurred_at, identifier),
            )
        if cursor.rowcount != 1:
            raise LookupError("change request not found")
        return self._get_record("project_change_requests", identifier)

    def create_review_link(
        self,
        project_id: str,
        revision: int,
        expires_at: str,
        actor: str,
        occurred_at: str,
    ) -> tuple[dict[str, object], str]:
        identifier = secrets.token_urlsafe(12)
        token = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_review_links(
                    id, token_hash, project_id, revision, expires_at, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    self._digest(token),
                    project_id,
                    revision,
                    expires_at,
                    actor,
                    occurred_at,
                ),
            )
        return self._get_record("project_review_links", identifier), token

    def resolve_review_link(self, token: str, occurred_at: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, project_id, revision, expires_at, revoked_at, created_by, created_at
                FROM project_review_links
                WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?
                """,
                (self._digest(token), occurred_at),
            ).fetchone()
        return dict(row) if row is not None else None

    def heartbeat_presence(
        self, project_id: str, username: str, location: str, occurred_at: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO project_presence(project_id, username, location, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, username) DO UPDATE SET
                    location = excluded.location,
                    last_seen_at = excluded.last_seen_at
                """,
                (project_id, username, location, occurred_at),
            )

    def list_presence(self, project_id: str, active_after: str) -> tuple[dict[str, object], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT project_id, username, location, last_seen_at
                FROM project_presence
                WHERE project_id = ? AND last_seen_at >= ?
                ORDER BY username COLLATE NOCASE
                """,
                (project_id, active_after),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _get_record(self, table: str, identifier: str) -> dict[str, object]:
        if table not in {
            "project_comments",
            "project_change_requests",
            "project_review_links",
        }:
            raise ValueError("unsupported collaboration table")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE id = ?",
                (identifier,),
            ).fetchone()
        if row is None:
            raise LookupError("collaboration record not found")
        result = dict(row)
        result.pop("token_hash", None)
        return result

    def _list_records(self, table: str, project_id: str) -> tuple[dict[str, object], ...]:
        if table not in {"project_comments", "project_change_requests"}:
            raise ValueError("unsupported collaboration table")
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _user(row: sqlite3.Row) -> LocalUser:
        return LocalUser(
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
            role=Role(str(row["role"])),
            disabled=bool(row["disabled"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _membership(row: sqlite3.Row) -> ProjectMembership:
        return ProjectMembership(
            project_id=str(row["project_id"]),
            username=str(row["username"]),
            role=Role(str(row["role"])),
            can_live_output=bool(row["can_live_output"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, isolation_level=None, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
