from __future__ import annotations

import inspect
from pathlib import Path

from streamlit.testing.v1 import AppTest

from agent_control import admin_streamlit


def test_streamlit_admin_has_no_legacy_admin_link() -> None:
    # admin.py's ~1,300-line embedded HTML admin was deleted (docs/ROADMAP.md
    # P4) - Streamlit is the one real admin UI now, so a button pointing back
    # to it would be circular. st.link_button isn't inspectable through
    # AppTest's structured widget API, so check the source directly.
    source = inspect.getsource(admin_streamlit)
    assert "Legacy admin" not in source
    assert "Legacy FastAPI admin" not in source
    assert not hasattr(admin_streamlit, "_legacy_admin_url")


def test_streamlit_admin_helpers_parse_ids_and_status_labels() -> None:
    assert admin_streamlit._parse_csv_ints("123, bad, 456") == [123, 456]
    assert admin_streamlit._activity_label("awaiting_approval") == "Waiting approval"
    assert admin_streamlit._activity_label("running") == "Running"
    assert admin_streamlit._summary_path(15) == "/admin/api/summary?task_limit=15"
    assert admin_streamlit._tasks_path(20, 5) == "/admin/api/tasks?limit=20&offset=5"
    assert admin_streamlit._audit_path(25, "tool", "copilot") == "/admin/api/audit?limit=25&category=tool&q=copilot"
    assert admin_streamlit._access_mode_class("full_access") == "mode-full"
    assert admin_streamlit._access_mode_class("write_access") == "mode-write"
    assert admin_streamlit._access_mode_class("read_only") == "mode-read"
    assert admin_streamlit._access_mode_class("off") == "mode-off"
    assert admin_streamlit._parse_lines(" C:/work \n\nC:/tmp ") == ["C:/work", "C:/tmp"]


def test_streamlit_admin_extracts_task_links_and_tool_output() -> None:
    task = {
        "metadata": {
            "preview_url": "http://127.0.0.1:8890/",
            "workspace_dir": "C:/tmp/workspace",
            "last_tool_result": {
                "output": {
                    "terminal_output": [
                        {"content": "created files"},
                        {"content": "served app"},
                    ]
                }
            },
        }
    }
    result = task["metadata"]["last_tool_result"]

    assert ("Open preview", "http://127.0.0.1:8890/") in admin_streamlit._task_links(task)
    assert ("Workspace", "C:/tmp/workspace") in admin_streamlit._task_links(task)
    assert admin_streamlit._terminal_output_text(result) == "created files\n\nserved app"


def test_streamlit_admin_timeline_details_are_human_readable() -> None:
    timeline = [
        {
            "at": "2026-05-18 04:32:48 UTC",
            "kind": "audit",
            "title": "Plan Created",
            "summary": "plan_created",
            "details": {
                "config_context": "very long hidden planner context",
                "source": "default_vscode_development_plan",
                "plan": {
                    "id": "plan_1",
                    "objective": "Create app",
                    "steps": [
                        {"title": "Prepare workspace", "tool_name": "workspace.manage"},
                        {"title": "Ask Copilot", "tool_name": "vscode.copilot_terminal"},
                    ],
                    "postconditions": [
                        {"type": "preview_url", "description": "A local preview URL is reported."},
                        {"type": "workspace_dir", "description": "A task workspace directory is reported."},
                    ],
                },
            },
        },
        {
            "at": "2026-05-18T04:33:00Z",
            "kind": "tool",
            "title": "workspace.manage",
            "summary": "succeeded",
            "details": {
                "request": {
                    "tool_name": "workspace.manage",
                    "capability": "filesystem.write",
                    "input": {"operation": "launch_static", "prompt": "Launch the preview server"},
                },
                "result": {
                    "status": "succeeded",
                    "output": {"url": "http://127.0.0.1:8890/", "files": ["index.html"]},
                },
            },
        },
    ]

    frame = admin_streamlit._timeline_frame(timeline)

    assert "steps: 1. Prepare workspace -> workspace.manage; 2. Ask Copilot -> vscode.copilot_terminal" in frame.iloc[0]["Details"]
    assert "postconditions: preview_url, workspace_dir" in frame.iloc[0]["Details"]
    assert "very long hidden planner context" not in frame.iloc[0]["Details"]
    assert frame.iloc[0]["Source"] == "default_vscode_development_plan"
    assert frame.iloc[0]["Next"] == "tool executor"
    assert "operation: launch_static" in frame.iloc[1]["Details"]
    assert "url: http://127.0.0.1:8890/" in frame.iloc[1]["Details"]
    assert frame.iloc[1]["Source"] == "workspace.manage"
    assert frame.iloc[1]["Next"] == "worker result: succeeded"
    assert "prompt: Launch the preview server" in frame.iloc[1]["Prompt / Payload"]

    trace_frame = admin_streamlit._trace_timeline_frame(
        {
            "timeline": timeline,
            "plan": {
                "steps": [
                    {
                        "title": "Prepare workspace",
                        "description": "Create the task workspace",
                        "tool_name": "workspace.manage",
                        "risk_level": "high",
                        "required_capabilities": ["filesystem.write"],
                        "tool_input": {"operation": "prepare"},
                        "expected_output": "workspace path",
                    }
                ]
            },
        }
    )
    plan_step = trace_frame[trace_frame["Kind"] == "plan step"].iloc[0]
    assert plan_step["Title"] == "1. Prepare workspace"
    assert plan_step["Source"] == "plan"
    assert plan_step["Next"] == "workspace.manage"
    assert "step_input:" in plan_step["Prompt / Payload"]
    assert "capabilities: filesystem.write" in plan_step["Details"]


