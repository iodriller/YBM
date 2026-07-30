from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import os
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import Field

from agent_control.config_sync import CONFIG_FILE_PATH, ConfigManager, read_env_value
from agent_control.config import AppSettings, backend_base_url, is_loopback_host
from agent_control.llm.providers import OpenAICompatibleProvider
from agent_control.orchestration.signals import apply_task_signal
from agent_control.policy import apply_access_modes_to_config, summarize_access_modes
from agent_control.prompts import render_prompt
from agent_control.runtime_status import service_summary
from agent_control.schemas import ApprovalStatus, AuditEventType, Capability, CapabilityAccessMode, StrictBaseModel
from agent_control.storage.audit import AuditLogger
from agent_control.storage.audit_view import format_audit_event
from agent_control.storage.database import Database
from agent_control.storage.repositories import Repositories
from agent_control.storage.secrets import SecretVault, SecretVaultError
from agent_control.tools.registry import build_tool_registry
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


class AdminLLMPresetRequest(StrictBaseModel):
    preset: str = Field(min_length=1, max_length=80)


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


class AdminWorkspaceConfigRequest(StrictBaseModel):
    enabled: bool | None = None
    root_dir: str | None = None
    web_host: str | None = None
    web_port_start: int | None = Field(default=None, ge=1, le=65535)
    open_browser: bool | None = None


class AdminComputerUseConfigRequest(StrictBaseModel):
    enabled: bool | None = None
    max_steps: int | None = Field(default=None, ge=1, le=50)
    step_delay_seconds: float | None = Field(default=None, ge=0.0, le=10.0)
    screenshot_dir: str | None = None
    allowed_apps: list[str] | None = None
    allowed_roots: list[str] | None = None
    require_session_approval: bool | None = None
    max_ui_elements: int | None = Field(default=None, ge=0, le=500)


class AdminAccessModesRequest(StrictBaseModel):
    modes: dict[str, CapabilityAccessMode]


class AdminApprovalDecisionRequest(StrictBaseModel):
    decision: str = Field(pattern="^(approve|reject)$")


class AdminSecretSetRequest(StrictBaseModel):
    service: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=8000)


SettingsLoader = Callable[[], AppSettings]
RepositoriesLoader = Callable[[], Repositories]


