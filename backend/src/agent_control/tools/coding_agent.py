from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
from typing import Protocol
from uuid import uuid4

from agent_control.config import CodingAgentAdapterConfig
from agent_control.schemas import ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus


class CommandRunner(Protocol):
    async def run(self, command: list[str], *, cwd: str | None, timeout: int) -> tuple[int, str, str]:
        ...


class AsyncSubprocessRunner:
    async def run(self, command: list[str], *, cwd: str | None, timeout: int) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return process.returncode or 0, stdout_bytes.decode(errors="replace"), stderr_bytes.decode(errors="replace")


class CodingAgentAdapter:
    def __init__(self, config: CodingAgentAdapterConfig, runner: CommandRunner | None = None) -> None:
        self.config = config
        self.runner = runner or AsyncSubprocessRunner()

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        if not self.config.enabled:
            return _failed(request, "coding agent adapter is disabled")
        operation = str(request.input.get("operation") or "run_goal")
        provider = str(request.input.get("provider") or "")
        try:
            if operation in {"status", "limits"}:
                output = self._status_output(request, operation, provider)
            elif operation == "stop":
                output = self._status_output(request, operation, provider, summary="Stop requested for coding agent session.")
            elif operation in {"plan", "run_step", "run_goal", "resume"}:
                output = await self._run(request, operation, provider)
            else:
                return _failed(request, f"unsupported coding agent operation: {operation}")
        except TimeoutError:
            return ToolCallResult(
                request_id=request.id,
                status=ToolResultStatus.TIMEOUT,
                error_class=ErrorClass.TRANSIENT,
                error_message="coding agent command timed out",
            )
        except Exception as exc:
            return _failed(request, f"coding agent operation failed: {exc}")

        output["operation"] = operation
        output["provider"] = provider
        output["terminal_output"] = [_terminal_output(operation, output)]
        status = ToolResultStatus.SUCCEEDED
        limit_state = output.get("limit_state")
        if isinstance(limit_state, dict) and limit_state.get("limited"):
            status = ToolResultStatus.RATE_LIMITED
        elif output.get("returncode") not in (None, 0):
            status = ToolResultStatus.FAILED
        return ToolCallResult(
            request_id=request.id,
            status=status,
            output=output,
            error_class=(
                ErrorClass.USAGE_LIMITED
                if status == ToolResultStatus.RATE_LIMITED
                else ErrorClass.ADAPTER_FAILED
                if status == ToolResultStatus.FAILED
                else None
            ),
            error_message=(
                "coding agent usage limit reached"
                if status == ToolResultStatus.RATE_LIMITED
                else output.get("summary")
                if status == ToolResultStatus.FAILED
                else None
            ),
        )

    async def _run(self, request: ToolCallRequest, operation: str, provider: str) -> dict:
        prompt = str(request.input.get("prompt") or request.input.get("objective") or "").strip()
        if not prompt:
            raise ValueError("prompt or objective is required")
        workspace = self._workspace(request)
        workspace.mkdir(parents=True, exist_ok=True)
        before = _workspace_snapshot(workspace)
        command = self._command(provider, prompt, workspace)
        returncode, stdout, stderr = await self.runner.run(command, cwd=str(workspace), timeout=self.config.timeout_seconds)
        after = _workspace_snapshot(workspace)
        changed_files = _changed_files(before, after)
        stdout = stdout[: self.config.output_limit_chars]
        stderr = stderr[: self.config.output_limit_chars]
        combined = f"{stdout}\n{stderr}"
        limit_state = _limit_state(combined, self.config.rate_limit_patterns, self.config.usage_limit_patterns)
        return {
            "workspace_dir": str(workspace),
            "session_id": str(request.input.get("session_id") or f"{provider}_{uuid4().hex[:12]}"),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
            "limit_state": limit_state,
            "files_before": sorted(before),
            "files_after": sorted(after),
            "changed_files": changed_files,
            "summary": _summary(provider, operation, returncode, stdout, stderr, limit_state),
        }

    def _status_output(self, request: ToolCallRequest, operation: str, provider: str, *, summary: str | None = None) -> dict:
        workspace = self._workspace(request)
        available = self._executable(provider) is not None
        return {
            "workspace_dir": str(workspace),
            "session_id": request.input.get("session_id"),
            "returncode": 0 if available else None,
            "limit_state": {"available": available, "limited": False, "source": "local_cli_probe"},
            "summary": summary or (
                f"{provider} CLI is available." if available else f"{provider} CLI was not found on PATH or config."
            ),
        }

    def _command(self, provider: str, prompt: str, workspace: Path) -> list[str]:
        executable = self._executable(provider)
        if executable is None:
            raise ValueError(f"{provider} CLI was not found")
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
        if provider == "github_copilot":
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
        raise ValueError(f"unsupported coding provider: {provider}")

    def _executable(self, provider: str) -> str | None:
        configured = self.config.codex_path if provider == "codex" else self.config.copilot_path
        if configured and Path(configured).exists():
            return configured
        names = ["codex"] if provider == "codex" else ["copilot", "gh"]
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


def _limit_state(text: str, rate_patterns: list[str], usage_patterns: list[str]) -> dict:
    lowered = text.lower()
    if any(pattern.lower() in lowered for pattern in usage_patterns):
        return {"limited": True, "kind": "usage", "source": "cli_output"}
    if any(pattern.lower() in lowered for pattern in rate_patterns):
        return {"limited": True, "kind": "rate", "source": "cli_output"}
    return {"limited": False, "source": "cli_output"}


def _summary(provider: str, operation: str, returncode: int, stdout: str, stderr: str, limit_state: dict) -> str:
    if limit_state.get("limited"):
        return f"{provider} reported a {limit_state.get('kind')} limit."
    if returncode == 0:
        text = stdout.strip() or stderr.strip()
        return f"{provider} {operation} completed." + (f" {text[:600]}" if text else "")
    return f"{provider} {operation} failed with exit code {returncode}."


def _workspace_snapshot(workspace: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    if not workspace.exists():
        return snapshot
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            snapshot[str(path.resolve().relative_to(workspace))] = (stat.st_size, int(stat.st_mtime_ns))
        except OSError:
            continue
    return snapshot


def _changed_files(before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]) -> list[str]:
    changed = []
    for path, stat in sorted(after.items()):
        if before.get(path) != stat:
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
