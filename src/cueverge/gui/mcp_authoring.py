import hashlib
import json
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import JsonValue, ValidationError

from cueverge.core.loader import load_show_package
from cueverge.gui.compiler import CompilationError, DraftCompiler
from cueverge.gui.contracts import DraftProject, Role
from cueverge.gui.repository import GuiRepository, RevisionConflictError, StoredRevision
from cueverge.gui.simulation_scenarios import (
    SimulationScenarioService,
    StoredSimulationScenario,
)

MAX_MCP_PAYLOAD_BYTES = 256 * 1024
MAX_TOOL_CALLS_PER_MINUTE = 60
MAX_ACTIVE_PROPOSALS_PER_PROJECT = 20
MAX_SIMULATION_STEPS = 200
MAX_SIMULATION_DURATION_MS = 10 * 60 * 1000
MAX_TIMELINE_RANGE_MS = 5 * 60 * 1000
MAX_ASSET_RESULTS = 100


class McpScope(StrEnum):
    READ = "read"
    DRAFT_EDIT = "draft_edit"
    DIRECT_EDIT = "direct_edit"
    SIMULATION = "simulation"
    PUBLISH_PREPARE = "publish_prepare"
    PUBLISH = "publish"
    ACTIVATE = "activate"
    LIVE_OPERATION = "live_operation"


def allowed_mcp_scopes(
    global_role: Role,
    project_role: Role | None,
    can_live_output: bool,
) -> set[McpScope]:
    effective_role = Role.ADMINISTRATOR if global_role is Role.ADMINISTRATOR else project_role
    scopes = {McpScope.READ}
    if effective_role in {Role.DESIGNER, Role.PUBLISHER, Role.ADMINISTRATOR}:
        scopes.update({McpScope.DRAFT_EDIT, McpScope.DIRECT_EDIT})
    if effective_role in {Role.DESIGNER, Role.OPERATOR, Role.PUBLISHER, Role.ADMINISTRATOR}:
        scopes.add(McpScope.SIMULATION)
    if effective_role in {Role.PUBLISHER, Role.ADMINISTRATOR}:
        scopes.update({McpScope.PUBLISH_PREPARE, McpScope.PUBLISH})
        if global_role is Role.ADMINISTRATOR or can_live_output:
            scopes.add(McpScope.ACTIVATE)
    if can_live_output and effective_role in {Role.OPERATOR, Role.ADMINISTRATOR}:
        scopes.add(McpScope.LIVE_OPERATION)
    return scopes


