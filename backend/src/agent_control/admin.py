from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import Field

from agent_control.config_sync import CONFIG_FILE_PATH, ConfigManager, env_bool, env_json
from agent_control.config import AppSettings
from agent_control.llm.providers import OpenAICompatibleProvider
from agent_control.policy import apply_access_modes_to_config, summarize_access_modes
from agent_control.schemas import AuditEventType, Capability, CapabilityAccessMode, StrictBaseModel
from agent_control.storage.audit import AuditLogger
from agent_control.storage.audit_view import format_audit_event
from agent_control.storage.database import Database
from agent_control.storage.repositories import Repositories
from agent_control.tools.vscode_bridge import VSCodeBridgeStore, VSCodeTerminalCommand


class AdminTerminalCommandRequest(StrictBaseModel):
    command: str = Field(min_length=1, max_length=4000)
    terminal_id: str = "agent-control"
    instance_id: str | None = None
    cwd: str | None = None


class AdminTaskSignalRequest(StrictBaseModel):
    signal: str = Field(pattern="^(pause|resume|cancel)$")


class AdminLLMConfigRequest(StrictBaseModel):
    profile_name: str = Field(default="default", min_length=1, max_length=80)
    default_profile: str = Field(default="default", min_length=1, max_length=80)
    provider: str = Field(default="openai_compatible", min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=3600)
    max_tokens: int = Field(default=4096, ge=1, le=262144)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    api_key_value: str | None = None


class AdminTelegramConfigRequest(StrictBaseModel):
    enabled: bool | None = None
    token_env: str | None = Field(default=None, min_length=1, max_length=120)
    allowed_user_ids: list[int] | None = None
    allowed_chat_ids: list[int] | None = None
    polling: bool | None = None
    bot_token: str | None = None


class AdminVSCodeConfigRequest(StrictBaseModel):
    enabled: bool | None = None
    bridge_host: str | None = None
    bridge_port: int | None = Field(default=None, ge=1, le=65535)
    auth_token_env: str | None = None
    bridge_token: str | None = None


class AdminAccessModesRequest(StrictBaseModel):
    modes: dict[str, CapabilityAccessMode]


SettingsLoader = Callable[[], AppSettings]
RepositoriesLoader = Callable[[], Repositories]


