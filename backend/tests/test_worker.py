from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, WorkspaceAdapterConfig
from agent_control.llm import PlannerService, StaticPlanProvider
from agent_control.orchestration import StaticToolAdapter, TaskWorker, ToolExecutor
from agent_control.orchestration.default_plans import build_default_vscode_development_plan, build_evaluator_recovery_plan
from agent_control.policy import PolicyEngine
from agent_control.recovery import RetryPolicy
from agent_control.schemas import (
    ApprovalStatus,
    Capability,
    ErrorClass,
    PlanModel,
    PlanPostcondition,
    PlanStep,
    PostconditionType,
    RiskLevel,
    TaskStatus,
    TaskType,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
)
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.local_workspace import LocalWorkspaceAdapter


def _repos(tmp_path) -> tuple[Repositories, AuditLogger]:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    return repos, AuditLogger(repos.audit)


@pytest.mark.asyncio
async def test_worker_plans_and_completes_plan_only_task(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Plan an app")
    planner = PlannerService(
        StaticPlanProvider(
            PlanModel(
                objective="Plan an app",
                steps=[PlanStep(title="Plan", description="Create plan.")],
                success_criteria=["Plan exists."],
            )
        ),
        repos,
        audit,
    )
    worker = TaskWorker(repos, audit, planner=planner)

    updated = await worker.process_task(task.id)

    assert updated.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_worker_runs_allowed_tool_step(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run safe step")
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective="Run safe step",
            steps=[
                PlanStep(
                    title="Summarize",
                    description="Run a safe LLM step.",
                    required_capabilities=[Capability.LLM_GENERATE],
                    tool_name="llm",
                )
            ],
            success_criteria=["Step completed."],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
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
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": StaticToolAdapter()},
    )
    worker = TaskWorker(repos, audit, executor=executor)

    running = await worker.process_task(task.id)
    completed = await worker.process_task(running.id)

    assert running.status == TaskStatus.RUNNING
    assert completed.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_worker_resumes_after_step_approval(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run approved step")
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective="Run approved step",
            steps=[
                PlanStep(
                    title="Run terminal step",
                    description="A step that requires approval.",
                    required_capabilities=[Capability.TERMINAL_RUN],
                    risk_level=RiskLevel.MEDIUM,
                    requires_approval=True,
                    tool_name="terminal",
                )
            ],
            success_criteria=["Step completed."],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
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
    adapter = StaticToolAdapter({"terminal_output": [{"content": "done", "is_final": True, "exit_code": 0}]})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"terminal": adapter},
    )
    worker = TaskWorker(repos, audit, executor=executor)

    awaiting = await worker.process_task(task.id)
    approval = repos.approvals.list_for_task(task.id)[0]
    repos.approvals.set_status(approval.id, ApprovalStatus.APPROVED)
    running = await worker.process_task(task.id)
    completed = await worker.process_task(task.id)

    assert awaiting.status == TaskStatus.AWAITING_APPROVAL
    assert running.status == TaskStatus.RUNNING
    assert completed.status == TaskStatus.COMPLETED
    assert adapter.requests


@pytest.mark.asyncio
async def test_worker_auto_grants_stale_approval_when_capability_is_full_access(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run terminal step")
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective=task.objective,
            steps=[
                PlanStep(
                    title="Run terminal step",
                    description="A previously approval-gated step.",
                    required_capabilities=[Capability.TERMINAL_RUN],
                    risk_level=RiskLevel.MEDIUM,
                    requires_approval=True,
                    tool_name="terminal",
                )
            ],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TERMINAL_RUN: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )
    adapter = StaticToolAdapter({"terminal_output": [{"content": "done", "is_final": True, "exit_code": 0}]})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"terminal": adapter},
    )
    worker = TaskWorker(repos, audit, executor=executor)

    awaiting = await worker.process_task(task.id)
    running = await worker.process_task(task.id)
    completed = await worker.process_task(task.id)

    assert awaiting.status == TaskStatus.AWAITING_APPROVAL
    assert running.status == TaskStatus.RUNNING
    assert completed.status == TaskStatus.COMPLETED
    assert repos.approvals.list_for_task(task.id)[0].status == ApprovalStatus.APPROVED
    assert adapter.requests


class TransientFailureAdapter:
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.TIMEOUT,
            error_class=ErrorClass.TRANSIENT,
            error_message="temporary failure",
        )