class McpProposalState(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    APPROVED = "approved"
    MERGED = "merged"
    REJECTED = "rejected"
    FAILED = "failed"


class McpAuthoringError(RuntimeError):
    pass


@dataclass(frozen=True)
class McpToken:
    id: str
    client_id: str
    username: str
    project_id: str
    scopes: tuple[McpScope, ...]
    expires_at: str
    revoked_at: str | None
    created_at: str


@dataclass(frozen=True)
class NewMcpToken:
    record: McpToken
    token: str


@dataclass(frozen=True)
class McpProposal:
    id: str
    project_id: str
    base_revision: int
    title: str
    kind: str
    proposed_draft: DraftProject
    changed_paths: tuple[str, ...]
    state: McpProposalState
    client_id: str
    username: str
    validation: tuple[dict[str, JsonValue], ...]
    simulation: dict[str, JsonValue] | None
    approved_by: str | None
    disposition_reason: str | None
    created_at: str
    updated_at: str


class McpAuthoringStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_tokens (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    client_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    scopes_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mcp_proposals (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    base_revision INTEGER NOT NULL CHECK (base_revision >= 1),
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    proposed_draft_json TEXT NOT NULL,
                    changed_paths_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'proposed', 'validated', 'approved', 'merged',
                            'rejected', 'failed'
                        )
                    ),
                    client_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    validation_json TEXT NOT NULL DEFAULT '[]',
                    simulation_json TEXT,
                    approved_by TEXT,
                    disposition_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mcp_proposals_project_state
                ON mcp_proposals(project_id, state);
                """
            )

    def create_token(
        self,
        *,
        client_id: str,
        username: str,
        project_id: str,
        scopes: tuple[McpScope, ...],
        expires_at: str,
        occurred_at: str,
    ) -> NewMcpToken:
        token_id = str(uuid4())
        secret = secrets.token_urlsafe(32)
        token = f"cvmcp_{token_id}_{secret}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mcp_tokens(
                    id, token_hash, client_id, username, project_id,
                    scopes_json, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    _digest(token),
                    client_id,
                    username,
                    project_id,
                    json.dumps([scope.value for scope in scopes]),
                    expires_at,
                    occurred_at,
                ),
            )
        record = self.get_token(token_id)
        if record is None:
            raise McpAuthoringError("MCP token was not persisted")
        return NewMcpToken(record=record, token=token)

    def verify_token(self, token: str, occurred_at: str) -> McpToken | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, client_id, username, project_id, scopes_json,
                       expires_at, revoked_at, created_at
                FROM mcp_tokens
                WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?
                """,
                (_digest(token), occurred_at),
            ).fetchone()
        return self._token(row) if row is not None else None

    def get_token(self, token_id: str) -> McpToken | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, client_id, username, project_id, scopes_json,
                       expires_at, revoked_at, created_at
                FROM mcp_tokens WHERE id = ?
                """,
                (token_id,),
            ).fetchone()
        return self._token(row) if row is not None else None

    def list_tokens(self, project_id: str) -> tuple[McpToken, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, client_id, username, project_id, scopes_json,
                       expires_at, revoked_at, created_at
                FROM mcp_tokens WHERE project_id = ?
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        return tuple(self._token(row) for row in rows)

    def revoke_token(self, token_id: str, occurred_at: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE mcp_tokens SET revoked_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (occurred_at, token_id),
            )
        if cursor.rowcount != 1:
            raise McpAuthoringError("MCP token does not exist or is already revoked")

    def create_proposal(
        self,
        *,
        project_id: str,
        base_revision: int,
        title: str,
        kind: str,
        proposed_draft: DraftProject,
        changed_paths: tuple[str, ...],
        client_id: str,
        username: str,
        occurred_at: str,
    ) -> McpProposal:
        with self._connect() as connection:
            active_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM mcp_proposals
                    WHERE project_id = ?
                      AND state IN ('proposed', 'validated', 'approved')
                    """,
                    (project_id,),
                ).fetchone()[0]
            )
            if active_count >= MAX_ACTIVE_PROPOSALS_PER_PROJECT:
                raise McpAuthoringError("project has reached its active proposal limit")
            proposal_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO mcp_proposals(
                    id, project_id, base_revision, title, kind,
                    proposed_draft_json, changed_paths_json, state,
                    client_id, username, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    project_id,
                    base_revision,
                    title,
                    kind,
                    json.dumps(proposed_draft.model_dump(mode="json"), sort_keys=True),
                    json.dumps(changed_paths),
                    client_id,
                    username,
                    occurred_at,
                    occurred_at,
                ),
            )
        return self.require_proposal(proposal_id)

    def require_proposal(self, proposal_id: str) -> McpProposal:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise McpAuthoringError("MCP proposal does not exist")
        return self._proposal(row)

    def list_proposals(self, project_id: str) -> tuple[McpProposal, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM mcp_proposals
                WHERE project_id = ?
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        return tuple(self._proposal(row) for row in rows)

    def update_proposal(
        self,
        proposal_id: str,
        *,
        state: McpProposalState,
        occurred_at: str,
        validation: tuple[dict[str, JsonValue], ...] | None = None,
        simulation: dict[str, JsonValue] | None = None,
        approved_by: str | None = None,
        disposition_reason: str | None = None,
    ) -> McpProposal:
        updates = ["state = ?", "updated_at = ?"]
        values: list[object] = [state.value, occurred_at]
        if validation is not None:
            updates.append("validation_json = ?")
            values.append(json.dumps(validation, sort_keys=True))
        if simulation is not None:
            updates.append("simulation_json = ?")
            values.append(json.dumps(simulation, sort_keys=True))
        if approved_by is not None:
            updates.append("approved_by = ?")
            values.append(approved_by)
        if disposition_reason is not None:
            updates.append("disposition_reason = ?")
            values.append(disposition_reason)
        values.append(proposal_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE mcp_proposals SET {', '.join(updates)} WHERE id = ?",
                values,
            )
        if cursor.rowcount != 1:
            raise McpAuthoringError("MCP proposal does not exist")
        return self.require_proposal(proposal_id)

    @staticmethod
    def _token(row: sqlite3.Row) -> McpToken:
        return McpToken(
            id=str(row["id"]),
            client_id=str(row["client_id"]),
            username=str(row["username"]),
            project_id=str(row["project_id"]),
            scopes=tuple(McpScope(value) for value in json.loads(row["scopes_json"])),
            expires_at=str(row["expires_at"]),
            revoked_at=(str(row["revoked_at"]) if row["revoked_at"] is not None else None),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _proposal(row: sqlite3.Row) -> McpProposal:
        return McpProposal(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            base_revision=int(row["base_revision"]),
            title=str(row["title"]),
            kind=str(row["kind"]),
            proposed_draft=DraftProject.model_validate(json.loads(row["proposed_draft_json"])),
            changed_paths=tuple(json.loads(row["changed_paths_json"])),
            state=McpProposalState(str(row["state"])),
            client_id=str(row["client_id"]),
            username=str(row["username"]),
            validation=tuple(json.loads(row["validation_json"])),
            simulation=(
                cast(dict[str, JsonValue], json.loads(row["simulation_json"]))
                if row["simulation_json"] is not None
                else None
            ),
            approved_by=(str(row["approved_by"]) if row["approved_by"] is not None else None),
            disposition_reason=(
                str(row["disposition_reason"]) if row["disposition_reason"] is not None else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, isolation_level=None, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


class McpAuthoringService:
    def __init__(
        self,
        store: McpAuthoringStore,
        repository: GuiRepository,
        compiler: DraftCompiler,
        simulations: SimulationScenarioService,
    ) -> None:
        self.store = store
        self.repository = repository
        self.compiler = compiler
        self.simulations = simulations
        self._tool_calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_rate_limit(self, token_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            calls = self._tool_calls[token_id]
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= MAX_TOOL_CALLS_PER_MINUTE:
                raise McpAuthoringError("MCP tool-call rate limit exceeded")
            calls.append(now)

    def propose_scene_outline(
        self,
        *,
        project_id: str,
        base_revision: int,
        template_scene_id: str,
        scene_id: str,
        name: str,
        duration_ms: int,
        client_id: str,
        username: str,
    ) -> McpProposal:
        if not scene_id or len(scene_id) > 100 or not name or len(name) > 200:
            raise McpAuthoringError("scene identity or name is invalid")
        if duration_ms < 100 or duration_ms > MAX_TIMELINE_RANGE_MS:
            raise McpAuthoringError("scene duration is outside the MCP timeline limit")
        revision = self.repository.get_revision(project_id, base_revision)
        if any(scene.id == scene_id for scene in revision.draft.scenes):
            raise McpAuthoringError("scene ID already exists")
        template = next(
            (scene for scene in revision.draft.scenes if scene.id == template_scene_id),
            None,
        )
        if template is None:
            raise McpAuthoringError("template scene does not exist")
        scene_document = template.model_dump(mode="json")
        scene_document.update({"id": scene_id, "name": name, "duration_ms": duration_ms})
        proposed_document = revision.draft.model_dump(mode="json")
        scenes = cast(list[dict[str, JsonValue]], proposed_document["scenes"])
        scenes.append(cast(dict[str, JsonValue], scene_document))
        try:
            proposed = DraftProject.model_validate(proposed_document)
        except ValidationError as error:
            raise McpAuthoringError("proposed scene does not satisfy the draft schema") from error
        return self.store.create_proposal(
            project_id=project_id,
            base_revision=base_revision,
            title=f"Create scene: {name}",
            kind="scene_outline",
            proposed_draft=proposed,
            changed_paths=(f"/scenes/{len(scenes) - 1}",),
            client_id=client_id,
            username=username,
            occurred_at=_now_iso(),
        )

    def propose_project_revision(
        self,
        *,
        project_id: str,
        base_revision: int,
        title: str,
        kind: str,
        proposed_document: dict[str, JsonValue],
        client_id: str,
        username: str,
    ) -> McpProposal:
        proposed_draft, proposal_paths = self._prepare_revision(
            project_id, base_revision, proposed_document
        )
        if not proposal_paths:
            raise McpAuthoringError("proposal does not change the working revision")
        return self.store.create_proposal(
            project_id=project_id,
            base_revision=base_revision,
            title=title,
            kind=kind,
            proposed_draft=proposed_draft,
            changed_paths=proposal_paths,
            client_id=client_id,
            username=username,
            occurred_at=_now_iso(),
        )

    def update_project_draft(
        self,
        *,
        project_id: str,
        base_revision: int,
        proposed_document: dict[str, JsonValue],
        username: str,
        role: Role,
    ) -> StoredRevision:
        draft, paths = self._prepare_revision(project_id, base_revision, proposed_document)
        if not paths:
            raise McpAuthoringError("edit does not change the working revision")
        try:
            return self.repository.update_draft(
                project_id=project_id,
                expected_revision=base_revision,
                draft=draft,
                actor=username,
                role=role,
                occurred_at=_now_iso(),
            )
        except RevisionConflictError as error:
            raise McpAuthoringError(
                "working revision changed during editing; read the latest draft before retrying"
            ) from error

    def _prepare_revision(
        self,
        project_id: str,
        base_revision: int,
        proposed_document: dict[str, JsonValue],
    ) -> tuple[DraftProject, tuple[str, ...]]:
        current = self.repository.get_revision(project_id)
        if current.revision != base_revision:
            raise McpAuthoringError("base revision is not the current working revision")
        try:
            draft = DraftProject.model_validate(proposed_document)
        except ValidationError as error:
            raise McpAuthoringError(
                f"proposed draft does not match the authoring schema: {error}"
            ) from error
        return draft, changed_paths(
            current.draft.model_dump(mode="json"), draft.model_dump(mode="json")
        )

    def validate_proposal(self, proposal_id: str) -> McpProposal:
        proposal = self.store.require_proposal(proposal_id)
        if proposal.state not in {
            McpProposalState.PROPOSED,
            McpProposalState.VALIDATED,
        }:
            raise McpAuthoringError("proposal cannot be validated in its current state")
        diagnostics: list[dict[str, JsonValue]] = []
        try:
            self.compiler.compile(
                project_id=f"mcp-{proposal.id}",
                revision=proposal.base_revision,
                draft=proposal.proposed_draft,
            )
        except CompilationError as error:
            diagnostics.extend(
                {
                    "code": item.code,
                    "severity": "error",
                    "message": item.message,
                    "object_ids": list(item.object_ids),
                }
                for item in error.diagnostics
            )
            if not error.diagnostics:
                diagnostics.append(
                    {
                        "code": "compilation_failed",
                        "severity": "error",
                        "message": str(error),
                    }
                )
        state = (
            McpProposalState.FAILED
            if any(item.get("severity") == "error" for item in diagnostics)
            else McpProposalState.VALIDATED
        )
        return self.store.update_proposal(
            proposal_id,
            state=state,
            validation=tuple(diagnostics),
            occurred_at=_now_iso(),
        )

    async def simulate_proposal(
        self,
        proposal_id: str,
        steps: tuple[dict[str, JsonValue], ...],
        expected: dict[str, JsonValue],
    ) -> McpProposal:
        if len(steps) > MAX_SIMULATION_STEPS:
            raise McpAuthoringError("simulation step limit exceeded")
        duration = 0
        for step in steps:
            if step.get("kind") != "advance":
                continue
            delta = step.get("delta_ms", 0)
            if not isinstance(delta, int) or isinstance(delta, bool) or delta < 0:
                raise McpAuthoringError("simulation advance delta must be non-negative")
            duration += delta
        if duration > MAX_SIMULATION_DURATION_MS:
            raise McpAuthoringError("simulation duration limit exceeded")
        proposal = self.store.require_proposal(proposal_id)
        if proposal.state is not McpProposalState.VALIDATED:
            raise McpAuthoringError("proposal must pass validation before simulation")
        compiled = self.compiler.compile(
            project_id=f"mcp-{proposal.id}",
            revision=proposal.base_revision,
            draft=proposal.proposed_draft,
        )
        scenario = StoredSimulationScenario(
            id=f"mcp-{proposal.id}",
            project_id=proposal.project_id,
            name="MCP proposal simulation",
            source_revision=proposal.base_revision,
            steps=steps,
            expected=expected,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        result = await self.simulations.run(
            scenario,
            load_show_package(compiled.package_root),
        )
        return self.store.update_proposal(
            proposal_id,
            state=McpProposalState.VALIDATED,
            simulation=result,
            occurred_at=_now_iso(),
        )

    def approve(
        self,
        proposal_id: str,
        approver: str,
        reason: str | None = None,
    ) -> McpProposal:
        proposal = self.store.require_proposal(proposal_id)
        if proposal.state is not McpProposalState.VALIDATED:
            raise McpAuthoringError("only validated proposals can be approved")
        return self.store.update_proposal(
            proposal_id,
            state=McpProposalState.APPROVED,
            approved_by=approver,
            disposition_reason=reason,
            occurred_at=_now_iso(),
        )

    def reject(self, proposal_id: str, actor: str, reason: str) -> McpProposal:
        proposal = self.store.require_proposal(proposal_id)
        if proposal.state in {
            McpProposalState.MERGED,
            McpProposalState.REJECTED,
        }:
            raise McpAuthoringError("proposal is already final")
        return self.store.update_proposal(
            proposal_id,
            state=McpProposalState.REJECTED,
            approved_by=actor,
            disposition_reason=reason,
            occurred_at=_now_iso(),
        )

    def merge(self, proposal_id: str, actor: str, role: Role) -> McpProposal:
        proposal = self.store.require_proposal(proposal_id)
        if proposal.state is not McpProposalState.APPROVED:
            raise McpAuthoringError("proposal requires explicit approval before merge")
        try:
            self.repository.update_draft(
                project_id=proposal.project_id,
                expected_revision=proposal.base_revision,
                draft=proposal.proposed_draft,
                actor=actor,
                role=role,
                occurred_at=_now_iso(),
            )
        except RevisionConflictError as error:
            raise McpAuthoringError("working revision changed after proposal creation") from error
        return self.store.update_proposal(
            proposal_id,
            state=McpProposalState.MERGED,
            disposition_reason="merged into a new working revision",
            occurred_at=_now_iso(),
        )


def changed_paths(left: JsonValue, right: JsonValue, path: str = "") -> tuple[str, ...]:
    changes: list[str] = []
    if type(left) is not type(right):
        return (path or "/",)
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}/{_json_pointer(key)}"
            if key not in left or key not in right:
                changes.append(child)
            else:
                changes.extend(changed_paths(left[key], right[key], child))
    elif isinstance(left, list) and isinstance(right, list):
        for index in range(max(len(left), len(right))):
            child = f"{path}/{index}"
            if index >= len(left) or index >= len(right):
                changes.append(child)
            else:
                changes.extend(changed_paths(left[index], right[index], child))
    elif left != right:
        changes.append(path or "/")
    return tuple(changes)


def _json_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
