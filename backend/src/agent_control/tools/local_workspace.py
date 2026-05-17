from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import webbrowser

from agent_control.config import WorkspaceAdapterConfig
from agent_control.schemas import ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus


class LocalWorkspaceWebAppAdapter:
    def __init__(self, config: WorkspaceAdapterConfig) -> None:
        self.config = config

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        if not self.config.enabled:
            return _failed(request, "workspace adapter is disabled")

        try:
            workspace_dir = _workspace_dir(self.config.root_dir, request.task_id)
            workspace_dir.mkdir(parents=True, exist_ok=True)

            objective = str(request.input.get("objective") or request.input.get("prompt") or "").strip()
            title = _title_from_objective(objective)
            files = _write_web_app(workspace_dir, request.task_id, title, objective)
            port = _free_port(self.config.web_host, int(request.input.get("web_port_start") or self.config.web_port_start))
            url = f"http://{self.config.web_host}:{port}/"
            process = _start_static_server(workspace_dir, self.config.web_host, port)

            if bool(request.input.get("open_browser", self.config.open_browser)):
                webbrowser.open(url)

            content = (
                "Created and launched a local web app preview.\n"
                f"URL: {url}\n"
                f"Workspace: {workspace_dir}\n"
                f"Server PID: {process.pid}\n"
                "Files:\n"
                + "\n".join(f"- {path}" for path in files)
            )
            return ToolCallResult(
                request_id=request.id,
                status=ToolResultStatus.SUCCEEDED,
                output={
                    "url": url,
                    "workspace_dir": str(workspace_dir),
                    "server_pid": process.pid,
                    "files": [str(path) for path in files],
                    "terminal_output": [
                        {
                            "instance_id": "local-worker",
                            "terminal_id": "workspace-preview",
                            "content": content,
                            "command_id": None,
                            "is_final": True,
                            "exit_code": 0,
                            "source": "local_workspace_web_app",
                        }
                    ],
                },
            )
        except Exception as exc:
            return _failed(request, f"workspace web app launch failed: {exc}")


def _workspace_dir(root_dir: str, task_id: str) -> Path:
    root = Path(root_dir).expanduser().resolve()
    workspace = (root / _safe_segment(task_id)).resolve()
    if root != workspace and root not in workspace.parents:
        raise ValueError("workspace path escaped configured root")
    return workspace


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "task"


def _title_from_objective(objective: str) -> str:
    lowered = objective.lower()
    if "hello" in lowered and "world" in lowered:
        return "Hello World"
    if "web app" in lowered:
        return "Local Web App"
    return "Generated Web App"


def _write_web_app(workspace_dir: Path, task_id: str, title: str, objective: str) -> list[Path]:
    html_path = workspace_dir / "index.html"
    readme_path = workspace_dir / "README.md"
    created_at = datetime.now().isoformat(timespec="seconds")
    html_path.write_text(
        f"""<!doctype html>
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
    .mark {{ color: var(--accent); font-weight: 700; }}
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
""",
        encoding="utf-8",
    )
    readme_path.write_text(
        f"""# {title}

Task: `{task_id}`

Objective:

```text
{objective or "Create a local web app preview."}
```

Run locally from this directory:

```powershell
python -m http.server
```
""",
        encoding="utf-8",
    )
    return [html_path, readme_path]


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


def _failed(request: ToolCallRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        request_id=request.id,
        status=ToolResultStatus.FAILED,
        error_class=ErrorClass.ADAPTER_FAILED,
        error_message=message,
    )
