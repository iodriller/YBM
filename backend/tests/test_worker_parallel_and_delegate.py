"""Unit tests for call_tools_parallel and delegate (docs/HISTORY.md Part 3
T1.1/T1.2) - the two new OperatorAction values that let the Operator loop
run independent tool calls concurrently, or hand a bounded sub-task off to
an isolated inner loop with its own history and step budget.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration import TaskWorker, ToolExecutor
from agent_control.policy import PolicyEngine
from agent_control.schemas import (
    Capability,
    OperatorAction,
    OperatorDecision,
    ParallelToolCall,
    RiskLevel,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
)
from agent_control.tools.registry import ToolDefinition
from helpers import make_repos


class QueueOperator:
    def __init__(self, decisions: list[OperatorDecision], usages: list[dict | None] | None = None) -> None:
        self.decisions = list(decisions)
        self._usages = list(usages) if usages is not None else None
        self.calls = 0
        self.last_usage: dict | None = None

    async def decide(self, objective, config_context, history, *, memory_context="", prefer_major=False):
        self.calls += 1
        if self._usages is not None:
            self.last_usage = self._usages.pop(0)
        return self.decisions.pop(0)


class RecordingAdapter:
    """Return distinguishable output and optionally track concurrent calls."""

    def __init__(
        self,
        name: str,
        *,
        delay: float = 0.05,
        output: dict | None = None,
        needs_approval: bool = False,
        concurrency_probe: dict[str, int] | None = None,
    ) -> None:
        self.name = name
        self.delay = delay
        self.output = output or {"tool": name}
        self.needs_approval = needs_approval
        self.concurrency_probe = concurrency_probe
        self.started_at: list[float] = []
        self.finished_at: list[float] = []

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        loop = asyncio.get_event_loop()
        self.started_at.append(loop.time())
        if self.concurrency_probe is not None:
            self.concurrency_probe["active"] += 1
            self.concurrency_probe["maximum"] = max(
                self.concurrency_probe["maximum"],
                self.concurrency_probe["active"],
            )
        try:
            await asyncio.sleep(self.delay)
        finally:
            if self.concurrency_probe is not None:
                self.concurrency_probe["active"] -= 1
            self.finished_at.append(loop.time())
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=self.output)


class BackgroundAdapter:
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id, status=ToolResultStatus.SUCCEEDED,
            output={"status": "running", "session_id": "sess-1", "provider": "codex"},
        )


def _settings(*caps: Capability) -> AppSettings:
    return AppSettings(
        _env_file=None,
        capabilities={
            cap: CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
            for cap in caps
        },
    )


def _executor(settings, audit, repos, adapters: dict) -> ToolExecutor:
    return ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters=adapters,
        tool_definitions={
            name: ToolDefinition(name=name, capability=Capability.LLM_GENERATE, enabled=True, description="test tool")
            for name in adapters
        },
    )


@pytest.mark.asyncio
async def test_call_tools_parallel_runs_calls_concurrently_not_sequentially(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("check three sites")
    settings = _settings(Capability.LLM_GENERATE)
    concurrency_probe = {"active": 0, "maximum": 0}
    site_a = RecordingAdapter("site_a", delay=0.08, concurrency_probe=concurrency_probe)
    site_b = RecordingAdapter("site_b", delay=0.08, concurrency_probe=concurrency_probe)
    site_c = RecordingAdapter("site_c", delay=0.08, concurrency_probe=concurrency_probe)
    executor = _executor(settings, audit, repos, {"site_a": site_a, "site_b": site_b, "site_c": site_c})
    operator = QueueOperator([
        OperatorDecision(
            action=OperatorAction.CALL_TOOLS_PARALLEL,
            parallel_calls=[
                ParallelToolCall(tool_name="site_a", tool_input={}),
                ParallelToolCall(tool_name="site_b", tool_input={}),
                ParallelToolCall(tool_name="site_c", tool_input={}),
            ],
        ),
        OperatorDecision(action=OperatorAction.DONE, final_answer="Checked all three."),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    running = await worker.process_task(task.id)

    # Track overlap directly instead of comparing clock readings. Windows CI
    # can quantize loop.time() enough that a start and finish compare equal.
    assert concurrency_probe == {"active": 0, "maximum": 3}

    history = running.metadata["operator_history"]
    assert len(history) == 3
    assert {entry["tool_name"] for entry in history} == {"site_a", "site_b", "site_c"}
    assert all(entry["status"] == "succeeded" and entry["parallel"] is True for entry in history)


@pytest.mark.asyncio
async def test_call_tools_parallel_counts_as_n_steps_toward_the_budget(tmp_path) -> None:
    """Fairness/safety: a parallel batch must cost as many budget slots as
    the calls it makes, not act as a free unlimited-fanout escape hatch."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("check three sites")
    settings = _settings(Capability.LLM_GENERATE)
    adapters = {name: RecordingAdapter(name, delay=0.0) for name in ("a", "b", "c")}
    executor = _executor(settings, audit, repos, adapters)
    operator = QueueOperator([
        OperatorDecision(
            action=OperatorAction.CALL_TOOLS_PARALLEL,
            parallel_calls=[ParallelToolCall(tool_name=n, tool_input={}) for n in ("a", "b", "c")],
        ),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator, operator_max_steps=3)

    running = await worker.process_task(task.id)

    # 3 history entries already consumes the whole budget of 3.
    assert len(running.metadata["operator_history"]) == 3
    exhausted = await worker.process_task(running.id)
    assert exhausted.status.value == "failed"
    events = repos.audit.list_for_task(exhausted.id)
    assert any(event.payload.get("error") == "operator_step_budget_exhausted" for event in events)


@pytest.mark.asyncio
async def test_call_tools_parallel_call_needing_approval_fails_that_call_only(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("do two things, one risky")
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.LLM_GENERATE: CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH),
            Capability.TERMINAL_RUN: CapabilityPolicy(enabled=True, requires_approval=True, max_risk_level=RiskLevel.HIGH),
        },
    )
    safe_adapter = RecordingAdapter("safe_read", delay=0.0)
    risky_adapter = RecordingAdapter("risky_write", delay=0.0)
    executor = ToolExecutor(
        PolicyEngine(settings, audit), repos, audit,
        adapters={"safe_read": safe_adapter, "risky_write": risky_adapter},
        tool_definitions={
            "safe_read": ToolDefinition(name="safe_read", capability=Capability.LLM_GENERATE, enabled=True, description="x"),
            "risky_write": ToolDefinition(name="risky_write", capability=Capability.TERMINAL_RUN, enabled=True, description="x"),
        },
    )
    operator = QueueOperator([
        OperatorDecision(
            action=OperatorAction.CALL_TOOLS_PARALLEL,
            parallel_calls=[
                ParallelToolCall(tool_name="safe_read", tool_input={}, risk_level=RiskLevel.LOW),
                ParallelToolCall(tool_name="risky_write", tool_input={}, risk_level=RiskLevel.HIGH),
            ],
        ),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    running = await worker.process_task(task.id)

    history = {entry["tool_name"]: entry for entry in running.metadata["operator_history"]}
    assert history["safe_read"]["status"] == "succeeded"
    assert history["risky_write"]["status"] == "failed"
    assert "needs approval" in history["risky_write"]["error"]
    # The task itself does not pause for approval - parallel calls that need
    # it fail cleanly instead, per the module docstring.
    assert running.status.value == "running"


@pytest.mark.asyncio
async def test_call_tools_parallel_background_session_call_fails_that_call_only(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("start a coding session and check a site")
    settings = _settings(Capability.LLM_GENERATE)
    executor = _executor(
        settings, audit, repos,
        {"coding.agent": BackgroundAdapter(), "site_a": RecordingAdapter("site_a", delay=0.0)},
    )
    operator = QueueOperator([
        OperatorDecision(
            action=OperatorAction.CALL_TOOLS_PARALLEL,
            parallel_calls=[
                ParallelToolCall(tool_name="coding.agent", tool_input={}),
                ParallelToolCall(tool_name="site_a", tool_input={}),
            ],
        ),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    running = await worker.process_task(task.id)

    history = {entry["tool_name"]: entry for entry in running.metadata["operator_history"]}
    assert history["site_a"]["status"] == "succeeded"
    assert history["coding.agent"]["status"] == "failed"
    assert "background session" in history["coding.agent"]["error"]


@pytest.mark.asyncio
async def test_delegate_isolates_sub_task_history_from_parent(tmp_path) -> None:
    """The whole point of delegation: the sub-agent's own step-by-step
    history never appears in the parent's history - only the summary does."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("summarize a file via a sub-task")
    settings = _settings(Capability.LLM_GENERATE)
    executor = _executor(settings, audit, repos, {"filesystem.manage": RecordingAdapter("filesystem.manage", delay=0.0, output={"text": "file contents here"})})
    parent_operator = QueueOperator([
        OperatorDecision(action=OperatorAction.DELEGATE, delegate_objective="read and summarize the file"),
        OperatorDecision(action=OperatorAction.DONE, final_answer="Summary: file contents here."),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=parent_operator)
    # The sub-loop shares the SAME operator instance/provider in production;
    # here we swap decisions mid-flight by queuing the sub-task's decisions
    # after the parent's - QueueOperator just pops in call order.
    parent_operator.decisions.insert(
        1,
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="filesystem.manage", tool_input={}, risk_level=RiskLevel.LOW),
    )
    parent_operator.decisions.insert(
        2, OperatorDecision(action=OperatorAction.DONE, final_answer="sub-task done: file contents here")
    )

    running = await worker.process_task(task.id)

    history = running.metadata["operator_history"]
    assert len(history) == 1
    assert history[0]["tool_name"] == "delegate"
    assert history[0]["status"] == "succeeded"
    assert history[0]["output_summary"] == "sub-task done: file contents here"
    # The sub-task's own filesystem.manage call must NOT appear at the
    # parent level - only the one "delegate" summary entry does.
    assert all(entry["tool_name"] != "filesystem.manage" for entry in history)


@pytest.mark.asyncio
async def test_delegate_restricts_tools_to_the_declared_subset(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("delegate with a restricted tool set")
    settings = _settings(Capability.LLM_GENERATE)
    executor = _executor(
        settings, audit, repos,
        {"allowed_tool": RecordingAdapter("allowed_tool", delay=0.0), "forbidden_tool": RecordingAdapter("forbidden_tool", delay=0.0)},
    )
    operator = QueueOperator([
        OperatorDecision(
            action=OperatorAction.DELEGATE, delegate_objective="do a narrow thing", delegate_tools=["allowed_tool"],
        ),
        # Sub-loop step 1: tries the forbidden tool - must be refused in code,
        # not just discouraged by the prompt.
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="forbidden_tool", tool_input={}, risk_level=RiskLevel.LOW),
        # Sub-loop step 2: tries the allowed tool - must succeed.
        OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="allowed_tool", tool_input={}, risk_level=RiskLevel.LOW),
        OperatorDecision(action=OperatorAction.DONE, final_answer="used the allowed tool"),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    running = await worker.process_task(task.id)

    forbidden_adapter = executor.adapters["forbidden_tool"]
    allowed_adapter = executor.adapters["allowed_tool"]
    assert forbidden_adapter.started_at == []  # never actually invoked
    assert len(allowed_adapter.started_at) == 1
    history = running.metadata["operator_history"]
    assert len(history) == 1
    assert history[0]["status"] == "succeeded"
    assert history[0]["output_summary"] == "used the allowed tool"


@pytest.mark.asyncio
async def test_delegate_cannot_recursively_delegate(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("delegate that tries to delegate again")
    settings = _settings(Capability.LLM_GENERATE)
    executor = _executor(settings, audit, repos, {})
    operator = QueueOperator([
        OperatorDecision(action=OperatorAction.DELEGATE, delegate_objective="outer"),
        # Sub-loop tries to delegate further - must be refused, forcing it
        # toward something else instead of recursing.
        OperatorDecision(action=OperatorAction.DELEGATE, delegate_objective="inner"),
        OperatorDecision(action=OperatorAction.DONE, final_answer="gave up on nested delegation, finished anyway"),
    ])
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    running = await worker.process_task(task.id)

    history = running.metadata["operator_history"]
    assert len(history) == 1
    assert history[0]["status"] == "succeeded"
    assert history[0]["output_summary"] == "gave up on nested delegation, finished anyway"


@pytest.mark.asyncio
async def test_delegate_fails_cleanly_when_step_budget_exhausted(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("delegate that never finishes")
    settings = _settings(Capability.LLM_GENERATE)
    executor = _executor(settings, audit, repos, {"loop_tool": RecordingAdapter("loop_tool", delay=0.0)})
    # DELEGATE_MAX_STEPS is 6 - queue 6 CALL_TOOL decisions for the sub-loop
    # that never call DONE, so it must exhaust its own bounded budget.
    operator = QueueOperator(
        [OperatorDecision(action=OperatorAction.DELEGATE, delegate_objective="never finishes")]
        + [
            OperatorDecision(action=OperatorAction.CALL_TOOL, tool_name="loop_tool", tool_input={}, risk_level=RiskLevel.LOW)
            for _ in range(TaskWorker.DELEGATE_MAX_STEPS)
        ]
    )
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    running = await worker.process_task(task.id)

    history = running.metadata["operator_history"]
    assert len(history) == 1
    assert history[0]["status"] == "failed"
    assert "step budget" in history[0]["error"]
    assert operator.calls == 1 + TaskWorker.DELEGATE_MAX_STEPS


@pytest.mark.asyncio
async def test_delegate_accumulates_subagent_token_usage_into_parent_task(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("delegate that costs tokens")
    settings = _settings(Capability.LLM_GENERATE)
    executor = _executor(settings, audit, repos, {})
    operator = QueueOperator(
        [
            OperatorDecision(action=OperatorAction.DELEGATE, delegate_objective="a sub-task"),
            OperatorDecision(action=OperatorAction.DONE, final_answer="sub-task result"),
        ],
        usages=[
            None,  # the parent's own decide() call for the DELEGATE decision
            {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50, "model": "small"},
        ],
    )
    worker = TaskWorker(repos, audit, executor=executor, operator=operator)

    running = await worker.process_task(task.id)

    usage = running.metadata["token_usage"]
    assert usage["by_source"]["subagent"] == {
        "calls": 1, "prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50,
    }
