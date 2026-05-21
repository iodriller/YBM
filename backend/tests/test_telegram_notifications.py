from __future__ import annotations

from pathlib import Path

import pytest

from agent_control.channels.telegram_notifications import TelegramTaskNotifier, _task_message, _task_message_without_screenshot
from agent_control.schemas import TaskRecord, TaskStatus


class FakeTelegramClient:
    def __init__(self, fail_photo: bool = False) -> None:
        self.fail_photo = fail_photo
        self.messages: list[tuple[str | int, str]] = []
        self.photos: list[tuple[str | int, str, str | None]] = []

    async def send_message(self, chat_id: str | int, text: str) -> dict:
        self.messages.append((chat_id, text))
        return {"ok": True}

    async def send_photo_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict:
        if self.fail_photo:
            raise RuntimeError("sendPhoto rejected")
        self.photos.append((chat_id, path, caption))
        return {"ok": True}


def test_task_message_prioritizes_result_links() -> None:
    task = TaskRecord(
        objective="Create a duck app and launch it",
        status=TaskStatus.COMPLETED,
        metadata={
            "preview_url": "http://127.0.0.1:8890/",
            "workspace_dir": "C:/tmp/duck",
            "last_tool_name": "workspace.manage",
            "last_tool_result": {
                "output": {
                    "terminal_output": [
                        {
                            "content": "Workspace operation completed: launch_static",
                            "is_final": True,
                            "exit_code": 0,
                        }
                    ]
                }
            },
        },
    )

    message = _task_message(task)

    assert message.startswith("Done: Create a duck app and launch it")
    assert "Result: http://127.0.0.1:8890/" in message
    assert "Workspace: C:/tmp/duck" in message
    assert "Tool: workspace.manage" in message


def test_task_message_reports_gap_and_retry_on_blocked_task() -> None:
    task = TaskRecord(
        objective="Create an app and launch it",
        status=TaskStatus.BLOCKED,
        metadata={
            "fulfillment_gap": "expected_preview_url_missing",
            "fulfillment_retry_count": 2,
            "last_tool_result": {
                "error_message": "assistant output did not include materializable static app files",
                "output": {},
            },
        },
    )

    message = _task_message(task)

    assert message.startswith("Blocked: Create an app and launch it")
    assert "Gap: expected_preview_url_missing" in message
    assert "Retries: 2" in message
    assert "Error: assistant output did not include materializable static app files" in message


def test_task_message_without_screenshot_keeps_photo_path_out_of_text(tmp_path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"png")
    task = TaskRecord(
        objective="Take a screenshot",
        status=TaskStatus.COMPLETED,
        metadata={
            "source_chat_id": "100",
            "screenshot_path": str(screenshot),
            "last_tool_name": "computer.use",
        },
    )

    message = _task_message_without_screenshot(task)

    assert "Screenshot:" not in message
    assert str(screenshot) not in message
    assert "Tool: computer.use" in message


@pytest.mark.asyncio
async def test_notifier_sends_screenshot_as_photo(tmp_path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"png")
    task = TaskRecord(
        objective="Take a screenshot of my desktop and send it to me now",
        status=TaskStatus.COMPLETED,
        metadata={
            "source_chat_id": "100",
            "last_tool_name": "computer.use",
            "last_tool_result": {"output": {"screenshot_path": str(screenshot), "final_summary": "Desktop is visible."}},
        },
    )
    client = FakeTelegramClient()

    await TelegramTaskNotifier(client).notify(task)  # type: ignore[arg-type]

    assert client.messages == [("100", _task_message_without_screenshot(task))]
    assert client.photos == [("100", str(screenshot), "Screenshot - Take a screenshot of my desktop and send it to me now")]


@pytest.mark.asyncio
async def test_notifier_reports_photo_delivery_failure(tmp_path) -> None:
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"png")
    task = TaskRecord(
        objective="Take a screenshot",
        status=TaskStatus.COMPLETED,
        metadata={"source_chat_id": "100", "screenshot_path": Path(screenshot).resolve().as_uri()},
    )
    client = FakeTelegramClient(fail_photo=True)

    await TelegramTaskNotifier(client).notify(task)  # type: ignore[arg-type]

    assert len(client.messages) == 2
    assert "Telegram photo delivery failed" in client.messages[1][1]
    assert str(screenshot) in client.messages[1][1]