def create_admin_router(
    settings_loader: SettingsLoader,
    repositories_loader: RepositoriesLoader,
    vscode_store: VSCodeBridgeStore,
) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])
    config_manager = ConfigManager()

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
            "audit": [format_audit_event(event).model_dump(mode="json") for event in audit_events],
            "vscode": _vscode_summary(vscode_store),
            "access_modes": {
                name: summary.model_dump(mode="json")
                for name, summary in summarize_access_modes(loaded).items()
            },
            "warnings": _config_warnings(loaded),
            "database": _database_summary(loaded),
            "integrations": {
                "telegram": {
                    "enabled": loaded.channels.telegram.enabled,
                    "token_env": loaded.channels.telegram.token_env,
                    "token_present": bool(os.getenv(loaded.channels.telegram.token_env)),
                    "allowed_user_ids": loaded.channels.telegram.allowed_user_ids,
                    "allowed_chat_ids": loaded.channels.telegram.allowed_chat_ids,
                    "allowed_user_count": len(loaded.channels.telegram.allowed_user_ids),
                    "allowed_chat_count": len(loaded.channels.telegram.allowed_chat_ids),
                },
                "llm": {
                    "default_profile": loaded.llm.default_profile,
                    "profile_count": len(loaded.llm.profiles),
                    "default_profile_configured": loaded.llm.default_profile in loaded.llm.profiles,
                },
            },
            "admin": {
                "enabled": loaded.server.admin_enabled,
                "token_required": bool(os.getenv(loaded.server.admin_token_env)),
                "config_file": str(CONFIG_FILE_PATH),
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
        category: str | None = None,
        event_type: AuditEventType | None = None,
        actor: str | None = None,
        q: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        require_admin(request)
        repositories = repositories_loader()
        raw_events = repositories.audit.list_for_task(task_id)[-limit:] if task_id else repositories.audit.list_recent(200)
        formatted = [format_audit_event(event) for event in raw_events]
        if category:
            formatted = [event for event in formatted if event.category == category]
        if event_type:
            formatted = [event for event in formatted if event.type == event_type]
        if actor:
            formatted = [event for event in formatted if actor.lower() in event.actor.lower()]
        if q:
            needle = q.lower()
            formatted = [
                event
                for event in formatted
                if needle in event.summary.lower()
                or needle in event.title.lower()
                or needle in str(event.details).lower()
            ]
        return {"events": [event.model_dump(mode="json") for event in formatted[:limit]]}

    @router.get("/api/config/effective")
    def admin_effective_config(request: Request) -> dict[str, Any]:
        loaded = require_admin(request)
        return {
            "config": loaded.safe_summary(),
            "access_modes": {
                name: summary.model_dump(mode="json")
                for name, summary in summarize_access_modes(loaded).items()
            },
            "warnings": _config_warnings(loaded),
        }

    @router.get("/api/database/summary")
    def admin_database_summary(request: Request) -> dict[str, Any]:
        loaded = require_admin(request)
        return _database_summary(loaded)

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

    @router.post("/api/config/llm")
    def admin_update_llm_config(request: Request, payload: AdminLLMConfigRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        config = _read_config_file(config_manager)
        llm = config.setdefault("llm", {})
        profiles = llm.setdefault("profiles", {})
        llm["default_profile"] = payload.default_profile
        profiles[payload.profile_name] = {
            "provider": payload.provider,
            "model": payload.model,
            "base_url": _blank_to_none(payload.base_url),
            "api_key_env": _blank_to_none(payload.api_key_env),
            "timeout_seconds": payload.timeout_seconds,
            "max_tokens": payload.max_tokens,
            "temperature": payload.temperature,
        }
        _write_config_file(config_manager, config)
        env_updates = {
            "AGENT_LLM__DEFAULT_PROFILE": payload.default_profile,
            f"AGENT_LLM__PROFILES__{payload.profile_name}__PROVIDER": payload.provider,
            f"AGENT_LLM__PROFILES__{payload.profile_name}__MODEL": payload.model,
            f"AGENT_LLM__PROFILES__{payload.profile_name}__BASE_URL": payload.base_url or "",
            f"AGENT_LLM__PROFILES__{payload.profile_name}__API_KEY_ENV": payload.api_key_env or "",
            f"AGENT_LLM__PROFILES__{payload.profile_name}__TIMEOUT_SECONDS": str(payload.timeout_seconds),
            f"AGENT_LLM__PROFILES__{payload.profile_name}__MAX_TOKENS": str(payload.max_tokens),
            f"AGENT_LLM__PROFILES__{payload.profile_name}__TEMPERATURE": str(payload.temperature),
        }
        if payload.api_key_env and payload.api_key_value:
            env_updates[payload.api_key_env] = payload.api_key_value
        config_manager.upsert_env(env_updates)
        _audit_config_update(repositories_loader(), loaded, "llm", payload.model_dump(mode="json"))
        return {"config_file": str(CONFIG_FILE_PATH), "llm": llm}

    @router.post("/api/config/telegram")
    def admin_update_telegram_config(request: Request, payload: AdminTelegramConfigRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        config = _read_config_file(config_manager)
        telegram = config.setdefault("channels", {}).setdefault("telegram", {})
        patch = payload.model_dump(exclude_unset=True)
        patch.pop("bot_token", None)
        for key, value in patch.items():
            if value is not None:
                telegram[key] = value
        _write_config_file(config_manager, config)
        env_updates: dict[str, str | None] = {}
        if payload.enabled is not None:
            env_updates["AGENT_CHANNELS__TELEGRAM__ENABLED"] = env_bool(payload.enabled)
        if payload.token_env:
            env_updates["AGENT_CHANNELS__TELEGRAM__TOKEN_ENV"] = payload.token_env
        if payload.allowed_user_ids is not None:
            env_updates["AGENT_CHANNELS__TELEGRAM__ALLOWED_USER_IDS"] = env_json(payload.allowed_user_ids)
        if payload.allowed_chat_ids is not None:
            env_updates["AGENT_CHANNELS__TELEGRAM__ALLOWED_CHAT_IDS"] = env_json(payload.allowed_chat_ids)
        if payload.polling is not None:
            env_updates["AGENT_CHANNELS__TELEGRAM__POLLING"] = env_bool(payload.polling)
        if payload.token_env and payload.bot_token:
            env_updates[payload.token_env] = payload.bot_token
        if env_updates:
            config_manager.upsert_env(env_updates)
        _audit_config_update(repositories_loader(), loaded, "telegram", patch)
        return {"config_file": str(CONFIG_FILE_PATH), "telegram": telegram}

    @router.post("/api/config/vscode")
    def admin_update_vscode_config(request: Request, payload: AdminVSCodeConfigRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        config = _read_config_file(config_manager)
        vscode = config.setdefault("adapters", {}).setdefault("vscode", {})
        patch = payload.model_dump(exclude_unset=True)
        patch.pop("bridge_token", None)
        for key, value in patch.items():
            if value is not None:
                vscode[key] = value
        _write_config_file(config_manager, config)
        env_updates: dict[str, str | None] = {}
        if payload.enabled is not None:
            env_updates["AGENT_ADAPTERS__VSCODE__ENABLED"] = env_bool(payload.enabled)
        if payload.bridge_host:
            env_updates["AGENT_ADAPTERS__VSCODE__BRIDGE_HOST"] = payload.bridge_host
        if payload.bridge_port is not None:
            env_updates["AGENT_ADAPTERS__VSCODE__BRIDGE_PORT"] = str(payload.bridge_port)
        if payload.auth_token_env:
            env_updates["AGENT_ADAPTERS__VSCODE__AUTH_TOKEN_ENV"] = payload.auth_token_env
        if payload.auth_token_env and payload.bridge_token:
            env_updates[payload.auth_token_env] = payload.bridge_token
        if env_updates:
            config_manager.upsert_env(env_updates)
        _audit_config_update(repositories_loader(), loaded, "vscode", patch)
        return {"config_file": str(CONFIG_FILE_PATH), "vscode": vscode}

    @router.post("/api/config/access-modes")
    def admin_update_access_modes(request: Request, payload: AdminAccessModesRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        config = _read_config_file(config_manager)
        apply_access_modes_to_config(config, payload.modes)
        _write_config_file(config_manager, config)
        os.environ.pop("AGENT_CAPABILITIES", None)
        _audit_config_update(
            repositories_loader(),
            loaded,
            "access_modes",
            {"modes": {name: mode.value for name, mode in payload.modes.items()}},
        )
        refreshed = settings_loader()
        return {
            "config_file": str(CONFIG_FILE_PATH),
            "access_modes": {
                name: summary.model_dump(mode="json")
                for name, summary in summarize_access_modes(refreshed).items()
            },
        }

    @router.post("/api/llm/test")
    async def admin_test_llm(request: Request) -> dict[str, Any]:
        loaded = require_admin(request)
        profile = loaded.llm.profiles.get(loaded.llm.default_profile)
        if profile is None:
            raise HTTPException(status_code=400, detail="default LLM profile is not configured")
        if profile.provider != "openai_compatible":
            raise HTTPException(status_code=400, detail=f"unsupported LLM provider: {profile.provider}")
        try:
            provider = OpenAICompatibleProvider(profile)
            output = await provider.generate_text(
                "You are a health check endpoint. Return a short plain text response.",
                "Reply with: ok",
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"profile": loaded.llm.default_profile, "output_preview": output[:500]}

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


def _read_config_file(config_manager: ConfigManager) -> dict[str, Any]:
    try:
        return config_manager.read_config()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _write_config_file(config_manager: ConfigManager, config: dict[str, Any]) -> None:
    config_manager.write_config(config)


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _audit_config_update(
    repositories: Repositories,
    settings: AppSettings,
    section: str,
    payload: dict[str, Any],
) -> None:
    AuditLogger(repositories.audit, settings.logging.redact_patterns).append(
        AuditEventType.CONFIG_UPDATED,
        actor="admin",
        payload={"section": section, "config_file": str(CONFIG_FILE_PATH), "patch": payload},
    )


def _config_warnings(settings: AppSettings) -> list[str]:
    warnings: list[str] = []
    telegram = settings.channels.telegram
    if telegram.enabled and not telegram.allowed_user_ids and not telegram.allowed_chat_ids:
        warnings.append("Telegram is enabled but no allowed user IDs or chat IDs are configured; all messages will be denied.")
    if settings.llm.default_profile not in settings.llm.profiles:
        warnings.append("Default orchestrator LLM profile is not configured; Telegram task classification will fail.")
    if os.getenv("AGENT_CAPABILITIES"):
        warnings.append("AGENT_CAPABILITIES is set in the environment and may override access-mode changes saved to YAML.")
    return warnings


def _database_summary(settings: AppSettings) -> dict[str, Any]:
    database = Database(settings.storage.database_url)
    database.initialize()
    tables = [
        "conversations",
        "messages",
        "tasks",
        "plans",
        "approvals",
        "tool_invocations",
        "artifacts",
        "audit_events",
    ]
    counts: dict[str, int] = {}
    with database.connect() as connection:
        for table in tables:
            counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        last_task = connection.execute("SELECT updated_at FROM tasks ORDER BY updated_at DESC LIMIT 1").fetchone()
        last_audit = connection.execute("SELECT created_at FROM audit_events ORDER BY created_at DESC LIMIT 1").fetchone()
    return {
        "database_url": settings.storage.database_url,
        "path": database.path,
        "table_counts": counts,
        "last_task_at": last_task[0] if last_task else None,
        "last_audit_at": last_audit[0] if last_audit else None,
        "recommended_vscode_extension": "qwtel.sqlite-viewer",
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
    button, input, select {
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
    .field { display: grid; gap: 4px; margin-bottom: 10px; }
    .stack { display: grid; gap: 8px; }
    .warning { color: #9a6700; }
    label { font-size: 12px; color: var(--muted); }
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
      <div id="warnings" class="warning"></div>
      <pre id="config"></pre>
    </section>
    <section class="panel">
      <h2>VS Code Bridge</h2>
      <pre id="vscode"></pre>
      <div class="field"><label>Enabled</label><input id="vscode-enabled" type="checkbox"></div>
      <div class="field"><label>Bridge Host</label><input id="vscode-host" placeholder="127.0.0.1"></div>
      <div class="field"><label>Bridge Port</label><input id="vscode-port" placeholder="8766"></div>
      <div class="field"><label>Token Env</label><input id="vscode-token-env" placeholder="VSCODE_BRIDGE_TOKEN"></div>
      <div class="field"><label>Replace Token</label><input id="vscode-token-value" type="password" placeholder="leave blank to keep current value"></div>
      <div class="row"><button onclick="saveVSCode()">Save VS Code</button></div>
      <div class="row">
        <input id="terminal-command" placeholder="VS Code terminal command" style="flex: 1; min-width: 220px;">
        <button onclick="queueCommand()">Queue</button>
      </div>
      <div id="command-result" class="muted"></div>
    </section>
    <section class="panel">
      <h2>Orchestrator LLM</h2>
      <div class="field"><label>Profile</label><input id="llm-profile" value="default"></div>
      <div class="field"><label>Default Profile</label><input id="llm-default-profile" value="default"></div>
      <div class="field"><label>Provider</label><input id="llm-provider" value="openai_compatible"></div>
      <div class="field"><label>Model</label><input id="llm-model" placeholder="model name"></div>
      <div class="field"><label>Base URL</label><input id="llm-base-url" placeholder="http://127.0.0.1:1234/v1"></div>
      <div class="field"><label>API Key Env</label><input id="llm-api-key-env" placeholder="OPENAI_API_KEY"></div>
      <div class="field"><label>Replace API Key</label><input id="llm-api-key-value" type="password" placeholder="leave blank to keep current value"></div>
      <div class="row">
        <button onclick="saveLLM()">Save</button>
        <button onclick="testLLM()">Test</button>
      </div>
      <div id="llm-result" class="muted"></div>
    </section>
    <section class="panel">
      <h2>Telegram</h2>
      <div class="field"><label>Enabled</label><input id="telegram-enabled" type="checkbox"></div>
      <div class="field"><label>Token Env</label><input id="telegram-token-env" value="TELEGRAM_BOT_TOKEN"></div>
      <div class="field"><label>Replace Bot Token</label><input id="telegram-token-value" type="password" placeholder="leave blank to keep current value"></div>
      <div class="field"><label>User IDs</label><input id="telegram-user-ids" placeholder="123,456"></div>
      <div class="field"><label>Chat IDs</label><input id="telegram-chat-ids" placeholder="123,456"></div>
      <div class="field"><label>Polling</label><input id="telegram-polling" type="checkbox" checked></div>
      <div class="row"><button onclick="saveTelegram()">Save</button></div>
      <div id="telegram-result" class="muted"></div>
    </section>
    <section class="panel wide">
      <h2>Capability Access</h2>
      <div id="access-modes"></div>
      <div class="row"><button onclick="saveAccessModes()">Save Access Modes</button></div>
    </section>
    <section class="panel wide">
      <h2>Database</h2>
      <pre id="database"></pre>
    </section>
    <section class="panel wide">
      <h2>Tasks</h2>
      <div id="tasks"></div>
    </section>
    <section class="panel wide">
      <h2>Audit</h2>
      <div class="row">
        <select id="audit-category">
          <option value="">All categories</option>
          <option value="raw_telegram">Raw Telegram</option>
          <option value="telegram_access">Telegram Access</option>
          <option value="classification">Classifications</option>
          <option value="spawned_task">Spawned Tasks</option>
          <option value="failed_classification">Failed Spawns</option>
          <option value="policy">Policy Decisions</option>
          <option value="config">Config Changes</option>
          <option value="tool">Tool Events</option>
        </select>
        <input id="audit-query" placeholder="search audit">
        <button onclick="loadAudit()">Filter</button>
      </div>
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

    function renderAccessModes(accessModes) {
      const modes = accessModes || {};
      document.getElementById("access-modes").innerHTML = Object.entries(modes).map(([name, item]) => `
        <div class="capability">
          <code>${escapeHtml(name)}</code>
          <select data-access-mode="${escapeHtml(name)}">
            ${["off", "read_only", "write_access", "full_access"].map(mode => `
              <option value="${mode}" ${item.mode === mode ? "selected" : ""}>${mode.replace("_", " ")}</option>
            `).join("")}
          </select>
          <span class="badge">${escapeHtml((item.capabilities || []).join(", "))}</span>
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
          <thead><tr><th>Time</th><th>Category</th><th>Summary</th><th>Decision</th><th>Reason</th><th>Task</th></tr></thead>
          <tbody>${events.map(event => `
            <tr>
              <td>${escapeHtml(event.formatted_time || event.created_at)}</td>
              <td><span class="badge">${escapeHtml(event.category || event.type)}</span><br>${escapeHtml(event.actor)}</td>
              <td>${escapeHtml(event.summary || event.title)}</td>
              <td>${escapeHtml(event.decision || "")}</td>
              <td>${escapeHtml(event.reason || "")}</td>
              <td><code>${escapeHtml(event.task_id || "")}</code></td>
            </tr>
            <tr>
              <td></td>
              <td colspan="5"><pre>${escapeHtml(jsonBlock(event.details || {}))}</pre></td>
            </tr>
          `).join("")}</tbody>
        </table>
      `;
    }

    async function loadAudit() {
      const params = new URLSearchParams();
      const category = document.getElementById("audit-category").value;
      const q = document.getElementById("audit-query").value;
      if (category) params.set("category", category);
      if (q) params.set("q", q);
      const data = await api(`/admin/api/audit?${params.toString()}`);
      renderAudit(data.events || []);
    }

    function populateConfigForms(config) {
      const llm = config.llm || {};
      const profileName = llm.default_profile || "default";
      const profile = (llm.profiles || {})[profileName] || {};
      document.getElementById("llm-profile").value = profileName;
      document.getElementById("llm-default-profile").value = profileName;
      document.getElementById("llm-provider").value = profile.provider || "openai_compatible";
      document.getElementById("llm-model").value = profile.model || "";
      document.getElementById("llm-base-url").value = profile.base_url || "";
      document.getElementById("llm-api-key-env").value = profile.api_key_env || "";
      document.getElementById("llm-api-key-value").value = "";

      const telegram = (config.channels || {}).telegram || {};
      document.getElementById("telegram-enabled").checked = Boolean(telegram.enabled);
      document.getElementById("telegram-token-env").value = telegram.token_env || "TELEGRAM_BOT_TOKEN";
      document.getElementById("telegram-token-value").value = "";
      document.getElementById("telegram-user-ids").value = (telegram.allowed_user_ids || []).join(",");
      document.getElementById("telegram-chat-ids").value = (telegram.allowed_chat_ids || []).join(",");
      document.getElementById("telegram-polling").checked = telegram.polling !== false;

      const vscode = ((config.adapters || {}).vscode) || {};
      document.getElementById("vscode-enabled").checked = Boolean(vscode.enabled);
      document.getElementById("vscode-host").value = vscode.bridge_host || "127.0.0.1";
      document.getElementById("vscode-port").value = vscode.bridge_port || 8766;
      document.getElementById("vscode-token-env").value = vscode.auth_token_env || "VSCODE_BRIDGE_TOKEN";
      document.getElementById("vscode-token-value").value = "";
    }

    function parseIds(value) {
      return value.split(",")
        .map(item => item.trim())
        .filter(Boolean)
        .map(item => Number(item))
        .filter(item => Number.isInteger(item));
    }

    async function refresh() {
      const status = document.getElementById("status");
      try {
        const data = await api("/admin/api/summary");
        status.textContent = `OK | ${data.config.identity.instance_name}`;
        document.getElementById("warnings").innerHTML = (data.warnings || []).map(escapeHtml).join("<br>");
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
        document.getElementById("database").textContent = jsonBlock(data.database || {});
        populateConfigForms(data.config);
        renderAccessModes(data.access_modes || {});
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

    async function saveLLM() {
      const result = document.getElementById("llm-result");
      try {
        await api("/admin/api/config/llm", {
          method: "POST",
          body: JSON.stringify({
            profile_name: document.getElementById("llm-profile").value,
            default_profile: document.getElementById("llm-default-profile").value,
            provider: document.getElementById("llm-provider").value,
            model: document.getElementById("llm-model").value,
            base_url: document.getElementById("llm-base-url").value,
            api_key_env: document.getElementById("llm-api-key-env").value,
            api_key_value: document.getElementById("llm-api-key-value").value || null
          })
        });
        result.textContent = "Saved. Restart long-running processes to reload config.";
        await refresh();
      } catch (error) {
        result.textContent = error.message;
      }
    }

    async function testLLM() {
      const result = document.getElementById("llm-result");
      result.textContent = "Testing";
      try {
        const data = await api("/admin/api/llm/test", {method: "POST", body: "{}"});
        result.textContent = data.output_preview;
      } catch (error) {
        result.textContent = error.message;
      }
    }

    async function saveTelegram() {
      const result = document.getElementById("telegram-result");
      const payload = {
        enabled: document.getElementById("telegram-enabled").checked,
        token_env: document.getElementById("telegram-token-env").value,
        polling: document.getElementById("telegram-polling").checked,
        bot_token: document.getElementById("telegram-token-value").value || null
      };
      payload.allowed_user_ids = parseIds(document.getElementById("telegram-user-ids").value);
      payload.allowed_chat_ids = parseIds(document.getElementById("telegram-chat-ids").value);
      try {
        await api("/admin/api/config/telegram", {method: "POST", body: JSON.stringify(payload)});
        result.textContent = "Saved. Restart Telegram polling to reload config.";
        await refresh();
      } catch (error) {
        result.textContent = error.message;
      }
    }

    async function saveVSCode() {
      const result = document.getElementById("command-result");
      try {
        await api("/admin/api/config/vscode", {
          method: "POST",
          body: JSON.stringify({
            enabled: document.getElementById("vscode-enabled").checked,
            bridge_host: document.getElementById("vscode-host").value,
            bridge_port: Number(document.getElementById("vscode-port").value),
            auth_token_env: document.getElementById("vscode-token-env").value,
            bridge_token: document.getElementById("vscode-token-value").value || null
          })
        });
        result.textContent = "VS Code config saved. Restart the extension if needed.";
        await refresh();
      } catch (error) {
        result.textContent = error.message;
      }
    }

    async function saveAccessModes() {
      const modes = {};
      document.querySelectorAll("[data-access-mode]").forEach(select => {
        modes[select.getAttribute("data-access-mode")] = select.value;
      });
      await api("/admin/api/config/access-modes", {
        method: "POST",
        body: JSON.stringify({modes})
      });
      await refresh();
    }

    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>
"""
