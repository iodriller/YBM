from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any
from urllib import error, parse, request

import pandas as pd
import streamlit as st

from agent_control.config_sync import read_env_value


DEFAULT_BACKEND_URL = "http://127.0.0.1:8765"
DEFAULT_TASK_LIMIT = 25
DEFAULT_AUDIT_LIMIT = 50
ACTIVE_STATUSES = {"received", "interpreting", "planned", "running", "retrying", "awaiting_approval"}
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}


def main() -> None:
    st.set_page_config(page_title="Agent Control", layout="wide", initial_sidebar_state="expanded")
    _inject_css()

    state = _sidebar_state()
    st.title("Agent Control")
    st.caption("Local operator console for Telegram intake, task orchestration, tools, config, and audit.")
    _show_flash()

    try:
        summary = _api_json(state["backend_url"], _summary_path(_current_task_limit()), state["token"])
    except ApiError as exc:
        st.error(str(exc))
        st.info("Confirm the backend is running and the admin token is correct.")
        return

    _render_header(summary)
    _render_operations(summary, state)
    st.divider()
    _render_tasks(summary, state)
    st.divider()
    bottom_left, bottom_right = st.columns([1.15, 1])
    with bottom_left:
        _render_configuration(summary, state)
    with bottom_right:
        _render_audit(summary, state)
        st.divider()
        _render_diagnostics(summary, state)


def _sidebar_state() -> dict[str, str]:
    st.sidebar.header("Connection")
    backend_url = st.sidebar.text_input(
        "Backend URL",
        value=os.getenv("AGENT_ADMIN_BACKEND_URL", DEFAULT_BACKEND_URL),
        help="FastAPI backend that exposes /admin/api endpoints.",
    ).rstrip("/")
    default_token = read_env_value("AGENT_ADMIN_TOKEN") or ""
    token = st.sidebar.text_input(
        "Admin token",
        value=default_token,
        type="password",
        help="Uses AGENT_ADMIN_TOKEN from .env when available.",
    )
    if st.sidebar.button("Refresh", use_container_width=True):
        st.rerun()
    st.sidebar.link_button("Legacy FastAPI admin", _legacy_admin_url(backend_url or DEFAULT_BACKEND_URL, token), use_container_width=True)
    return {"backend_url": backend_url or DEFAULT_BACKEND_URL, "token": token}


def _render_header(summary: dict[str, Any]) -> None:
    config = summary.get("config", {})
    telegram = ((config.get("channels") or {}).get("telegram") or {})
    llm = config.get("llm") or {}
    vscode = summary.get("vscode") or {}
    tasks = summary.get("tasks") or []
    active_tasks = len([task for task in tasks if task.get("status") in ACTIVE_STATUSES])
    workspace = ((config.get("adapters") or {}).get("workspace") or {})

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("LLM", llm.get("default_profile") or "missing")
    c2.metric("Telegram", "Enabled" if telegram.get("enabled") else "Disabled")
    c3.metric("VS Code", "Connected" if vscode.get("connected") else "Fallback")
    c4.metric("Active Tasks", active_tasks)
    c5.metric("Workspace", workspace.get("root_dir") or ".agent_control/workspaces")

    warnings = summary.get("warnings") or []
    for warning in warnings:
        st.warning(warning)


def _render_operations(summary: dict[str, Any], state: dict[str, str]) -> None:
    config = summary.get("config", {})
    integrations = summary.get("integrations") or {}
    database = summary.get("database") or {}
    vscode = summary.get("vscode") or {}
    workspace = ((config.get("adapters") or {}).get("workspace") or {})

    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Current Runtime")
        st.dataframe(
            _runtime_rows(summary),
            hide_index=True,
            use_container_width=True,
            column_config={"Value": st.column_config.TextColumn(width="large")},
        )

        st.subheader("VS Code Command")
        with st.form("vscode-terminal-command"):
            command = st.text_area("Terminal command", height=90, placeholder="echo hello")
            terminal_id = st.text_input("Terminal ID", value="agent-control")
            instance_id = st.text_input("Instance ID", value="", help="Optional VS Code bridge instance filter.")
            cwd = st.text_input("Working directory", value="", help="Optional command working directory.")
            submitted = st.form_submit_button("Queue command")
        if submitted:
            if not command.strip():
                st.error("Command is required.")
            else:
                _post_feedback(
                    state,
                    "/admin/api/vscode/terminal-commands",
                    {
                        "command": command.strip(),
                        "terminal_id": terminal_id or "agent-control",
                        "instance_id": instance_id or None,
                        "cwd": cwd or None,
                    },
                    "Command queued.",
                )
        st.caption("Requires VS Code adapter plus terminal.run access.")

    with right:
        st.subheader("Quick Links")
        st.link_button("Backend health", f"{state['backend_url']}/health", use_container_width=True)
        st.link_button("Legacy FastAPI admin", _legacy_admin_url(state["backend_url"], state["token"]), use_container_width=True)
        vscode_state = vscode.get("state") or {}
        if vscode_state.get("active_file"):
            st.code(vscode_state["active_file"], language=None)
        st.caption(f"Database: {database.get('path') or database.get('database_url') or 'unknown'}")
        st.caption(f"Workspace root: {workspace.get('root_dir') or 'not configured'}")
        st.caption(f"Telegram allowlist users: {((integrations.get('telegram') or {}).get('allowed_user_count')) or 0}")