class UsageLimitAdapter:
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.RATE_LIMITED,
            error_class=ErrorClass.USAGE_LIMITED,
            error_message="tool quota reached",
            output={"limit_state": {"limited": True, "kind": "usage"}},
        )


class FailingAdapter:
    def __init__(self, message: str) -> None:
        self.message = message

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.FAILED,
            error_class=ErrorClass.ADAPTER_FAILED,
            error_message=self.message,
        )


class RecordingNotifier:
    def __init__(self) -> None:
        self.tasks = []

    async def notify(self, task) -> None:
        self.tasks.append(task)


class RecordingAdapter:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.requests: list[ToolCallRequest] = []

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        self.requests.append(request)
        return await self.inner.execute(request)


@pytest.mark.asyncio
async def test_worker_does_not_treat_browser_url_as_preview_url(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Open a page")
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective=task.objective,
            steps=[
                PlanStep(
                    title="Open browser",
                    description="Open a browser page.",
                    required_capabilities=[Capability.BROWSER_OPEN],
                    risk_level=RiskLevel.LOW,
                    tool_name="browser.open",
                )
            ],
            postconditions=[
                PlanPostcondition(
                    type=PostconditionType.BROWSER_STATE,
                    description="Browser state is reported.",
                )
            ],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.BROWSER_OPEN: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.LOW,
            )
        },
    )
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"browser.open": StaticToolAdapter({"url": "https://example.com", "browser_url": "https://example.com"})},
    )
    worker = TaskWorker(repos, audit, executor=executor)

    latest = task
    for _ in range(3):
        latest = await worker.process_task(task.id)
        if latest.status == TaskStatus.COMPLETED:
            break

    assert latest.status == TaskStatus.COMPLETED
    assert latest.metadata["browser_url"] == "https://example.com"
    assert "preview_url" not in latest.metadata


@pytest.mark.asyncio
async def test_worker_marks_retrying_for_transient_failure(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run retry step")
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective="Run retry step",
            steps=[
                PlanStep(
                    title="Retryable",
                    description="A retryable step.",
                    required_capabilities=[Capability.LLM_GENERATE],
                    tool_name="llm",
                )
            ],
            success_criteria=["Step completed."],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.LLM_GENERATE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.LOW,
            )
        },
        limits={"max_retries": 1, "retry_backoff_seconds": 1},
    )
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": TransientFailureAdapter()},
    )
    worker = TaskWorker(repos, audit, executor=executor, retry_policy=RetryPolicy(settings.limits))

    running = await worker.process_task(task.id)
    retrying = await worker.process_task(running.id)

    assert retrying.status == TaskStatus.RETRYING
    assert retrying.metadata["retry_count"] == 1


@pytest.mark.asyncio
async def test_worker_blocks_usage_limited_tools_without_blind_retries(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run usage limited step")
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective="Run usage limited step",
            steps=[
                PlanStep(
                    title="Usage limited",
                    description="A usage-limited external tool.",
                    required_capabilities=[Capability.TERMINAL_RUN],
                    risk_level=RiskLevel.HIGH,
                    tool_name="coding.agent",
                )
            ],
            success_criteria=["Step completed or limit reported."],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TERMINAL_RUN: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
        limits={"max_retries": 3, "retry_backoff_seconds": 1},
    )
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"coding.agent": UsageLimitAdapter()},
    )
    worker = TaskWorker(repos, audit, executor=executor, retry_policy=RetryPolicy(settings.limits))

    running = await worker.process_task(task.id)
    blocked = await worker.process_task(running.id)

    assert blocked.status == TaskStatus.BLOCKED
    assert "retry_count" not in blocked.metadata
    assert "limit" in blocked.metadata["intervention_summary"].lower()


@pytest.mark.asyncio
async def test_worker_requeues_when_launch_request_lacks_preview_url(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Create a modern app and launch it")
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective=task.objective,
            steps=[
                PlanStep(
                    title="Answer only",
                    description="Returns text but no preview.",
                    required_capabilities=[Capability.LLM_GENERATE],
                    tool_name="llm",
                )
            ],
            success_criteria=["A preview exists."],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
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
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": StaticToolAdapter({"text": "code only"})},
    )
    worker = TaskWorker(repos, audit, executor=executor)

    await worker.process_task(task.id)
    requeued = await worker.process_task(task.id)

    assert requeued.status == TaskStatus.RECEIVED
    assert requeued.metadata["fulfillment_gap"] == "expected_preview_url_missing"
    assert requeued.metadata["fulfillment_missing"][0] == "preview_url"
    assert requeued.metadata["fulfillment_retry_count"] == 1


