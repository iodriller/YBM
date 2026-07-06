from __future__ import annotations

from typing import Any

from agent_control.orchestration.failure_diagnosis import FailureDiagnosis, FailureType
from agent_control.schemas import ToolCallResult, utc_now


def append_attempt_history(
    metadata: dict[str, Any],
    *,
    step_id: str | None,
    tool_name: str,
    operation: str,
    result: ToolCallResult,
    diagnosis: FailureDiagnosis | None,
    next_action: str,
) -> dict[str, Any]:
    history = list(metadata.get("attempt_history") or [])
    failure_type = diagnosis.failure_type.value if diagnosis else None
    history.append(
        {
            "attempt": len(history) + 1,
            "step_id": step_id,
            "tool": tool_name,
            "operation": operation,
            "status": result.status.value,
            "failure_type": failure_type,
            "next_action": next_action,
            "message": _message(result, diagnosis),
            "created_at": utc_now().isoformat(),
        }
    )
    updated = {**metadata, "attempt_history": history[-50:]}
    if diagnosis is not None:
        updated["last_failure_type"] = failure_type
    updated["last_recovery_action"] = next_action
    return updated


def count_same_tool_attempts(metadata: dict[str, Any], tool_name: str, operation: str = "") -> int:
    return sum(
        1
        for item in metadata.get("attempt_history", [])
        if isinstance(item, dict)
        and item.get("tool") == tool_name
        and (not operation or item.get("operation") == operation)
        and item.get("status") != "succeeded"
    )


def count_strategy_attempts(metadata: dict[str, Any]) -> int:
    return sum(
        1
        for item in metadata.get("attempt_history", [])
        if isinstance(item, dict) and item.get("status") != "succeeded"
    )


def _message(result: ToolCallResult, diagnosis: FailureDiagnosis | None) -> str:
    if result.error_message:
        return result.error_message[:800]
    if diagnosis and diagnosis.failure_type != FailureType.UNKNOWN:
        return diagnosis.message[:800]
    if isinstance(result.output, dict) and result.output.get("summary"):
        return str(result.output["summary"])[:800]
    return result.status.value
