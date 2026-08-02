"""Unit tests for the Operator loop path in orchestration/worker.py - the
sole execution path (docs/HISTORY.md P3 §2.2). Uses the same
StaticToolAdapter/PolicyEngine harness as test_worker.py, but drives
TaskWorker with a scripted decision sequence instead of a persisted plan.
See orchestration/operator.py's module docstring for the design.
"""

from __future__ import annotations

import pytest

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration import StaticToolAdapter, TaskWorker, ToolExecutor
from agent_control.orchestration.auditor import AuditResult
from agent_control.policy import PolicyEngine
from agent_control.recovery import RetryPolicy
from agent_control.schemas import (
    ApprovalStatus,
    Capability,
    ErrorClass,
    OperatorAction,
    OperatorDecision,
    RiskLevel,
    TaskStatus,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
)
from agent_control.tools.registry import ToolDefinition
from helpers import make_repos


class RateLimitedAdapter:
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.RATE_LIMITED,
            error_class=ErrorClass.RATE_LIMITED,
            error_message="rate limited, try later",
        )


class UsageLimitedAdapter:
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.RATE_LIMITED,
            error_class=ErrorClass.USAGE_LIMITED,
            error_message="quota exhausted",
        )


class FailsOnceThenSucceedsAdapter:
    """Rate-limited on the first call, succeeds on every call after - models
    a real backoff-then-retry-and-succeed sequence."""

    def __init__(self, output: dict | None = None) -> None:
        self.output = output or {"ok": True}
        self.calls = 0

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        self.calls += 1
        if self.calls == 1:
            return ToolCallResult(
                request_id=request.id,
                status=ToolResultStatus.RATE_LIMITED,
                error_class=ErrorClass.RATE_LIMITED,
                error_message="rate limited, try later",
            )
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=self.output)


class BackgroundSessionAdapter:
    """Mimics coding.agent starting a session that finishes asynchronously -
    the initial call returns immediately with status=running."""

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.SUCCEEDED,
            output={"status": "running", "session_id": "sess-1", "provider": "codex"},
        )




class QueueOperator:
    """Fake OperatorLoopService - returns decisions in order, one per decide() call.

    `usages`, if given, is a same-length list of usage dicts (or None) - one
    per decide() call, mirroring how the real OperatorLoopService sets
    self.last_usage after each call (docs/HISTORY.md Part 4 T1.4).
    """

    def __init__(self, decisions: list[OperatorDecision], usages: list[dict | None] | None = None) -> None:
        self.decisions = list(decisions)
        self._usages = list(usages) if usages is not None else None
        self.calls = 0
        self.last_usage: dict | None = None
        # docs/HISTORY.md Part 4 T2.6: one entry per decide() call, so tests
        # can assert exactly when the caller asked for the stronger model.
        self.prefer_major_calls: list[bool] = []

    async def decide(self, objective, config_context, history, *, memory_context="", prefer_major=False):
        self.calls += 1
        self.prefer_major_calls.append(prefer_major)
        if self._usages is not None:
            self.last_usage = self._usages.pop(0)
        return self.decisions.pop(0)


class QueueAuditor:
    """Fake AuditorService - returns results in order, one per audit() call."""

    def __init__(self, results: list[AuditResult], usages: list[dict | None] | None = None) -> None:
        self.results = list(results)
        self._usages = list(usages) if usages is not None else None
        self.calls: list[tuple[str, str]] = []
        self.last_usage: dict | None = None

    async def audit(self, objective, raw_output, *, original_message=None):
        self.calls.append((objective, raw_output))
        if self._usages is not None:
            self.last_usage = self._usages.pop(0)
        return self.results.pop(0)


def _executor(settings, audit, repos, *, tool_name="llm", output=None) -> ToolExecutor:
    return ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={tool_name: StaticToolAdapter(output)},
        tool_definitions={
            tool_name: ToolDefinition(
                name=tool_name, capability=Capability.LLM_GENERATE, enabled=True, description="test tool",
            )
        },
    )


def _settings() -> AppSettings:
    return AppSettings(
        _env_file=None,
        capabilities={
            Capability.LLM_GENERATE: CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.LOW)
        },
    )


