from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Protocol
from uuid import uuid4

from agent_control.config import CodingAgentAdapterConfig
from agent_control.schemas import ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus


PROVIDERS = ("codex", "github_copilot", "claude_code")

RUN_OPERATIONS = {"start", "plan", "run_step", "run_goal", "resume"}


class ProcessHandle(Protocol):
    pid: int

    async def wait(self) -> int:
        ...

    def terminate(self) -> None:
        ...


class ProcessSpawner(Protocol):
    async def spawn(self, command: list[str], *, cwd: str, log_path: str) -> ProcessHandle:
        ...


class AsyncProcessSpawner:
    """Spawn the coding CLI detached from the tool call, streaming output to a log file."""

    async def spawn(self, command: list[str], *, cwd: str, log_path: str) -> ProcessHandle:
        log_file = open(log_path, "ab")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        finally:
            # The child holds its own copy of the file descriptor.
            log_file.close()
        return _AsyncioProcessHandle(process)


class _AsyncioProcessHandle:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self.pid = process.pid

    async def wait(self) -> int:
        return await self._process.wait()

    def terminate(self) -> None:
        if self._process.returncode is None:
            self._process.terminate()


# Async callback invoked with the final session dict when a background run ends.
SessionCompletionCallback = Callable[[dict], Awaitable[None]]


