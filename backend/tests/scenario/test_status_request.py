"""Scenario: a status request resolves entirely through the deterministic
default-plan path (backend/src/agent_control/orchestration/default_plans.py),
with zero LLM calls. Proves the full worker/registry/policy/executor stack
wires together correctly with no fixture required - the simplest possible
scenario test, and a smoke check on the harness itself.
"""

from __future__ import annotations

import os

import pytest

from agent_control.schemas import TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion

pytestmark = pytest.mark.skipif(
    not os.environ.get("YBM_SCENARIO_RECORD"),
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/AnswerValidator prompts); the Operator loop (docs/HISTORY.md P3 "
    "\u00a72.2) is now the sole execution path and needs its own fixture, recorded fresh "
    "against a live LLM - see orchestration/operator.py and test_operator_loop.py for the "
    "pattern. Left in place (not deleted) so the scenario this file documents survives as "
    "a checklist for that re-recording pass."
)


@pytest.mark.asyncio
async def test_status_request_completes_without_any_llm_call(tmp_path, monkeypatch) -> None:
    settings = isolated_settings(monkeypatch, tmp_path)
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="status_request")

    task = await run_task_to_completion(scenario, "give me the current status")

    assert task.status == TaskStatus.COMPLETED
    assert scenario.provider.calls == []


@pytest.mark.asyncio
async def test_status_request_reports_recent_tasks(tmp_path, monkeypatch) -> None:
    settings = isolated_settings(monkeypatch, tmp_path)
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="status_request")
    scenario.repositories.tasks.create(objective="an earlier task")

    task = await run_task_to_completion(scenario, "current status")

    assert task.status == TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "task.status" for call in tool_calls)
