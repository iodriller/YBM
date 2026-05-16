from __future__ import annotations

import pytest

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.llm import PlannerService, StaticPlanProvider
from agent_control.orchestration import StaticToolAdapter, TaskWorker, ToolExecutor
from agent_control.policy import PolicyEngine
from agent_control.schemas import ApprovalStatus, Capability, PlanModel, PlanStep, RiskLevel, TaskStatus
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
