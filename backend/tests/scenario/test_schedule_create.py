"""Scenario: "create a daily schedule that checks X" through the real LLM planner ->
schedule.manage create. schedule.manage is not a content tool (no synthesis
step), so this locks in the deterministic-completion path - the counterpart
to test_status_request.py's zero-LLM path and the content-tool round-trips in
the other scenario tests. Ports e2e/all_cases.json's `scheduled_jobs` case
down to the deterministic tier (docs/ROADMAP.md P2).
"""

from __future__ import annotations

import pytest

from agent_control.config import CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion

pytestmark = pytest.mark.skip(
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/AnswerValidator prompts); the Operator loop (docs/ROADMAP.md P3 "
    "\u00a72.2) is now the sole execution path and needs its own fixture, recorded fresh "
    "against a live LLM - see orchestration/operator.py and test_operator_loop.py for the "
    "pattern. Left in place (not deleted) so the scenario this file documents survives as "
    "a checklist for that re-recording pass."
)


@pytest.mark.asyncio
async def test_schedule_create_persists_a_schedule(tmp_path, monkeypatch) -> None:
    caps = default_capability_policies()
    caps[Capability.SCHEDULE_MANAGE] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.MEDIUM)
    settings = isolated_settings(monkeypatch, tmp_path, capabilities=caps)
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="schedule_create")

    task = await run_task_to_completion(
        scenario, "create a daily schedule that checks https://example.com/status for updates"
    )

    assert task.status == TaskStatus.COMPLETED
    schedules = scenario.repositories.schedules.list_recent(10)
    assert len(schedules) == 1
    assert "example.com/status" in schedules[0].objective
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "schedule.manage" for call in tool_calls)


@pytest.mark.asyncio
async def test_schedule_create_denied_without_capability(tmp_path, monkeypatch) -> None:
    # SCHEDULE_MANAGE left at its secure-by-default disabled state.
    settings = isolated_settings(monkeypatch, tmp_path)
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="schedule_create")

    task = await run_task_to_completion(
        scenario, "create a daily schedule that checks https://example.com/status for updates"
    )

    assert task.status != TaskStatus.COMPLETED
    assert scenario.repositories.schedules.list_recent(10) == []
