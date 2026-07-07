from __future__ import annotations

from pathlib import Path

import pytest

from agent_control.channels.telegram_notifications import TelegramTaskNotifier, _task_message, _task_message_without_screenshot, _user_facing_task_message
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
    assert "Tool:" not in message


def test_completed_filesystem_search_message_includes_file_contents() -> None:
    task = TaskRecord(
        objective="Find the resume notes file from my desktop and read it to me.",
        status=TaskStatus.COMPLETED,
        metadata={
            "last_tool_name": "filesystem.manage",
            "last_tool_result": {
                "output": {
                    "operation": "search",
                    "root": "C:/Users/oneye/Desktop",
                    "entries": [
                        {
                            "relative_path": "oney-resume-notes.txt",
                            "is_dir": False,
                            "size_bytes": 100,
                            "content_preview": "Oney resume notes include Python automation and local LLM orchestration.",
                        }
                    ],
                }
            },
        },
    )

    message = _task_message_without_screenshot(task)

    assert "oney-resume-notes.txt" in message
    assert "Python automation" in message
    assert "Task:" not in message
    assert "Tool:" not in message


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


def test_code_interpreter_response_shows_stdout_as_primary_content() -> None:
    task = TaskRecord(
        objective="Run a data processing script",
        status=TaskStatus.COMPLETED,
        metadata={
            "last_tool_name": "code.interpreter",
            "last_tool_result": {
                "output": {
                    "operation": "run_python",
                    "stdout": "Total: 190\nFiles processed: 3",
                    "summary": "Script ran successfully.",
                    "files_created": ["expense-summary.json"],
                    "workspace_dir": "/tmp/code/task_abc",
                }
            },
        },
    )

    message = _task_message_without_screenshot(task)

    assert "Total: 190" in message
    assert "expense-summary.json" in message
    stdout_pos = message.index("Total: 190")
    files_pos = message.index("expense-summary.json")
    assert stdout_pos < files_pos
    assert "Task:" not in message
    assert "Tool:" not in message
    assert "Command:" not in message


def test_mcp_response_shows_tool_result_content() -> None:
    task = TaskRecord(
        objective="Echo hello",
        status=TaskStatus.COMPLETED,
        metadata={
            "last_tool_name": "mcp.client",
            "last_tool_result": {
                "output": {
                    "operation": "call_tool",
                    "summary": "Called MCP tool fake.echo.",
                    "result": {"content": [{"type": "text", "text": "hello from E2E"}]},
                    "selected_tool": {"server": "fake", "tool": "echo"},
                }
            },
        },
    )

    message = _task_message_without_screenshot(task)

    assert "hello from E2E" in message
    assert "Task:" not in message


def test_completed_task_messages_do_not_leak_internal_ids() -> None:
    for tool_name, output in [
        ("filesystem.manage", {"operation": "inspect_folder", "root": "C:/Desktop", "entries": []}),
        ("browser.open", {"operation": "open", "url": "https://example.com", "page_title": "Example", "summary": "A page."}),
        ("code.interpreter", {"operation": "run_python", "stdout": "done", "summary": "ran", "files_created": [], "workspace_dir": "/tmp"}),
    ]:
        task = TaskRecord(
            objective="Test task",
            status=TaskStatus.COMPLETED,
            metadata={"last_tool_name": tool_name, "last_tool_result": {"output": output}},
        )
        message = _task_message_without_screenshot(task)
        assert "Task:" not in message, f"Task ID leaked for {tool_name}"
        assert "Command:" not in message, f"Command ID leaked for {tool_name}"


def test_user_facing_message_received_status_is_friendly() -> None:
    task = TaskRecord(objective="Find my resume", status=TaskStatus.RECEIVED)
    message = _user_facing_task_message(task)
    lower = message.lower()
    assert "working on it" in lower or "got your message" in lower or "on it" in lower


def test_user_facing_message_retrying_status_explains_retry() -> None:
    task = TaskRecord(objective="Do something", status=TaskStatus.RETRYING)
    message = _user_facing_task_message(task)
    lower = message.lower()
    assert "different" in lower or "trying" in lower or "approach" in lower or "retry" in lower
