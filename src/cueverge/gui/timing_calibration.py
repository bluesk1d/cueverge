import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import uuid4

from pydantic import JsonValue

from cueverge.gui.contracts import Role
from cueverge.gui.repository import GuiRepository

TargetKind = Literal["projector_mapping", "lighting_surface"]
AdjustmentKind = Literal["onset", "release"]
SessionState = Literal["active", "accepted", "cancelled", "expired"]


class TimingCalibrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredTimingCalibration:
    id: str
    target_kind: TargetKind
    geometry_id: str
    adjustment_kind: AdjustmentKind
    baseline_ms: int
    proposed_ms: int
    state: SessionState
    revision: int
    actor: str
    expires_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredAcceptedCalibration:
    id: str
    target_kind: TargetKind
    geometry_id: str
    adjustment_kind: AdjustmentKind
    accepted_ms: int
    resource: str
    device_id: str | None
    software_version: str | None
    test_asset_id: str | None
    test_asset_version: str | None
    observed_variability_ms: float | None
    notes: str | None
    context: dict[str, JsonValue]
    actor: str
    accepted_at: str
    stale_reasons: tuple[str, ...]


class TimingCalibrationService:
    def __init__(self, repository: GuiRepository) -> None:
        self.repository = repository

    def start(
        self,
        *,
        target_kind: TargetKind,
        geometry_id: str,
        adjustment_kind: AdjustmentKind,
        actor: str,
        role: Role,
        occurred_at: str,
        test_asset_id: str | None,
        test_asset_version: str | None,
        observed_variability_ms: float | None,
        notes: str | None,
        context: Mapping[str, JsonValue],
    ) -> StoredTimingCalibration:
        table, onset_column, release_column = _target(target_kind)
        column = onset_column if adjustment_kind == "onset" else release_column
        session_id = str(uuid4())
        expires_at = (
            (datetime.fromisoformat(occurred_at.replace("Z", "+00:00")) + timedelta(minutes=10))
            .astimezone(UTC)
            .isoformat()
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute(
                f"SELECT {column} AS baseline FROM {table} WHERE geometry_id = ?",
                (geometry_id,),
            ).fetchone()
            if target is None:
                raise TimingCalibrationError("timing calibration target does not exist")
            baseline = int(target["baseline"])
            connection.execute(
                """
                UPDATE timing_calibration_sessions
                SET state = 'cancelled', updated_at = ?
                WHERE target_kind = ? AND geometry_id = ? AND state = 'active'
                """,
                (occurred_at, target_kind, geometry_id),
            )
            connection.execute(
                """
                INSERT INTO timing_calibration_sessions(
                    id, target_kind, geometry_id, adjustment_kind,
                    baseline_ms, proposed_ms, state, actor, expires_at,
                    test_asset_id, test_asset_version, observed_variability_ms,
                    notes, context_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    target_kind,
                    geometry_id,
                    adjustment_kind,
                    baseline,
                    baseline,
                    actor,
                    expires_at,
                    test_asset_id,
                    test_asset_version,
                    observed_variability_ms,
                    notes,
                    json.dumps(context, sort_keys=True, separators=(",", ":")),
                    occurred_at,
                    occurred_at,
                ),
            )
            _audit(connection, actor, role, "start", session_id, occurred_at)
            connection.commit()
        return self.get(session_id)

    def update(
        self,
        session_id: str,
        *,
        expected_revision: int,
        proposed_ms: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredTimingCalibration:
        if proposed_ms < -5000 or proposed_ms > 5000:
            raise TimingCalibrationError("timing adjustment must be between -5000 and 5000 ms")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _active_session(connection, session_id, expected_revision, occurred_at)
            connection.execute(
                """
                UPDATE timing_calibration_sessions
                SET proposed_ms = ?, revision = revision + 1, updated_at = ?
                WHERE id = ?
                """,
                (proposed_ms, occurred_at, session_id),
            )
            _audit(
                connection,
                actor,
                role,
                "adjust",
                session_id,
                occurred_at,
                {"previous_ms": int(row["proposed_ms"]), "proposed_ms": proposed_ms},
            )
            connection.commit()
        return self.get(session_id)

    def finish(
        self,
        session_id: str,
        *,
        expected_revision: int,
        accept: bool,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredTimingCalibration:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _active_session(connection, session_id, expected_revision, occurred_at)
            if accept:
                target_kind = str(row["target_kind"])
                adjustment_kind = str(row["adjustment_kind"])
                table, onset_column, release_column = _target(cast(TargetKind, target_kind))
                column = onset_column if adjustment_kind == "onset" else release_column
                connection.execute(
                    f"""
                    UPDATE {table}
                    SET {column} = ?, revision = revision + 1, updated_at = ?
                    WHERE geometry_id = ?
                    """,
                    (int(row["proposed_ms"]), occurred_at, str(row["geometry_id"])),
                )
                resource = connection.execute(
                    f"SELECT resource FROM {table} WHERE geometry_id = ?",
                    (str(row["geometry_id"]),),
                ).fetchone()
                if resource is None:
                    raise TimingCalibrationError("calibration resource no longer exists")
                resource_name = str(resource["resource"])
                identity = connection.execute(
                    """
                    SELECT d.id, d.capabilities_json
                    FROM resource_assignments AS a
                    JOIN devices AS d ON d.id = a.device_id
                    WHERE a.resource = ?
                    """,
                    (resource_name,),
                ).fetchone()
                device_id = str(identity["id"]) if identity is not None else None
                software_version = None
                if identity is not None:
                    capabilities = json.loads(str(identity["capabilities_json"]))
                    software_version = capabilities.get("software_version")
                connection.execute(
                    """
                    INSERT INTO accepted_calibrations(
                        id, target_kind, geometry_id, adjustment_kind,
                        accepted_ms, resource, device_id, software_version,
                        test_asset_id, test_asset_version, observed_variability_ms,
                        notes, context_json, actor, accepted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        target_kind,
                        str(row["geometry_id"]),
                        adjustment_kind,
                        int(row["proposed_ms"]),
                        resource_name,
                        device_id,
                        software_version,
                        row["test_asset_id"],
                        row["test_asset_version"],
                        row["observed_variability_ms"],
                        row["notes"],
                        str(row["context_json"]),
                        actor,
                        occurred_at,
                    ),
                )
            state = "accepted" if accept else "cancelled"
            connection.execute(
                """
                UPDATE timing_calibration_sessions
                SET state = ?, revision = revision + 1, updated_at = ?
                WHERE id = ?
                """,
                (state, occurred_at, session_id),
            )
            _audit(connection, actor, role, state, session_id, occurred_at)
            connection.commit()
        return self.get(session_id)

    def get(self, session_id: str) -> StoredTimingCalibration:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM timing_calibration_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise TimingCalibrationError("timing calibration session does not exist")
        return _stored_session(row)

    def accepted(
        self,
        target_kind: TargetKind,
        geometry_id: str,
        current_context: Mapping[str, JsonValue],
    ) -> tuple[StoredAcceptedCalibration, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM accepted_calibrations
                WHERE target_kind = ? AND geometry_id = ?
                ORDER BY accepted_at DESC, id DESC
                """,
                (target_kind, geometry_id),
            ).fetchall()
            results: list[StoredAcceptedCalibration] = []
            for row in rows:
                identity = connection.execute(
                    """
                    SELECT d.id, d.capabilities_json
                    FROM resource_assignments AS a
                    JOIN devices AS d ON d.id = a.device_id
                    WHERE a.resource = ?
                    """,
                    (str(row["resource"]),),
                ).fetchone()
                current_device_id = str(identity["id"]) if identity is not None else None
                current_version = None
                if identity is not None:
                    current_version = json.loads(str(identity["capabilities_json"])).get(
                        "software_version"
                    )
                recorded_context = json.loads(str(row["context_json"]))
                stale: list[str] = []
                if current_device_id != row["device_id"]:
                    stale.append("Assigned device changed.")
                if current_version != row["software_version"]:
                    stale.append("Device software or firmware changed.")
                for key in (
                    "hardware",
                    "routing",
                    "display_mode",
                    "media_settings",
                    "environment",
                ):
                    if current_context.get(key) != recorded_context.get(key):
                        stale.append(f"{key.replace('_', ' ').title()} changed.")
                results.append(_stored_accepted(row, tuple(stale)))
        return tuple(results)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.repository.database_path,
            isolation_level=None,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def _target(target_kind: TargetKind) -> tuple[str, str, str]:
    return (
        ("projector_surface_mappings", "media_start_delay_ms", "media_release_delay_ms")
        if target_kind == "projector_mapping"
        else ("lighting_surfaces", "timing_offset_ms", "release_offset_ms")
    )


def _active_session(
    connection: sqlite3.Connection,
    session_id: str,
    expected_revision: int,
    occurred_at: str,
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM timing_calibration_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise TimingCalibrationError("timing calibration session does not exist")
    if int(row["revision"]) != expected_revision:
        raise TimingCalibrationError("timing calibration revision conflict")
    if str(row["state"]) != "active":
        raise TimingCalibrationError("timing calibration session is not active")
    if str(row["expires_at"]) <= occurred_at:
        connection.execute(
            "UPDATE timing_calibration_sessions SET state = 'expired' WHERE id = ?",
            (session_id,),
        )
        raise TimingCalibrationError("timing calibration session expired")
    return cast(sqlite3.Row, row)


def _audit(
    connection: sqlite3.Connection,
    actor: str,
    role: Role,
    action: str,
    session_id: str,
    occurred_at: str,
    context: dict[str, object] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_events(
            actor, action, target, result, occurred_at, authorization_context_json
        ) VALUES (?, ?, ?, 'success', ?, ?)
        """,
        (
            actor,
            f"timing_calibration.{action}",
            session_id,
            occurred_at,
            json.dumps(
                {"role": role.value, "simulation_only": True, **(context or {})},
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )


def _stored_session(row: sqlite3.Row) -> StoredTimingCalibration:
    return StoredTimingCalibration(
        id=str(row["id"]),
        target_kind=cast(TargetKind, str(row["target_kind"])),
        geometry_id=str(row["geometry_id"]),
        adjustment_kind=cast(AdjustmentKind, str(row["adjustment_kind"])),
        baseline_ms=int(row["baseline_ms"]),
        proposed_ms=int(row["proposed_ms"]),
        state=cast(SessionState, str(row["state"])),
        revision=int(row["revision"]),
        actor=str(row["actor"]),
        expires_at=str(row["expires_at"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _stored_accepted(
    row: sqlite3.Row,
    stale_reasons: tuple[str, ...],
) -> StoredAcceptedCalibration:
    context = json.loads(str(row["context_json"]))
    if not isinstance(context, dict):
        raise TimingCalibrationError("accepted calibration context is invalid")
    return StoredAcceptedCalibration(
        id=str(row["id"]),
        target_kind=cast(TargetKind, str(row["target_kind"])),
        geometry_id=str(row["geometry_id"]),
        adjustment_kind=cast(AdjustmentKind, str(row["adjustment_kind"])),
        accepted_ms=int(row["accepted_ms"]),
        resource=str(row["resource"]),
        device_id=str(row["device_id"]) if row["device_id"] is not None else None,
        software_version=(
            str(row["software_version"]) if row["software_version"] is not None else None
        ),
        test_asset_id=(str(row["test_asset_id"]) if row["test_asset_id"] is not None else None),
        test_asset_version=(
            str(row["test_asset_version"]) if row["test_asset_version"] is not None else None
        ),
        observed_variability_ms=(
            float(row["observed_variability_ms"])
            if row["observed_variability_ms"] is not None
            else None
        ),
        notes=str(row["notes"]) if row["notes"] is not None else None,
        context=context,
        actor=str(row["actor"]),
        accepted_at=str(row["accepted_at"]),
        stale_reasons=stale_reasons,
    )