def _origin_is_trusted(request: Request) -> bool:
    """Reject cross-origin browser requests to the admin API.

    There is no CORSMiddleware on this app, so Starlette sends no
    Access-Control-Allow-Origin header - that stops a malicious page's JS
    from *reading* a cross-origin response, but does not stop the browser
    from *sending* a state-changing request in the first place (a plain
    <form enctype="text/plain"> POST needs no preflight and lands here
    regardless). Without a token configured, require_admin's only other
    check is the server's own bind host, not the caller's origin - so on
    the common local, token-less, loopback-only setup, any website the
    admin's browser visits could otherwise trigger mutations on
    127.0.0.1. A same-origin check closes that regardless of token state.
    request.headers.get("origin") is absent for non-browser clients (curl,
    ybm CLI, direct same-origin navigation in older browsers) - nothing to
    compare against, so those are allowed through unchanged.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    host_header = request.headers.get("host", "")
    return urlparse(origin).netloc == host_header


LLM_PRESETS: dict[str, dict[str, Any]] = {
    "localdeploy_qwen3vl_8b": {
        "label": "LocalDeploy Qwen3-VL 8B (recommended)",
        "profile_name": "localdeploy_qwen3vl_8b",
        "provider": "openai_compatible",
        "model": "qwen3vl_8b_ollama",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_env": None,
        "timeout_seconds": 800,
        "max_tokens": 4096,
        "temperature": 0.1,
        "context_limit": 32768,
    },
    "localdeploy_gemma3_12b": {
        "label": "LocalDeploy Gemma 3 12B",
        "profile_name": "localdeploy_gemma3_12b",
        "provider": "openai_compatible",
        "model": "gemma3_12b_ollama_safe",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_env": None,
        "timeout_seconds": 360,
        "max_tokens": 9000,
        "temperature": 0.2,
        "context_limit": 32768,
    },
    "localdeploy_gemma3_4b": {
        "label": "LocalDeploy Gemma 3 4B",
        "profile_name": "localdeploy_gemma3_4b",
        "provider": "openai_compatible",
        "model": "gemma3_4b_ollama_safe",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_env": None,
        "timeout_seconds": 180,
        "max_tokens": 1024,
        "temperature": 0.2,
        "context_limit": 32768,
    },
    "openai_gpt41": {
        "label": "OpenAI GPT-4.1",
        "profile_name": "openai_saved",
        "provider": "openai_compatible",
        "model": "gpt-4.1",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "timeout_seconds": 60,
        "max_tokens": 4096,
        "temperature": 0.2,
    },
}


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
        if not _origin_is_trusted(request):
            raise HTTPException(
                status_code=403,
                detail="admin API refused: cross-origin request (Origin header does not match Host)",
            )
        expected = read_env_value(loaded.server.admin_token_env)
        provided = request.headers.get("X-Agent-Control-Admin-Token") or request.query_params.get("token")
        if not expected:
            if not is_loopback_host(loaded.server.host):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "admin API refused: server.host is not loopback-only and no admin token "
                        f"is configured. Set {loaded.server.admin_token_env} before binding beyond 127.0.0.1."
                    ),
                )
            return loaded
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid admin token")
        return loaded

    @router.get("", response_class=HTMLResponse)
    def admin_page(request: Request) -> HTMLResponse:
        require_admin(request)
        return HTMLResponse(_ADMIN_HTML)

    @router.get("/api/summary")
    def admin_summary(request: Request, task_limit: int = Query(default=5, ge=1, le=100)) -> dict[str, Any]:
        loaded = require_admin(request)
        repositories = repositories_loader()
        tasks = repositories.tasks.list_recent(task_limit)
        task_total = repositories.tasks.count()
        audit_events = repositories.audit.list_recent(20)
        schedules = repositories.schedules.list_recent(10)
        audit_logger = AuditLogger(repositories.audit, loaded.logging.redact_patterns)
        return {
            "status": "ok",
            "config": loaded.safe_summary(),
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "task_pagination": {
                "limit": task_limit,
                "offset": 0,
                "total": task_total,
                "has_more": task_total > task_limit,
            },
            "audit": [format_audit_event(event).model_dump(mode="json") for event in audit_events],
            "vscode": _vscode_summary(vscode_store),
            "access_modes": {
                name: summary.model_dump(mode="json")
                for name, summary in summarize_access_modes(loaded).items()
            },
            "warnings": _config_warnings(loaded),
            "database": _database_summary(loaded),
            "services": service_summary(loaded),
            "schedules": {
                "total": repositories.schedules.count(),
                "items": [schedule.model_dump(mode="json") for schedule in schedules],
            },
            "tool_registry": _tool_registry_summary(loaded, repositories, audit_logger),
            "integrations": {
                "telegram": {
                    "enabled": loaded.channels.telegram.enabled,
                    "token_env": loaded.channels.telegram.token_env,
                    "token_present": bool(read_env_value(loaded.channels.telegram.token_env)),
                    "allowed_user_ids": loaded.channels.telegram.allowed_user_ids,
                    "allowed_chat_ids": loaded.channels.telegram.allowed_chat_ids,
                    "allowed_user_count": len(loaded.channels.telegram.allowed_user_ids),
                    "allowed_chat_count": len(loaded.channels.telegram.allowed_chat_ids),
                },
                "llm": {
                    "default_profile": loaded.llm.default_profile,
                    "profile_count": len(loaded.llm.profiles),
                    "default_profile_configured": loaded.llm.default_profile in loaded.llm.profiles,
                    "presets": [
                        {**preset, "key": key, "active": loaded.llm.default_profile == preset["profile_name"]}
                        for key, preset in LLM_PRESETS.items()
                    ],
                },
            },
            "admin": {
                "enabled": loaded.server.admin_enabled,
                "token_required": bool(read_env_value(loaded.server.admin_token_env)),
                "config_file": str(CONFIG_FILE_PATH),
            },
        }

    @router.get("/api/tasks")
    def admin_tasks(
        request: Request,
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        require_admin(request)
        repositories = repositories_loader()
        total = repositories.tasks.count()
        tasks = repositories.tasks.list_recent(limit, offset=offset)
        return {
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "has_more": offset + len(tasks) < total,
            },
        }

    @router.get("/api/tasks/{task_id}/trace")
    def admin_task_trace(request: Request, task_id: str) -> dict[str, Any]:
        require_admin(request)
        repositories = repositories_loader()
        trace = build_task_trace(repositories, task_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="task not found")
        return trace

    @router.get("/api/approvals")
    def admin_pending_approvals(request: Request) -> dict[str, Any]:
        require_admin(request)
        repositories = repositories_loader()
        pending = repositories.approvals.list_pending()
        items = []
        for approval in pending:
            task = repositories.tasks.get(approval.task_id)
            items.append(
                {
                    "approval": approval.model_dump(mode="json"),
                    "task_objective": task.objective if task is not None else None,
                    "task_status": task.status.value if task is not None else None,
                }
            )
        return {"approvals": items}

    @router.post("/api/approvals/{approval_id}/decide")
    def admin_decide_approval(request: Request, approval_id: str, payload: AdminApprovalDecisionRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        repositories = repositories_loader()
        approval = repositories.approvals.get(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        if approval.status != ApprovalStatus.PENDING:
            raise HTTPException(status_code=409, detail=f"approval is already {approval.status.value}, not pending")

        new_status = ApprovalStatus.APPROVED if payload.decision == "approve" else ApprovalStatus.REJECTED
        repositories.approvals.set_status(approval_id, new_status)
        AuditLogger(repositories.audit, loaded.logging.redact_patterns).append(
            AuditEventType.APPROVAL_DECIDED,
            actor="admin",
            task_id=approval.task_id,
            payload={"approval_id": approval_id, "decision": payload.decision, "source": "admin_ui"},
        )
        updated = repositories.approvals.get(approval_id)
        return {"approval": updated.model_dump(mode="json") if updated else None}

    @router.delete("/api/tasks")
    def admin_clear_tasks(
        request: Request,
        include_active: bool = Query(default=False),
    ) -> dict[str, Any]:
        loaded = require_admin(request)
        repositories = repositories_loader()
        deleted = repositories.tasks.clear_history(include_active=include_active)
        AuditLogger(repositories.audit, loaded.logging.redact_patterns).append(
            AuditEventType.CONFIG_UPDATED,
            actor="admin",
            payload={
                "section": "task_history",
                "deleted_tasks": deleted,
                "include_active": include_active,
            },
        )
        return {"deleted_tasks": deleted, "include_active": include_active}

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

    @router.delete("/api/audit")
    def admin_clear_audit(request: Request) -> dict[str, Any]:
        require_admin(request)
        repositories = repositories_loader()
        deleted = repositories.audit.clear_all()
        return {"deleted_audit_events": deleted}

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

        audit = AuditLogger(repositories.audit, loaded.logging.redact_patterns)
        try:
            signal, _, _ = apply_task_signal(
                repositories,
                audit,
                task_id,
                payload.signal,
                "admin",
                payload.model_dump(mode="json"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        updated = repositories.tasks.get(task_id)
        if updated is None:
            raise HTTPException(status_code=404, detail="task not found")
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
        config_manager.remove_env_keys(["AGENT_LLM__DEFAULT_PROFILE", *_llm_profile_env_keys(payload.profile_name)])
        env_updates: dict[str, str | None] = {}
        if payload.api_key_env and payload.api_key_value:
            env_updates[payload.api_key_env] = payload.api_key_value
        if env_updates:
            config_manager.upsert_env(env_updates)
        _audit_config_update(repositories_loader(), loaded, "llm", payload.model_dump(mode="json"))
        return {"config_file": str(CONFIG_FILE_PATH), "llm": llm}

    @router.post("/api/config/llm/preset")
    def admin_select_llm_preset(request: Request, payload: AdminLLMPresetRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        preset = LLM_PRESETS.get(payload.preset)
        if preset is None:
            raise HTTPException(status_code=400, detail=f"unknown LLM preset: {payload.preset}")

        config = _read_config_file(config_manager)
        llm = config.setdefault("llm", {})
        profiles = llm.setdefault("profiles", {})
        profile_name = preset["profile_name"]
        llm["default_profile"] = profile_name
        profiles[profile_name] = {
            "provider": preset["provider"],
            "model": preset["model"],
            "base_url": preset["base_url"],
            "api_key_env": preset["api_key_env"],
            "timeout_seconds": preset["timeout_seconds"],
            "max_tokens": preset["max_tokens"],
            "temperature": preset["temperature"],
        }
        if preset.get("context_limit") is not None:
            profiles[profile_name]["context_limit"] = preset["context_limit"]
        _write_config_file(config_manager, config)
        config_manager.remove_env_keys(["AGENT_LLM__DEFAULT_PROFILE", *_legacy_default_llm_env_keys(), *_llm_profile_env_keys(profile_name)])
        _audit_config_update(repositories_loader(), loaded, "llm_preset", {"preset": payload.preset, "profile": profile_name})
        return {"config_file": str(CONFIG_FILE_PATH), "preset": payload.preset, "llm": llm}

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
        if payload.token_env and payload.bot_token:
            env_updates[payload.token_env] = payload.bot_token
        config_manager.remove_env_keys(_telegram_config_env_keys())
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
        if payload.auth_token_env and payload.bridge_token:
            env_updates[payload.auth_token_env] = payload.bridge_token
        config_manager.remove_env_keys(_vscode_config_env_keys())
        if env_updates:
            config_manager.upsert_env(env_updates)
        _audit_config_update(repositories_loader(), loaded, "vscode", patch)
        return {"config_file": str(CONFIG_FILE_PATH), "vscode": vscode}

    @router.post("/api/config/workspace")
    def admin_update_workspace_config(request: Request, payload: AdminWorkspaceConfigRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        config = _read_config_file(config_manager)
        workspace = config.setdefault("adapters", {}).setdefault("workspace", {})
        patch = payload.model_dump(exclude_unset=True)
        for key, value in patch.items():
            if value is not None:
                workspace[key] = value
        _write_config_file(config_manager, config)
        config_manager.remove_env_keys(_workspace_config_env_keys())
        _audit_config_update(repositories_loader(), loaded, "workspace", patch)
        return {"config_file": str(CONFIG_FILE_PATH), "workspace": workspace}

    @router.post("/api/config/computer-use")
    def admin_update_computer_use_config(request: Request, payload: AdminComputerUseConfigRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        config = _read_config_file(config_manager)
        computer_use = config.setdefault("adapters", {}).setdefault("computer_use", {})
        patch = payload.model_dump(exclude_unset=True)
        for key, value in patch.items():
            if value is not None:
                computer_use[key] = value
        _write_config_file(config_manager, config)
        config_manager.remove_env_keys(_computer_use_config_env_keys())
        _audit_config_update(repositories_loader(), loaded, "computer_use", patch)
        return {"config_file": str(CONFIG_FILE_PATH), "computer_use": computer_use}

    @router.post("/api/config/access-modes")
    def admin_update_access_modes(request: Request, payload: AdminAccessModesRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        config = _read_config_file(config_manager)
        apply_access_modes_to_config(config, payload.modes)
        _write_config_file(config_manager, config)
        os.environ.pop("AGENT_CAPABILITIES", None)
        config_manager.remove_env_keys(["AGENT_CAPABILITIES"])
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

    @router.get("/api/secrets")
    def admin_list_secrets(request: Request) -> dict[str, Any]:
        loaded = require_admin(request)
        if not read_env_value(loaded.secrets.key_env):
            return {"available": False, "key_env": loaded.secrets.key_env, "services": {}}
        try:
            services = SecretVault(loaded.secrets).list_secrets()
        except SecretVaultError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"available": True, "key_env": loaded.secrets.key_env, "services": services}

    @router.post("/api/secrets")
    def admin_set_secret(request: Request, payload: AdminSecretSetRequest) -> dict[str, Any]:
        loaded = require_admin(request)
        if not read_env_value(loaded.secrets.key_env):
            raise HTTPException(
                status_code=400,
                detail=f"{loaded.secrets.key_env} is not set - run `ybm setup` to generate it first",
            )
        try:
            SecretVault(loaded.secrets).set_secret(payload.service, payload.key, payload.value)
        except SecretVaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Service/key are audited so changes are traceable; the value never is.
        _audit_config_update(
            repositories_loader(), loaded, "secrets",
            {"action": "set", "service": payload.service, "key": payload.key},
        )
        return {"service": payload.service, "key": payload.key, "set": True}

    @router.delete("/api/secrets/{service}/{key}")
    def admin_delete_secret(request: Request, service: str, key: str) -> dict[str, Any]:
        loaded = require_admin(request)
        if not read_env_value(loaded.secrets.key_env):
            raise HTTPException(status_code=400, detail=f"{loaded.secrets.key_env} is not set")
        try:
            deleted = SecretVault(loaded.secrets).delete_secret(service, key)
        except SecretVaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail=f"secret not found: {service}.{key}")
        _audit_config_update(
            repositories_loader(), loaded, "secrets",
            {"action": "delete", "service": service, "key": key},
        )
        return {"service": service, "key": key, "deleted": True}

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
                render_prompt("base/llm_health_check_system.md"),
                render_prompt("tasks/llm_health_check_user.md"),
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
    latest = _latest_vscode_observed_at(store)
    age_seconds = None
    if latest is not None:
        age_seconds = max(0, int((datetime.now(timezone.utc) - latest.astimezone(timezone.utc)).total_seconds()))
    connected = age_seconds is not None and age_seconds <= 90
    status = "connected" if connected else "stale" if latest is not None else "waiting"
    return {
        "connected": connected,
        "status": status,
        "last_seen_at": latest.isoformat() if latest is not None else None,
        "last_seen_age_seconds": age_seconds,
        "heartbeat": store.heartbeat.model_dump(mode="json") if store.heartbeat else None,
        "state": store.state.model_dump(mode="json") if store.state else None,
        "pending_terminal_commands": len(store.terminal_commands),
        "terminal_outputs": [output.model_dump(mode="json") for output in store.terminal_outputs[-20:]],
    }


def _latest_vscode_observed_at(store: VSCodeBridgeStore) -> datetime | None:
    candidates = []
    if store.heartbeat is not None:
        candidates.append(store.heartbeat.observed_at)
    if store.state is not None:
        candidates.append(store.state.observed_at)
    if not candidates:
        return None
    return max(item if item.tzinfo else item.replace(tzinfo=timezone.utc) for item in candidates)


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


def _legacy_default_llm_env_keys() -> list[str]:
    return _llm_profile_env_keys("default")


def _llm_profile_env_keys(profile_name: str) -> list[str]:
    fields = ("PROVIDER", "MODEL", "BASE_URL", "API_KEY_ENV", "TIMEOUT_SECONDS", "MAX_TOKENS", "TEMPERATURE")
    return [f"AGENT_LLM__PROFILES__{profile_name}__{field}" for field in fields]


def _telegram_config_env_keys() -> list[str]:
    return [
        "AGENT_CHANNELS__TELEGRAM__ENABLED",
        "AGENT_CHANNELS__TELEGRAM__TOKEN_ENV",
        "AGENT_CHANNELS__TELEGRAM__ALLOWED_USER_IDS",
        "AGENT_CHANNELS__TELEGRAM__ALLOWED_CHAT_IDS",
        "AGENT_CHANNELS__TELEGRAM__POLLING",
    ]


def _vscode_config_env_keys() -> list[str]:
    return [
        "AGENT_ADAPTERS__VSCODE__ENABLED",
        "AGENT_ADAPTERS__VSCODE__BRIDGE_HOST",
        "AGENT_ADAPTERS__VSCODE__BRIDGE_PORT",
        "AGENT_ADAPTERS__VSCODE__AUTH_TOKEN_ENV",
    ]


def _workspace_config_env_keys() -> list[str]:
    return [
        "AGENT_ADAPTERS__WORKSPACE__ENABLED",
        "AGENT_ADAPTERS__WORKSPACE__ROOT_DIR",
        "AGENT_ADAPTERS__WORKSPACE__WEB_HOST",
        "AGENT_ADAPTERS__WORKSPACE__WEB_PORT_START",
        "AGENT_ADAPTERS__WORKSPACE__OPEN_BROWSER",
    ]


def _computer_use_config_env_keys() -> list[str]:
    return [
        "AGENT_ADAPTERS__COMPUTER_USE__ENABLED",
        "AGENT_ADAPTERS__COMPUTER_USE__MAX_STEPS",
        "AGENT_ADAPTERS__COMPUTER_USE__STEP_DELAY_SECONDS",
        "AGENT_ADAPTERS__COMPUTER_USE__SCREENSHOT_DIR",
        "AGENT_ADAPTERS__COMPUTER_USE__ALLOWED_APPS",
        "AGENT_ADAPTERS__COMPUTER_USE__ALLOWED_ROOTS",
        "AGENT_ADAPTERS__COMPUTER_USE__REQUIRE_SESSION_APPROVAL",
        "AGENT_ADAPTERS__COMPUTER_USE__MAX_UI_ELEMENTS",
    ]


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


def build_task_trace(repositories: Repositories, task_id: str) -> dict[str, Any] | None:
    """The full "what did this task do" record - shared by the `/api/tasks/{id}/trace`
    endpoint and `ybm trace` (which calls this directly against the DB, no
    running backend required - see cli.py's `trace_task()`). Returns None if
    the task doesn't exist.
    """
    task = repositories.tasks.get(task_id)
    if task is None:
        return None
    raw_audit_events = _task_trace_audit_events(repositories, task.model_dump(mode="json"))
    tool_invocations = repositories.tool_invocations.list_for_task(task_id)
    formatted_audit = [format_audit_event(event).model_dump(mode="json") for event in raw_audit_events]
    raw_audit = [event.model_dump(mode="json") for event in raw_audit_events]
    # PlanModel is dead (docs/HISTORY.md §1.1 - the Operator loop never creates
    # one); operator_history in task metadata is the real step-by-step
    # execution record now.
    trace_context = _trace_context(task.model_dump(mode="json"), raw_audit)
    return {
        "task": task.model_dump(mode="json"),
        "context": trace_context,
        "operator_history": task.metadata.get("operator_history") or [],
        "timeline": _trace_timeline(formatted_audit, tool_invocations),
        "tool_invocations": tool_invocations,
        "evidence": _extract_evidence(tool_invocations),
        "approvals": [approval.model_dump(mode="json") for approval in repositories.approvals.list_for_task(task_id)],
        "artifacts": [artifact.model_dump(mode="json") for artifact in repositories.artifacts.list_for_task(task_id)],
        "signals": [signal.model_dump(mode="json") for signal in repositories.task_signals.list_for_task(task_id)],
        "audit": formatted_audit,
        "raw_audit": raw_audit,
    }


# What a completed task actually touched (docs/HISTORY.md N5's "evidence view").
# Deliberately key-based, not a per-tool_name dispatch table: every tool's
# input/output dict already uses one of these field names for a path/URL/
# command whenever it has one (confirmed across filesystem, browser, http,
# code interpreter, workspace, VS Code, artifact delivery) - matching on the
# key means a newly registered tool is covered automatically as long as it
# follows the same naming, instead of needing a new branch here every time.
_EVIDENCE_FILE_KEYS = (
    "path", "paths", "workspace_dir", "changed_paths", "files_created",
    "screenshot_path", "screenshot_uri", "adapter_dir",
)
_EVIDENCE_URL_KEYS = ("url", "urls", "visited_urls", "browser_url", "preview_url")
_EVIDENCE_COMMAND_KEYS = ("command", "commands")


def _collect_evidence_values(source: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = source.get(key)
        if not value:
            continue
        if isinstance(value, list):
            values.extend(str(item) for item in value if item)
        else:
            values.append(str(value))
    return values


def _extract_evidence(tool_invocations: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = {"files": [], "urls": [], "commands": []}
    seen: dict[str, set[str]] = {"files": set(), "urls": set(), "commands": set()}
    for invocation in tool_invocations:
        request = invocation.get("request") or {}
        result = invocation.get("result") or {}
        request_input = request.get("input") if isinstance(request, dict) else None
        result_output = result.get("output") if isinstance(result, dict) else None
        sources = [source for source in (request_input, result_output) if isinstance(source, dict)]
        for bucket, keys in (
            ("files", _EVIDENCE_FILE_KEYS),
            ("urls", _EVIDENCE_URL_KEYS),
            ("commands", _EVIDENCE_COMMAND_KEYS),
        ):
            for source in sources:
                for value in _collect_evidence_values(source, keys):
                    if value in seen[bucket]:
                        continue
                    seen[bucket].add(value)
                    evidence[bucket].append(
                        {
                            "value": value,
                            "tool_name": invocation.get("tool_name"),
                            "at": invocation.get("created_at"),
                        }
                    )
    return evidence


def _task_trace_audit_events(repositories: Repositories, task: dict[str, Any]) -> list:
    events = repositories.audit.list_for_task(str(task["id"]))
    seen = {event.id for event in events}

    for event in list(events):
        if event.correlation_id:
            for related in repositories.audit.list_by_correlation_id(event.correlation_id):
                if related.id not in seen:
                    events.append(related)
                    seen.add(related.id)

    source_message_id = (task.get("metadata") or {}).get("source_message_id")
    if source_message_id:
        for related in repositories.audit.list_matching_payload_value("message_id", str(source_message_id)):
            if related.id not in seen:
                events.append(related)
                seen.add(related.id)
        for related in repositories.audit.list_matching_payload_value("source_message_id", str(source_message_id)):
            if related.id not in seen:
                events.append(related)
                seen.add(related.id)

    return sorted(events, key=lambda event: event.created_at)


def _trace_context(task: dict[str, Any], raw_audit: list[dict[str, Any]]) -> dict[str, Any]:
    classification = next((event for event in raw_audit if event.get("type") == AuditEventType.MESSAGE_CLASSIFIED.value), None)
    message = next((event for event in raw_audit if event.get("type") == AuditEventType.MESSAGE_RECEIVED.value), None)
    classification_payload = classification.get("payload", {}) if classification else {}
    return {
        "inbound_message": message.get("payload", {}) if message else None,
        "classification": classification_payload or {
            "task_type": (task.get("metadata") or {}).get("task_type"),
            "confidence": (task.get("metadata") or {}).get("classification_confidence"),
            "reason": (task.get("metadata") or {}).get("classification_reason"),
            "original_message_text": (task.get("metadata") or {}).get("original_message_text"),
        },
        "classifier_llm": classification_payload.get("llm"),
        "final_metadata": task.get("metadata") or {},
    }


def _trace_timeline(audit_events: list[dict[str, Any]], tool_invocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in audit_events:
        items.append(
            {
                "at": event.get("formatted_time") or event.get("created_at"),
                "kind": "audit",
                "title": event.get("title") or event.get("type"),
                "summary": event.get("summary"),
                "actor": event.get("actor"),
                "details": event.get("details"),
            }
        )
    for tool in tool_invocations:
        items.append(
            {
                "at": tool.get("completed_at") or tool.get("created_at"),
                "kind": "tool",
                "title": tool.get("tool_name"),
                "summary": tool.get("status"),
                "actor": "orchestrator",
                "details": {"request": tool.get("request"), "result": tool.get("result")},
            }
        )
    return sorted(items, key=lambda item: str(item.get("at") or ""))


def _config_warnings(settings: AppSettings) -> list[str]:
    warnings: list[str] = []
    telegram = settings.channels.telegram
    if telegram.enabled and not telegram.allowed_user_ids and not telegram.allowed_chat_ids:
        warnings.append("Telegram is enabled but no allowed user IDs or chat IDs are configured; all messages will be denied.")
    if settings.llm.default_profile not in settings.llm.profiles:
        warnings.append("Default orchestrator LLM profile is not configured; Telegram task classification will fail.")
    if read_env_value("AGENT_CAPABILITIES"):
        warnings.append("AGENT_CAPABILITIES is set in the environment and may override access-mode changes saved to YAML.")
    schedule_policy = settings.capabilities.get(Capability.SCHEDULE_MANAGE)
    if settings.scheduler.enabled and not (schedule_policy and schedule_policy.enabled):
        warnings.append("Scheduler service is enabled, but schedule.manage capability is off; scheduled jobs cannot be created from tasks.")
    return warnings


def _database_summary(settings: AppSettings) -> dict[str, Any]:
    database = Database(settings.storage.database_url)
    database.initialize()
    tables = [
        "conversations",
        "conversation_memory",
        "messages",
        "tasks",
        "approvals",
        "tool_invocations",
        "artifacts",
        "schedules",
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


def _tool_registry_summary(settings: AppSettings, repositories: Repositories, audit: AuditLogger) -> dict[str, Any]:
    registry = build_tool_registry(
        settings,
        backend_base_url(settings),
        artifact_repository=repositories.artifacts,
        task_repository=repositories.tasks,
        repositories=repositories,
        audit_logger=audit,
    )
    tools = []
    for definition in registry.definitions:
        operations = list(definition.operations)
        tools.append(
            {
                "name": definition.name,
                "group": _tool_group(definition.name),
                "capability": definition.capability.value,
                "enabled": definition.enabled,
                "lifecycle": definition.lifecycle,
                "operations": operations,
                "default_operation": definition.default_operation,
                "input_schema": definition.input_schema.__name__ if definition.input_schema else None,
                "output_schema": definition.output_schema.__name__ if definition.output_schema else None,
                "operation_schemas": {
                    operation: schema.__name__
                    for operation, schema in (definition.operation_schemas or {}).items()
                },
                "operation_output_schemas": {
                    operation: schema.__name__
                    for operation, schema in (definition.operation_output_schemas or {}).items()
                },
            }
        )
    return {
        "total": len(tools),
        "enabled": len([tool for tool in tools if tool["enabled"]]),
        "tools": tools,
    }



def _tool_group(name: str) -> str:
    if name.startswith("browser."):
        return "browser"
    if name.startswith("computer.") or name.startswith("desktop."):
        return "desktop"
    if name.startswith("filesystem.") or name.startswith("workspace."):
        return "filesystem"
    if name.startswith("document."):
        return "documents"
    if name in {"coding.agent", "coding_assistant", "vscode.copilot_terminal", "vscode.terminal_command"}:
        return "coding_agents"
    if name.startswith("schedule."):
        return "schedules"
    if name.startswith("artifact."):
        return "artifacts"
    if name.startswith("adapter."):
        return "adapter_factory"
    return "other"


# The Streamlit app (admin_streamlit.py) is the one real admin UI - this used
# to be a second, ~1,300-line embedded-HTML console (docs/HISTORY.md P4).
# Kept as a small pointer, not a 404, for anyone with the old /admin URL
# bookmarked; the JSON API below it (everything /admin/api/*) is unchanged
# and is what Streamlit itself talks to.
_ADMIN_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Control Admin</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 32rem; margin: 4rem auto; padding: 0 1.5rem; color: #1a1a1a; }
    a { color: #2563eb; }
  </style>
</head>
<body>
  <h1>Agent Control Admin</h1>
  <p>The admin console has moved to the Streamlit app.</p>
  <p><a href="http://127.0.0.1:8501">Open the admin console (Streamlit, port 8501)</a></p>
  <p><code>ybm start</code> launches it along with the rest of the stack, if it isn't already running.</p>
  <p>This page's JSON API (<code>/admin/api/*</code>) is unchanged and is what the Streamlit app itself uses.</p>
</body>
</html>
"""


