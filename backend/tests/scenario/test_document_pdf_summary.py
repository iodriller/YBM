"""Scenario: "summarize the PDF at X" through the real LLM planner ->
document.manage summarize_pdf -> validator -> synthesizer. Replayed from a
fixture recorded against a live LLM. Ports e2e/all_cases.json's
`pdf_open_summary` case down to the deterministic tier (docs/ROADMAP.md P2).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_control.config import CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "e2e"))
from fixtures import _write_minimal_pdf  # noqa: E402

pytestmark = pytest.mark.skip(
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/AnswerValidator prompts); the Operator loop (docs/ROADMAP.md P3 "
    "\u00a72.2) is now the sole execution path and needs its own fixture, recorded fresh "
    "against a live LLM - see orchestration/operator.py and test_operator_loop.py for the "
    "pattern. Left in place (not deleted) so the scenario this file documents survives as "
    "a checklist for that re-recording pass."
)


@pytest.mark.asyncio
async def test_document_pdf_summary(tmp_path, monkeypatch) -> None:
    docs_dir = scenario_scratch_dir("document_pdf_summary")
    pdf_path = docs_dir / "quarterly_report.pdf"
    _write_minimal_pdf(
        pdf_path,
        "Quarterly Report Summary. Revenue: 4.2 million dollars, up 12 percent quarter over "
        "quarter. Expenses: 2.8 million dollars. Net profit: 1.4 million dollars. Headcount "
        "grew from 40 to 48 employees. Outlook: positive, driven by new enterprise contracts.",
    )

    caps = default_capability_policies()
    caps[Capability.FILESYSTEM_WRITE] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    settings = isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        adapters={"computer_use": {"enabled": True, "allowed_roots": [str(docs_dir)]}},
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="document_pdf_summary")

    task = await run_task_to_completion(scenario, f"summarize the PDF at {pdf_path}")

    assert task.status == TaskStatus.COMPLETED
    answer = task.metadata["synthesized_answer"]
    assert "4.2 million" in answer or "4,200,000" in answer
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "document.manage" for call in tool_calls)


@pytest.mark.asyncio
async def test_document_pdf_summary_denied_without_filesystem_write(tmp_path, monkeypatch) -> None:
    docs_dir = scenario_scratch_dir("document_pdf_summary")
    pdf_path = docs_dir / "quarterly_report.pdf"
    _write_minimal_pdf(pdf_path, "placeholder content, not used - capability is off")

    # No capability override: FILESYSTEM_WRITE (and therefore document.manage)
    # stays at its secure-by-default disabled state.
    settings = isolated_settings(
        monkeypatch, tmp_path,
        adapters={"computer_use": {"enabled": True, "allowed_roots": [str(docs_dir)]}},
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="document_pdf_summary")

    task = await run_task_to_completion(scenario, f"summarize the PDF at {pdf_path}")

    assert task.status != TaskStatus.COMPLETED
