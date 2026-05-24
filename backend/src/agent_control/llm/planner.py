from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt
from agent_control.schemas import AuditEventType, PlanModel, TaskStatus
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories


PLANNER_SYSTEM_PROMPT = prompt_text("base/planner_system.md")

# Keywords that indicate a complex/major task needing a larger LLM context window
_MAJOR_TASK_KEYWORDS = (
    "write code", "write a script", "generate report", "automate",
    "excel", "create a script", "build a script", "step by step",
    "research and", "analyze and",
)


def _is_major_task(objective: str) -> bool:
    lowered = objective.lower()
    return any(kw in lowered for kw in _MAJOR_TASK_KEYWORDS)


class PlannerService:
    def __init__(
        self,
        provider: LLMProvider,
        repositories: Repositories,
        audit: AuditLogger,
        plan_validator: Callable[[PlanModel], PlanModel] | None = None,
        major_provider: LLMProvider | None = None,
    ) -> None:
        self.provider = provider
        self.repositories = repositories
        self.audit = audit
        self.plan_validator = plan_validator
        self.major_provider = major_provider

    async def plan_task(self, task_id: str, config_context: str = "No extra capability context provided.") -> PlanModel:
        task = self.repositories.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")

        self.repositories.tasks.update_status(task_id, TaskStatus.INTERPRETING)
        self.audit.task_state_changed("planner", task_id, task.status, TaskStatus.INTERPRETING)

        # Use the major provider for complex tasks if configured
        provider = self.provider
        if self.major_provider is not None and _is_major_task(task.objective):
            provider = self.major_provider

        # Use enriched objective during replanning (includes error context from failed attempt)
        objective = str(task.metadata.get("replan_objective") or task.objective).strip()
        original_text = str(task.metadata.get("original_message_text") or "").strip()
        if original_text and original_text != objective:
            objective = (
                f"User's original message (preserve exact wording, language, URLs, and section names):\n"
                f"{original_text}\n\n"
                f"Normalized objective: {objective}"
            )
        raw_memory = str(task.metadata.get("memory_context") or "").strip()
        memory_section = f"## Conversation context\n{raw_memory}\n\n" if raw_memory else ""
        user_prompt = self._prompt(objective, config_context, memory_section)
        plan: PlanModel | None = None
        last_error: Exception | None = None
        current_prompt = user_prompt
        for attempt in range(3):
            try:
                candidate = await provider.generate_structured(PLANNER_SYSTEM_PROMPT, current_prompt, PlanModel)
                plan = self._validate_plan(candidate)
                break
            except (ValueError, ValidationError) as exc:
                last_error = exc
                current_prompt = render_prompt(
                    "tasks/structured_retry.md",
                    original_prompt=user_prompt,
                    error=str(exc)[:2000],
                )
        if plan is None:
            assert last_error is not None
            raise last_error

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
                    "used_major_provider": provider is not self.provider,
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
    def _prompt(objective: str, config_context: str, memory_context: str = "") -> str:
        return render_prompt(
            "tasks/planner_user.md",
            objective=objective,
            config_context=config_context,
            memory_context=memory_context,
        )
