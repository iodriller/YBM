"""Fulfillment checks for the Operator loop.

Expectations come from objective text only - the plan-derived path
(`plan.postconditions` and tool-name inference) went away with the plan-once
execution path, since nothing creates a PlanModel anymore (docs/HISTORY.md §1.1).

Tests are split accordingly:
- `validate_fulfillment()` for the end-to-end path, driven by objective wording.
- `_postcondition_satisfied()` directly for satisfaction rules whose
  postcondition objective-text inference doesn't produce on its own. Those
  rules are still live and load-bearing (a `run_goal` that stopped early or a
  coding agent still running must NOT count as done) - previously they were
  only reachable in tests via a hand-built plan, which no longer exists.
"""

from __future__ import annotations

from agent_control.orchestration.fulfillment import _postcondition_satisfied, validate_fulfillment
from agent_control.schemas import PostconditionType, TaskRecord


def test_browser_state_postcondition_satisfied_from_objective_text() -> None:
    task = TaskRecord(
        objective="Open the browser, search docs, and tell me what you see",
        metadata={
            "last_tool_result": {
                "output": {
                    "browser_url": "https://example.test",
                    "page_title": "Example",
                }
            }
        },
    )

    validation = validate_fulfillment(task)

    assert validation.ok


def test_external_command_postcondition_requires_successful_exit() -> None:
    task = TaskRecord(
        objective="Run a terminal command",
        metadata={
            "last_tool_result": {
                "output": {
                    "terminal_output": [
                        {
                            "content": "failed",
                            "is_final": True,
                            "exit_code": 1,
                        }
                    ]
                }
            }
        },
    )

    validation = validate_fulfillment(task)

    assert not validation.ok
    assert validation.first_gap == "expected_external_command_missing"


def test_desktop_observation_inferred_from_objective_is_unsatisfied_without_evidence() -> None:
    task = TaskRecord(objective="take a screenshot of my desktop", metadata={})

    validation = validate_fulfillment(task)

    assert not validation.ok
    assert validation.first_gap == "expected_desktop_observation_missing"


def test_requested_screenshot_requires_the_image_itself_to_be_delivered() -> None:
    task = TaskRecord(
        objective="Open the page and send me a screenshot",
        metadata={
            "browser_url": "https://example.test",
            "screenshot_path": "/tmp/page.png",
            "artifact_delivery": {
                "delivered": True,
                "operation": "send_latest",
                "path": "/tmp/latest-output.txt",
                "delivery_method": "telegram.sendDocument",
            },
        },
    )

    validation = validate_fulfillment(task)

    assert PostconditionType.SCREENSHOT_DELIVERED in {item.type for item in validation.expected}
    assert PostconditionType.SCREENSHOT_DELIVERED in validation.missing


def test_send_screenshot_satisfies_requested_screenshot_delivery() -> None:
    task = TaskRecord(
        objective="Open the page and send me a screenshot",
        metadata={
            "browser_url": "https://example.test",
            "screenshot_path": "/tmp/page.png",
            "artifact_delivery": {
                "delivered": True,
                "operation": "send_screenshot",
                "path": "/tmp/page.png",
                "delivery_method": "telegram.sendPhoto",
            },
        },
    )

    assert validate_fulfillment(task).ok


def test_run_goal_that_stopped_early_does_not_satisfy_desktop_observation() -> None:
    """A run_goal is a multi-step action loop with a real objective; a
    screenshot existing is not evidence the goal was reached. Only an explicit
    completed=True counts - max_steps exhaustion reports False."""
    task = TaskRecord(
        objective="Use computer to open a folder",
        metadata={
            "last_tool_result": {
                "output": {
                    "operation": "run_goal",
                    "completed": False,
                    "screenshot_path": "C:/tmp/screen.png",
                    "final_summary": "Stopped after max steps.",
                }
            }
        },
    )

    assert not _postcondition_satisfied(task, PostconditionType.DESKTOP_OBSERVATION)


def test_run_goal_missing_completed_field_is_not_satisfied() -> None:
    task = TaskRecord(
        objective="Use computer to open a folder",
        metadata={
            "last_tool_result": {
                "output": {
                    "operation": "run_goal",
                    "screenshot_path": "C:/tmp/screen.png",
                }
            }
        },
    )

    assert not _postcondition_satisfied(task, PostconditionType.DESKTOP_OBSERVATION)


def test_completed_run_goal_satisfies_desktop_observation() -> None:
    task = TaskRecord(
        objective="Use computer to open a folder",
        metadata={
            "last_tool_result": {
                "output": {
                    "operation": "run_goal",
                    "completed": True,
                    "final_summary": "Folder opened.",
                }
            }
        },
    )

    assert _postcondition_satisfied(task, PostconditionType.DESKTOP_OBSERVATION)


def test_running_coding_agent_session_does_not_satisfy_postcondition() -> None:
    task = TaskRecord(
        objective="Use Codex to fix tests",
        metadata={
            "last_tool_result": {
                "output": {
                    "provider": "codex",
                    "status": "running",
                    "session_id": "codex_abc",
                    "returncode": None,
                }
            }
        },
    )

    assert not _postcondition_satisfied(task, PostconditionType.CODING_AGENT_STEP)


def test_completed_coding_agent_session_satisfies_postcondition() -> None:
    task = TaskRecord(
        objective="Use Codex to fix tests",
        metadata={
            "last_tool_result": {
                "output": {
                    "provider": "codex",
                    "status": "completed",
                    "session_id": "codex_abc",
                    "returncode": 0,
                }
            }
        },
    )

    assert _postcondition_satisfied(task, PostconditionType.CODING_AGENT_STEP)


def test_embedded_filesystem_path_does_not_fabricate_a_postcondition() -> None:
    """Regression guard (docs/HISTORY.md): objectives routinely embed a literal
    path whose last segment contains a trigger word. A folder named
    '..._search' must not infer a BROWSER_STATE expectation nothing can
    satisfy - that produced a gap the loop could never close."""
    task = TaskRecord(
        objective=r"look in the folder C:\Users\me\AppData\Local\Temp\operator_loop_filesystem_search and read the file",
        metadata={},
    )

    validation = validate_fulfillment(task)

    assert PostconditionType.BROWSER_STATE not in {item.type for item in validation.expected}


def test_embedded_posix_path_does_not_fabricate_a_postcondition() -> None:
    task = TaskRecord(
        objective=(
            "look in the folder "
            "/tmp/ybm_scenario_scratch/operator_loop_filesystem_search "
            "and read the file"
        ),
        metadata={},
    )

    validation = validate_fulfillment(task)

    assert PostconditionType.BROWSER_STATE not in {
        item.type for item in validation.expected
    }


def test_daily_change_in_adapter_request_does_not_fabricate_schedule_postcondition() -> None:
    task = TaskRecord(
        objective=(
            "Create an adapter for a stock API that fetches the latest price, "
            "daily change, and basic quote information."
        ),
        metadata={"adapter_dir": "/tmp/adapters/stock_quotes"},
    )

    validation = validate_fulfillment(task)

    assert PostconditionType.SCHEDULE_CREATED not in {item.type for item in validation.expected}
    assert validation.ok


def test_daily_schedule_still_requires_created_schedule() -> None:
    task = TaskRecord(objective="Create a daily schedule to check the stock price", metadata={})

    validation = validate_fulfillment(task)

    assert PostconditionType.SCHEDULE_CREATED in {item.type for item in validation.expected}
    assert validation.first_gap == "expected_schedule_created_missing"