@pytest.mark.asyncio
async def test_worker_evaluator_attaches_recovery_plan_for_delivery_root_failure(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create(
        "Find me the file about invoices from my desktop and send it to me.",
        metadata={"original_message_text": "Find me the file about invoices from my desktop and send it to me."},
    )
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective=task.objective,
            steps=[
                PlanStep(
                    title="Broken delivery",
                    description="Attempt direct delivery with an invalid path.",
                    required_capabilities=[Capability.TELEGRAM_SEND],
                    risk_level=RiskLevel.LOW,
                    tool_name="artifact.deliver",
                    tool_input={"operation": "send_file", "path": "desktop/invoices.pdf"},
                )
            ],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
    settings = AppSettings(
        _env_file=None,
        adapters={"computer_use": {"enabled": True, "allowed_roots": [str(Path.home())]}},
        capabilities={
            Capability.TELEGRAM_SEND: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.LOW,
            ),
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
        },
    )
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"artifact.deliver": FailingAdapter("path is outside configured delivery roots")},
    )
    worker = TaskWorker(
        repos,
        audit,
        executor=executor,
        recovery_plan_factory=lambda item, reason: build_evaluator_recovery_plan(settings, item, reason),
    )

    await worker.process_task(task.id)
    repaired = await worker.process_task(task.id)
    repair_plan = repos.plans.get(repaired.plan_id or "")

    assert repaired.status == TaskStatus.PLANNED
    assert repaired.metadata["evaluator_repair_count"] == 1
    assert repaired.metadata["evaluator_repair_reason"] == "path is outside configured delivery roots"
    assert repair_plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in repair_plan.steps] == [
        "filesystem.manage:search",
        "artifact.deliver:send_file",
    ]


@pytest.mark.asyncio
async def test_worker_uses_explicit_plan_postconditions(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run a tool and prove it produced a preview")
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective=task.objective,
            steps=[
                PlanStep(
                    title="Answer only",
                    description="Returns text but no preview.",
                    required_capabilities=[Capability.LLM_GENERATE],
                    tool_name="llm",
                )
            ],
            postconditions=[
                PlanPostcondition(
                    type=PostconditionType.PREVIEW_URL,
                    description="A local preview URL is reported.",
                )
            ],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
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
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"llm": StaticToolAdapter({"text": "done"})},
    )
    worker = TaskWorker(repos, audit, executor=executor)

    await worker.process_task(task.id)
    requeued = await worker.process_task(task.id)

    assert requeued.status == TaskStatus.RECEIVED
    assert requeued.metadata["fulfillment_gap"] == "expected_preview_url_missing"
    assert requeued.metadata["fulfillment_expected"][0]["type"] == "preview_url"


@pytest.mark.asyncio
async def test_worker_default_vscode_development_plan_runs_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Ask Copilot to inspect the failing test", metadata={"task_type": TaskType.DEVELOPMENT.value})
    settings = AppSettings(
        _env_file=None,
        adapters={"vscode": {"enabled": True}},
        capabilities={
            Capability.VSCODE_WRITE_FILES: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )
    adapter = StaticToolAdapter()
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"vscode.copilot_terminal": adapter},
    )
    worker = TaskWorker(
        repos,
        audit,
        executor=executor,
        default_plan_factory=lambda item: build_default_vscode_development_plan(settings, item),
    )

    running = await worker.process_task(task.id)
    completed = await worker.process_task(task.id)

    assert running.status == TaskStatus.RUNNING
    assert completed.status == TaskStatus.COMPLETED
    assert adapter.requests[0].tool_name == "vscode.copilot_terminal"
    assert adapter.requests[0].capability == Capability.VSCODE_WRITE_FILES


