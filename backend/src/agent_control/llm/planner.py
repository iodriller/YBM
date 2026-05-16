from __future__ import annotations

from pydantic import ValidationError

from agent_control.llm.providers import LLMProvider
from agent_control.schemas import AuditEventType, PlanModel, TaskStatus
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories


PLANNER_SYSTEM_PROMPT = """You are the planning layer for a local agentic control system.
Return only structured JSON matching the requested schema.
Plans must be conservative, permission-aware, and split into concrete steps.
Do not assume access to terminal, files, VS Code, desktop, browser, or GitHub unless listed by configuration context."""


class PlannerService:
    def __init__(self, provider: LLMProvider, repositories: Repositories, audit: AuditLogger) -> None:
        self.provider = provider
        self.repositories = repositories
        self.audit = audit

    async def plan_task(self, task_id: str, config_context: str = "No extra capability context provided.") -> PlanModel:
        task = self.repositories.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")

        self.repositories.tasks.update_status(task_id, TaskStatus.INTERPRETING)
        self.audit.task_state_changed("planner", task_id, task.status, TaskStatus.INTERPRETING)

        user_prompt = self._prompt(task.objective, config_context)
        try:
            plan = await self.provider.generate_structured(PLANNER_SYSTEM_PROMPT, user_prompt, PlanModel)
        except (ValueError, ValidationError) as exc:
            retry_prompt = f"{user_prompt}\n\nPrevious structured output error:\n{exc}\nReturn corrected JSON only."
            plan = await self.provider.generate_structured(PLANNER_SYSTEM_PROMPT, retry_prompt, PlanModel)

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
            },
        )
        self.audit.task_state_changed("planner", task_id, TaskStatus.INTERPRETING, updated.status)
        return plan

    @staticmethod
    def _prompt(objective: str, config_context: str) -> str:
        return f"""Create an execution plan for this objective:

{objective}

Configuration/capability context:
{config_context}

The plan must include assumptions, required_capabilities, approval_gates when needed, ordered steps, and success_criteria."""
