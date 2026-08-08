"""Let the operator talk to work that is already running.

Until now a task in flight was deaf. The only things that reached it were
`pause`, `resume` and `cancel`; anything else you typed was either answered as
chat or spawned a *second* task. The one channel inward - `resume_clarifying_reply` -
opens only while the task sits in CLARIFYING, which is to say only when the
agent asked you a question first. You could answer. You could never begin a
sentence.

So "actually make it five, not three" meant cancelling and starting over,
throwing away everything the run had already done.

A note lands in `operator_history` like any other entry, and the Operator sees
it on its next step alongside its own tool results. It is not a command and
not a new objective: the loop weighs it in context, which is the same way it
handles every other piece of evidence. Prefixed with `_` so the existing
"skip bookkeeping entries" filters in task_notify and elsewhere already ignore
it when summarizing tool attempts.
"""

from __future__ import annotations

from agent_control.schemas import AuditEventType, TaskRecord, TaskStatus
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories


NOTE_ENTRY_NAME = "_user_note"

# A task in any of these is still doing something a note could usefully change.
# CLARIFYING is deliberately absent: that has its own resume path which feeds
# the answer back to the specific question that was asked.
STEERABLE_STATUSES = [
    TaskStatus.RECEIVED,
    TaskStatus.INTERPRETING,
    TaskStatus.PLANNED,
    TaskStatus.RUNNING,
    TaskStatus.RETRYING,
    TaskStatus.AWAITING_APPROVAL,
    TaskStatus.AWAITING_EXTERNAL,
]


def find_steerable_task(
    repositories: Repositories, *, conversation_id: str | None, chat_id: str | None
) -> TaskRecord | None:
    """The most recent in-flight task for this conversation, if any.

    Newest wins: if two are somehow live, the operator almost certainly means
    the one they just watched start.
    """
    candidates = [
        task
        for task in repositories.tasks.list_by_statuses(STEERABLE_STATUSES, limit=20)
        if (conversation_id and task.conversation_id == conversation_id)
        or (chat_id and str(task.metadata.get("source_chat_id") or "") == str(chat_id))
    ]
    return max(candidates, key=lambda task: task.created_at) if candidates else None


def attach_note(
    repositories: Repositories,
    audit: AuditLogger,
    task: TaskRecord,
    *,
    text: str,
    actor: str,
) -> TaskRecord:
    """Append the operator's words to the running task's history."""
    note = (text or "").strip()
    if not note:
        return task
    history = list(task.metadata.get("operator_history") or [])
    history.append({
        "tool_name": NOTE_ENTRY_NAME,
        "input": None,
        "status": "user_note",
        "output_summary": note[:2000],
        "error": None,
    })
    metadata = {**task.metadata, "operator_history": history}
    # Counted so a reader of the trace can see the task was steered, without
    # having to scan history for note entries.
    metadata["user_note_count"] = int(task.metadata.get("user_note_count", 0)) + 1
    updated = repositories.tasks.update_metadata(task.id, metadata)
    audit.append(
        AuditEventType.TASK_STATE_CHANGED,
        actor=actor,
        task_id=task.id,
        payload={"action": "user_note", "note": note[:500], "status": task.status.value},
    )
    return updated


def acknowledgement(task: TaskRecord) -> str:
    """What to say back. Names the task being steered so a note that lands on
    the wrong one is obvious immediately, rather than at the end."""
    return (
        f"Noted - I'll factor that into the task I'm working on now "
        f"(\"{task.objective[:80]}\"). Say \"cancel\" if you'd rather I stop it."
    )