def _executor_with_adapter(settings, audit, repos, adapter, *, tool_name="llm") -> ToolExecutor:
    return ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={tool_name: adapter},
        tool_definitions={
            tool_name: ToolDefinition(
                name=tool_name, capability=Capability.LLM_GENERATE, enabled=True, description="test tool",
            )
        },
    )


@pytest.mark.asyncio
async def test_operator_loop_calls_tool_then_completes(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Answer a question")
    settings = _settings()
    executor = _executor(settings, audit, repos, output={"answer": "42"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="The answer is 42."),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    running = await worker.process_task(task.id)
    completed = await worker.process_task(running.id)

    assert running.status == TaskStatus.RUNNING
    assert running.metadata["operator_loop"] is True
    assert len(running.metadata["operator_history"]) == 1
    assert running.metadata["operator_history"][0]["tool_name"] == "llm"
    assert running.metadata["operator_history"][0]["status"] == "succeeded"
    assert completed.status == TaskStatus.COMPLETED
    assert completed.metadata["synthesized_answer"] == "The answer is 42."


@pytest.mark.asyncio
async def test_operator_loop_done_on_first_step_needs_no_tool(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("trivial question")
    settings = _settings()
    executor = _executor(settings, audit, repos)
    operator = QueueOperator([OperatorDecision(action=OperatorAction.DONE, final_answer="answer")])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    completed = await worker.process_task(task.id)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.metadata["synthesized_answer"] == "answer"


@pytest.mark.asyncio
async def test_operator_loop_ask_user_transitions_to_clarifying(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("ambiguous request")
    settings = _settings()
    executor = _executor(settings, audit, repos)
    operator = QueueOperator([OperatorDecision(action=OperatorAction.ASK_USER, question="Which folder?")])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.CLARIFYING
    assert result.metadata["clarifying_question"] == "Which folder?"


@pytest.mark.asyncio
async def test_operator_loop_blocked_action_transitions_to_blocked(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("impossible request")
    settings = _settings()
    executor = _executor(settings, audit, repos)
    operator = QueueOperator([OperatorDecision(action=OperatorAction.BLOCKED, reason="no tool can do this")])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_operator_loop_exhausts_step_budget(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("endless task")
    settings = _settings()
    executor = _executor(settings, audit, repos)
    # Always calls the tool, never finishes - should hit the step budget.
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW)
        for _ in range(10)
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, operator_max_steps=3)

    result = task
    for _ in range(6):
        result = await worker.process_task(result.id)
        if result.status not in {TaskStatus.RUNNING}:
            break

    assert result.status == TaskStatus.FAILED
    assert len(result.metadata["operator_history"]) == 3
    audit_events = repos.audit.list_for_task(task.id)
    assert any(
        event.payload.get("error") == "operator_step_budget_exhausted"
        for event in audit_events
        if event.type == "error"
    )


@pytest.mark.asyncio
async def test_gap_check_entries_do_not_consume_the_tool_call_budget(tmp_path) -> None:
    """Regression guard (docs/HISTORY.md §3.1): the fulfillment/audit gap paths
    append check pseudo-entries to operator_history so the next decide() sees
    why `done` was rejected. Those are bookkeeping - counting them against
    operator_max_steps meant every gap stole a slot from the tool calls the
    model needs to close that gap, and a task could exhaust its whole budget
    having called zero tools."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("summarize the report")
    settings = _settings()
    executor = _executor(settings, audit, repos, tool_name="filesystem.manage", output={"text": "content"})

    class GapThenOkAuditor:
        def __init__(self) -> None:
            self.calls = 0
        async def audit(self, objective, raw_output, *, original_message=None):
            self.calls += 1
            if self.calls == 1:
                return AuditResult(sufficient=False, reason="need more")
            return AuditResult(sufficient=True, answer="grounded")

    class AlwaysCallTool:
        def __init__(self, prefix) -> None:
            self.prefix = list(prefix)
        async def decide(self, objective, config_context, history, *, memory_context="", prefer_major=False):
            if self.prefix:
                return self.prefix.pop(0)
            return OperatorDecision(
                action=OperatorAction.CALL_TOOL, tool_name="filesystem.manage",
                tool_input={}, risk_level=RiskLevel.LOW,
            )

    operator = AlwaysCallTool([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="filesystem.manage", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="early"),
    ])
    worker = TaskWorker(
        repos, audit, executor=executor, operator=operator,
        auditor=GapThenOkAuditor(), operator_max_steps=3,
    )

    result = task
    for _ in range(12):
        result = await worker.process_task(result.id)
        if result.status != TaskStatus.RUNNING:
            break

    history = result.metadata["operator_history"]
    real = [h for h in history if not str(h.get("tool_name") or "").startswith("_")]
    pseudo = [h for h in history if str(h.get("tool_name") or "").startswith("_")]
    assert pseudo, "expected an audit-gap check entry in this scenario"
    # The budget bought 3 REAL tool calls; the check row did not steal one.
    assert len(real) == 3


@pytest.mark.asyncio
async def test_auditor_receives_full_output_not_the_truncated_history_summary(tmp_path) -> None:
    """Regression guard (docs/HISTORY.md §3.2): history entries store a
    2000-char display summary. The Auditor judges count/section sufficiency,
    so handing it a truncation produces false INSUFFICIENT verdicts on exactly
    the long-content objectives it exists for."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("list every line")
    settings = _settings()
    long_text = "EPISODE-LINE " * 900  # ~11.7k chars, well past the 2000 summary cut
    executor = _executor(settings, audit, repos, tool_name="filesystem.manage", output={"text": long_text})

    class RecordingAuditor:
        def __init__(self) -> None:
            self.seen: list[str] = []
        async def audit(self, objective, raw_output, *, original_message=None):
            self.seen.append(raw_output)
            return AuditResult(sufficient=True, answer="ok")

    auditor = RecordingAuditor()
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="filesystem.manage", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="done"),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, auditor=auditor)

    running = await worker.process_task(task.id)
    await worker.process_task(running.id)

    assert auditor.seen, "auditor should have run"
    assert len(auditor.seen[0]) > 2000
    assert len(auditor.seen[0]) == len(long_text)


@pytest.mark.asyncio
async def test_multi_step_task_sends_a_progress_notification_per_step(tmp_path) -> None:
    """Regression guard (docs/HISTORY.md §3.3): the RUNNING dedupe key used to be
    built from metadata["attempt_history"] + current_step_id, both plan-era
    fields with zero writers since P3. That collapsed the key to the constant
    "running", so a 30-step task sent one "working on it" and then went
    silent."""
    repos, audit = make_repos(tmp_path)
    repos.tasks.create("do three things", metadata={"source_chat_id": "100"})
    settings = _settings()
    executor = _executor(settings, audit, repos, output={"text": "ok"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="finished"),
    ])

    class RecordingNotifier:
        def __init__(self) -> None:
            self.sent: list[str] = []
        async def notify(self, task) -> None:
            self.sent.append(task.status.value)

    notifier = RecordingNotifier()
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, notification_sink=notifier)

    for _ in range(6):
        processed = await worker.process_next()
        if processed is None or processed.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            break

    assert notifier.sent.count("running") == 3, notifier.sent
    assert notifier.sent[-1] == "completed"


