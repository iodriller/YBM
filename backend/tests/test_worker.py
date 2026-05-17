from __future__ import annotations

import pytest

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.llm import PlannerService, StaticPlanProvider
from agent_control.orchestration import StaticToolAdapter, TaskWorker, ToolExecutor
from agent_control.orchestration.default_plans import build_default_vscode_development_plan
from agent_control.policy import PolicyEngine
from agent_control.recovery import RetryPolicy
from agent_control.schemas import (
    ApprovalStatus,
    Capability,
    ErrorClass,
    PlanModel,
    PlanStep,
    RiskLevel,
    TaskStatus,
    TaskType,
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
    adapter = StaticToolAdapter()
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


class TransientFailureAdapter:
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.TIMEOUT,
            error_class=ErrorClass.TRANSIENT,
            error_message="temporary failure",
        )


class RecordingNotifier:
    def __init__(self) -> None:
        self.tasks = []

    async def notify(self, task) -> None:
        self.tasks.append(task)


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
async def test_worker_default_vscode_development_plan_runs_when_enabled(tmp_path) -> None:
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
async def test_worker_default_web_app_plan_uses_workspace_adapter(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create(
        "Create a simple hello world web app and launch it",
        metadata={"task_type": TaskType.DEVELOPMENT.value},
    )
    settings = AppSettings(
        _env_file=None,
        adapters={"workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces"), "open_browser": False}},
        capabilities={
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )
    adapter = StaticToolAdapter({"url": "http://127.0.0.1:8890/", "workspace_dir": str(tmp_path / "workspaces")})
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters={"workspace.web_app": adapter},
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
    assert adapter.requests[0].tool_name == "workspace.web_app"
    assert adapter.requests[0].capability == Capability.FILESYSTEM_WRITE


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

    assert len(notifier.tasks) == 1
    assert notifier.tasks[0].status == TaskStatus.COMPLETED
    assert repos.tasks.get(task.id).metadata["notified_statuses"] == [TaskStatus.COMPLETED.value]
