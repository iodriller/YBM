from __future__ import annotations

from datetime import timedelta

from agent_control.config import LimitsConfig
from agent_control.schemas import ErrorClass, StrictBaseModel, ToolCallResult, ToolResultStatus, utc_now


class RetryDecision(StrictBaseModel):
    retry: bool
    reason: str
    retry_count: int
    next_retry_at: str | None = None


class RetryPolicy:
    RETRYABLE_ERRORS = {
        ErrorClass.TRANSIENT,
        ErrorClass.RATE_LIMITED,
        ErrorClass.USAGE_LIMITED,
        # ADAPTER_FAILED and VALIDATION_FAILED are intentionally not retried:
        # the worker already tries _attach_recovery_plan and then _replan_with_error
        # for those error classes. Retrying the same failing step wastes cycles
        # and delays reaching the replan path (e.g. browser not available → fallback).
    }
    RETRYABLE_STATUSES = {ToolResultStatus.RATE_LIMITED, ToolResultStatus.TIMEOUT}

    def __init__(self, limits: LimitsConfig) -> None:
        self.limits = limits

    def evaluate(self, result: ToolCallResult, current_retry_count: int) -> RetryDecision:
        retryable = result.status in self.RETRYABLE_STATUSES or result.error_class in self.RETRYABLE_ERRORS
        if not retryable:
            return RetryDecision(retry=False, reason="not_retryable", retry_count=current_retry_count)
        if current_retry_count >= self.limits.max_retries:
            return RetryDecision(retry=False, reason="retry_limit_reached", retry_count=current_retry_count)

        retry_count = current_retry_count + 1
        backoff = self.limits.retry_backoff_seconds * retry_count
        next_retry_at = utc_now() + timedelta(seconds=backoff)
        return RetryDecision(
            retry=True,
            reason=result.error_class.value if result.error_class else result.status.value,
            retry_count=retry_count,
            next_retry_at=next_retry_at.isoformat(),
        )

    @staticmethod
    def intervention_summary(result: ToolCallResult) -> str:
        if result.error_class in {ErrorClass.RATE_LIMITED, ErrorClass.USAGE_LIMITED}:
            return "The configured tool hit a limit and retries are exhausted."
        return result.error_message or "The task failed and retries are exhausted."
