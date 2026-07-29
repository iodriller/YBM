from __future__ import annotations

from datetime import datetime, timezone
from html import escape as html_escape
import json
import os
from typing import Any
from urllib import error, parse, request

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from agent_control.config import load_settings
from agent_control.config_sync import read_env_value
from agent_control.logging_setup import configure_logging


DEFAULT_BACKEND_URL = "http://127.0.0.1:8765"
DEFAULT_TASK_LIMIT = 25
DEFAULT_AUDIT_LIMIT = 50
LIVE_REFRESH_SECONDS = 3
ACTIVE_STATUSES = {"received", "interpreting", "planned", "running", "retrying", "awaiting_approval", "awaiting_external"}
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}


@st.cache_resource
def _configure_logging_once() -> None:
    # Streamlit reruns this whole module top-to-bottom on every interaction -
    # cache_resource is Streamlit's per-process singleton, so the log file
    # handler is opened once, not re-opened on every click.
    try:
        configure_logging(load_settings(), "admin_ui")
    except Exception:
        pass  # admin UI should still render even if settings/logging setup fails


def main() -> None:
    _configure_logging_once()
    st.set_page_config(page_title="Agent Control", layout="wide", initial_sidebar_state="collapsed")
    _inject_css()

    state = _connection_state()
    st.title("Agent Control")
    st.caption("Local operator console for Telegram intake, task orchestration, tools, config, and audit.")
    header_actions = st.columns([1, 1, 6])
    if header_actions[0].button("Refresh", use_container_width=True):
        st.rerun()
    live_updates = header_actions[1].toggle("Live", value=True, key="live-updates")
    _show_flash()

    if live_updates and hasattr(st, "fragment"):
        st.fragment(run_every=f"{LIVE_REFRESH_SECONDS}s")(_render_live_page)(state)
        return

    _render_live_page(state)
    if live_updates:
        _inject_auto_reload(LIVE_REFRESH_SECONDS)


def _render_live_page(state: dict[str, str]) -> None:
    try:
        summary = _api_json(state["backend_url"], _summary_path(_current_task_limit()), state["token"])
    except ApiError as exc:
        st.error(str(exc))
        st.info("Confirm the backend is running and the admin token is correct.")
        return

    _render_header(summary)
    _render_pending_approvals(state)
    _render_live_activity(summary, state)
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


def _inject_auto_reload(seconds: int) -> None:
    components.html(
        f"<script>setTimeout(() => window.parent.location.reload(), {seconds * 1000});</script>",
        height=0,
    )


def _connection_state() -> dict[str, str]:
    backend_url = os.getenv("AGENT_ADMIN_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/") or DEFAULT_BACKEND_URL
    token = read_env_value("AGENT_ADMIN_TOKEN") or ""
    return {"backend_url": backend_url, "token": token}


def _render_header(summary: dict[str, Any]) -> None:
    config = summary.get("config", {})
    telegram = ((config.get("channels") or {}).get("telegram") or {})
    llm = config.get("llm") or {}
    vscode = summary.get("vscode") or {}
    services = _services_by_name(summary)
    tasks = summary.get("tasks") or []
    active_tasks = len([task for task in tasks if task.get("status") in ACTIVE_STATUSES])

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("LLM", llm.get("default_profile") or "missing")
    c2.metric("Telegram", "Enabled" if telegram.get("enabled") else "Disabled")
    c3.metric("Worker", _service_label(services.get("worker")))
    c4.metric("Polling", _service_label(services.get("telegram_polling")))
    c5.metric("VS Code", _vscode_status_label(vscode))
    c6.metric("Active Tasks", active_tasks)

    _render_health(summary)

    warnings = summary.get("warnings") or []
    for warning in warnings:
        st.warning(warning)


def _extract_last_output(task: dict[str, Any]) -> str | None:
    meta = task.get("metadata") or {}
    result = meta.get("last_tool_result") or {}
    out = result.get("output") or {}
    text = (
        out.get("stdout") or out.get("summary") or out.get("response")
        or out.get("content") or out.get("text") or out.get("result") or None
    )
    if text:
        return str(text)
    if result.get("error_message"):
        return f"Error: {result['error_message']}"
    if meta.get("last_worker_error"):
        return f"Error: {meta['last_worker_error']}"
    return None


