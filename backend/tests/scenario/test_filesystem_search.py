"""Scenario: "look in this folder for a file about X" through the real LLM
planner -> filesystem.manage -> validator -> synthesizer pipeline, replayed
from a fixture recorded against a live LLM (see harness.build_scenario's
record_with= parameter to regenerate it).
"""

from __future__ import annotations

import os

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir

pytestmark = pytest.mark.skipif(
    not os.environ.get("YBM_SCENARIO_RECORD"),
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/AnswerValidator prompts); the Operator loop (docs/HISTORY.md P3 "
    "\u00a72.2) is now the sole execution path and needs its own fixture, recorded fresh "
    "against a live LLM - see orchestration/operator.py and test_operator_loop.py for the "
    "pattern. Left in place (not deleted) so the scenario this file documents survives as "
    "a checklist for that re-recording pass."
)


def _settings(monkeypatch, tmp_path, allowed_root: str) -> AppSettings:
    caps = default_capability_policies()
    caps[Capability.FILESYSTEM_READ] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.LOW)
    caps[Capability.FILESYSTEM_WRITE] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    return isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        adapters={"computer_use": {"enabled": True, "allowed_roots": [allowed_root]}},
    )


@pytest.mark.asyncio
async def test_filesystem_search_finds_resume_file(tmp_path, monkeypatch) -> None:
    docs_dir = scenario_scratch_dir("filesystem_search")
    (docs_dir / "resume.txt").write_text("Jane Doe - Software Engineer resume", encoding="utf-8")
    (docs_dir / "notes.txt").write_text("meeting notes from Tuesday", encoding="utf-8")
    (docs_dir / "invoice_2026.txt").write_text("Invoice #4471 - $250.00", encoding="utf-8")

    settings = _settings(monkeypatch, tmp_path, str(docs_dir))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="filesystem_search")

    task = await run_task_to_completion(
        scenario, f"look in the folder {docs_dir} and find which file mentions a resume"
    )

    assert task.status == TaskStatus.COMPLETED
    assert "resume.txt" in task.metadata.get("synthesized_answer", "")
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "filesystem.manage" for call in tool_calls)


@pytest.mark.asyncio
async def test_filesystem_search_rejects_path_outside_allowed_roots(tmp_path, monkeypatch) -> None:
    docs_dir = scenario_scratch_dir("filesystem_search")
    (docs_dir / "resume.txt").write_text("Jane Doe - Software Engineer resume", encoding="utf-8")
    (docs_dir / "notes.txt").write_text("meeting notes from Tuesday", encoding="utf-8")
    (docs_dir / "invoice_2026.txt").write_text("Invoice #4471 - $250.00", encoding="utf-8")

    # allowed_roots does NOT include docs_dir - the same recorded plan should
    # still be produced (same prompt), but policy/execution must refuse it.
    settings = _settings(monkeypatch, tmp_path, str(tmp_path / "somewhere_else"))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="filesystem_search")

    task = await run_task_to_completion(
        scenario, f"look in the folder {docs_dir} and find which file mentions a resume"
    )

    assert task.status != TaskStatus.COMPLETED
