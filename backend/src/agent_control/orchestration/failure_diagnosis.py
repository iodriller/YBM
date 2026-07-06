from __future__ import annotations

from enum import StrEnum

from agent_control.schemas import ErrorClass, StrictBaseModel, ToolCallResult, ToolResultStatus


class FailureType(StrEnum):
    MISSING_INPUT = "missing_input"
    AMBIGUOUS_REQUEST = "ambiguous_request"
    TOOL_DISABLED = "tool_disabled"
    AUTH_REQUIRED = "auth_required"
    LOCAL_MODEL_UNAVAILABLE = "local_model_unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    BROWSER_UNREACHABLE = "browser_unreachable"
    FILE_NOT_FOUND = "file_not_found"
    CONNECTOR_MISSING = "connector_missing"
    CODING_AGENT_FAILED = "coding_agent_failed"
    BAD_PLAN = "bad_plan"
    BAD_TOOL_ARGS = "bad_tool_args"
    UNSAFE_ACTION = "unsafe_action"
    UNKNOWN = "unknown"


class FailureDiagnosis(StrictBaseModel):
    failure_type: FailureType
    message: str
    retryable: bool = False


def diagnose_failure(result: ToolCallResult, *, tool_name: str = "", operation: str = "") -> FailureDiagnosis:
    message = " ".join(
        part
        for part in (
            tool_name,
            operation,
            result.status.value,
            result.error_class.value if result.error_class else "",
            result.error_message or "",
            str(result.output.get("summary") or "") if isinstance(result.output, dict) else "",
        )
        if part
    )
    haystack = message.lower()

    if result.status == ToolResultStatus.TIMEOUT:
        return FailureDiagnosis(failure_type=FailureType.TIMEOUT, message=message, retryable=True)
    if result.status == ToolResultStatus.RATE_LIMITED or result.error_class in {ErrorClass.RATE_LIMITED, ErrorClass.USAGE_LIMITED}:
        return FailureDiagnosis(failure_type=FailureType.RATE_LIMITED, message=message, retryable=False)
    if result.error_class == ErrorClass.POLICY_DENIED or any(marker in haystack for marker in ("unsafe", "denied", "policy")):
        return FailureDiagnosis(failure_type=FailureType.UNSAFE_ACTION, message=message, retryable=False)
    if any(marker in haystack for marker in ("login", "log in", "sign in", "credential", "unauthorized", "401", "403")):
        return FailureDiagnosis(failure_type=FailureType.AUTH_REQUIRED, message=message, retryable=False)
    if any(marker in haystack for marker in ("localdeploy", "connection refused", "connecterror", "http 5", "model unavailable")):
        return FailureDiagnosis(failure_type=FailureType.LOCAL_MODEL_UNAVAILABLE, message=message, retryable=True)
    if any(marker in haystack for marker in ("chrome", "devtools", "browser", "websocket")):
        return FailureDiagnosis(failure_type=FailureType.BROWSER_UNREACHABLE, message=message, retryable=True)
    if any(marker in haystack for marker in ("no such file", "file not found", "not found", "does not exist", "missing path")):
        return FailureDiagnosis(failure_type=FailureType.FILE_NOT_FOUND, message=message, retryable=False)
    if any(marker in haystack for marker in ("tool adapter not registered", "unregistered tool", "connector missing", "missing tool")):
        return FailureDiagnosis(failure_type=FailureType.CONNECTOR_MISSING, message=message, retryable=False)
    if result.error_class == ErrorClass.VALIDATION_FAILED or any(marker in haystack for marker in ("invalid input", "bad args", "schema")):
        return FailureDiagnosis(failure_type=FailureType.BAD_TOOL_ARGS, message=message, retryable=False)
    if any(marker in haystack for marker in ("no plan", "planning failed", "bad plan")):
        return FailureDiagnosis(failure_type=FailureType.BAD_PLAN, message=message, retryable=False)
    if tool_name == "coding.agent" or "coding agent" in haystack:
        return FailureDiagnosis(failure_type=FailureType.CODING_AGENT_FAILED, message=message, retryable=False)
    if any(marker in haystack for marker in ("missing input", "required", "which ", "what exact")):
        return FailureDiagnosis(failure_type=FailureType.MISSING_INPUT, message=message, retryable=False)
    return FailureDiagnosis(failure_type=FailureType.UNKNOWN, message=message, retryable=False)