@pytest.mark.asyncio
async def test_worker_default_vscode_plan_prepares_workspace_when_copilot_is_explicit(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Use GitHub Copilot to write a small Python script", metadata={"task_type": TaskType.DEVELOPMENT.value})
    settings = AppSettings(
        _env_file=None,
        adapters={
            "workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces"), "open_browser": False},
            "vscode": {"enabled": True},
        },
        capabilities={
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
            Capability.VSCODE_WRITE_FILES: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
        },
    )
    workspace_adapter = StaticToolAdapter({"workspace_dir": str(tmp_path / "workspaces" / task.id)})
    vscode_adapter = StaticToolAdapter({"text": "done"})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"workspace.manage": workspace_adapter, "vscode.copilot_terminal": vscode_adapter},
    )
    worker = TaskWorker(
        repos,
        audit,
        executor=executor,
        default_plan_factory=lambda item: build_default_vscode_development_plan(settings, item),
    )

    await worker.process_task(task.id)
    await worker.process_task(task.id)
    completed = await worker.process_task(task.id)

    assert completed.status == TaskStatus.COMPLETED
    assert workspace_adapter.requests[0].input["operation"] == "prepare"
    assert vscode_adapter.requests[0].input["cwd"].endswith(task.id)


@pytest.mark.asyncio
async def test_worker_default_web_app_plan_uses_workspace_adapter(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create(
        "Create a simple hello world web app and launch it",
        metadata={"task_type": TaskType.DEVELOPMENT.value},
    )
    settings = AppSettings(
        _env_file=None,
        adapters={
            "workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces"), "open_browser": False},
            "vscode": {"enabled": False},
        },
        capabilities={
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
            Capability.VSCODE_WRITE_FILES: CapabilityPolicy(
                enabled=False,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
        },
    )
    adapter = StaticToolAdapter({"url": "http://127.0.0.1:8890/", "workspace_dir": str(tmp_path / "workspaces")})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"workspace.manage": adapter},
    )
    worker = TaskWorker(
        repos,
        audit,
        executor=executor,
        default_plan_factory=lambda item: build_default_vscode_development_plan(settings, item),
    )

    running = await worker.process_task(task.id)
    completed = await worker.process_task(task.id)

    assert running.status == TaskStatus.RUNNING
    assert completed.status == TaskStatus.COMPLETED
    assert adapter.requests[0].tool_name == "workspace.manage"
    assert adapter.requests[0].input["operation"] == "web_app_preview"
    assert adapter.requests[0].capability == Capability.FILESYSTEM_WRITE


@pytest.mark.asyncio
async def test_worker_launchable_app_request_uses_preview_even_without_web_keyword(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create(
        "Create a modern app about ferrets and launch it",
        metadata={"task_type": TaskType.DEVELOPMENT.value},
    )
    settings = AppSettings(
        _env_file=None,
        adapters={
            "workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces"), "open_browser": False},
            "vscode": {"enabled": False},
        },
        capabilities={
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
            Capability.VSCODE_WRITE_FILES: CapabilityPolicy(
                enabled=False,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
        },
    )
    adapter = StaticToolAdapter({"url": "http://127.0.0.1:8890/", "workspace_dir": str(tmp_path / "workspaces")})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"workspace.manage": adapter},
    )
    worker = TaskWorker(
        repos,
        audit,
        executor=executor,
        default_plan_factory=lambda item: build_default_vscode_development_plan(settings, item),
    )

    running = await worker.process_task(task.id)
    completed = await worker.process_task(task.id)

    assert running.status == TaskStatus.RUNNING
    assert completed.status == TaskStatus.COMPLETED
    assert adapter.requests[0].input["operation"] == "web_app_preview"


@pytest.mark.asyncio
async def test_worker_launchable_app_request_does_not_use_copilot_unless_explicit(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create(
        "Create a modern app about ferrets and launch it",
        metadata={"task_type": TaskType.DEVELOPMENT.value},
    )
    settings = AppSettings(
        _env_file=None,
        adapters={
            "workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces"), "open_browser": False},
            "vscode": {"enabled": True},
        },
        capabilities={
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
            Capability.VSCODE_WRITE_FILES: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
        },
    )
    workspace_adapter = StaticToolAdapter({"url": "http://127.0.0.1:8890/", "workspace_dir": str(tmp_path / "workspaces")})
    vscode_adapter = StaticToolAdapter({"text": "should not be called"})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"workspace.manage": workspace_adapter, "vscode.copilot_terminal": vscode_adapter},
    )
    worker = TaskWorker(
        repos,
        audit,
        executor=executor,
        default_plan_factory=lambda item: build_default_vscode_development_plan(settings, item),
    )

    await worker.process_task(task.id)
    completed = await worker.process_task(task.id)
    plan_event = next(event for event in repos.audit.list_for_task(task.id) if event.type.value == "plan_created")

    assert completed.status == TaskStatus.COMPLETED
    assert workspace_adapter.requests[0].input["operation"] == "web_app_preview"
    assert vscode_adapter.requests == []
    assert plan_event.payload["route_decision"]["external_agent_skipped"] == [
        "codex_and_github_copilot_not_used_without_explicit_user_request"
    ]