def test_streamlit_admin_action_disabled_rules() -> None:
    assert admin_streamlit._action_disabled({"status": "completed"}, "pause") is True
    assert admin_streamlit._action_disabled({"status": "paused"}, "resume") is False
    assert admin_streamlit._action_disabled({"status": "running"}, "resume") is True


def test_streamlit_admin_formats_task_options_and_api_errors() -> None:
    task = {"status": "running", "objective": "Create a detailed local workspace app"}

    assert admin_streamlit._task_option_label(task) == "Running | Create a detailed local workspace app"
    assert admin_streamlit._api_error_detail('{"detail":"terminal.run capability is disabled"}') == "terminal.run capability is disabled"
    assert admin_streamlit._api_error_detail("plain failure") == "plain failure"


def test_streamlit_admin_health_and_connection_helpers() -> None:
    summary = {
        "config": {
            "llm": {"default_profile": "localdeploy_gemma3_4b"},
            "adapters": {"workspace": {"enabled": True, "root_dir": ".agent_control/workspaces"}},
        },
        "integrations": {
            "telegram": {"enabled": True, "token_present": True},
            "llm": {"default_profile_configured": True},
        },
        "vscode": {"connected": False, "status": "waiting"},
        "database": {"path": "agent_control.db"},
    }

    items = {item["label"]: item for item in admin_streamlit._health_items(summary)}

    assert items["Backend"]["state"] == "ok"
    assert items["VS Code"]["state"] == "bad"
    runtime = admin_streamlit._runtime_rows({"config": summary["config"], "integrations": summary["integrations"], "services": {"items": [{"name": "scheduler", "expected": True, "ok": True, "status": "running", "age_seconds": 1}]}})
    assert "Scheduler service" in set(runtime["Setting"])
    assert admin_streamlit._vscode_status_label({"connected": True}) == "Connected"
    assert admin_streamlit._vscode_status_label({"connected": False, "status": "stale", "last_seen_age_seconds": 120}) == "Stale (120s)"


def test_streamlit_admin_schedule_and_registry_frames() -> None:
    schedules = admin_streamlit._schedule_frame(
        [
            {
                "id": "schedule_1",
                "status": "enabled",
                "cadence": "daily",
                "next_run_at": "2026-05-22T00:00:00+00:00",
                "objective": "check example.com",
            }
        ]
    )
    registry = admin_streamlit._tool_registry_frame(
        [
            {
                "group": "schedules",
                "name": "schedule.manage",
                "enabled": True,
                "capability": "schedule.manage",
                "operations": ["create", "list"],
            }
        ]
    )

    assert schedules.iloc[0]["ID"] == "schedule_1"
    assert registry.iloc[0]["Tool"] == "schedule.manage"
    assert registry.iloc[0]["Operations"] == "create, list"


