"""A usage limit should be waited out, not escalated to a human.

Coding assistants fail long builds with "quota exceeded, resets in N hours".
The worker used to answer that with ask_user: the task stopped until someone
replied. For a limit that resolves on a timer, a human adds only the delay
until they next look at their phone - so the task now parks itself and
resumes on its own.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent_control.orchestration import TaskWorker, ToolExecutor
from agent_control.policy import PolicyEngine
from agent_control.recovery import RetryPolicy
from agent_control.recovery.usage_limits import (
    DEFAULT_RETRY_AFTER_SECONDS,
    MAX_RETRY_AFTER_SECONDS,
    next_attempt_at,
    parse_retry_after_seconds,
)
from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.schemas import (
    Capability,
    ErrorClass,
    OperatorAction,
    OperatorDecision,
    RiskLevel,
    TaskStatus,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
    utc_now,
)
from agent_control.tools.registry import ToolDefinition
from helpers import make_repos

from test_worker_operator_loop import QueueOperator


class UsageLimitedAdapter:
    def __init__(self, message: str) -> None:
        self.message = message

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.RATE_LIMITED,
            error_class=ErrorClass.USAGE_LIMITED,
            error_message=self.message,
        )


def _worker(tmp_path, message: str):
    repos, audit = make_repos(tmp_path)
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.LLM_GENERATE: CapabilityPolicy(
                enabled=True, requires_approval=False, max_risk_level=RiskLevel.LOW
            )
        },
    )
    executor = ToolExecutor(
        PolicyEngine(settings, audit), repos, audit,
        adapters={"llm": UsageLimitedAdapter(message)},
        tool_definitions={
            "llm": ToolDefinition(
                name="llm", capability=Capability.LLM_GENERATE, enabled=True, description="t"
            )
        },
    )
    operator = QueueOperator([
        OperatorDecision(
            action=OperatorAction.CALL_TOOL, tool_name="llm", tool_input={}, risk_level=RiskLevel.LOW
        ),
    ])
    return repos, TaskWorker(
        repos, audit, executor=executor, operator=operator, retry_policy=RetryPolicy(settings.limits),
    )


@pytest.mark.asyncio
async def test_usage_limit_parks_the_task_instead_of_asking_the_user(tmp_path) -> None:
    repos, worker = _worker(tmp_path, "Usage limit reached. Try again in 45 minutes.")
    task = repos.tasks.create("build the thing with copilot")

    parked = await worker.process_task(task.id)

    assert parked.status == TaskStatus.RETRYING, "must not stop and wait for a human"
    wait = parked.metadata["usage_limit_wait"]
    assert wait["reset_time_from_provider"] is True
    assert wait["wait_seconds"] == 45 * 60
    assert parked.metadata["next_retry_at"] > utc_now().isoformat()


@pytest.mark.asyncio
async def test_a_parked_task_does_not_starve_the_queue(tmp_path) -> None:
    """max_parallel_tasks is 1, so a task waiting hours must not be re-claimed
    on every poll ahead of newer work that could run right now."""
    repos, worker = _worker(tmp_path, "Quota exceeded. Try again in 3 hours.")
    parked_task = repos.tasks.create("the long copilot build")
    await worker.process_task(parked_task.id)
    newer = repos.tasks.create("something quick")

    claimed = repos.tasks.claim_next(
        [TaskStatus.RECEIVED, TaskStatus.RUNNING, TaskStatus.RETRYING], "worker-1"
    )

    assert claimed is not None
    assert claimed.id == newer.id, "the parked task was claimed ahead of runnable work"


@pytest.mark.asyncio
async def test_resuming_clears_the_wait_so_it_can_be_claimed_again(tmp_path) -> None:
    """next_retry_at only moves forward, so leaving it set after a resume
    would make claim_next's filter exclude the task permanently."""
    repos, worker = _worker(tmp_path, "Quota exceeded. Try again in 1 minute.")
    task = repos.tasks.create("build it")
    parked = await worker.process_task(task.id)
    repos.tasks.update_metadata(
        parked.id,
        {**parked.metadata, "next_retry_at": (utc_now() - timedelta(seconds=5)).isoformat()},
        TaskStatus.RETRYING,
    )

    resumed = await worker.process_task(task.id)

    assert resumed.status == TaskStatus.RUNNING
    assert "next_retry_at" not in resumed.metadata
    assert "usage_limit_wait" not in resumed.metadata


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("You have exceeded your quota. Try again in 45 minutes.", 45 * 60),
        ("rate limit; retry after 90 seconds", 90),
        ("Usage limit reached, resets in 2 hours", 2 * 3600),
        ("quota exceeded", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_retry_after_seconds(message, expected) -> None:
    assert parse_retry_after_seconds(message) == expected


def test_unparseable_message_falls_back_without_claiming_provider_knowledge(tmp_path) -> None:
    """The caller reports the wait differently depending on where it came
    from, so an invented number must never be labelled as the provider's."""
    _resume_at, seconds, from_provider = next_attempt_at("quota exceeded")

    assert seconds == DEFAULT_RETRY_AFTER_SECONDS
    assert from_provider is False


def test_absurd_reset_times_are_capped(tmp_path) -> None:
    """A task must not be retired into a state nobody revisits."""
    assert parse_retry_after_seconds("try again in 30 days") == MAX_RETRY_AFTER_SECONDS
