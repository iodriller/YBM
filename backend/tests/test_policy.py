from __future__ import annotations

import pytest

from datetime import timedelta

from agent_control.config import AppSettings, CapabilityPolicy, default_capability_policies
from agent_control.orchestration import StaticToolAdapter, ToolExecutor
from agent_control.policy import PolicyEngine
from agent_control.schemas import (
    ApprovalGrant,
    ApprovalStatus,
    Capability,
    RiskLevel,
    ToolCallRequest,
    ToolResultStatus,
    utc_now,
)
from agent_control.tools.registry import build_tool_registry
from helpers import make_repos




def test_disabled_capability_is_denied(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Run command")
    settings = AppSettings(_env_file=None, capabilities=default_capability_policies())
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


def test_scope_check_does_not_allow_prefix_escape(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Read file")
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.FILESYSTEM_READ: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.LOW,
                scopes=["C:/safe"],
            )
        },
    )
    policy = PolicyEngine(settings, audit)

    denied = policy.evaluate(
        ToolCallRequest(
            task_id=task.id,
            tool_name="filesystem",
            capability=Capability.FILESYSTEM_READ,
            risk_level=RiskLevel.LOW,
            scope_target="C:/safe_evil/file.txt",
        )
    )
    allowed = policy.evaluate(
        ToolCallRequest(
            task_id=task.id,
            tool_name="filesystem",
            capability=Capability.FILESYSTEM_READ,
            risk_level=RiskLevel.LOW,
            scope_target="C:/safe/file.txt",
        )
    )

    assert denied.allowed is False
    assert denied.reason == "scope_not_allowed"
    assert allowed.allowed is True


