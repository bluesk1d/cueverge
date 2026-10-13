import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue

from cueverge.gui.contracts import DraftProject, Role


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


class ProjectNotFoundError(LookupError):
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
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
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


def _draft_json(draft: DraftProject) -> str:
    return json.dumps(draft.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _changed_top_level_paths(current_json: str, proposed_json: str) -> tuple[str, ...]:
    current = json.loads(current_json)
    proposed = json.loads(proposed_json)
    keys = sorted(set(current).union(proposed))
    return tuple(f"/{key}" for key in keys if current.get(key) != proposed.get(key))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
