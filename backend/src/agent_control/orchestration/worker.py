from __future__ import annotations

from datetime import timedelta

from agent_control.llm import PlannerService
from agent_control.orchestration.executor import ToolExecutor
from agent_control.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEventType,
    PlanStep,
    TaskRecord,
    TaskStatus,
    ToolCallRequest,
    ToolResultStatus,
    utc_now,
)
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories


class TaskWorker:
    def __init__(
        self,
        repositories: Repositories,
        audit: AuditLogger,
        planner: PlannerService | None = None,
        executor: ToolExecutor | None = None,
    ) -> None:
        self.repositories = repositories
        self.audit = audit
        self.planner = planner
        self.executor = executor

    async def process_task(self, task_id: str) -> TaskRecord:
        task = self.repositories.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")

        if task.status == TaskStatus.RECEIVED:
            if self.planner is None:
                return task
            await self.planner.plan_task(task_id)
            task = self.repositories.tasks.get(task_id)
            if task is None:
                raise KeyError(f"task not found after planning: {task_id}")

        if task.status == TaskStatus.PLANNED:
            return await self._process_planned(task)

        if task.status == TaskStatus.RUNNING:
            return await self._process_running(task)

        if task.status == TaskStatus.AWAITING_APPROVAL:
            return await self._process_awaiting_approval(task)

        return task

    async def _process_planned(self, task: TaskRecord) -> TaskRecord:
        if not task.plan_id:
            return self._transition(task, TaskStatus.BLOCKED, "planned_task_missing_plan")
        plan = self.repositories.plans.get(task.plan_id)
        if plan is None:
            return self._transition(task, TaskStatus.BLOCKED, "plan_not_found")

        approval_steps = [step for step in plan.steps if step.requires_approval]
        if approval_steps:
            self.repositories.tasks.set_current_step(task.id, approval_steps[0].id)
            for step in approval_steps:
                self._create_step_approval(task, step)
            return self._transition(task, TaskStatus.AWAITING_APPROVAL, "approval_required")

        runnable_steps = [step for step in plan.steps if step.tool_name]
        if not runnable_steps:
            return self._transition(task, TaskStatus.COMPLETED, "plan_only_task_completed")

        self.repositories.tasks.set_current_step(task.id, runnable_steps[0].id)
        return self._transition(task, TaskStatus.RUNNING, "ready_to_execute")

    async def _process_running(self, task: TaskRecord) -> TaskRecord:
        if self.executor is None:
            return self._transition(task, TaskStatus.BLOCKED, "executor_not_configured")
        if not task.plan_id:
            return self._transition(task, TaskStatus.BLOCKED, "running_task_missing_plan")
        plan = self.repositories.plans.get(task.plan_id)
        if plan is None:
            return self._transition(task, TaskStatus.BLOCKED, "plan_not_found")

        step = next((item for item in plan.steps if item.id == task.current_step_id), None)
        if step is None:
            return self._transition(task, TaskStatus.COMPLETED, "no_current_step")
        if not step.tool_name:
            return self._transition(task, TaskStatus.BLOCKED, "current_step_missing_tool")
        if not step.required_capabilities:
            return self._transition(task, TaskStatus.BLOCKED, "current_step_missing_capability")

        request = ToolCallRequest(
            task_id=task.id,
            tool_name=step.tool_name,
            capability=step.required_capabilities[0],
            risk_level=step.risk_level,
            input=step.tool_input,
            requires_approval=step.requires_approval,
        )
        step_approved = self._step_is_approved(task.id, step.id)
        result = await self.executor.execute(request, approved=step_approved)
        if result.status == ToolResultStatus.SUCCEEDED:
            next_step = self._next_runnable_step(plan.steps, step.id)
            if next_step is None:
                self.repositories.tasks.set_current_step(task.id, None)
                return self._transition(task, TaskStatus.COMPLETED, "all_steps_completed")
            self.repositories.tasks.set_current_step(task.id, next_step.id)
            return self.repositories.tasks.get(task.id) or task
        if result.status == ToolResultStatus.NEEDS_APPROVAL:
            return self._transition(task, TaskStatus.AWAITING_APPROVAL, "tool_approval_required")
        if result.status == ToolResultStatus.DENIED:
            return self._transition(task, TaskStatus.BLOCKED, "tool_policy_denied")
        return self._transition(task, TaskStatus.FAILED, "tool_failed")

    async def _process_awaiting_approval(self, task: TaskRecord) -> TaskRecord:
        approvals = self.repositories.approvals.list_for_task(task.id)
        if not approvals:
            return self._transition(task, TaskStatus.BLOCKED, "awaiting_approval_without_request")
        terminal_denials = {ApprovalStatus.REJECTED, ApprovalStatus.CANCELLED, ApprovalStatus.EXPIRED}
        if any(approval.status in terminal_denials for approval in approvals):
            return self._transition(task, TaskStatus.BLOCKED, "approval_not_granted")
        if any(approval.status == ApprovalStatus.PENDING for approval in approvals):
            return task
        if not task.plan_id:
            return self._transition(task, TaskStatus.BLOCKED, "approved_task_missing_plan")
        plan = self.repositories.plans.get(task.plan_id)
        if plan is None:
            return self._transition(task, TaskStatus.BLOCKED, "approved_plan_not_found")
        if task.current_step_id is None:
            next_step = next((step for step in plan.steps if step.tool_name), None)
            if next_step is None:
                return self._transition(task, TaskStatus.COMPLETED, "approved_plan_only_task_completed")
            self.repositories.tasks.set_current_step(task.id, next_step.id)
        return self._transition(task, TaskStatus.RUNNING, "approval_granted")

    def _create_step_approval(self, task: TaskRecord, step: PlanStep) -> None:
        if not step.required_capabilities:
            return

        approval = ApprovalRequest(
            task_id=task.id,
            capability=step.required_capabilities[0],
            risk_level=step.risk_level,
            summary=step.title,
            action_payload=step.model_dump(mode="json"),
            expires_at=utc_now() + timedelta(minutes=15),
        )
        self.repositories.approvals.create(approval)
        self.audit.append(
            AuditEventType.APPROVAL_REQUESTED,
            actor="orchestrator",
            task_id=task.id,
            payload={"approval_id": approval.id, "step_id": step.id},
        )

    def _step_is_approved(self, task_id: str, step_id: str) -> bool:
        return any(
            approval.status == ApprovalStatus.APPROVED and approval.action_payload.get("id") == step_id
            for approval in self.repositories.approvals.list_for_task(task_id)
        )

    @staticmethod
    def _next_runnable_step(steps: list[PlanStep], current_step_id: str) -> PlanStep | None:
        found = False
        for step in steps:
            if found and step.tool_name:
                return step
            if step.id == current_step_id:
                found = True
        return None

    def _transition(self, task: TaskRecord, status: TaskStatus, reason: str) -> TaskRecord:
        updated = self.repositories.tasks.update_status(task.id, status)
        self.audit.task_state_changed("orchestrator", task.id, task.status, updated.status)
        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor="orchestrator",
            task_id=task.id,
            payload={"reason": reason, "status": status.value},
        )
        return updated
