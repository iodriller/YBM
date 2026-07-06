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
    return _user_facing_task_message(task)


def _user_facing_task_message(task: TaskRecord) -> str:
    if task.status == TaskStatus.RECEIVED:
        return "Got your message, working on it now…"
    if task.status == TaskStatus.RUNNING:
        return "On it."
    if task.status == TaskStatus.RETRYING:
        return "That attempt did not work, so I am trying a different approach now."
    if task.status == TaskStatus.AWAITING_APPROVAL:
        return _trim(
            "I need approval before I can continue. If full access is enabled for this capability, the worker will approve it automatically; otherwise approve it from the admin UI.",
            3900,
        )
    if task.status == TaskStatus.AWAITING_EXTERNAL:
        session = task.metadata.get("awaiting_external") if isinstance(task.metadata, dict) else None
        if isinstance(session, dict) and session.get("provider"):
            return f"{session['provider']} is working in the background. I will report back when it finishes."
        return "An external tool is working in the background. I will report back when it finishes."
    if task.status == TaskStatus.CANCELLED:
        return "Cancelled."
    if task.status == TaskStatus.CLARIFYING:
        question = str(task.metadata.get("clarifying_question") or "").strip()
        if question:
            return _trim(f"{question}\n\n(Reply here to continue this task, or say 'cancel'.)", 3900)
        return "I need more input to continue this task — reply here with details, or say 'cancel'."
    if task.status in {TaskStatus.BLOCKED, TaskStatus.FAILED}:
        error = _last_error(task)
        first_line = _failure_headline(task, error)
        lines = [first_line]
        lines.extend(_failure_lines(task))
        return _trim("\n".join(lines), 3900)
    if task.status != TaskStatus.COMPLETED:
        return f"Status: {task.status.value}."

    # Synthesizer-produced focused answer wins over raw tool output.
    # The synthesizer was specifically prompted with the user's objective and
    # extracts ONLY what was asked for — sending the raw page dump instead
    # defeats the entire purpose of synthesis.
    synthesized = str(task.metadata.get("synthesized_answer") or "").strip()
    if synthesized:
        return _trim(synthesized, 3900)

    answer = _completed_answer(task)
    if answer:
        return _trim(answer, 3900)
    output = _last_output(task)
    if output:
        return _trim(output, 3900)
    return "Done."


def _legacy_task_message_without_screenshot(task: TaskRecord) -> str:
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


def _completed_answer(task: TaskRecord) -> str | None:
    result = task.metadata.get("last_tool_result")
    output = result.get("output") if isinstance(result, dict) else None
    if not isinstance(output, dict):
        return None
    tool_name = str(task.metadata.get("last_tool_name") or result.get("tool_name") or "")
    operation = str(output.get("operation") or "")

    if tool_name == "filesystem.manage":
        return _filesystem_answer(operation, output)
    if tool_name == "document.manage":
        return _document_answer(output)
    if tool_name in {"browser.open", "browser.control"}:
        return _browser_answer(operation, output)
    if tool_name == "computer.use":
        return _computer_answer(output)
    if tool_name == "code.interpreter":
        return _code_interpreter_answer(output)
    if tool_name == "workspace.manage":
        return _workspace_answer(output)
    if tool_name == "artifact.deliver":
        return _artifact_answer(output)
    if tool_name == "task.status":
        return str(output.get("summary") or output.get("text") or _last_output(task) or "").strip() or None
    return None


def _filesystem_answer(operation: str, output: dict) -> str:
    root = output.get("root")
    path = output.get("path")
    entries = output.get("entries") if isinstance(output.get("entries"), list) else []
    if operation == "read_file":
        text = str(output.get("text") or output.get("content_preview") or "").strip()
        header = f"I read {Path(str(path)).name if path else 'the file'}."
        if text:
            return f"{header}\n\n{text[:3400]}"
        return f"{header}\n\nNo readable text was extracted."
    if operation in {"inspect_folder", "collect_folder_snapshot"}:
        lines = [f"I found {len(entries)} item(s)" + (f" in {root}." if root else ".")]
        lines.extend(_entry_lines(entries, include_preview=False, limit=80))
        return "\n".join(lines)
    if operation == "search":
        lines = [f"I found {len(entries)} matching item(s)" + (f" under {root}." if root else ".")]
        lines.extend(_entry_lines(entries, include_preview=True, limit=12))
        return "\n".join(lines)
    if operation == "describe_folder":
        lines = [str(output.get("summary") or f"I described {len(entries)} file(s).")]
        lines.extend(_entry_lines(entries, include_preview=True, limit=20))
        return "\n".join(lines)
    if operation == "write_text_file":
        return f"I created the file:\n{path}"
    changed = output.get("changed_paths") if isinstance(output.get("changed_paths"), list) else []
    if changed:
        lines = [str(output.get("summary") or f"Changed {len(changed)} path(s).")]
        lines.extend(f"- {item}" for item in changed[:40])
        return "\n".join(lines)
    return str(output.get("summary") or "Filesystem request completed.")


