"""Scenario: "find a .txt file and read it to me" through the real LLM
planner -> a 2-step filesystem.manage plan (search, then read_file) ->
validator -> synthesizer. Ports e2e/all_cases.json's `file_find_and_read`
case down to the deterministic tier (docs/ROADMAP.md P2) - locks in
multi-step planning within a single tool, not just multi-tool plans.
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
async def test_file_find_and_read_returns_file_contents(tmp_path, monkeypatch) -> None:
    desktop_dir = scenario_scratch_dir("file_find_and_read")
    (desktop_dir / "resume-notes.txt").write_text(
        "Oney resume notes: Python automation, local LLM orchestration, desktop control.",
        encoding="utf-8",
    )
    (desktop_dir / "receipt.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    settings = _settings(monkeypatch, tmp_path, str(desktop_dir))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="file_find_and_read")

    task = await run_task_to_completion(
        scenario, f"Find a .txt file in {desktop_dir} and read me its contents"
    )

    assert task.status == TaskStatus.COMPLETED
    answer = task.metadata.get("synthesized_answer", "")
    assert "resume-notes.txt" in answer
    assert "Python automation" in answer
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    operations = [
        call["request"].get("input", {}).get("operation")
        for call in tool_calls
        if call["tool_name"] == "filesystem.manage"
    ]
    assert "search" in operations
    assert "read_file" in operations


@pytest.mark.asyncio
async def test_file_find_and_read_rejects_path_outside_allowed_roots(tmp_path, monkeypatch) -> None:
    desktop_dir = scenario_scratch_dir("file_find_and_read")
    (desktop_dir / "resume-notes.txt").write_text(
        "Oney resume notes: Python automation, local LLM orchestration, desktop control.",
        encoding="utf-8",
    )
    (desktop_dir / "receipt.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    settings = _settings(monkeypatch, tmp_path, str(tmp_path / "somewhere_else"))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="file_find_and_read")

    task = await run_task_to_completion(
        scenario, f"Find a .txt file in {desktop_dir} and read me its contents"
    )

    assert task.status != TaskStatus.COMPLETED