class CodingAgentAdapter:
    """Session-based access to external coding CLIs (Codex, Claude Code, Copilot).

    Runs are background sessions persisted as JSON files under
    ``config.session_root`` so status/stop/log queries work from any process.
    ``start`` waits up to ``config.start_wait_seconds`` for quick runs to finish
    inline; longer runs keep going in the background and ``on_complete`` is
    invoked with the final session when they end.
    """

    def __init__(
        self,
        config: CodingAgentAdapterConfig,
        spawner: ProcessSpawner | None = None,
        on_complete: SessionCompletionCallback | None = None,
    ) -> None:
        self.config = config
        self.spawner = spawner or AsyncProcessSpawner()
        self.on_complete = on_complete
        self._processes: dict[str, ProcessHandle] = {}
        self._watchers: set[asyncio.Task] = set()

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        if not self.config.enabled:
            return _failed(request, "coding agent adapter is disabled")
        operation = str(request.input.get("operation") or "run_goal")
        provider = str(request.input.get("provider") or "")
        try:
            if operation in RUN_OPERATIONS:
                return await self._start(request, operation, provider)
            if operation == "status":
                output = self._status(request, provider)
            elif operation == "get_latest_output":
                output = self._latest_output(request, provider)
            elif operation == "stop":
                output = self._stop(request, provider)
            elif operation == "limits":
                output = self._probe(request, provider)
            else:
                return _failed(request, f"unsupported coding agent operation: {operation}")
        except Exception as exc:
            return _failed(request, f"coding agent operation failed: {exc}")
        output.setdefault("operation", operation)
        output.setdefault("provider", provider or str(output.get("provider") or ""))
        output["terminal_output"] = [_terminal_output(operation, output)]
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

    async def _start(self, request: ToolCallRequest, operation: str, provider: str) -> ToolCallResult:
        if provider not in PROVIDERS:
            return _failed(request, f"unsupported coding provider: {provider or '<missing>'}")
        prompt = str(request.input.get("prompt") or request.input.get("objective") or "").strip()
        if not prompt:
            return _failed(request, "prompt or objective is required")
        executable = self._executable(provider)
        if executable is None:
            return _failed(request, f"{provider} CLI was not found on PATH or config")

        workspace = self._workspace(request)
        workspace.mkdir(parents=True, exist_ok=True)
        session_root = self._session_root()
        session_root.mkdir(parents=True, exist_ok=True)

        session_id = str(request.input.get("session_id") or f"{provider}_{uuid4().hex[:12]}")
        log_path = session_root / f"{session_id}.log"
        command = self._command(provider, executable, prompt, workspace)

        session = {
            "session_id": session_id,
            "provider": provider,
            "operation": operation,
            "prompt": prompt[:2000],
            "task_id": request.task_id,
            "workspace_dir": str(workspace),
            "log_path": str(log_path),
            "status": "running",
            "pid": None,
            "returncode": None,
            "started_at": _now(),
            "ended_at": None,
            "changed_files": [],
            "files_before": _workspace_snapshot(workspace),
            "summary": None,
            "limit_state": {"limited": False, "source": "cli_output"},
        }

        process = await self.spawner.spawn(command, cwd=str(workspace), log_path=str(log_path))
        session["pid"] = process.pid
        self._processes[session_id] = process
        _write_session(session_root, session)

        try:
            returncode = await asyncio.wait_for(process.wait(), timeout=self.config.start_wait_seconds)
        except (asyncio.TimeoutError, TimeoutError):
            watcher = asyncio.create_task(self._watch(process, session_id))
            self._watchers.add(watcher)
            watcher.add_done_callback(self._watchers.discard)
            output = _session_output(session, log_tail=read_log_tail(str(log_path)))
            output["summary"] = (
                f"{provider} is working in the background (session {session_id}). "
                "Ask for its status any time; a completion report is sent when it finishes."
            )
            output["terminal_output"] = [_terminal_output(operation, output)]
            return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

        session = self._finalize(session_id, returncode)
        return self._result_from_session(request, operation, session)

    async def _watch(self, process: ProcessHandle, session_id: str) -> None:
        timed_out = False
        remaining = max(self.config.timeout_seconds - self.config.start_wait_seconds, 1)
        try:
            returncode = await asyncio.wait_for(process.wait(), timeout=remaining)
        except (asyncio.TimeoutError, TimeoutError):
            timed_out = True
            process.terminate()
            try:
                returncode = await asyncio.wait_for(process.wait(), timeout=10)
            except (asyncio.TimeoutError, TimeoutError):
                returncode = -1
        session = self._finalize(session_id, returncode, timed_out=timed_out)
        if self.on_complete is not None:
            await self.on_complete(session)

    def _finalize(self, session_id: str, returncode: int, *, timed_out: bool = False) -> dict:
        session_root = self._session_root()
        session = load_session(str(session_root), session_id) or {"session_id": session_id}
        self._processes.pop(session_id, None)

        log_tail = read_log_tail(str(session.get("log_path") or ""), max_chars=self.config.output_limit_chars)
        limit_state = _limit_state(log_tail, self.config.rate_limit_patterns, self.config.usage_limit_patterns)
        workspace = Path(str(session.get("workspace_dir") or "."))
        changed = _changed_files(session.get("files_before") or {}, _workspace_snapshot(workspace))

        if timed_out:
            status = "failed"
            summary = f"{session.get('provider')} run exceeded {self.config.timeout_seconds}s and was terminated."
        elif limit_state.get("limited"):
            status = "failed"
            summary = f"{session.get('provider')} reported a {limit_state.get('kind')} limit."
        elif returncode == 0:
            status = "completed"
            summary = _completion_summary(session, changed, log_tail)
        else:
            status = "failed"
            summary = f"{session.get('provider')} exited with code {returncode}. Last output: {log_tail[-600:]}"

        session.update(
            status=status,
            returncode=returncode,
            ended_at=_now(),
            changed_files=changed,
            summary=summary,
            limit_state=limit_state,
        )
        _write_session(session_root, session)
        return session

    def _result_from_session(self, request: ToolCallRequest, operation: str, session: dict) -> ToolCallResult:
        output = _session_output(session, log_tail=read_log_tail(str(session.get("log_path") or "")))
        output["terminal_output"] = [_terminal_output(operation, output)]
        limit_state = session.get("limit_state") or {}
        if limit_state.get("limited"):
            return ToolCallResult(
                request_id=request.id,
                status=ToolResultStatus.RATE_LIMITED,
                output=output,
                error_class=ErrorClass.USAGE_LIMITED,
                error_message="coding agent usage limit reached",
            )
        if session.get("returncode") not in (None, 0):
            return ToolCallResult(
                request_id=request.id,
                status=ToolResultStatus.FAILED,
                output=output,
                error_class=ErrorClass.ADAPTER_FAILED,
                error_message=str(session.get("summary") or "coding agent run failed"),
            )
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

    def _status(self, request: ToolCallRequest, provider: str) -> dict:
        session = self._resolve_session(request, provider)
        if session is None:
            return self._probe(request, provider, summary_prefix="No coding sessions found yet. ")
        log_tail = read_log_tail(str(session.get("log_path") or ""))
        output = _session_output(session, log_tail=log_tail)
        if session.get("status") == "running":
            workspace = Path(str(session.get("workspace_dir") or "."))
            output["changed_files"] = _changed_files(session.get("files_before") or {}, _workspace_snapshot(workspace))
            output["summary"] = format_session_status(session, log_tail=log_tail, changed_files=output["changed_files"])
        else:
            output["summary"] = str(session.get("summary") or format_session_status(session, log_tail=log_tail))
        return output

    def _latest_output(self, request: ToolCallRequest, provider: str) -> dict:
        session = self._resolve_session(request, provider)
        if session is None:
            raise ValueError("no coding sessions found")
        log_tail = read_log_tail(str(session.get("log_path") or ""), max_chars=self.config.output_limit_chars)
        output = _session_output(session, log_tail=log_tail)
        output["summary"] = log_tail or "The session log is empty so far."
        return output

    def _stop(self, request: ToolCallRequest, provider: str) -> dict:
        session = self._resolve_session(request, provider)
        if session is None:
            raise ValueError("no coding sessions found")
        session_id = str(session["session_id"])
        if session.get("status") != "running":
            output = _session_output(session)
            output["summary"] = f"Session {session_id} is not running (status: {session.get('status')})."
            return output
        handle = self._processes.get(session_id)
        if handle is not None:
            handle.terminate()
            summary = f"Stop requested for session {session_id}; final report follows when it exits."
        else:
            stop_session_process(session)
            session["status"] = "stopped"
            session["ended_at"] = _now()
            session["summary"] = f"Session {session_id} was stopped."
            _write_session(self._session_root(), session)
            summary = str(session["summary"])
        output = _session_output(session)
        output["summary"] = summary
        return output

    def _probe(self, request: ToolCallRequest, provider: str, *, summary_prefix: str = "") -> dict:
        providers = [provider] if provider in PROVIDERS else list(PROVIDERS)
        lines = []
        for name in providers:
            available = self._executable(name) is not None
            lines.append(f"{name}: {'available' if available else 'not found on PATH or config'}")
        return {
            "provider": provider or "all",
            "workspace_dir": str(self._workspace(request)),
            "limit_state": {"limited": False, "source": "local_cli_probe"},
            "summary": summary_prefix + " | ".join(lines),
        }

    def _resolve_session(self, request: ToolCallRequest, provider: str) -> dict | None:
        session_root = str(self._session_root())
        session_id = str(request.input.get("session_id") or "")
        if session_id:
            return load_session(session_root, session_id)
        return latest_session(session_root, provider=provider or None)

    def _command(self, provider: str, executable: str, prompt: str, workspace: Path) -> list[str]:
        if provider == "codex":
            return [
                executable,
                "exec",
                "--json",
                "--cd",
                str(workspace),
                "--sandbox",
                "workspace-write",
                "--skip-git-repo-check",
                prompt,
            ]
        if provider == "claude_code":
            return [
                executable,
                "-p",
                prompt,
                "--output-format",
                "text",
                "--permission-mode",
                "acceptEdits",
            ]
        args = [
            "-p",
            prompt,
            "-C",
            str(workspace),
            "--output-format",
            "json",
            "--allow-all",
            "--no-ask-user",
        ]
        if Path(executable).name.lower() in {"gh", "gh.exe"}:
            return [executable, "copilot", "--", *args]
        return [executable, *args]

    def _executable(self, provider: str) -> str | None:
        configured = {
            "codex": self.config.codex_path,
            "github_copilot": self.config.copilot_path,
            "claude_code": self.config.claude_path,
        }.get(provider)
        if configured and Path(configured).exists():
            return configured
        names = {
            "codex": ["codex"],
            "github_copilot": ["copilot", "gh"],
            "claude_code": ["claude"],
        }.get(provider, [])
        for name in names:
            found = shutil.which(name)
            if found:
                return found
        return None

    def _workspace(self, request: ToolCallRequest) -> Path:
        value = request.input.get("workspace_dir")
        if value:
            return Path(str(value)).expanduser().resolve()
        return (Path(self.config.workspace_root).expanduser().resolve() / f"task_{request.task_id}")

    def _session_root(self) -> Path:
        return Path(self.config.session_root).expanduser().resolve()


