from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse
from pathlib import Path

from agent_control.channels.task_notify import format_task_message
from agent_control.channels.telegram import TelegramBotApi
from agent_control.schemas import ApprovalStatus, ChannelType, TaskRecord, TaskStatus, channel_chat_id
from agent_control.storage.repositories import ApprovalRepository


def _approval_inline_keyboard(approval_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"approval:{approval_id}:approve"},
                {"text": "Reject", "callback_data": f"approval:{approval_id}:reject"},
            ]
        ]
    }


class TelegramTaskNotifier:
    def __init__(self, client: TelegramBotApi, approvals: ApprovalRepository | None = None) -> None:
        self.client = client
        # Optional: lets an AWAITING_APPROVAL notification carry a real
        # Approve/Reject inline keyboard instead of only the plain-text
        # "reply 'approve'" instruction. None (e.g. in tests) just skips it.
        self.approvals = approvals

    async def notify(self, task: TaskRecord) -> None:
        chat_id = channel_chat_id(task, ChannelType.TELEGRAM)
        if not chat_id:
            return

        # Send text message without screenshot line
        message_text = format_task_message(task)
        reply_markup = self._pending_approval_keyboard(task)
        await self.client.send_message(chat_id, message_text, reply_markup=reply_markup)

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

    def _pending_approval_keyboard(self, task: TaskRecord) -> dict[str, Any] | None:
        if self.approvals is None or task.status != TaskStatus.AWAITING_APPROVAL:
            return None
        pending = [a for a in self.approvals.list_for_task(task.id) if a.status == ApprovalStatus.PENDING]
        if not pending:
            return None
        # The operator loop stashes exactly one pending tool call at a time
        # (metadata["operator_pending_call"]), so there is always at most one
        # approval to act on here even if list_for_task returns older ones.
        return _approval_inline_keyboard(pending[-1].id)


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


def _output_value(task: TaskRecord, key: str) -> str | None:
    result = task.metadata.get("last_tool_result")
    if not isinstance(result, dict):
        return None
    output = result.get("output")
    if isinstance(output, dict) and output.get(key):
        return str(output[key])
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


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."
