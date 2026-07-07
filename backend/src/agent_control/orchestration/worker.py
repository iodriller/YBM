from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import json
import logging
from typing import Any, Protocol
from uuid import uuid4

from agent_control.channels.memory import ConversationMemoryService
from agent_control.llm import PlannerService
from agent_control.llm.synthesizer import ResponseSynthesizer
from agent_control.llm.validator import AnswerValidator
from agent_control.orchestration.attempt_history import append_attempt_history
from agent_control.orchestration.clarify import build_clarifying_question
from agent_control.orchestration.executor import ToolExecutor
from agent_control.orchestration.failure_diagnosis import diagnose_failure
from agent_control.orchestration.fulfillment import validate_fulfillment
from agent_control.orchestration.recovery_policy import RecoveryAction, choose_recovery
from agent_control.recovery import RetryPolicy
from agent_control.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEventType,
    ErrorClass,
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
from agent_control.tools.mcp_client import mcp_output_text


logger = logging.getLogger(__name__)

DefaultPlanFactory = Callable[[TaskRecord], PlanModel | None]
RecoveryPlanFactory = Callable[[TaskRecord, str], PlanModel | None]


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
    TaskStatus.AWAITING_EXTERNAL,
    TaskStatus.BLOCKED,
    TaskStatus.CANCELLED,
    TaskStatus.CLARIFYING,
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.RETRYING,
    TaskStatus.RUNNING,
}


