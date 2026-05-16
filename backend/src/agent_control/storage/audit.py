from __future__ import annotations

from typing import Any

from agent_control.schemas import AuditEvent, AuditEventType, TaskStatus
from agent_control.storage.redaction import redact_payload
from agent_control.storage.repositories import AuditRepository


class AuditLogger:
    def __init__(self, repository: AuditRepository, redact_patterns: list[str] | None = None) -> None:
        self.repository = repository
        self.redact_patterns = redact_patterns

    def append(
        self,
        event_type: AuditEventType,
        actor: str,
        payload: dict[str, Any] | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            type=event_type,
            actor=actor,
            task_id=task_id,
            correlation_id=correlation_id,
            payload=redact_payload(payload or {}, self.redact_patterns or None),
        )
        return self.repository.append(event)

    def task_state_changed(
        self,
        actor: str,
        task_id: str,
        old_status: TaskStatus,
        new_status: TaskStatus,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        return self.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor=actor,
            task_id=task_id,
            correlation_id=correlation_id,
            payload={"old_status": old_status.value, "new_status": new_status.value},
        )
