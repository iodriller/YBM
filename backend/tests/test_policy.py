from __future__ import annotations

import pytest

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration import StaticToolAdapter, ToolExecutor
from agent_control.policy import PolicyEngine
from agent_control.schemas import Capability, RiskLevel, ToolCallRequest, ToolResultStatus
from agent_control.storage import AuditLogger, Database, Repositories


def _repos(tmp_path) -> tuple[Repositories, AuditLogger]:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    return repos, AuditLogger(repos.audit)


def test_disabled_capability_is_denied(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run command")
    settings = AppSettings(_env_file=None)
    policy = PolicyEngine(settings, audit)

    decision = policy.evaluate(
        ToolCallRequest(
            task_id=task.id,
            tool_name="terminal",
            capability=Capability.TERMINAL_RUN,
            risk_level=RiskLevel.LOW,
        )
    )

    assert decision.allowed is False
    assert decision.reason == "capability_disabled"


@pytest.mark.asyncio
async def test_executor_creates_approval_before_tool_call(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run command")
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TERMINAL_RUN: CapabilityPolicy(
                enabled=True,
                requires_approval=True,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"terminal": StaticToolAdapter()},
    )

    result = await executor.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="terminal",
            capability=Capability.TERMINAL_RUN,
            risk_level=RiskLevel.LOW,
        )
    )

    approvals = repos.approvals.list_for_task(task.id)

    assert result.status == ToolResultStatus.NEEDS_APPROVAL
    assert len(approvals) == 1


@pytest.mark.asyncio
async def test_executor_runs_allowed_tool(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Summarize")
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
    adapter = StaticToolAdapter({"done": True})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": adapter},
    )

    result = await executor.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="llm",
            capability=Capability.LLM_GENERATE,
            risk_level=RiskLevel.LOW,
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert adapter.requests
