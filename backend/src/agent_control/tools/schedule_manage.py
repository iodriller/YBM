from __future__ import annotations

from agent_control.scheduler import cadence_from_text, create_due_task, next_run_after, objective_from_schedule_text, schedule_to_output
from agent_control.schemas import ChannelType, ErrorClass, ScheduleRecord, ScheduleStatus, ToolCallRequest, ToolCallResult, ToolResultStatus, utc_now
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories


class ScheduleManageAdapter:
    def __init__(self, repositories: Repositories, audit: AuditLogger, *, default_timezone: str = "America/Chicago") -> None:
        self.repositories = repositories
        self.audit = audit
        self.default_timezone = default_timezone

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        operation = str(request.input.get("operation") or "create")
        try:
            if operation == "create":
                output = self._create(request)
            elif operation == "list":
                output = self._list()
            elif operation == "pause":
                output = self._status(request, ScheduleStatus.PAUSED)
            elif operation == "resume":
                output = self._status(request, ScheduleStatus.ENABLED)
            elif operation == "delete":
                output = self._status(request, ScheduleStatus.DELETED)
            elif operation == "run_now":
                output = self._run_now(request)
            else:
                return _failed(request, f"unsupported schedule operation: {operation}")
        except Exception as exc:
            return _failed(request, f"schedule operation failed: {exc}")
        output["operation"] = operation
        output["terminal_output"] = [_terminal_output(operation, output)]
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

    def _create(self, request: ToolCallRequest) -> dict:
        raw_objective = str(request.input.get("objective") or request.input.get("prompt") or "").strip()
        if not raw_objective:
            raise ValueError("objective is required")
        cadence = str(request.input.get("cadence") or cadence_from_text(raw_objective))
        objective = objective_from_schedule_text(raw_objective)
        schedule = self.repositories.schedules.create(
            ScheduleRecord(
                source_channel=ChannelType.TELEGRAM,
                source_chat_id=request.input.get("source_chat_id"),
                objective=objective,
                cadence=cadence,
                timezone=str(request.input.get("timezone") or self.default_timezone),
                next_run_at=next_run_after(cadence, utc_now()),
                metadata=dict(request.input.get("metadata") or {}),
            )
        )
        return {
            "schedule_id": schedule.id,
            "schedules": [schedule_to_output(schedule)],
            "next_run_at": schedule.next_run_at.isoformat(),
            "summary": f"Created schedule {schedule.id}: {schedule.cadence} -> {schedule.objective}",
        }

    def _list(self) -> dict:
        schedules = self.repositories.schedules.list_recent(50)
        return {
            "schedules": [schedule_to_output(schedule) for schedule in schedules],
            "summary": f"Found {len(schedules)} schedule(s).",
        }

    def _status(self, request: ToolCallRequest, status: ScheduleStatus) -> dict:
        schedule_id = str(request.input.get("schedule_id") or "")
        if not schedule_id:
            raise ValueError("schedule_id is required")
        schedule = self.repositories.schedules.update_status(schedule_id, status)
        return {
            "schedule_id": schedule.id,
            "schedules": [schedule_to_output(schedule)],
            "next_run_at": schedule.next_run_at.isoformat(),
            "summary": f"Set schedule {schedule.id} to {status.value}.",
        }

    def _run_now(self, request: ToolCallRequest) -> dict:
        schedule_id = str(request.input.get("schedule_id") or "")
        if not schedule_id:
            raise ValueError("schedule_id is required")
        schedule = self.repositories.schedules.get(schedule_id)
        if schedule is None:
            raise ValueError(f"schedule not found: {schedule_id}")
        task = create_due_task(self.repositories, self.audit, schedule)
        updated = self.repositories.schedules.get(schedule.id) or schedule
        return {
            "schedule_id": schedule.id,
            "task_id": task.id,
            "schedules": [schedule_to_output(updated)],
            "next_run_at": updated.next_run_at.isoformat(),
            "summary": f"Created task {task.id} from schedule {schedule.id}.",
        }


def _terminal_output(operation: str, output: dict) -> dict:
    return {
        "content": output.get("summary") or f"schedule.manage {operation} completed.",
        "is_final": True,
        "exit_code": 0,
    }


def _failed(request: ToolCallRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        request_id=request.id,
        status=ToolResultStatus.FAILED,
        error_class=ErrorClass.ADAPTER_FAILED,
        error_message=message,
    )