class TaskWorker:
    # Default per-task wall-clock budget. A single task that exceeds this is
    # forcibly transitioned to FAILED so the worker can move on to the queue.
    # Override globally via ``settings.limits.task_budget_seconds`` (config) or
    # per-task via ``task.metadata["task_budget_seconds"]``.
    DEFAULT_TASK_BUDGET_SECONDS: float = 600.0

    def __init__(
        self,
        repositories: Repositories,
        audit: AuditLogger,
        planner: PlannerService | None = None,
        executor: ToolExecutor | None = None,
        retry_policy: RetryPolicy | None = None,
        config_context: str = "No extra capability context provided.",
        config_context_factory: Callable[[], str] | None = None,
        default_plan_factory: DefaultPlanFactory | None = None,
        recovery_plan_factory: RecoveryPlanFactory | None = None,
        notification_sink: TaskNotificationSink | None = None,
        synthesizer: ResponseSynthesizer | None = None,
        validator: AnswerValidator | None = None,
        task_budget_seconds: float | None = None,
    ) -> None:
        self.repositories = repositories
        self.audit = audit
        self.planner = planner
        self.executor = executor
        self.retry_policy = retry_policy
        self.config_context = config_context
        self.config_context_factory = config_context_factory
        self.default_plan_factory = default_plan_factory
        self.recovery_plan_factory = recovery_plan_factory
        self.notification_sink = notification_sink
        self.synthesizer = synthesizer
        self.validator = validator
        self.task_budget_seconds = (
            float(task_budget_seconds)
            if task_budget_seconds is not None
            else self.DEFAULT_TASK_BUDGET_SECONDS
        )
        # Stable id per worker process; written into tasks.claimed_by so
        # concurrent workers can't race on the same task.
        self.worker_id = f"worker-{uuid4().hex[:12]}"

    async def process_next(self) -> TaskRecord | None:
        # Atomically claim a task. Two workers running this call simultaneously
        # will produce at most one successful claim — the other returns None
        # and tries again on the next poll. Claim expires after budget+buffer
        # so a crashed worker doesn't strand the task.
        claim_expiry = int(self.task_budget_seconds) + 120
        task = self.repositories.tasks.claim_next(
            WORKABLE_STATUSES,
            worker_id=self.worker_id,
            claim_expiry_seconds=claim_expiry,
        )
        if task is None:
            return None
        budget = float(task.metadata.get("task_budget_seconds") or self.task_budget_seconds)
        try:
            processed = await asyncio.wait_for(self.process_task(task.id), timeout=budget)
            await self._notify_if_needed(processed)
            # Once the task is terminal, drop the claim so it can't be
            # accidentally re-claimed by a stale lookup. Best-effort.
            if processed.status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.BLOCKED,
                TaskStatus.CANCELLED,
                TaskStatus.AWAITING_EXTERNAL,
            }:
                self.repositories.tasks.release_claim(processed.id)
            return processed
        except asyncio.TimeoutError:
            # Wall-clock budget exceeded — fail the task so the worker can keep
            # moving. This is the only way a stuck planner/tool call doesn't
            # starve every queued task behind it.
            latest = self.repositories.tasks.get(task.id) or task
            reason = f"task budget {budget:.0f}s exceeded; forcibly failed by worker"
            metadata = {**latest.metadata, "last_worker_error": reason}
            failed = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.FAILED)
            self.repositories.tasks.release_claim(failed.id)
            self.audit.task_state_changed("worker", latest.id, latest.status, failed.status)
            self.audit.append(
                AuditEventType.ERROR,
                actor="worker",
                task_id=latest.id,
                payload={"error": reason, "status": failed.status.value, "budget_seconds": budget},
            )
            await self._notify_if_needed(failed)
            return failed
        except Exception as exc:
            latest = self.repositories.tasks.get(task.id)
            if latest is None:
                raise
            metadata = {**latest.metadata, "last_worker_error": str(exc)}
            failed = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.FAILED)
            self.repositories.tasks.release_claim(failed.id)
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
            # LLM planner is the primary planning path
            _planner_error: str | None = None
            if self.planner is not None:
                try:
                    await self.planner.plan_task(task_id, self._planner_context())
                except Exception as exc:
                    _planner_error = str(exc)
                    self.audit.append(
                        AuditEventType.ERROR,
                        actor="planner",
                        task_id=task_id,
                        payload={"error": "planning_failed", "reason": str(exc)},
                    )
            task = self.repositories.tasks.get(task_id)
            if task is None:
                raise KeyError(f"task not found after planning: {task_id}")

            # Hardcoded factory is a fallback for system commands (status, etc.)
            # only when LLM planning did not produce a plan
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
                            "source": "hardcoded_fallback_plan",
                            "route_decision": _route_decision(task, default_plan),
                            "config_context": self._planner_context(),
                            "plan": default_plan.model_dump(mode="json"),
                        },
                    )
                    self.audit.task_state_changed("worker", task_id, task.status, updated.status)
                else:
                    # No plan was produced. Treat as a planning failure and try to recover
                    # by triggering the same replan loop used for execution failures so the
                    # planner gets another shot with the error context.
                    reason = _planner_error or "no plan could be produced for this objective"
                    meta = {**task.metadata, "planning_error": reason[:800], "last_worker_error": reason[:800]}
                    self.repositories.tasks.update_metadata(task.id, meta)
                    replan_count = int(task.metadata.get("replan_count", 0))
                    if replan_count < 2 and self.planner is not None:
                        replanned = await self._replan_with_error(
                            self.repositories.tasks.get(task.id) or task,
                            f"Planning failed: {reason[:400]}",
                        )
                        if replanned is not None:
                            return replanned
                    failed = self.repositories.tasks.update_metadata(task.id, meta, TaskStatus.FAILED)
                    self.audit.task_state_changed("worker", task_id, task.status, TaskStatus.FAILED)
                    return failed
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

        if task.status == TaskStatus.AWAITING_EXTERNAL:
            return task

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

        pending_result = _pending_tool_result(latest, step)
        if pending_result is not None:
            metadata = {**latest.metadata}
            metadata.pop("pending_tool_result", None)
            metadata.pop("awaiting_external", None)
            latest = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.RUNNING)
            task = self._record_tool_result(latest.id, step.tool_name, pending_result)
            return await self._handle_step_result(task, plan, step, pending_result)

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
        return await self._handle_step_result(task, plan, step, result)

    async def _handle_step_result(
        self,
        task: TaskRecord,
        plan: PlanModel,
        step: PlanStep,
        result: ToolCallResult,
    ) -> TaskRecord:
        latest = self.repositories.tasks.get(task.id)
        if latest is None or latest.status in {TaskStatus.PAUSED, TaskStatus.CANCELLED}:
            return latest or task
        operation = _operation_from_step_result(step, result)
        diagnosis = None if result.status == ToolResultStatus.SUCCEEDED else diagnose_failure(
            result,
            tool_name=step.tool_name or "",
            operation=operation,
        )
        recovery_decision = None
        next_action = "continue"
        if diagnosis is not None:
            tentative_metadata = append_attempt_history(
                latest.metadata,
                step_id=step.id,
                tool_name=step.tool_name or "",
                operation=operation,
                result=result,
                diagnosis=diagnosis,
                next_action="pending",
            )
            recovery_decision = choose_recovery(
                latest.model_copy(update={"metadata": tentative_metadata}),
                result,
                diagnosis,
                tool_name=step.tool_name or "",
                operation=operation,
            )
            next_action = recovery_decision.action
        elif _is_background_external_result(step, result):
            next_action = "await_external"
        metadata = append_attempt_history(
            latest.metadata,
            step_id=step.id,
            tool_name=step.tool_name or "",
            operation=operation,
            result=result,
            diagnosis=diagnosis,
            next_action=next_action,
        )
        if recovery_decision is not None:
            metadata["recovery_decision"] = recovery_decision.model_dump(mode="json")
        latest = self.repositories.tasks.update_metadata(latest.id, metadata)
        if _is_background_external_result(step, result):
            return self._await_external(latest, step, result)
        if recovery_decision is not None and recovery_decision.action == RecoveryAction.ASK_USER:
            extra_metadata: dict[str, Any] = {}
            if (
                self.retry_policy is not None
                and (result.status == ToolResultStatus.RATE_LIMITED or result.error_class == ErrorClass.USAGE_LIMITED)
            ):
                extra_metadata["intervention_summary"] = self.retry_policy.intervention_summary(result)
            return self._ask_user(
                latest,
                result.error_message or recovery_decision.reason,
                extra_metadata=extra_metadata or None,
            )
        if recovery_decision is not None and recovery_decision.action == RecoveryAction.USE_MCP:
            recovery_reason = result.error_message or f"connector_missing:{recovery_decision.reason}"
            recovery = self._attach_recovery_plan(latest, recovery_reason)
            if recovery is not None:
                return recovery
            return await self._replan_with_error(
                latest,
                "Connector missing. Refresh or use the MCP catalog, then call mcp.client call_tool if a configured MCP tool fits.",
            ) or self._ask_user(latest, recovery_reason)
        if recovery_decision is not None and recovery_decision.action == RecoveryAction.USE_CODE_INTERPRETER:
            recovery = self._attach_recovery_plan(latest, f"use_code_interpreter:{result.error_message or recovery_decision.reason}")
            if recovery is not None:
                return recovery
            return self._ask_user(latest, result.error_message or recovery_decision.reason)
        if result.status == ToolResultStatus.SUCCEEDED:
            if _is_mcp_recovery_discovery(latest, step, operation):
                return await self._replan_after_mcp_catalog(latest, result)
            next_step = self._next_runnable_step(plan.steps, step.id)
            if next_step is None:
                self.repositories.tasks.set_current_step(task.id, None)
                replan = await self._validate_and_synthesize(task, step.tool_name or "", result)
                if replan is not None:
                    return replan
                return self._transition(task, TaskStatus.COMPLETED, "all_steps_completed")
            self.repositories.tasks.set_current_step(task.id, next_step.id)
            return self.repositories.tasks.get(task.id) or task
        if result.status == ToolResultStatus.NEEDS_APPROVAL:
            return self._transition(task, TaskStatus.AWAITING_APPROVAL, "tool_approval_required")
        if result.status == ToolResultStatus.DENIED:
            return self._transition(task, TaskStatus.BLOCKED, "tool_policy_denied")
        if result.error_class in {ErrorClass.ADAPTER_FAILED, ErrorClass.VALIDATION_FAILED}:
            recovery = self._attach_recovery_plan(task, result.error_message or result.status.value)
            if recovery is not None:
                return recovery
        retry = self._retry_decision(task, result)
        if retry:
            if retry.status == TaskStatus.BLOCKED and result.error_class != ErrorClass.USAGE_LIMITED:
                recovery = self._attach_recovery_plan(retry, result.error_message or result.status.value)
                if recovery is not None:
                    return recovery
            return retry
        recovery = self._attach_recovery_plan(task, result.error_message or result.status.value)
        if recovery is not None:
            return recovery
        # Intelligent re-planning: ask the LLM to create a new plan using the error context
        replan = await self._replan_with_error(task, result.error_message or result.status.value)
        if replan is not None:
            return replan
        # Every safe strategy is exhausted — ask the user a targeted question
        # instead of dying silently.
        return self._ask_user(task, result.error_message or result.status.value)

    def _await_external(self, task: TaskRecord, step: PlanStep, result: ToolCallResult) -> TaskRecord:
        output = result.output if isinstance(result.output, dict) else {}
        session_id = str(output.get("session_id") or "")
        metadata = {
            **task.metadata,
            "awaiting_external": {
                "tool_name": step.tool_name,
                "step_id": step.id,
                "session_id": session_id,
                "provider": output.get("provider"),
                "status": output.get("status"),
                "request_id": result.request_id,
                "started_at": output.get("started_at"),
            },
        }
        updated = self.repositories.tasks.update_metadata(task.id, metadata, TaskStatus.AWAITING_EXTERNAL)
        self.audit.task_state_changed("orchestrator", task.id, task.status, updated.status)
        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor="orchestrator",
            task_id=task.id,
            payload={"reason": "awaiting_external_session", "session_id": session_id, "tool": step.tool_name},
        )
        return updated

    async def _replan_after_mcp_catalog(self, task: TaskRecord, result: ToolCallResult) -> TaskRecord:
        output = result.output if isinstance(result.output, dict) else {}
        catalog_text = mcp_output_text(output)
        latest = self.repositories.tasks.get(task.id) or task
        metadata = {
            **latest.metadata,
            "recovery_stage": "mcp_catalog_replan",
            "mcp_catalog": {
                "catalog_path": output.get("catalog_path"),
                "catalog_updated_at": output.get("catalog_updated_at"),
                "tool_count": len(output.get("tools") or []),
                "healthy": output.get("healthy"),
            },
        }
        latest = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.RECEIVED)
        replan = await self._replan_with_error(
            latest,
            (
                "MCP catalog refresh completed during connector-missing recovery. "
                "If one configured MCP tool fits the original objective, create a plan that calls "
                "mcp.client with operation=call_tool, server, tool, and arguments. Do not stop after list_tools. "
                "If no MCP tool fits or MCP is unavailable, use code.interpreter solve_once for a bounded local helper."
            ),
            extra_config_context=f"\n\n{catalog_text[:2400]}",
        )
        if replan is not None:
            return replan
        recovery = self._attach_recovery_plan(latest, "mcp_unavailable_or_no_matching_tool")
        if recovery is not None:
            return recovery
        return self._ask_user(latest, "No configured MCP tool could satisfy the missing connector.")

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
        pending = [approval for approval in approvals if approval.status == ApprovalStatus.PENDING]
        if pending:
            if not self._pending_approvals_auto_grantable(pending):
                return task
            for approval in pending:
                self.repositories.approvals.set_status(approval.id, ApprovalStatus.APPROVED)
                self.audit.append(
                    AuditEventType.APPROVAL_DECIDED,
                    actor="policy",
                    task_id=task.id,
                    payload={
                        "approval_id": approval.id,
                        "status": ApprovalStatus.APPROVED.value,
                        "reason": "full_access_policy",
                    },
                )
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

    def _pending_approvals_auto_grantable(self, approvals: list[ApprovalRequest]) -> bool:
        if self.executor is None:
            return False
        settings = self.executor.policy.settings
        for approval in approvals:
            policy = settings.capabilities.get(approval.capability)
            if policy is None or not policy.enabled or policy.requires_approval:
                return False
        return True

    def _attach_recovery_plan(self, task: TaskRecord, reason: str) -> TaskRecord | None:
        if self.recovery_plan_factory is None:
            return None
        latest = self.repositories.tasks.get(task.id) or task
        repair_count = int(latest.metadata.get("evaluator_repair_count", 0))
        if repair_count >= 3:
            return None
        plan = self.recovery_plan_factory(latest, reason)
        if plan is None:
            return None
        stage = _recovery_stage_for_plan(plan)
        stage_counts = _recovery_stage_counts(latest.metadata.get("recovery_stage_counts"))
        if int(stage_counts.get(stage, 0)) >= 1:
            return None
        stage_counts[stage] = int(stage_counts.get(stage, 0)) + 1
        self.repositories.plans.create(latest.id, plan)
        metadata = {
            **latest.metadata,
            "evaluator_repair_count": repair_count + 1,
            "evaluator_repair_reason": reason,
            "evaluator_repair_plan_id": plan.id,
            "recovery_stage": stage,
            "recovery_stage_counts": stage_counts,
        }
        updated = self.repositories.tasks.update_metadata(latest.id, metadata)
        updated = self.repositories.tasks.attach_plan(updated.id, plan.id, TaskStatus.PLANNED)
        self.repositories.tasks.set_current_step(updated.id, None)
        self.audit.append(
            AuditEventType.PLAN_CREATED,
            actor="evaluator",
            task_id=latest.id,
            payload={
                "plan_id": plan.id,
                "step_count": len(plan.steps),
                "reason": reason,
                "plan": plan.model_dump(mode="json"),
            },
        )
        self.audit.task_state_changed("evaluator", latest.id, latest.status, updated.status)
        return self.repositories.tasks.get(latest.id) or updated

    def _retry_decision(self, task: TaskRecord, result: ToolCallResult) -> TaskRecord | None:
        if self.retry_policy is None:
            return None
        if result.error_class == ErrorClass.USAGE_LIMITED:
            return self._ask_user(
                task,
                result.error_message or "usage limit reached",
                extra_metadata={"intervention_summary": self.retry_policy.intervention_summary(result)},
            )
        current_retry_count = int(task.metadata.get("retry_count", 0))
        decision = self.retry_policy.evaluate(result, current_retry_count)
        if not decision.retry:
            if decision.reason == "retry_limit_reached":
                return self._ask_user(
                    task,
                    result.error_message or result.status.value,
                    extra_metadata={
                        "retry_count": decision.retry_count,
                        "intervention_summary": self.retry_policy.intervention_summary(result),
                    },
                )
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

    def _ask_user(self, task: TaskRecord, reason: str, *, extra_metadata: dict[str, Any] | None = None) -> TaskRecord:
        """Pause the task with one targeted question instead of a dead BLOCKED/FAILED end.

        The user's next message in the source chat resumes the task with the
        answer attached (see TelegramIntakeService). At most two questions per
        task — after that it fails with the accumulated context.
        """
        latest = self.repositories.tasks.get(task.id) or task
        ask_count = int(latest.metadata.get("clarify_count", 0))
        if ask_count >= 2:
            return self._transition(latest, TaskStatus.FAILED, "clarification_attempts_exhausted")
        question = build_clarifying_question(latest, reason)
        metadata = {
            **latest.metadata,
            **(extra_metadata or {}),
            "clarify_count": ask_count + 1,
            "clarifying_question": question,
            "clarifying_reason": reason[:400],
        }
        updated = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.CLARIFYING)
        self.audit.task_state_changed("orchestrator", latest.id, latest.status, updated.status)
        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor="orchestrator",
            task_id=latest.id,
            payload={"reason": "clarification_requested", "question": question, "clarify_count": ask_count + 1},
        )
        return updated

    async def _validate_and_synthesize(
        self, task: TaskRecord, tool_name: str, result: ToolCallResult
    ) -> TaskRecord | None:
        """Validate raw tool output first; if sufficient, synthesize the final answer.

        Order matters: the validator checks the RAW tool output before the synthesizer
        runs. If the raw output doesn't contain what was asked (wrong count, missing
        section, login wall, etc.), we replan immediately with a specific reason — no
        synthesizer call is wasted on insufficient content, and we avoid the risk of
        the synthesizer hallucinating a plausible answer from incomplete data.

        Returns a replanned TaskRecord if content is insufficient, or None to proceed to COMPLETED.
        """
        if self.synthesizer is None or not ResponseSynthesizer.is_content_tool(tool_name):
            return None
        raw = _tool_output_text(result)
        if not raw:
            return None
        original_message = str(task.metadata.get("original_message_text") or "").strip() or None

        # Step 1: validate raw output BEFORE synthesis.
        if self.validator is not None:
            valid, reason = await self.validator.validate(
                task.objective,
                raw,
                original_message=original_message,
            )
            if not valid:
                self.audit.append(
                    AuditEventType.TASK_STATE_CHANGED,
                    actor="validator",
                    task_id=task.id,
                    payload={
                        "action": "raw_output_rejected",
                        "tool": tool_name,
                        "reason": reason,
                    },
                )
                return await self._replan_with_error(
                    task,
                    f"Tool output insufficient: {reason}. Use a different tool/operation "
                    f"or extract more of the page content (e.g. summarize_page instead of "
                    f"extract_page_state, or a deeper page scan).",
                )

        # Step 2: synthesize the validated raw output into a focused answer.
        answer = await self.synthesizer.synthesize(task.objective, raw, original_message=original_message)
        if not answer:
            sample = raw[:400].replace("\n", " ")
            return await self._replan_with_error(
                task,
                f"Synthesizer could not extract a focused answer from validated raw output. "
                f"Sample: '{sample}'. Try a different tool/operation.",
            )

        latest = self.repositories.tasks.get(task.id) or task
        meta = {**latest.metadata, "synthesized_answer": answer}
        self.repositories.tasks.update_metadata(task.id, meta)
        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor="synthesizer",
            task_id=task.id,
            payload={"action": "answer_synthesized", "tool": tool_name},
        )
        return None

    async def _replan_with_error(
        self,
        task: TaskRecord,
        error_context: str,
        *,
        extra_config_context: str = "",
    ) -> TaskRecord | None:
        """Ask the LLM planner to produce a new plan given the error context (up to 2 replan attempts)."""
        if self.planner is None:
            return None
        replan_count = int(task.metadata.get("replan_count", 0))
        if replan_count >= 2:
            return None
        enriched_objective = (
            f"{task.objective}\n\n"
            f"[Previous attempt failed: {error_context[:400]}. "
            "Try a different approach or different tool to accomplish the same goal.]"
        )
        latest = self.repositories.tasks.get(task.id) or task
        metadata = {
            **latest.metadata,
            "replan_count": replan_count + 1,
            "last_replan_reason": error_context[:400],
            "replan_objective": enriched_objective,
        }
        self.repositories.tasks.update_metadata(task.id, metadata, TaskStatus.RECEIVED)
        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor="orchestrator",
            task_id=task.id,
            payload={"reason": "replan_after_failure", "replan_count": replan_count + 1, "error": error_context[:400]},
        )
        try:
            await self.planner.plan_task(
                task.id,
                self._planner_context(extra_config_context + f"\n\nError context: {error_context[:400]}"),
            )
        except Exception:
            logger.warning("replanning planner call failed for task %s", task.id, exc_info=True)
            return None
        return self.repositories.tasks.get(task.id)

    def _planner_context(self, extra: str = "") -> str:
        if self.config_context_factory is None:
            return self.config_context + extra
        try:
            base = self.config_context_factory()
        except Exception:
            logger.warning("config context factory failed; falling back to startup context", exc_info=True)
            base = self.config_context
        return base + extra

    async def _notify_if_needed(self, task: TaskRecord) -> None:
        if task.status not in NOTIFIABLE_STATUSES:
            return
        # CLARIFYING can happen more than once per task; key by question number
        # so the second question is not swallowed by the dedupe set.
        status_key = task.status.value
        if task.status == TaskStatus.CLARIFYING:
            status_key = f"clarifying:{task.metadata.get('clarify_count', 0)}"
        notified = set(task.metadata.get("notified_statuses", []))
        if self.notification_sink is not None and status_key not in notified:
            await self.notification_sink.notify(task)
        await self._remember_task_completion(task)
        latest = self.repositories.tasks.get(task.id)
        if latest is None:
            return
        updated_notified = sorted({*latest.metadata.get("notified_statuses", []), status_key})
        self.repositories.tasks.update_metadata(
            task.id,
            {**latest.metadata, "notified_statuses": updated_notified},
        )

    async def _remember_task_completion(self, task: TaskRecord) -> None:
        if not task.conversation_id or task.status not in {TaskStatus.COMPLETED, TaskStatus.BLOCKED, TaskStatus.FAILED}:
            return
        summary = _task_memory_summary(task)
        if not summary:
            return
        try:
            await ConversationMemoryService(self.repositories).update_from_task_summary(task.conversation_id, summary)
        except Exception as exc:
            self.audit.append(
                AuditEventType.ERROR,
                actor="memory",
                task_id=task.id,
                payload={"error": "task_memory_update_failed", "reason": str(exc)},
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
            ("changed_paths", "changed_paths"),
            ("organized_paths", "changed_paths"),
            ("rename_manifest", "rename_manifest"),
            ("file_manifest", "manifest"),
            ("document_path", "path"),
            ("document_summary", "summary"),
            ("coding_agent_workspace", "workspace_dir"),
            ("coding_agent_session_id", "session_id"),
            ("coding_agent_limit_state", "limit_state"),
            ("changed_files", "changed_files"),
            ("schedule_id", "schedule_id"),
            ("scheduled_task_id", "task_id"),
            ("schedule_next_run_at", "next_run_at"),
            ("mcp_catalog_path", "catalog_path"),
            ("mcp_catalog_updated_at", "catalog_updated_at"),
            ("mcp_selected_tool", "selected_tool"),
        ):
            if output.get(output_key):
                metadata[metadata_key] = output[output_key]
        if tool_name == "mcp.client":
            metadata["mcp_catalog"] = {
                "catalog_path": output.get("catalog_path"),
                "catalog_updated_at": output.get("catalog_updated_at"),
                "tool_count": len(output.get("tools") or []),
                "healthy": output.get("healthy"),
            }
        if result.artifact_ids:
            metadata["last_artifact_ids"] = result.artifact_ids
        elif output.get("artifact_ids"):
            metadata["last_artifact_ids"] = output["artifact_ids"]
        if output.get("preview_url"):
            metadata["preview_url"] = output["preview_url"]
        elif tool_name == "workspace.manage" and output.get("url"):
            metadata["preview_url"] = output["url"]
        if tool_name == "artifact.deliver":
            metadata["artifact_delivery"] = output
            if output.get("artifact_id"):
                metadata["last_delivered_artifact_id"] = output["artifact_id"]
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
                recovery = self._attach_recovery_plan(latest, gap)
                if recovery is not None:
                    return recovery
                retry_count = int(latest.metadata.get("fulfillment_retry_count", 0))
                metadata = {
                    **latest.metadata,
                    "fulfillment_gap": gap,
                    "fulfillment_expected": [item.model_dump(mode="json") for item in validation.expected],
                    "fulfillment_missing": [item.value for item in validation.missing],
                    "fulfillment_retry_count": retry_count + 1,
                }
                if retry_count < 2:
                    updated = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.RECEIVED)
                    self.audit.task_state_changed("validator", latest.id, latest.status, updated.status)
                    self.audit.append(
                        AuditEventType.TASK_STATE_CHANGED,
                        actor="validator",
                        task_id=latest.id,
                        payload={"reason": "fulfillment_retry", "gap": gap, "status": updated.status.value},
                    )
                    return updated
                self.repositories.tasks.update_metadata(latest.id, metadata)
                self.audit.append(
                    AuditEventType.TASK_STATE_CHANGED,
                    actor="validator",
                    task_id=latest.id,
                    payload={"reason": "fulfillment_validation_failed", "gap": gap},
                )
                return self._ask_user(latest, gap)
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
        "{{last_entry_path}}": _last_entry_path(task),
    }
    return _replace_placeholders(value, replacements)


