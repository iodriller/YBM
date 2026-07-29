"""Scenario: "normalize this inline task list into JSON" through the real
LLM planner -> code.interpreter generate_and_run -> real local Python
execution. Ports e2e/all_cases.json's `code_interpreter_json_transform`
case down to the deterministic tier (docs/HISTORY.md P2) - data-in-the-
objective (no CSV/PDF fixture file needed), normalized-JSON-out; also
asserts `coding.agent`/`vscode.copilot_terminal` are never selected, the
same routing guard as test_implicit_code_interpreter_numbers_report.py.
"""

from __future__ import annotations

import os

import pytest

from agent_control.config import CapabilityPolicy, default_capability_policies
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


@pytest.mark.asyncio
async def test_code_interpreter_json_transform_normalizes_task_list(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_json_transform")

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
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="code_interpreter_json_transform")

    task = await run_task_to_completion(
        scenario,
        "Use the local code interpreter to normalize this task list into tasks-normalized.json: "
        "task A priority high owner Oney; task B priority low owner Agent; task C priority medium owner Oney.",
    )

    assert task.status == TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    tool_names = {call["tool_name"] for call in tool_calls}
    assert "code.interpreter" in tool_names
    assert "coding.agent" not in tool_names
    assert "vscode.copilot_terminal" not in tool_names
    created_files = [
        name
        for call in tool_calls
        if call["tool_name"] == "code.interpreter"
        for name in (call["result"] or {}).get("output", {}).get("files_created", [])
    ]
    assert any("tasks-normalized.json" in name for name in created_files)


@pytest.mark.asyncio
async def test_code_interpreter_json_transform_disabled_by_capability_policy(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_json_transform")

    # TERMINAL_RUN left at its secure-by-default disabled state.
    settings = isolated_settings(
        monkeypatch, tmp_path,
        adapters={"code_interpreter": {"enabled": True, "workspace_root": str(workspace)}},
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="code_interpreter_json_transform")

    task = await run_task_to_completion(
        scenario,
        "Use the local code interpreter to normalize this task list into tasks-normalized.json: "
        "task A priority high owner Oney; task B priority low owner Agent; task C priority medium owner Oney.",
    )

    assert task.status != TaskStatus.COMPLETED
