from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import json
from typing import Any, Protocol

from agent_control.llm import PlannerService
from agent_control.orchestration.executor import ToolExecutor
from agent_control.orchestration.fulfillment import validate_fulfillment
from agent_control.recovery import RetryPolicy
from agent_control.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEventType,
    PlanModel,
    PlanStep,
    TaskRecord,
    TaskStatus,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
    utc_now,
)
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories

DefaultPlanFactory = Callable[[TaskRecord], PlanModel | None]


class TaskNotificationSink(Protocol):
    async def notify(self, task: TaskRecord) -> None:
        ...

WORKABLE_STATUSES = [
    TaskStatus.RECEIVED,
    TaskStatus.INTERPRETING,
    TaskStatus.PLANNED,
    TaskStatus.AWAITING_APPROVAL,
    TaskStatus.RUNNING,
    TaskStatus.RETRYING,
]

NOTIFIABLE_STATUSES = {
    TaskStatus.AWAITING_APPROVAL,
    TaskStatus.BLOCKED,
    TaskStatus.CANCELLED,
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
}


class TaskWorker:
    def __init__(
        self,
        repositories: Repositories,
        audit: AuditLogger,
        planner: PlannerService | None = None,
        executor: ToolExecutor | None = None,
        retry_policy: RetryPolicy | None = None,
        config_context: str = "No extra capability context provided.",
        default_plan_factory: DefaultPlanFactory | None = None,
        notification_sink: TaskNotificationSink | None = None,
    ) -> None:
        self.repositories = repositories
        self.audit = audit
        self.planner = planner
        self.executor = executor
        self.retry_policy = retry_policy
        self.config_context = config_context
        self.default_plan_factory = default_plan_factory
        self.notification_sink = notification_sink

    async def process_next(self) -> TaskRecord | None:
        tasks = self.repositories.tasks.list_by_statuses(WORKABLE_STATUSES, limit=1)
        if not tasks:
            return None
        try:
            processed = await self.process_task(tasks[0].id)
            await self._notify_if_needed(processed)
            return processed
        except Exception as exc:
            latest = self.repositories.tasks.get(tasks[0].id)
            if latest is None:
                raise
            metadata = {**latest.metadata, "last_worker_error": str(exc)}
            failed = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.FAILED)
            self.audit.task_state_changed("worker", latest.id, latest.status, failed.status)
            self.audit.append(
                AuditEventType.ERROR,
                actor="worker",
                task_id=latest.id,
                payload={"error": str(exc), "status": failed.status.value},
            )
            await self._notify_if_needed(failed)
            return failed

    async def run_forever(self, poll_interval_seconds: float = 3.0) -> None:
        while True:
            processed = await self.process_next()
            if processed is None:
                await asyncio.sleep(poll_interval_seconds)

    async def process_task(self, task_id: str) -> TaskRecord:
        task = self.repositories.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")

        if task.status in {TaskStatus.RECEIVED, TaskStatus.INTERPRETING}:
            default_plan = self.default_plan_factory(task) if self.default_plan_factory else None
            if default_plan is not None:
                self.repositories.plans.create(task_id, default_plan)
                updated = self.repositories.tasks.attach_plan(task_id, default_plan.id, TaskStatus.PLANNED)
                self.audit.append(
                    AuditEventType.PLAN_CREATED,
                    actor="worker",
                    task_id=task_id,
                    payload={
                        "plan_id": default_plan.id,
                        "step_count": len(default_plan.steps),
                        "required_capabilities": [
                            capability.value for capability in default_plan.required_capabilities
                        ],
                        "source": "default_vscode_development_plan",
                        "config_context": self.config_context,
                        "plan": default_plan.model_dump(mode="json"),
                    },
                )
                self.audit.task_state_changed("worker", task_id, task.status, updated.status)
            elif self.planner is None:
                return task
            else:
                await self.planner.plan_task(task_id, self.config_context)
            task = self.repositories.tasks.get(task_id)
            if task is None:
                raise KeyError(f"task not found after planning: {task_id}")

        if task.status == TaskStatus.PLANNED:
            return await self._process_planned(task)

        if task.status == TaskStatus.RUNNING:
            return await self._process_running(task)

        if task.status == TaskStatus.AWAITING_APPROVAL:
            return await self._process_awaiting_approval(task)

        if task.status == TaskStatus.RETRYING:
            return self._process_retrying(task)

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

        latest = self.repositories.tasks.get(task.id)
        if latest is None or latest.status in {TaskStatus.PAUSED, TaskStatus.CANCELLED}:
            return latest or task

        resolved_input = _resolve_step_input(latest, step.tool_input)
        request = ToolCallRequest(
            task_id=task.id,
            tool_name=step.tool_name,
            capability=step.required_capabilities[0],
            risk_level=step.risk_level,
            scope_target=resolved_input.get("scope_target"),
            input=resolved_input,
            timeout_seconds=int(resolved_input.get("timeout_seconds", 60)),
            requires_approval=step.requires_approval,
        )
        step_approved = self._step_is_approved(task.id, step.id)
        result = await self.executor.execute(request, approved=step_approved)
        task = self._record_tool_result(task.id, step.tool_name, result)
        latest = self.repositories.tasks.get(task.id)
        if latest is None or latest.status in {TaskStatus.PAUSED, TaskStatus.CANCELLED}:
            return latest or task
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
        retry = self._retry_decision(task, result)
        if retry:
            return retry
        return self._transition(task, TaskStatus.FAILED, "tool_failed")

    def _process_retrying(self, task: TaskRecord) -> TaskRecord:
        next_retry_at = task.metadata.get("next_retry_at")
        if next_retry_at and datetime.fromisoformat(next_retry_at) > utc_now():
            return task
        return self._transition(task, TaskStatus.RUNNING, "retry_due")

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

    def _retry_decision(self, task: TaskRecord, result: ToolCallResult) -> TaskRecord | None:
        if self.retry_policy is None:
            return None
        current_retry_count = int(task.metadata.get("retry_count", 0))
        decision = self.retry_policy.evaluate(result, current_retry_count)
        if not decision.retry:
            if decision.reason == "retry_limit_reached":
                metadata = {
                    **task.metadata,
                    "retry_count": decision.retry_count,
                    "intervention_summary": self.retry_policy.intervention_summary(result),
                }
                return self.repositories.tasks.update_metadata(task.id, metadata, TaskStatus.BLOCKED)
            return None
        metadata = {
            **task.metadata,
            "retry_count": decision.retry_count,
            "last_retry_reason": decision.reason,
            "next_retry_at": decision.next_retry_at,
        }
        updated = self.repositories.tasks.update_metadata(task.id, metadata, TaskStatus.RETRYING)
        self.audit.task_state_changed("orchestrator", task.id, task.status, updated.status)
        return updated

    async def _notify_if_needed(self, task: TaskRecord) -> None:
        if self.notification_sink is None or task.status not in NOTIFIABLE_STATUSES:
            return
        notified = set(task.metadata.get("notified_statuses", []))
        if task.status.value in notified:
            return
        await self.notification_sink.notify(task)
        latest = self.repositories.tasks.get(task.id)
        if latest is None:
            return
        updated_notified = sorted({*latest.metadata.get("notified_statuses", []), task.status.value})
        self.repositories.tasks.update_metadata(
            task.id,
            {**latest.metadata, "notified_statuses": updated_notified},
        )

    def _record_tool_result(self, task_id: str, tool_name: str, result: ToolCallResult) -> TaskRecord:
        latest = self.repositories.tasks.get(task_id)
        if latest is None:
            raise KeyError(f"task not found: {task_id}")
        metadata = {
            **latest.metadata,
            "last_tool_name": tool_name,
            "last_tool_result": _trim_result(result),
        }
        output_text = _tool_output_text(result)
        if output_text:
            metadata["last_tool_output_text"] = output_text if len(output_text) <= 20000 else f"{output_text[:19997]}..."
        output = result.output if isinstance(result.output, dict) else {}
        usage = output.get("usage")
        if isinstance(usage, dict) and usage:
            metadata["last_tool_usage"] = usage
            if "copilot" in tool_name:
                metadata["last_copilot_usage"] = usage
        for metadata_key, output_key in (
            ("workspace_dir", "workspace_dir"),
            ("server_pid", "server_pid"),
            ("adapter_dir", "adapter_dir"),
            ("adapter_name", "adapter_name"),
            ("browser_state", "browser_state"),
            ("browser_url", "browser_url"),
            ("page_title", "page_title"),
            ("screenshot_path", "screenshot_path"),
            ("screenshot_uri", "screenshot_uri"),
            ("desktop_observation", "observation"),
            ("computer_use_actions", "actions_taken"),
            ("organized_paths", "changed_paths"),
            ("file_manifest", "manifest"),
        ):
            if output.get(output_key):
                metadata[metadata_key] = output[output_key]
        if output.get("preview_url"):
            metadata["preview_url"] = output["preview_url"]
        elif tool_name == "workspace.manage" and output.get("url"):
            metadata["preview_url"] = output["url"]
        return self.repositories.tasks.update_metadata(task_id, metadata)

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
        if status == TaskStatus.COMPLETED:
            latest = self.repositories.tasks.get(task.id) or task
            plan = self.repositories.plans.get(latest.plan_id) if latest.plan_id else None
            validation = validate_fulfillment(latest, plan)
            gap = validation.first_gap
            if gap:
                retry_count = int(latest.metadata.get("fulfillment_retry_count", 0))
                metadata = {
                    **latest.metadata,
                    "fulfillment_gap": gap,
                    "fulfillment_expected": [item.model_dump(mode="json") for item in validation.expected],
                    "fulfillment_missing": [item.value for item in validation.missing],
                    "fulfillment_retry_count": retry_count + 1,
                }
                if retry_count < 1:
                    updated = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.RECEIVED)
                    self.audit.task_state_changed("validator", latest.id, latest.status, updated.status)
                    self.audit.append(
                        AuditEventType.TASK_STATE_CHANGED,
                        actor="validator",
                        task_id=latest.id,
                        payload={"reason": "fulfillment_retry", "gap": gap, "status": updated.status.value},
                    )
                    return updated
                updated = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.BLOCKED)
                self.audit.task_state_changed("validator", latest.id, latest.status, updated.status)
                self.audit.append(
                    AuditEventType.TASK_STATE_CHANGED,
                    actor="validator",
                    task_id=latest.id,
                    payload={"reason": "fulfillment_validation_failed", "gap": gap, "status": updated.status.value},
                )
                return updated
        updated = self.repositories.tasks.update_status(task.id, status)
        self.audit.task_state_changed("orchestrator", task.id, task.status, updated.status)
        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor="orchestrator",
            task_id=task.id,
            payload={"reason": reason, "status": status.value},
        )
        return updated