def test_has_grant_skips_approval_but_not_risk_ceiling(tmp_path) -> None:
    """"Allow for this task" (docs/UI_UX_AUDIT.md Phase 1): has_grant=True
    only bypasses the "ask a human" step. It must never let a call through
    that fails the capability's own risk ceiling - a grant recorded while
    the ceiling allowed HIGH must not survive a config change that later
    lowers it, since PolicyEngine re-checks the ceiling on every call."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Run command")
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TERMINAL_RUN: CapabilityPolicy(
                enabled=True, requires_approval=True, max_risk_level=RiskLevel.MEDIUM,
            )
        },
    )
    engine = PolicyEngine(settings, audit)
    request = ToolCallRequest(
        task_id=task.id, tool_name="terminal", capability=Capability.TERMINAL_RUN, risk_level=RiskLevel.MEDIUM,
    )

    without_grant = engine.evaluate(request)
    with_grant = engine.evaluate(request, has_grant=True)
    over_ceiling = engine.evaluate(
        request.model_copy(update={"risk_level": RiskLevel.HIGH}), has_grant=True,
    )

    assert without_grant.needs_approval is True
    assert with_grant.allowed is True and with_grant.needs_approval is False
    assert with_grant.reason == "granted_for_task"
    assert over_ceiling.allowed is False and over_ceiling.reason == "risk_exceeds_capability_policy"


@pytest.mark.asyncio
async def test_executor_consults_a_matching_grant_instead_of_asking_again(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Run two commands")
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TERMINAL_RUN: CapabilityPolicy(
                enabled=True, requires_approval=True, max_risk_level=RiskLevel.HIGH,
            )
        },
    )
    adapter = StaticToolAdapter({"done": True})
    executor = ToolExecutor(PolicyEngine(settings, audit), repos, audit, adapters={"terminal": adapter})

    first = await executor.execute(
        ToolCallRequest(task_id=task.id, tool_name="terminal", capability=Capability.TERMINAL_RUN, risk_level=RiskLevel.LOW)
    )
    assert first.status == ToolResultStatus.NEEDS_APPROVAL

    repos.approval_grants.create(
        ApprovalGrant(
            task_id=task.id,
            tool_name="terminal",
            capability=Capability.TERMINAL_RUN,
            granted_from_approval_id=first.output["approval_id"],
            expires_at=utc_now() + timedelta(minutes=10),
        )
    )

    second = await executor.execute(
        ToolCallRequest(task_id=task.id, tool_name="terminal", capability=Capability.TERMINAL_RUN, risk_level=RiskLevel.LOW)
    )

    assert second.status == ToolResultStatus.SUCCEEDED
    assert len(adapter.requests) == 1  # only the granted second call actually dispatched


@pytest.mark.asyncio
async def test_executor_marks_a_grant_bypassed_call_as_approved_for_the_adapter(tmp_path) -> None:
    """Regression: a grant only bypasses PolicyEngine's "ask a human" gate.
    Some adapters (code.interpreter's generate_and_run, when generated code
    silently fell back to an unsandboxed backend) carry their own separate
    approved-input check, independent of policy. The dispatch-time input
    rewrite that flips input["approved"] to True only fired for the
    approval-replay path (approval is not None) - a grant-authorized call
    reached the adapter with approved still False, so the adapter's own
    gate re-raised "needs approval" right after policy had just granted it.
    Caught by hand-tracing a live end-to-end run; this locks it in."""
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Run a command that checks its own approval")
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TERMINAL_RUN: CapabilityPolicy(
                enabled=True, requires_approval=True, max_risk_level=RiskLevel.HIGH,
            )
        },
    )
    adapter = StaticToolAdapter({"done": True})
    executor = ToolExecutor(PolicyEngine(settings, audit), repos, audit, adapters={"terminal": adapter})
    repos.approval_grants.create(
        ApprovalGrant(
            task_id=task.id,
            tool_name="terminal",
            capability=Capability.TERMINAL_RUN,
            granted_from_approval_id="approval_seed",
            expires_at=utc_now() + timedelta(minutes=10),
        )
    )

    result = await executor.execute(
        ToolCallRequest(
            task_id=task.id, tool_name="terminal", capability=Capability.TERMINAL_RUN,
            risk_level=RiskLevel.LOW, input={"approved": False},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert adapter.requests[0].input["approved"] is True


def test_global_approval_floor_cannot_be_disabled_per_capability(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Send network request")
    settings = AppSettings(
        _env_file=None,
        approval_policy={"require_approval_at_or_above": RiskLevel.MEDIUM},
        capabilities={
            Capability.NETWORK_HTTP: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )

    decision = PolicyEngine(settings, audit).evaluate(
        ToolCallRequest(
            task_id=task.id,
            tool_name="http.request",
            capability=Capability.NETWORK_HTTP,
            risk_level=RiskLevel.HIGH,
        )
    )

    assert decision.needs_approval is True
    assert decision.reason == "approval_required"


@pytest.mark.asyncio
async def test_executor_creates_approval_before_tool_call(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
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
    repos, audit = make_repos(tmp_path)
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

    events = repos.audit.list_for_task(task.id)
    completed = [event for event in events if event.type.value == "tool_completed"]
    assert completed


@pytest.mark.asyncio
async def test_executor_rejects_invalid_registered_tool_input_before_adapter(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Launch app")
    settings = AppSettings(
        _env_file=None,
        approval_policy={"require_approval_at_or_above": RiskLevel.CRITICAL},
        adapters={"workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces")}},
        capabilities={
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )
    registry = build_tool_registry(settings, "http://127.0.0.1:8765")
    adapter = StaticToolAdapter({"url": "http://127.0.0.1:8890/"})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"workspace.manage": adapter},
        tool_definitions=registry.definitions,
    )

    result = await executor.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="workspace.manage",
            capability=Capability.FILESYSTEM_WRITE,
            risk_level=RiskLevel.HIGH,
            input={"operation": "launch_static", "web_port_start": "not-a-port"},
        )
    )

    assert result.status == ToolResultStatus.FAILED
    assert result.error_class.value == "validation_failed"
    assert "invalid input for workspace.manage" in (result.error_message or "")
    assert adapter.requests == []


@pytest.mark.asyncio
async def test_executor_normalizes_registered_tool_defaults(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Prepare workspace")
    settings = AppSettings(
        _env_file=None,
        approval_policy={"require_approval_at_or_above": RiskLevel.CRITICAL},
        adapters={"workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces")}},
        capabilities={
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )
    registry = build_tool_registry(settings, "http://127.0.0.1:8765")
    adapter = StaticToolAdapter({"workspace_dir": str(tmp_path / "workspaces" / task.id)})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"workspace.manage": adapter},
        tool_definitions=registry.definitions,
    )

    result = await executor.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="workspace.manage",
            capability=Capability.FILESYSTEM_WRITE,
            risk_level=RiskLevel.HIGH,
            input={"objective": "Prepare workspace"},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert adapter.requests[0].input["operation"] == "prepare"


@pytest.mark.asyncio
async def test_executor_rejects_invalid_registered_tool_output(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    task = repos.tasks.create("Launch app")
    settings = AppSettings(
        _env_file=None,
        approval_policy={"require_approval_at_or_above": RiskLevel.MEDIUM},
        adapters={"workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces")}},
        capabilities={
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )
    registry = build_tool_registry(settings, "http://127.0.0.1:8765")
    adapter = StaticToolAdapter({"workspace_dir": str(tmp_path / "workspaces" / task.id)})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"workspace.manage": adapter},
        tool_definitions=registry.definitions,
    )

    def _launch_request() -> ToolCallRequest:
        # A fresh request each call (own id -> own tool_invocations row);
        # the approval binds on content (task/tool/capability/risk/input),
        # not on request id, so two separately-built but identical requests
        # still match the same approval.
        return ToolCallRequest(
            task_id=task.id,
            tool_name="workspace.manage",
            capability=Capability.FILESYSTEM_WRITE,
            risk_level=RiskLevel.HIGH,
            input={"operation": "launch_static"},
        )

    # HIGH is filesystem.write's minimum required risk (no per-operation
    # override), and HIGH >= approval_policy.require_approval_at_or_above -
    # this needs a real, consumed approval to reach output validation at
    # all, same as any other HIGH-risk call.
    gated = await executor.execute(_launch_request())
    assert gated.status == ToolResultStatus.NEEDS_APPROVAL
    approval_id = gated.output["approval_id"]
    assert repos.approvals.decide_pending(approval_id, ApprovalStatus.APPROVED)

    result = await executor.execute(_launch_request(), approval_id=approval_id)

    assert result.status == ToolResultStatus.FAILED
    assert result.error_class.value == "validation_failed"
    assert "invalid output for workspace.manage" in (result.error_message or "")