def _render_tasks(summary: dict[str, Any], state: dict[str, str]) -> None:
    st.subheader("Tasks")
    task_limit = st.number_input(
        "Show latest tasks",
        min_value=5,
        max_value=100,
        value=_current_task_limit(),
        step=5,
        key="task-limit",
    )
    st.session_state["admin_task_limit"] = int(task_limit)
    tasks_payload = _api_json(state["backend_url"], _tasks_path(int(task_limit)), state["token"])
    tasks = tasks_payload.get("tasks") or []
    pagination = tasks_payload.get("pagination") or {}

    confirm_clear = st.checkbox("Enable task-history clear buttons", key="confirm-task-clear")
    toolbar = st.columns([1, 1, 1, 1, 3])
    if toolbar[0].button("Clear completed", disabled=not confirm_clear, use_container_width=True):
        _delete_feedback(state, "/admin/api/tasks?include_active=false", "Completed task history cleared.")
    if toolbar[1].button("Clear all", disabled=not confirm_clear, use_container_width=True):
        _delete_feedback(state, "/admin/api/tasks?include_active=true", "All task history cleared.")
    toolbar[2].metric("Total", pagination.get("total", len(tasks)))
    toolbar[3].metric("Shown", len(tasks))

    if not tasks:
        st.info("No tasks found.")
        return

    statuses = sorted({str(task.get("status") or "unknown") for task in tasks})
    selected_statuses = st.multiselect("Filter shown statuses", statuses, default=statuses)
    visible_tasks = [task for task in tasks if str(task.get("status") or "unknown") in selected_statuses]
    st.dataframe(_task_frame(visible_tasks), hide_index=True, use_container_width=True)
    st.divider()

    if not visible_tasks:
        st.info("No tasks match the selected statuses.")
        return

    task_ids = [task["id"] for task in visible_tasks]
    if st.session_state.get("selected-task-id") not in task_ids:
        st.session_state["selected-task-id"] = task_ids[0]
    selected_task_id = st.selectbox(
        "Task details",
        task_ids,
        format_func=lambda task_id: _task_option_label(_find_task(visible_tasks, task_id)),
        key="selected-task-id",
    )
    selected_task = _find_task(visible_tasks, selected_task_id) or visible_tasks[0]
    _render_task_card(selected_task, state)


def _render_task_card(task: dict[str, Any], state: dict[str, str]) -> None:
    status = task.get("status", "")
    activity = _activity_label(status)
    title = f"{activity} | {task.get('objective', 'Untitled task')}"
    st.markdown(f"### {title}")
    with st.container():
        meta = task.get("metadata") or {}
        c1, c2, c3, c4 = st.columns([1.3, 1, 1, 1])
        c1.code(task.get("id") or "", language=None)
        c2.metric("Status", status)
        c3.metric("Type", meta.get("task_type") or "unknown")
        c4.metric("Updated", _relative_time(task.get("updated_at")))

        links = _task_links(task)
        if links:
            st.markdown("Result")
            for label, value in links:
                if value.startswith("http://") or value.startswith("https://"):
                    st.link_button(label, value)
                else:
                    st.code(value, language=None)

        actions = st.columns([1, 1, 1, 5])
        if actions[0].button("Pause", key=f"pause-{task['id']}", disabled=_action_disabled(task, "pause")):
            _task_signal(state, task["id"], "pause")
        if actions[1].button("Resume", key=f"resume-{task['id']}", disabled=_action_disabled(task, "resume")):
            _task_signal(state, task["id"], "resume")
        if actions[2].button("Cancel", key=f"cancel-{task['id']}", disabled=_action_disabled(task, "cancel")):
            _task_signal(state, task["id"], "cancel")

        trace = _api_json(state["backend_url"], f"/admin/api/tasks/{parse.quote(task['id'])}/trace", state["token"])
        _render_task_trace(trace, key_prefix=str(task["id"]))


