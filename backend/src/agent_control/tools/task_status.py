from __future__ import annotations

from typing import Any

from agent_control.schemas import TaskStatus, ToolCallRequest, ToolCallResult, ToolResultStatus
from agent_control.storage.repositories import Repositories


class TaskStatusAdapter:
    """Report current task and plan state for status questions."""

    def __init__(self, repositories: Repositories) -> None:
        self.repositories = repositories

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        limit = int(request.input.get("limit") or 10)
        recent = self.repositories.tasks.list_recent(limit=limit)
        active_statuses = {
            TaskStatus.RECEIVED,
            TaskStatus.INTERPRETING,
            TaskStatus.PLANNED,
            TaskStatus.RUNNING,
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.RETRYING,
        }
        active = [task for task in recent if task.status in active_statuses]
        task_rows = []
        for task in recent:
            plan_summary: dict[str, Any] | None = None
            if task.plan_id:
                plan = self.repositories.plans.get(task.plan_id)
                if plan is not None:
                    plan_summary = {
                        "id": plan.id,
                        "objective": plan.objective,
                        "step_count": len(plan.steps),
                        "current_step_id": task.current_step_id,
                    }
            task_rows.append(
                {
                    "id": task.id,
                    "status": task.status.value,
                    "objective": task.objective,
                    "updated_at": task.updated_at.isoformat(),
                    "plan": plan_summary,
                    "last_tool_name": task.metadata.get("last_tool_name"),
                    "last_error": task.metadata.get("last_error"),
                }
            )

        summary_lines = [f"Status: {len(recent)} recent task(s), {len(active)} active."]
        if active:
            summary_lines.append("Current active work:")
            summary_lines.extend(f"- {task.id}: {task.status.value} - {task.objective[:120]}" for task in active[:5])
        else:
            summary_lines.append("Current active work: none.")
        if recent:
            completed = [task for task in recent if task.status == TaskStatus.COMPLETED]
            blocked = [task for task in recent if task.status in {TaskStatus.BLOCKED, TaskStatus.FAILED}]
            summary_lines.append(f"Completed recently: {len(completed)}.")
            summary_lines.append(f"Blocked or failed recently: {len(blocked)}.")
            summary_lines.append("Latest task:")
            summary_lines.append(f"- {recent[0].id}: {recent[0].status.value} - {recent[0].objective[:160]}")

        output = {
            "operation": "status",
            "summary": "\n".join(summary_lines),
            "task_status": {
                "recent_count": len(recent),
                "active_count": len(active),
                "recent_tasks": task_rows,
            },
            "plan": next((row["plan"] for row in task_rows if row.get("plan")), None),
            "terminal_output": [{"content": "\n".join(summary_lines), "exit_code": 0, "is_final": True}],
        }
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)
