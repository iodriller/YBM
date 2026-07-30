"""Scenario: "use python to compute X" through the real Operator loop ->
code.interpreter (which makes its own generate_structured call to produce the
script) -> real local Python execution -> Auditor. Replayed from a fixture
recorded against a live LLM. Fixture re-recorded 2026-07-28
(`ybm scenario record code_interpreter_fibonacci`, localdeploy_qwen3vl_8b).
"""

from __future__ import annotations

from agent_control.config import CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus
import pytest

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir


@pytest.mark.asyncio
async def test_code_interpreter_computes_fibonacci(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_fibonacci")

    caps = default_capability_policies()
    caps[Capability.TERMINAL_RUN] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    settings = isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        # require_approval_for_untrusted_run_python: False - this test is
        # about execution correctness (right tool, right file, right
        # answer), not the approval gate itself (see
        # test_code_interpreter.py's
        # test_code_interpreter_generated_run_needs_approval_on_silent_docker_fallback
        # for that, and test_code_interpreter_default_settings_need_approval_without_docker.py
        # for this same objective proven to correctly require approval
        # under the real default config, with no opt-out).
        adapters={
            "code_interpreter": {
                "enabled": True,
                "workspace_root": str(workspace),
                "require_approval_for_untrusted_run_python": False,
            }
        },
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="code_interpreter_fibonacci")

    task = await run_task_to_completion(
        scenario, "use python to compute the 20th fibonacci number and tell me the result"
    )

    assert task.status == TaskStatus.COMPLETED
    assert "6765" in task.metadata.get("synthesized_answer", "")
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "code.interpreter" for call in tool_calls)


@pytest.mark.asyncio
async def test_code_interpreter_disabled_by_capability_policy(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_fibonacci")

    # TERMINAL_RUN left at its secure-by-default disabled state - the plan
    # from the fixture should not be executable, proving the policy gate
    # (not just the tool description) actually blocks it.
    settings = isolated_settings(
        monkeypatch, tmp_path,
        adapters={"code_interpreter": {"enabled": True, "workspace_root": str(workspace)}},
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="code_interpreter_fibonacci")

    task = await run_task_to_completion(
        scenario, "use python to compute the 20th fibonacci number and tell me the result"
    )

    assert task.status != TaskStatus.COMPLETED
