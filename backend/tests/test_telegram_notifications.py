from __future__ import annotations

from agent_control.channels.telegram_notifications import _task_message
from agent_control.schemas import TaskRecord, TaskStatus


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
