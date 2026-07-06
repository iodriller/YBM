from __future__ import annotations

from agent_control.orchestration.attempt_history import append_attempt_history
from agent_control.orchestration.failure_diagnosis import FailureType, diagnose_failure
from agent_control.orchestration.recovery_policy import RecoveryAction, choose_recovery
from agent_control.schemas import ErrorClass, TaskRecord, ToolCallResult, ToolResultStatus


def _failed(message: str, *, error_class: ErrorClass = ErrorClass.ADAPTER_FAILED) -> ToolCallResult:
    return ToolCallResult(
        request_id="toolreq_1",
        status=ToolResultStatus.FAILED,
        error_class=error_class,
        error_message=message,
    )


def test_browser_unreachable_is_diagnosed_retryable() -> None:
    result = _failed("Chrome DevTools endpoint is unreachable")
    diagnosis = diagnose_failure(result, tool_name="browser.open", operation="open")
    task = TaskRecord(objective="check page")

    decision = choose_recovery(task, result, diagnosis, tool_name="browser.open", operation="open")

    assert diagnosis.failure_type == FailureType.BROWSER_UNREACHABLE
    assert decision.action == RecoveryAction.RETRY


def test_same_tool_failures_are_bounded_to_ask_user() -> None:
    result = _failed("Chrome DevTools endpoint is unreachable")
    diagnosis = diagnose_failure(result, tool_name="browser.open", operation="open")
    metadata = {}
    for _ in range(2):
        metadata = append_attempt_history(
            metadata,
            step_id="step_1",
            tool_name="browser.open",
            operation="open",
            result=result,
            diagnosis=diagnosis,
            next_action="retry",
        )
    task = TaskRecord(objective="check page", metadata=metadata)

    decision = choose_recovery(task, result, diagnosis, tool_name="browser.open", operation="open")

    assert decision.action == RecoveryAction.ASK_USER
    assert decision.reason == "same_tool_attempt_limit_reached"


def test_connector_missing_prefers_mcp_path() -> None:
    result = _failed("tool adapter not registered: github.issue")
    diagnosis = diagnose_failure(result, tool_name="github.issue", operation="create")
    task = TaskRecord(objective="create issue")

    decision = choose_recovery(task, result, diagnosis, tool_name="github.issue", operation="create")

    assert diagnosis.failure_type == FailureType.CONNECTOR_MISSING
    assert decision.action == RecoveryAction.USE_MCP
