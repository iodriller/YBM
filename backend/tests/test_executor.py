"""Unit tests for the ToolExecutor.

ToolExecutor sits between the worker and the tool adapters: it validates
inputs, consults the policy engine, dispatches to the right adapter, validates
outputs, and records everything in the audit log + tool_invocations table.
These tests pin its decision branches.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration.executor import StaticToolAdapter, ToolExecutor
from agent_control.policy import PolicyEngine
from agent_control.schemas import (
    Capability,
    ErrorClass,
    RiskLevel,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
)
from agent_control.storage import AuditLogger, Database, Repositories


def _repos(tmp_path) -> tuple[Repositories, AuditLogger]:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    return repos, AuditLogger(repos.audit)


def _settings_with(capability: Capability, **overrides: Any) -> AppSettings:
    """Build an AppSettings whose only enabled capability is `capability`."""
    base = AppSettings()
    base.capabilities[capability] = CapabilityPolicy(
        enabled=overrides.pop("enabled", True),
        scopes=overrides.pop("scopes", []),
        requires_approval=overrides.pop("requires_approval", False),
        max_risk_level=overrides.pop("max_risk_level", RiskLevel.LOW),
        allow_patterns=overrides.pop("allow_patterns", []),
        deny_patterns=overrides.pop("deny_patterns", []),
    )
    return base


class _ExplodingAdapter:
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        raise RuntimeError("adapter blew up")


def _request(task_id: str, *, capability: Capability = Capability.LLM_GENERATE,
             tool_name: str = "llm", risk: RiskLevel = RiskLevel.LOW,
             requires_approval: bool = False) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        tool_name=tool_name,
        capability=capability,
        risk_level=risk,
        requires_approval=requires_approval,
        input={"prompt": "hi"},
    )


@pytest.mark.asyncio
async def test_executor_dispatches_to_registered_adapter(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("t")
    settings = _settings_with(Capability.LLM_GENERATE)
    adapter = StaticToolAdapter(output={"text": "hello"})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": adapter},
    )

    result = await executor.execute(_request(task.id))

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output == {"text": "hello"}
    assert len(adapter.requests) == 1


@pytest.mark.asyncio
async def test_executor_records_request_and_completion_in_audit(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("t")
    settings = _settings_with(Capability.LLM_GENERATE)
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": StaticToolAdapter()},
    )

    await executor.execute(_request(task.id))

    invocations = repos.tool_invocations.list_for_task(task.id)
    assert len(invocations) == 1
    assert invocations[0]["result"] is not None
    assert invocations[0]["result"]["status"] == ToolResultStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_executor_denies_when_policy_blocks(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("t")
    # explicitly disable the capability so the policy denies it
    settings = _settings_with(Capability.LLM_GENERATE, enabled=False)
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": StaticToolAdapter()},
    )

    result = await executor.execute(_request(task.id))

    assert result.status == ToolResultStatus.DENIED
    assert result.error_class == ErrorClass.POLICY_DENIED


@pytest.mark.asyncio
async def test_executor_returns_needs_approval_and_creates_approval(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("t")
    settings = _settings_with(Capability.LLM_GENERATE, requires_approval=True)
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": StaticToolAdapter()},
    )

    result = await executor.execute(_request(task.id))

    assert result.status == ToolResultStatus.NEEDS_APPROVAL
    assert "approval_id" in result.output
    approvals = repos.approvals.list_for_task(task.id)
    assert len(approvals) == 1


@pytest.mark.asyncio
async def test_executor_runs_when_pre_approved(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("t")
    settings = _settings_with(Capability.LLM_GENERATE, requires_approval=True)
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": StaticToolAdapter()},
    )

    result = await executor.execute(_request(task.id), approved=True)

    assert result.status == ToolResultStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_executor_fails_when_no_adapter_registered(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("t")
    settings = _settings_with(Capability.LLM_GENERATE)
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={},  # nothing registered
    )

    result = await executor.execute(_request(task.id))

    assert result.status == ToolResultStatus.FAILED
    assert result.error_class == ErrorClass.ADAPTER_FAILED
    assert "not registered" in (result.error_message or "")


@pytest.mark.asyncio
async def test_executor_converts_adapter_exception_to_failed(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("t")
    settings = _settings_with(Capability.LLM_GENERATE)
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": _ExplodingAdapter()},
    )

    result = await executor.execute(_request(task.id))

    assert result.status == ToolResultStatus.FAILED
    assert result.error_class == ErrorClass.ADAPTER_FAILED
    assert "adapter blew up" in (result.error_message or "")


@pytest.mark.asyncio
async def test_executor_rejects_risk_above_policy(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("t")
    # policy caps at LOW; request asks for HIGH
    settings = _settings_with(Capability.LLM_GENERATE, max_risk_level=RiskLevel.LOW)
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": StaticToolAdapter()},
    )

    result = await executor.execute(_request(task.id, risk=RiskLevel.HIGH))

    assert result.status == ToolResultStatus.DENIED
    assert result.error_class == ErrorClass.POLICY_DENIED
