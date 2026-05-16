from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import sqlite3
from typing import Any

from agent_control.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    Artifact,
    AuditEvent,
    AuditEventType,
    ChannelType,
    InboundMessage,
    PlanModel,
    TaskRecord,
    TaskSignal,
    TaskStatus,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
    utc_now,
)
from agent_control.storage.database import Database


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
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (
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
        return message


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
                    id, objective, status, conversation_id, plan_id, current_step_id,
                    metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.objective,
                    task.status.value,
                    task.conversation_id,
                    task.plan_id,
                    task.current_step_id,
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

    def list_recent(self, limit: int = 20) -> list[TaskRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

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

    def attach_plan(self, task_id: str, plan_id: str, status: TaskStatus = TaskStatus.PLANNED) -> TaskRecord:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE tasks SET plan_id = ?, status = ?, updated_at = ? WHERE id = ?",
                (plan_id, status.value, _dt(now), task_id),
            )
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        return self._row_to_task(row)

    def set_current_step(self, task_id: str, step_id: str | None) -> TaskRecord:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE tasks SET current_step_id = ?, updated_at = ? WHERE id = ?",
                (step_id, _dt(now), task_id),
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
            plan_id=row["plan_id"],
            current_step_id=row["current_step_id"],
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


class PlanRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, task_id: str, plan: PlanModel) -> PlanModel:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO plans (id, task_id, objective, plan_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    plan.id,
                    task_id,
                    plan.objective,
                    plan.model_dump_json(),
                    _dt(utc_now()),
                ),
            )
        return plan

    def get(self, plan_id: str) -> PlanModel | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT plan_json FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            return None
        return PlanModel.model_validate_json(row["plan_json"])


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

    def list_for_task(self, task_id: str) -> list[ApprovalRequest]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_approval(row) for row in rows]

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
    messages: MessageRepository
    tasks: TaskRepository
    task_signals: TaskSignalRepository
    plans: PlanRepository
    approvals: ApprovalRepository
    tool_invocations: ToolInvocationRepository
    artifacts: ArtifactRepository
    audit: AuditRepository

    @classmethod
    def for_database(cls, database: Database) -> "Repositories":
        return cls(
            conversations=ConversationRepository(database),
            messages=MessageRepository(database),
            tasks=TaskRepository(database),
            task_signals=TaskSignalRepository(database),
            plans=PlanRepository(database),
            approvals=ApprovalRepository(database),
            tool_invocations=ToolInvocationRepository(database),
            artifacts=ArtifactRepository(database),
            audit=AuditRepository(database),
        )