def test_streamlit_admin_smoke_renders_without_backend(tmp_path: Path) -> None:
    app_file = tmp_path / "streamlit_smoke.py"
    app_file.write_text(
        '''
from agent_control import admin_streamlit


def fake_api_json(backend_url, path, token, method="GET", payload=None):
    summary = {
        "config": {
            "identity": {"instance_name": "test-agent"},
            "channels": {"telegram": {"enabled": True, "token_env": "TELEGRAM_BOT_TOKEN", "allowed_user_ids": [], "allowed_chat_ids": [], "polling": True}},
            "llm": {"default_profile": "local", "profiles": {"local": {"provider": "openai_compatible", "model": "gemma", "timeout_seconds": 180, "max_tokens": 1024, "temperature": 0.2}}},
            "adapters": {
                "workspace": {"enabled": True, "root_dir": ".agent_control/workspaces", "web_host": "127.0.0.1", "web_port_start": 8890, "open_browser": True},
                "vscode": {"enabled": True, "bridge_host": "127.0.0.1", "bridge_port": 8766, "auth_token_env": "VSCODE_BRIDGE_TOKEN"},
                "adapter_factory": {"root_dir": ".agent_control/adapters"},
                "computer_use": {"enabled": True, "max_steps": 8, "screenshot_dir": ".agent_control/computer_use/screenshots", "allowed_roots": [".agent_control/workspaces"], "allowed_apps": []},
            },
            "server": {},
            "storage": {},
            "limits": {},
        },
        "tasks": [
            {"id": "task_1", "status": "completed", "objective": "Create app", "updated_at": "2026-05-17T00:00:00+00:00", "metadata": {"task_type": "development", "preview_url": "http://127.0.0.1:8890/"}}
        ],
        "task_pagination": {"total": 1, "has_more": False},
        "audit": [],
        "vscode": {"connected": False, "state": None, "heartbeat": None, "terminal_outputs": []},
        "access_modes": {
            "filesystem": {
                "label": "File system",
                "mode": "full_access",
                "capabilities": ["filesystem.read", "filesystem.write"],
                "options": [
                    {"value": "off", "label": "Off"},
                    {"value": "read_only", "label": "Read-only"},
                    {"value": "write_access", "label": "Write with approval"},
                    {"value": "full_access", "label": "Full access"},
                ],
            },
            "vscode": {
                "label": "VS Code",
                "mode": "full_access",
                "capabilities": ["vscode.read_state", "vscode.write_files"],
                "options": [
                    {"value": "off", "label": "Off"},
                    {"value": "read_only", "label": "Read-only"},
                    {"value": "write_access", "label": "Write with approval"},
                    {"value": "full_access", "label": "Full access"},
                ],
            },
        },
        "warnings": [],
        "database": {"path": "agent_control.db"},
        "integrations": {"telegram": {"enabled": True, "allowed_user_count": 0}, "llm": {"presets": []}},
        "admin": {"token_required": False, "config_file": "config/config.yaml"},
    }
    if path.startswith("/admin/api/tasks/task_1/trace"):
        return {
            "task": summary["tasks"][0],
            "context": {},
            "plan": {"steps": [{"title": "Prepare", "description": "Create workspace.", "tool_name": "workspace.manage", "risk_level": "high", "required_capabilities": ["filesystem.write"], "tool_input": {"operation": "prepare"}}]},
            "tool_invocations": [{"id": "tool_1", "tool_name": "workspace.manage", "status": "succeeded", "capability": "filesystem.write", "created_at": "now", "completed_at": "now", "request": {"input": {"operation": "prepare"}}, "result": {"output": {"terminal_output": [{"content": "done"}]}}}],
            "approvals": [],
            "signals": [],
            "artifacts": [],
            "audit": [],
            "timeline": [],
        }
    if path.startswith("/admin/api/tasks"):
        return {"tasks": summary["tasks"], "pagination": {"total": 1, "has_more": False}}
    if path.startswith("/admin/api/audit"):
        return {"events": []}
    if path.startswith("/admin/api/config/effective"):
        return {"config": summary["config"], "access_modes": summary["access_modes"], "warnings": []}
    return summary


admin_streamlit._api_json = fake_api_json
admin_streamlit.main()
''',
        encoding="utf-8",
    )

    app = AppTest.from_file(str(app_file))
    app.run(timeout=10)

    assert not app.exception
    assert app.markdown[0].value
    assert any("File system" in item.value for item in app.markdown)