def _render_pending_approvals(state: dict[str, str]) -> None:
    try:
        payload = _api_json(state["backend_url"], "/admin/api/approvals", state["token"])
    except ApiError as exc:
        st.error(f"Could not load pending approvals: {exc}")
        return
    items = payload.get("approvals") or []
    if not items:
        return
    st.markdown("### Pending Approvals")
    for item in items:
        approval = item.get("approval") or {}
        approval_id = approval.get("id")
        with st.container(border=True):
            cols = st.columns([3, 1, 1, 1, 1])
            cols[0].markdown(f"**{html_escape(str(approval.get('summary') or ''))}**")
            cols[0].caption(html_escape(str(item.get("task_objective") or "")))
            cols[1].metric("Capability", str(approval.get("capability") or ""))
            cols[2].metric("Risk", str(approval.get("risk_level") or ""))
            if cols[3].button("Approve", key=f"approve-{approval_id}", type="primary"):
                _post_feedback(
                    state, f"/admin/api/approvals/{parse.quote(str(approval_id))}/decide",
                    {"decision": "approve"}, "Approved.",
                )
            if cols[4].button("Reject", key=f"reject-{approval_id}"):
                _post_feedback(
                    state, f"/admin/api/approvals/{parse.quote(str(approval_id))}/decide",
                    {"decision": "reject"}, "Rejected.",
                )


def _render_live_activity(summary: dict[str, Any], state: dict[str, str]) -> None:
    tasks = summary.get("tasks") or []
    active = [task for task in tasks if task.get("status") in ACTIVE_STATUSES]
    if not active:
        return
    st.markdown("### Live Activity")
    for task in active:
        status = str(task.get("status") or "")
        last_output = _extract_last_output(task)
        # Step count comes from operator_history. This used to read
        # task["current_step_id"], a plan-era field with zero writers since the
        # Operator loop replaced the plan path - so the metric read "—" for
        # every task forever, no matter how many tools it had actually called.
        history = (task.get("metadata") or {}).get("operator_history")
        steps_taken = len(history) if isinstance(history, list) else 0
        with st.container(border=True):
            cols = st.columns([3, 1, 1, 1])
            cols[0].markdown(f"**{html_escape(task.get('objective') or '')}**")
            cols[1].metric("Status", _activity_label(status))
            cols[2].metric("Updated", _relative_time(task.get("updated_at")))
            cols[3].metric("Steps", steps_taken)
            if last_output:
                _wrapped_text(last_output[:800], css_class="live-output-text")
            btn_cols = st.columns([1, 1, 6])
            if btn_cols[0].button("Pause", key=f"live-pause-{task['id']}", disabled=_action_disabled(task, "pause")):
                _task_signal(state, task["id"], "pause")
            if btn_cols[1].button("Cancel", key=f"live-cancel-{task['id']}", disabled=_action_disabled(task, "cancel")):
                _task_signal(state, task["id"], "cancel")


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

    with right:
        st.subheader("Connections")
        st.link_button("Backend health", f"{state['backend_url']}/health", use_container_width=True)
        st.markdown(f"**VS Code bridge:** {_vscode_status_label(vscode)}")
        if vscode.get("last_seen_at"):
            st.caption(f"Last seen: {vscode.get('last_seen_at')} ({vscode.get('last_seen_age_seconds')}s ago)")
        else:
            token_env = (((config.get("adapters") or {}).get("vscode") or {}).get("auth_token_env")) or "VSCODE_BRIDGE_TOKEN"
            st.caption(f"No VS Code heartbeat yet. Open VS Code with the Agent Control Bridge extension enabled; it reads `{token_env}` from process env, VS Code settings, or the workspace `.env`.")
        vscode_state = vscode.get("state") or {}
        workspace_folders = vscode_state.get("workspace_folders") or []
        if workspace_folders:
            st.caption(f"VS Code workspace: {workspace_folders[0]}")
        if vscode_state.get("active_file"):
            _wrapped_text(vscode_state["active_file"])
        st.caption(f"Database: {database.get('path') or database.get('database_url') or 'unknown'}")
        st.caption(f"Workspace root: {workspace.get('root_dir') or 'not configured'}")
        st.caption(f"Telegram allowlist users: {((integrations.get('telegram') or {}).get('allowed_user_count')) or 0}")
        service_items = (summary.get("services") or {}).get("items") or []
        if service_items:
            st.dataframe(
                _service_frame(service_items),
                hide_index=True,
                use_container_width=True,
                column_config={"Message": st.column_config.TextColumn(width="medium")},
            )


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
                    _wrapped_text(value)

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
    operator_history = trace.get("operator_history") or []
    tools = trace.get("tool_invocations") or []
    audit = trace.get("audit") or []
    trace_timeline = _trace_timeline_rows(trace)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Operator Steps", len(operator_history))
    c2.metric("Tool Calls", len(tools))
    c3.metric("Timeline", len(trace_timeline))
    c4.metric("Audit", len(audit))

    st.markdown("##### Timeline")
    if trace_timeline:
        st.dataframe(
            _trace_timeline_frame(trace),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Time": st.column_config.TextColumn(width="small"),
                "Kind": st.column_config.TextColumn(width="small"),
                "Title": st.column_config.TextColumn(width="medium"),
                "Source": st.column_config.TextColumn(width="small"),
                "Next": st.column_config.TextColumn(width="small"),
                "Prompt / Payload": st.column_config.TextColumn(width="large"),
                "Summary": st.column_config.TextColumn(width="medium"),
                "Details": st.column_config.TextColumn(width="large"),
            },
        )
    else:
        st.info("No timeline entries.")

    with st.expander(f"Operator Steps ({len(operator_history)})", expanded=False):
        _render_operator_history(operator_history, key_prefix)

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

    evidence = trace.get("evidence") or {}
    evidence_count = sum(len(evidence.get(bucket) or []) for bucket in ("files", "urls", "commands"))
    with st.expander(f"Evidence — what this task touched ({evidence_count})", expanded=False):
        _render_evidence(evidence)

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


