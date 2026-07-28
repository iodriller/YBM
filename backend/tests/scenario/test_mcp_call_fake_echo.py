"""Scenario: "use the fake MCP echo tool" through the real LLM planner ->
mcp.client call_tool -> a real fake MCP server subprocess over stdio ->
validator -> synthesizer. Ports e2e/all_cases.json's `mcp_call_fake_echo`
case down to the deterministic tier (docs/ROADMAP.md P2) - the first MCP
category case ported, and the first where the pre-built tool catalog (not
just capability config) has to be part of the planner's prompt for the LLM
to know `fake.echo` exists at all - mirrors test_mcp_client.py's
`_fake_mcp_server` fixture and `write_mcp_catalog` pre-seeding pattern
rather than round-tripping a live discovery call first.

Note: harness.build_scenario() snapshots config_context once at build time
(no config_context_factory, unlike production's cli.py wiring) - so the MCP
catalog must be written to disk *before* build_scenario() runs, or the
planner's prompt won't mention `fake.echo` and the fixture recorded here
would key against the wrong prompt.

Real bug found and deliberately locked in, not routed around: the recorded
plan's `mcp.client call_tool` step succeeds cleanly every time (right tool,
right server, exact echoed text, first try) - but the LLM's own
self-declared `plan.postconditions` list this fixture recorded says
`type: "adapter_proposal"` for it. `PostconditionType` has no entry for "an
external/MCP tool call returned a result" - the closest-sounding option is
`adapter_proposal` (meant for `adapter.factory` scaffolding a *new*
adapter), and the LLM picked it. `orchestration/fulfillment.py`'s
`_postcondition_satisfied()` for `adapter_proposal` checks for an
`adapter_dir`, which a `mcp.client` call never produces, so the gap never
closes - the worker retries the (already-succeeding) tool call 3 times, then
gives up into CLARIFYING. `_postconditions_from_plan()` already has a
correct, deterministic, tool-name-based path for this exact class of
mismatch (used for `adapter.factory`/`artifact.deliver`/`document.manage`)
but it's never reached: `expected_postconditions()` trusts the plan's own
self-declared `postconditions` list first, and only falls back to the
tool-derived rules when that list is empty. A real fix needs either a new
`PostconditionType` plus prompt guidance (touches `planner_system.md`,
which is embedded in every scenario fixture's key - would force
re-recording all of them, not something to bundle into a porting pass) or a
priority change in `expected_postconditions()` (a bigger behavioral change
than warranted here). Tracked in docs/ROADMAP.md; this test asserts the
actual guaranteed behavior instead of the ideal one - the tool call itself
is correct and reproducible, only the completion status is wrong.
"""

from __future__ import annotations

import sys

import pytest

from agent_control.config import CapabilityPolicy, MCPConfig, MCPServerConfig, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus
from agent_control.tools.mcp_client import write_mcp_catalog

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir

pytestmark = pytest.mark.skip(
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/"
    "AnswerValidator prompts); the Operator loop (docs/ROADMAP.md P3 §2.2) is now the sole "
    "execution path and needs its own fixture, recorded fresh against a live LLM - see "
    "orchestration/operator.py and test_operator_loop.py for the pattern. Left in place (not "
    "deleted) so the scenario this file documents survives as a checklist for that re-recording "
    "pass. (The fulfillment-gap bug this file's first test locked in is fixed by this migration - "
    "see fulfillment.py's _postconditions_from_plan priority note - so re-recording will need a "
    "new assertion, not just a new fixture.)"
)


def _write_fake_mcp_server(path) -> None:
    path.write_text(
        """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake")

@mcp.tool()
def echo(text: str) -> str:
    return text

if __name__ == "__main__":
    mcp.run()
""".strip()
        + "\n",
        encoding="utf-8",
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
async def test_mcp_call_fake_echo_tool_call_succeeds_despite_fulfillment_gap_bug(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("mcp_call_fake_echo")
    server_path = workspace / "fake_mcp_server.py"
    _write_fake_mcp_server(server_path)
    catalog_path = workspace / "tool_catalog.json"

    settings = _mcp_settings(monkeypatch, tmp_path, server_path, catalog_path)
    write_mcp_catalog(
        settings.mcp,
        servers=[{"name": "fake", "enabled": True, "healthy": True, "tool_count": 1}],
        tools=[
            {
                "server": "fake",
                "name": "echo",
                "tool": "echo",
                "description": "Echo text",
                "capability": Capability.TERMINAL_RUN.value,
                "risk_level": RiskLevel.LOW.value,
                "disabled": False,
            }
        ],
    )

    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="mcp_call_fake_echo")

    task = await run_task_to_completion(scenario, "Use the fake MCP echo tool to echo hello from E2E.")

    # The real, guaranteed behavior (see module docstring for the known
    # fulfillment-gap bug this locks in rather than routes around): every
    # mcp.client call_tool invocation succeeds with the exact right echoed
    # text, but the task still ends in CLARIFYING because the LLM
    # self-declared an "adapter_proposal" postcondition that a call_tool
    # step can never satisfy.
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    mcp_calls = [call for call in tool_calls if call["tool_name"] == "mcp.client"]
    assert mcp_calls
    for call in mcp_calls:
        assert call["request"].get("input", {}).get("operation") == "call_tool"
        assert call["result"]["status"] == "succeeded"
        assert "hello from E2E" in str(call["result"]["output"].get("result", ""))
    assert task.status == TaskStatus.CLARIFYING
    assert "expected_adapter_proposal_missing" in str(task.metadata.get("clarifying_reason", ""))
    assert task.metadata.get("synthesized_answer") == "hello from E2E"


@pytest.mark.asyncio
async def test_mcp_call_fake_echo_disabled_by_capability_policy(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("mcp_call_fake_echo")
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
    write_mcp_catalog(
        settings.mcp,
        servers=[{"name": "fake", "enabled": True, "healthy": True, "tool_count": 1}],
        tools=[
            {
                "server": "fake",
                "name": "echo",
                "tool": "echo",
                "description": "Echo text",
                "capability": Capability.TERMINAL_RUN.value,
                "risk_level": RiskLevel.LOW.value,
                "disabled": False,
            }
        ],
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="mcp_call_fake_echo")

    task = await run_task_to_completion(scenario, "Use the fake MCP echo tool to echo hello from E2E.")

    # Distinct from the CLARIFYING outcome above (a fulfillment-gap bug after
    # a successful call): here the policy gate should refuse the call
    # outright, so no mcp.client invocation should have run at all.
    assert task.status != TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert not any(call["tool_name"] == "mcp.client" for call in tool_calls)
