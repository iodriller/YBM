from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
import sqlite3
from typing import Any

from agent_control.schemas import (
    ApprovalGrant,
    ApprovalRequest,
    ApprovalStatus,
    Artifact,
    AuditEvent,
    AuditEventType,
    Capability,
    ChannelType,
    InboundMessage,
    MemoryFact,
    ScheduleRecord,
    ScheduleStatus,
    TaskRecord,
    TaskSignal,
    TaskStatus,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
    utc_now,
)
from agent_control.storage.database import Database


logger = logging.getLogger(__name__)


def _dump(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _load(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _dt(value: datetime) -> str:
    return value.isoformat()


class ConversationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_or_create(self, channel: ChannelType, external_id: str) -> str:
        now = _dt(utc_now())
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id FROM conversations WHERE channel = ? AND external_id = ?",
                (channel.value, external_id),
            ).fetchone()
            if row:
                return str(row["id"])

            conversation_id = f"conv_{channel.value}_{external_id}"
            connection.execute(
                """
                INSERT INTO conversations (id, channel, external_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, channel.value, external_id, now, now),
            )
            return conversation_id


class MessageRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, message: InboundMessage, conversation_id: str | None = None) -> InboundMessage:
        self.try_create(message, conversation_id)
        return message

    def try_create(self, message: InboundMessage, conversation_id: str | None = None) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    id, conversation_id, channel, kind, sender_id, chat_id, text,
                    attachments_json, raw_json, correlation_id, received_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    conversation_id,
                    message.channel.value,
                    message.kind.value,
                    message.sender_id,
                    message.chat_id,
                    message.text,
                    _dump([attachment.model_dump(mode="json") for attachment in message.attachments]),
                    _dump(message.raw) if message.raw is not None else None,
                    message.correlation_id,
                    _dt(message.received_at),
                ),
            )
            return cursor.rowcount > 0


class ConversationMemoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT summary, facts_json, updated_at FROM conversation_memory WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "conversation_id": conversation_id,
            "summary": row["summary"],
            "facts": _load(row["facts_json"], {}),
            "updated_at": row["updated_at"],
        }

    def upsert(self, conversation_id: str, summary: str, facts: dict[str, Any]) -> dict[str, Any]:
        now = _dt(utc_now())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_memory (conversation_id, summary, facts_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    summary = excluded.summary,
                    facts_json = excluded.facts_json,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, summary, _dump(facts), now),
            )
        return {
            "conversation_id": conversation_id,
            "summary": summary,
            "facts": facts,
            "updated_at": now,
        }


class TaskRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        objective: str,
        conversation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        task = TaskRecord(objective=objective, conversation_id=conversation_id, metadata=metadata or {})
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    id, objective, status, conversation_id,
                    metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.objective,
                    task.status.value,
                    task.conversation_id,
                    _dump(task.metadata),
                    _dt(task.created_at),
                    _dt(task.updated_at),
                ),
            )
        return task

    def get(self, task_id: str) -> TaskRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def list_recent(self, limit: int = 20, offset: int = 0) -> list[TaskRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_for_conversation(self, conversation_id: str, limit: int = 50) -> list[TaskRecord]:
        """Oldest-first (a chat transcript reads top-to-bottom), unlike
        list_recent's newest-first - used by the local web chat channel
        (docs/HISTORY.md Part 4 T2.8) to render one conversation's history.
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def count(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()
        return int(row[0] if row else 0)

    def clear_history(self, include_active: bool = False) -> int:
        where_clause = ""
        args: list[str] = []
        if not include_active:
            terminal_statuses = [
                TaskStatus.BLOCKED.value,
                TaskStatus.CANCELLED.value,
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
            ]
            where_clause = f"WHERE status IN ({','.join('?' for _ in terminal_statuses)})"
            args = terminal_statuses

        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT id FROM tasks {where_clause}", args).fetchall()
            task_ids = [str(row["id"]) for row in rows]
            if not task_ids:
                return 0
            placeholders = ",".join("?" for _ in task_ids)
            for table in (
                "task_signals",
                "approvals",
                "tool_invocations",
                "artifacts",
                "audit_events",
            ):
                connection.execute(f"DELETE FROM {table} WHERE task_id IN ({placeholders})", task_ids)
            connection.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", task_ids)
        return len(task_ids)

    def list_by_statuses(self, statuses: list[TaskStatus], limit: int = 20) -> list[TaskRecord]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at ASC LIMIT ?",
                [*(status.value for status in statuses), limit],
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def claim_next(
        self,
        statuses: list[TaskStatus],
        worker_id: str,
        *,
        claim_expiry_seconds: int = 1200,
    ) -> TaskRecord | None:
        """Atomically claim the oldest workable task for ``worker_id``.

        Uses a single ``UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING *``
        statement, which SQLite serializes via its write lock. Two workers
        cannot both succeed at the same SELECT here — exactly one gets the
        row back; the other gets an empty result and tries again on the
        next poll.

        Returns ``None`` when no workable task is available. Claims that go
        stale (worker crashed) become eligible again after ``claim_expiry_seconds``.
        """
        if not statuses:
            return None
        now = utc_now()
        expires_at = now + timedelta(seconds=claim_expiry_seconds)
        placeholders = ",".join("?" for _ in statuses)
        # The inner SELECT picks the oldest task whose claim is either NULL or
        # already expired. The UPDATE writes the new claim and returns the row.
        # Eligible to claim: unclaimed, expired, OR already claimed by THIS
        # worker (so a single worker can iterate across multiple process_next
        # calls without re-racing for its own task).
        sql = f"""
            UPDATE tasks
            SET claimed_by = ?, claim_expires_at = ?, updated_at = ?
            WHERE id = (
                SELECT id FROM tasks
                WHERE status IN ({placeholders})
                  AND (
                      claimed_by IS NULL
                      OR claimed_by = ?
                      OR claim_expires_at IS NULL
                      OR claim_expires_at < ?
                  )
                ORDER BY created_at ASC
                LIMIT 1
            )
            RETURNING *
        """
        with self.database.connect() as connection:
            row = connection.execute(
                sql,
                [
                    worker_id,
                    _dt(expires_at),
                    _dt(now),
                    *(status.value for status in statuses),
                    worker_id,
                    _dt(now),
                ],
            ).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def release_claim(self, task_id: str) -> None:
        """Clear the worker claim on a task once it reaches a terminal state.

        Best-effort; failures are intentionally swallowed because this is
        purely a hint to other workers (the claim would also expire on its own).
        """
        try:
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE tasks SET claimed_by = NULL, claim_expires_at = NULL WHERE id = ?",
                    (task_id,),
                )
        except Exception:
            logger.debug("release_claim failed for task %s; claim will expire on its own", task_id, exc_info=True)

    def update_status(self, task_id: str, status: TaskStatus) -> TaskRecord:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _dt(now), task_id),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        return self._row_to_task(row)

    def update_objective(self, task_id: str, objective: str) -> TaskRecord:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE tasks SET objective = ?, updated_at = ? WHERE id = ?",
                (objective, _dt(now), task_id),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        return self._row_to_task(row)

    def update_metadata(
        self,
        task_id: str,
        metadata: dict[str, Any],
        status: TaskStatus | None = None,
    ) -> TaskRecord:
        now = utc_now()
        with self.database.connect() as connection:
            if status is None:
                connection.execute(
                    "UPDATE tasks SET metadata_json = ?, updated_at = ? WHERE id = ?",
                    (_dump(metadata), _dt(now), task_id),
                )
            else:
                connection.execute(
                    "UPDATE tasks SET metadata_json = ?, status = ?, updated_at = ? WHERE id = ?",
                    (_dump(metadata), status.value, _dt(now), task_id),
                )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        return self._row_to_task(row)

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            objective=row["objective"],
            status=TaskStatus(row["status"]),
            conversation_id=row["conversation_id"],
            metadata=_load(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class TaskSignalRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, signal: TaskSignal) -> TaskSignal:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO task_signals (id, task_id, signal, actor, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.id,
                    signal.task_id,
                    signal.signal,
                    signal.actor,
                    _dump(signal.payload),
                    _dt(signal.created_at),
                ),
            )
        return signal

    def list_for_task(self, task_id: str) -> list[TaskSignal]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_signals WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [
            TaskSignal(
                id=row["id"],
                task_id=row["task_id"],
                signal=row["signal"],
                actor=row["actor"],
                payload=_load(row["payload_json"], {}),
                created_at=row["created_at"],
            )
            for row in rows
        ]


class ApprovalRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO approvals (
                    id, task_id, capability, risk_level, summary, action_payload_json,
                    status, expires_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.id,
                    approval.task_id,
                    approval.capability.value,
                    approval.risk_level.value,
                    approval.summary,
                    _dump(approval.action_payload),
                    approval.status.value,
                    _dt(approval.expires_at),
                    _dt(approval.created_at),
                ),
            )
        return approval

    def set_status(self, approval_id: str, status: ApprovalStatus) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE approvals SET status = ? WHERE id = ?",
                (status.value, approval_id),
            )

    def decide_pending(self, approval_id: str, status: ApprovalStatus) -> bool:
        """Atomically decide a live pending approval.

        Approval UI/API races must not revive an expired request or overwrite
        an earlier decision. Every decision fails closed once the exact expiry
        timestamp has passed.
        """
        if status not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            raise ValueError("approval decisions must be approved or rejected")
        now = _dt(utc_now())
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE approvals
                SET status = ?
                WHERE id = ? AND status = ? AND expires_at > ?
                """,
                (
                    status.value,
                    approval_id,
                    ApprovalStatus.PENDING.value,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                connection.execute(
                    """
                    UPDATE approvals
                    SET status = ?
                    WHERE id = ? AND status = ? AND expires_at <= ?
                    """,
                    (
                        ApprovalStatus.EXPIRED.value,
                        approval_id,
                        ApprovalStatus.PENDING.value,
                        now,
                    ),
                )
        return cursor.rowcount == 1

    def consume_approved(self, approval_id: str) -> bool:
        """Consume one unexpired approval exactly once before dispatch."""
        now = _dt(utc_now())
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE approvals
                SET status = ?
                WHERE id = ? AND status = ? AND expires_at > ?
                """,
                (
                    ApprovalStatus.CONSUMED.value,
                    approval_id,
                    ApprovalStatus.APPROVED.value,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                connection.execute(
                    """
                    UPDATE approvals
                    SET status = ?
                    WHERE id = ? AND status = ? AND expires_at <= ?
                    """,
                    (
                        ApprovalStatus.EXPIRED.value,
                        approval_id,
                        ApprovalStatus.APPROVED.value,
                        now,
                    ),
                )
        return cursor.rowcount == 1

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        return self._row_to_approval(row) if row is not None else None

    def list_for_task(self, task_id: str) -> list[ApprovalRequest]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_approval(row) for row in rows]

    def list_pending(self, limit: int = 100) -> list[ApprovalRequest]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at ASC LIMIT ?",
                (ApprovalStatus.PENDING.value, limit),
            ).fetchall()
        return [self._row_to_approval(row) for row in rows]

    def cancel_pending_for_task(self, task_id: str) -> int:
        """Called when a task is cancelled (docs/UI_UX_AUDIT.md Phase 8) - a
        pending approval whose task is already dead must not keep sitting in
        the pending list looking actionable. Mirrors decide_pending's exact
        live-vs-already-expired split, just scoped to every pending approval
        on one task instead of a single id chosen by the caller.
        """
        now = _dt(utc_now())
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE approvals SET status = ? WHERE task_id = ? AND status = ? AND expires_at > ?",
                (ApprovalStatus.CANCELLED.value, task_id, ApprovalStatus.PENDING.value, now),
            )
            cancelled = cursor.rowcount
            connection.execute(
                "UPDATE approvals SET status = ? WHERE task_id = ? AND status = ? AND expires_at <= ?",
                (ApprovalStatus.EXPIRED.value, task_id, ApprovalStatus.PENDING.value, now),
            )
        return cancelled

    @staticmethod
    def _row_to_approval(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            id=row["id"],
            task_id=row["task_id"],
            capability=row["capability"],
            risk_level=row["risk_level"],
            summary=row["summary"],
            action_payload=_load(row["action_payload_json"], {}),
            status=ApprovalStatus(row["status"]),
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )


class ApprovalGrantRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, grant: ApprovalGrant) -> ApprovalGrant:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO approval_grants (
                    id, task_id, tool_name, capability, granted_from_approval_id,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant.id,
                    grant.task_id,
                    grant.tool_name,
                    grant.capability.value,
                    grant.granted_from_approval_id,
                    _dt(grant.created_at),
                    _dt(grant.expires_at),
                ),
            )
        return grant

    def find_matching(self, task_id: str, tool_name: str, capability: Capability) -> ApprovalGrant | None:
        now = _dt(utc_now())
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM approval_grants
                WHERE task_id = ? AND tool_name = ? AND capability = ? AND expires_at > ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (task_id, tool_name, capability.value, now),
            ).fetchone()
        return self._row_to_grant(row) if row is not None else None

    def list_for_task(self, task_id: str) -> list[ApprovalGrant]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approval_grants WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_grant(row) for row in rows]

    def expire_for_task(self, task_id: str) -> int:
        """Revokes every still-active grant for a cancelled task by expiring
        it immediately - reuses find_matching's existing expires_at > now
        check rather than adding a separate revoked column for what is,
        functionally, the same "not valid anymore" state.
        """
        now = _dt(utc_now())
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE approval_grants SET expires_at = ? WHERE task_id = ? AND expires_at > ?",
                (now, task_id, now),
            )
        return cursor.rowcount

    @staticmethod
    def _row_to_grant(row: sqlite3.Row) -> ApprovalGrant:
        return ApprovalGrant(
            id=row["id"],
            task_id=row["task_id"],
            tool_name=row["tool_name"],
            capability=row["capability"],
            granted_from_approval_id=row["granted_from_approval_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )


class ToolInvocationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, request: ToolCallRequest, status: ToolResultStatus = ToolResultStatus.NEEDS_APPROVAL) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_invocations (
                    id, task_id, tool_name, capability, request_json, result_json,
                    status, created_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.id,
                    request.task_id,
                    request.tool_name,
                    request.capability.value,
                    request.model_dump_json(),
                    None,
                    status.value,
                    _dt(request.created_at),
                    None,
                ),
            )

    def complete(self, result: ToolCallResult) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE tool_invocations
                SET result_json = ?, status = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    result.model_dump_json(),
                    result.status.value,
                    _dt(result.completed_at),
                    result.request_id,
                ),
            )

    def list_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM tool_invocations
                WHERE task_id = ?
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "tool_name": row["tool_name"],
                "capability": row["capability"],
                "request": _load(row["request_json"], {}),
                "result": _load(row["result_json"], None),
                "status": row["status"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }
            for row in rows
        ]

    def cancel_pending_for_task(self, task_id: str) -> int:
        """needs_approval is the only non-terminal status this table has -
        called when a task is cancelled so its trace doesn't show a call
        forever "awaiting" a decision that will never come.
        """
        now = _dt(utc_now())
        result = _dump({"status": "cancelled", "error_message": "Task was cancelled before this action was decided."})
        with self.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE tool_invocations SET status = ?, result_json = ?, completed_at = ? WHERE task_id = ? AND status = ?",
                (ToolResultStatus.CANCELLED.value, result, now, task_id, ToolResultStatus.NEEDS_APPROVAL.value),
            )
        return cursor.rowcount


class ArtifactRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, artifact: Artifact) -> Artifact:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, task_id, artifact_type, uri, content_preview, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.task_id,
                    artifact.type.value,
                    artifact.uri,
                    artifact.content_preview,
                    _dump(artifact.metadata),
                    _dt(artifact.created_at),
                ),
            )
        return artifact

    def link_to_task(self, artifact_id: str, task_id: str) -> None:
        """Attaches an artifact created before its task existed (a chat
        upload) to the task it was sent with - list_for_task filters by
        this same column, so an unlinked upload would never appear
        anywhere the task's own artifacts do."""
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE artifacts SET task_id = ? WHERE id = ?",
                (task_id, artifact_id),
            )

    def list_for_task(self, task_id: str) -> list[Artifact]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [
            Artifact(
                id=row["id"],
                task_id=row["task_id"],
                type=row["artifact_type"],
                uri=row["uri"],
                content_preview=row["content_preview"],
                metadata=_load(row["metadata_json"], {}),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_recent(self, limit: int = 20, artifact_type: str | None = None) -> list[Artifact]:
        query = "SELECT * FROM artifacts"
        params: list[object] = []
        if artifact_type:
            query += " WHERE artifact_type = ?"
            params.append(artifact_type)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [
            Artifact(
                id=row["id"],
                task_id=row["task_id"],
                type=row["artifact_type"],
                uri=row["uri"],
                content_preview=row["content_preview"],
                metadata=_load(row["metadata_json"], {}),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get(self, artifact_id: str) -> Artifact | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            return None
        return Artifact(
            id=row["id"],
            task_id=row["task_id"],
            type=row["artifact_type"],
            uri=row["uri"],
            content_preview=row["content_preview"],
            metadata=_load(row["metadata_json"], {}),
            created_at=row["created_at"],
        )


class MemoryFactRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, fact: MemoryFact) -> MemoryFact:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_facts (
                    id, category, content, source, confidence, task_id,
                    supersedes_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.id, fact.category, fact.content, fact.source.value, fact.confidence,
                    fact.task_id, fact.supersedes_id, _dt(fact.created_at), _dt(fact.updated_at),
                ),
            )
        return fact

    def get(self, fact_id: str) -> MemoryFact | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM memory_facts WHERE id = ?", (fact_id,)).fetchone()
        return self._row_to_fact(row) if row is not None else None

    def list_all(self, *, category: str | None = None, query: str | None = None) -> list[MemoryFact]:
        sql = "SELECT * FROM memory_facts"
        clauses: list[str] = []
        params: list[object] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if query:
            clauses.append("(content LIKE ? OR category LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self._row_to_fact(row) for row in rows]

    def update_content(self, fact_id: str, *, category: str, content: str) -> MemoryFact | None:
        now = _dt(utc_now())
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE memory_facts SET category = ?, content = ?, updated_at = ? WHERE id = ?",
                (category, content, now, fact_id),
            )
        return self.get(fact_id)

    def delete(self, fact_id: str) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM memory_facts WHERE id = ?", (fact_id,))
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_fact(row: sqlite3.Row) -> MemoryFact:
        return MemoryFact(
            id=row["id"],
            category=row["category"],
            content=row["content"],
            source=row["source"],
            confidence=row["confidence"],
            task_id=row["task_id"],
            supersedes_id=row["supersedes_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ScheduleRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, schedule: ScheduleRecord) -> ScheduleRecord:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO schedules (
                    id, source_channel, source_chat_id, objective, cadence, timezone, status,
                    next_run_at, last_run_at, last_task_id, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule.id,
                    schedule.source_channel.value,
                    schedule.source_chat_id,
                    schedule.objective,
                    schedule.cadence,
                    schedule.timezone,
                    schedule.status.value,
                    _dt(schedule.next_run_at),
                    _dt(schedule.last_run_at) if schedule.last_run_at else None,
                    schedule.last_task_id,
                    _dump(schedule.metadata),
                    _dt(schedule.created_at),
                    _dt(schedule.updated_at),
                ),
            )
        return schedule

    def get(self, schedule_id: str) -> ScheduleRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        return self._row_to_schedule(row) if row else None

    def list_recent(self, limit: int = 50) -> list[ScheduleRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM schedules ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_schedule(row) for row in rows]

    def list_due(self, now: datetime, limit: int = 20) -> list[ScheduleRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM schedules
                WHERE status = ? AND next_run_at <= ?
                ORDER BY next_run_at ASC
                LIMIT ?
                """,
                (ScheduleStatus.ENABLED.value, _dt(now), limit),
            ).fetchall()
        return [self._row_to_schedule(row) for row in rows]

    def update_status(self, schedule_id: str, status: ScheduleStatus) -> ScheduleRecord:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE schedules SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _dt(now), schedule_id),
            )
            row = connection.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        if row is None:
            raise KeyError(f"schedule not found: {schedule_id}")
        return self._row_to_schedule(row)

    def update_metadata(self, schedule_id: str, metadata: dict[str, Any]) -> ScheduleRecord:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE schedules SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (_dump(metadata), _dt(now), schedule_id),
            )
            row = connection.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        if row is None:
            raise KeyError(f"schedule not found: {schedule_id}")
        return self._row_to_schedule(row)

    def mark_run(self, schedule_id: str, task_id: str, last_run_at: datetime, next_run_at: datetime) -> ScheduleRecord:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE schedules
                SET last_run_at = ?, last_task_id = ?, next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (_dt(last_run_at), task_id, _dt(next_run_at), _dt(now), schedule_id),
            )
            row = connection.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        if row is None:
            raise KeyError(f"schedule not found: {schedule_id}")
        return self._row_to_schedule(row)

    def count(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM schedules").fetchone()
        return int(row[0] if row else 0)

    @staticmethod
    def _row_to_schedule(row: sqlite3.Row) -> ScheduleRecord:
        return ScheduleRecord(
            id=row["id"],
            source_channel=ChannelType(row["source_channel"]),
            source_chat_id=row["source_chat_id"],
            objective=row["objective"],
            cadence=row["cadence"],
            timezone=row["timezone"],
            status=ScheduleStatus(row["status"]),
            next_run_at=row["next_run_at"],
            last_run_at=row["last_run_at"],
            last_task_id=row["last_task_id"],
            metadata=_load(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class AuditRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def append(self, event: AuditEvent) -> AuditEvent:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    id, event_type, actor, task_id, correlation_id, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.type.value,
                    event.actor,
                    event.task_id,
                    event.correlation_id,
                    _dump(event.payload),
                    _dt(event.created_at),
                ),
            )
        return event

    def list_for_task(self, task_id: str) -> list[AuditEvent]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_by_correlation_id(self, correlation_id: str) -> list[AuditEvent]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE correlation_id = ? ORDER BY created_at ASC",
                (correlation_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_matching_payload_value(self, key: str, value: str, limit: int = 50) -> list[AuditEvent]:
        pattern = f'%"{key}": "{value}"%'
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM audit_events
                WHERE payload_json LIKE ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (pattern, limit),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_recent(self, limit: int = 50) -> list[AuditEvent]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def clear_all(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()
            count = int(row[0] if row else 0)
            connection.execute("DELETE FROM audit_events")
        return count

    def list_by_type(self, event_type: AuditEventType) -> list[AuditEvent]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE event_type = ? ORDER BY created_at ASC",
                (event_type.value,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            id=row["id"],
            type=AuditEventType(row["event_type"]),
            actor=row["actor"],
            task_id=row["task_id"],
            correlation_id=row["correlation_id"],
            payload=_load(row["payload_json"], {}),
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class Repositories:
    conversations: ConversationRepository
    conversation_memory: ConversationMemoryRepository
    messages: MessageRepository
    tasks: TaskRepository
    task_signals: TaskSignalRepository
    approvals: ApprovalRepository
    approval_grants: ApprovalGrantRepository
    tool_invocations: ToolInvocationRepository
    artifacts: ArtifactRepository
    memory_facts: MemoryFactRepository
    schedules: ScheduleRepository
    audit: AuditRepository

    @classmethod
    def for_database(cls, database: Database) -> "Repositories":
        return cls(
            conversations=ConversationRepository(database),
            conversation_memory=ConversationMemoryRepository(database),
            messages=MessageRepository(database),
            tasks=TaskRepository(database),
            task_signals=TaskSignalRepository(database),
            approvals=ApprovalRepository(database),
            approval_grants=ApprovalGrantRepository(database),
            tool_invocations=ToolInvocationRepository(database),
            artifacts=ArtifactRepository(database),
            memory_facts=MemoryFactRepository(database),
            schedules=ScheduleRepository(database),
            audit=AuditRepository(database),
        )
