from __future__ import annotations

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, CodingAgentAdapterConfig
from agent_control.orchestration.default_plans import build_default_task_plan
from agent_control.schemas import Capability, RiskLevel, TaskRecord, ToolCallRequest, ToolResultStatus
from agent_control.tools.coding_agent import CodingAgentAdapter
from agent_control.tools.registry import build_tool_registry


class FakeRunner:
    def __init__(self, stdout: str = "done", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls: list[tuple[list[str], str | None, int]] = []

    async def run(self, command: list[str], *, cwd: str | None, timeout: int) -> tuple[int, str, str]:
        self.calls.append((command, cwd, timeout))
        return self.returncode, self.stdout, self.stderr


def test_registry_exposes_coding_agent_when_terminal_run_is_enabled(tmp_path) -> None:
    codex = tmp_path / "codex.exe"
    codex.write_text("", encoding="utf-8")
    settings = AppSettings(
        _env_file=None,
        adapters={"coding_agent": {"enabled": True, "codex_path": str(codex)}},
        capabilities={
            Capability.TERMINAL_RUN: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )

    registry = build_tool_registry(settings, "http://127.0.0.1:8765")
    definitions = {definition.name: definition for definition in registry.definitions}

    assert definitions["coding.agent"].enabled is True
    assert "run_goal" in definitions["coding.agent"].operations
    assert "coding.agent" in registry.adapters


@pytest.mark.asyncio
async def test_coding_agent_runs_codex_with_workspace(tmp_path) -> None:
    codex = tmp_path / "codex.exe"
    codex.write_text("", encoding="utf-8")
    runner = FakeRunner(stdout='{"status":"ok"}')
    adapter = CodingAgentAdapter(
        CodingAgentAdapterConfig(enabled=True, codex_path=str(codex), workspace_root=str(tmp_path / "workspaces")),
        runner=runner,
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_code",
            tool_name="coding.agent",
            capability=Capability.TERMINAL_RUN,
            risk_level=RiskLevel.HIGH,
            input={"operation": "run_goal", "provider": "codex", "prompt": "create app"},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert runner.calls[0][0][:2] == [str(codex), "exec"]
    assert "-a" not in runner.calls[0][0]
    assert "--skip-git-repo-check" in runner.calls[0][0]
    assert result.output["provider"] == "codex"
    assert result.output["workspace_dir"].endswith("task_task_code")


@pytest.mark.asyncio
async def test_coding_agent_runs_copilot_with_workspace_and_autonomy_flags(tmp_path) -> None:
    copilot = tmp_path / "copilot.exe"
    copilot.write_text("", encoding="utf-8")
    runner = FakeRunner(stdout='{"status":"ok"}')
    adapter = CodingAgentAdapter(
        CodingAgentAdapterConfig(enabled=True, copilot_path=str(copilot), workspace_root=str(tmp_path / "workspaces")),
        runner=runner,
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_code",
            tool_name="coding.agent",
            capability=Capability.TERMINAL_RUN,
            risk_level=RiskLevel.HIGH,
            input={"operation": "run_goal", "provider": "github_copilot", "prompt": "create component"},
        )
    )

    command = runner.calls[0][0]
    assert result.status == ToolResultStatus.SUCCEEDED
    assert command[:2] == [str(copilot), "-p"]
    assert "-C" in command
    assert "--allow-all" in command
    assert "--no-ask-user" in command


@pytest.mark.asyncio
async def test_coding_agent_reports_nonzero_exit_as_failed(tmp_path) -> None:
    codex = tmp_path / "codex.exe"
    codex.write_text("", encoding="utf-8")
    adapter = CodingAgentAdapter(
        CodingAgentAdapterConfig(enabled=True, codex_path=str(codex), workspace_root=str(tmp_path)),
        runner=FakeRunner(stderr="bad flag", returncode=2),
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_code",
            tool_name="coding.agent",
            capability=Capability.TERMINAL_RUN,
            risk_level=RiskLevel.HIGH,
            input={"operation": "run_step", "provider": "codex", "prompt": "continue"},
        )
    )

    assert result.status == ToolResultStatus.FAILED
    assert result.error_class.value == "adapter_failed"
    assert "exit code 2" in (result.error_message or "")


@pytest.mark.asyncio
async def test_coding_agent_reports_usage_limit(tmp_path) -> None:
    codex = tmp_path / "codex.exe"
    codex.write_text("", encoding="utf-8")
    adapter = CodingAgentAdapter(
        CodingAgentAdapterConfig(enabled=True, codex_path=str(codex), workspace_root=str(tmp_path)),
        runner=FakeRunner(stdout="Usage limit reached. Try later."),
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_code",
            tool_name="coding.agent",
            capability=Capability.TERMINAL_RUN,
            risk_level=RiskLevel.HIGH,
            input={"operation": "run_step", "provider": "codex", "prompt": "continue"},
        )
    )

    assert result.status == ToolResultStatus.RATE_LIMITED
    assert result.output["limit_state"]["limited"] is True
    assert result.error_class.value == "usage_limited"


@pytest.mark.asyncio
async def test_coding_agent_detects_copilot_quota_json_output(tmp_path) -> None:
    copilot = tmp_path / "copilot.exe"
    copilot.write_text("", encoding="utf-8")
    adapter = CodingAgentAdapter(
        CodingAgentAdapterConfig(enabled=True, copilot_path=str(copilot), workspace_root=str(tmp_path)),
        runner=FakeRunner(stdout='{"type":"session.error","data":{"errorCode":"quota_exceeded","message":"You have no quota"}}', returncode=1),
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_code",
            tool_name="coding.agent",
            capability=Capability.TERMINAL_RUN,
            risk_level=RiskLevel.HIGH,
            input={"operation": "run_step", "provider": "github_copilot", "prompt": "continue"},
        )
    )

    assert result.status == ToolResultStatus.RATE_LIMITED
    assert result.output["limit_state"]["limited"] is True
    assert result.error_class.value == "usage_limited"


def test_default_plan_routes_explicit_codex_to_coding_agent(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        adapters={"coding_agent": {"enabled": True, "workspace_root": str(tmp_path)}},
        capabilities={
            Capability.TERMINAL_RUN: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.HIGH,
            )
        },
    )

    plan = build_default_task_plan(
        settings,
        TaskRecord(objective="Use Codex and start creating an app for mobile deployment of an LLM"),
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["coding.agent", "coding.agent"]
    assert plan.steps[0].tool_input["provider"] == "codex"
    assert plan.steps[0].tool_input["operation"] == "plan"
    assert plan.steps[1].tool_input["operation"] == "run_step"


def test_default_plan_combines_explicit_codex_with_web_research(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        adapters={"browser": {"enabled": True}, "coding_agent": {"enabled": True, "workspace_root": str(tmp_path)}},
        capabilities={
            Capability.BROWSER_OPEN: CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.LOW),
            Capability.TERMINAL_RUN: CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH),
        },
    )

    plan = build_default_task_plan(settings, TaskRecord(objective="Use Codex and web search for ducks"))

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["browser.open", "coding.agent"]
    assert plan.steps[0].tool_input["operation"] == "research_pages"
    assert plan.steps[1].tool_input["provider"] == "codex"
