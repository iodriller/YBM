"""Scenario: proves the code.interpreter approval gate (docs/ROADMAP.md P5)
holds through the *entire* real worker/planner/policy/registry/executor
stack under genuinely default settings - not just at the adapter unit-test
level (see test_code_interpreter.py's
test_code_interpreter_generated_run_needs_approval_on_silent_docker_fallback
for that).

Every other code.interpreter scenario test explicitly sets
`require_approval_for_untrusted_run_python: False` to test execution
correctness in isolation from this gate - this is the one test in the suite
that deliberately leaves it at its real default (True), reusing
test_code_interpreter_generate_file.py's fixture (recorded against a live
LLM, no live LLM needed here since the plan is scripted-replayed) to confirm
that with zero configuration changes, on a machine without Docker running
(true of the machine these fixtures were recorded on), the worker correctly
stops short of running LLM-generated code unsandboxed and surfaces a
clarifying question instead of silently executing it.
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
async def test_generated_code_needs_approval_under_true_default_settings(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_generate_file")

    caps = default_capability_policies()
    caps[Capability.TERMINAL_RUN] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    settings = isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        # require_approval_for_untrusted_run_python left unset - true default (True).
        adapters={"code_interpreter": {"enabled": True, "workspace_root": str(workspace)}},
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="code_interpreter_generate_file")

    task = await run_task_to_completion(
        scenario,
        "Use the local code interpreter to create a file named interpreter-report.txt with a "
        "two-line summary of this test, run the script, and tell me where the file is.",
    )

    # Not COMPLETED: the code never ran, so no file was created and nothing
    # was delivered - the gate fires before any execution, not after.
    assert task.status != TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    interpreter_calls = [call for call in tool_calls if call["tool_name"] == "code.interpreter"]
    assert interpreter_calls
    assert all(call["result"]["status"] == "needs_approval" for call in interpreter_calls)
    assert all(
        "unsandboxed" in (call["result"].get("error_message") or "") for call in interpreter_calls
    )
    assert not scenario.telegram.documents
