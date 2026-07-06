from __future__ import annotations

from agent_control.orchestration.attempt_history import count_same_tool_attempts, count_strategy_attempts
from agent_control.orchestration.failure_diagnosis import FailureDiagnosis, FailureType
from agent_control.schemas import StrictBaseModel, TaskRecord, ToolCallResult


class RecoveryAction(str):
    RETRY = "retry"
    SWITCH_TOOL = "switch_tool"
    USE_MCP = "use_mcp"
    USE_CODE_INTERPRETER = "use_code_interpreter"
    REPLAN = "replan"
    ASK_USER = "ask_user"
    BLOCK = "block"


class RecoveryDecision(StrictBaseModel):
    action: str
    reason: str


MAX_SAME_TOOL_ATTEMPTS = 2
MAX_TOTAL_STRATEGY_ATTEMPTS = 3


def choose_recovery(
    task: TaskRecord,
    result: ToolCallResult,
    diagnosis: FailureDiagnosis,
    *,
    tool_name: str,
    operation: str = "",
) -> RecoveryDecision:
    metadata = task.metadata
    same_tool = count_same_tool_attempts(metadata, tool_name, operation)
    total = count_strategy_attempts(metadata)
    if same_tool >= MAX_SAME_TOOL_ATTEMPTS:
        return RecoveryDecision(action=RecoveryAction.ASK_USER, reason="same_tool_attempt_limit_reached")
    if total >= MAX_TOTAL_STRATEGY_ATTEMPTS:
        return RecoveryDecision(action=RecoveryAction.ASK_USER, reason="strategy_attempt_limit_reached")

    failure_type = diagnosis.failure_type
    if failure_type in {FailureType.TIMEOUT, FailureType.BROWSER_UNREACHABLE, FailureType.LOCAL_MODEL_UNAVAILABLE}:
        return RecoveryDecision(action=RecoveryAction.RETRY, reason=failure_type.value)
    if failure_type == FailureType.RATE_LIMITED:
        return RecoveryDecision(action=RecoveryAction.ASK_USER, reason="usage_or_rate_limit")
    if failure_type == FailureType.CONNECTOR_MISSING:
        return RecoveryDecision(action=RecoveryAction.USE_MCP, reason="missing_connector_try_mcp")
    if failure_type in {FailureType.BAD_TOOL_ARGS, FailureType.BAD_PLAN, FailureType.CODING_AGENT_FAILED}:
        return RecoveryDecision(action=RecoveryAction.REPLAN, reason=failure_type.value)
    if failure_type in {FailureType.FILE_NOT_FOUND, FailureType.AUTH_REQUIRED, FailureType.MISSING_INPUT, FailureType.UNSAFE_ACTION}:
        return RecoveryDecision(action=RecoveryAction.ASK_USER, reason=failure_type.value)
    return RecoveryDecision(action=RecoveryAction.REPLAN, reason="unknown_failure")
