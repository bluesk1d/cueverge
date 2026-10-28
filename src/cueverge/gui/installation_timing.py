"""Installation-level output timing: hardware latency compensation shared by every show.

Each output resource has one delay. Projectors (media agents) start their media that many
milliseconds after the scene asks them to; LED outputs render that many milliseconds behind
scene time. Scene-level output delays stack on top of these values.
"""

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from typing import Literal

from cueverge.gui.contracts import Role
from cueverge.gui.repository import GuiRepository

OutputTimingKind = Literal["media", "lighting"]
TIMING_LIMITS: dict[OutputTimingKind, tuple[int, int]] = {
    "media": (0, 5000),
    "lighting": (-5000, 5000),
}
MIGRATION_VERSION = 46


class InstallationTimingError(RuntimeError):
    pass


@dataclass(frozen=True)
class OutputTiming:
    resource: str
    kind: OutputTimingKind
    delay_ms: int
    revision: int
    updated_by: str | None
    updated_at: str | None

    @property
    def minimum_ms(self) -> int:
        return TIMING_LIMITS[self.kind][0]

    @property
    def maximum_ms(self) -> int:
        return TIMING_LIMITS[self.kind][1]


def timing_kind(resource_kind: str) -> OutputTimingKind | None:
    if resource_kind == "media_agent":
        return "media"
    if resource_kind == "pixel_output":
        return "lighting"
    return None


def validate_delay(kind: OutputTimingKind, delay_ms: int) -> None:
    minimum, maximum = TIMING_LIMITS[kind]
    if not minimum <= delay_ms <= maximum:
        noun = "Projector start delay" if kind == "media" else "LED timing offset"
        raise InstallationTimingError(f"{noun} must be between {minimum} and {maximum} ms")


def migrate_output_timing(repository: GuiRepository) -> None:
    """Create the store and, once, carry over the retired per-surface delays.

    Surfaces of one output that agree keep their value; disagreeing surfaces use the largest.
    Projector start delays are clamped to the non-negative range.
    """
    with closing(_connect(repository)) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS installation_output_timing (
                installation_id TEXT NOT NULL REFERENCES installations(id) ON DELETE CASCADE,
                resource TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('media', 'lighting')),
                delay_ms INTEGER NOT NULL CHECK (delay_ms >= -5000 AND delay_ms <= 5000),
                revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
                updated_by TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (installation_id, resource)
            )
            """
        )
        applied = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (MIGRATION_VERSION,)
        ).fetchone()
        if applied is not None:
            return
        sources: tuple[tuple[OutputTimingKind, str, str], ...] = (
            ("media", "projector_surface_mappings", "media_start_delay_ms"),
            ("lighting", "lighting_surfaces", "timing_offset_ms"),
        )
        for kind, table, column in sources:
            rows = connection.execute(
                f"""
                SELECT g.installation_id AS installation_id, s.resource AS resource,
                       s.{column} AS delay_ms
                FROM {table} AS s
                JOIN installation_geometry AS g ON g.id = s.geometry_id
                """
            ).fetchall()
            grouped: dict[tuple[str, str], set[int]] = {}
            for row in rows:
                grouped.setdefault((str(row["installation_id"]), str(row["resource"])), set()).add(
                    int(row["delay_ms"])
                )
            for (installation_id, resource), values in grouped.items():
                minimum, maximum = TIMING_LIMITS[kind]
                value = min(maximum, max(minimum, max(values)))
                if value == 0:
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO installation_output_timing(
                        installation_id, resource, kind, delay_ms, revision,
                        updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, 1, 'migration', strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                    """,
                    (installation_id, resource, kind, value),
                )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
            f"VALUES ({MIGRATION_VERSION},strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        )


class InstallationTimingService:
    def __init__(self, repository: GuiRepository) -> None:
        self.repository = repository
        migrate_output_timing(repository)

    def list(
        self, installation_id: str, outputs: Iterable[tuple[str, OutputTimingKind]]
    ) -> tuple[OutputTiming, ...]:
        """Timing for each (resource, kind) output; outputs never set default to 0."""
        stored = self._stored(installation_id)
        return tuple(
            stored[resource]
            if resource in stored and stored[resource].kind == kind
            else OutputTiming(resource, kind, 0, 0, None, None)
            for resource, kind in outputs
        )

    def delays(self, installation_id: str) -> dict[str, int]:
        return {resource: item.delay_ms for resource, item in self._stored(installation_id).items()}

    def set(
        self,
        *,
        installation_id: str,
        resource: str,
        kind: OutputTimingKind,
        delay_ms: int,
        expected_revision: int | None,
        actor: str,
        role: Role,
        occurred_at: str,
        reason: str = "manual",
    ) -> OutputTiming:
        validate_delay(kind, delay_ms)
        with closing(_connect(self.repository)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT kind, delay_ms, revision FROM installation_output_timing "
                "WHERE installation_id = ? AND resource = ?",
                (installation_id, resource),
            ).fetchone()
            current = int(row["revision"]) if row is not None and row["kind"] == kind else 0
            if expected_revision is not None and expected_revision != current:
                connection.rollback()
                raise InstallationTimingError(
                    f"{resource} timing changed since it was loaded; reload and retry"
                )
            previous = int(row["delay_ms"]) if row is not None and row["kind"] == kind else 0
            connection.execute(
                """
                INSERT INTO installation_output_timing(
                    installation_id, resource, kind, delay_ms, revision, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(installation_id, resource) DO UPDATE SET
                    kind = excluded.kind, delay_ms = excluded.delay_ms,
                    revision = excluded.revision, updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (installation_id, resource, kind, delay_ms, current + 1, actor, occurred_at),
            )
            connection.execute(
                """
                INSERT INTO audit_events(
                    actor, action, target, result, occurred_at, authorization_context_json
                ) VALUES (?, 'installation.output_timing.set', ?, 'success', ?, ?)
                """,
                (
                    actor,
                    f"{installation_id}:{resource}",
                    occurred_at,
                    json.dumps(
                        {
                            "role": role.value,
                            "kind": kind,
                            "previous_ms": previous,
                            "delay_ms": delay_ms,
                            "revision": current + 1,
                            "reason": reason,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.commit()
        return self._stored(installation_id)[resource]

    def _stored(self, installation_id: str) -> dict[str, OutputTiming]:
        with closing(_connect(self.repository)) as connection:
            rows = connection.execute(
                "SELECT resource, kind, delay_ms, revision, updated_by, updated_at "
                "FROM installation_output_timing WHERE installation_id = ?",
                (installation_id,),
            ).fetchall()
        return {
            str(row["resource"]): OutputTiming(
                resource=str(row["resource"]),
                kind="media" if row["kind"] == "media" else "lighting",
                delay_ms=int(row["delay_ms"]),
                revision=int(row["revision"]),
                updated_by=str(row["updated_by"]) if row["updated_by"] is not None else None,
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        }


def _connect(repository: GuiRepository) -> sqlite3.Connection:
    connection = sqlite3.connect(repository.database_path, isolation_level=None, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection
