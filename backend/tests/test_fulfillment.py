from __future__ import annotations

from agent_control.orchestration.fulfillment import validate_fulfillment
from agent_control.schemas import PlanModel, PlanPostcondition, PlanStep, PostconditionType, TaskRecord


def test_fulfillment_supports_future_postcondition_types() -> None:
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
    plan = PlanModel(
        objective=task.objective,
        steps=[PlanStep(title="Open browser", description="Open page.", tool_name="browser.open")],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.BROWSER_STATE,
                description="Browser state is reported.",
            )
        ],
    )

    validation = validate_fulfillment(task, plan)

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
    plan = PlanModel(
        objective=task.objective,
        steps=[
            PlanStep(
                title="Run",
                description="Run command.",
                tool_name="vscode.terminal_command",
            )
        ],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.EXTERNAL_COMMAND,
                description="Command succeeded.",
            )
        ],
    )

    validation = validate_fulfillment(task, plan)

    assert not validation.ok
    assert validation.first_gap == "expected_external_command_missing"


def test_desktop_run_goal_postcondition_requires_completed_output() -> None:
    task = TaskRecord(
        objective="Use computer to open a folder",
        metadata={
            "last_tool_result": {
                "output": {
                    "operation": "run_goal",
                    "observation": {"active_window": {"title": "Desktop"}},
                    "screenshot_path": "C:/tmp/screen.png",
                    "completed": False,
                    "final_summary": "Stopped after max steps.",
                }
            }
        },
    )
    plan = PlanModel(
        objective=task.objective,
        steps=[PlanStep(title="Use computer", description="Run computer-use.", tool_name="computer.use")],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.DESKTOP_OBSERVATION,
                description="Desktop request is completed.",
            )
        ],
    )

    validation = validate_fulfillment(task, plan)

    assert not validation.ok
    assert validation.first_gap == "expected_desktop_observation_missing"
