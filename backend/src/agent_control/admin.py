from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import Field

from agent_control.config_sync import CONFIG_FILE_PATH, ConfigManager, read_env_value
from agent_control.config import AppSettings
from agent_control.llm.providers import OpenAICompatibleProvider
from agent_control.orchestration.signals import apply_task_signal
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


class AdminAccessModesRequest(StrictBaseModel):
    modes: dict[str, CapabilityAccessMode]


SettingsLoader = Callable[[], AppSettings]
RepositoriesLoader = Callable[[], Repositories]


LLM_PRESETS: dict[str, dict[str, Any]] = {
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
        expected = read_env_value(loaded.server.admin_token_env)
        provided = request.headers.get("X-Agent-Control-Admin-Token") or request.query_params.get("token")
        if expected and provided != expected:
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
        task = repositories.tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        plan = repositories.plans.get(task.plan_id) if task.plan_id else None
        raw_audit_events = _task_trace_audit_events(repositories, task.model_dump(mode="json"))
        tool_invocations = repositories.tool_invocations.list_for_task(task_id)
        formatted_audit = [format_audit_event(event).model_dump(mode="json") for event in raw_audit_events]
        raw_audit = [event.model_dump(mode="json") for event in raw_audit_events]
        plan_payload = plan.model_dump(mode="json") if plan else None
        trace_context = _trace_context(task.model_dump(mode="json"), plan_payload, raw_audit)
        return {
            "task": task.model_dump(mode="json"),
            "context": trace_context,
            "plan": plan_payload,
            "timeline": _trace_timeline(formatted_audit, tool_invocations),
            "tool_invocations": tool_invocations,
            "approvals": [approval.model_dump(mode="json") for approval in repositories.approvals.list_for_task(task_id)],
            "artifacts": [artifact.model_dump(mode="json") for artifact in repositories.artifacts.list_for_task(task_id)],
            "signals": [signal.model_dump(mode="json") for signal in repositories.task_signals.list_for_task(task_id)],
            "audit": formatted_audit,
            "raw_audit": raw_audit,
        }

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


def _trace_context(task: dict[str, Any], plan: dict[str, Any] | None, raw_audit: list[dict[str, Any]]) -> dict[str, Any]:
    classification = next((event for event in raw_audit if event.get("type") == AuditEventType.MESSAGE_CLASSIFIED.value), None)
    message = next((event for event in raw_audit if event.get("type") == AuditEventType.MESSAGE_RECEIVED.value), None)
    plan_created = next((event for event in raw_audit if event.get("type") == AuditEventType.PLAN_CREATED.value), None)
    classification_payload = classification.get("payload", {}) if classification else {}
    plan_payload = plan_created.get("payload", {}) if plan_created else {}
    return {
        "inbound_message": message.get("payload", {}) if message else None,
        "classification": classification_payload or {
            "task_type": (task.get("metadata") or {}).get("task_type"),
            "confidence": (task.get("metadata") or {}).get("classification_confidence"),
            "reason": (task.get("metadata") or {}).get("classification_reason"),
            "original_message_text": (task.get("metadata") or {}).get("original_message_text"),
        },
        "classifier_llm": classification_payload.get("llm"),
        "planner_or_default_plan": {
            "audit_payload": plan_payload,
            "config_context": plan_payload.get("config_context") or (plan_payload.get("llm") or {}).get("config_context"),
            "llm": plan_payload.get("llm"),
            "plan_source": plan_payload.get("source") or "planner",
        },
        "plan": plan,
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
    return warnings


def _database_summary(settings: AppSettings) -> dict[str, Any]:
    database = Database(settings.storage.database_url)
    database.initialize()
    tables = [
        "conversations",
        "conversation_memory",
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
      --success: #1a7f37;
      --success-bg: #dafbe1;
      --chip: #f6f8fa;
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
        --success: #3fb950;
        --success-bg: #17301f;
        --chip: #21262d;
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
    button:disabled { cursor: not-allowed; opacity: 0.45; }
    button.primary { border-color: var(--accent); color: var(--accent); }
    button.active {
      border-color: var(--success);
      background: var(--success-bg);
      color: var(--success);
      font-weight: 600;
    }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; }
    .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
    .status-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: var(--panel); }
    .status-card strong { display: block; font-size: 18px; margin-top: 4px; overflow-wrap: anywhere; }
    .mini-label { color: var(--muted); font-size: 12px; }
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
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td { padding: 8px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; overflow-wrap: anywhere; }
    th { font-size: 12px; color: var(--muted); font-weight: 600; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }
    pre { white-space: pre-wrap; word-break: break-word; max-height: 360px; overflow: auto; }
    .access-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
    .access-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: grid; gap: 10px; }
    .access-card-title { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
    .access-card h3 { margin: 0; font-size: 14px; }
    .mode-buttons { display: flex; flex-wrap: wrap; gap: 6px; }
    .mode-button { font-size: 12px; padding: 6px 8px; }
    .cap-list { display: flex; flex-wrap: wrap; gap: 6px; }
    .badge { border: 1px solid var(--border); border-radius: 999px; padding: 2px 8px; font-size: 12px; }
    .badge.soft { background: var(--chip); }
    .link-list { display: grid; gap: 4px; margin-top: 8px; font-size: 12px; }
    a { color: var(--accent); }
    .activity { display: inline-flex; gap: 6px; align-items: center; white-space: nowrap; }
    .activity-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex: 0 0 auto; }
    .activity.active .activity-dot { background: var(--accent); animation: pulse 1.1s ease-in-out infinite; }
    .activity.waiting .activity-dot { background: #9a6700; }
    .activity.paused .activity-dot { background: var(--muted); }
    .activity.done .activity-dot { background: var(--success); }
    .activity.bad .activity-dot { background: var(--danger); }
    .task-meta { color: var(--muted); font-size: 12px; margin-top: 4px; }
    .task-toolbar { margin-bottom: 12px; }
    .task-id { font-size: 12px; }
    .task-objective { font-weight: 600; }
    .task-details-row td { background: var(--chip); }
    .trace-panel { display: grid; gap: 12px; padding: 8px 0; }
    .trace-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; }
    .trace-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px; background: var(--panel); min-width: 0; }
    .trace-card h3 { margin: 0 0 8px; font-size: 13px; }
    .trace-card pre { max-height: 280px; margin: 0; }
    .trace-step, .tool-call { border-left: 3px solid var(--accent); padding-left: 8px; margin: 8px 0; }
    .trace-title { display: flex; gap: 8px; align-items: baseline; justify-content: space-between; }
    .trace-output { margin-top: 8px; }
    details.trace-details > summary { cursor: pointer; color: var(--muted); font-size: 12px; }
    .kv { display: grid; grid-template-columns: minmax(110px, 190px) minmax(0, 1fr); gap: 5px 10px; margin: 0; }
    .kv dt { color: var(--muted); font-size: 12px; }
    .kv dd { margin: 0; min-width: 0; }
    .kv.compact { grid-template-columns: minmax(90px, 150px) minmax(0, 1fr); }
    .list-value { margin: 0; padding-left: 18px; }
    .text-block {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      max-height: 320px;
      overflow: auto;
      margin: 4px 0;
      padding: 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
    }
    @keyframes pulse {
      0%, 100% { opacity: 0.45; }
      50% { opacity: 1; }
    }
    .audit-toolbar { margin-bottom: 12px; }
    .audit-list { display: grid; gap: 6px; }
    .audit-card { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; display: grid; gap: 5px; }
    .audit-card-header { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: start; }
    .audit-title { font-weight: 650; }
    .audit-summary { color: var(--text); font-size: 13px; line-height: 1.35; }
    .audit-meta { display: flex; flex-wrap: wrap; gap: 6px; color: var(--muted); font-size: 12px; }
    .audit-decision { display: inline-flex; gap: 6px; color: var(--muted); font-size: 12px; }
    details.audit-details > summary { cursor: pointer; color: var(--muted); font-size: 12px; }
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
    <section class="panel wide">
      <h2>Overview</h2>
      <div id="overview" class="status-grid"></div>
    </section>
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
      <h2>Local Workspace</h2>
      <div class="field"><label>Enabled</label><input id="workspace-enabled" type="checkbox"></div>
      <div class="field"><label>Root Directory</label><input id="workspace-root" placeholder=".agent_control/workspaces"></div>
      <div class="field"><label>Preview Host</label><input id="workspace-host" placeholder="127.0.0.1"></div>
      <div class="field"><label>Port Start</label><input id="workspace-port" placeholder="8890"></div>
      <div class="field"><label>Open Browser</label><input id="workspace-open-browser" type="checkbox"></div>
      <div class="row"><button onclick="saveWorkspace()">Save Workspace</button></div>
      <div id="workspace-result" class="muted"></div>
    </section>
    <section class="panel">
      <h2>Orchestrator LLM</h2>
      <div class="field">
        <label>Preset</label>
        <div class="row">
          <select id="llm-preset">
            <option value="localdeploy_gemma3_4b">LocalDeploy Gemma 3 4B</option>
            <option value="openai_gpt41">OpenAI GPT-4.1</option>
          </select>
          <button onclick="applyLLMPreset()">Use Preset</button>
        </div>
      </div>
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
      <div class="row"><button onclick="saveAccessModes()">Save Access Modes</button><span id="access-result" class="muted"></span></div>
    </section>
    <section class="panel wide">
      <h2>Database</h2>
      <pre id="database"></pre>
    </section>
    <section class="panel wide">
      <h2>Tasks</h2>
      <div class="task-toolbar row">
        <span id="task-count" class="muted"></span>
        <button id="task-more" onclick="viewMoreTasks()">View more tasks</button>
        <button onclick="clearCompletedTasks()">Clear completed tasks</button>
        <button onclick="clearAllTasks()">Clear all tasks</button>
      </div>
      <div id="tasks"></div>
    </section>
    <section class="panel wide">
      <h2>Audit</h2>
      <div class="audit-toolbar row">
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
        <button onclick="loadAudit(true)">Filter</button>
        <button onclick="clearAuditFilters()">Clear</button>
        <button onclick="clearAuditHistory()">Clear audit history</button>
      </div>
      <div id="audit-count" class="muted"></div>
      <div id="audit" class="audit-list"></div>
      <div class="row" style="margin-top: 10px;">
        <button id="audit-more" onclick="viewMoreAudit()">View more</button>
      </div>
    </section>
  </main>
  <script>
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    const headers = token ? {"X-Agent-Control-Admin-Token": token} : {};
    let auditLimit = 20;
    let auditCustomView = false;
    let taskLimit = 5;
    const expandedTaskIds = new Set();
    const expandedAuditIds = new Set();
    const taskTraces = new Map();

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

    function labelize(value) {
      return String(value || "")
        .replace(/[_.]+/g, " ")
        .replace(/\\b\\w/g, char => char.toUpperCase());
    }

    function isPlainObject(value) {
      return value && typeof value === "object" && !Array.isArray(value);
    }

    function renderText(value) {
      return `<div class="text-block">${escapeHtml(value)}</div>`;
    }

    function renderHumanValue(value, depth = 0) {
      if (value === null || value === undefined || value === "") return `<span class="muted">none</span>`;
      if (typeof value === "string") {
        if (value.length > 100 || value.includes("\\n")) return renderText(value);
        return `<span>${escapeHtml(value)}</span>`;
      }
      if (typeof value === "number" || typeof value === "boolean") return `<code>${escapeHtml(value)}</code>`;
      if (Array.isArray(value)) {
        if (!value.length) return `<span class="muted">empty</span>`;
        return `<ol class="list-value">${value.map(item => `<li>${renderHumanValue(item, depth + 1)}</li>`).join("")}</ol>`;
      }
      if (isPlainObject(value)) {
        const entries = Object.entries(value);
        if (!entries.length) return `<span class="muted">empty</span>`;
        return `
          <dl class="kv ${depth > 0 ? "compact" : ""}">
            ${entries.map(([key, item]) => `
              <dt>${escapeHtml(labelize(key))}</dt>
              <dd>${renderHumanValue(item, depth + 1)}</dd>
            `).join("")}
          </dl>
        `;
      }
      return `<span>${escapeHtml(String(value))}</span>`;
    }

    function terminalOutputText(result) {
      const output = (result || {}).output || {};
      const terminal = output.terminal_output || [];
      return terminal
        .map(item => item && item.content ? String(item.content) : "")
        .filter(Boolean)
        .join("\\n\\n");
    }

    function toolPrompt(tool) {
      const input = ((tool || {}).request || {}).input || {};
      return input.prompt || input.command || input.objective || "";
    }

    function activityForStatus(status) {
      const active = ["interpreting", "planned", "running", "retrying"];
      if (active.includes(status)) return {label: status === "planned" ? "Ready" : labelize(status), className: "active"};
      if (status === "awaiting_approval") return {label: "Waiting Approval", className: "waiting"};
      if (status === "received") return {label: "Queued", className: ""};
      if (status === "paused") return {label: "Paused", className: "paused"};
      if (status === "completed") return {label: "Done", className: "done"};
      if (status === "cancelled") return {label: "Cancelled", className: "bad"};
      if (status === "failed" || status === "blocked") return {label: labelize(status), className: "bad"};
      return {label: labelize(status), className: ""};
    }

    function taskActionDisabled(task, action) {
      const terminal = ["completed", "cancelled", "failed"];
      if (terminal.includes(task.status)) return true;
      if (action === "pause") return task.status === "paused";
      if (action === "resume") return task.status !== "paused" && task.status !== "blocked";
      return false;
    }

    function taskUpdatedLabel(task) {
      if (!task.updated_at) return "";
      const updated = new Date(task.updated_at);
      if (Number.isNaN(updated.getTime())) return "";
      const seconds = Math.max(0, Math.floor((Date.now() - updated.getTime()) / 1000));
      if (seconds < 60) return `${seconds}s ago`;
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return `${minutes}m ago`;
      return `${Math.floor(minutes / 60)}h ago`;
    }

    function renderOverview(data) {
      const config = data.config || {};
      const llm = config.llm || {};
      const telegram = ((config.channels || {}).telegram) || {};
      const workspace = (((config.adapters || {}).workspace) || {});
      const vscode = data.vscode || {};
      const activeTasks = (data.tasks || []).filter(task => ["received", "interpreting", "planned", "running", "retrying", "awaiting_approval"].includes(task.status)).length;
      document.getElementById("overview").innerHTML = `
        <div class="status-card"><span class="mini-label">LLM</span><strong>${escapeHtml(llm.default_profile || "missing")}</strong></div>
        <div class="status-card"><span class="mini-label">Telegram</span><strong>${telegram.enabled ? "Enabled" : "Disabled"}</strong></div>
        <div class="status-card"><span class="mini-label">VS Code Bridge</span><strong>${vscode.connected ? "Connected" : "Fallback/Waiting"}</strong></div>
        <div class="status-card"><span class="mini-label">Active Tasks</span><strong>${activeTasks}</strong></div>
        <div class="status-card"><span class="mini-label">Workspace</span><strong>${escapeHtml(workspace.root_dir || ".agent_control/workspaces")}</strong></div>
      `;
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
      document.getElementById("access-modes").innerHTML = `
        <div class="access-grid">
          ${Object.entries(modes).map(([name, item]) => {
            const options = item.options || [
              {value: "off", label: "Off"},
              {value: "read_only", label: "Read-only"},
              {value: "write_access", label: "Write with approval"},
              {value: "full_access", label: "Full access"}
            ];
            return `
              <div class="access-card">
                <div class="access-card-title">
                  <div>
                    <h3>${escapeHtml(item.label || labelize(name))}</h3>
                    <div class="muted">${escapeHtml(name)}</div>
                  </div>
                  <span class="badge ${item.mode === "off" ? "" : "soft"}">${escapeHtml(labelize(item.mode))}</span>
                </div>
                <div class="mode-buttons">
                  ${options.map(option => `
                    <button
                      type="button"
                      class="mode-button ${item.mode === option.value ? "active" : ""}"
                      data-access-mode-group="${escapeHtml(name)}"
                      data-mode="${escapeHtml(option.value)}"
                    >${escapeHtml(option.label || labelize(option.value))}</button>
                  `).join("")}
                </div>
                <div class="cap-list">
                  ${(item.capabilities || []).map(capability => `<span class="badge soft">${escapeHtml(capability)}</span>`).join("")}
                </div>
              </div>
            `;
          }).join("")}
        </div>
      `;
    }

    async function setAccessMode(group, mode) {
      document.querySelectorAll("[data-access-mode-group]").forEach(button => {
        if (button.getAttribute("data-access-mode-group") !== group) return;
        button.classList.toggle("active", button.getAttribute("data-mode") === mode);
      });
      await saveAccessModes();
    }

    function taskResultLinks(task) {
      const result = ((task.metadata || {}).last_tool_result || {}).output || {};
      const links = [];
      const previewUrl = result.url || (task.metadata || {}).preview_url;
      const workspaceDir = result.workspace_dir || (task.metadata || {}).workspace_dir;
      const serverPid = result.server_pid || (task.metadata || {}).server_pid;
      if (previewUrl) links.push(`<a href="${escapeHtml(previewUrl)}" target="_blank" rel="noreferrer">${escapeHtml(previewUrl)}</a>`);
      if (workspaceDir) links.push(`<span>Workspace: <code>${escapeHtml(workspaceDir)}</code></span>`);
      if (serverPid) links.push(`<span>Server PID: ${escapeHtml(serverPid)}</span>`);
      return links.length ? `<div class="link-list">${links.join("")}</div>` : "";
    }

    function renderTasks(tasks, pagination = {}) {
      const total = Number(pagination.total ?? tasks.length);
      document.getElementById("task-count").textContent = `Showing ${tasks.length} of ${total} task${total === 1 ? "" : "s"}`;
      const more = document.getElementById("task-more");
      if (more) more.style.display = pagination.has_more ? "inline-flex" : "none";
      if (!tasks.length) {
        document.getElementById("tasks").innerHTML = "<div class='muted'>No tasks found.</div>";
        return;
      }
      document.getElementById("tasks").innerHTML = `
        <table>
          <thead><tr><th style="width: 22%;">ID</th><th style="width: 16%;">Activity</th><th>Objective</th><th style="width: 250px;">Actions</th></tr></thead>
          <tbody>${tasks.map(task => {
            const activity = activityForStatus(task.status);
            const type = (task.metadata || {}).task_type;
            const expanded = expandedTaskIds.has(task.id);
            return `
              <tr>
              <td>
                <code class="task-id">${escapeHtml(task.id)}</code>
                <div class="task-meta">${escapeHtml(taskUpdatedLabel(task))}</div>
              </td>
              <td>
                <span class="activity ${escapeHtml(activity.className)}">
                  <span class="activity-dot"></span>
                  <span>${escapeHtml(activity.label)}</span>
                </span>
                ${task.current_step_id ? `<div class="task-meta">step ${escapeHtml(task.current_step_id)}</div>` : ""}
              </td>
              <td>
                <div class="task-objective">${escapeHtml(task.objective)}</div>
                ${type ? `<div class="task-meta">${escapeHtml(labelize(type))}</div>` : ""}
                ${taskResultLinks(task)}
              </td>
              <td class="row">
                <button ${taskActionDisabled(task, "pause") ? "disabled" : ""} data-task-id="${escapeHtml(task.id)}" data-task-action="pause">Pause</button>
                <button ${taskActionDisabled(task, "resume") ? "disabled" : ""} data-task-id="${escapeHtml(task.id)}" data-task-action="resume">Resume</button>
                <button ${taskActionDisabled(task, "cancel") ? "disabled" : ""} data-task-id="${escapeHtml(task.id)}" data-task-action="cancel">Cancel</button>
                <button class="primary" data-task-id="${escapeHtml(task.id)}" data-task-details="toggle">${expanded ? "Hide details" : "Details"}</button>
              </td>
            </tr>
            ${expanded ? `
              <tr class="task-details-row">
                <td colspan="4">${renderTaskTrace(task.id)}</td>
              </tr>
            ` : ""}
          `}).join("")}</tbody>
        </table>
      `;
    }

    function renderTaskTrace(taskId) {
      const trace = taskTraces.get(taskId);
      if (!trace) return `<div class="trace-panel muted">Loading full task trace...</div>`;
      if (trace.error) return `<div class="trace-panel danger">${escapeHtml(trace.error)}</div>`;
      const context = trace.context || {};
      const plan = trace.plan || {};
      const steps = plan.steps || [];
      const tools = trace.tool_invocations || [];
      const timeline = trace.timeline || [];
      return `
        <div class="trace-panel">
          <div class="row">
            <button data-task-id="${escapeHtml(taskId)}" data-task-reload="trace">Reload trace</button>
            <span class="muted">Full trace: inbound message, classifier prompt/result, orchestrator context, plan, tool prompts, tool outputs, policy/audit, approvals, artifacts, and final metadata.</span>
          </div>
          <div class="trace-grid">
            <div class="trace-card">
              <h3>Context Fed To Orchestrator</h3>
              ${renderHumanValue({
                inbound_message: context.inbound_message,
                classification: context.classification,
                classifier_llm: context.classifier_llm,
                planner_or_default_plan: context.planner_or_default_plan
              })}
            </div>
            <div class="trace-card">
              <h3>Task And Final State</h3>
              ${renderHumanValue({
                id: (trace.task || {}).id,
                status: (trace.task || {}).status,
                objective: (trace.task || {}).objective,
                current_step_id: (trace.task || {}).current_step_id,
                final_metadata: context.final_metadata
              })}
            </div>
          </div>
          <div class="trace-card">
            <h3>Orchestrator Plan</h3>
              ${steps.length ? steps.map((step, index) => `
                <div class="trace-step">
                  <div class="trace-title">
                    <strong>${index + 1}. ${escapeHtml(step.title || "Step")}</strong>
                    <span class="badge soft">${escapeHtml(step.tool_name || "plan only")}</span>
                  </div>
                  <div class="task-meta">capability=${escapeHtml((step.required_capabilities || []).join(", ") || "none")} risk=${escapeHtml(step.risk_level || "")}</div>
                  <div>${escapeHtml(step.description || "")}</div>
                  ${step.tool_input ? `<details class="trace-details"><summary>Step input / prompt</summary>${renderHumanValue(step.tool_input)}</details>` : ""}
                </div>
              `).join("") : `<div class="muted">No plan persisted yet.</div>`}
              <details class="trace-details"><summary>Raw plan JSON</summary>${renderHumanValue(plan || null)}</details>
          </div>
          <div class="trace-card">
            <h3>Tool Calls And Outputs</h3>
            ${tools.length ? tools.map((tool, index) => `
              <div class="tool-call">
                <div class="trace-title">
                  <strong>${index + 1}. ${escapeHtml(tool.tool_name)}</strong>
                  <span class="badge soft">${escapeHtml(tool.status)} | ${escapeHtml(tool.capability)}</span>
                </div>
                <div class="task-meta">created=${escapeHtml(tool.created_at || "")} completed=${escapeHtml(tool.completed_at || "")}</div>
                ${toolPrompt(tool) ? `<div class="trace-output"><strong>Prompt / command</strong>${renderText(toolPrompt(tool))}</div>` : ""}
                ${terminalOutputText(tool.result) ? `<div class="trace-output"><strong>Output</strong>${renderText(terminalOutputText(tool.result))}</div>` : ""}
                <details class="trace-details">
                  <summary>Full request and result</summary>
                  ${renderHumanValue({request: tool.request, result: tool.result})}
                </details>
              </div>
            `).join("") : `<div class="muted">No tool calls recorded yet.</div>`}
          </div>
          <div class="trace-grid">
            <div class="trace-card">
              <h3>Approvals, Signals, Artifacts</h3>
              ${renderHumanValue({
                approvals: trace.approvals || [],
                signals: trace.signals || [],
                artifacts: trace.artifacts || []
              })}
            </div>
            <div class="trace-card">
              <h3>Timeline</h3>
              ${timeline.length ? timeline.map(item => `
                <details class="trace-details">
                  <summary>${escapeHtml(item.at || "")} | ${escapeHtml(item.kind || "")} | ${escapeHtml(item.title || "")}</summary>
                  ${renderHumanValue(item.details || item)}
                </details>
              `).join("") : `<div class="muted">No timeline items recorded yet.</div>`}
            </div>
          </div>
          <div class="trace-card">
            <h3>Raw Trace</h3>
            <details class="trace-details">
              <summary>JSON for copy/debugging</summary>
              <pre>${escapeHtml(jsonBlock(trace))}</pre>
            </details>
          </div>
        </div>
      `;
    }

    async function toggleTaskDetails(taskId) {
      if (expandedTaskIds.has(taskId)) {
        expandedTaskIds.delete(taskId);
        await refresh();
        return;
      }
      expandedTaskIds.add(taskId);
      await refresh();
      if (!taskTraces.has(taskId)) {
        try {
          taskTraces.set(taskId, await api(`/admin/api/tasks/${encodeURIComponent(taskId)}/trace`));
        } catch (error) {
          taskTraces.set(taskId, {error: error.message});
        }
        await refresh();
      }
    }

    async function reloadTaskTrace(taskId) {
      taskTraces.delete(taskId);
      await refresh();
      try {
        taskTraces.set(taskId, await api(`/admin/api/tasks/${encodeURIComponent(taskId)}/trace`));
      } catch (error) {
        taskTraces.set(taskId, {error: error.message});
      }
      await refresh();
    }

    function renderAudit(events) {
      document.getElementById("audit-count").textContent = `Showing ${events.length} audit event${events.length === 1 ? "" : "s"}`;
      const more = document.getElementById("audit-more");
      if (more) {
        more.style.display = events.length >= auditLimit ? "inline-flex" : "none";
      }
      if (!events.length) {
        document.getElementById("audit").innerHTML = "<div class='muted'>No audit events found.</div>";
        return;
      }
      document.getElementById("audit").innerHTML = `
        ${events.map(event => `
          <article class="audit-card">
            <div class="audit-card-header">
              <div>
                <div class="audit-title">${escapeHtml(event.title || labelize(event.category || event.type))}</div>
                <div class="audit-meta">
                  <span>${escapeHtml(event.formatted_time || event.created_at || "")}</span>
                  <span>${escapeHtml(event.actor || "")}</span>
                  ${event.source ? `<span>${escapeHtml(event.source)}</span>` : ""}
                  ${event.task_type ? `<span>task: ${escapeHtml(labelize(event.task_type))}</span>` : ""}
                  ${event.task_id ? `<span><code>${escapeHtml(event.task_id)}</code></span>` : ""}
                </div>
              </div>
              <span class="badge soft">${escapeHtml(labelize(event.category || event.type))}</span>
            </div>
            <div class="audit-summary">${escapeHtml(event.summary || "")}</div>
            ${(event.decision || event.reason) ? `
              <div class="audit-decision">
                ${event.decision ? `<strong>${escapeHtml(labelize(event.decision))}</strong>` : ""}
                ${event.reason ? `<span>${escapeHtml(event.reason)}</span>` : ""}
              </div>
            ` : ""}
            <details class="audit-details" data-audit-id="${escapeHtml(event.id)}" ${expandedAuditIds.has(event.id) ? "open" : ""}>
              <summary>Details</summary>
              ${renderHumanValue(event.details || {})}
            </details>
          </article>
        `).join("")}
      `;
    }

    async function loadAudit(reset = false) {
      if (reset) auditLimit = 20;
      auditCustomView = true;
      const params = new URLSearchParams();
      const category = document.getElementById("audit-category").value;
      const q = document.getElementById("audit-query").value;
      if (category) params.set("category", category);
      if (q) params.set("q", q);
      params.set("limit", String(auditLimit));
      const data = await api(`/admin/api/audit?${params.toString()}`);
      renderAudit(data.events || []);
    }

    async function viewMoreAudit() {
      auditLimit += 20;
      await loadAudit(false);
    }

    async function viewMoreTasks() {
      taskLimit += 5;
      await refresh();
    }

    async function clearAuditFilters() {
      document.getElementById("audit-category").value = "";
      document.getElementById("audit-query").value = "";
      auditLimit = 20;
      auditCustomView = false;
      await refresh();
    }

    async function clearCompletedTasks() {
      if (!confirm("Clear completed, failed, blocked, and cancelled tasks plus their task-scoped audit/tool history?")) return;
      await api("/admin/api/tasks?include_active=false", {method: "DELETE"});
      taskLimit = 5;
      expandedTaskIds.clear();
      taskTraces.clear();
      await refresh();
    }

    async function clearAllTasks() {
      if (!confirm("Clear ALL tasks, including active tasks, plus their task-scoped audit/tool history?")) return;
      await api("/admin/api/tasks?include_active=true", {method: "DELETE"});
      taskLimit = 5;
      expandedTaskIds.clear();
      taskTraces.clear();
      await refresh();
    }

    async function clearAuditHistory() {
      if (!confirm("Clear all audit events? Tasks and tool records will remain.")) return;
      await api("/admin/api/audit", {method: "DELETE"});
      expandedAuditIds.clear();
      await refresh();
    }

    function populateConfigForms(config) {
      const llm = config.llm || {};
      const profileName = llm.default_profile || "default";
      const profile = (llm.profiles || {})[profileName] || {};
      const presetSelect = document.getElementById("llm-preset");
      if (presetSelect) {
        presetSelect.value = profileName === "openai_saved" ? "openai_gpt41" : "localdeploy_gemma3_4b";
      }
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

      const workspace = ((config.adapters || {}).workspace) || {};
      document.getElementById("workspace-enabled").checked = workspace.enabled !== false;
      document.getElementById("workspace-root").value = workspace.root_dir || ".agent_control/workspaces";
      document.getElementById("workspace-host").value = workspace.web_host || "127.0.0.1";
      document.getElementById("workspace-port").value = workspace.web_port_start || 8890;
      document.getElementById("workspace-open-browser").checked = workspace.open_browser !== false;
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
        const data = await api(`/admin/api/summary?task_limit=${taskLimit}`);
        status.textContent = `OK | ${data.config.identity.instance_name}`;
        renderOverview(data);
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
        renderTasks(data.tasks || [], data.task_pagination || {});
        if (!auditCustomView) {
          auditLimit = 20;
          renderAudit(data.audit || []);
        }
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
      const status = document.getElementById("status");
      try {
        await api(`/admin/api/tasks/${taskId}/signals`, {
          method: "POST",
          body: JSON.stringify({signal})
        });
        await refresh();
      } catch (error) {
        status.innerHTML = `<span class="danger">${error.message}</span>`;
      }
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

    async function applyLLMPreset() {
      const result = document.getElementById("llm-result");
      try {
        const preset = document.getElementById("llm-preset").value;
        const data = await api("/admin/api/config/llm/preset", {
          method: "POST",
          body: JSON.stringify({preset})
        });
        result.textContent = `Selected ${data.preset}. Restart long-running processes to reload config.`;
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

    async function saveWorkspace() {
      const result = document.getElementById("workspace-result");
      try {
        await api("/admin/api/config/workspace", {
          method: "POST",
          body: JSON.stringify({
            enabled: document.getElementById("workspace-enabled").checked,
            root_dir: document.getElementById("workspace-root").value,
            web_host: document.getElementById("workspace-host").value,
            web_port_start: Number(document.getElementById("workspace-port").value),
            open_browser: document.getElementById("workspace-open-browser").checked
          })
        });
        result.textContent = "Workspace config saved. Restart worker to reload config.";
        await refresh();
      } catch (error) {
        result.textContent = error.message;
      }
    }

    async function saveAccessModes() {
      const result = document.getElementById("access-result");
      if (result) result.textContent = "Saving";
      const modes = {};
      document.querySelectorAll("[data-access-mode-group].active").forEach(button => {
        modes[button.getAttribute("data-access-mode-group")] = button.getAttribute("data-mode");
      });
      try {
        await api("/admin/api/config/access-modes", {
          method: "POST",
          body: JSON.stringify({modes})
        });
        if (result) result.textContent = "Saved";
        await refresh();
      } catch (error) {
        if (result) result.textContent = error.message;
      }
    }

    document.addEventListener("click", event => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const taskAction = target.closest("[data-task-id][data-task-action]");
      if (taskAction) {
        event.preventDefault();
        taskSignal(taskAction.getAttribute("data-task-id"), taskAction.getAttribute("data-task-action"));
        return;
      }
      const taskDetails = target.closest("[data-task-id][data-task-details]");
      if (taskDetails) {
        event.preventDefault();
        toggleTaskDetails(taskDetails.getAttribute("data-task-id"));
        return;
      }
      const taskReload = target.closest("[data-task-id][data-task-reload]");
      if (taskReload) {
        event.preventDefault();
        reloadTaskTrace(taskReload.getAttribute("data-task-id"));
        return;
      }
      const accessButton = target.closest("[data-access-mode-group][data-mode]");
      if (accessButton) {
        event.preventDefault();
        setAccessMode(accessButton.getAttribute("data-access-mode-group"), accessButton.getAttribute("data-mode"));
      }
    });

    document.addEventListener("toggle", event => {
      const details = event.target;
      if (!(details instanceof HTMLDetailsElement)) return;
      const auditId = details.getAttribute("data-audit-id");
      if (!auditId) return;
      if (details.open) expandedAuditIds.add(auditId);
      else expandedAuditIds.delete(auditId);
    }, true);

    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>
"""
