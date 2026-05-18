from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import webbrowser
from typing import Any

from agent_control.config import WorkspaceAdapterConfig
from agent_control.schemas import ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus


class LocalWorkspaceAdapter:
    """General local workspace tool for task files, generated code, and previews."""

    def __init__(self, config: WorkspaceAdapterConfig) -> None:
        self.config = config

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        if not self.config.enabled:
            return _failed(request, "workspace adapter is disabled")

        operation = str(request.input.get("operation") or _default_operation(request.tool_name))
        try:
            if operation == "prepare":
                output = self._prepare(request)
            elif operation == "write_files":
                output = self._write_files(request)
            elif operation == "materialize_static_app":
                output = self._materialize_static_app(request)
            elif operation == "launch_static":
                output = self._launch_static(request)
            elif operation == "web_app_preview":
                output = self._web_app_preview(request)
            else:
                return _failed(request, f"unsupported workspace operation: {operation}")
        except Exception as exc:
            return _failed(request, f"workspace operation failed: {exc}")

        output["operation"] = operation
        output["terminal_output"] = [_terminal_output(operation, output)]
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

    def _prepare(self, request: ToolCallRequest) -> dict[str, Any]:
        workspace_dir = workspace_dir_for_task(self.config.root_dir, request.task_id)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        objective = str(request.input.get("objective") or "").strip()
        task_file = workspace_dir / "TASK.md"
        if not task_file.exists() or bool(request.input.get("refresh_task_file", True)):
            task_file.write_text(_task_markdown(request.task_id, objective), encoding="utf-8")
        return {
            "workspace_dir": str(workspace_dir),
            "files": [str(task_file)],
        }

    def _write_files(self, request: ToolCallRequest) -> dict[str, Any]:
        prepared = self._prepare(request)
        workspace_dir = Path(prepared["workspace_dir"])
        files = []
        for item in request.input.get("files") or []:
            if not isinstance(item, dict):
                continue
            relative_path = str(item.get("path") or "").strip()
            content = str(item.get("content") or "")
            if not relative_path:
                continue
            target = _safe_child_path(workspace_dir, relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            files.append(str(target))
        return {
            "workspace_dir": str(workspace_dir),
            "files": [*prepared.get("files", []), *files],
        }

    def _launch_static(self, request: ToolCallRequest) -> dict[str, Any]:
        prepared = self._prepare(request)
        workspace_dir = Path(prepared["workspace_dir"])
        objective = str(request.input.get("objective") or request.input.get("prompt") or "").strip()
        index_file = workspace_dir / "index.html"
        fallback_files: list[str] = []
        if bool(request.input.get("ensure_index", True)) and not index_file.exists():
            title = _title_from_objective(objective)
            index_file.write_text(_web_app_html(request.task_id, title, objective), encoding="utf-8")
            fallback_files.append(str(index_file))
        port = _free_port(self.config.web_host, int(request.input.get("web_port_start") or self.config.web_port_start))
        url = f"http://{self.config.web_host}:{port}/"
        process = _start_static_server(workspace_dir, self.config.web_host, port)
        if bool(request.input.get("open_browser", self.config.open_browser)):
            webbrowser.open(url)
        return {
            "workspace_dir": str(workspace_dir),
            "url": url,
            "server_pid": process.pid,
            "files": sorted(set([*prepared.get("files", []), *fallback_files])),
        }

    def _materialize_static_app(self, request: ToolCallRequest) -> dict[str, Any]:
        prepared = self._prepare(request)
        workspace_dir = Path(prepared["workspace_dir"])
        overwrite = bool(request.input.get("overwrite", False))
        source_text = str(request.input.get("source_text") or request.input.get("assistant_output") or "").strip()
        objective = str(request.input.get("objective") or request.input.get("prompt") or "").strip()

        parsed_files = _extract_files_from_text(source_text)
        existing_index = workspace_dir / "index.html"
        if existing_index.exists() and not overwrite:
            return {
                "workspace_dir": str(workspace_dir),
                "files": sorted(set([*prepared.get("files", []), str(existing_index)])),
                "materialized_from": "existing_files",
            }

        files = []
        if parsed_files:
            for relative_path, content in parsed_files.items():
                target = _safe_child_path(workspace_dir, relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and not overwrite:
                    files.append(str(target))
                    continue
                target.write_text(content, encoding="utf-8")
                files.append(str(target))
            materialized_from = "assistant_output"
        else:
            title = _title_from_objective(objective)
            target = workspace_dir / "index.html"
            target.write_text(_web_app_html(request.task_id, title, objective), encoding="utf-8")
            files.append(str(target))
            materialized_from = "fallback_template"

        readme = workspace_dir / "README.md"
        if not readme.exists() or overwrite:
            readme.write_text(_web_app_readme(_title_from_objective(objective), request.task_id, objective), encoding="utf-8")
            files.append(str(readme))

        return {
            "workspace_dir": str(workspace_dir),
            "files": sorted(set([*prepared.get("files", []), *files])),
            "materialized_from": materialized_from,
        }

    def _web_app_preview(self, request: ToolCallRequest) -> dict[str, Any]:
        objective = str(request.input.get("objective") or request.input.get("prompt") or "").strip()
        title = _title_from_objective(objective)
        write_output = self._write_files(
            request.model_copy(
                update={
                    "input": {
                        **request.input,
                        "objective": objective,
                        "files": [
                            {"path": "index.html", "content": _web_app_html(request.task_id, title, objective)},
                            {"path": "README.md", "content": _web_app_readme(title, request.task_id, objective)},
                        ],
                    }
                }
            )
        )
        launch_output = self._launch_static(request)
        return {
            **launch_output,
            "workspace_dir": write_output["workspace_dir"],
            "files": sorted(set([*write_output.get("files", []), *launch_output.get("files", [])])),
        }


LocalWorkspaceWebAppAdapter = LocalWorkspaceAdapter


def workspace_dir_for_task(root_dir: str, task_id: str) -> Path:
    root = Path(root_dir).expanduser().resolve()
    workspace = (root / _safe_segment(task_id)).resolve()
    if root != workspace and root not in workspace.parents:
        raise ValueError("workspace path escaped configured root")
    return workspace


def _default_operation(tool_name: str) -> str:
    if tool_name == "workspace.web_app":
        return "web_app_preview"
    return "prepare"


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "task"


def _safe_child_path(workspace_dir: Path, relative_path: str) -> Path:
    target = (workspace_dir / relative_path).resolve()
    if workspace_dir != target and workspace_dir not in target.parents:
        raise ValueError(f"file path escaped workspace: {relative_path}")
    return target


def _extract_files_from_text(source_text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    if not source_text:
        return files

    fence_pattern = re.compile(
        r"```(?P<lang>[A-Za-z0-9_+.-]*)[ \t]*(?P<meta>[^\n`]*)\n(?P<code>.*?)```",
        re.DOTALL,
    )
    for match in fence_pattern.finditer(source_text):
        language = (match.group("lang") or "").strip().lower()
        metadata = match.group("meta") or ""
        code = match.group("code")
        filename = _filename_from_fence(metadata) or _filename_before_fence(source_text[: match.start()])
        if filename is None and language in {"html", "htm"}:
            filename = "index.html"
        elif filename is None and language == "css":
            filename = "styles.css"
        elif filename is None and language in {"js", "javascript"}:
            filename = "script.js"
        if not filename:
            continue
        safe = _safe_relative_file(filename)
        if safe:
            files[safe] = code.rstrip() + "\n"
    return files


def _filename_from_fence(metadata: str) -> str | None:
    match = re.search(r"(?:filename|file|path)\s*=\s*['\"]?([^'\"\s`]+)", metadata, re.IGNORECASE)
    if match:
        return match.group(1)
    stripped = metadata.strip()
    if _looks_like_filename(stripped):
        return stripped
    return None


def _filename_before_fence(prefix: str) -> str | None:
    for line in reversed(prefix.splitlines()[-4:]):
        cleaned = line.strip().strip("`*: ")
        cleaned = re.sub(r"^(?:file|filename|path)\s*[:=-]\s*", "", cleaned, flags=re.IGNORECASE)
        if _looks_like_filename(cleaned):
            return cleaned
    return None


def _looks_like_filename(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.\-/ ]+\.(?:html|css|js|mjs|json|md|txt)", value.strip()))


def _safe_relative_file(value: str) -> str | None:
    normalized = value.replace("\\", "/").strip().lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        return None
    return normalized


def _title_from_objective(objective: str) -> str:
    lowered = objective.lower()
    if "hello" in lowered and "world" in lowered:
        return "Hello World"
    if "web app" in lowered:
        return "Local Web App"
    return "Generated Web App"


def _task_markdown(task_id: str, objective: str) -> str:
    return f"""# Task Workspace

Task: `{task_id}`

Objective:

```text
{objective or "No objective provided."}
```

Generated at: {datetime.now().isoformat(timespec="seconds")}
"""


def _web_app_html(task_id: str, title: str, objective: str) -> str:
    created_at = datetime.now().isoformat(timespec="seconds")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html(title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --text: #1f2328;
      --muted: #57606a;
      --accent: #0a7f5a;
      --border: #d8dee4;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0d1117;
        --panel: #161b22;
        --text: #e6edf3;
        --muted: #8b949e;
        --accent: #3fb950;
        --border: #30363d;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(760px, calc(100vw - 32px));
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      padding: 32px;
    }}
    h1 {{ margin: 0 0 10px; font-size: clamp(32px, 6vw, 56px); line-height: 1.05; }}
    p {{ margin: 0 0 18px; color: var(--muted); font-size: 18px; }}
    dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 8px 14px; margin: 24px 0 0; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <main>
    <h1>{_html(title)}</h1>
    <p>This preview was generated from your Telegram task and is running locally.</p>
    <dl>
      <dt>Task</dt><dd>{_html(task_id)}</dd>
      <dt>Objective</dt><dd>{_html(objective or "Create a local web app preview.")}</dd>
      <dt>Created</dt><dd>{_html(created_at)}</dd>
    </dl>
  </main>
</body>
</html>
"""


def _web_app_readme(title: str, task_id: str, objective: str) -> str:
    return f"""# {title}

Task: `{task_id}`

Objective:

```text
{objective or "Create a local web app preview."}
```

Run locally from this directory:

```powershell
python -m http.server
```
"""


def _html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _free_port(host: str, start: int) -> int:
    for port in range(start, min(start + 200, 65536)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError(f"no free port found from {start}")


def _start_static_server(workspace_dir: Path, host: str, port: int) -> subprocess.Popen:
    log = (workspace_dir / "server.log").open("ab")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", host],
            cwd=str(workspace_dir),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    finally:
        log.close()


def _terminal_output(operation: str, output: dict[str, Any]) -> dict[str, Any]:
    lines = [f"Workspace operation completed: {operation}"]
    if output.get("url"):
        lines.append(f"URL: {output['url']}")
    if output.get("workspace_dir"):
        lines.append(f"Workspace: {output['workspace_dir']}")
    if output.get("server_pid"):
        lines.append(f"Server PID: {output['server_pid']}")
    if output.get("files"):
        lines.append("Files:")
        lines.extend(f"- {path}" for path in output["files"])
    return {
        "instance_id": "local-worker",
        "terminal_id": "workspace",
        "content": "\n".join(lines),
        "command_id": None,
        "is_final": True,
        "exit_code": 0,
        "source": "local_workspace",
    }


def _failed(request: ToolCallRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        request_id=request.id,
        status=ToolResultStatus.FAILED,
        error_class=ErrorClass.ADAPTER_FAILED,
        error_message=message,
    )
