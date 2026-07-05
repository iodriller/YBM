from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, CodingAgentAdapterConfig
from agent_control.orchestration.default_plans import build_default_task_plan
from agent_control.schemas import Capability, RiskLevel, TaskRecord, ToolCallRequest, ToolResultStatus
from agent_control.tools.coding_agent import CodingAgentAdapter, latest_session, load_session
from agent_control.tools.registry import build_tool_registry


class FakeProcess:
    def __init__(self, returncode: int = 0, finish_event: asyncio.Event | None = None) -> None:
        self.pid = 4242
        self._returncode = returncode
        self._finish_event = finish_event
        self.terminated = False

    async def wait(self) -> int:
        if self._finish_event is not None:
            await self._finish_event.wait()
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        if self._finish_event is not None:
            self._finish_event.set()


class FakeSpawner:
    def __init__(self, log_content: str = "done", returncode: int = 0, finish_event: asyncio.Event | None = None) -> None:
        self.log_content = log_content
        self.returncode = returncode
        self.finish_event = finish_event
        self.calls: list[tuple[list[str], str, str]] = []

    async def spawn(self, command: list[str], *, cwd: str, log_path: str) -> FakeProcess:
        self.calls.append((command, cwd, log_path))
        Path(log_path).write_text(self.log_content, encoding="utf-8")
        return FakeProcess(returncode=self.returncode, finish_event=self.finish_event)


def _config(tmp_path, **overrides) -> CodingAgentAdapterConfig:
    defaults = dict(
        enabled=True,
        workspace_root=str(tmp_path / "workspaces"),
        session_root=str(tmp_path / "sessions"),
        start_wait_seconds=5,
    )
    defaults.update(overrides)
    return CodingAgentAdapterConfig(**defaults)


def _request(operation: str, provider: str | None = "codex", **extra) -> ToolCallRequest:
    payload: dict = {"operation": operation, **extra}
    if provider is not None:
        payload["provider"] = provider
    return ToolCallRequest(
        task_id="task_code",
        tool_name="coding.agent",
        capability=Capability.TERMINAL_RUN,
        risk_level=RiskLevel.HIGH,
        input=payload,
    )


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
    assert "start" in definitions["coding.agent"].operations
    assert "get_latest_output" in definitions["coding.agent"].operations
    assert "coding.agent" in registry.adapters


@pytest.mark.asyncio
async def test_quick_codex_run_completes_inline(tmp_path) -> None:
    codex = tmp_path / "codex.exe"
    codex.write_text("", encoding="utf-8")
    spawner = FakeSpawner(log_content='{"status":"ok"}')
    adapter = CodingAgentAdapter(_config(tmp_path, codex_path=str(codex)), spawner=spawner)

    result = await adapter.execute(_request("run_goal", prompt="create app"))

    assert result.status == ToolResultStatus.SUCCEEDED
    command = spawner.calls[0][0]
    assert command[:2] == [str(codex), "exec"]
    assert "--skip-git-repo-check" in command
    assert result.output["provider"] == "codex"
    assert result.output["status"] == "completed"
    assert result.output["workspace_dir"].endswith("task_task_code")
    session = latest_session(str(tmp_path / "sessions"), provider="codex")
    assert session is not None and session["status"] == "completed"


@pytest.mark.asyncio
async def test_claude_code_command_shape(tmp_path) -> None:
    claude = tmp_path / "claude.exe"
    claude.write_text("", encoding="utf-8")
    spawner = FakeSpawner(log_content="done")
    adapter = CodingAgentAdapter(_config(tmp_path, claude_path=str(claude)), spawner=spawner)

    result = await adapter.execute(_request("start", provider="claude_code", prompt="fix tests"))

    assert result.status == ToolResultStatus.SUCCEEDED
    command = spawner.calls[0][0]
    assert command[0] == str(claude)
    assert "-p" in command
    assert "--permission-mode" in command