def _render_task_trace(trace: dict[str, Any], key_prefix: str = "trace") -> None:
    st.markdown("#### Trace")
    plan = trace.get("plan") or {}
    steps = plan.get("steps") or []
    tools = trace.get("tool_invocations") or []
    timeline = trace.get("timeline") or []
    audit = trace.get("audit") or []

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Plan Steps", len(steps))
    c2.metric("Tool Calls", len(tools))
    c3.metric("Timeline", len(timeline))
    c4.metric("Audit", len(audit))

    st.markdown("##### Timeline")
    if timeline:
        st.dataframe(
            _timeline_frame(timeline),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Time": st.column_config.TextColumn(width="small"),
                "Kind": st.column_config.TextColumn(width="small"),
                "Title": st.column_config.TextColumn(width="medium"),
                "Summary": st.column_config.TextColumn(width="medium"),
                "Details": st.column_config.TextColumn(width="large"),
            },
        )
    else:
        st.info("No timeline entries.")

    with st.expander(f"Plan ({len(steps)} steps)", expanded=False):
        if not steps:
            st.info("No plan persisted yet.")
        for index, step in enumerate(steps, 1):
            st.markdown(f"**{index}. {step.get('title') or 'Step'}**")
            st.caption(
                f"tool={step.get('tool_name') or 'plan only'} | "
                f"risk={step.get('risk_level')} | "
                f"capabilities={', '.join(step.get('required_capabilities') or []) or 'none'}"
            )
            if step.get("description"):
                st.write(step["description"])
            if step.get("tool_input"):
                st.text_area(
                    "Step input / prompt",
                    _json_text(step["tool_input"]),
                    height=130,
                    key=f"{key_prefix}-step-input-{index}",
                    disabled=True,
                )

    with st.expander(f"Tools Used ({len(tools)} calls)", expanded=False):
        if not tools:
            st.info("No tool calls recorded yet.")
        for index, tool in enumerate(tools, 1):
            tool_id = str(tool.get("id") or index)
            st.markdown(f"**{index}. {tool.get('tool_name')}**")
            st.caption(f"{tool.get('status')} | {tool.get('capability')} | {tool.get('created_at')} -> {tool.get('completed_at')}")
            prompt = _tool_prompt(tool)
            output = _terminal_output_text((tool.get("result") or {}))
            if prompt:
                st.text_area("Prompt / command", prompt, height=150, key=f"{key_prefix}-prompt-{tool_id}", disabled=True)
            if output:
                st.text_area("Output", output, height=240, key=f"{key_prefix}-output-{tool_id}", disabled=True)
            st.text_area(
                "Full request/result",
                _json_text({"request": tool.get("request"), "result": tool.get("result")}),
                height=180,
                key=f"{key_prefix}-full-tool-{tool_id}",
                disabled=True,
            )

    related_left, related_right = st.columns(2)
    with related_left:
        _json_expander("Orchestrator Context", trace.get("context") or {})
    with related_right:
        _json_expander(
            "Related Records",
            {
                "approvals": trace.get("approvals") or [],
                "signals": trace.get("signals") or [],
                "artifacts": trace.get("artifacts") or [],
                "audit": audit,
            },
        )

    _json_expander("Raw trace JSON", trace)


def _render_configuration(summary: dict[str, Any], state: dict[str, str]) -> None:
    config = summary.get("config") or {}
    st.subheader("Configuration")
    st.caption("Access modes are shown first because they control what the worker can do.")
    _render_access_config(summary, state)
    with st.expander("LLM", expanded=False):
        _render_llm_config(summary, state)
    with st.expander("Telegram", expanded=False):
        _render_telegram_config(config, state)
    with st.expander("VS Code", expanded=False):
        _render_vscode_config(config, state)
    with st.expander("Workspace", expanded=False):
        _render_workspace_config(config, state)
    with st.expander("Effective Config", expanded=False):
        try:
            effective = _api_json(state["backend_url"], "/admin/api/config/effective", state["token"])
            st.code(_json_text(effective), language="json")
        except ApiError as exc:
            st.error(str(exc))
            st.code(_json_text(config), language="json")