@pytest.mark.asyncio
async def test_worker_launchable_app_request_uses_copilot_then_workspace_preview_when_explicit(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create(
        "Use GitHub Copilot to create a modern app about ferrets and launch it",
        metadata={"task_type": TaskType.DEVELOPMENT.value},
    )
    settings = AppSettings(
        _env_file=None,
        adapters={
            "workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces"), "open_browser": False},
            "vscode": {"enabled": True},
        },
        capabilities={
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
            Capability.VSCODE_WRITE_FILES: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            ),
        },
    )
    workspace_adapter = RecordingAdapter(
        LocalWorkspaceAdapter(
            WorkspaceAdapterConfig(root_dir=str(tmp_path / "workspaces"), web_port_start=8890, open_browser=False)
        )
    )
    vscode_adapter = StaticToolAdapter(
        {
            "usage": {"requests": "Requests  1 Premium", "tokens": "Tokens    10"},
            "text": """```html filename=index.html
<!doctype html><html><body><h1>Modern Ferrets</h1></body></html>
```"""
        }
    )
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"workspace.manage": workspace_adapter, "vscode.copilot_terminal": vscode_adapter},
    )
    worker = TaskWorker(
        repos,
        audit,
        executor=executor,
        default_plan_factory=lambda item: build_default_vscode_development_plan(settings, item),
    )

    latest = task
    for _ in range(6):
        latest = await worker.process_task(task.id)
        if latest.status == TaskStatus.COMPLETED:
            break

    workspace = Path(tmp_path / "workspaces" / task.id)
    operations = [request.input["operation"] for request in workspace_adapter.requests]
    assert latest.status == TaskStatus.COMPLETED
    assert operations == ["prepare", "materialize_static_app", "launch_static"]
    assert vscode_adapter.requests[0].tool_name == "vscode.copilot_terminal"
    assert vscode_adapter.requests[0].input["cwd"].endswith(task.id)
    assert vscode_adapter.requests[0].input["require_file_blocks"] is True
    assert workspace_adapter.requests[1].input["allow_fallback_template"] is True
    assert workspace_adapter.requests[1].input["require_index"] is True
    assert workspace_adapter.requests[2].input["ensure_index"] is False
    assert "Modern Ferrets" in workspace_adapter.requests[1].input["source_text"]
    assert "Modern Ferrets" in (workspace / "index.html").read_text(encoding="utf-8")
    assert latest.metadata["preview_url"].startswith("http://127.0.0.1:")
    assert latest.metadata["last_copilot_usage"]["requests"] == "Requests  1 Premium"

    _stop_process(int(latest.metadata["server_pid"]))


@pytest.mark.asyncio
async def test_worker_notifies_once_on_completion(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("Run safe step", metadata={"source_chat_id": "100"})
    plan = repos.plans.create(
        task.id,
        PlanModel(
            objective="Run safe step",
            steps=[
                PlanStep(
                    title="Summarize",
                    description="Run a safe LLM step.",
                    required_capabilities=[Capability.LLM_GENERATE],
                    tool_name="llm",
                )
            ],
            success_criteria=["Step completed."],
        ),
    )
    repos.tasks.attach_plan(task.id, plan.id)
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
    worker = TaskWorker(repos, audit, executor=executor, notification_sink=notifier)

    await worker.process_next()
    await worker.process_next()
    await worker.process_next()

    assert [item.status for item in notifier.tasks] == [TaskStatus.RUNNING, TaskStatus.COMPLETED]
    assert repos.tasks.get(task.id).metadata["notified_statuses"] == [
        TaskStatus.COMPLETED.value,
        TaskStatus.RUNNING.value,
    ]


def _stop_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)
