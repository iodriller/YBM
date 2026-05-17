from __future__ import annotations

from agent_control.channels.telegram import TelegramBotApi
from agent_control.schemas import TaskRecord, TaskStatus


class TelegramTaskNotifier:
    def __init__(self, client: TelegramBotApi) -> None:
        self.client = client

    async def notify(self, task: TaskRecord) -> None:
        chat_id = _task_chat_id(task)
        if not chat_id:
            return
        await self.client.send_message(chat_id, _task_message(task))


def _task_chat_id(task: TaskRecord) -> str | None:
    value = task.metadata.get("source_chat_id")
    if value:
        return str(value)
    if task.conversation_id and task.conversation_id.startswith("conv_telegram_"):
        return task.conversation_id.removeprefix("conv_telegram_")
    return None


def _task_message(task: TaskRecord) -> str:
    header = {
        TaskStatus.COMPLETED: "Task completed",
        TaskStatus.FAILED: "Task failed",
        TaskStatus.BLOCKED: "Task blocked",
        TaskStatus.CANCELLED: "Task cancelled",
        TaskStatus.AWAITING_APPROVAL: "Task awaiting approval",
    }.get(task.status, f"Task {task.status.value}")

    lines = [
        f"{header}: {task.id}",
        f"Status: {task.status.value}",
        f"Objective: {_trim(task.objective, 240)}",
    ]

    tool_name = task.metadata.get("last_tool_name")
    if tool_name:
        lines.append(f"Tool: {tool_name}")

    command_id = _last_command_id(task)
    if command_id:
        lines.append(f"Command: {command_id}")

    usage = _last_usage(task)
    if usage:
        lines.append(f"Usage: {usage}")

    output = _last_output(task)
    if output:
        lines.append("")
        lines.append(_trim(output, 3200))

    error = _last_error(task)
    if error:
        lines.append("")
        lines.append(f"Error: {_trim(error, 1200)}")

    return _trim("\n".join(lines), 3900)


def _last_command_id(task: TaskRecord) -> str | None:
    result = task.metadata.get("last_tool_result")
    if not isinstance(result, dict):
        return None
    output = result.get("output")
    if isinstance(output, dict):
        command_id = output.get("command_id")
        if command_id:
            return str(command_id)
    return None


def _last_output(task: TaskRecord) -> str | None:
    result = task.metadata.get("last_tool_result")
    if not isinstance(result, dict):
        return None
    output = result.get("output")
    if isinstance(output, dict):
        terminal_output = output.get("terminal_output")
        if isinstance(terminal_output, list) and terminal_output:
            last = terminal_output[-1]
            if isinstance(last, dict) and last.get("content"):
                return str(last["content"]).strip()
        for key in ("stdout", "response", "text"):
            if output.get(key):
                return str(output[key]).strip()
    return None


def _last_usage(task: TaskRecord) -> str | None:
    result = task.metadata.get("last_tool_result")
    if not isinstance(result, dict):
        return None
    output = result.get("output")
    if not isinstance(output, dict):
        return None
    usage = output.get("usage")
    if not isinstance(usage, dict) or not usage:
        return None
    return " | ".join(str(value) for _, value in sorted(usage.items()))


def _last_error(task: TaskRecord) -> str | None:
    result = task.metadata.get("last_tool_result")
    if not isinstance(result, dict):
        return task.metadata.get("last_worker_error")
    value = result.get("error_message") or task.metadata.get("last_worker_error")
    return str(value) if value else None


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."
