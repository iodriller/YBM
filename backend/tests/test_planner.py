from __future__ import annotations

import pytest

from agent_control.llm import PlannerService, StaticPlanProvider
from agent_control.schemas import Capability, PlanModel, PlanStep, TaskStatus
from agent_control.storage import AuditLogger, Database, Repositories


@pytest.mark.asyncio
async def test_planner_generates_and_persists_plan(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    task = repos.tasks.create("Build a todo app")
    provider = StaticPlanProvider(
        PlanModel(
            objective="Build a todo app",
            required_capabilities=[Capability.LLM_GENERATE],
            steps=[
                PlanStep(
                    title="Clarify scope",
                    description="Summarize requirements and identify missing details.",
                    required_capabilities=[Capability.LLM_GENERATE],
                )
            ],
            success_criteria=["Plan is ready for approval."],
        )
    )
    planner = PlannerService(provider, repos, audit)

    plan = await planner.plan_task(task.id)
    updated = repos.tasks.get(task.id)
    persisted = repos.plans.get(plan.id)

    assert updated is not None
    assert updated.status == TaskStatus.PLANNED
    assert updated.plan_id == plan.id
    assert persisted == plan
