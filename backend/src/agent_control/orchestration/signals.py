from __future__ import annotations

from agent_control.schemas import TaskSignal, TaskStatus
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories

TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}


def apply_task_signal(
    repositories: Repositories,
    audit: AuditLogger,
    task_id: str,
    action: str,
    actor: str,
    payload: dict,
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
    else:
        raise ValueError(f"unsupported task signal: {action}")

    signal = TaskSignal(task_id=task_id, signal=action, actor=actor, payload=payload)
    repositories.task_signals.create(signal)
    audit.task_state_changed(actor=actor, task_id=task_id, old_status=task.status, new_status=updated.status)
    return signal, task.status, updated.status


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