def _is_background_external_result(step: PlanStep, result: ToolCallResult) -> bool:
    if step.tool_name != "coding.agent" or result.status != ToolResultStatus.SUCCEEDED:
        return False
    output = result.output if isinstance(result.output, dict) else {}
    return output.get("status") == "running" and bool(output.get("session_id"))


def _pending_tool_result(task: TaskRecord, step: PlanStep) -> ToolCallResult | None:
    pending = task.metadata.get("pending_tool_result")
    if not isinstance(pending, dict):
        return None
    if pending.get("step_id") and pending.get("step_id") != step.id:
        return None
    if pending.get("tool_name") and pending.get("tool_name") != step.tool_name:
        return None
    result = pending.get("result")
    if not isinstance(result, dict):
        return None
    try:
        return ToolCallResult.model_validate(result)
    except Exception:
        return None


def _operation_from_step_result(step: PlanStep, result: ToolCallResult) -> str:
    output = result.output if isinstance(result.output, dict) else {}
    if output.get("operation"):
        return str(output["operation"])
    if step.tool_input.get("operation"):
        return str(step.tool_input["operation"])
    return ""


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


def _last_entry_path(task: TaskRecord) -> str:
    payload = task.metadata.get("last_tool_result")
    if not isinstance(payload, dict):
        return ""
    output = payload.get("output")
    if not isinstance(output, dict):
        return ""
    if output.get("path"):
        return str(output["path"])
    entries = output.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("path") and not entry.get("is_dir"):
                return str(entry["path"])
    return ""


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
    if _looks_like_mcp_output(output):
        return mcp_output_text(output)
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


