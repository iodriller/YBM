from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_control.schemas import AuditEvent, AuditEventType, FormattedAuditEvent


CATEGORY_BY_TYPE = {
    AuditEventType.MESSAGE_RECEIVED: "raw_telegram",
    AuditEventType.MESSAGE_SENT: "raw_telegram",
    AuditEventType.TELEGRAM_ACCESS_DECISION: "telegram_access",
    AuditEventType.MESSAGE_CLASSIFIED: "classification",
    AuditEventType.TASK_SPAWN_FAILED: "failed_classification",
    AuditEventType.TASK_CREATED: "spawned_task",
    AuditEventType.POLICY_DECISION: "policy",
    AuditEventType.CONFIG_UPDATED: "config",
    AuditEventType.TOOL_REQUESTED: "tool",
    AuditEventType.TOOL_COMPLETED: "tool",
    AuditEventType.APPROVAL_REQUESTED: "approval",
    AuditEventType.APPROVAL_DECIDED: "approval",
    AuditEventType.ERROR: "error",
    # Added docs/UI_UX_AUDIT.md Phase 14 - these four previously fell back
    # to the generic "system" category, which was a real gap for
    # TASK_STATE_CHANGED specifically: it's the single most common event
    # type in any task's timeline (fires on every status transition), so
    # lumping it under "system" left almost every timeline row looking
    # like miscellaneous noise instead of the actual state machine.
    AuditEventType.TASK_STATE_CHANGED: "task_state",
    AuditEventType.TASK_CANCELLED: "task_state",
    AuditEventType.ARTIFACT_CREATED: "artifact",
    AuditEventType.EGRESS_CONTACTED: "egress",
}


def format_audit_event(event: AuditEvent) -> FormattedAuditEvent:
    payload = event.payload
    category = CATEGORY_BY_TYPE.get(event.type, "system")
    title = _title(event.type)
    summary = _summary(event.type, payload)
    decision = _decision(event.type, payload)
    reason = _reason(payload)
    task_type = _task_type(payload)
    source = _source(event, payload)
    return FormattedAuditEvent(
        id=event.id,
        type=event.type,
        category=category,
        formatted_time=_format_time(event.created_at),
        actor=event.actor,
        task_id=event.task_id,
        title=title,
        summary=summary,
        decision=decision,
        reason=reason,
        task_type=task_type,
        source=source,
        details=payload,
    )


def _title(event_type: AuditEventType) -> str:
    return {
        AuditEventType.MESSAGE_RECEIVED: "Telegram message received",
        AuditEventType.MESSAGE_SENT: "Telegram message sent",
        AuditEventType.TELEGRAM_ACCESS_DECISION: "Telegram access decision",
        AuditEventType.MESSAGE_CLASSIFIED: "Message classified",
        AuditEventType.TASK_SPAWN_FAILED: "Task not spawned",
        AuditEventType.TASK_CREATED: "Task spawned",
        AuditEventType.POLICY_DECISION: "Policy decision",
        AuditEventType.CONFIG_UPDATED: "Configuration updated",
        AuditEventType.TOOL_REQUESTED: "Tool requested",
        AuditEventType.TOOL_COMPLETED: "Tool completed",
        AuditEventType.APPROVAL_REQUESTED: "Approval requested",
        AuditEventType.APPROVAL_DECIDED: "Approval decided",
        AuditEventType.ERROR: "Error",
    }.get(event_type, event_type.value.replace("_", " ").title())


def _summary(event_type: AuditEventType, payload: dict[str, Any]) -> str:
    if event_type == AuditEventType.MESSAGE_RECEIVED:
        return _preview(payload.get("text") or payload.get("text_preview") or "Message received")
    if event_type == AuditEventType.MESSAGE_SENT:
        return _preview(payload.get("text") or payload.get("caption") or payload.get("kind") or "Message sent")
    if event_type == AuditEventType.TELEGRAM_ACCESS_DECISION:
        return f"{'Allowed' if payload.get('allowed') else 'Denied'} Telegram message"
    if event_type == AuditEventType.MESSAGE_CLASSIFIED:
        return _preview(payload.get("normalized_objective") or payload.get("text") or payload.get("reason") or "Classified message")
    if event_type == AuditEventType.TASK_SPAWN_FAILED:
        return _preview(payload.get("reason") or payload.get("error") or "Task was not created")
    if event_type == AuditEventType.TASK_CREATED:
        return _preview(payload.get("objective") or "Task created")
    if event_type == AuditEventType.POLICY_DECISION:
        return f"{'Allowed' if payload.get('allowed') else 'Denied'} {payload.get('capability', 'capability')}"
    if event_type == AuditEventType.CONFIG_UPDATED:
        return f"Updated {payload.get('section', 'configuration')}"
    if event_type == AuditEventType.TOOL_COMPLETED:
        return str(payload.get("status") or "Tool completed")
    return _preview(payload.get("summary") or payload.get("reason") or payload.get("error") or event_type.value)


def _decision(event_type: AuditEventType, payload: dict[str, Any]) -> str | None:
    if "allowed" in payload:
        return "allowed" if payload.get("allowed") else "denied"
    if event_type == AuditEventType.MESSAGE_CLASSIFIED:
        return "task" if payload.get("is_task") else "not_task"
    if event_type == AuditEventType.TASK_CREATED:
        return "spawned"
    if event_type == AuditEventType.TASK_SPAWN_FAILED:
        return "not_spawned"
    if "decision" in payload:
        return str(payload["decision"])
    if "status" in payload:
        return str(payload["status"])
    return None


def _reason(payload: dict[str, Any]) -> str | None:
    value = payload.get("reason") or payload.get("error") or payload.get("error_message")
    return str(value) if value is not None else None


def _task_type(payload: dict[str, Any]) -> str | None:
    value = payload.get("task_type")
    return str(value) if value is not None else None


def _source(event: AuditEvent, payload: dict[str, Any]) -> str | None:
    if event.actor.startswith("telegram"):
        return "telegram"
    value = payload.get("source") or payload.get("channel")
    return str(value) if value is not None else None


def _format_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def _preview(value: Any, limit: int = 180) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."
