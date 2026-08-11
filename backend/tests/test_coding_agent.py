from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, CodingAgentAdapterConfig
from agent_control.schemas import Capability, RiskLevel, ToolCallRequest, ToolResultStatus
from agent_control.tools.coding_agent import (
    CodingAgentAdapter,
    _execution_prompt,
    _prefer_native_claude_executable,
    _sandbox_compatible_codex_path,
    _workspace_snapshot,
    latest_session,
    load_session,
    mark_session_progress_notified,
    scan_coding_sessions_once,
    session_progress_due,
    session_progress_message,
    terminal_session_result,
)
from agent_control.tools.registry import build_tool_registry


def test_execution_prompt_bounds_optional_verification() -> None:
    prompt = _execution_prompt("Build the requested app.")

    assert "Do not launch GUI/headless browsers" in prompt
    assert "report the gap and finish" in prompt


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
        self.environments: list[dict[str, str] | None] = []

    async def spawn(
        self,
        command: list[str],
        *,
        cwd: str,
        log_path: str,
        env: dict[str, str] | None = None,
    ) -> FakeProcess:
        self.calls.append((command, cwd, log_path))
        self.environments.append(env)
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


def test_codex_path_hides_only_incompatible_windowsapps_shell_entries() -> None:
    original = ";".join(
        [
            r"C:\Tools",
            r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.4.0_x64__8wekyb3d8bbwe",
            r"C:\Users\me\AppData\Local\Microsoft\WindowsApps",
            r"C:\Windows\System32\WindowsPowerShell\v1.0",
        ]
    )

    compatible = _sandbox_compatible_codex_path(
        original,
        r"C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.4.0_x64__8wekyb3d8bbwe\pwsh.exe",
        is_windows=True,
    )

    assert "WindowsApps" not in compatible
    assert r"C:\Tools" in compatible
    assert r"C:\Windows\System32\WindowsPowerShell\v1.0" in compatible
    assert _sandbox_compatible_codex_path(original, r"C:\Tools\pwsh.exe", is_windows=True) == original

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
    assert result.output["workspace_dir"].endswith("task_code")
    assert "create app" in command[-1]
    assert "make those changes on disk" in command[-1]
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


def test_windows_claude_npm_shim_resolves_to_tracked_native_binary(tmp_path, monkeypatch) -> None:
    npm_root = tmp_path / "npm"
    native = npm_root / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    native.parent.mkdir(parents=True)
    native.write_text("", encoding="utf-8")
    shim = npm_root / "claude.CMD"
    shim.write_text("@echo off", encoding="utf-8")

    # Injected rather than monkeypatching os.name: that attribute lives on the
    # real os module, so setting it flips pathlib to the Windows flavour and
    # every later Path() raises NotImplementedError on POSIX CI.
    resolved = _prefer_native_claude_executable(str(shim), is_windows=True)

    assert resolved == str(native)