@pytest.mark.asyncio
async def test_copilot_command_keeps_autonomy_flags(tmp_path) -> None:
    copilot = tmp_path / "copilot.exe"
    copilot.write_text("", encoding="utf-8")
    spawner = FakeSpawner(log_content='{"status":"ok"}')
    adapter = CodingAgentAdapter(_config(tmp_path, copilot_path=str(copilot)), spawner=spawner)

    result = await adapter.execute(_request("run_goal", provider="github_copilot", prompt="create component"))

    command = spawner.calls[0][0]
    assert result.status == ToolResultStatus.SUCCEEDED
    assert command[:2] == [str(copilot), "-p"]
    assert "--allow-all" in command
    assert "--no-ask-user" in command


@pytest.mark.asyncio
async def test_long_run_goes_to_background_and_notifies_on_completion(tmp_path) -> None:
    codex = tmp_path / "codex.exe"
    codex.write_text("", encoding="utf-8")
    finish = asyncio.Event()
    spawner = FakeSpawner(log_content="working...", finish_event=finish)
    completions: list[dict] = []

    async def on_complete(session: dict) -> None:
        completions.append(session)

    adapter = CodingAgentAdapter(
        _config(tmp_path, codex_path=str(codex), start_wait_seconds=0),
        spawner=spawner,
        on_complete=on_complete,
    )

    result = await adapter.execute(_request("start", prompt="big refactor"))

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["status"] == "running"
    session_id = result.output["session_id"]

    status = await adapter.execute(_request("status", provider=None))
    assert status.output["status"] == "running"

    finish.set()
    await asyncio.gather(*adapter._watchers)

    assert completions and completions[0]["session_id"] == session_id
    stored = load_session(str(tmp_path / "sessions"), session_id)
    assert stored["status"] == "completed"


@pytest.mark.asyncio
async def test_nonzero_exit_reports_failed(tmp_path) -> None:
    codex = tmp_path / "codex.exe"
    codex.write_text("", encoding="utf-8")
    adapter = CodingAgentAdapter(
        _config(tmp_path, codex_path=str(codex)),
        spawner=FakeSpawner(log_content="bad flag", returncode=2),
    )

    result = await adapter.execute(_request("run_step", prompt="continue"))

    assert result.status == ToolResultStatus.FAILED
    assert result.error_class.value == "adapter_failed"
    assert "exit" in (result.error_message or "").lower()


@pytest.mark.asyncio
async def test_usage_limit_in_log_reports_rate_limited(tmp_path) -> None:
    codex = tmp_path / "codex.exe"
    codex.write_text("", encoding="utf-8")
    adapter = CodingAgentAdapter(
        _config(tmp_path, codex_path=str(codex)),
        spawner=FakeSpawner(log_content="Usage limit reached. Try later.", returncode=1),
    )

    result = await adapter.execute(_request("run_step", prompt="continue"))

    assert result.status == ToolResultStatus.RATE_LIMITED
    assert result.output["limit_state"]["limited"] is True
    assert result.error_class.value == "usage_limited"


@pytest.mark.asyncio
async def test_stop_terminates_running_session(tmp_path) -> None:
    codex = tmp_path / "codex.exe"
    codex.write_text("", encoding="utf-8")
    finish = asyncio.Event()
    adapter = CodingAgentAdapter(
        _config(tmp_path, codex_path=str(codex), start_wait_seconds=0),
        spawner=FakeSpawner(log_content="working", finish_event=finish),
    )

    started = await adapter.execute(_request("start", prompt="long task"))
    session_id = started.output["session_id"]

    stopped = await adapter.execute(_request("stop", provider=None, session_id=session_id))
    assert stopped.status == ToolResultStatus.SUCCEEDED
    await asyncio.gather(*adapter._watchers)

    stored = load_session(str(tmp_path / "sessions"), session_id)
    assert stored["status"] in {"completed", "failed", "stopped"}


@pytest.mark.asyncio
async def test_status_without_sessions_probes_clis(tmp_path) -> None:
    adapter = CodingAgentAdapter(_config(tmp_path), spawner=FakeSpawner())

    result = await adapter.execute(_request("status", provider=None))

    assert result.status == ToolResultStatus.SUCCEEDED
    assert "No coding sessions found yet." in (result.output["summary"] or "")


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

    assert plan is None


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

    assert plan is None
