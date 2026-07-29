"""Scenario: the additive Operator loop (P3 §2.2) driving the SAME real
worker/registry/policy/executor stack as the planner-based scenarios, but via
observe/decide/act instead of a persisted PlanModel. Fixture recorded against
a live LLM - see harness.build_scenario's record_with= parameter.

Proves the loop end to end: two real filesystem.manage tool calls
(search, then read_file) chosen one at a time from tool-result history, then
a grounded `done` decision - not a single-shot happy path.
"""

from __future__ import annotations

import pytest

from agent_control.config import CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir


@pytest.mark.asyncio
async def test_operator_loop_finds_and_reads_resume_file(tmp_path, monkeypatch) -> None:
    docs_dir = scenario_scratch_dir("operator_loop_filesystem_search")
    (docs_dir / "resume.txt").write_text("Jane Doe - Software Engineer resume", encoding="utf-8")
    (docs_dir / "notes.txt").write_text("meeting notes from Tuesday", encoding="utf-8")
    (docs_dir / "invoice_2026.txt").write_text("Invoice #4471 - $250.00", encoding="utf-8")

    caps = default_capability_policies()
    caps[Capability.FILESYSTEM_READ] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.LOW)
    caps[Capability.FILESYSTEM_WRITE] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    settings = isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        adapters={"computer_use": {"enabled": True, "allowed_roots": [str(docs_dir)]}},
        operator={"max_steps": 6},
    )
    scenario = build_scenario(
        settings, tmp_path=tmp_path, fixture_name="operator_loop_filesystem_search",
    )

    task = await run_task_to_completion(
        scenario, f"look in the folder {docs_dir} and find which file mentions a resume", max_ticks=6,
    )

    audit_events = scenario.repositories.audit.list_for_task(task.id)
    failure_details = [
        event.payload for event in audit_events if event.type.value == "error"
    ]
    assert task.status == TaskStatus.COMPLETED, failure_details
    assert "resume.txt" in task.metadata["synthesized_answer"]
    history = task.metadata["operator_history"]
    assert [entry["tool_name"] for entry in history] == ["filesystem.manage", "filesystem.manage"]
    assert history[0]["input"]["operation"] == "search"
    assert history[1]["input"]["operation"] == "read_file"
    assert all(entry["status"] == "succeeded" for entry in history)


@pytest.mark.asyncio
async def test_operator_loop_marker_set_on_first_tick(tmp_path, monkeypatch) -> None:
    docs_dir = scenario_scratch_dir("operator_loop_filesystem_search")
    (docs_dir / "resume.txt").write_text("Jane Doe - Software Engineer resume", encoding="utf-8")
    (docs_dir / "notes.txt").write_text("meeting notes from Tuesday", encoding="utf-8")
    (docs_dir / "invoice_2026.txt").write_text("Invoice #4471 - $250.00", encoding="utf-8")

    caps = default_capability_policies()
    caps[Capability.FILESYSTEM_READ] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.LOW)
    caps[Capability.FILESYSTEM_WRITE] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    settings = isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        adapters={"computer_use": {"enabled": True, "allowed_roots": [str(docs_dir)]}},
        operator={"max_steps": 6},
    )
    scenario = build_scenario(
        settings, tmp_path=tmp_path, fixture_name="operator_loop_filesystem_search",
    )

    task = scenario.repositories.tasks.create(
        objective=f"look in the folder {docs_dir} and find which file mentions a resume"
    )
    first_tick = await scenario.worker.process_task(task.id)

    assert first_tick.status == TaskStatus.RUNNING
    assert first_tick.metadata["operator_loop"] is True
    assert len(first_tick.metadata["operator_history"]) == 1