def _looks_like_mcp_output(output: dict[str, Any]) -> bool:
    if output.get("operation") in {"discover", "list_tools", "call_tool", "health"} and (
        "servers" in output or "tools" in output or "result" in output
    ):
        return True
    return bool(output.get("catalog_path") and ("servers" in output or "tools" in output))


def _is_mcp_recovery_discovery(task: TaskRecord, step: PlanStep, operation: str) -> bool:
    if step.tool_name != "mcp.client" or operation not in {"discover", "list_tools", "health"}:
        return False
    reason = str(task.metadata.get("evaluator_repair_reason") or task.metadata.get("last_failure_type") or "").lower()
    stage = str(task.metadata.get("recovery_stage") or "").lower()
    return (
        "connector_missing" in reason
        or "tool adapter not registered" in reason
        or "unregistered tool" in reason
        or stage == "mcp_catalog_refresh"
    )


def _recovery_stage_for_plan(plan: PlanModel) -> str:
    tool_ops = {(step.tool_name or "", str(step.tool_input.get("operation") or "")) for step in plan.steps}
    if any(tool == "mcp.client" and operation in {"discover", "list_tools", "health"} for tool, operation in tool_ops):
        return "mcp_catalog_refresh"
    if any(tool == "mcp.client" and operation == "call_tool" for tool, operation in tool_ops):
        return "mcp_call_tool"
    if any(tool == "code.interpreter" for tool, _operation in tool_ops):
        return "code_interpreter"
    if any(tool == "adapter.factory" for tool, _operation in tool_ops):
        return "adapter_factory"
    if any(tool == "filesystem.manage" for tool, _operation in tool_ops):
        return "filesystem_recovery"
    if any(tool == "artifact.deliver" for tool, _operation in tool_ops):
        return "artifact_delivery"
    if any(tool == "computer.use" for tool, _operation in tool_ops):
        return "computer_use_recovery"
    return "evaluator_recovery"


