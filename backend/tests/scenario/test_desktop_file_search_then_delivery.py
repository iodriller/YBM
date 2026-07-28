"""Scenario: "find this file on my desktop and send it to me" through the
real LLM planner -> filesystem.manage search -> artifact.deliver -> the fake
Telegram client. Ports e2e/all_cases.json's `desktop_file_search_then_delivery`
case down to the deterministic tier (docs/ROADMAP.md P2) - combines
test_file_find_and_read.py's search step with test_send_found_pdf.py's
delivery step, this time chained from a name only (no literal path given).
"""

from __future__ import annotations

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir

pytestmark = pytest.mark.skip(
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/AnswerValidator prompts); the Operator loop (docs/ROADMAP.md P3 "
    "\u00a72.2) is now the sole execution path and needs its own fixture, recorded fresh "
    "against a live LLM - see orchestration/operator.py and test_operator_loop.py for the "
    "pattern. Left in place (not deleted) so the scenario this file documents survives as "
    "a checklist for that re-recording pass."
)


def _settings(monkeypatch, tmp_path, allowed_root: str) -> AppSettings:
    caps = default_capability_policies()
    caps[Capability.FILESYSTEM_WRITE] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    return isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        adapters={"computer_use": {"enabled": True, "allowed_roots": [allowed_root]}},
    )


@pytest.mark.asyncio
async def test_desktop_file_search_then_delivery_finds_and_sends(tmp_path, monkeypatch) -> None:
    desktop_dir = scenario_scratch_dir("desktop_file_search_then_delivery")
    (desktop_dir / "agent-control-sample.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (desktop_dir / "unrelated.txt").write_text("not the file we want", encoding="utf-8")

    settings = _settings(monkeypatch, tmp_path, str(desktop_dir))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="desktop_file_search_then_delivery")

    task = await run_task_to_completion(
        scenario, f"Find me the file named agent-control-sample from {desktop_dir} and send it to me."
    )

    assert task.status == TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "filesystem.manage" for call in tool_calls)
    assert any(call["tool_name"] == "artifact.deliver" for call in tool_calls)
    assert scenario.telegram.documents
    assert any("agent-control-sample" in path for _chat_id, path, _caption in scenario.telegram.documents)


@pytest.mark.asyncio
async def test_desktop_file_search_then_delivery_rejects_path_outside_allowed_roots(tmp_path, monkeypatch) -> None:
    desktop_dir = scenario_scratch_dir("desktop_file_search_then_delivery")
    (desktop_dir / "agent-control-sample.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (desktop_dir / "unrelated.txt").write_text("not the file we want", encoding="utf-8")

    settings = _settings(monkeypatch, tmp_path, str(tmp_path / "somewhere_else"))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="desktop_file_search_then_delivery")

    task = await run_task_to_completion(
        scenario, f"Find me the file named agent-control-sample from {desktop_dir} and send it to me."
    )

    assert task.status != TaskStatus.COMPLETED
    assert not scenario.telegram.documents