def _render_access_config(summary: dict[str, Any], state: dict[str, str]) -> None:
    access_modes = summary.get("access_modes") or {}
    selected: dict[str, str] = {}
    columns = st.columns(2)
    for index, (name, item) in enumerate(access_modes.items()):
        with columns[index % 2]:
            options = item.get("options") or [
                {"value": "off", "label": "Off"},
                {"value": "read_only", "label": "Read-only"},
                {"value": "write_access", "label": "Write with approval"},
                {"value": "full_access", "label": "Full access"},
            ]
            values = [option["value"] for option in options]
            labels = {option["value"]: option.get("label") or option["value"] for option in options}
            current = item.get("mode") if item.get("mode") in values else values[0]
            st.markdown(f"**{item.get('label') or name}**")
            st.caption(", ".join(item.get("capabilities") or []))
            selected[name] = st.selectbox(
                "Mode",
                values,
                index=values.index(current),
                format_func=lambda value, mapping=labels: mapping.get(value, value),
                key=f"access-{name}",
                label_visibility="collapsed",
            )
    if st.button("Save access modes", type="primary"):
        _post_feedback(state, "/admin/api/config/access-modes", {"modes": selected}, "Access modes saved.")


def _render_llm_config(summary: dict[str, Any], state: dict[str, str]) -> None:
    config = summary.get("config") or {}
    llm = config.get("llm") or {}
    integrations = summary.get("integrations") or {}
    presets = ((integrations.get("llm") or {}).get("presets") or [])
    preset_labels = {preset["key"]: preset.get("label") or preset["key"] for preset in presets}
    preset = st.selectbox("Preset", list(preset_labels), format_func=lambda key: preset_labels[key]) if preset_labels else None
    c1, c2 = st.columns([1, 3])
    if c1.button("Use preset", use_container_width=True, disabled=preset is None):
        _post_feedback(state, "/admin/api/config/llm/preset", {"preset": preset}, "LLM preset saved. Restart long-running processes.")
    if c2.button("Test active LLM", use_container_width=True):
        try:
            response = _api_json(state["backend_url"], "/admin/api/llm/test", state["token"], method="POST", payload={})
            st.success(response.get("output_preview") or "LLM responded.")
        except ApiError as exc:
            st.error(str(exc))

    active = llm.get("default_profile") or "default"
    profile = (llm.get("profiles") or {}).get(active) or {}
    with st.form("llm-config"):
        profile_name = st.text_input("Profile", active)
        default_profile = st.text_input("Default profile", active)
        provider = st.text_input("Provider", profile.get("provider") or "openai_compatible")
        model = st.text_input("Model", profile.get("model") or "")
        base_url = st.text_input("Base URL", profile.get("base_url") or "")
        api_key_env = st.text_input("API key env", profile.get("api_key_env") or "")
        api_key_value = st.text_input("Replace API key", type="password", value="")
        timeout_seconds = st.number_input(
            "Timeout seconds",
            min_value=1,
            max_value=3600,
            value=int(profile.get("timeout_seconds") or 60),
        )
        max_tokens = st.number_input(
            "Max tokens",
            min_value=1,
            max_value=262144,
            value=int(profile.get("max_tokens") or 4096),
        )
        temperature = st.number_input(
            "Temperature",
            min_value=0.0,
            max_value=2.0,
            value=float(profile.get("temperature") if profile.get("temperature") is not None else 0.2),
            step=0.1,
        )
        submitted = st.form_submit_button("Save LLM config")
    if submitted:
        _post_feedback(
            state,
            "/admin/api/config/llm",
            {
                "profile_name": profile_name,
                "default_profile": default_profile,
                "provider": provider,
                "model": model,
                "base_url": base_url or None,
                "api_key_env": api_key_env or None,
                "api_key_value": api_key_value or None,
                "timeout_seconds": int(timeout_seconds),
                "max_tokens": int(max_tokens),
                "temperature": float(temperature),
            },
            "LLM config saved. Restart long-running processes.",
        )


def _render_telegram_config(config: dict[str, Any], state: dict[str, str]) -> None:
    telegram = ((config.get("channels") or {}).get("telegram") or {})
    with st.form("telegram-config"):
        enabled = st.checkbox("Enabled", value=bool(telegram.get("enabled")))
        token_env = st.text_input("Token env", telegram.get("token_env") or "TELEGRAM_BOT_TOKEN")
        bot_token = st.text_input("Replace bot token", type="password", value="")
        user_ids = st.text_input("Allowed user IDs", ",".join(str(item) for item in telegram.get("allowed_user_ids") or []))
        chat_ids = st.text_input("Allowed chat IDs", ",".join(str(item) for item in telegram.get("allowed_chat_ids") or []))
        polling = st.checkbox("Polling", value=telegram.get("polling") is not False)
        submitted = st.form_submit_button("Save Telegram config")
    if submitted:
        _post_feedback(
            state,
            "/admin/api/config/telegram",
            {
                "enabled": enabled,
                "token_env": token_env,
                "bot_token": bot_token or None,
                "allowed_user_ids": _parse_csv_ints(user_ids),
                "allowed_chat_ids": _parse_csv_ints(chat_ids),
                "polling": polling,
            },
            "Telegram config saved. Restart polling to reload config.",
        )


