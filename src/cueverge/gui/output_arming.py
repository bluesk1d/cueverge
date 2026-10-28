import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import uuid4

from cueverge.gui.contracts import Role
from cueverge.gui.repository import GuiRepository

ArmState = Literal["active", "stopped", "expired"]


class OutputArmError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredOutputArm:
    id: str
    resource: str
    resource_kind: str
    device_identity: str
    stop_action: str
    state: ArmState
    actor: str
    expires_at: str
    created_at: str
    updated_at: str


class OutputArmService:
    def __init__(self, repository: GuiRepository) -> None:
        self.repository = repository

    def create(
        self,
        *,
        resource: str,
        resource_kind: str,
        device_identity: str,
        stop_action: str,
        confirmation: str,
        duration_seconds: int,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredOutputArm:
        if confirmation != resource:
            raise OutputArmError("arming confirmation must exactly match the resource")
        arm_id = str(uuid4())
        expires_at = (
            (
                datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
                + timedelta(seconds=duration_seconds)
            )
            .astimezone(UTC)
            .isoformat()
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE output_safety_arms
                SET state = 'stopped', updated_at = ?
                WHERE resource = ? AND state = 'active'
                """,
                (occurred_at, resource),
            )
            connection.execute(
                """
                INSERT INTO output_safety_arms(
                    id, resource, resource_kind, device_identity, stop_action,
                    state, actor, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    arm_id,
                    resource,
                    resource_kind,
                    device_identity,
                    stop_action,
                    actor,
                    expires_at,
                    occurred_at,
                    occurred_at,
                ),
            )
            _audit(connection, actor, role, "arm", arm_id, occurred_at, resource)
            connection.commit()
        return self.get(arm_id, occurred_at)

    def stop(
        self,
        arm_id: str,
        *,
        actor: str,
        role: Role,
        occurred_at: str,
    ) -> StoredOutputArm:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """
                UPDATE output_safety_arms
                SET state = 'stopped', updated_at = ?
                WHERE id = ? AND state = 'active'
                """,
                (occurred_at, arm_id),
            )
            if changed.rowcount == 0:
                raise OutputArmError("active output arm does not exist")
            row = connection.execute(
                "SELECT resource FROM output_safety_arms WHERE id = ?",
                (arm_id,),
            ).fetchone()
            _audit(
                connection,
                actor,
                role,
                "stop",
                arm_id,
                occurred_at,
                str(row["resource"]),
            )
            connection.commit()
        return self.get(arm_id, occurred_at)

    def get(self, arm_id: str, now: str) -> StoredOutputArm:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM output_safety_arms WHERE id = ?",
                (arm_id,),
            ).fetchone()
            if row is None:
                raise OutputArmError("output arm does not exist")
            if str(row["state"]) == "active" and str(row["expires_at"]) <= now:
                connection.execute(
                    """
                    UPDATE output_safety_arms
                    SET state = 'expired', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, arm_id),
                )
                row = connection.execute(
                    "SELECT * FROM output_safety_arms WHERE id = ?",
                    (arm_id,),
                ).fetchone()
        return _stored_arm(cast(sqlite3.Row, row))

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


def _audit(
    connection: sqlite3.Connection,
    actor: str,
    role: Role,
    action: str,
    arm_id: str,
    occurred_at: str,
    resource: str,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_events(
            actor, action, target, result, occurred_at, authorization_context_json
        ) VALUES (?, ?, ?, 'success', ?, ?)
        """,
        (
            actor,
            f"output_arm.{action}",
            arm_id,
            occurred_at,
            json.dumps(
                {
                    "role": role.value,
                    "resource": resource,
                    "hardware_command_sent": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )


def _stored_arm(row: sqlite3.Row) -> StoredOutputArm:
    return StoredOutputArm(
        id=str(row["id"]),
        resource=str(row["resource"]),
        resource_kind=str(row["resource_kind"]),
        device_identity=str(row["device_identity"]),
        stop_action=str(row["stop_action"]),
        state=cast(ArmState, str(row["state"])),
        actor=str(row["actor"]),
        expires_at=str(row["expires_at"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
