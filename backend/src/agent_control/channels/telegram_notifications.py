from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from agent_control.channels.telegram import TelegramBotApi
from agent_control.schemas import TaskRecord, TaskStatus


class TelegramTaskNotifier:
    def __init__(self, client: TelegramBotApi) -> None:
        self.client = client

    async def notify(self, task: TaskRecord) -> None:
        chat_id = _task_chat_id(task)
        if not chat_id:
            return

        # Send text message without screenshot line
        message_text = _task_message_without_screenshot(task)
        await self.client.send_message(chat_id, message_text)

        # Send screenshot as separate photo if available
        screenshot_path = _get_screenshot_path(task)
        if screenshot_path:
            try:
                caption = f"Screenshot - {_trim(task.objective, 80)}"
                await self.client.send_photo_file(chat_id, screenshot_path, caption)
            except Exception as exc:
                await self.client.send_message(
                    chat_id,
                    "Screenshot was captured, but Telegram photo delivery failed.\n"
                    f"Local file: {screenshot_path}\n"
                    f"Error: {_trim(str(exc), 600)}",
                )


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


def _task_message_without_screenshot(task: TaskRecord) -> str:
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

    lines.extend(_result_lines_without_screenshot(task))
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
    result_url = task.metadata.get("preview_url") or _output_value(task, "url")
    browser_url = task.metadata.get("browser_url") or _output_value(task, "browser_url")
    for label, value in (
        ("Result", result_url),
        ("Browser", browser_url if browser_url != result_url else None),
        ("Workspace", task.metadata.get("workspace_dir") or _output_value(task, "workspace_dir")),
        ("Adapter", task.metadata.get("adapter_dir") or _output_value(task, "adapter_dir")),
        ("Pull request", pull_request),
        ("Screenshot", screenshot),
    ):
        if value:
            lines.append(f"{label}: {value}")
    return lines


def _result_lines_without_screenshot(task: TaskRecord) -> list[str]:
    """Same as _result_lines but excludes screenshot to avoid showing path as link."""
    lines = []
    pull_request = (
        task.metadata.get("pull_request_url")
        or task.metadata.get("pr_url")
        or _output_value(task, "pull_request_url")
        or _output_value(task, "html_url")
    )
    result_url = task.metadata.get("preview_url") or _output_value(task, "url")
    browser_url = task.metadata.get("browser_url") or _output_value(task, "browser_url")
    for label, value in (
        ("Result", result_url),
        ("Browser", browser_url if browser_url != result_url else None),
        ("Workspace", task.metadata.get("workspace_dir") or _output_value(task, "workspace_dir")),
        ("Adapter", task.metadata.get("adapter_dir") or _output_value(task, "adapter_dir")),
        ("Pull request", pull_request),
    ):
        if value:
            lines.append(f"{label}: {value}")
    return lines


def _get_screenshot_path(task: TaskRecord) -> str | None:
    for value in (
        task.metadata.get("screenshot_path"),
        _output_value(task, "screenshot_path"),
        task.metadata.get("screenshot_uri"),
        _output_value(task, "screenshot_uri"),
    ):
        path = _path_from_screenshot_value(value)
        if path is not None:
            return str(path)
    return None


def _path_from_screenshot_value(value: object) -> Path | None:
    if not value:
        return None
    text = str(value)
    if text.startswith("file:///"):
        parsed = urlparse(text)
        raw_path = unquote(parsed.path)
        if raw_path.startswith("/") and len(raw_path) > 3 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        path = Path(raw_path)
    else:
        path = Path(text)
    return path if path.exists() and path.is_file() else None


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
        for key in ("final_summary", "summary", "stdout", "response", "text"):
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
