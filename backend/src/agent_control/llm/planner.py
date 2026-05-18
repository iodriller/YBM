from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt
from agent_control.schemas import AuditEventType, PlanModel, TaskStatus
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories


PLANNER_SYSTEM_PROMPT = prompt_text("base/planner_system.md")


class PlannerService:
    def __init__(
        self,
        provider: LLMProvider,
        repositories: Repositories,
        audit: AuditLogger,
        plan_validator: Callable[[PlanModel], PlanModel] | None = None,
    ) -> None:
        self.provider = provider
        self.repositories = repositories
        self.audit = audit
        self.plan_validator = plan_validator

    async def plan_task(self, task_id: str, config_context: str = "No extra capability context provided.") -> PlanModel:
        task = self.repositories.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")

        self.repositories.tasks.update_status(task_id, TaskStatus.INTERPRETING)
        self.audit.task_state_changed("planner", task_id, task.status, TaskStatus.INTERPRETING)

        user_prompt = self._prompt(task.objective, config_context)
        try:
            plan = await self.provider.generate_structured(PLANNER_SYSTEM_PROMPT, user_prompt, PlanModel)
            plan = self._validate_plan(plan)
        except (ValueError, ValidationError) as exc:
            retry_prompt = render_prompt(
                "tasks/structured_retry.md",
                original_prompt=user_prompt,
                error=exc,
            )
            plan = await self.provider.generate_structured(PLANNER_SYSTEM_PROMPT, retry_prompt, PlanModel)
            plan = self._validate_plan(plan)

        self.repositories.plans.create(task_id, plan)
        updated = self.repositories.tasks.attach_plan(task_id, plan.id, TaskStatus.PLANNED)
        self.audit.append(
            AuditEventType.PLAN_CREATED,
            actor="planner",
            task_id=task_id,
            payload={
                "plan_id": plan.id,
                "step_count": len(plan.steps),
                "required_capabilities": [capability.value for capability in plan.required_capabilities],
                "llm": {
                    "system_prompt": PLANNER_SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                    "config_context": config_context,
                },
                "plan": plan.model_dump(mode="json"),
            },
        )
        self.audit.task_state_changed("planner", task_id, TaskStatus.INTERPRETING, updated.status)
        return plan

    def _validate_plan(self, plan: PlanModel) -> PlanModel:
        if self.plan_validator is None:
            return plan
        return self.plan_validator(plan)

    @staticmethod
    def _prompt(objective: str, config_context: str) -> str:
        return render_prompt("tasks/planner_user.md", objective=objective, config_context=config_context)
