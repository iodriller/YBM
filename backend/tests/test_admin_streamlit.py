from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from agent_control import admin_streamlit


def test_streamlit_admin_helpers_parse_ids_and_status_labels() -> None:
    assert admin_streamlit._parse_csv_ints("123, bad, 456") == [123, 456]
    assert admin_streamlit._activity_label("awaiting_approval") == "Waiting approval"
    assert admin_streamlit._activity_label("running") == "Running"
    assert admin_streamlit._summary_path(15) == "/admin/api/summary?task_limit=15"
    assert admin_streamlit._tasks_path(20, 5) == "/admin/api/tasks?limit=20&offset=5"
    assert admin_streamlit._audit_path(25, "tool", "copilot") == "/admin/api/audit?limit=25&category=tool&q=copilot"
    assert admin_streamlit._legacy_admin_url("http://127.0.0.1:8765", "secret token") == "http://127.0.0.1:8765/admin?token=secret+token"


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


def test_streamlit_admin_action_disabled_rules() -> None:
    assert admin_streamlit._action_disabled({"status": "completed"}, "pause") is True
    assert admin_streamlit._action_disabled({"status": "paused"}, "resume") is False
    assert admin_streamlit._action_disabled({"status": "running"}, "resume") is True


def test_streamlit_admin_formats_task_options_and_api_errors() -> None:
    task = {"status": "running", "objective": "Create a detailed local workspace app"}

    assert admin_streamlit._task_option_label(task) == "Running | Create a detailed local workspace app"
    assert admin_streamlit._api_error_detail('{"detail":"terminal.run capability is disabled"}') == "terminal.run capability is disabled"
    assert admin_streamlit._api_error_detail("plain failure") == "plain failure"


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
        "access_modes": {},
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
        return {"config": summary["config"], "access_modes": {}, "warnings": []}
    return summary


admin_streamlit._api_json = fake_api_json
admin_streamlit.main()
''',
        encoding="utf-8",
    )

    app = AppTest.from_file(str(app_file))
    app.run(timeout=10)

    assert not app.exception
