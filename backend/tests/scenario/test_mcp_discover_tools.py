"""Scenario: "check what MCP tools are available" through the real LLM
planner -> mcp.client list_tools -> a real fake MCP server subprocess over
stdio. Ports e2e/all_cases.json's `mcp_discover_tools` case down to the
deterministic tier (docs/HISTORY.md P2) - the list_tools counterpart to
test_mcp_call_fake_echo.py's call_tool case.

Confirms the known fulfillment-gap bug documented in
test_mcp_call_fake_echo.py's docstring is broader than first assumed: it
isn't specific to `call_tool`. The recorded plan for THIS objective (no
"call", no "echo", just "check what MCP tools are available") also
self-declares `type: "adapter_proposal"` in `plan.postconditions` - the
planner reaches for that mismatched type for `mcp.client` generally, not
just for one operation. Same result as the call_tool case: 3 successful
`list_tools` calls (verified against a real fake MCP server subprocess,
each one correctly reporting `fake.echo`), then CLARIFYING anyway.
"""

from __future__ import annotations

import sys

import os

import pytest

from agent_control.config import CapabilityPolicy, MCPConfig, MCPServerConfig, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir
from .test_mcp_call_fake_echo import _write_fake_mcp_server

pytestmark = pytest.mark.skipif(
    not os.environ.get("YBM_SCENARIO_RECORD"),
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/AnswerValidator prompts); the Operator loop (docs/HISTORY.md P3 "
    "\u00a72.2) is now the sole execution path and needs its own fixture, recorded fresh "
    "against a live LLM - see orchestration/operator.py and test_operator_loop.py for the "
    "pattern. Left in place (not deleted) so the scenario this file documents survives as "
    "a checklist for that re-recording pass."
)


def _mcp_settings(monkeypatch, tmp_path, server_path, catalog_path):
    caps = default_capability_policies()
    caps[Capability.TERMINAL_RUN] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    return isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        mcp=MCPConfig(
            enabled=True,
            catalog_path=str(catalog_path),
            servers={"fake": MCPServerConfig(command=sys.executable, args=[str(server_path)], timeout_seconds=10)},
        ),
    )


@pytest.mark.asyncio
async def test_mcp_discover_tools_lists_configured_tools_despite_fulfillment_gap_bug(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("mcp_discover_tools")
    server_path = workspace / "fake_mcp_server.py"
    _write_fake_mcp_server(server_path)
    catalog_path = workspace / "tool_catalog.json"

    settings = _mcp_settings(monkeypatch, tmp_path, server_path, catalog_path)
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="mcp_discover_tools")

    task = await run_task_to_completion(scenario, "Check what MCP tools are available.")

    # See module docstring: every list_tools call succeeds and correctly
    # reports fake.echo, but the task still ends in CLARIFYING because of
    # the same adapter_proposal fulfillment-gap bug as
    # test_mcp_call_fake_echo.py - not operation-specific.
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    mcp_calls = [call for call in tool_calls if call["tool_name"] == "mcp.client"]
    assert mcp_calls
    for call in mcp_calls:
        assert call["request"].get("input", {}).get("operation") == "list_tools"
        assert call["result"]["status"] == "succeeded"
        assert any(tool.get("name") == "echo" for tool in call["result"]["output"].get("tools", []))
    assert task.status == TaskStatus.CLARIFYING
    assert "expected_adapter_proposal_missing" in str(task.metadata.get("clarifying_reason", ""))


@pytest.mark.asyncio
async def test_mcp_discover_tools_disabled_by_capability_policy(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("mcp_discover_tools")
    server_path = workspace / "fake_mcp_server.py"
    _write_fake_mcp_server(server_path)
    catalog_path = workspace / "tool_catalog.json"

    # TERMINAL_RUN left at its secure-by-default disabled state.
    settings = isolated_settings(
        monkeypatch, tmp_path,
        mcp=MCPConfig(
            enabled=True,
            catalog_path=str(catalog_path),
            servers={"fake": MCPServerConfig(command=sys.executable, args=[str(server_path)], timeout_seconds=10)},
        ),
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="mcp_discover_tools")

    task = await run_task_to_completion(scenario, "Check what MCP tools are available.")

    assert task.status != TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert not any(call["tool_name"] == "mcp.client" for call in tool_calls)