def _entry_lines(entries: list, *, include_preview: bool, limit: int) -> list[str]:
    lines: list[str] = []
    for item in entries[:limit]:
        if not isinstance(item, dict):
            continue
        kind = "folder" if item.get("is_dir") else "file"
        name = item.get("relative_path") or item.get("path") or ""
        size = item.get("size_bytes")
        size_text = f" ({size} bytes)" if size is not None and kind == "file" else ""
        lines.append(f"- [{kind}] {name}{size_text}")
        if include_preview:
            preview = item.get("content_summary") or item.get("content_preview") or item.get("ocr_text")
            if preview:
                lines.append(f"  {str(preview)[:900]}")
    if len(entries) > limit:
        lines.append(f"- ... {len(entries) - limit} more item(s)")
    return lines


def _document_answer(output: dict) -> str:
    path = output.get("path")
    summary = output.get("summary") or output.get("text")
    if path and summary:
        return f"Here is what I found in {Path(str(path)).name}:\n\n{str(summary)[:3400]}"
    if summary:
        return str(summary)
    if path:
        return f"Document output created:\n{path}"
    return "Document task completed."


def _browser_answer(operation: str, output: dict) -> str:
    title = output.get("page_title") or "Untitled page"
    url = output.get("url") or output.get("browser_url")
    summary = str(output.get("summary") or "").strip()
    lower_summary = summary.lower()
    if "chatgpt" in str(url).lower() and any(marker in lower_summary for marker in ("log in", "sign up", "sign in", "login")):
        return (
            f"I opened ChatGPT, but the page appears to require login before I can send the prompt.\n\n"
            f"Page: {title}\nURL: {url}\n\n"
            "Log in in the opened browser session, then ask me to continue, or tell me to use normal web search instead."
        )
    lines = [f"Page: {title}"]
    if url:
        lines.append(f"URL: {url}")
    if output.get("visited_urls"):
        lines.append(f"Visited {len(output['visited_urls'])} page(s).")
    if summary:
        lines.append("")
        lines.append(summary[:3200])
    screenshot = output.get("screenshot_path")
    if screenshot:
        lines.append("")
        lines.append(f"Screenshot saved locally: {screenshot}")
    return "\n".join(lines)


def _computer_answer(output: dict) -> str:
    summary = str(output.get("final_summary") or output.get("summary") or "").strip()
    observation = output.get("observation") if isinstance(output.get("observation"), dict) else {}
    lines = []
    if summary:
        lines.append(summary)
    active = observation.get("active_window") if isinstance(observation, dict) else None
    if isinstance(active, dict) and active.get("title"):
        lines.append(f"Active window: {active['title']}")
    windows = observation.get("visible_windows") if isinstance(observation, dict) else None
    if isinstance(windows, list) and windows:
        lines.append("Visible windows:")
        for item in windows[:10]:
            if isinstance(item, dict) and item.get("title"):
                lines.append(f"- {item['title']}")
    screenshot = output.get("screenshot_path") or output.get("screenshot_uri")
    if screenshot:
        lines.append(f"Screenshot captured: {screenshot}")
    return "\n".join(lines) or "I inspected the desktop."


def _code_interpreter_answer(output: dict) -> str:
    summary = str(output.get("summary") or "").strip()
    stdout = str(output.get("stdout") or "").strip()
    stderr = str(output.get("stderr") or "").strip()
    workspace = output.get("workspace_dir")
    files = output.get("files_created") if isinstance(output.get("files_created"), list) else []

    lines: list[str] = []
    if stdout:
        lines.append(stdout[:3000])
    elif summary:
        lines.append(summary)
    else:
        lines.append("The script ran successfully.")

    if files:
        lines.append("")
        lines.append("Created files:")
        lines.extend(f"- {item}" for item in files[:40])
    if workspace:
        lines.append(f"Workspace: {workspace}")
    if stderr:
        lines.append("")
        lines.append("Errors:")
        lines.append(stderr[:1200])
    if stdout and summary and summary not in stdout:
        lines.append("")
        lines.append(summary)
    return "\n".join(lines)


def _workspace_answer(output: dict) -> str:
    url = output.get("preview_url") or output.get("url")
    workspace = output.get("workspace_dir")
    lines = [str(output.get("summary") or "The local workspace is ready.")]
    if url:
        lines.append(f"Open it here: {url}")
    if workspace:
        lines.append(f"Workspace: {workspace}")
    return "\n".join(lines)


def _artifact_answer(output: dict) -> str:
    if output.get("delivered"):
        path = output.get("path")
        return "I sent the file or screenshot." + (f"\n{path}" if path else "")
    return str(output.get("summary") or "Artifact delivery completed.")


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


def _failure_headline(task: TaskRecord, error: str | None) -> str:
    if not error:
        return "I could not complete this request."
    low = error.lower()
    if "chrome" in low or "devtools" in low or "browser" in low or "websocket" in low:
        return (
            "Browser task failed — Chrome is not running with remote debugging enabled.\n"
            "Start Chrome with: chrome --remote-debugging-port=9222 --remote-allow-origins=*"
        )
    if "planning" in low or "plan failed" in low or "no plan" in low:
        return "I could not plan this task."
    if "capability" in low or "disabled" in low or "not enabled" in low:
        return "A required capability is not enabled for this task."
    return "I could not complete this request."


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
    fallback = (
        task.metadata.get("last_worker_error")
        or task.metadata.get("planning_error")
        or task.metadata.get("last_replan_reason")
    )
    result = task.metadata.get("last_tool_result")
    if not isinstance(result, dict):
        return fallback
    value = result.get("error_message") or fallback
    return str(value) if value else None


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."
