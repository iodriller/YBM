"""Scenario: a status request, through the real Operator loop.

Was "resolves with zero LLM calls" pre-P3: `default_plans.py`'s
`build_default_task_plan()` had a deterministic, LLM-free shortcut
specifically for status-shaped objectives, reached before the planner ever
ran. That whole mechanism was deleted with the plan-based path - the
Operator loop has no equivalent shortcut, so a status request now costs
exactly two `decide()` calls like any other task: `call_tool task.status`,
then `done` with the synthesized answer. This is a real, disclosed latency
regression versus the old deterministic path (worth a dedicated fast-path
optimization later, tracked as a gap, not silently accepted as free), but
still functionally correct and fully deterministic once recorded. Fixture
re-recorded 2026-07-28 (`ybm scenario record status_request`,
localdeploy_qwen3vl_8b).
"""

from __future__ import annotations

from agent_control.schemas import TaskStatus
import pytest

from .harness import build_scenario, isolated_settings, run_task_to_completion


@pytest.mark.asyncio
async def test_status_request_calls_task_status_tool_then_completes(tmp_path, monkeypatch) -> None:
    settings = isolated_settings(monkeypatch, tmp_path)
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="status_request")

    task = await run_task_to_completion(scenario, "give me the current status")

    assert task.status == TaskStatus.COMPLETED
    # decide() x2 (call_tool task.status, then done) - not zero; see module docstring.
    assert len(scenario.provider.calls) == 2
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "task.status" for call in tool_calls)


@pytest.mark.asyncio
async def test_status_request_reports_recent_tasks(tmp_path, monkeypatch) -> None:
    settings = isolated_settings(monkeypatch, tmp_path)
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="status_request")
    scenario.repositories.tasks.create(objective="an earlier task")

    # Not "current status" (bare, 2 words): reproduced live, twice, the
    # local 8B model reading that as ambiguous between a status *query* and
    # a status *update* request ("What is the current status you would
    # like to check or update?" - an `ask_user`, ending the task at
    # CLARIFYING rather than COMPLETED). "task status" removes the
    # check-or-update reading while staying distinct from the sibling
    # test's "give me the current status" above, so both phrasings stay
    # covered.
    task = await run_task_to_completion(scenario, "what's the current task status?")

    assert task.status == TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "task.status" for call in tool_calls)
