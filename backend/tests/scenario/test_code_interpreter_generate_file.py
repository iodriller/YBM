"""Scenario: "use the code interpreter to create a report file" through the
real LLM planner -> a single code.interpreter generate_and_run call -> real
local Python execution -> validator -> synthesizer. Ports
e2e/all_cases.json's `code_interpreter_generate_file` case down to the
deterministic tier (docs/ROADMAP.md P2) - the single-step, no-delivery
counterpart to test_code_interpreter_csv_summary.py's two-step,
delivery-ending case.
"""

from __future__ import annotations

import pytest

from agent_control.config import CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir

pytestmark = pytest.mark.skip(
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/AnswerValidator prompts); the Operator loop (docs/ROADMAP.md P3 "
    "\u00a72.2) is now the sole execution path and needs its own fixture, recorded fresh "
    "against a live LLM - see orchestration/operator.py and test_operator_loop.py for the "
    "pattern. Left in place (not deleted) so the scenario this file documents survives as "
    "a checklist for that re-recording pass."
)


@pytest.mark.asyncio
async def test_code_interpreter_generate_file_writes_report(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_generate_file")

    caps = default_capability_policies()
    caps[Capability.TERMINAL_RUN] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    settings = isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        # require_approval_for_untrusted_run_python: False - this test is
        # about execution correctness, not the approval gate; see
        # test_code_interpreter.py's
        # test_code_interpreter_generated_run_needs_approval_on_silent_docker_fallback
        # for that gate's own coverage.
        adapters={
            "code_interpreter": {
                "enabled": True,
                "workspace_root": str(workspace),
                "require_approval_for_untrusted_run_python": False,
            }
        },
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="code_interpreter_generate_file")

    task = await run_task_to_completion(
        scenario,
        "Use the local code interpreter to create a file named interpreter-report.txt with a "
        "two-line summary of this test, run the script, and tell me where the file is.",
    )

    assert task.status == TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    interpreter_calls = [call for call in tool_calls if call["tool_name"] == "code.interpreter"]
    assert interpreter_calls
    created_files = [
        name
        for call in interpreter_calls
        for name in (call["result"] or {}).get("output", {}).get("files_created", [])
    ]
    assert any("interpreter-report.txt" in name for name in created_files)
    # Ends via artifact.deliver, not a synthesized answer - matches
    # test_code_interpreter_csv_summary.py's delivery-only completion path,
    # not test_code_interpreter.py's synthesized-answer one.
    assert scenario.telegram.documents
    assert any("interpreter-report.txt" in path for _chat_id, path, _caption in scenario.telegram.documents)


@pytest.mark.asyncio
async def test_code_interpreter_generate_file_disabled_by_capability_policy(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_generate_file")

    # TERMINAL_RUN left at its secure-by-default disabled state.
    settings = isolated_settings(
        monkeypatch, tmp_path,
        adapters={"code_interpreter": {"enabled": True, "workspace_root": str(workspace)}},
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="code_interpreter_generate_file")

    task = await run_task_to_completion(
        scenario,
        "Use the local code interpreter to create a file named interpreter-report.txt with a "
        "two-line summary of this test, run the script, and tell me where the file is.",
    )

    assert task.status != TaskStatus.COMPLETED
