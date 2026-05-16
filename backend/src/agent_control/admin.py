from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import Field

from agent_control.config import AppSettings
from agent_control.schemas import AuditEventType, Capability, StrictBaseModel
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories
from agent_control.tools.vscode_bridge import VSCodeBridgeStore, VSCodeTerminalCommand


class AdminTerminalCommandRequest(StrictBaseModel):
    command: str = Field(min_length=1, max_length=4000)
    terminal_id: str = "agent-control"
    instance_id: str | None = None
    cwd: str | None = None


class AdminTaskSignalRequest(StrictBaseModel):
    signal: str = Field(pattern="^(pause|resume|cancel)$")


SettingsLoader = Callable[[], AppSettings]
RepositoriesLoader = Callable[[], Repositories]


def create_admin_router(
    settings_loader: SettingsLoader,
    repositories_loader: RepositoriesLoader,
    vscode_store: VSCodeBridgeStore,
) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    def settings() -> AppSettings:
        loaded = settings_loader()
        if not loaded.server.admin_enabled:
            raise HTTPException(status_code=404, detail="admin UI is disabled")
        return loaded

    def require_admin(request: Request) -> AppSettings:
        loaded = settings()
        expected = os.getenv(loaded.server.admin_token_env)
        provided = request.headers.get("X-Agent-Control-Admin-Token") or request.query_params.get("token")
        if expected and provided != expected:
            raise HTTPException(status_code=401, detail="invalid admin token")
        return loaded

    @router.get("", response_class=HTMLResponse)
    def admin_page(request: Request) -> HTMLResponse:
        require_admin(request)
        return HTMLResponse(_ADMIN_HTML)

    @router.get("/api/summary")
    def admin_summary(request: Request) -> dict[str, Any]:
        loaded = require_admin(request)
        repositories = repositories_loader()
        tasks = repositories.tasks.list_recent(10)
        audit_events = repositories.audit.list_recent(20)
        return {
            "status": "ok",
            "config": loaded.safe_summary(),
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "audit": [event.model_dump(mode="json") for event in audit_events],
            "vscode": _vscode_summary(vscode_store),
            "admin": {
                "enabled": loaded.server.admin_enabled,
                "token_required": bool(os.getenv(loaded.server.admin_token_env)),
            },
        }

    @router.get("/api/tasks")
    def admin_tasks(request: Request, limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
        require_admin(request)
        repositories = repositories_loader()
        tasks = repositories.tasks.list_recent(limit)
        return {"tasks": [task.model_dump(mode="json") for task in tasks]}

    @router.get("/api/audit")
    def admin_audit(
        request: Request,
        task_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        require_admin(request)
        repositories = repositories_loader()
        events = repositories.audit.list_for_task(task_id)[-limit:] if task_id else repositories.audit.list_recent(limit)
        return {"events": [event.model_dump(mode="json") for event in events]}

    @router.get("/api/vscode")
    def admin_vscode(request: Request) -> dict[str, Any]:
        require_admin(request)
        return _vscode_summary(vscode_store)

    @router.post("/api/vscode/terminal-commands")
    def admin_enqueue_vscode_command(
        request: Request,
        payload: AdminTerminalCommandRequest,
    ) -> dict[str, Any]:
        loaded = require_admin(request)
        _ensure_terminal_dispatch_allowed(loaded)

        command = vscode_store.enqueue_terminal_command(
            VSCodeTerminalCommand(
                command=payload.command,
                terminal_id=payload.terminal_id,
                instance_id=payload.instance_id,
                cwd=payload.cwd,
            )
        )
        repositories = repositories_loader()
        AuditLogger(repositories.audit, loaded.logging.redact_patterns).append(
            AuditEventType.TOOL_REQUESTED,
            actor="admin",
            payload={
                "tool": "vscode.terminal_command",
                "command_id": command.id,
                "terminal_id": command.terminal_id,
                "instance_id": command.instance_id,
                "cwd": command.cwd,
                "command_preview": command.command[:160],
            },
        )
        return {"queued": command.model_dump(mode="json")}

    @router.post("/api/tasks/{task_id}/signals")
    def admin_task_signal(
        request: Request,
        task_id: str,
        payload: AdminTaskSignalRequest,
    ) -> dict[str, Any]:
        loaded = require_admin(request)
        repositories = repositories_loader()
        task = repositories.tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")

        from agent_control.schemas import TaskSignal, TaskStatus

        signal = TaskSignal(task_id=task_id, signal=payload.signal, actor="admin", payload=payload.model_dump())
        repositories.task_signals.create(signal)
        target_status = {
            "pause": TaskStatus.PAUSED,
            "resume": TaskStatus.RECEIVED,
            "cancel": TaskStatus.CANCELLED,
        }[payload.signal]
        updated = repositories.tasks.update_status(task_id, target_status)
        AuditLogger(repositories.audit, loaded.logging.redact_patterns).task_state_changed(
            actor="admin",
            task_id=task_id,
            old_status=task.status,
            new_status=updated.status,
        )
        return {"signal": signal.model_dump(mode="json"), "task": updated.model_dump(mode="json")}

    return router


def _ensure_terminal_dispatch_allowed(settings: AppSettings) -> None:
    if not settings.adapters.vscode.enabled:
        raise HTTPException(status_code=403, detail="VS Code adapter is disabled")
    policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if policy is None or not policy.enabled:
        raise HTTPException(status_code=403, detail="terminal.run capability is disabled")
    if policy.requires_approval:
        raise HTTPException(status_code=403, detail="terminal.run requires approval; use the orchestrated approval flow")


def _vscode_summary(store: VSCodeBridgeStore) -> dict[str, Any]:
    return {
        "connected": store.heartbeat is not None or store.state is not None,
        "heartbeat": store.heartbeat.model_dump(mode="json") if store.heartbeat else None,
        "state": store.state.model_dump(mode="json") if store.state else None,
        "pending_terminal_commands": len(store.terminal_commands),
        "terminal_outputs": [output.model_dump(mode="json") for output in store.terminal_outputs[-20:]],
    }


_ADMIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Control Admin</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2328;
      --muted: #656d76;
      --border: #d0d7de;
      --accent: #0969da;
      --danger: #cf222e;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0d1117;
        --panel: #161b22;
        --text: #e6edf3;
        --muted: #8b949e;
        --border: #30363d;
        --accent: #58a6ff;
        --danger: #ff7b72;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header, main { max-width: 1200px; margin: 0 auto; padding: 20px; }
    header { display: flex; gap: 16px; align-items: center; justify-content: space-between; }
    h1 { margin: 0; font-size: 24px; }
    h2 { margin: 0 0 12px; font-size: 16px; }
    button, input {
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--text);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
    }
    button { cursor: pointer; }
    button.primary { border-color: var(--accent); color: var(--accent); }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; }
    .panel {
      grid-column: span 6;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      min-width: 0;
    }
    .wide { grid-column: span 12; }
    .muted { color: var(--muted); }
    .danger { color: var(--danger); }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 8px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
    th { font-size: 12px; color: var(--muted); font-weight: 600; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }
    pre { white-space: pre-wrap; word-break: break-word; max-height: 360px; overflow: auto; }
    .capability {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) auto auto;
      gap: 8px;
      padding: 6px 0;
      border-bottom: 1px solid var(--border);
      align-items: center;
    }
    .badge { border: 1px solid var(--border); border-radius: 999px; padding: 2px 8px; font-size: 12px; }
    .enabled { color: #1a7f37; }
    .disabled { color: var(--muted); }
    @media (max-width: 800px) {
      header { align-items: flex-start; flex-direction: column; }
      .panel { grid-column: span 12; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Agent Control Admin</h1>
      <div id="status" class="muted">Loading</div>
    </div>
    <div class="row">
      <button class="primary" onclick="refresh()">Refresh</button>
    </div>
  </header>
  <main class="grid">
    <section class="panel">
      <h2>Configuration</h2>
      <pre id="config"></pre>
    </section>
    <section class="panel">
      <h2>VS Code Bridge</h2>
      <pre id="vscode"></pre>
      <div class="row">
        <input id="terminal-command" placeholder="VS Code terminal command" style="flex: 1; min-width: 220px;">
        <button onclick="queueCommand()">Queue</button>
      </div>
      <div id="command-result" class="muted"></div>
    </section>
    <section class="panel wide">
      <h2>Capabilities</h2>
      <div id="capabilities"></div>
    </section>
    <section class="panel wide">
      <h2>Tasks</h2>
      <div id="tasks"></div>
    </section>
    <section class="panel wide">
      <h2>Audit</h2>
      <div id="audit"></div>
    </section>
  </main>
  <script>
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    const headers = token ? {"X-Agent-Control-Admin-Token": token} : {};

    function jsonBlock(value) {
      return JSON.stringify(value, null, 2);
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      })[char]);
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: {"Content-Type": "application/json", ...headers, ...(options.headers || {})}
      });
      if (!response.ok) {
        throw new Error(`${response.status} ${await response.text()}`);
      }
      return response.json();
    }

    function renderCapabilities(config) {
      const caps = config.capabilities || {};
      document.getElementById("capabilities").innerHTML = Object.entries(caps).map(([name, policy]) => `
        <div class="capability">
          <code>${escapeHtml(name)}</code>
          <span class="badge ${policy.enabled ? "enabled" : "disabled"}">${policy.enabled ? "enabled" : "disabled"}</span>
          <span class="badge">${policy.requires_approval ? "approval" : "no approval"}</span>
        </div>
      `).join("");
    }

    function renderTasks(tasks) {
      if (!tasks.length) {
        document.getElementById("tasks").innerHTML = "<div class='muted'>No tasks found.</div>";
        return;
      }
      document.getElementById("tasks").innerHTML = `
        <table>
          <thead><tr><th>ID</th><th>Status</th><th>Objective</th><th>Actions</th></tr></thead>
          <tbody>${tasks.map(task => `
            <tr>
              <td><code>${escapeHtml(task.id)}</code></td>
              <td>${escapeHtml(task.status)}</td>
              <td>${escapeHtml(task.objective)}</td>
              <td class="row">
                <button onclick="taskSignal(${JSON.stringify(task.id)}, 'pause')">Pause</button>
                <button onclick="taskSignal(${JSON.stringify(task.id)}, 'resume')">Resume</button>
                <button onclick="taskSignal(${JSON.stringify(task.id)}, 'cancel')">Cancel</button>
              </td>
            </tr>
          `).join("")}</tbody>
        </table>
      `;
    }

    function renderAudit(events) {
      if (!events.length) {
        document.getElementById("audit").innerHTML = "<div class='muted'>No audit events found.</div>";
        return;
      }
      document.getElementById("audit").innerHTML = `
        <table>
          <thead><tr><th>Created</th><th>Type</th><th>Actor</th><th>Task</th></tr></thead>
          <tbody>${events.map(event => `
            <tr>
              <td>${escapeHtml(event.created_at)}</td>
              <td>${escapeHtml(event.type)}</td>
              <td>${escapeHtml(event.actor)}</td>
              <td><code>${escapeHtml(event.task_id || "")}</code></td>
            </tr>
          `).join("")}</tbody>
        </table>
      `;
    }

    async function refresh() {
      const status = document.getElementById("status");
      try {
        const data = await api("/admin/api/summary");
        status.textContent = `OK · ${data.config.identity.instance_name}`;
        document.getElementById("config").textContent = jsonBlock({
          server: data.config.server,
          identity: data.config.identity,
          channels: data.config.channels,
          llm: data.config.llm,
          adapters: data.config.adapters,
          storage: data.config.storage,
          limits: data.config.limits,
          admin: data.admin
        });
        document.getElementById("vscode").textContent = jsonBlock(data.vscode);
        renderCapabilities(data.config);
        renderTasks(data.tasks || []);
        renderAudit(data.audit || []);
      } catch (error) {
        status.innerHTML = `<span class="danger">${error.message}</span>`;
      }
    }

    async function queueCommand() {
      const input = document.getElementById("terminal-command");
      const result = document.getElementById("command-result");
      try {
        const data = await api("/admin/api/vscode/terminal-commands", {
          method: "POST",
          body: JSON.stringify({command: input.value})
        });
        result.textContent = `Queued ${data.queued.id}`;
        input.value = "";
        await refresh();
      } catch (error) {
        result.textContent = error.message;
      }
    }

    async function taskSignal(taskId, signal) {
      await api(`/admin/api/tasks/${taskId}/signals`, {
        method: "POST",
        body: JSON.stringify({signal})
      });
      await refresh();
    }

    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>
"""
