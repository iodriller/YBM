from __future__ import annotations

import logging

from agent_control.config import AppSettings
from agent_control.schemas import AuditEventType, TaskSignal, TaskStatus
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}


def apply_task_signal(
    repositories: Repositories,
    audit: AuditLogger,
    task_id: str,
    action: str,
    actor: str,
    payload: dict,
    settings: AppSettings | None = None,
) -> tuple[TaskSignal, TaskStatus, TaskStatus]:
    task = repositories.tasks.get(task_id)
    if task is None:
        raise KeyError(f"task not found: {task_id}")

    if action == "pause":
        if task.status in TERMINAL_STATUSES:
            raise ValueError(f"cannot pause task in terminal status {task.status.value}")
        metadata = {
            **task.metadata,
            "paused_from_status": task.status.value,
            "last_control_signal": action,
        }
        updated = repositories.tasks.update_metadata(task_id, metadata, TaskStatus.PAUSED)
    elif action == "resume":
        if task.status in TERMINAL_STATUSES:
            raise ValueError(f"cannot resume task in terminal status {task.status.value}")
        target = _resume_target(task.status, task.metadata.get("paused_from_status"))
        metadata = {**task.metadata, "last_control_signal": action}
        metadata.pop("paused_from_status", None)
        updated = repositories.tasks.update_metadata(task_id, metadata, target)
    elif action == "cancel":
        if task.status == TaskStatus.CANCELLED:
            updated = task
        elif task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            raise ValueError(f"cannot cancel task in terminal status {task.status.value}")
        else:
            metadata = {
                **task.metadata,
                "cancelled_from_status": task.status.value,
                "last_control_signal": action,
            }
            updated = repositories.tasks.update_metadata(task_id, metadata, TaskStatus.CANCELLED)
            _cleanup_cancelled_task(repositories, audit, task, settings)
    else:
        raise ValueError(f"unsupported task signal: {action}")

    signal = TaskSignal(task_id=task_id, signal=action, actor=actor, payload=payload)
    repositories.task_signals.create(signal)
    audit.task_state_changed(actor=actor, task_id=task_id, old_status=task.status, new_status=updated.status)
    return signal, task.status, updated.status


def _cleanup_cancelled_task(
    repositories: Repositories, audit: AuditLogger, task, settings: AppSettings | None
) -> None:
    """Everything a cancelled task leaves behind that must not keep looking
    live (docs/UI_UX_AUDIT.md Phase 8 - "cancellation can leave stale
    approvals and block the worker"). Cancelling a task previously only
    flipped its status: the pending approval stayed pending forever (still
    shown as actionable in the console), any task-scoped grant stayed valid
    until its own natural expiry, and a stuck-at-needs_approval tool
    invocation stayed stuck in the trace. None of this failing should stop
    the cancel itself from taking effect - each step is independent and
    best-effort.
    """
    try:
        cancelled_approvals = repositories.approvals.cancel_pending_for_task(task.id)
    except Exception:
        logger.exception("failed to cancel pending approvals for task %s", task.id)
        cancelled_approvals = 0
    try:
        revoked_grants = repositories.approval_grants.expire_for_task(task.id)
    except Exception:
        logger.exception("failed to revoke approval grants for task %s", task.id)
        revoked_grants = 0
    try:
        cancelled_invocations = repositories.tool_invocations.cancel_pending_for_task(task.id)
    except Exception:
        logger.exception("failed to cancel pending tool invocations for task %s", task.id)
        cancelled_invocations = 0

    if cancelled_approvals or revoked_grants or cancelled_invocations:
        audit.append(
            AuditEventType.TASK_CANCELLED,
            actor="worker",
            task_id=task.id,
            payload={
                "action": "cancellation_cleanup",
                "cancelled_approvals": cancelled_approvals,
                "revoked_grants": revoked_grants,
                "cancelled_invocations": cancelled_invocations,
            },
        )

    if task.status == TaskStatus.AWAITING_EXTERNAL and settings is not None:
        _stop_awaiting_external_session(task, settings, audit)


def _stop_awaiting_external_session(task, settings: AppSettings, audit: AuditLogger) -> None:
    awaiting = task.metadata.get("awaiting_external") if isinstance(task.metadata, dict) else None
    session_id = awaiting.get("session_id") if isinstance(awaiting, dict) else None
    if not session_id:
        return
    try:
        from agent_control.tools.coding_agent import load_session, stop_session_process

        session = load_session(settings.adapters.coding_agent.session_root, str(session_id))
        stopped = stop_session_process(session) if session else False
    except Exception:
        logger.exception("failed to stop coding session %s for cancelled task %s", session_id, task.id)
        stopped = False
    audit.append(
        AuditEventType.TASK_CANCELLED,
        actor="worker",
        task_id=task.id,
        payload={"action": "cancellation_stop_external_session", "session_id": session_id, "stopped": stopped},
    )


def requeue_after_approval_decision(repositories: Repositories, task_id: str) -> None:
    """Makes an AWAITING_APPROVAL task workable again once its approval has
    been decided (docs/UI_UX_AUDIT.md Phase 8, second pass).

    worker.py's WORKABLE_STATUSES deliberately excludes AWAITING_APPROVAL
    now - claim_next never re-selects a task sitting in it, the same way it
    already never re-selected AWAITING_EXTERNAL - so a pending approval no
    longer monopolizes the single worker's claim while a human decides.
    This is the other half of that fix: without it, a decided approval
    would never be revisited, since nothing would flip its status back to
    something claimable. Flipping to RUNNING here means the worker's very
    next poll picks it up - _process_operator_awaiting_approval already
    knows how to resume from metadata["operator_pending_call"] regardless
    of which status the task was in when it got there.

    Every caller (admin API, Telegram inline buttons, Telegram plain-text
    "approve") should call this right after a `decide_pending` that
    actually changed a PENDING approval to APPROVED or REJECTED - not
    speculatively, and not on a no-op decide (already decided, expired).
    Best-effort and idempotent: a no-op if the task isn't currently
    AWAITING_APPROVAL (already resumed, or this is a race between two
    decision paths for the same approval).
    """
    task = repositories.tasks.get(task_id)
    if task is None or task.status != TaskStatus.AWAITING_APPROVAL:
        return
    repositories.tasks.update_metadata(task_id, task.metadata, TaskStatus.RUNNING)


def _resume_target(current_status: TaskStatus, paused_from_status: str | None) -> TaskStatus:
    if current_status != TaskStatus.PAUSED:
        return current_status
    if paused_from_status:
        try:
            target = TaskStatus(paused_from_status)
        except ValueError:
            return TaskStatus.RECEIVED
        if target not in TERMINAL_STATUSES and target != TaskStatus.PAUSED:
            return target
    return TaskStatus.RECEIVED