def _recovery_stage_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        try:
            count = int(raw)
        except (TypeError, ValueError):
            continue
        if count > 0:
            counts[key] = count
    return counts


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


def _task_memory_summary(task: TaskRecord) -> str:
    metadata = task.metadata
    parts = [
        f"Task {task.id} {task.status.value}: {task.objective}",
    ]
    for label, key in (
        ("tool", "last_tool_name"),
        ("output", "last_tool_output_text"),
        ("desktop_observation", "desktop_observation"),
        ("screenshot_path", "screenshot_path"),
        ("browser_url", "browser_url"),
        ("page_title", "page_title"),
        ("document_path", "document_path"),
        ("document_summary", "document_summary"),
        ("workspace_dir", "workspace_dir"),
        ("preview_url", "preview_url"),
        ("changed_paths", "changed_paths"),
        ("organized_paths", "organized_paths"),
        ("file_manifest", "file_manifest"),
        ("rename_manifest", "rename_manifest"),
        ("artifact_ids", "last_artifact_ids"),
    ):
        value = metadata.get(key)
        if value:
            parts.append(f"{label}: {_compact_jsonish(value)}")
    error = _last_error_from_task(task)
    if error:
        parts.append(f"error: {error}")
    return _trim_value(" | ".join(parts))  # type: ignore[return-value]