def test_native_claude_preference_is_a_windows_only_behaviour() -> None:
    """On POSIX the npm shim is the real entry point; rewriting it to a .exe
    that cannot exist there would break an otherwise working install."""
    assert _prefer_native_claude_executable("/usr/local/bin/claude", is_windows=False) == "/usr/local/bin/claude"

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
async def test_quota_introspection_prose_does_not_report_usage_limit(tmp_path) -> None:
    claude = tmp_path / "claude.exe"
    claude.write_text("", encoding="utf-8")
    adapter = CodingAgentAdapter(
        _config(tmp_path, claude_path=str(claude)),
        spawner=FakeSpawner(
            log_content="Build complete. I have no quota-introspection tool.",
            returncode=0,
        ),
    )

    result = await adapter.execute(
        _request("run_step", provider="claude_code", prompt="build the app")
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["limit_state"]["limited"] is False

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

@pytest.mark.asyncio
async def test_scan_coding_sessions_returns_terminal_unnotified_sessions(tmp_path) -> None:
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    log_path = session_root / "codex_done.log"
    log_path.write_text("done", encoding="utf-8")
    (session_root / "codex_done.json").write_text(
        """
{
  "session_id": "codex_done",
  "request_id": "toolreq_1",
  "provider": "codex",
  "status": "completed",
  "task_id": "task_1",
  "workspace_dir": ".",
  "log_path": "LOG_PATH",
  "returncode": 0,
  "changed_files": [],
  "summary": "finished",
  "limit_state": {"limited": false}
}
""".replace("LOG_PATH", str(log_path).replace("\\", "\\\\")),
        encoding="utf-8",
    )

    sessions = await scan_coding_sessions_once(str(session_root))
    result = terminal_session_result(sessions[0])

    assert sessions[0]["session_id"] == "codex_done"
    assert result.status == ToolResultStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_session_watcher_gives_runner_time_to_write_terminal_record(tmp_path) -> None:
    session_root = tmp_path / "sessions"
    workspace = tmp_path / "workspace"
    session_root.mkdir()
    workspace.mkdir()
    session_path = session_root / "claude_race.json"
    session_path.write_text(
        json.dumps(
            {
                "session_id": "claude_race",
                "provider": "claude_code",
                "status": "running",
                "task_id": "task_1",
                "workspace_dir": str(workspace),
                "log_path": str(session_root / "claude_race.log"),
                "pid": 99999999,
                "files_before": {},
                "returncode": None,
            }
        ),
        encoding="utf-8",
    )

    first_scan = await scan_coding_sessions_once(str(session_root))
    observed = load_session(str(session_root), "claude_race")

    assert first_scan == []
    assert observed["status"] == "running"
    assert observed["process_missing_since"]

    observed["process_missing_since"] = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    session_path.write_text(json.dumps(observed), encoding="utf-8")
    second_scan = await scan_coding_sessions_once(str(session_root))

    assert second_scan[0]["status"] == "failed"
    assert "without a runner final report" in second_scan[0]["summary"]


def test_coding_session_progress_heartbeat_is_durable_and_bounded(tmp_path) -> None:
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    now = datetime(2026, 8, 9, 13, 0, tzinfo=timezone.utc)
    session = {
        "session_id": "codex_live",
        "provider": "codex",
        "status": "running",
        "started_at": (now - timedelta(seconds=301)).isoformat(),
        "workspace_dir": str(tmp_path / "workspace"),
    }
    (session_root / "codex_live.json").write_text(
        json.dumps(session),
        encoding="utf-8",
    )

    assert session_progress_due(session, 300, now=now)
    marked = mark_session_progress_notified(str(session_root), "codex_live")

    assert marked is not None
    assert marked["progress_notified_at"]
    assert not session_progress_due(marked, 300, now=datetime.now(timezone.utc))
    message = session_progress_message(marked)
    assert "codex is still working" in message
    assert "Workspace preserved at:" in message


def test_coding_session_progress_heartbeat_ignores_terminal_and_new_sessions() -> None:
    now = datetime(2026, 8, 9, 13, 0, tzinfo=timezone.utc)
    recent = {
        "status": "running",
        "started_at": (now - timedelta(seconds=299)).isoformat(),
    }
    terminal = {
        "status": "completed",
        "started_at": (now - timedelta(seconds=600)).isoformat(),
    }

    assert not session_progress_due(recent, 300, now=now)
    assert not session_progress_due(terminal, 300, now=now)


def test_workspace_snapshot_excludes_agent_control_files_from_project_evidence(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    internal = workspace / ".agent_control" / "logs" / "coding_agent_session.jsonl"
    internal.parent.mkdir(parents=True)
    internal.write_text("internal", encoding="utf-8")
    (workspace / "README.md").write_text("project", encoding="utf-8")

    snapshot = _workspace_snapshot(workspace)

    assert list(snapshot) == ["README.md"]