def _render_vscode_config(config: dict[str, Any], state: dict[str, str]) -> None:
    vscode = ((config.get("adapters") or {}).get("vscode") or {})
    with st.form("vscode-config"):
        enabled = st.checkbox("Enabled", value=bool(vscode.get("enabled")))
        host = st.text_input("Bridge host", vscode.get("bridge_host") or "127.0.0.1")
        port = st.number_input("Bridge port", min_value=1, max_value=65535, value=int(vscode.get("bridge_port") or 8766))
        auth_token_env = st.text_input("Token env", vscode.get("auth_token_env") or "VSCODE_BRIDGE_TOKEN")
        bridge_token = st.text_input("Replace token", type="password", value="")
        submitted = st.form_submit_button("Save VS Code config")
    if submitted:
        _post_feedback(
            state,
            "/admin/api/config/vscode",
            {
                "enabled": enabled,
                "bridge_host": host,
                "bridge_port": int(port),
                "auth_token_env": auth_token_env,
                "bridge_token": bridge_token or None,
            },
            "VS Code config saved.",
        )


def _render_workspace_config(config: dict[str, Any], state: dict[str, str]) -> None:
    workspace = ((config.get("adapters") or {}).get("workspace") or {})
    with st.form("workspace-config"):
        enabled = st.checkbox("Enabled", value=workspace.get("enabled") is not False)
        root_dir = st.text_input("Root directory", workspace.get("root_dir") or ".agent_control/workspaces")
        web_host = st.text_input("Preview host", workspace.get("web_host") or "127.0.0.1")
        web_port_start = st.number_input("Port start", min_value=1, max_value=65535, value=int(workspace.get("web_port_start") or 8890))
        open_browser = st.checkbox("Open browser", value=workspace.get("open_browser") is not False)
        submitted = st.form_submit_button("Save workspace config")
    if submitted:
        _post_feedback(
            state,
            "/admin/api/config/workspace",
            {
                "enabled": enabled,
                "root_dir": root_dir,
                "web_host": web_host,
                "web_port_start": int(web_port_start),
                "open_browser": open_browser,
            },
            "Workspace config saved. Restart worker to reload config.",
        )


def _render_audit(summary: dict[str, Any], state: dict[str, str]) -> None:
    st.subheader("Audit")
    c1, c2, c3 = st.columns([1, 2, 1])
    category = c1.selectbox(
        "Category",
        ["", "raw_telegram", "telegram_access", "classification", "spawned_task", "failed_classification", "policy", "config", "tool"],
        format_func=lambda value: "All" if not value else value.replace("_", " ").title(),
    )
    query = c2.text_input("Search")
    limit = c3.number_input("Limit", min_value=10, max_value=200, value=DEFAULT_AUDIT_LIMIT, step=10)
    audit = _api_json(state["backend_url"], _audit_path(limit=int(limit), category=category, query=query), state["token"])
    events = audit.get("events", [])

    confirm_audit_clear = st.checkbox("Enable audit clear button", key="confirm-audit-clear")
    c4, c5 = st.columns([1, 5])
    if c4.button("Clear audit", disabled=not confirm_audit_clear, use_container_width=True):
        _delete_feedback(state, "/admin/api/audit", "Audit history cleared.")
    c5.caption(f"Showing {len(events)} events")

    if not events:
        st.info("No audit events found.")
        return
    for event in events:
        with st.expander(f"{event.get('formatted_time') or event.get('created_at')} | {event.get('title') or event.get('type')}"):
            st.write(event.get("summary") or "")
            st.code(_json_text(event.get("details") or event), language="json")


def _render_diagnostics(summary: dict[str, Any], state: dict[str, str]) -> None:
    st.subheader("Diagnostics")
    c1, c2 = st.columns(2)
    with c1:
        _json_expander("VS Code Bridge", summary.get("vscode") or {})
    with c2:
        _json_expander("Database", summary.get("database") or {})
    _json_expander("Raw Summary", summary)


