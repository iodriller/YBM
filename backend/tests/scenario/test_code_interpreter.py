"""Scenario: "use python to compute X" through the real LLM planner ->
code.interpreter (which makes its own generate_structured call to produce the
script) -> real local Python execution -> validator -> synthesizer. Replayed
from a fixture recorded against a live LLM.
"""

from __future__ import annotations

import pytest

from agent_control.config import CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir


@pytest.mark.asyncio
async def test_code_interpreter_computes_fibonacci(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_fibonacci")

    caps = default_capability_policies()
    caps[Capability.TERMINAL_RUN] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    settings = isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        adapters={"code_interpreter": {"enabled": True, "workspace_root": str(workspace)}},
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
