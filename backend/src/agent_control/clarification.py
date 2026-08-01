"""Shared "resume a clarifying task" logic, used by every inbound channel.

Extracted from channels/telegram.py's _resume_clarifying_task (the only
channel that had it) so admin.py's web chat endpoint can share the exact
same behavior instead of admin_send_chat_message's prior behavior of always
creating a brand-new, unrelated task while the real one sat stuck in
CLARIFYING forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agent_control.schemas import AuditEventType, TaskRecord, TaskStatus
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories

CANCEL_WORDS = {"cancel", "stop", "drop it", "never mind", "nevermind", "forget it", "no"}


@dataclass
class ClarificationResumeResult:
    task: TaskRecord
    cancelled: bool


def find_clarifying_task(
    repositories: Repositories, *, conversation_id: str | None, chat_id: str | None
) -> TaskRecord | None:
    return next(
        (
            candidate
            for candidate in repositories.tasks.list_by_statuses([TaskStatus.CLARIFYING], limit=20)
            if (conversation_id is not None and candidate.conversation_id == conversation_id)
            or (chat_id is not None and str(candidate.metadata.get("source_chat_id")) == str(chat_id))
        ),
        None,
    )


def resume_clarifying_task(
    repositories: Repositories,
    audit: AuditLogger,
    task: TaskRecord,
    *,
    text: str,
    actor: str,
    message_id: str,
    received_at: datetime,
    correlation_id: str | None = None,
) -> ClarificationResumeResult:
    """Route a reply to the task waiting on a question instead of spawning a new task."""
    if text.lower() in CANCEL_WORDS:
        repositories.tasks.update_status(task.id, TaskStatus.CANCELLED)
        audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor=actor,
            task_id=task.id,
            correlation_id=correlation_id,
            payload={"reason": "clarification_cancelled", "status": TaskStatus.CANCELLED.value},
        )
        updated = repositories.tasks.get(task.id)
        return ClarificationResumeResult(task=updated or task, cancelled=True)

    # Fold the answer into the objective so replanning sees it, and reset
    # the attempt counters - the user's input makes this a fresh attempt.
    objective = f"{task.objective}\n[User clarification: {text}]"
    repositories.tasks.update_objective(task.id, objective)
    answers = list(task.metadata.get("clarification_answers") or [])
    answers.append(
        {
            "question": task.metadata.get("clarifying_question"),
            "answer": text,
            "message_id": message_id,
            "created_at": received_at.isoformat(),
        }
    )
    metadata = {
        **task.metadata,
        "clarification_answer": text,
        "clarification_answers": answers,
        "answered_clarifying_question": task.metadata.get("clarifying_question"),
        "retry_count": 0,
        "replan_count": 0,
        "evaluator_repair_count": 0,
        "fulfillment_retry_count": 0,
    }
    metadata.pop("clarifying_question", None)
    updated = repositories.tasks.update_metadata(task.id, metadata, TaskStatus.RECEIVED)
    audit.append(
        AuditEventType.TASK_STATE_CHANGED,
        actor=actor,
        task_id=task.id,
        correlation_id=correlation_id,
        payload={"reason": "clarification_answered", "answer": text[:400], "status": TaskStatus.RECEIVED.value},
    )
    return ClarificationResumeResult(task=updated, cancelled=False)