# --- session store helpers (pure file reads/writes, usable from any process) ---


def load_session(session_root: str, session_id: str) -> dict | None:
    path = Path(session_root) / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_sessions(session_root: str, limit: int = 20) -> list[dict]:
    root = Path(session_root)
    if not root.exists():
        return []
    sessions = []
    for path in root.glob("*.json"):
        try:
            sessions.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    sessions.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return sessions[:limit]


def latest_session(session_root: str, provider: str | None = None) -> dict | None:
    for session in load_sessions(session_root):
        if provider is None or session.get("provider") == provider:
            return session
    return None


def read_log_tail(log_path: str, max_chars: int = 2000) -> str:
    path = Path(log_path)
    if not log_path or not path.exists():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-max_chars:].decode(errors="replace").strip()


def stop_session_process(session: dict) -> bool:
    """Best-effort cross-process kill by pid; used when the owning worker is gone."""
    pid = session.get("pid")
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        else:
            os.kill(int(pid), 15)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def format_session_status(session: dict, *, log_tail: str = "", changed_files: list[str] | None = None) -> str:
    provider = session.get("provider") or "coding agent"
    status = session.get("status") or "unknown"
    lines = [f"{provider} session {session.get('session_id')}: {status}"]
    started = _parse_time(session.get("started_at"))
    if started is not None:
        end = _parse_time(session.get("ended_at")) or datetime.now(timezone.utc)
        minutes = max(int((end - started).total_seconds() // 60), 0)
        lines.append(f"Runtime: {minutes} min")
    changed = changed_files if changed_files is not None else session.get("changed_files") or []
    if changed:
        lines.append(f"Changed files ({len(changed)}): " + ", ".join(changed[:10]))
    if session.get("summary") and status != "running":
        lines.append(str(session["summary"]))
    if log_tail:
        lines.append("")
        lines.append("Latest output:")
        lines.append(log_tail[-1200:])
    return "\n".join(lines)


def session_completion_message(session: dict) -> str:
    provider = session.get("provider") or "coding agent"
    status = session.get("status")
    headline = {
        "completed": f"{provider} finished.",
        "failed": f"{provider} did not finish cleanly.",
        "stopped": f"{provider} was stopped.",
    }.get(str(status), f"{provider} session ended ({status}).")
    lines = [headline]
    summary = str(session.get("summary") or "").strip()
    if summary:
        lines.append(summary)
    changed = session.get("changed_files") or []
    if changed:
        lines.append(f"Changed files ({len(changed)}):")
        lines.extend(f"- {item}" for item in changed[:20])
    lines.append(f"Workspace: {session.get('workspace_dir')}")
    lines.append(f"Session: {session.get('session_id')}")
    return "\n".join(lines)


def _write_session(session_root: Path, session: dict) -> None:
    session_root.mkdir(parents=True, exist_ok=True)
    path = session_root / f"{session['session_id']}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _session_output(session: dict, *, log_tail: str = "") -> dict:
    return {
        "operation": str(session.get("operation") or "status"),
        "provider": str(session.get("provider") or ""),
        "workspace_dir": session.get("workspace_dir"),
        "session_id": session.get("session_id"),
        "status": session.get("status"),
        "pid": session.get("pid"),
        "returncode": session.get("returncode"),
        "started_at": session.get("started_at"),
        "ended_at": session.get("ended_at"),
        "limit_state": session.get("limit_state") or {},
        "changed_files": session.get("changed_files") or [],
        "log_path": session.get("log_path"),
        "log_tail": log_tail,
        "summary": session.get("summary"),
    }


def _completion_summary(session: dict, changed: list[str], log_tail: str) -> str:
    provider = session.get("provider")
    parts = [f"{provider} completed the run."]
    if changed:
        parts.append(f"{len(changed)} file(s) changed.")
    tail = log_tail[-600:].strip()
    if tail:
        parts.append(tail)
    return " ".join(parts)


def _limit_state(text: str, rate_patterns: list[str], usage_patterns: list[str]) -> dict:
    lowered = text.lower()
    if any(pattern.lower() in lowered for pattern in usage_patterns):
        return {"limited": True, "kind": "usage", "source": "cli_output"}
    if any(pattern.lower() in lowered for pattern in rate_patterns):
        return {"limited": True, "kind": "rate", "source": "cli_output"}
    return {"limited": False, "source": "cli_output"}


def _workspace_snapshot(workspace: Path) -> dict[str, list[int]]:
    snapshot: dict[str, list[int]] = {}
    if not workspace.exists():
        return snapshot
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            snapshot[str(path.resolve().relative_to(workspace))] = [stat.st_size, int(stat.st_mtime_ns)]
        except OSError:
            continue
    return snapshot


def _changed_files(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changed = []
    for path, stat in sorted(after.items()):
        if list(before.get(path) or []) != list(stat):
            changed.append(path)
    return changed


def _terminal_output(operation: str, output: dict) -> dict:
    return {
        "content": output.get("summary") or f"coding.agent {operation} completed.",
        "is_final": True,
        "exit_code": output.get("returncode") or 0,
    }


def _failed(request: ToolCallRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        request_id=request.id,
        status=ToolResultStatus.FAILED,
        error_class=ErrorClass.ADAPTER_FAILED,
        error_message=message,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