def _render_evidence(evidence: dict[str, Any]) -> None:
    """What a completed task actually touched, sourced from real
    tool_invocations (docs/HISTORY.md N5) - the "we need to be able to see the
    result of it" ask, without having to open a log file or read raw JSON."""
    files = evidence.get("files") or []
    urls = evidence.get("urls") or []
    commands = evidence.get("commands") or []
    if not files and not urls and not commands:
        st.info("Nothing recorded yet for this task.")
        return
    for label, items in (("Files", files), ("URLs", urls), ("Commands", commands)):
        if not items:
            continue
        st.markdown(f"**{label}**")
        for item in items:
            st.markdown(f"- `{html_escape(str(item.get('value') or ''))}` — {html_escape(str(item.get('tool_name') or ''))}")


def _render_operator_history(history: list[dict[str, Any]], key_prefix: str) -> None:
    """operator_history is the Operator loop's own observe/decide/act record -
    one entry per tool call plus fulfillment/audit gap checks (docs/HISTORY.md
    §2.2). This is the real "what did the agent do" view; the admin UI used
    to render a PlanModel that nothing creates anymore and always showed
    "No plan persisted yet" for every task."""
    if not history:
        st.info("No steps recorded yet.")
        return
    for index, step in enumerate(history, 1):
        tool_name = str(step.get("tool_name") or "")
        status = str(step.get("status") or "unknown")
        is_check = tool_name.startswith("_")
        label = "check" if is_check else tool_name or "step"
        st.markdown(f"**{index}. {label}** — {status}")
        step_input = step.get("input")
        if step_input:
            st.text_area(
                "Input", _json_text(step_input), height=90,
                key=f"{key_prefix}-op-input-{index}", disabled=True,
            )
        if step.get("output_summary"):
            st.text_area(
                "Output", str(step["output_summary"]), height=120,
                key=f"{key_prefix}-op-output-{index}", disabled=True,
            )
        if step.get("error"):
            st.error(str(step["error"]))


def _render_configuration(summary: dict[str, Any], state: dict[str, str]) -> None:
    config = summary.get("config") or {}
    st.subheader("Configuration")
    st.caption("Access modes are shown first because they control what the worker can do.")
    _render_access_config(summary, state)
    st.divider()
    st.markdown("#### LLM")
    _render_llm_config(summary, state)
    st.divider()
    st.markdown("#### Telegram")
    _render_telegram_config(config, state)
    st.divider()
    st.markdown("#### VS Code")
    _render_vscode_config(config, state)
    st.divider()
    st.markdown("#### Workspace")
    _render_workspace_config(config, state)
    st.divider()
    st.markdown("#### Computer Use")
    _render_computer_use_config(summary, state)
    with st.expander("Effective Config", expanded=False):
        try:
            effective = _api_json(state["backend_url"], "/admin/api/config/effective", state["token"])
            _wrapped_json(effective)
        except ApiError as exc:
            st.error(str(exc))
            _wrapped_json(config)


