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
    if task.status == TaskStatus.COMPLETED:
        lines = [f"Done: {_trim(task.objective, 220)}"]
    elif task.status == TaskStatus.AWAITING_APPROVAL:
        lines = [f"Approval needed: {_trim(task.objective, 220)}"]
    elif task.status == TaskStatus.BLOCKED:
        lines = [f"Blocked: {_trim(task.objective, 220)}"]
    elif task.status == TaskStatus.FAILED:
        lines = [f"Could not finish: {_trim(task.objective, 220)}"]
    elif task.status == TaskStatus.CANCELLED:
        lines = [f"Cancelled: {_trim(task.objective, 220)}"]
    else:
        lines = [f"Task {task.status.value}: {_trim(task.objective, 220)}"]

    lines.extend(_result_lines(task))
    if task.status in {TaskStatus.BLOCKED, TaskStatus.FAILED, TaskStatus.AWAITING_APPROVAL}:
        lines.extend(_failure_lines(task))

    tool_name = task.metadata.get("last_tool_name")
    if tool_name:
        lines.append(f"Tool: {tool_name}")

    command_id = _last_command_id(task)
    if command_id:
        lines.append(f"Command: {command_id}")

    usage = _last_usage(task)
    if usage:
        lines.append(f"Usage: {usage}")

    lines.append(f"Task: {task.id}")
    if task.status != TaskStatus.COMPLETED:
        lines.append(f"Status: {task.status.value}")

    output = _last_output(task)
    if output:
        lines.append("")
        lines.append(f"Summary: {_trim(output, 2200)}")

    error = _last_error(task)
    if error and task.status not in {TaskStatus.BLOCKED, TaskStatus.FAILED}:
        lines.append("")
        lines.append(f"Error: {_trim(error, 1200)}")

    return _trim("\n".join(lines), 3900)


def _result_lines(task: TaskRecord) -> list[str]:
    lines = []
    pull_request = (
        task.metadata.get("pull_request_url")
        or task.metadata.get("pr_url")
        or _output_value(task, "pull_request_url")
        or _output_value(task, "html_url")
    )
    screenshot = (
        task.metadata.get("screenshot_uri")
        or task.metadata.get("screenshot_path")
        or _output_value(task, "screenshot_uri")
        or _output_value(task, "screenshot_path")
    )
    for label, value in (
        ("Result", task.metadata.get("preview_url") or _output_value(task, "url")),
        ("Workspace", task.metadata.get("workspace_dir") or _output_value(task, "workspace_dir")),
        ("Adapter", task.metadata.get("adapter_dir") or _output_value(task, "adapter_dir")),
        ("Pull request", pull_request),
        ("Screenshot", screenshot),
    ):
        if value:
            lines.append(f"{label}: {value}")
    return lines


def _failure_lines(task: TaskRecord) -> list[str]:
    lines = []
    gap = task.metadata.get("fulfillment_gap")
    if gap:
        lines.append(f"Gap: {gap}")
    retry_count = task.metadata.get("retry_count") or task.metadata.get("fulfillment_retry_count")
    if retry_count:
        lines.append(f"Retries: {retry_count}")
    intervention = task.metadata.get("intervention_summary")
    if intervention:
        lines.append(f"Next step: {_trim(str(intervention), 300)}")
    error = _last_error(task)
    if error:
        lines.append(f"Error: {_trim(error, 900)}")
    return lines


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


def _output_value(task: TaskRecord, key: str) -> str | None:
    result = task.metadata.get("last_tool_result")
    if not isinstance(result, dict):
        return None
    output = result.get("output")
    if isinstance(output, dict) and output.get(key):
        return str(output[key])
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
    usage = None
    if isinstance(result, dict):
        output = result.get("output")
        if isinstance(output, dict):
            usage = output.get("usage")
    if not usage:
        usage = task.metadata.get("last_copilot_usage") or task.metadata.get("last_tool_usage")
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