def _compact_jsonish(value: Any, limit: int = 900) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _last_error_from_task(task: TaskRecord) -> str | None:
    result = task.metadata.get("last_tool_result")
    if isinstance(result, dict) and result.get("error_message"):
        return str(result["error_message"])
    if task.metadata.get("last_worker_error"):
        return str(task.metadata["last_worker_error"])
    if task.metadata.get("fulfillment_gap"):
        return str(task.metadata["fulfillment_gap"])
    return None


def _route_decision(task: TaskRecord, plan: PlanModel) -> dict[str, Any]:
    tool_names = [step.tool_name for step in plan.steps if step.tool_name]
    lowered = task.objective.lower()
    explicit_external_agents = []
    if "codex" in lowered:
        explicit_external_agents.append("codex")
    if "copilot" in lowered:
        explicit_external_agents.append("github_copilot")
    used_external_agents = []
    if any(tool and "copilot" in tool for tool in tool_names):
        used_external_agents.append("github_copilot")
    if any(tool in {"coding.agent", "coding_assistant"} for tool in tool_names):
        used_external_agents.append("coding_agent")
    skipped: list[str] = []
    if not explicit_external_agents:
        skipped.append("codex_and_github_copilot_not_used_without_explicit_user_request")
    return {
        "objective": task.objective,
        "selected_tools": tool_names,
        "explicit_external_agents": explicit_external_agents,
        "used_external_agents": used_external_agents,
        "external_agent_skipped": skipped,
    }