def _runtime_rows(summary: dict[str, Any]) -> pd.DataFrame:
    config = summary.get("config") or {}
    integrations = summary.get("integrations") or {}
    llm = config.get("llm") or {}
    adapters = config.get("adapters") or {}
    rows = [
        ("Instance", (config.get("identity") or {}).get("instance_name")),
        ("Admin token", "required" if (summary.get("admin") or {}).get("token_required") else "not required"),
        ("LLM profile", llm.get("default_profile")),
        ("Telegram", "enabled" if ((integrations.get("telegram") or {}).get("enabled")) else "disabled"),
        ("VS Code adapter", "enabled" if ((adapters.get("vscode") or {}).get("enabled")) else "disabled"),
        ("Workspace", (adapters.get("workspace") or {}).get("root_dir")),
        ("Adapter cache", (adapters.get("adapter_factory") or {}).get("root_dir")),
        ("Config file", (summary.get("admin") or {}).get("config_file")),
    ]
    return pd.DataFrame([{"Setting": key, "Value": value or "not configured"} for key, value in rows])


def _task_frame(tasks: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for task in tasks:
        metadata = task.get("metadata") or {}
        rows.append(
            {
                "Activity": _activity_label(task.get("status", "")),
                "Status": task.get("status"),
                "Type": metadata.get("task_type"),
                "Objective": task.get("objective"),
                "Preview": metadata.get("preview_url"),
                "Workspace": metadata.get("workspace_dir"),
                "Updated": _relative_time(task.get("updated_at")),
                "ID": task.get("id"),
            }
        )
    return pd.DataFrame(rows)


def _timeline_frame(timeline: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in timeline:
        rows.append(
            {
                "Time": item.get("at") or "",
                "Kind": item.get("kind") or "",
                "Title": item.get("title") or "",
                "Actor": item.get("actor") or "",
                "Summary": item.get("summary") or "",
                "Details": _timeline_detail_text(item),
            }
        )
    return pd.DataFrame(rows)


def _timeline_detail_text(item: dict[str, Any]) -> str:
    details = item.get("details")
    if not isinstance(details, dict):
        return _clip_text(details or "")

    lines: list[str] = []
    _add_line(lines, "decision", details.get("decision"))
    _add_line(lines, "reason", details.get("reason"))
    _add_line(lines, "status", _status_transition(details))
    _add_line(lines, "task", details.get("task_id"))
    _add_line(lines, "objective", details.get("objective") or details.get("normalized_objective"))
    _add_line(lines, "type", details.get("task_type"))
    _add_line(lines, "confidence", details.get("confidence") or details.get("classification_confidence"))
    _add_line(lines, "tool", details.get("tool_name"))
    _add_line(lines, "operation", _nested_value(details, "input", "operation") or details.get("operation"))
    _add_line(lines, "capability", details.get("capability"))
    _add_line(lines, "scope", details.get("scope_target") or _nested_value(details, "input", "scope_target"))
    _add_line(lines, "workspace", details.get("workspace_dir") or _nested_value(details, "output", "workspace_dir"))
    _add_line(lines, "url", details.get("url") or details.get("preview_url") or _nested_value(details, "output", "url"))
    _add_line(lines, "materialized", details.get("materialized_from") or _nested_value(details, "output", "materialized_from"))
    _add_line(lines, "message", details.get("text_preview") or details.get("text") or details.get("message"))

    request_payload = details.get("request")
    if isinstance(request_payload, dict):
        request_input = request_payload.get("input") if isinstance(request_payload.get("input"), dict) else {}
        _add_line(lines, "tool", request_payload.get("tool_name"))
        _add_line(lines, "operation", request_input.get("operation"))
        _add_line(lines, "capability", request_payload.get("capability"))
        _add_line(lines, "cwd", request_input.get("cwd"))
        _add_line(lines, "scope", request_payload.get("scope_target") or request_input.get("scope_target"))

    result_payload = details.get("result")
    if isinstance(result_payload, dict):
        _add_line(lines, "result", result_payload.get("status"))
        _add_line(lines, "error", result_payload.get("error_message"))
        output_payload = result_payload.get("output") if isinstance(result_payload.get("output"), dict) else {}
        _add_line(lines, "workspace", output_payload.get("workspace_dir"))
        _add_line(lines, "url", output_payload.get("url"))
        _add_line(lines, "materialized", output_payload.get("materialized_from"))
        if isinstance(output_payload.get("files"), list):
            _add_line(lines, "files", f"{len(output_payload['files'])} file(s)")
        usage = output_payload.get("usage")
        if isinstance(usage, dict):
            _add_line(lines, "usage", "; ".join(str(value) for value in usage.values()))
        terminal = output_payload.get("terminal_output")
        if isinstance(terminal, list) and terminal:
            first = next((item.get("content") for item in terminal if isinstance(item, dict) and item.get("content")), None)
            _add_line(lines, "output", first)

    if isinstance(details.get("plan"), dict):
        lines.extend(_plan_summary_lines(details["plan"]))
    elif isinstance(details.get("steps"), list):
        lines.extend(_step_summary_lines(details["steps"]))

    output = details.get("output")
    if isinstance(output, dict):
        _add_line(lines, "output", output.get("output_text") or output.get("message") or output.get("summary"))

    if not lines:
        for key, value in details.items():
            if key in {"llm", "plan", "config_context", "system_prompt", "user_prompt", "prompt", "source_text"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                _add_line(lines, key, value)
            elif isinstance(value, dict):
                simple = [f"{child_key}={_clip_text(child_value, 80)}" for child_key, child_value in value.items() if isinstance(child_value, (str, int, float, bool))]
                if simple:
                    _add_line(lines, key, ", ".join(simple[:4]))

    return "\n".join(lines[:10]) if lines else _clip_text(details)


def _plan_summary_lines(plan: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    _add_line(lines, "plan", plan.get("id"))
    _add_line(lines, "source", plan.get("source"))
    _add_line(lines, "objective", plan.get("objective"))
    steps = plan.get("steps") or []
    if isinstance(steps, list):
        lines.extend(_step_summary_lines(steps))
    return lines


def _step_summary_lines(steps: list[Any]) -> list[str]:
    titles = []
    for index, step in enumerate(steps[:6], 1):
        if isinstance(step, dict):
            title = step.get("title") or "Step"
            tool = step.get("tool_name") or "plan only"
            titles.append(f"{index}. {title} -> {tool}")
    if not titles:
        return []
    suffix = "" if len(steps) <= 6 else f"; +{len(steps) - 6} more"
    return [f"steps: {'; '.join(titles)}{suffix}"]


def _status_transition(details: dict[str, Any]) -> str | None:
    old_status = details.get("old_status")
    new_status = details.get("new_status")
    if old_status or new_status:
        return f"{old_status or '?'} -> {new_status or '?'}"
    return details.get("status")


def _nested_value(value: dict[str, Any], parent: str, child: str) -> Any:
    nested = value.get(parent)
    if isinstance(nested, dict):
        return nested.get(child)
    return None


def _add_line(lines: list[str], label: str, value: Any) -> None:
    if value is None or value == "":
        return
    lines.append(f"{label}: {_clip_text(value)}")


def _clip_text(value: Any, limit: int = 260) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _find_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
    return next((task for task in tasks if task.get("id") == task_id), None)


def _task_option_label(task: dict[str, Any] | None) -> str:
    if not task:
        return "Unknown task"
    objective = str(task.get("objective") or "Untitled task")
    trimmed = objective if len(objective) <= 90 else f"{objective[:87]}..."
    return f"{_activity_label(str(task.get('status') or ''))} | {trimmed}"


def _legacy_admin_url(backend_url: str, token: str | None = None) -> str:
    base = f"{backend_url.rstrip('/')}/admin"
    if token:
        return f"{base}?{parse.urlencode({'token': token})}"
    return base


def _task_links(task: dict[str, Any]) -> list[tuple[str, str]]:
    metadata = task.get("metadata") or {}
    output = ((metadata.get("last_tool_result") or {}).get("output") or {})
    links = []
    preview = output.get("url") or metadata.get("preview_url")
    workspace = output.get("workspace_dir") or metadata.get("workspace_dir")
    adapter_dir = output.get("adapter_dir") or metadata.get("adapter_dir")
    if preview:
        links.append(("Open preview", str(preview)))
    if workspace:
        links.append(("Workspace", str(workspace)))
    if adapter_dir:
        links.append(("Adapter cache", str(adapter_dir)))
    return links


def _activity_label(status: str) -> str:
    mapping = {
        "received": "Queued",
        "interpreting": "Interpreting",
        "planned": "Ready",
        "awaiting_approval": "Waiting approval",
        "running": "Running",
        "retrying": "Retrying",
        "paused": "Paused",
        "completed": "Done",
        "cancelled": "Cancelled",
        "failed": "Failed",
        "blocked": "Blocked",
    }
    return mapping.get(status, status.replace("_", " ").title())


def _action_disabled(task: dict[str, Any], action: str) -> bool:
    status = task.get("status")
    if status in TERMINAL_STATUSES:
        return True
    if action == "pause":
        return status == "paused"
    if action == "resume":
        return status not in {"paused", "blocked"}
    return False


def _task_signal(state: dict[str, str], task_id: str, signal: str) -> None:
    _post_feedback(state, f"/admin/api/tasks/{parse.quote(task_id)}/signals", {"signal": signal}, f"Task {signal} signal sent.")


def _post_feedback(state: dict[str, str], path: str, payload: dict[str, Any], success: str) -> None:
    try:
        _api_json(state["backend_url"], path, state["token"], method="POST", payload=payload)
        _set_flash("success", success)
        st.rerun()
    except ApiError as exc:
        st.error(str(exc))


def _delete_feedback(state: dict[str, str], path: str, success: str) -> None:
    try:
        _api_json(state["backend_url"], path, state["token"], method="DELETE")
        _set_flash("success", success)
        st.rerun()
    except ApiError as exc:
        st.error(str(exc))


def _set_flash(kind: str, message: str) -> None:
    st.session_state["admin_flash"] = {"kind": kind, "message": message}


def _show_flash() -> None:
    flash = st.session_state.pop("admin_flash", None)
    if not isinstance(flash, dict) or not flash.get("message"):
        return
    if flash.get("kind") == "success":
        st.success(str(flash["message"]))
    else:
        st.info(str(flash["message"]))


def _current_task_limit() -> int:
    value = st.session_state.get("admin_task_limit", DEFAULT_TASK_LIMIT)
    try:
        return max(5, min(100, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_TASK_LIMIT


def _summary_path(task_limit: int = DEFAULT_TASK_LIMIT) -> str:
    return f"/admin/api/summary?{parse.urlencode({'task_limit': str(task_limit)})}"


def _tasks_path(limit: int = DEFAULT_TASK_LIMIT, offset: int = 0) -> str:
    return f"/admin/api/tasks?{parse.urlencode({'limit': str(limit), 'offset': str(offset)})}"


def _audit_path(limit: int = DEFAULT_AUDIT_LIMIT, category: str | None = None, query: str | None = None) -> str:
    params = {"limit": str(limit)}
    if category:
        params["category"] = category
    if query:
        params["q"] = query
    return f"/admin/api/audit?{parse.urlencode(params)}"


def _api_json(
    backend_url: str,
    path: str,
    token: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{backend_url.rstrip('/')}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Agent-Control-Admin-Token"] = token
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"{exc.code} {_api_error_detail(detail)}") from exc
    except error.URLError as exc:
        raise ApiError(f"Backend unavailable: {exc.reason}") from exc


class ApiError(RuntimeError):
    pass


def _api_error_detail(raw: str) -> str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, dict) and parsed.get("detail"):
        return str(parsed["detail"])
    return raw


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _json_expander(label: str, value: Any, *, expanded: bool = False) -> None:
    with st.expander(label, expanded=expanded):
        st.code(_json_text(value), language="json")


def _parse_csv_ints(value: str) -> list[int]:
    parsed: list[int] = []
    for item in value.split(","):
        stripped = item.strip()
        if not stripped:
            continue
        try:
            parsed.append(int(stripped))
        except ValueError:
            continue
    return parsed


def _tool_prompt(tool: dict[str, Any]) -> str:
    input_payload = ((tool.get("request") or {}).get("input") or {})
    return str(input_payload.get("prompt") or input_payload.get("command") or input_payload.get("objective") or "")


def _terminal_output_text(result: dict[str, Any]) -> str:
    output = result.get("output") or {}
    terminal = output.get("terminal_output") or []
    chunks = [str(item.get("content")) for item in terminal if isinstance(item, dict) and item.get("content")]
    return "\n\n".join(chunks)


def _relative_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return value
    seconds = max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.3rem; padding-bottom: 2rem; max-width: 1500px; }
        [data-testid="stMetric"] {
          border: 1px solid rgba(49, 51, 63, 0.18);
          border-radius: 8px;
          padding: 12px 14px;
          background: rgba(250, 250, 250, 0.65);
        }
        [data-testid="stMetricLabel"] { font-size: 0.78rem; }
        [data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
        div[data-testid="stExpander"] { border-radius: 8px; }
        .stTabs [data-baseweb="tab-list"] { gap: 6px; }
        .stTabs [data-baseweb="tab"] {
          border-radius: 6px;
          padding: 8px 12px;
          border: 1px solid rgba(49, 51, 63, 0.12);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
