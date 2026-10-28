import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cueverge.gui.repository import GuiRepository

START_ERROR_LIMIT_MS = 20.0
DRIFT_LIMIT_MS_PER_MINUTE = 10.0
EVIDENCE_MAX_AGE = timedelta(days=30)


class SyncRehearsalError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredSyncRehearsal:
    id: str
    resources: tuple[str, ...]
    start_error_ms: float
    drift_ms_per_minute: float
    measurement_duration_seconds: int
    context: dict[str, object]
    notes: str | None
    actor: str
    measured_at: str


@dataclass(frozen=True)
class SyncAdmission:
    allowed: bool
    code: str
    message: str
    rehearsal: StoredSyncRehearsal | None


class SyncRehearsalService:
    def __init__(self, repository: GuiRepository) -> None:
        self.repository = repository

    def record(
        self,
        *,
        resources: tuple[str, ...],
        start_error_ms: float,
        drift_ms_per_minute: float,
        measurement_duration_seconds: int,
        context: dict[str, object],
        notes: str | None,
        actor: str,
        measured_at: str,
    ) -> StoredSyncRehearsal:
        normalized = tuple(sorted(set(resources)))
        if len(normalized) < 2:
            raise SyncRehearsalError("sync rehearsal requires at least two resources")
        if start_error_ms < 0 or drift_ms_per_minute < 0 or measurement_duration_seconds <= 0:
            raise SyncRehearsalError("sync rehearsal measurements are invalid")
        rehearsal_id = str(uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projector_sync_rehearsals(
                    id, resources_json, start_error_ms, drift_ms_per_minute,
                    measurement_duration_seconds, context_json, notes,
                    actor, measured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rehearsal_id,
                    json.dumps(normalized, separators=(",", ":")),
                    start_error_ms,
                    drift_ms_per_minute,
                    measurement_duration_seconds,
                    json.dumps(context, sort_keys=True, separators=(",", ":")),
                    notes,
                    actor,
                    measured_at,
                ),
            )
        return self.get(rehearsal_id)

    def get(self, rehearsal_id: str) -> StoredSyncRehearsal:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projector_sync_rehearsals WHERE id = ?",
                (rehearsal_id,),
            ).fetchone()
        if row is None:
            raise SyncRehearsalError("sync rehearsal does not exist")
        return _stored_rehearsal(row)

    def evaluate(
        self,
        *,
        resources: tuple[str, ...],
        current_context: dict[str, object],
        now: str,
    ) -> SyncAdmission:
        normalized = tuple(sorted(set(resources)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM projector_sync_rehearsals
                ORDER BY measured_at DESC, id DESC
                """
            ).fetchall()
        rehearsal = next(
            (
                _stored_rehearsal(row)
                for row in rows
                if tuple(json.loads(str(row["resources_json"]))) == normalized
            ),
            None,
        )
        if rehearsal is None:
            return SyncAdmission(
                False,
                "sync_rehearsal_missing",
                "No cross-projector rehearsal exists for this resource group.",
                None,
            )
        measured = datetime.fromisoformat(rehearsal.measured_at.replace("Z", "+00:00")).astimezone(
            UTC
        )
        observed_now = datetime.fromisoformat(now.replace("Z", "+00:00")).astimezone(UTC)
        if observed_now - measured > EVIDENCE_MAX_AGE:
            return SyncAdmission(
                False,
                "sync_rehearsal_stale",
                "Cross-projector rehearsal evidence is older than 30 days.",
                rehearsal,
            )
        if rehearsal.context != current_context:
            return SyncAdmission(
                False,
                "sync_rehearsal_context_changed",
                "Client, device, firmware, or routing context changed after rehearsal.",
                rehearsal,
            )
        if rehearsal.start_error_ms > START_ERROR_LIMIT_MS:
            return SyncAdmission(
                False,
                "sync_start_error_exceeded",
                (
                    f"Measured start error {rehearsal.start_error_ms:.1f} ms exceeds "
                    f"{START_ERROR_LIMIT_MS:.1f} ms."
                ),
                rehearsal,
            )
        if rehearsal.drift_ms_per_minute > DRIFT_LIMIT_MS_PER_MINUTE:
            return SyncAdmission(
                False,
                "sync_drift_exceeded",
                (
                    f"Measured drift {rehearsal.drift_ms_per_minute:.1f} ms/min exceeds "
                    f"{DRIFT_LIMIT_MS_PER_MINUTE:.1f} ms/min."
                ),
                rehearsal,
            )
        return SyncAdmission(
            True,
            "sync_within_tolerance",
            "Cross-projector rehearsal is current and within tolerance.",
            rehearsal,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.repository.database_path,
            isolation_level=None,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection


def _stored_rehearsal(row: sqlite3.Row) -> StoredSyncRehearsal:
    context = json.loads(str(row["context_json"]))
    if not isinstance(context, dict):
        raise SyncRehearsalError("stored sync rehearsal context is invalid")
    return StoredSyncRehearsal(
        id=str(row["id"]),
        resources=tuple(json.loads(str(row["resources_json"]))),
        start_error_ms=float(row["start_error_ms"]),
        drift_ms_per_minute=float(row["drift_ms_per_minute"]),
        measurement_duration_seconds=int(row["measurement_duration_seconds"]),
        context=context,
        notes=str(row["notes"]) if row["notes"] is not None else None,
        actor=str(row["actor"]),
        measured_at=str(row["measured_at"]),
    )
