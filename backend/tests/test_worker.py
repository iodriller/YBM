from __future__ import annotations

import pytest

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration import StaticToolAdapter, TaskWorker, ToolExecutor, reconcile_orphaned_tasks
from agent_control.policy import PolicyEngine
from agent_control.schemas import (
    AuditEventType,
    Capability,
    OperatorAction,
    OperatorDecision,
    RiskLevel,
    TaskStatus,
)
from agent_control.storage import AuditLogger, Database, Repositories


def _repos(tmp_path) -> tuple[Repositories, AuditLogger]:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    return repos, AuditLogger(repos.audit)


class QueueOperator:
    """Fake OperatorLoopService - returns decisions in order, one per decide() call."""

    def __init__(self, decisions: list[OperatorDecision]) -> None:
        self.decisions = list(decisions)

    async def decide(self, objective, config_context, history, *, memory_context=""):
        return self.decisions.pop(0)


class RecordingNotifier:
    def __init__(self) -> None:
        self.tasks = []

    async def notify(self, task) -> None:
        self.tasks.append(task)


@pytest.mark.asyncio
async def test_worker_notifies_once_on_completion(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run safe step", metadata={"source_chat_id": "100"})
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.LLM_GENERATE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.LOW,
            )
        },
    )
    notifier = RecordingNotifier()
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": StaticToolAdapter({"text": "done"})},
    )
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="done"),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, notification_sink=notifier)

    await worker.process_next()
    await worker.process_next()

    assert [item.status for item in notifier.tasks] == [TaskStatus.RUNNING, TaskStatus.COMPLETED]
    assert repos.tasks.get(task.id).metadata["notified_statuses"] == [
        TaskStatus.COMPLETED.value,
        TaskStatus.RUNNING.value,
    ]


def test_reconcile_orphaned_tasks_fails_running_and_interpreting_tasks(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    running = repos.tasks.create("was mid-execution when the worker died")
    repos.tasks.update_status(running.id, TaskStatus.RUNNING)
    interpreting = repos.tasks.create("was mid-planning when the worker died")
    repos.tasks.update_status(interpreting.id, TaskStatus.INTERPRETING)
    # Untouched statuses should survive reconciliation unchanged.
    received = repos.tasks.create("freshly queued, no worker has touched it yet")
    clarifying = repos.tasks.create("waiting on the user, not a dead worker")
    repos.tasks.update_status(clarifying.id, TaskStatus.CLARIFYING)

    count = reconcile_orphaned_tasks(repos, audit)

    assert count == 2
    assert repos.tasks.get(running.id).status == TaskStatus.FAILED
    assert repos.tasks.get(interpreting.id).status == TaskStatus.FAILED
    assert "failed explicitly rather than silently resumed" in repos.tasks.get(running.id).metadata["last_worker_error"]
    assert repos.tasks.get(received.id).status == TaskStatus.RECEIVED
    assert repos.tasks.get(clarifying.id).status == TaskStatus.CLARIFYING


def test_reconcile_orphaned_tasks_releases_claim_and_writes_audit_trail(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("claimed by a worker that then crashed")
    repos.tasks.update_status(task.id, TaskStatus.RUNNING)
    repos.tasks.claim_next([TaskStatus.RUNNING], worker_id="worker-that-died", claim_expiry_seconds=1200)

    reconcile_orphaned_tasks(repos, audit)

    reloaded = repos.tasks.get(task.id)
    assert reloaded.status == TaskStatus.FAILED
    events = repos.audit.list_for_task(task.id)
    assert any(event.type == AuditEventType.ERROR for event in events)
    assert any(event.type == AuditEventType.TASK_STATE_CHANGED for event in events)
    with repos.tasks.database.connect() as connection:
        row = connection.execute(
            "SELECT claimed_by, claim_expires_at FROM tasks WHERE id = ?", (task.id,)
        ).fetchone()
    assert row["claimed_by"] is None
    assert row["claim_expires_at"] is None


def test_reconcile_orphaned_tasks_returns_zero_when_nothing_to_do(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    repos.tasks.create("normal queued task")

    assert reconcile_orphaned_tasks(repos, audit) == 0
