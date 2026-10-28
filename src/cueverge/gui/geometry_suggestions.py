import json
import sqlite3
from dataclasses import dataclass
from typing import Literal, cast
from uuid import uuid4

from cueverge.gui.contracts import Role
from cueverge.gui.installation_geometry import GeometryKind
from cueverge.gui.repository import GuiRepository

SuggestionState = Literal["pending", "reviewed", "rejected"]


class GeometrySuggestionError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredGeometrySuggestion:
    id: str
    installation_id: str
    photo_id: str
    kind: GeometryKind
    name: str
    points: tuple[tuple[float, float], ...]
    closed: bool
    confidence: float
    evidence: str
    state: SuggestionState
    created_by: str
    reviewed_by: str | None
    created_at: str
    updated_at: str


class GeometrySuggestionService:
    def __init__(self, repository: GuiRepository) -> None:
        self.repository = repository

    def create(
        self,
        *,
        installation_id: str,
        photo_id: str,
        kind: GeometryKind,
        name: str,
        points: tuple[tuple[float, float], ...],
        closed: bool,
        confidence: float,
        evidence: str,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredGeometrySuggestion:
        suggestion_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            photo = connection.execute(
                """
                SELECT 1 FROM installation_photos
                WHERE id = ? AND installation_id = ?
                """,
                (photo_id, installation_id),
            ).fetchone()
            if photo is None:
                raise GeometrySuggestionError("suggestion photo does not exist")
            connection.execute(
                """
                INSERT INTO geometry_suggestions(
                    id, installation_id, photo_id, kind, name, points_json,
                    closed, confidence, evidence, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion_id,
                    installation_id,
                    photo_id,
                    kind,
                    name,
                    json.dumps(points, separators=(",", ":")),
                    int(closed),
                    confidence,
                    evidence,
                    actor,
                    occurred_at,
                    occurred_at,
                ),
            )
            _audit(connection, actor, role, "create", suggestion_id, occurred_at)
            connection.commit()
        return self.get(suggestion_id)

    def list(self, installation_id: str) -> tuple[StoredGeometrySuggestion, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM geometry_suggestions
                WHERE installation_id = ?
                ORDER BY state, confidence DESC, created_at, id
                """,
                (installation_id,),
            ).fetchall()
        return tuple(_stored_suggestion(row) for row in rows)

    def get(self, suggestion_id: str) -> StoredGeometrySuggestion:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM geometry_suggestions WHERE id = ?",
                (suggestion_id,),
            ).fetchone()
        if row is None:
            raise GeometrySuggestionError("geometry suggestion does not exist")
        return _stored_suggestion(row)

    def review(
        self,
        suggestion_id: str,
        *,
        state: Literal["reviewed", "rejected"],
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredGeometrySuggestion:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE geometry_suggestions
                SET state = ?, reviewed_by = ?, updated_at = ?
                WHERE id = ? AND state = 'pending'
                """,
                (state, actor, occurred_at, suggestion_id),
            )
            if changed.rowcount == 0:
                raise GeometrySuggestionError("pending geometry suggestion does not exist")
            _audit(connection, actor, role, state, suggestion_id, occurred_at)
            connection.commit()
        return self.get(suggestion_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.repository.database_path,
            isolation_level=None,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection


def _audit(
    connection: sqlite3.Connection,
    actor: str,
    role: Role,
    action: str,
    target: str,
    occurred_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_events(
            actor, action, target, result, occurred_at, authorization_context_json
        ) VALUES (?, ?, ?, 'success', ?, ?)
        """,
        (
            actor,
            f"installation.geometry_suggestion.{action}",
            target,
            occurred_at,
            json.dumps({"role": role.value}, sort_keys=True, separators=(",", ":")),
        ),
    )


def _stored_suggestion(row: sqlite3.Row) -> StoredGeometrySuggestion:
    points = json.loads(str(row["points_json"]))
    return StoredGeometrySuggestion(
        id=str(row["id"]),
        installation_id=str(row["installation_id"]),
        photo_id=str(row["photo_id"]),
        kind=cast(GeometryKind, str(row["kind"])),
        name=str(row["name"]),
        points=tuple((float(point[0]), float(point[1])) for point in points),
        closed=bool(row["closed"]),
        confidence=float(row["confidence"]),
        evidence=str(row["evidence"]),
        state=cast(SuggestionState, str(row["state"])),
        created_by=str(row["created_by"]),
        reviewed_by=(str(row["reviewed_by"]) if row["reviewed_by"] is not None else None),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
