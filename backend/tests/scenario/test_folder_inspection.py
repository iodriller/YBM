"""Scenario: "inspect this folder and tell me what's inside" through the real
LLM planner -> filesystem.manage inspect_folder -> validator -> synthesizer.
Ports e2e/all_cases.json's `folder_open_inspection` case down to the
deterministic tier (docs/ROADMAP.md P2) - the inspect_folder operation
counterpart to test_filesystem_search.py's search operation.
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
async def test_folder_inspection_reports_files_and_subfolders(tmp_path, monkeypatch) -> None:
    docs_dir = scenario_scratch_dir("folder_open_inspection")
    (docs_dir / "notes.txt").write_text("notes for e2e organization", encoding="utf-8")
    (docs_dir / "budget.csv").write_text("name,amount\nsample,10\n", encoding="utf-8")
    (docs_dir / "archive").mkdir()

    settings = _settings(monkeypatch, tmp_path, str(docs_dir))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="folder_open_inspection")

    task = await run_task_to_completion(
        scenario, f"Inspect the folder {docs_dir} and tell me what files and subfolders are inside."
    )

    assert task.status == TaskStatus.COMPLETED
    answer = task.metadata.get("synthesized_answer", "")
    assert "notes.txt" in answer
    assert "budget.csv" in answer
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "filesystem.manage" for call in tool_calls)


@pytest.mark.asyncio
async def test_folder_inspection_rejects_path_outside_allowed_roots(tmp_path, monkeypatch) -> None:
    docs_dir = scenario_scratch_dir("folder_open_inspection")
    (docs_dir / "notes.txt").write_text("notes for e2e organization", encoding="utf-8")
    (docs_dir / "budget.csv").write_text("name,amount\nsample,10\n", encoding="utf-8")
    (docs_dir / "archive").mkdir()

    # allowed_roots does NOT include docs_dir - the same recorded plan should
    # still be produced (same prompt), but policy/execution must refuse it.
    settings = _settings(monkeypatch, tmp_path, str(tmp_path / "somewhere_else"))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="folder_open_inspection")

    task = await run_task_to_completion(
        scenario, f"Inspect the folder {docs_dir} and tell me what files and subfolders are inside."
    )

    assert task.status != TaskStatus.COMPLETED
