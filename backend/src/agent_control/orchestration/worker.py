from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import json
import logging
from typing import Any, Protocol
from uuid import uuid4

from agent_control.channels.memory import ConversationMemoryService
from agent_control.logging_setup import bind_task_context
from agent_control.orchestration.auditor import AuditorService
from agent_control.orchestration.executor import ToolExecutor
from agent_control.orchestration.fulfillment import validate_fulfillment
from agent_control.orchestration.operator import OperatorLoopService
from agent_control.recovery import RetryPolicy
from agent_control.schemas import (
    ApprovalStatus,
    AuditEventType,
    ErrorClass,
    LLMCallRecord,
    OperatorAction,
    OperatorDecision,
    ParallelToolCall,
    RiskLevel,
    TaskRecord,
    TaskStatus,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
    utc_now,
)
from agent_control.storage.audit import AuditLogger
from agent_control.storage.redaction import redact_payload
from agent_control.storage.repositories import Repositories
from agent_control.tools.mcp_client import mcp_output_text

logger = logging.getLogger(__name__)

class TaskNotificationSink(Protocol):
    async def notify(self, task: TaskRecord) -> None:
        ...

WORKABLE_STATUSES = [
    TaskStatus.RECEIVED,
    TaskStatus.INTERPRETING,
    TaskStatus.PLANNED,
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

# Statuses that only make sense while a specific worker process is actively
# holding them. If a task is still RUNNING or INTERPRETING when a *new*
# worker starts up, the process that was supposed to finish it is gone
# (crashed, killed, restarted before its claim naturally expired via
# claim_next()'s claim_expiry_seconds). Left alone, the task would either
# sit stuck until that stale claim eventually times out, or get silently
# re-claimed and re-run from a status that assumes in-flight state which no
# longer exists - re-running from the top could duplicate side effects (a
# second Telegram send, a second file write), and there's no checkpoint to
# resume from mid-flight. See docs/HISTORY.md P6.
ORPHANABLE_STATUSES = (TaskStatus.RUNNING, TaskStatus.INTERPRETING)

# Pseudo-entries the fulfillment/audit gap paths append to operator_history so
# the next decide() call can see why `done` was rejected. They are observations,
# not work - _tool_call_count() excludes them from the step budget.
CHECK_ENTRY_FULFILLMENT = "_fulfillment_check"
CHECK_ENTRY_AUDIT = "_audit_check"
CHECK_ENTRY_NAMES = frozenset({CHECK_ENTRY_FULFILLMENT, CHECK_ENTRY_AUDIT})

def reconcile_orphaned_tasks(repositories: Repositories, audit: AuditLogger) -> int:
    """Explicitly fail any task left RUNNING/INTERPRETING by a worker that
    never got to finish it. Call once, before a worker starts polling -
    never silently resumed. Returns the number of tasks reconciled."""
    total = 0
    while True:
        orphaned = repositories.tasks.list_by_statuses(list(ORPHANABLE_STATUSES), limit=100)
        if not orphaned:
            break
        for task in orphaned:
            reason = (
                f"worker restarted while task was {task.status.value}; "
                "failed explicitly rather than silently resumed"
            )
            metadata = {**task.metadata, "last_worker_error": reason}
            failed = repositories.tasks.update_metadata(task.id, metadata, TaskStatus.FAILED)
            repositories.tasks.release_claim(failed.id)
            audit.task_state_changed("worker", task.id, task.status, failed.status)
            audit.append(
                AuditEventType.ERROR,
                actor="worker",
                task_id=task.id,
                payload={"error": reason, "status": failed.status.value},
            )
            total += 1
    return total

class TaskWorker:
    # Default per-task wall-clock budget. A single task that exceeds this is
    # forcibly transitioned to FAILED so the worker can move on to the queue.
    # Override globally via ``settings.limits.task_budget_seconds`` (config) or
    # per-task via ``task.metadata["task_budget_seconds"]``.
    DEFAULT_TASK_BUDGET_SECONDS: float = 600.0
    # A delegated sub-task (docs/HISTORY.md Part 4 T1.2) gets its own small,
    # fixed step budget, independent of operator_max_steps - the whole point
    # of delegation is bounding how much a sub-task can explore before it
    # must report back, not inheriting the parent's full budget.
    DELEGATE_MAX_STEPS: int = 6

    def __init__(
        self,
        repositories: Repositories,
        audit: AuditLogger,
        executor: ToolExecutor | None = None,
        retry_policy: RetryPolicy | None = None,
        config_context: str = "No extra capability context provided.",
        config_context_factory: Callable[[], str] | None = None,
        notification_sink: TaskNotificationSink | None = None,
        task_budget_seconds: float | None = None,
        operator: OperatorLoopService | None = None,
        operator_max_steps: int = 8,
        auditor: AuditorService | None = None,
        persist_llm_calls: bool = True,
        llm_call_max_chars: int = 8000,
        redact_patterns: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.repositories = repositories
        self.audit = audit
        self.executor = executor
        self.retry_policy = retry_policy
        self.config_context = config_context
        self.config_context_factory = config_context_factory
        self.notification_sink = notification_sink
        self.task_budget_seconds = (
            float(task_budget_seconds)
            if task_budget_seconds is not None
            else self.DEFAULT_TASK_BUDGET_SECONDS
        )
        # The observe/decide/act loop (docs/HISTORY.md P3 §2.2) - the sole
        # execution path. See orchestration/operator.py.
        self.operator = operator
        self.operator_max_steps = operator_max_steps
        # The Auditor (docs/HISTORY.md P3 §2.1) - grounds a `done` decision
        # against raw tool output before letting it complete. Optional: a
        # worker with no auditor configured skips straight to the
        # fulfillment-gap check, same as before this existed.
        self.auditor = auditor
        # LLM-call persistence (docs/UI_UX_AUDIT.md Phase 14d) - the receipts
        # behind the Duration view's real (non-inferred) segments. See
        # _record_llm_call below.
        self.persist_llm_calls = persist_llm_calls
        self.llm_call_max_chars = llm_call_max_chars
        self.redact_patterns = redact_patterns
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
                TaskStatus.AWAITING_APPROVAL,
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
            # AWAITING_APPROVAL is deliberately NOT in WORKABLE_STATUSES
            # (docs/UI_UX_AUDIT.md Phase 8, second pass) - claim_next never
            # re-selects it, the same way it already never re-selected
            # AWAITING_EXTERNAL. A task landing there has its claim released
            # in process_next above, freeing this worker to claim a
            # different queued task on its very next poll instead of
            # re-picking the same blocked one (the actual bug: with the
            # default max_parallel_tasks=1, a pending approval used to
            # prevent the one worker from ever reaching a later task).
            # orchestration/signals.py's requeue_after_approval_decision()
            # is the other half - it flips the task back to RUNNING the
            # moment a decision lands, so it becomes claimable again without
            # needing this same task re-polled.
            if processed is None:
                await asyncio.sleep(poll_interval_seconds)

    async def process_task(self, task_id: str) -> TaskRecord:
        # Rebind (not merge) so every log line for this tick is greppable by
        # task_id alone - one grep, the whole story, instead of correlating
        # timestamps across an unstructured stdout capture (docs/HISTORY.md §2.1).
        # Rebinding fresh each call matters: this worker's asyncio Task is
        # long-lived (run_forever() polls it repeatedly), so a stale task_id
        # from a previous tick would otherwise leak into this one's logs.
        bind_task_context(task_id=task_id, worker_id=self.worker_id)
        task = self.repositories.tasks.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        return await self._process_operator_loop(task)

    async def _process_operator_loop(self, task: TaskRecord) -> TaskRecord:
        """One observe/decide/act tick - the sole execution path (P3 §2.2).
        See orchestration/operator.py.

        Approval flow: a tool call that comes back NEEDS_APPROVAL creates a
        real ApprovalRequest (same repository/audit path the plan-based flow
        used) and transitions to AWAITING_APPROVAL with the pending call
        stashed in metadata["operator_pending_call"]; resuming is handled by
        _process_operator_awaiting_approval below, which replays that exact
        call with its one-shot, parameter-bound approval.

        Fulfillment: a `done` decision is checked against
        `validate_fulfillment` (objective-inferred postconditions - there is
        no PlanModel here, so `plan=None`) before it is allowed to complete.
        A gap is appended to `history` as an observation and the loop
        continues - "the next decide() call sees the error in context" - capped
        at 2 gap cycles the same way clarify/replan attempts are capped
        elsewhere in this file, so a model that can't close the gap doesn't
        loop forever.

        Rate limits: RATE_LIMITED/USAGE_LIMITED results back off through
        _operator_retry_or_ask instead of retrying on the next ~3s poll tick.

        Background sessions: a tool that reports status=running with a
        session_id (coding.agent) transitions to AWAITING_EXTERNAL via
        _await_operator_external and is picked back up by
        _resume_operator_pending_external once metadata["pending_tool_result"]
        appears - written by the same completion callback the plan-based path
        used (cli.py), which is status-based, not plan-shaped.
        """
        if self.operator is None or self.executor is None:
            return self._transition_operator(task, task.metadata, TaskStatus.BLOCKED, "operator_loop_not_configured")

        latest = self.repositories.tasks.get(task.id) or task
        if latest.status in {TaskStatus.PAUSED, TaskStatus.CANCELLED}:
            return latest

        if latest.status == TaskStatus.AWAITING_APPROVAL:
            return await self._process_operator_awaiting_approval(latest)

        if latest.status == TaskStatus.RETRYING:
            return self._process_operator_retrying(latest)

        if isinstance(latest.metadata.get("pending_tool_result"), dict):
            return self._resume_operator_pending_external(latest)

        if latest.status == TaskStatus.AWAITING_EXTERNAL:
            return latest

        history: list[dict[str, Any]] = list(latest.metadata.get("operator_history") or [])
        if not latest.metadata.get("operator_loop"):
            latest = self.repositories.tasks.update_metadata(
                latest.id, {**latest.metadata, "operator_loop": True}, TaskStatus.RUNNING
            )
            self.audit.task_state_changed("worker", task.id, task.status, TaskStatus.RUNNING)

        if _tool_call_count(history) >= self.operator_max_steps:
            return self._transition_operator(
                latest, {**latest.metadata, "operator_history": history},
                TaskStatus.FAILED, "operator_step_budget_exhausted",
            )

        memory_context = str(latest.metadata.get("memory_context") or "")
        # docs/HISTORY.md Part 4 T2.6: a done that was already rejected once
        # (audit-gap or fulfillment-gap retry marker in history) is a
        # concrete, local, zero-cost-to-check sign the default model is
        # struggling on this task - worth the stronger model if one is
        # configured, same reasoning as the existing parse-failure escalation
        # in operator.py, just reacting to an earlier signal.
        prefer_major = any(entry.get("tool_name") in CHECK_ENTRY_NAMES for entry in history)
        try:
            decision = await self.operator.decide(
                latest.objective, self._planner_context(), history,
                memory_context=memory_context, prefer_major=prefer_major,
            )
            latest = self._record_llm_usage(latest, "operator", getattr(self.operator, "last_usage", None))
            self._record_llm_call(latest.id, "operator", len(history), self.operator)
        except Exception as exc:
            self.audit.append(
                AuditEventType.ERROR, actor="operator", task_id=latest.id,
                payload={"error": "operator_decide_failed", "reason": str(exc)},
            )
            return self._transition_operator(
                latest, {**latest.metadata, "operator_history": history},
                TaskStatus.FAILED, f"operator_decide_failed: {exc}"[:400],
            )

        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED, actor="operator", task_id=latest.id,
            payload={"action": "operator_decision", "decision": decision.model_dump(mode="json"), "step_index": len(history)},
        )

        if decision.action == OperatorAction.DONE:
            final_answer = decision.final_answer
            content_entry = _last_content_tool_history_entry(history) if self.auditor is not None else None
            if content_entry is not None:
                audit_gap_count = int(latest.metadata.get("operator_audit_gap_count", 0))
                if audit_gap_count < 2:
                    # Audit the FULL recorded output, not the history entry's
                    # 2000-char display summary. The Auditor's whole job is
                    # count/section sufficiency ("are all 5 episodes here?") -
                    # judging a truncation produces false INSUFFICIENT verdicts
                    # on exactly the long-content objectives it exists for.
                    # See docs/HISTORY.md §3.2.
                    raw_output = str(
                        latest.metadata.get("last_tool_output_text")
                        or content_entry.get("output_summary")
                        or ""
                    )
                    audit_result = await self.auditor.audit(
                        latest.objective,
                        raw_output,
                        original_message=str(latest.metadata.get("original_message_text") or "") or None,
                    )
                    latest = self._record_llm_usage(latest, "auditor", getattr(self.auditor, "last_usage", None))
                    self._record_llm_call(latest.id, "auditor", len(history), self.auditor)
                    if not audit_result.sufficient:
                        history.append({
                            "tool_name": CHECK_ENTRY_AUDIT,
                            "input": None,
                            "status": "audit_gap",
                            "error": (
                                f"Declared done, but the auditor found the raw output insufficient: "
                                f"{audit_result.reason}. Keep working toward the objective - call "
                                "another tool, or explain to the user why it can't be met - before "
                                "declaring done again."
                            ),
                        })
                        metadata = {
                            **latest.metadata,
                            "operator_history": history,
                            "operator_audit_gap_count": audit_gap_count + 1,
                        }
                        return self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.RUNNING)
                    if audit_result.answer:
                        final_answer = audit_result.answer

            metadata = {**latest.metadata, "operator_history": history, "synthesized_answer": final_answer}
            gap = validate_fulfillment(latest.model_copy(update={"metadata": metadata})).first_gap
            if gap:
                gap_count = int(latest.metadata.get("operator_fulfillment_gap_count", 0))
                if gap_count < 2:
                    history.append({
                        "tool_name": CHECK_ENTRY_FULFILLMENT,
                        "input": None,
                        "status": "fulfillment_gap",
                        "error": (
                            f"Declared done, but the objective still expects: {gap}. Keep working "
                            "toward the objective - call another tool, or explain to the user why "
                            "it can't be met - before declaring done again."
                        ),
                    })
                    metadata = {
                        **latest.metadata,
                        "operator_history": history,
                        "operator_fulfillment_gap_count": gap_count + 1,
                    }
                    return self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.RUNNING)
                metadata["fulfillment_gap"] = gap
            return self._transition_operator(latest, metadata, TaskStatus.COMPLETED, "operator_done")

        if decision.action == OperatorAction.ASK_USER:
            latest = self.repositories.tasks.update_metadata(latest.id, {**latest.metadata, "operator_history": history})
            return self._operator_ask_user(latest, decision.question or "Need more information to continue.")

        if decision.action == OperatorAction.BLOCKED:
            metadata = {**latest.metadata, "operator_history": history}
            return self._transition_operator(latest, metadata, TaskStatus.BLOCKED, decision.reason or "operator_blocked")

        if decision.action == OperatorAction.CALL_TOOLS_PARALLEL:
            entries = await self._run_parallel_calls(latest.id, decision.parallel_calls)
            history.extend(entries)
            return self.repositories.tasks.update_metadata(latest.id, {**latest.metadata, "operator_history": history})

        if decision.action == OperatorAction.DELEGATE:
            latest, entry = await self._run_delegate(latest, decision)
            history.append(entry)
            return self.repositories.tasks.update_metadata(latest.id, {**latest.metadata, "operator_history": history})

        # CALL_TOOL
        tool_def = self.executor.tool_definitions.get(decision.tool_name)
        if tool_def is None:
            history.append({
                "tool_name": decision.tool_name, "input": decision.tool_input,
                "status": "failed", "error": f"unregistered tool: {decision.tool_name}",
            })
            return self.repositories.tasks.update_metadata(latest.id, {**latest.metadata, "operator_history": history})

        request = ToolCallRequest(
            task_id=latest.id,
            tool_name=decision.tool_name,
            capability=tool_def.capability,
            risk_level=_effective_operator_risk(tool_def, decision.tool_input, decision.risk_level),
            input=decision.tool_input,
        )
        if request.risk_level != decision.risk_level:
            decision = decision.model_copy(update={"risk_level": request.risk_level})
        result = await self.executor.execute(request)

        if result.status == ToolResultStatus.NEEDS_APPROVAL:
            return self._await_operator_approval(
                latest,
                decision,
                history,
                approval_id=str(result.output.get("approval_id") or ""),
            )

        recorded = self._record_tool_result(latest.id, decision.tool_name, result)
        if _is_background_external_tool_result(decision.tool_name, result):
            return self._await_operator_external(recorded, decision, result, history)
        if result.status != ToolResultStatus.SUCCEEDED:
            retry_outcome = self._operator_retry_or_ask(recorded, decision, result, history)
            if retry_outcome is not None:
                return retry_outcome
        output_text = _tool_output_text(result) if result.status == ToolResultStatus.SUCCEEDED else None
        history.append({
            "tool_name": decision.tool_name,
            "input": decision.tool_input,
            "status": result.status.value,
            "output_summary": output_text[:2000] if output_text else None,
            "error": result.error_message,
            "request_id": result.request_id,
        })
        return self.repositories.tasks.update_metadata(recorded.id, {**recorded.metadata, "operator_history": history})

    async def _run_parallel_calls(
        self, task_id: str, calls: list[ParallelToolCall], *, origin_prefix: str = ""
    ) -> list[dict[str, Any]]:
        """Execute independent tool calls concurrently (docs/HISTORY.md Part 3
        T1.1) and return one history-entry dict per call, same shape as a
        normal call_tool entry plus ``"parallel": True``.

        Every call in one batch shares a single ``parallel_batch:<id>``
        ``ToolCallRequest.origin`` (docs/UI_REWRITE_PLAN.md §7/§9 Phase 0.6),
        so a trace UI can render them as siblings that ran at once instead of
        indistinguishable sequential calls. ``origin_prefix`` lets
        ``_run_delegate`` nest this correctly when a sub-task itself fans
        out - the batch's origin becomes ``subagent:<id>/parallel_batch:<id>``.

        Deliberately narrow, not a general CALL_TOOL replacement: skips the
        approval/retry/background-wait machinery entirely, because none of
        it generalizes cleanly to N calls at once (which one pauses the
        whole task for approval? what does "retry" mean when 2 of 5
        succeeded?). A call that needs approval or starts a background
        session fails with a message telling the model to reissue it alone
        via call_tool - a real, disclosed limitation. Intended for
        independent reads/lookups only; operator_system.md tells the model
        so directly.

        Safe to run concurrently against the same task's metadata: each
        call gets its own ToolCallRequest (its own id, so
        tool_invocations.create() is an independent insert per call, not a
        shared row), and _record_tool_result - a plain synchronous method
        with no ``await`` inside it - runs atomically with respect to the
        single-threaded asyncio event loop, so concurrent calls can't
        interleave a read and a write against each other. Which call's
        result ends up as last_tool_name/last_tool_output_text is simply
        whichever finishes last - there is no single well-defined "last"
        call when N ran at once, and that's an acceptable, disclosed
        property of a parallel batch, not a bug.
        """
        batch_origin = f"{origin_prefix}parallel_batch:{uuid4().hex[:12]}"

        async def _one(call: ParallelToolCall) -> dict[str, Any]:
            tool_def = self.executor.tool_definitions.get(call.tool_name)
            if tool_def is None:
                return {
                    "tool_name": call.tool_name, "input": call.tool_input,
                    "status": "failed", "error": f"unregistered tool: {call.tool_name}",
                    "parallel": True, "origin": batch_origin,
                }
            request = ToolCallRequest(
                task_id=task_id, tool_name=call.tool_name, capability=tool_def.capability,
                risk_level=_effective_operator_risk(tool_def, call.tool_input, call.risk_level),
                input=call.tool_input, origin=batch_origin,
            )
            result = await self.executor.execute(request)
            if result.status == ToolResultStatus.NEEDS_APPROVAL:
                return {
                    "tool_name": call.tool_name, "input": call.tool_input,
                    "status": "failed",
                    "error": (
                        "this call needs approval, which call_tools_parallel does not support - "
                        "reissue it alone via call_tool if it actually needs to run"
                    ),
                    "parallel": True, "origin": batch_origin,
                }
            if _is_background_external_tool_result(call.tool_name, result):
                return {
                    "tool_name": call.tool_name, "input": call.tool_input,
                    "status": "failed",
                    "error": (
                        "this call started a background session, which call_tools_parallel does not "
                        "support - reissue it alone via call_tool"
                    ),
                    "parallel": True, "origin": batch_origin,
                }
            self._record_tool_result(task_id, call.tool_name, result)
            output_text = _tool_output_text(result) if result.status == ToolResultStatus.SUCCEEDED else None
            return {
                "tool_name": call.tool_name, "input": call.tool_input,
                "status": result.status.value,
                "output_summary": output_text[:2000] if output_text else None,
                "error": result.error_message,
                "parallel": True, "origin": batch_origin,
                "request_id": result.request_id,
            }

        return list(await asyncio.gather(*[_one(call) for call in calls]))

    async def _run_delegate(
        self, task: TaskRecord, decision: OperatorDecision
    ) -> tuple[TaskRecord, dict[str, Any]]:
        """Run a delegated sub-task in an isolated context (docs/HISTORY.md
        Part 3 T1.2): its own operator loop, its own history starting from
        nothing, its own fixed step budget - only a compact summary crosses
        back into the parent's history. That's the entire value: a
        long/exploratory sub-task doesn't bloat the parent's context the way
        inlining the same steps via plain call_tool would.

        The parent task's own metadata (last_tool_name, last_tool_output_text,
        etc.) IS updated by the sub-task's tool calls, deliberately, not as a
        leak: it's what lets the audit gate ground a `done` that immediately
        follows a delegate step in the sub-agent's real last tool output
        (see CONTENT_TOOLS in orchestration/auditor.py, which lists
        "delegate" for exactly this). Only the step-by-step history stays
        isolated - the whole point of the isolation is a smaller prompt for
        the parent's next decide() call, not hiding what happened.

        Deliberately narrow, same reasoning as _run_parallel_calls: a
        sub-task cannot pause for approval, wait on a background session,
        ask the user, or delegate further - each needs task-level state a
        synchronous, in-process sub-loop doesn't have. Hitting one of those
        fails the sub-task with a clear reason instead of hanging; the
        parent sees the reason in its own history and can handle that step
        directly with a normal call_tool instead of delegating it.
        """
        delegate_origin = f"subagent:{uuid4().hex[:12]}"
        objective = decision.delegate_objective or ""
        allowed_tools = set(decision.delegate_tools) if decision.delegate_tools else None
        extra_context = ""
        if allowed_tools:
            extra_context = (
                "\n\nFor this delegated sub-task, you may ONLY use these tools: "
                f"{', '.join(sorted(allowed_tools))}. Any other tool_name will be refused."
            )
        summary_input = {"objective": objective, "delegate_tools": decision.delegate_tools}
        sub_history: list[dict[str, Any]] = []

        for _step in range(self.DELEGATE_MAX_STEPS):
            try:
                sub_decision = await self.operator.decide(
                    objective, self._planner_context(extra_context), sub_history, memory_context=""
                )
            except Exception as exc:
                return task, {
                    "tool_name": "delegate", "input": summary_input, "status": "failed",
                    "output_summary": None, "error": f"sub-task decide() failed: {exc}",
                    "origin": delegate_origin,
                }
            task = self._record_llm_usage(task, "subagent", getattr(self.operator, "last_usage", None))
            self._record_llm_call(task.id, "subagent", len(sub_history), self.operator)

            if sub_decision.action == OperatorAction.DONE:
                return task, {
                    "tool_name": "delegate", "input": summary_input, "status": "succeeded",
                    "output_summary": (sub_decision.final_answer or "")[:2000], "error": None,
                    "origin": delegate_origin,
                }
            if sub_decision.action == OperatorAction.BLOCKED:
                return task, {
                    "tool_name": "delegate", "input": summary_input, "status": "failed",
                    "output_summary": None,
                    "error": f"sub-task blocked: {sub_decision.reason or 'no reason given'}",
                    "origin": delegate_origin,
                }
            if sub_decision.action == OperatorAction.ASK_USER:
                return task, {
                    "tool_name": "delegate", "input": summary_input, "status": "failed",
                    "output_summary": None,
                    "error": (
                        "sub-task needs user input, which delegation does not support: "
                        f"{sub_decision.question or ''}"
                    ),
                    "origin": delegate_origin,
                }
            if sub_decision.action == OperatorAction.DELEGATE:
                sub_history.append({
                    "tool_name": "delegate", "input": None, "status": "failed",
                    "error": "delegation is not available inside a delegated sub-task",
                })
                continue
            if sub_decision.action == OperatorAction.CALL_TOOLS_PARALLEL:
                calls = sub_decision.parallel_calls
                if allowed_tools and any(call.tool_name not in allowed_tools for call in calls):
                    sub_history.append({
                        "tool_name": "call_tools_parallel", "input": None, "status": "failed",
                        "error": f"one or more tools are not in this sub-task's allowed set: {sorted(allowed_tools)}",
                    })
                    continue
                sub_history.extend(
                    await self._run_parallel_calls(task.id, calls, origin_prefix=f"{delegate_origin}/")
                )
                continue

            # CALL_TOOL
            if allowed_tools and sub_decision.tool_name not in allowed_tools:
                sub_history.append({
                    "tool_name": sub_decision.tool_name, "input": sub_decision.tool_input, "status": "failed",
                    "error": f"tool not in this sub-task's allowed set: {sorted(allowed_tools)}",
                })
                continue
            tool_def = self.executor.tool_definitions.get(sub_decision.tool_name)
            if tool_def is None:
                sub_history.append({
                    "tool_name": sub_decision.tool_name, "input": sub_decision.tool_input, "status": "failed",
                    "error": f"unregistered tool: {sub_decision.tool_name}",
                })
                continue
            request = ToolCallRequest(
                task_id=task.id, tool_name=sub_decision.tool_name, capability=tool_def.capability,
                risk_level=_effective_operator_risk(tool_def, sub_decision.tool_input, sub_decision.risk_level),
                input=sub_decision.tool_input, origin=delegate_origin,
            )
            result = await self.executor.execute(request)
            if result.status == ToolResultStatus.NEEDS_APPROVAL:
                sub_history.append({
                    "tool_name": sub_decision.tool_name, "input": sub_decision.tool_input, "status": "failed",
                    "error": "this call needs approval, which delegation does not support",
                })
                continue
            if _is_background_external_tool_result(sub_decision.tool_name, result):
                sub_history.append({
                    "tool_name": sub_decision.tool_name, "input": sub_decision.tool_input, "status": "failed",
                    "error": "this call started a background session, which delegation does not support",
                })
                continue
            task = self._record_tool_result(task.id, sub_decision.tool_name, result)
            output_text = _tool_output_text(result) if result.status == ToolResultStatus.SUCCEEDED else None
            sub_history.append({
                "tool_name": sub_decision.tool_name, "input": sub_decision.tool_input,
                "status": result.status.value,
                "output_summary": output_text[:2000] if output_text else None,
                "error": result.error_message,
            })

        return task, {
            "tool_name": "delegate", "input": summary_input, "status": "failed",
            "output_summary": None,
            "error": f"sub-task step budget ({self.DELEGATE_MAX_STEPS}) exhausted without finishing",
            "origin": delegate_origin,
        }

    def _operator_retry_or_ask(
        self, task: TaskRecord, decision: OperatorDecision, result: ToolCallResult, history: list[dict[str, Any]]
    ) -> TaskRecord | None:
        """Rate-limit/usage-limit backoff, ported from the plan-based path's
        _retry_decision so the loop doesn't hot-loop a rate-limited API on
        every ~3s poll tick. Returns None to fall through to the normal "log
        it and let the next decide() call see it in context" handling for
        every other kind of failure - that in-context recovery is deliberate
        (docs/HISTORY.md P3 §2.2); this is narrowly about pacing, not
        diagnosis, the same split the plan-based path draws.
        """
        if self.retry_policy is None:
            return None
        if result.error_class == ErrorClass.USAGE_LIMITED:
            history.append({
                "tool_name": decision.tool_name, "input": decision.tool_input,
                "status": result.status.value, "output_summary": None, "error": result.error_message,
                "request_id": result.request_id,
            })
            latest = self.repositories.tasks.update_metadata(task.id, {**task.metadata, "operator_history": history})
            return self._operator_ask_user(
                latest, result.error_message or "This tool hit a usage limit and needs your input to continue."
            )
        retry_count = int(task.metadata.get("operator_retry_count", 0))
        retry_decision = self.retry_policy.evaluate(result, retry_count)
        if not retry_decision.retry:
            return None
        history.append({
            "tool_name": decision.tool_name, "input": decision.tool_input,
            "status": result.status.value, "output_summary": None, "error": result.error_message,
            "request_id": result.request_id,
        })
        metadata = {
            **task.metadata,
            "operator_history": history,
            "operator_retry_count": retry_decision.retry_count,
            "next_retry_at": retry_decision.next_retry_at,
        }
        return self._transition_operator(task, metadata, TaskStatus.RETRYING, retry_decision.reason)

    def _await_operator_external(
        self, task: TaskRecord, decision: OperatorDecision, result: ToolCallResult, history: list[dict[str, Any]]
    ) -> TaskRecord:
        """A tool (coding.agent today) reported a session still running in the
        background rather than a finished result. cli.py's
        _coding_session_completion_callback writes metadata["pending_tool_result"]
        and flips AWAITING_EXTERNAL back to RUNNING once the session actually
        finishes - it isn't plan-shaped (only checks task.status), so it works
        for the operator loop unchanged. _resume_operator_pending_external
        below is the other half: picking that result back up.
        """
        output = result.output if isinstance(result.output, dict) else {}
        history.append({
            "tool_name": decision.tool_name,
            "input": decision.tool_input,
            "status": "running",
            "output_summary": f"session {output.get('session_id')} started, running in background",
            "error": None,
        })
        metadata = {
            **task.metadata,
            "operator_history": history,
            "operator_pending_call": {"tool_name": decision.tool_name, "tool_input": decision.tool_input},
            "awaiting_external": {
                "tool_name": decision.tool_name,
                "session_id": str(output.get("session_id") or ""),
                "provider": output.get("provider"),
                "status": output.get("status"),
                "request_id": result.request_id,
                "started_at": output.get("started_at"),
            },
        }
        updated = self.repositories.tasks.update_metadata(task.id, metadata, TaskStatus.AWAITING_EXTERNAL)
        self.audit.task_state_changed("worker", task.id, task.status, TaskStatus.AWAITING_EXTERNAL)
        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor="worker",
            task_id=task.id,
            payload={"reason": "awaiting_external_session", "session_id": output.get("session_id"), "tool": decision.tool_name},
        )
        return updated

    def _resume_operator_pending_external(self, task: TaskRecord) -> TaskRecord:
        history: list[dict[str, Any]] = list(task.metadata.get("operator_history") or [])
        pending = task.metadata.get("pending_tool_result") or {}
        pending_call = task.metadata.get("operator_pending_call")
        tool_input = pending_call.get("tool_input") if isinstance(pending_call, dict) else None
        tool_name = str(pending.get("tool_name") or (pending_call or {}).get("tool_name") or "coding.agent")
        metadata = {**task.metadata}
        for key in ("pending_tool_result", "awaiting_external", "operator_pending_call"):
            metadata.pop(key, None)
        try:
            result = ToolCallResult.model_validate(pending.get("result"))
        except Exception:
            history.append({
                "tool_name": tool_name, "input": tool_input,
                "status": "failed", "error": "malformed pending_tool_result from external session callback",
            })
            return self.repositories.tasks.update_metadata(
                task.id, {**metadata, "operator_history": history}, TaskStatus.RUNNING
            )
        recorded = self._record_tool_result(task.id, tool_name, result)
        output_text = _tool_output_text(result) if result.status == ToolResultStatus.SUCCEEDED else None
        history.append({
            "tool_name": tool_name,
            "input": tool_input,
            "status": result.status.value,
            "output_summary": output_text[:2000] if output_text else None,
            "error": result.error_message,
        })
        final_metadata = {**metadata, **recorded.metadata, "operator_history": history}
        for key in ("pending_tool_result", "awaiting_external", "operator_pending_call"):
            final_metadata.pop(key, None)
        return self.repositories.tasks.update_metadata(task.id, final_metadata, TaskStatus.RUNNING)

    def _process_operator_retrying(self, task: TaskRecord) -> TaskRecord:
        next_retry_at = task.metadata.get("next_retry_at")
        if next_retry_at and datetime.fromisoformat(next_retry_at) > utc_now():
            return task
        return self._transition_operator(task, task.metadata, TaskStatus.RUNNING, "retry_due")

    def _await_operator_approval(
        self,
        task: TaskRecord,
        decision: OperatorDecision,
        history: list[dict[str, Any]],
        *,
        approval_id: str,
    ) -> TaskRecord:
        """self.executor.execute() above already created the ApprovalRequest
        (and its audit event) via the policy engine, same as the plan-based
        path - this only stashes what's needed to replay the exact call once
        that specific approval is granted, and surfaces a preview the same way
        _process_planned does.
        """
        preview = f"- {decision.tool_name} (risk: {decision.risk_level.value}): {json.dumps(decision.tool_input, ensure_ascii=False)}"
        metadata = {
            **task.metadata,
            "operator_history": history,
            "operator_pending_call": {
                "tool_name": decision.tool_name,
                "tool_input": decision.tool_input,
                "risk_level": decision.risk_level.value,
                "approval_id": approval_id,
            },
            "pending_approval_preview": preview,
        }
        return self._transition_operator(task, metadata, TaskStatus.AWAITING_APPROVAL, "operator_approval_required")

    async def _process_operator_awaiting_approval(self, task: TaskRecord) -> TaskRecord:
        """Resume path for _request_operator_approval above: mirrors
        _process_awaiting_approval's status handling, but replays the exact
        pending tool call (stashed in metadata["operator_pending_call"])
        instead of assuming a plan_id/current_step_id to advance to.
        """
        history: list[dict[str, Any]] = list(task.metadata.get("operator_history") or [])
        pending_call = task.metadata.get("operator_pending_call")
        if not isinstance(pending_call, dict) or not pending_call.get("tool_name"):
            return self._transition_operator(
                task, {**task.metadata, "operator_history": history}, TaskStatus.BLOCKED,
                "operator_pending_call_missing",
            )
        approval_id = str(pending_call.get("approval_id") or "")
        approval = self.repositories.approvals.get(approval_id) if approval_id else None
        if approval is None or approval.task_id != task.id:
            return self._transition_operator(
                task, {**task.metadata, "operator_history": history}, TaskStatus.BLOCKED, "approval_not_granted",
            )
        if approval.status == ApprovalStatus.PENDING:
            return task
        if approval.expires_at <= utc_now() and approval.status == ApprovalStatus.APPROVED:
            self.repositories.approvals.set_status(approval.id, ApprovalStatus.EXPIRED)
        if approval.status != ApprovalStatus.APPROVED or approval.expires_at <= utc_now():
            return self._transition_operator(
                task, {**task.metadata, "operator_history": history}, TaskStatus.BLOCKED,
                "approval_not_granted",
            )
        tool_name = str(pending_call["tool_name"])
        tool_input = pending_call.get("tool_input") or {}
        assert self.executor is not None
        tool_def = self.executor.tool_definitions.get(tool_name)
        metadata = {**task.metadata}
        metadata.pop("operator_pending_call", None)
        metadata.pop("pending_approval_preview", None)
        if tool_def is None:
            history.append({
                "tool_name": tool_name, "input": tool_input,
                "status": "failed", "error": f"unregistered tool: {tool_name}",
            })
            return self.repositories.tasks.update_metadata(
                task.id, {**metadata, "operator_history": history}, TaskStatus.RUNNING
            )
        request = ToolCallRequest(
            task_id=task.id,
            tool_name=tool_name,
            capability=tool_def.capability,
            risk_level=RiskLevel(pending_call["risk_level"]) if pending_call.get("risk_level") else RiskLevel.LOW,
            input=tool_input,
        )
        result = await self.executor.execute(request, approval_id=approval.id)
        recorded = self._record_tool_result(task.id, tool_name, result)
        output_text = _tool_output_text(result) if result.status == ToolResultStatus.SUCCEEDED else None
        history.append({
            "tool_name": tool_name,
            "input": tool_input,
            "status": result.status.value,
            "output_summary": output_text[:2000] if output_text else None,
            "error": result.error_message,
            "request_id": result.request_id,
        })
        final_metadata = {**metadata, **recorded.metadata, "operator_history": history}
        final_metadata.pop("operator_pending_call", None)
        final_metadata.pop("pending_approval_preview", None)
        return self.repositories.tasks.update_metadata(task.id, final_metadata, TaskStatus.RUNNING)

    def _transition_operator(
        self, task: TaskRecord, metadata: dict[str, Any], status: TaskStatus, reason: str
    ) -> TaskRecord:
        """Terminal-state transition for the operator loop only.

        Deliberately does NOT call _transition(): that method's COMPLETED path
        runs fulfillment-gap recovery which can attach a plan-based recovery
        plan, crossing back into the plan-once path from inside this one.
        """
        updated = self.repositories.tasks.update_metadata(task.id, metadata, status)
        self.audit.task_state_changed("worker", task.id, task.status, status)
        if status in {TaskStatus.FAILED, TaskStatus.BLOCKED}:
            self.audit.append(
                AuditEventType.ERROR, actor="worker", task_id=task.id,
                payload={"error": reason, "status": status.value},
            )
        return updated

    def _operator_ask_user(self, task: TaskRecord, question: str) -> TaskRecord:
        """The question is used verbatim - the operator already composed a
        specific question because it decided it needs one, so there's nothing
        to template from a failure reason. Two-question exhaustion limit,
        same as everywhere else in this file that can pause a task on the
        user (see clarify_count usage throughout).
        """
        latest = self.repositories.tasks.get(task.id) or task
        ask_count = int(latest.metadata.get("clarify_count", 0))
        if ask_count >= 2:
            return self._transition_operator(
                latest, latest.metadata, TaskStatus.FAILED, "clarification_attempts_exhausted"
            )
        metadata = {
            **latest.metadata,
            "clarify_count": ask_count + 1,
            "clarifying_question": question,
            "clarifying_reason": question[:400],
        }
        updated = self.repositories.tasks.update_metadata(latest.id, metadata, TaskStatus.CLARIFYING)
        self.audit.task_state_changed("worker", latest.id, latest.status, updated.status)
        self.audit.append(
            AuditEventType.TASK_STATE_CHANGED,
            actor="operator",
            task_id=latest.id,
            payload={"reason": "clarification_requested", "question": question, "clarify_count": ask_count + 1},
        )
        return updated

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
        elif task.status in {TaskStatus.RETRYING, TaskStatus.RUNNING}:
            # Key on how many steps the operator has taken, so a long task
            # sends a progress update per step instead of one "working on it"
            # and then silence. This used to key on metadata["attempt_history"]
            # + current_step_id - both plan-era fields with zero writers since
            # P3, which collapsed the key to the constant "running" and killed
            # progress reporting entirely. See docs/HISTORY.md §3.3.
            history = task.metadata.get("operator_history")
            if isinstance(history, list) and history:
                status_key = f"{task.status.value}:steps:{len(history)}"
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

    def _record_llm_usage(self, task: TaskRecord, source: str, usage: dict[str, Any] | None) -> TaskRecord:
        """Accumulate one LLM call's token usage into task.metadata["token_usage"].

        `source` is "operator" or "auditor" - the two LLM calls the worker
        itself makes per step (docs/HISTORY.md Part 4 T1.4). Deliberately
        does NOT cover the Concierge/classifier/responder calls made before a
        task exists, or coding-agent-reported usage (already tracked
        separately as last_tool_usage/last_copilot_usage in
        _record_tool_result - a different cost source with different
        pricing, not merged with this one). `usage` is None whenever the
        provider didn't report it (replay in tests, or a server that omits
        the field) - a no-op, never a fabricated zero.
        """
        if not usage:
            return task
        current = dict(task.metadata.get("token_usage") or {})
        current["calls"] = int(current.get("calls", 0)) + 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int | float):
                current[key] = int(current.get(key, 0)) + int(value)
        by_source = dict(current.get("by_source") or {})
        source_entry = dict(by_source.get(source) or {})
        source_entry["calls"] = int(source_entry.get("calls", 0)) + 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int | float):
                source_entry[key] = int(source_entry.get(key, 0)) + int(value)
        by_source[source] = source_entry
        current["by_source"] = by_source
        if usage.get("model"):
            current["last_model"] = usage["model"]
        return self.repositories.tasks.update_metadata(task.id, {**task.metadata, "token_usage": current})

    def _record_llm_call(self, task_id: str, source: str, step_index: int, service: Any) -> None:
        """Persist one LLM call's request/response/timing (docs/UI_UX_AUDIT.md
        Phase 14d) - a sibling to _record_llm_usage's running token totals
        above, not a replacement: that stays the cheap, always-on counter;
        this is the full per-call record that turns the Duration view's
        inferred "operator thinking" gaps into measured latency.

        Reads last_request/last_response_text/last_model/last_started_at/
        last_latency_ms off `service` (the OperatorLoopService or
        AuditorService instance that just made the call) - the same
        provider-to-service proxying _record_llm_usage already reads
        last_usage from. A no-op if persistence is disabled, or if the call
        didn't actually complete (no last_request/last_started_at means the
        provider raised before setting them). Best-effort: a persistence
        failure is logged, not raised - it must never block the task itself.
        """
        if not self.persist_llm_calls:
            return
        messages = getattr(service, "last_request", None)
        started_at = getattr(service, "last_started_at", None)
        if not isinstance(messages, list) or not messages or started_at is None:
            return
        usage = getattr(service, "last_usage", None) or {}
        response_text = getattr(service, "last_response_text", None)
        if isinstance(response_text, str) and len(response_text) > self.llm_call_max_chars:
            response_text = f"{response_text[: self.llm_call_max_chars]}...[truncated]"
        try:
            record = LLMCallRecord(
                task_id=task_id,
                source=source,
                model=getattr(service, "last_model", None),
                step_index=step_index,
                messages=_cap_messages(redact_payload(messages, self.redact_patterns), self.llm_call_max_chars),
                response_text=response_text,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                latency_ms=getattr(service, "last_latency_ms", None),
                created_at=started_at,
            )
            self.repositories.llm_calls.create(record)
        except Exception as exc:
            self.audit.append(
                AuditEventType.ERROR, actor="worker", task_id=task_id,
                payload={"error": "llm_call_persist_failed", "reason": str(exc)},
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


def _effective_operator_risk(tool_definition: Any, tool_input: dict[str, Any], declared: RiskLevel) -> RiskLevel:
    """Apply the runtime-owned risk floor to a model-authored tool call.

    The executor still rejects understated requests from any other caller.
    Operator decisions are normalized first so a small model cannot bypass
    policy or derail a valid task merely by labeling a write as a low-risk
    read. Invalid inputs stay untouched and are rejected by the executor's
    normal schema validation path.
    """
    try:
        validated_input = tool_definition.validate_input(tool_input)
    except ValueError:
        return declared
    required = tool_definition.required_risk(validated_input)
    risk_order = {
        RiskLevel.LOW: 1,
        RiskLevel.MEDIUM: 2,
        RiskLevel.HIGH: 3,
        RiskLevel.CRITICAL: 4,
    }
    return required if risk_order[required] > risk_order[declared] else declared

def _is_background_external_tool_result(tool_name: str | None, result: ToolCallResult) -> bool:
    if tool_name != "coding.agent" or result.status != ToolResultStatus.SUCCEEDED:
        return False
    output = result.output if isinstance(result.output, dict) else {}
    return output.get("status") == "running" and bool(output.get("session_id"))


def _last_content_tool_history_entry(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in reversed(history):
        if entry.get("status") == "succeeded" and AuditorService.is_content_tool(entry.get("tool_name")):
            return entry
    return None


def _tool_call_count(history: list[dict[str, Any]]) -> int:
    """Real tool calls only, excluding the check pseudo-entries.

    The fulfillment/audit gap paths append CHECK_* rows to `history` so the
    next decide() call sees why `done` was rejected. Those are bookkeeping,
    not work: counting them against operator_max_steps meant every gap cycle
    stole a slot from the tool calls the model needs to actually close the
    gap - with the default budget of 8, two fulfillment plus two audit gaps
    burned half of it, and a task could exhaust its budget having called zero
    tools. See docs/HISTORY.md §3.1.
    """
    return len([entry for entry in history if entry.get("tool_name") not in CHECK_ENTRY_NAMES])

def _tool_output_text(result: ToolCallResult) -> str:
    output = result.output
    if not isinstance(output, dict):
        return ""
    if _looks_like_mcp_output(output):
        return mcp_output_text(output)
    if _looks_like_http_output(output):
        if output.get("json") is not None:
            return json.dumps(output["json"], ensure_ascii=False, indent=2, default=str)
        if output.get("text"):
            return str(output["text"])
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

def _looks_like_http_output(output: dict[str, Any]) -> bool:
    return output.get("operation") == "request" and "status_code" in output and (
        "json" in output or "text" in output
    )

def _cap_messages(messages: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """Bounds one LLM call's persisted messages (docs/UI_UX_AUDIT.md Phase
    14d) - applied after redaction, per-message, so the cap can't truncate
    mid-redaction-pattern. A multimodal message's image_url parts are a
    base64 data URI, not prompt text; they're replaced with a placeholder
    rather than capped like text, since a screenshot belongs in the task's
    artifacts, not duplicated (and truncated into garbage) in a text field.
    """
    return [
        {**message, "content": _cap_message_content(message.get("content"), max_chars)}
        for message in messages
        if isinstance(message, dict)
    ]


def _cap_message_content(content: Any, max_chars: int) -> Any:
    if isinstance(content, str):
        return content if len(content) <= max_chars else f"{content[:max_chars]}...[truncated]"
    if isinstance(content, list):
        capped = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                capped.append({"type": "image_url", "image_url": {"url": "[image omitted from trace]"}})
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                capped.append({**part, "text": _cap_message_content(part["text"], max_chars)})
            else:
                capped.append(part)
        return capped
    return content


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