def _resolve_step_input(task: TaskRecord, value: Any) -> Any:
    replacements = {
        "{{workspace_dir}}": str(task.metadata.get("workspace_dir") or ""),
        "{{adapter_dir}}": str(task.metadata.get("adapter_dir") or ""),
        "{{adapter_name}}": str(task.metadata.get("adapter_name") or ""),
        "{{preview_url}}": str(task.metadata.get("preview_url") or ""),
        "{{last_output}}": _last_tool_output_text(task),
        "{{last_manifest}}": _last_manifest(task),
    }
    return _replace_placeholders(value, replacements)


def _replace_placeholders(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value in replacements:
            return replacements[value]
        rendered = value
        for placeholder, replacement in replacements.items():
            if isinstance(replacement, str):
                rendered = rendered.replace(placeholder, replacement)
        return rendered
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_placeholders(item, replacements) for key, item in value.items()}
    return value


def _last_manifest(task: TaskRecord) -> list[dict]:
    payload = task.metadata.get("last_tool_result")
    if not isinstance(payload, dict):
        return []
    output = payload.get("output")
    if not isinstance(output, dict):
        return []
    manifest = output.get("manifest")
    return manifest if isinstance(manifest, list) else []


def _last_tool_output_text(task: TaskRecord) -> str:
    stored_output = task.metadata.get("last_tool_output_text")
    if isinstance(stored_output, str):
        return stored_output
    payload = task.metadata.get("last_tool_result")
    if not isinstance(payload, dict):
        return ""
    output = payload.get("output")
    if not isinstance(output, dict):
        return ""
    terminal_output = output.get("terminal_output")
    if isinstance(terminal_output, list):
        chunks = []
        for item in terminal_output:
            if isinstance(item, dict) and item.get("content"):
                chunks.append(str(item["content"]))
        if chunks:
            return "\n\n".join(chunks)
    for key in ("final_summary", "summary", "text", "message", "content"):
        if output.get(key):
            return str(output[key])
    return json.dumps(output, default=str)


def _tool_output_text(result: ToolCallResult) -> str:
    output = result.output
    if not isinstance(output, dict):
        return ""
    terminal_output = output.get("terminal_output")
    if isinstance(terminal_output, list):
        chunks = []
        for item in terminal_output:
            if isinstance(item, dict) and item.get("content"):
                chunks.append(str(item["content"]))
        if chunks:
            return "\n\n".join(chunks)
    for key in ("final_summary", "summary", "text", "message", "content"):
        if output.get(key):
            return str(output[key])
    return ""


def _trim_result(result: ToolCallResult) -> dict:
    payload = result.model_dump(mode="json")
    return _trim_value(payload)


def _trim_value(value):
    if isinstance(value, str):
        return value if len(value) <= 4000 else f"{value[:3997]}..."
    if isinstance(value, list):
        return [_trim_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _trim_value(item) for key, item in value.items()}
    return value