def _render_access_config(summary: dict[str, Any], state: dict[str, str]) -> None:
    access_modes = summary.get("access_modes") or {}
    _render_kill_switch(access_modes, state)
    for name, item in access_modes.items():
        options = item.get("options") or [
            {"value": "off", "label": "Off"},
            {"value": "read_only", "label": "Read-only"},
            {"value": "write_access", "label": "Write with approval"},
            {"value": "full_access", "label": "Full access"},
        ]
        current = str(item.get("mode") or "off")
        values = [str(option["value"]) for option in options]
        labels = {str(option["value"]): str(option.get("label") or option["value"]) for option in options}
        if current not in values:
            current = values[0]
        current_label = labels.get(current, current)
        capabilities = ", ".join(item.get("capabilities") or [])

        with st.container(border=True):
            st.markdown(
                (
                    '<div class="access-card-head">'
                    f'<div><strong>{html_escape(str(item.get("label") or name))}</strong>'
                    f'<span class="capability-list">{html_escape(capabilities)}</span></div>'
                    f'<span class="mode-pill {_access_mode_class(current)}">{html_escape(current_label)}</span>'
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
            selected = st.radio(
                f"{item.get('label') or name} access mode",
                values,
                index=values.index(current),
                format_func=lambda value, labels=labels: labels.get(str(value), str(value)),
                horizontal=True,
                label_visibility="collapsed",
                key=f"access-mode-{name}-{current}",
            )
            if str(selected) != current:
                modes = {group_name: str(group.get("mode") or "off") for group_name, group in access_modes.items()}
                modes[name] = str(selected)
                _post_feedback(
                    state,
                    "/admin/api/config/access-modes",
                    {"modes": modes},
                    f"{item.get('label') or name} set to {labels.get(str(selected), str(selected))}.",
                )


def _render_kill_switch(access_modes: dict[str, Any], state: dict[str, str]) -> None:
    already_off = access_modes and all(str(item.get("mode") or "off") == "off" for item in access_modes.values())
    with st.container(border=True):
        st.markdown("**Kill switch**")
        st.caption("Sets every access group below to Off in one action. The worker keeps running but every gated capability stops being usable until you turn groups back on.")
        confirm = st.checkbox("I understand this disables every capability", key="confirm-kill-switch", disabled=already_off)
        if st.button("Disable everything now", type="primary", disabled=not confirm or already_off):
            _post_feedback(
                state,
                "/admin/api/config/access-modes",
                {"modes": {name: "off" for name in access_modes}},
                "All access groups set to Off.",
            )


def _render_llm_config(summary: dict[str, Any], state: dict[str, str]) -> None:
    config = summary.get("config") or {}
    llm = config.get("llm") or {}
    integrations = summary.get("integrations") or {}
    presets = ((integrations.get("llm") or {}).get("presets") or [])
    if presets:
        preset_columns = st.columns(len(presets))
        for index, preset in enumerate(presets):
            label = preset.get("label") or preset["key"]
            active = bool(preset.get("active"))
            if preset_columns[index].button(
                label,
                type="primary" if active else "secondary",
                disabled=active,
                use_container_width=True,
                key=f"llm-preset-{preset['key']}",
            ):
                _post_feedback(state, "/admin/api/config/llm/preset", {"preset": preset["key"]}, "LLM preset saved. Restart long-running processes.")
    if st.button("Test active LLM", use_container_width=True):
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


def _render_computer_use_config(summary: dict[str, Any], state: dict[str, str]) -> None:
    config = summary.get("config") or {}
    computer_use = ((config.get("adapters") or {}).get("computer_use") or {})
    tasks = summary.get("tasks") or []
    recent = next(
        (
            task
            for task in tasks
            if ((task.get("metadata") or {}).get("last_tool_name") == "computer.use")
            or (task.get("metadata") or {}).get("desktop_observation")
        ),
        None,
    )
    if recent:
        metadata = recent.get("metadata") or {}
        st.caption(f"Last computer-use task: {recent.get('status')} | {recent.get('objective')}")
        if metadata.get("screenshot_path"):
            _wrapped_text(metadata["screenshot_path"])
        if metadata.get("computer_use_actions"):
            st.caption(f"Actions recorded: {len(metadata.get('computer_use_actions') or [])}")
        if recent.get("status") in {"received", "planned", "awaiting_approval", "awaiting_external", "running", "retrying"}:
            if st.button("Stop active computer-use task", key="stop-computer-use-task"):
                _post_feedback(
                    state,
                    f"/admin/api/tasks/{recent.get('id')}/signals",
                    {"signal": "cancel"},
                    "Computer-use stop signal sent.",
                )

    with st.form("computer-use-config"):
        enabled = st.checkbox("Enabled", value=bool(computer_use.get("enabled")))
        require_session_approval = st.checkbox(
            "Require session approval",
            value=computer_use.get("require_session_approval") is not False,
        )
        max_steps = st.number_input("Max steps", min_value=1, max_value=50, value=int(computer_use.get("max_steps") or 8))
        step_delay_seconds = st.number_input(
            "Step delay seconds",
            min_value=0.0,
            max_value=10.0,
            value=float(computer_use.get("step_delay_seconds") if computer_use.get("step_delay_seconds") is not None else 0.4),
            step=0.1,
        )
        max_ui_elements = st.number_input(
            "Max UI elements",
            min_value=0,
            max_value=500,
            value=int(computer_use.get("max_ui_elements") or 80),
        )
        screenshot_dir = st.text_input(
            "Screenshot directory",
            computer_use.get("screenshot_dir") or ".agent_control/computer_use/screenshots",
        )
        allowed_roots = st.text_area(
            "Allowed roots",
            "\n".join(str(item) for item in computer_use.get("allowed_roots") or []),
            height=90,
        )
        allowed_apps = st.text_area(
            "Allowed apps",
            "\n".join(str(item) for item in computer_use.get("allowed_apps") or []),
            height=70,
        )
        submitted = st.form_submit_button("Save computer use config")
    if submitted:
        _post_feedback(
            state,
            "/admin/api/config/computer-use",
            {
                "enabled": enabled,
                "max_steps": int(max_steps),
                "step_delay_seconds": float(step_delay_seconds),
                "screenshot_dir": screenshot_dir,
                "allowed_apps": _parse_lines(allowed_apps),
                "allowed_roots": _parse_lines(allowed_roots),
                "require_session_approval": require_session_approval,
                "max_ui_elements": int(max_ui_elements),
            },
            "Computer use config saved. Restart worker to reload config.",
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
            _wrapped_json(event.get("details") or event)


def _render_diagnostics(summary: dict[str, Any], state: dict[str, str]) -> None:
    st.subheader("Diagnostics")
    c1, c2 = st.columns(2)
    with c1:
        _json_expander("VS Code Bridge", summary.get("vscode") or {})
    with c2:
        _json_expander("Database", summary.get("database") or {})
    schedules = summary.get("schedules") or {}
    if schedules:
        st.markdown("**Schedules**")
        schedule_items = schedules.get("items") or []
        if schedule_items:
            st.dataframe(_schedule_frame(schedule_items), hide_index=True, use_container_width=True)
        else:
            st.caption("No schedules configured.")
    registry = summary.get("tool_registry") or {}
    if registry:
        st.markdown("**Tool Registry**")
        st.dataframe(_tool_registry_frame(registry.get("tools") or []), hide_index=True, use_container_width=True)
    _json_expander("Service Supervisors", summary.get("services") or {})
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
        ("Worker service", _service_label(_services_by_name(summary).get("worker"))),
        ("Scheduler service", _service_label(_services_by_name(summary).get("scheduler"))),
        ("Telegram polling service", _service_label(_services_by_name(summary).get("telegram_polling"))),
        ("VS Code adapter", "enabled" if ((adapters.get("vscode") or {}).get("enabled")) else "disabled"),
        ("Workspace", (adapters.get("workspace") or {}).get("root_dir")),
        ("Adapter cache", (adapters.get("adapter_factory") or {}).get("root_dir")),
        ("Config file", (summary.get("admin") or {}).get("config_file")),
    ]
    return pd.DataFrame([{"Setting": key, "Value": value or "not configured"} for key, value in rows])


def _render_health(summary: dict[str, Any]) -> None:
    chips = []
    for item in _health_items(summary):
        chips.append(
            f'<span class="status-chip status-{item["state"]}">'
            f'<span class="status-dot"></span>'
            f'<span><strong>{html_escape(item["label"])}</strong>: {html_escape(item["value"])}</span>'
            f"</span>"
        )
    st.markdown(f'<div class="status-strip">{"".join(chips)}</div>', unsafe_allow_html=True)


def _health_items(summary: dict[str, Any]) -> list[dict[str, str]]:
    config = summary.get("config") or {}
    integrations = summary.get("integrations") or {}
    telegram = (integrations.get("telegram") or {})
    llm = (integrations.get("llm") or {})
    vscode = summary.get("vscode") or {}
    adapters = config.get("adapters") or {}
    workspace = adapters.get("workspace") or {}
    database = summary.get("database") or {}
    services = _services_by_name(summary)
    service_items = []
    for service in services.values():
        state = _service_state(service)
        service_items.append(
            {
                "label": service.get("name", "service").replace("_", " ").title(),
                "value": _service_label(service),
                "state": state,
            }
        )

    return [
        {"label": "Backend", "value": "online", "state": "ok"},
        {
            "label": "LLM",
            "value": str((config.get("llm") or {}).get("default_profile") or "missing"),
            "state": "ok" if llm.get("default_profile_configured") else "bad",
        },
        {
            "label": "Telegram",
            "value": "ready" if telegram.get("enabled") and telegram.get("token_present") else "needs token/config",
            "state": "ok" if telegram.get("enabled") and telegram.get("token_present") else "bad",
        },
        {
            "label": "VS Code",
            "value": _vscode_status_label(vscode),
            "state": "ok" if vscode.get("connected") else "bad",
        },
        {
            "label": "Workspace",
            "value": str(workspace.get("root_dir") or "missing"),
            "state": "ok" if workspace.get("enabled") is not False and workspace.get("root_dir") else "bad",
        },
        {
            "label": "Database",
            "value": "ready" if database.get("path") or database.get("database_url") else "unknown",
            "state": "ok" if database.get("path") or database.get("database_url") else "bad",
        },
        *service_items,
    ]


def _services_by_name(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("name")): item
        for item in ((summary.get("services") or {}).get("items") or [])
        if isinstance(item, dict) and item.get("name")
    }


def _service_label(service: dict[str, Any] | None) -> str:
    if not service:
        return "Unknown"
    if not service.get("expected"):
        return "Disabled"
    status = str(service.get("status") or "missing").replace("_", " ")
    age = service.get("age_seconds")
    suffix = f", {age}s ago" if age is not None else ""
    return f"{status.title()}{suffix}"


def _service_state(service: dict[str, Any] | None) -> str:
    if not service:
        return "bad"
    if not service.get("expected"):
        return "ok"
    if service.get("ok"):
        return "ok"
    if service.get("status") in {"starting", "exited"}:
        return "warn"
    return "bad"


def _service_frame(items: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in items:
        rows.append(
            {
                "Service": str(item.get("name") or "").replace("_", " ").title(),
                "Expected": "yes" if item.get("expected") else "no",
                "Status": _service_label(item),
                "Restarts": item.get("restart_count") or 0,
                "Supervisor PID": item.get("supervisor_pid") or "",
                "Child PID": item.get("child_pid") or "",
                "Message": item.get("message") or "",
            }
        )
    return pd.DataFrame(rows)


def _schedule_frame(items: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in items:
        rows.append(
            {
                "Status": item.get("status"),
                "Cadence": item.get("cadence"),
                "Next Run": item.get("next_run_at"),
                "Last Run": item.get("last_run_at") or "",
                "Objective": item.get("objective"),
                "ID": item.get("id"),
            }
        )
    return pd.DataFrame(rows)


def _tool_registry_frame(items: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in items:
        rows.append(
            {
                "Group": item.get("group"),
                "Tool": item.get("name"),
                "Enabled": "yes" if item.get("enabled") else "no",
                "Capability": item.get("capability"),
                "Operations": ", ".join(item.get("operations") or []),
            }
        )
    return pd.DataFrame(rows)


def _vscode_status_label(vscode: dict[str, Any]) -> str:
    status = str(vscode.get("status") or "")
    if vscode.get("connected"):
        return "Connected"
    if status == "stale":
        age = vscode.get("last_seen_age_seconds")
        return f"Stale ({age}s)" if age is not None else "Stale"
    return "Not connected"


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


def _trace_timeline_frame(trace: dict[str, Any]) -> pd.DataFrame:
    return _timeline_frame(_trace_timeline_rows(trace))


def _trace_timeline_rows(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return list(trace.get("timeline") or [])


def _timeline_frame(timeline: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in timeline:
        rows.append(
            {
                "Time": item.get("at") or "",
                "Kind": item.get("kind") or "",
                "Title": item.get("title") or "",
                "Actor": item.get("actor") or "",
                "Source": _timeline_source(item),
                "Next": _timeline_next(item),
                "Prompt / Payload": _timeline_prompt_text(item),
                "Summary": item.get("summary") or "",
                "Details": _timeline_detail_text(item),
            }
        )
    return pd.DataFrame(rows)


def _timeline_source(item: dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    actor = item.get("actor") or ""
    if item.get("kind") == "plan step":
        return "plan"
    if item.get("kind") == "tool":
        request_payload = details.get("request") if isinstance(details.get("request"), dict) else {}
        return str(request_payload.get("tool_name") or actor or "orchestrator")
    if details.get("sender_id"):
        return f"telegram:{details.get('sender_id')}"
    if details.get("source"):
        return str(details["source"])
    return str(actor or item.get("kind") or "")


def _timeline_next(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").lower()
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    if item.get("kind") == "plan step":
        step = details.get("step") if isinstance(details.get("step"), dict) else {}
        return str(step.get("tool_name") or "plan only")
    if item.get("kind") == "tool":
        result_payload = details.get("result") if isinstance(details.get("result"), dict) else {}
        status = result_payload.get("status") or item.get("summary")
        return f"worker result: {status}" if status else "worker result"
    if "telegram message received" in title:
        return "classifier"
    if "message classified" in title:
        return "task spawn" if details.get("is_task") else "direct response"
    if "task spawned" in title:
        return "worker planner"
    if "plan" in title:
        return "tool executor"
    if "state" in title:
        return str(details.get("new_status") or details.get("status") or "state updated")
    if details.get("tool_name"):
        return str(details["tool_name"])
    return ""


def _timeline_prompt_text(item: dict[str, Any]) -> str:
    details = item.get("details")
    if not isinstance(details, dict):
        return ""

    prompts: list[str] = []
    llm = details.get("llm")
    if isinstance(llm, dict):
        _add_prompt(prompts, "system", llm.get("system_prompt"))
        _add_prompt(prompts, "user", llm.get("user_prompt"))

    request_payload = details.get("request") if isinstance(details.get("request"), dict) else {}
    request_input = request_payload.get("input") if isinstance(request_payload.get("input"), dict) else {}
    _add_prompt(prompts, "prompt", request_input.get("prompt"))
    _add_prompt(prompts, "command", request_input.get("command"))
    _add_prompt(prompts, "objective", request_input.get("objective"))
    _add_prompt(prompts, "source_text", request_input.get("source_text"))

    step = details.get("step") if isinstance(details.get("step"), dict) else {}
    step_input = step.get("tool_input") if isinstance(step.get("tool_input"), dict) else {}
    _add_prompt(prompts, "step_input", step_input)

    _add_prompt(prompts, "message", details.get("text") or details.get("text_preview"))
    _add_prompt(prompts, "objective", details.get("objective") or details.get("normalized_objective"))

    if not prompts and isinstance(details.get("plan"), dict):
        plan = details["plan"]
        _add_prompt(prompts, "objective", plan.get("objective"))
        steps = plan.get("steps") or []
        if isinstance(steps, list):
            _add_prompt(prompts, "steps", "; ".join(
                f"{step.get('title')} -> {step.get('tool_name')}" for step in steps if isinstance(step, dict)
            ))

    return "\n".join(prompts[:6])


def _add_prompt(lines: list[str], label: str, value: Any) -> None:
    if value is None or value == "":
        return
    lines.append(f"{label}: {_clip_text(value, 520)}")


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

    step = details.get("step") if isinstance(details.get("step"), dict) else {}
    if step:
        _add_line(lines, "step", f"{details.get('step_index')}. {step.get('title') or 'Step'}")
        _add_line(lines, "tool", step.get("tool_name"))
        _add_line(lines, "risk", step.get("risk_level"))
        _add_line(lines, "capabilities", ", ".join(step.get("required_capabilities") or []))
        _add_line(lines, "expected", step.get("expected_output"))

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
    postconditions = plan.get("postconditions") or []
    if isinstance(postconditions, list) and postconditions:
        labels = [
            str(item.get("type") or item.get("description") or "postcondition")
            for item in postconditions
            if isinstance(item, dict)
        ]
        if labels:
            _add_line(lines, "postconditions", ", ".join(labels[:6]))
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


def _task_links(task: dict[str, Any]) -> list[tuple[str, str]]:
    metadata = task.get("metadata") or {}
    output = ((metadata.get("last_tool_result") or {}).get("output") or {})
    links = []
    preview = output.get("url") or metadata.get("preview_url")
    browser_url = output.get("browser_url") or metadata.get("browser_url")
    workspace = output.get("workspace_dir") or metadata.get("workspace_dir")
    adapter_dir = output.get("adapter_dir") or metadata.get("adapter_dir")
    screenshot = output.get("screenshot_uri") or output.get("screenshot_path") or metadata.get("screenshot_uri") or metadata.get("screenshot_path")
    if preview:
        links.append(("Open preview", str(preview)))
    if browser_url and browser_url != preview:
        links.append(("Browser page", str(browser_url)))
    if screenshot:
        links.append(("Screenshot", str(screenshot)))
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
        "awaiting_external": "Waiting external",
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


def _access_mode_class(value: str) -> str:
    if value == "full_access":
        return "mode-full"
    if value == "write_access":
        return "mode-write"
    if value == "read_only":
        return "mode-read"
    return "mode-off"


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _wrapped_json(value: Any) -> None:
    _wrapped_text(_json_text(value), css_class="wrapped-json")


def _wrapped_text(value: Any, *, css_class: str = "wrapped-text") -> None:
    st.markdown(
        f'<pre class="{css_class}">{html_escape(str(value))}</pre>',
        unsafe_allow_html=True,
    )


def _json_expander(label: str, value: Any, *, expanded: bool = False) -> None:
    with st.expander(label, expanded=expanded):
        _wrapped_json(value)


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


def _parse_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


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
        div[data-testid="stRadio"] [role="radiogroup"] {
          display: flex;
          flex-direction: row;
          flex-wrap: wrap;
          gap: 4px 14px;
        }
        div[data-testid="stRadio"] label {
          margin-bottom: 0.1rem;
        }
        .access-card-head {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          gap: 10px;
          margin-bottom: 0.35rem;
        }
        .capability-list {
          display: block;
          color: #6b7280;
          font-size: 0.76rem;
          line-height: 1.25;
          overflow-wrap: anywhere;
          margin-top: 1px;
        }
        .mode-pill {
          border-radius: 999px;
          padding: 3px 9px;
          border: 1px solid rgba(49, 51, 63, 0.18);
          font-size: 0.74rem;
          font-weight: 700;
          white-space: nowrap;
        }
        .mode-off { color: #4b5563; background: #f3f4f6; border-color: #d1d5db; }
        .mode-read { color: #075985; background: #e0f2fe; border-color: #7dd3fc; }
        .mode-write { color: #92400e; background: #fef3c7; border-color: #fbbf24; }
        .mode-full { color: #166534; background: #dcfce7; border-color: #86efac; }
        .status-strip {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin: 0.4rem 0 1rem;
        }
        .status-chip {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          border: 1px solid rgba(49, 51, 63, 0.18);
          border-radius: 999px;
          padding: 6px 10px;
          font-size: 0.85rem;
          max-width: 100%;
          overflow-wrap: anywhere;
        }
        .status-dot {
          width: 9px;
          height: 9px;
          border-radius: 999px;
          flex: 0 0 auto;
          background: #9ca3af;
        }
        .status-ok .status-dot { background: #16a34a; }
        .status-ok { border-color: rgba(22, 163, 74, 0.35); background: rgba(22, 163, 74, 0.08); }
        .status-bad .status-dot { background: #dc2626; }
        .status-bad { border-color: rgba(220, 38, 38, 0.35); background: rgba(220, 38, 38, 0.08); }
        .status-warn .status-dot { background: #d97706; }
        .status-warn { border-color: rgba(217, 119, 6, 0.35); background: rgba(217, 119, 6, 0.08); }
        .wrapped-json,
        .wrapped-text {
          white-space: pre-wrap;
          overflow-wrap: anywhere;
          word-break: break-word;
          overflow-x: hidden;
          max-width: 100%;
          max-height: 520px;
          overflow-y: auto;
          border: 1px solid rgba(49, 51, 63, 0.18);
          border-radius: 8px;
          padding: 10px 12px;
          background: rgba(250, 250, 250, 0.72);
          font-size: 0.82rem;
          line-height: 1.35;
        }
        .stTabs [data-baseweb="tab-list"] { gap: 6px; }
        .stTabs [data-baseweb="tab"] {
          border-radius: 6px;
          padding: 8px 12px;
          border: 1px solid rgba(49, 51, 63, 0.12);
        }
        .live-output-text {
          white-space: pre-wrap;
          overflow-wrap: anywhere;
          word-break: break-word;
          max-height: 160px;
          overflow-y: auto;
          border: 1px solid rgba(9, 105, 218, 0.3);
          border-radius: 6px;
          padding: 8px 12px;
          background: rgba(9, 105, 218, 0.04);
          font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
          font-size: 0.81rem;
          line-height: 1.4;
          color: #374151;
          margin-top: 6px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
