"""Scenario: a status request resolves entirely through the deterministic
default-plan path (backend/src/agent_control/orchestration/default_plans.py),
with zero LLM calls. Proves the full worker/registry/policy/executor stack
wires together correctly with no fixture required - the simplest possible
scenario test, and a smoke check on the harness itself.
"""

from __future__ import annotations

import pytest

from agent_control.schemas import TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion


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