@pytest.mark.asyncio
async def test_operator_loop_unregistered_tool_does_not_crash(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("uses a nonexistent tool")
    settings = _settings()
    executor = _executor(settings, audit, repos)
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="not.a.real.tool", tool_input={}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.RUNNING
    assert result.metadata["operator_history"][0]["error"] == "unregistered tool: not.a.real.tool"


@pytest.mark.asyncio
async def test_operator_loop_applies_runtime_risk_floor_to_model_tool_call(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("run a generated script")
    settings = AppSettings(
        _env_file=None,
        approval_policy={"require_approval_at_or_above": RiskLevel.CRITICAL},
        capabilities={
            Capability.TERMINAL_RUN: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.CRITICAL,
            )
        },
    )
    adapter = StaticToolAdapter({"stdout": "done"})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"code.interpreter": adapter},
        tool_definitions={
            "code.interpreter": ToolDefinition(
                name="code.interpreter",
                capability=Capability.TERMINAL_RUN,
                enabled=True,
                description="run generated code",
                minimum_risk=RiskLevel.HIGH,
            )
        },
    )
    operator = QueueOperator([
        OperatorDecision(
            action=OperatorAction.CALL_TOOL,
            tool_name="code.interpreter",
            tool_input={"operation": "generate_and_run", "objective": "write a report"},
            risk_level=RiskLevel.LOW,
        ),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.RUNNING
    assert len(adapter.requests) == 1
    assert adapter.requests[0].risk_level == RiskLevel.HIGH
    assert result.metadata["operator_history"][0]["status"] == "succeeded"


def _approval_settings() -> AppSettings:
    return AppSettings(
        _env_file=None,
        capabilities={
            Capability.LLM_GENERATE: CapabilityPolicy(enabled=True, requires_approval=True, max_risk_level=RiskLevel.LOW)
        },
    )


@pytest.mark.asyncio
async def test_operator_loop_needs_approval_creates_request_and_awaits(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("needs approval")
    executor = _executor(_approval_settings(), audit, repos, output={"answer": "42"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={"q": "?"}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.AWAITING_APPROVAL
    assert result.metadata["operator_pending_call"]["tool_name"] == "llm"
    assert result.metadata["operator_pending_call"]["tool_input"] == {"q": "?"}
    assert "pending_approval_preview" in result.metadata
    approvals = repos.approvals.list_for_task(task.id)
    assert len(approvals) == 1
    assert approvals[0].status == ApprovalStatus.PENDING
    assert approvals[0].capability == Capability.LLM_GENERATE


@pytest.mark.asyncio
async def test_operator_loop_awaiting_approval_stays_put_while_pending(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("needs approval")
    executor = _executor(_approval_settings(), audit, repos, output={"answer": "42"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    awaiting = await worker.process_task(task.id)
    still_awaiting = await worker.process_task(awaiting.id)

    assert still_awaiting.status == TaskStatus.AWAITING_APPROVAL
    assert still_awaiting.metadata["operator_pending_call"]["tool_name"] == "llm"
    assert operator.calls == 1  # decide() is not called again while approval is pending


@pytest.mark.asyncio
async def test_run_forever_sleeps_instead_of_busy_looping_on_a_pending_approval(tmp_path, monkeypatch) -> None:
    """docs/UI_UX_AUDIT.md Phase 8: originally, process_next() returned the
    SAME AWAITING_APPROVAL task on every call (claim_next always re-picked
    the oldest task this worker already claimed, and that check returned
    instantly) - without a sleep here, run_forever spun the CPU at 100%
    hammering the DB for as long as a human takes to decide.

    Second pass (docs/UI_UX_AUDIT.md Phase 8, second review): AWAITING_APPROVAL
    was removed from WORKABLE_STATUSES entirely, so process_next() now
    returns None here (nothing else is queued in this test) rather than
    re-picking the same task - but the risk of a busy loop is the same
    shape (a fast, empty return with no sleep), so this test still earns
    its keep even though the specific mechanism it guards against changed.
    """
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("needs approval")
    executor = _executor(_approval_settings(), audit, repos, output={"answer": "42"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)
    await worker.process_task(task.id)  # gets it into AWAITING_APPROVAL once, for real

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise RuntimeError("stop the loop - three sleeps observed is enough to prove it isn't busy-looping")

    monkeypatch.setattr("agent_control.orchestration.worker.asyncio.sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop the loop"):
        await worker.run_forever(poll_interval_seconds=5.0)

    assert sleep_calls == [5.0, 5.0, 5.0]


@pytest.mark.asyncio
async def test_operator_loop_resumes_and_executes_after_approval_granted(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("needs approval")
    executor = _executor(_approval_settings(), audit, repos, output={"answer": "42"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={"q": "?"}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="42"),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    awaiting = await worker.process_task(task.id)
    approvals = repos.approvals.list_for_task(task.id)
    repos.approvals.set_status(approvals[0].id, ApprovalStatus.APPROVED)

    resumed = await worker.process_task(awaiting.id)

    assert resumed.status == TaskStatus.RUNNING
    assert "operator_pending_call" not in resumed.metadata
    assert "pending_approval_preview" not in resumed.metadata
    assert len(resumed.metadata["operator_history"]) == 1
    assert resumed.metadata["operator_history"][0]["tool_name"] == "llm"
    assert resumed.metadata["operator_history"][0]["status"] == "succeeded"

    completed = await worker.process_task(resumed.id)
    assert completed.status == TaskStatus.COMPLETED
    assert completed.metadata["synthesized_answer"] == "42"


@pytest.mark.asyncio
async def test_a_pending_approval_no_longer_blocks_the_worker_from_a_second_task(tmp_path) -> None:
    """docs/UI_UX_AUDIT.md Phase 8, second review: the CPU busy-loop was
    fixed, but the architectural bottleneck wasn't - with the default
    single worker, an AWAITING_APPROVAL task stayed claimable so the
    worker could notice when a decision landed, and claim_next's
    ORDER BY created_at ASC meant it always re-picked that same older task
    ahead of any newer one, starving every later task for as long as a
    human took to decide.

    This is the actual regression test for that: Task A goes to
    AWAITING_APPROVAL, Task B is submitted after it, and the worker's next
    process_next() call must reach Task B - not return None, and not
    re-select Task A.
    """
    repos, audit = make_repos(tmp_path)
    task_a = repos.tasks.create("needs approval")
    task_b = repos.tasks.create("a second, unrelated task")
    executor = _executor(_approval_settings(), audit, repos, output={"answer": "ok"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="ok"),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    awaiting = await worker.process_task(task_a.id)
    assert awaiting.status == TaskStatus.AWAITING_APPROVAL

    claimed = await worker.process_next()

    assert claimed is not None
    assert claimed.id == task_b.id
    assert claimed.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_operator_loop_blocked_when_approval_rejected(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("needs approval")
    executor = _executor(_approval_settings(), audit, repos)
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    awaiting = await worker.process_task(task.id)
    approvals = repos.approvals.list_for_task(task.id)
    repos.approvals.set_status(approvals[0].id, ApprovalStatus.REJECTED)

    result = await worker.process_task(awaiting.id)

    assert result.status == TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_operator_loop_done_with_fulfillment_gap_continues_instead_of_completing(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("create a script that prints hello")
    settings = _settings()
    executor = _executor(settings, audit, repos)
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.DONE, final_answer="Done, but no workspace was ever created."),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.RUNNING
    assert result.metadata["operator_fulfillment_gap_count"] == 1
    assert result.metadata["operator_history"][-1]["status"] == "fulfillment_gap"
    assert "workspace_dir" in result.metadata["operator_history"][-1]["error"]


@pytest.mark.asyncio
async def test_operator_loop_fulfillment_gap_resolves_once_postcondition_met(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("create a script that prints hello")
    settings = _settings()
    executor = _executor(settings, audit, repos, output={"workspace_dir": "/tmp/ws"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="Created the script in /tmp/ws."),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    running = await worker.process_task(task.id)
    completed = await worker.process_task(running.id)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.metadata["workspace_dir"] == "/tmp/ws"
    assert "fulfillment_gap" not in completed.metadata


@pytest.mark.asyncio
async def test_operator_loop_fulfillment_gap_exhausts_and_completes_with_gap_flagged(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("create a script that prints hello")
    settings = _settings()
    executor = _executor(settings, audit, repos)
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.DONE, final_answer=f"attempt {i}") for i in range(3)
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    result = task
    for _ in range(4):
        result = await worker.process_task(result.id)
        if result.status != TaskStatus.RUNNING:
            break

    assert result.status == TaskStatus.COMPLETED
    assert result.metadata["fulfillment_gap"] == "expected_workspace_dir_missing"
    assert result.metadata["operator_fulfillment_gap_count"] == 2


@pytest.mark.asyncio
async def test_operator_loop_audit_replaces_final_answer_with_grounded_synthesis(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("what is the invoice total?")
    settings = _settings()
    executor = _executor(settings, audit, repos, tool_name="filesystem.manage", output={"text": "Invoice #4471 - $250.00"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="filesystem.manage", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="I found an invoice."),
    ])
    auditor = QueueAuditor([AuditResult(sufficient=True, answer="The invoice total is $250.00.")])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, auditor=auditor)

    running = await worker.process_task(task.id)
    completed = await worker.process_task(running.id)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.metadata["synthesized_answer"] == "The invoice total is $250.00."
    assert len(auditor.calls) == 1
    assert auditor.calls[0][0] == "what is the invoice total?"


@pytest.mark.asyncio
async def test_operator_loop_audit_gap_continues_loop_instead_of_completing(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("list the first 5 episodes")
    settings = _settings()
    executor = _executor(settings, audit, repos, tool_name="filesystem.manage", output={"text": "1. Pilot\n2. Second"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="filesystem.manage", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="Here are the episodes."),
    ])
    auditor = QueueAuditor([AuditResult(sufficient=False, reason="only 2 of 5 requested episodes present")])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, auditor=auditor)

    running = await worker.process_task(task.id)
    result = await worker.process_task(running.id)

    assert result.status == TaskStatus.RUNNING
    assert result.metadata["operator_audit_gap_count"] == 1
    assert result.metadata["operator_history"][-1]["status"] == "audit_gap"
    assert "only 2 of 5" in result.metadata["operator_history"][-1]["error"]
    assert "synthesized_answer" not in result.metadata


@pytest.mark.asyncio
async def test_operator_loop_audit_gap_exhausts_and_completes_with_operator_answer(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("list the first 5 episodes")
    settings = _settings()
    executor = _executor(settings, audit, repos, tool_name="filesystem.manage", output={"text": "1. Pilot"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="filesystem.manage", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="attempt 1"),
        OperatorDecision(action=OperatorAction.DONE, final_answer="attempt 2"),
        OperatorDecision(action=OperatorAction.DONE, final_answer="attempt 3"),
    ])
    auditor = QueueAuditor([
        AuditResult(sufficient=False, reason="still insufficient"),
        AuditResult(sufficient=False, reason="still insufficient"),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, auditor=auditor)

    result = task
    for _ in range(5):
        result = await worker.process_task(result.id)
        if result.status != TaskStatus.RUNNING:
            break

    assert result.status == TaskStatus.COMPLETED
    assert result.metadata["operator_audit_gap_count"] == 2
    assert len(auditor.calls) == 2
    # Budget exhausted after 2 insufficient audits - the 3rd `done` skips the
    # audit check entirely and completes with the operator's own answer,
    # not silently dropped.
    assert result.metadata["synthesized_answer"] == "attempt 3"


@pytest.mark.asyncio
async def test_operator_loop_skips_audit_when_no_content_tool_was_called(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("trivial question")
    settings = _settings()
    executor = _executor(settings, audit, repos)
    operator = QueueOperator([OperatorDecision(action=OperatorAction.DONE, final_answer="answer")])
    auditor = QueueAuditor([])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, auditor=auditor)

    completed = await worker.process_task(task.id)

    assert completed.status == TaskStatus.COMPLETED
    assert completed.metadata["synthesized_answer"] == "answer"
    assert auditor.calls == []


@pytest.mark.asyncio
async def test_operator_loop_rate_limited_result_backs_off_instead_of_hot_looping(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("rate limited task")
    settings = _settings()
    executor = _executor_with_adapter(settings, audit, repos, RateLimitedAdapter())
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(
        repos, audit, executor=executor, operator=operator,
        retry_policy=RetryPolicy(settings.limits),
    )

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.RETRYING
    assert result.metadata["operator_retry_count"] == 1
    assert result.metadata["next_retry_at"] is not None
    assert result.metadata["operator_history"][-1]["status"] == "rate_limited"


@pytest.mark.asyncio
async def test_operator_loop_retrying_stays_put_before_backoff_elapses(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("rate limited task")
    settings = _settings()
    executor = _executor_with_adapter(settings, audit, repos, RateLimitedAdapter())
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(
        repos, audit, executor=executor, operator=operator,
        retry_policy=RetryPolicy(settings.limits),
    )

    retrying = await worker.process_task(task.id)
    still_retrying = await worker.process_task(retrying.id)

    assert still_retrying.status == TaskStatus.RETRYING
    assert operator.calls == 1  # decide() is not called again while backoff hasn't elapsed


@pytest.mark.asyncio
async def test_operator_loop_retrying_resumes_and_retries_after_backoff_elapses(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("rate limited task")
    settings = _settings()
    executor = _executor_with_adapter(settings, audit, repos, FailsOnceThenSucceedsAdapter(output={"answer": "ok"}))
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="ok"),
    ])
    worker = TaskWorker(
        repos, audit, executor=executor, operator=operator,
        retry_policy=RetryPolicy(settings.limits),
    )
    retrying = await worker.process_task(task.id)
    assert retrying.status == TaskStatus.RETRYING
    # Force the backoff window to have already elapsed instead of sleeping in the test.
    repos.tasks.update_metadata(
        retrying.id, {**retrying.metadata, "next_retry_at": "2000-01-01T00:00:00+00:00"},
    )

    resumed = await worker.process_task(retrying.id)
    assert resumed.status == TaskStatus.RUNNING

    running_after_retry = await worker.process_task(resumed.id)
    completed = await worker.process_task(running_after_retry.id)

    assert running_after_retry.metadata["operator_history"][-1]["status"] == "succeeded"
    assert completed.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_operator_loop_usage_limited_asks_user_instead_of_retrying(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("usage limited task")
    settings = _settings()
    executor = _executor_with_adapter(settings, audit, repos, UsageLimitedAdapter())
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(
        repos, audit, executor=executor, operator=operator,
        retry_policy=RetryPolicy(settings.limits),
    )

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.CLARIFYING
    assert result.metadata["clarifying_question"]
    assert "next_retry_at" not in result.metadata


@pytest.mark.asyncio
async def test_operator_loop_background_session_awaits_external_completion(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("kick off a coding session")
    settings = _settings()
    executor = _executor_with_adapter(settings, audit, repos, BackgroundSessionAdapter(), tool_name="coding.agent")
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="coding.agent", tool_input={"prompt": "fix it"}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.AWAITING_EXTERNAL
    assert result.metadata["awaiting_external"]["session_id"] == "sess-1"
    assert result.metadata["operator_pending_call"]["tool_name"] == "coding.agent"
    assert result.metadata["operator_history"][-1]["status"] == "running"


@pytest.mark.asyncio
async def test_operator_loop_awaiting_external_is_a_no_op_tick_until_callback_resolves(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("kick off a coding session")
    settings = _settings()
    executor = _executor_with_adapter(settings, audit, repos, BackgroundSessionAdapter(), tool_name="coding.agent")
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="coding.agent", tool_input={"prompt": "fix it"}, risk_level=RiskLevel.LOW),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    awaiting = await worker.process_task(task.id)
    still_awaiting = await worker.process_task(awaiting.id)

    assert still_awaiting.status == TaskStatus.AWAITING_EXTERNAL
    assert operator.calls == 1  # decide() is not called again while the session is still running


@pytest.mark.asyncio
async def test_operator_loop_resumes_after_external_completion_callback(tmp_path) -> None:
    """Mirrors cli.py's _coding_session_completion_callback: writes
    pending_tool_result and flips AWAITING_EXTERNAL back to RUNNING - that
    callback only checks task.status, so it works unchanged for an
    operator-loop task."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("kick off a coding session")
    settings = _settings()
    executor = _executor_with_adapter(settings, audit, repos, BackgroundSessionAdapter(), tool_name="coding.agent")
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="coding.agent", tool_input={"prompt": "fix it"}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="Session completed successfully."),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    awaiting = await worker.process_task(task.id)
    assert awaiting.status == TaskStatus.AWAITING_EXTERNAL

    finished_result = ToolCallResult(
        request_id="toolreq_x", status=ToolResultStatus.SUCCEEDED,
        output={"status": "completed", "returncode": 0, "session_id": "sess-1"},
    )
    repos.tasks.update_metadata(
        awaiting.id,
        {
            **awaiting.metadata,
            "pending_tool_result": {"tool_name": "coding.agent", "result": finished_result.model_dump(mode="json")},
        },
        TaskStatus.RUNNING,
    )

    resumed = await worker.process_task(awaiting.id)
    assert resumed.status == TaskStatus.RUNNING
    assert "pending_tool_result" not in resumed.metadata
    assert "awaiting_external" not in resumed.metadata
    assert resumed.metadata["operator_history"][-1]["status"] == "succeeded"

    completed = await worker.process_task(resumed.id)
    assert completed.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_worker_without_operator_or_executor_blocks_instead_of_crashing(tmp_path) -> None:
    """A misconfigured worker (no LLM provider available to build an operator
    from, e.g.) fails safely and explicitly rather than raising or silently
    doing nothing."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("plain task, nothing configured")
    worker = TaskWorker(repos, audit)

    result = await worker.process_task(task.id)

    assert result.status == TaskStatus.BLOCKED
    events = repos.audit.list_for_task(task.id)
    assert any(event.payload.get("error") == "operator_loop_not_configured" for event in events)


@pytest.mark.asyncio
async def test_worker_accumulates_token_usage_across_operator_and_auditor_calls(tmp_path) -> None:
    """docs/HISTORY.md Part 4 T1.4: the worker used to discard LLM usage
    entirely, so there was no way to see what a task cost. Two operator
    decide() calls plus one auditor audit() call must accumulate into
    task.metadata["token_usage"], split both as a running total and per
    source (operator vs auditor), since they're typically different models
    (major_provider escalation, or the Auditor using a smaller profile)."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("what is the invoice total?")
    settings = _settings()
    executor = _executor(settings, audit, repos, tool_name="filesystem.manage", output={"text": "Invoice #4471 - $250.00"})
    operator = QueueOperator(
        [
            OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="filesystem.manage", tool_input={}, risk_level=RiskLevel.LOW),
            OperatorDecision(action=OperatorAction.DONE, final_answer="I found an invoice."),
        ],
        usages=[
            {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "model": "gpt-4.1"},
            {"prompt_tokens": 150, "completion_tokens": 10, "total_tokens": 160, "model": "gpt-4.1"},
        ],
    )
    auditor = QueueAuditor(
        [AuditResult(sufficient=True, answer="The invoice total is $250.00.")],
        usages=[{"prompt_tokens": 50, "completion_tokens": 15, "total_tokens": 65, "model": "gpt-4.1-mini"}],
    )
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, auditor=auditor)

    running = await worker.process_task(task.id)
    completed = await worker.process_task(running.id)

    assert completed.status == TaskStatus.COMPLETED
    usage = completed.metadata["token_usage"]
    assert usage["calls"] == 3
    assert usage["prompt_tokens"] == 100 + 150 + 50
    assert usage["completion_tokens"] == 20 + 10 + 15
    assert usage["total_tokens"] == 120 + 160 + 65
    assert usage["by_source"]["operator"] == {
        "calls": 2, "prompt_tokens": 250, "completion_tokens": 30, "total_tokens": 280,
    }
    assert usage["by_source"]["auditor"] == {
        "calls": 1, "prompt_tokens": 50, "completion_tokens": 15, "total_tokens": 65,
    }
    assert usage["last_model"] == "gpt-4.1-mini"  # the auditor's model, since it ran last


@pytest.mark.asyncio
async def test_worker_does_not_record_token_usage_when_provider_reports_none(tmp_path) -> None:
    """A ScriptedLLMProvider (scenario replay) or a server that omits usage
    must leave token_usage entirely absent, not create a fabricated zero
    entry - see the docstring on TaskWorker._record_llm_usage."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("plain status-ish task")
    settings = _settings()
    executor = _executor(settings, audit, repos)
    operator = QueueOperator([OperatorDecision(action=OperatorAction.DONE, final_answer="Done.")])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    completed = await worker.process_task(task.id)

    assert completed.status == TaskStatus.COMPLETED
    assert "token_usage" not in completed.metadata


@pytest.mark.asyncio
async def test_worker_prefers_major_provider_on_the_step_after_an_audit_gap(tmp_path) -> None:
    """docs/HISTORY.md Part 4 T2.6: once a `done` has been rejected once
    (an audit-gap or fulfillment-gap marker is in history), the NEXT
    decide() call should prefer the major provider - a done-was-rejected
    signal is exactly the kind of observed difficulty the escalation logic
    was already designed to react to, just available before the call this
    time instead of only from a caught parse exception."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("list the first 5 episodes")
    settings = _settings()
    executor = _executor(settings, audit, repos, tool_name="filesystem.manage", output={"text": "1. Pilot\n2. Second"})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="filesystem.manage", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="Here are the episodes."),
        OperatorDecision(action=OperatorAction.DONE, final_answer="Here are the (better) episodes."),
    ])
    auditor = QueueAuditor([
        AuditResult(sufficient=False, reason="only 2 of 5 requested episodes present"),
        AuditResult(sufficient=True, answer="grounded final answer"),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, auditor=auditor)

    running = await worker.process_task(task.id)  # step 1: call_tool
    gapped = await worker.process_task(running.id)  # step 2: done -> audit gap
    await worker.process_task(gapped.id)  # step 3: done again, now with the gap marker in history

    # Steps 1 and 2 have no prior gap marker yet; step 3 runs after the
    # audit-gap entry was appended to history, so only it should prefer major.
    assert operator.prefer_major_calls == [False, False, True]
