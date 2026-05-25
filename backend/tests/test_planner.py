from __future__ import annotations

import pytest

from agent_control.llm import PlannerService, StaticPlanProvider
from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.schemas import Capability, PlanModel, PlanStep, RiskLevel, TaskStatus
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.registry import build_tool_registry


class QueuePlanProvider:
    def __init__(self, plans: list[PlanModel]) -> None:
        self.plans = plans
        self.prompts: list[tuple[str, str]] = []

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return self.plans[0].model_dump_json()

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model, **_ignored_kwargs):
        self.prompts.append((system_prompt, user_prompt))
        return output_model.model_validate(self.plans.pop(0).model_dump(mode="json"))


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


@pytest.mark.asyncio
async def test_planner_repairs_llm_plan_against_registry_before_persisting(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    task = repos.tasks.create("Create a web app and launch it")
    settings = AppSettings(
        _env_file=None,
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
    provider = QueuePlanProvider(
        [
            PlanModel(
                objective=task.objective,
                steps=[
                    PlanStep(
                        title="Bad launch",
                        description="Invalid port should be repaired before persistence.",
                        tool_name="workspace.manage",
                        tool_input={"operation": "launch_static", "web_port_start": "bad"},
                    )
                ],
            ),
            PlanModel(
                objective=task.objective,
                steps=[
                    PlanStep(
                        title="Launch",
                        description="Launch a static preview.",
                        tool_name="workspace.manage",
                        tool_input={"operation": "launch_static", "web_port_start": 8890},
                    )
                ],
            ),
        ]
    )
    planner = PlannerService(provider, repos, audit, plan_validator=registry.validate_plan)

    plan = await planner.plan_task(task.id)
    persisted = repos.plans.get(plan.id)

    assert len(provider.prompts) == 2
    assert "failed registry validation" in provider.prompts[1][1]
    assert persisted is not None
    assert persisted.steps[0].tool_input["web_port_start"] == 8890
    assert persisted.steps[0].required_capabilities == [Capability.FILESYSTEM_WRITE]
    assert repos.tasks.get(task.id).status == TaskStatus.PLANNED
