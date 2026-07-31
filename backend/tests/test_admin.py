from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from agent_control.admin import create_admin_router
from agent_control.config import AppSettings, default_capability_policies
from agent_control.main import app
from datetime import timedelta

from agent_control.schemas import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEventType,
    Capability,
    CapabilityAccessMode,
    RiskLevel,
    ScheduleRecord,
    TaskStatus,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
    utc_now,
)
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.vscode_bridge import VSCodeBridgeStore


def _repositories(database_url: str) -> Repositories:
    database = Database(database_url)
    database.initialize()
    return Repositories.for_database(database)


def test_admin_page_points_to_streamlit(monkeypatch, tmp_path) -> None:
    # chdir, not just delenv: read_env_value() reads .env from the current
    # working directory, so a bare delenv here is not real isolation.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_STORAGE__DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    client = TestClient(app)

    page = client.get("/admin")

    # The Streamlit app (admin_streamlit.py) is the one real admin UI
    # (docs/HISTORY.md P4) - /admin is now just a small pointer to it, not a
    # second console. Assert it stays small and points at Streamlit, rather
    # than reintroducing the ~1,300-line embedded SPA this replaced.
    assert page.status_code == 200
    assert "Agent Control Admin" in page.text
    assert "8501" in page.text
    assert len(page.text) < 2000
    assert "onclick=" not in page.text
    assert "task-card" not in page.text


def test_admin_summary_api_unaffected_by_html_page_removal(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_points_to_streamlit for why chdir, not just delenv
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_STORAGE__DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    client = TestClient(app)

    summary = client.get("/admin/api/summary")

    assert summary.status_code == 200
    assert summary.json()["config"]["identity"]["instance_name"] == "local-agent-control"
    assert "services" in summary.json()
    assert "schedules" in summary.json()
    assert "tool_registry" in summary.json()


def test_admin_fails_closed_on_non_loopback_host_without_token(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_SERVER__HOST", "0.0.0.0")
    monkeypatch.setenv("AGENT_STORAGE__DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    client = TestClient(app)

    summary = client.get("/admin/api/summary")

    assert summary.status_code == 503


def test_admin_rejects_cross_origin_request_even_without_token(monkeypatch, tmp_path) -> None:
    # The exploitable case: no token configured (the common local, convenient
    # setup), host is loopback (the default) - require_admin's token/host
    # checks alone would let this through. A malicious site the admin's
    # browser visits could otherwise trigger this exact request against
    # 127.0.0.1 without ever needing to read the response.
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_STORAGE__DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    client = TestClient(app)

    response = client.get("/admin/api/summary", headers={"origin": "http://evil.example"})

    assert response.status_code == 403


def test_admin_allows_same_origin_request_without_token(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_STORAGE__DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    client = TestClient(app)

    # TestClient's default base_url makes same-origin requests carry
    # Origin: http://testserver against Host: testserver.
    response = client.get("/admin/api/summary", headers={"origin": "http://testserver"})

    assert response.status_code == 200


def test_admin_lists_tasks_and_audit(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    monkeypatch.setenv("AGENT_STORAGE__DATABASE_URL", database_url)
    repositories = _repositories(database_url)
    task = repositories.tasks.create("review admin dashboard")
    AuditLogger(repositories.audit).append(AuditEventType.TASK_CREATED, actor="test", task_id=task.id)
    client = TestClient(app)

    tasks = client.get("/admin/api/tasks").json()["tasks"]
    audit = client.get("/admin/api/audit").json()["events"]
    filtered = client.get("/admin/api/audit?category=spawned_task").json()["events"]

    assert tasks[0]["objective"] == "review admin dashboard"
    assert audit[0]["type"] == AuditEventType.TASK_CREATED.value
    assert audit[0]["category"] == "spawned_task"
    assert audit[0]["formatted_time"].endswith("UTC")
    assert filtered[0]["category"] == "spawned_task"


def test_admin_task_signal_updates_task(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    task = repositories.tasks.create("pause me")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(f"/admin/api/tasks/{task.id}/signals", json={"signal": "pause"})
    updated = repositories.tasks.get(task.id)

    assert response.status_code == 200
    assert updated is not None
    assert updated.status == TaskStatus.PAUSED


def _pending_approval(repositories, task_id: str) -> ApprovalRequest:
    return repositories.approvals.create(
        ApprovalRequest(
            task_id=task_id,
            capability=Capability.FILESYSTEM_WRITE,
            risk_level=RiskLevel.HIGH,
            summary="write a file",
            expires_at=utc_now() + timedelta(minutes=15),
        )
    )


def test_admin_pending_approvals_lists_only_pending(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    task = repositories.tasks.create("write a report", metadata={"source_chat_id": "1"})
    pending = _pending_approval(repositories, task.id)
    decided = _pending_approval(repositories, task.id)
    repositories.approvals.set_status(decided.id, ApprovalStatus.APPROVED)
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(lambda: AppSettings(_env_file=None), lambda: repositories, VSCodeBridgeStore())
    )
    client = TestClient(local_app)

    response = client.get("/admin/api/approvals")

    assert response.status_code == 200
    items = response.json()["approvals"]
    assert [item["approval"]["id"] for item in items] == [pending.id]
    assert items[0]["task_objective"] == "write a report"
    assert items[0]["task_status"] == TaskStatus.RECEIVED.value


def test_admin_decide_approval_approve_updates_status_and_audits(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    task = repositories.tasks.create("write a report")
    approval = _pending_approval(repositories, task.id)
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(lambda: AppSettings(_env_file=None), lambda: repositories, VSCodeBridgeStore())
    )
    client = TestClient(local_app)

    response = client.post(f"/admin/api/approvals/{approval.id}/decide", json={"decision": "approve"})

    assert response.status_code == 200
    assert response.json()["approval"]["status"] == ApprovalStatus.APPROVED.value
    assert repositories.approvals.get(approval.id).status == ApprovalStatus.APPROVED
    events = repositories.audit.list_for_task(task.id)
    assert any(event.type == AuditEventType.APPROVAL_DECIDED for event in events)


def test_admin_decide_approval_reject_updates_status(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    task = repositories.tasks.create("write a report")
    approval = _pending_approval(repositories, task.id)
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(lambda: AppSettings(_env_file=None), lambda: repositories, VSCodeBridgeStore())
    )
    client = TestClient(local_app)

    response = client.post(f"/admin/api/approvals/{approval.id}/decide", json={"decision": "reject"})

    assert response.status_code == 200
    assert response.json()["approval"]["status"] == ApprovalStatus.REJECTED.value
    assert repositories.approvals.get(approval.id).status == ApprovalStatus.REJECTED


def test_admin_decide_approval_404_for_unknown_id(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(lambda: AppSettings(_env_file=None), lambda: repositories, VSCodeBridgeStore())
    )
    client = TestClient(local_app)

    response = client.post("/admin/api/approvals/approval_does_not_exist/decide", json={"decision": "approve"})

    assert response.status_code == 404


def test_admin_decide_approval_409_when_already_decided(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    task = repositories.tasks.create("write a report")
    approval = _pending_approval(repositories, task.id)
    repositories.approvals.set_status(approval.id, ApprovalStatus.APPROVED)
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(lambda: AppSettings(_env_file=None), lambda: repositories, VSCodeBridgeStore())
    )
    client = TestClient(local_app)

    response = client.post(f"/admin/api/approvals/{approval.id}/decide", json={"decision": "reject"})

    assert response.status_code == 409


def test_admin_task_trace_includes_operator_history_tool_calls_and_audit(monkeypatch, tmp_path) -> None:
    """The trace endpoint's execution record is operator_history + real
    tool_invocations now - PlanModel is dead (docs/HISTORY.md \u00a71.1), nothing
    creates one anymore."""
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    audit = AuditLogger(repositories.audit)
    task = repositories.tasks.create("create a test app")
    repositories.tasks.update_metadata(
        task.id,
        {
            "operator_history": [
                {
                    "tool_name": "vscode.copilot_terminal",
                    "input": {"prompt": "build the app", "cwd": "workspace"},
                    "status": "succeeded",
                    "output_summary": "created files",
                    "error": None,
                }
            ]
        },
    )
    request = ToolCallRequest(
        task_id=task.id,
        tool_name="vscode.copilot_terminal",
        capability=Capability.VSCODE_WRITE_FILES,
        input={"prompt": "build the app", "cwd": "workspace"},
    )
    repositories.tool_invocations.create(request)
    repositories.tool_invocations.complete(
        ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.SUCCEEDED,
            output={"terminal_output": [{"content": "created files"}]},
        )
    )
    audit.append(
        AuditEventType.TASK_STATE_CHANGED,
        actor="operator",
        task_id=task.id,
        payload={"action": "operator_decision", "decision": {"action": "call_tool", "tool_name": "vscode.copilot_terminal"}},
    )
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.get(f"/admin/api/tasks/{task.id}/trace")
    body = response.json()

    assert response.status_code == 200
    assert body["task"]["id"] == task.id
    assert "plan" not in body
    assert body["operator_history"][0]["tool_name"] == "vscode.copilot_terminal"
    assert body["operator_history"][0]["output_summary"] == "created files"
    assert body["tool_invocations"][0]["request"]["input"]["prompt"] == "build the app"
    assert body["tool_invocations"][0]["result"]["output"]["terminal_output"][0]["content"] == "created files"
    assert body["audit"][0]["details"]["action"] == "operator_decision"


def test_admin_task_trace_evidence_aggregates_files_urls_and_commands(monkeypatch, tmp_path) -> None:
    """The evidence view (docs/HISTORY.md N5): what a completed task actually
    touched, pulled from real tool_invocations rather than a new repository -
    files/urls/commands are found by known field name across every tool's
    input/output, deduplicated, so a task that wrote two files and visited
    one URL surfaces exactly that, not raw per-tool payload dumps."""
    monkeypatch.chdir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    task = repositories.tasks.create("write a report and check a page")

    write_request = ToolCallRequest(
        task_id=task.id,
        tool_name="filesystem.manage",
        capability=Capability.FILESYSTEM_WRITE,
        input={"operation": "write_text_file", "path": "C:/tmp/report.txt"},
    )
    repositories.tool_invocations.create(write_request)
    repositories.tool_invocations.complete(
        ToolCallResult(
            request_id=write_request.id,
            status=ToolResultStatus.SUCCEEDED,
            output={"changed_paths": ["C:/tmp/report.txt", "C:/tmp/report-backup.txt"]},
        )
    )

    browser_request = ToolCallRequest(
        task_id=task.id,
        tool_name="browser.open",
        capability=Capability.BROWSER_OPEN,
        input={"url": "https://example.com"},
    )
    repositories.tool_invocations.create(browser_request)
    repositories.tool_invocations.complete(
        ToolCallResult(
            request_id=browser_request.id,
            status=ToolResultStatus.SUCCEEDED,
            output={"url": "https://example.com", "visited_urls": ["https://example.com"]},
        )
    )

    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(lambda: AppSettings(_env_file=None), lambda: repositories, VSCodeBridgeStore())
    )
    client = TestClient(local_app)

    response = client.get(f"/admin/api/tasks/{task.id}/trace")
    evidence = response.json()["evidence"]

    assert response.status_code == 200
    assert [item["value"] for item in evidence["files"]] == ["C:/tmp/report.txt", "C:/tmp/report-backup.txt"]
    assert [item["value"] for item in evidence["urls"]] == ["https://example.com"]
    assert evidence["commands"] == []
    assert evidence["files"][0]["tool_name"] == "filesystem.manage"


def test_admin_clears_task_history_and_audit(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    completed = repositories.tasks.create("old task")
    repositories.tasks.update_status(completed.id, TaskStatus.COMPLETED)
    active = repositories.tasks.create("active task")
    AuditLogger(repositories.audit).append(AuditEventType.TASK_CREATED, actor="test", task_id=completed.id)
    AuditLogger(repositories.audit).append(AuditEventType.TASK_CREATED, actor="test", task_id=active.id)
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    clear_completed = client.delete("/admin/api/tasks?include_active=false")
    remaining_tasks = client.get("/admin/api/tasks?limit=10").json()["tasks"]
    clear_audit = client.delete("/admin/api/audit")

    assert clear_completed.status_code == 200
    assert clear_completed.json()["deleted_tasks"] == 1
    assert [task["id"] for task in remaining_tasks] == [active.id]
    assert clear_audit.status_code == 200
    assert repositories.audit.list_recent(10) == []


def test_admin_tasks_are_paginated(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    for index in range(7):
        repositories.tasks.create(f"task {index}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    first_page = client.get("/admin/api/tasks?limit=5").json()
    summary = client.get("/admin/api/summary?task_limit=5").json()

    assert len(first_page["tasks"]) == 5
    assert first_page["pagination"]["total"] == 7
    assert first_page["pagination"]["has_more"] is True
    assert summary["task_pagination"]["has_more"] is True


def test_admin_task_resume_restores_paused_status(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    task = repositories.tasks.create("resume me")
    task = repositories.tasks.update_status(task.id, TaskStatus.RUNNING)
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    paused = client.post(f"/admin/api/tasks/{task.id}/signals", json={"signal": "pause"})
    resumed = client.post(f"/admin/api/tasks/{task.id}/signals", json={"signal": "resume"})
    updated = repositories.tasks.get(task.id)

    assert paused.status_code == 200
    assert resumed.status_code == 200
    assert updated is not None
    assert updated.status == TaskStatus.RUNNING


def test_admin_rejects_vscode_terminal_command_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    store = VSCodeBridgeStore()
    settings = AppSettings(_env_file=None, capabilities=default_capability_policies())
    local_app = FastAPI()
    local_app.include_router(create_admin_router(lambda: settings, lambda: repositories, store))
    client = TestClient(local_app)

    response = client.post("/admin/api/vscode/terminal-commands", json={"command": "echo blocked"})

    assert response.status_code == 403
    assert store.terminal_commands == []


def test_admin_can_queue_vscode_terminal_command_when_enabled(monkeypatch, tmp_path) -> None:
    # chdir before constructing AppSettings so the temp repo owns config.yaml
    # and any local .env file it reads.
    monkeypatch.chdir(tmp_path)
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    settings = AppSettings(
        _env_file=None,
        adapters={"vscode": {"enabled": True}},
        capabilities=default_capability_policies(),
    )
    settings.capabilities[Capability.TERMINAL_RUN].enabled = True
    settings.capabilities[Capability.TERMINAL_RUN].requires_approval = False
    store = VSCodeBridgeStore()
    local_app = FastAPI()
    local_app.include_router(create_admin_router(lambda: settings, lambda: repositories, store))
    client = TestClient(local_app)

    response = client.post("/admin/api/vscode/terminal-commands", json={"command": "echo hi"})

    assert response.status_code == 200
    assert store.terminal_commands[0].command == "echo hi"


def test_admin_writes_llm_runtime_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(
        "/admin/api/config/llm",
        json={
            "profile_name": "local",
            "default_profile": "local",
            "provider": "openai_compatible",
            "model": "local-coder",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key_env": "",
        },
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["llm"]["default_profile"] == "local"
    assert saved["llm"]["profiles"]["local"]["base_url"] == "http://127.0.0.1:1234/v1"
    assert saved["llm"]["profiles"]["local"]["api_key_env"] is None
    assert not (tmp_path / ".env").exists()


def test_admin_selects_llm_preset(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post("/admin/api/config/llm/preset", json={"preset": "localdeploy_gemma3_12b"})
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["llm"]["default_profile"] == "localdeploy_gemma3_12b"
    assert saved["llm"]["profiles"]["localdeploy_gemma3_12b"]["model"] == "gemma3_12b_ollama_safe"
    assert saved["llm"]["profiles"]["localdeploy_gemma3_12b"]["base_url"] == "http://127.0.0.1:8000/v1"
    assert saved["llm"]["profiles"]["localdeploy_gemma3_12b"]["timeout_seconds"] == 360


def test_admin_writes_telegram_runtime_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(
        "/admin/api/config/telegram",
        json={
            "enabled": True,
            "token_env": "TELEGRAM_BOT_TOKEN",
            "allowed_user_ids": [123],
            "allowed_chat_ids": [456],
            "polling": True,
        },
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["channels"]["telegram"]["enabled"] is True
    assert saved["channels"]["telegram"]["allowed_user_ids"] == [123]
    assert not (tmp_path / ".env").exists()


def test_admin_llm_test_requires_configured_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None, llm={"default_profile": "missing", "profiles": {}}),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post("/admin/api/llm/test", json={})

    assert response.status_code == 400


def test_admin_writes_vscode_runtime_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(
        "/admin/api/config/vscode",
        json={
            "enabled": True,
            "bridge_host": "127.0.0.1",
            "bridge_port": 8766,
            "auth_token_env": "VSCODE_BRIDGE_TOKEN",
            "bridge_token": "secret",
        },
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert saved["adapters"]["vscode"]["enabled"] is True
    assert "VSCODE_BRIDGE_TOKEN=secret" in env_text


def test_admin_writes_workspace_runtime_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(
        "/admin/api/config/workspace",
        json={
            "enabled": True,
            "root_dir": ".agent_control/workspaces",
            "web_host": "127.0.0.1",
            "web_port_start": 8890,
            "open_browser": False,
        },
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["adapters"]["workspace"]["root_dir"] == ".agent_control/workspaces"
    assert saved["adapters"]["workspace"]["open_browser"] is False
    assert not (tmp_path / ".env").exists()


def test_admin_writes_computer_use_runtime_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(
        "/admin/api/config/computer-use",
        json={
            "enabled": True,
            "max_steps": 12,
            "step_delay_seconds": 0.2,
            "screenshot_dir": ".agent_control/computer_use/screenshots",
            "allowed_roots": [str(tmp_path)],
            "allowed_apps": ["notepad.exe"],
            "require_session_approval": True,
            "max_ui_elements": 120,
        },
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["adapters"]["computer_use"]["enabled"] is True
    assert saved["adapters"]["computer_use"]["allowed_roots"] == [str(tmp_path)]
    assert saved["adapters"]["computer_use"]["allowed_apps"] == ["notepad.exe"]
    assert saved["adapters"]["computer_use"]["max_steps"] == 12
    assert not (tmp_path / ".env").exists()


def test_admin_writes_access_modes(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(
        "/admin/api/config/access-modes",
        json={"modes": {"filesystem": CapabilityAccessMode.READ_ONLY.value}},
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["capabilities"][Capability.FILESYSTEM_READ.value]["enabled"] is True
    assert saved["capabilities"][Capability.FILESYSTEM_WRITE.value]["enabled"] is False


def test_admin_access_modes_sync_desktop_screenshot_adapter(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(
        "/admin/api/config/access-modes",
        json={"modes": {"desktop_screenshot": CapabilityAccessMode.READ_ONLY.value}},
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["capabilities"][Capability.DESKTOP_SCREENSHOT.value]["enabled"] is True
    assert saved["adapters"]["desktop"]["screenshot_enabled"] is True


def test_admin_access_modes_sync_computer_use_adapter(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(
        "/admin/api/config/access-modes",
        json={"modes": {"desktop_control": CapabilityAccessMode.WRITE_ACCESS.value}},
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["capabilities"][Capability.DESKTOP_CONTROL.value]["enabled"] is True
    assert saved["capabilities"][Capability.DESKTOP_CONTROL.value]["requires_approval"] is True
    assert saved["adapters"]["desktop"]["control_enabled"] is True
    assert saved["adapters"]["computer_use"]["enabled"] is True
    assert saved["adapters"]["computer_use"]["require_session_approval"] is True

    response = client.post(
        "/admin/api/config/access-modes",
        json={"modes": {"desktop_control": CapabilityAccessMode.FULL_ACCESS.value}},
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["capabilities"][Capability.DESKTOP_CONTROL.value]["enabled"] is True
    assert saved["capabilities"][Capability.DESKTOP_CONTROL.value]["requires_approval"] is False
    assert saved["adapters"]["desktop"]["control_enabled"] is True
    assert saved["adapters"]["computer_use"]["enabled"] is True
    assert saved["adapters"]["computer_use"]["require_session_approval"] is False


def test_admin_access_modes_sync_browser_adapter(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.post(
        "/admin/api/config/access-modes",
        json={"modes": {"browser": CapabilityAccessMode.WRITE_ACCESS.value}},
    )
    saved = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert saved["capabilities"][Capability.BROWSER_OPEN.value]["enabled"] is True
    assert saved["capabilities"][Capability.BROWSER_CONTROL.value]["enabled"] is True
    assert saved["capabilities"][Capability.BROWSER_CONTROL.value]["requires_approval"] is True
    assert saved["adapters"]["browser"]["enabled"] is True


def test_admin_summary_includes_database_and_schedule_data(monkeypatch, tmp_path) -> None:
    """Database table counts and schedule listing are embedded in
    /api/summary, not a separate route - GET /api/database/summary and
    GET /api/schedules were exact duplicates of data /api/summary already
    returns, and the Streamlit UI never called either, so both were deleted
    rather than kept as dead routes (docs/HISTORY.md redundancy pass)."""
    monkeypatch.chdir(tmp_path)  # see test_admin_page_and_summary for why chdir, not just delenv
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    repositories.tasks.create("inspect db")
    repositories.schedules.create(
        ScheduleRecord(
            objective="check example.com daily",
            cadence="daily",
            next_run_at=utc_now(),
        )
    )
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None, storage={"database_url": database_url}),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.get("/admin/api/summary")
    body = response.json()

    assert response.status_code == 200
    assert body["database"]["table_counts"]["tasks"] == 1
    assert "schedules" in body["database"]["table_counts"]
    assert body["schedules"]["total"] == 1
    assert body["schedules"]["items"][0]["objective"] == "check example.com daily"


def _secrets_app(monkeypatch, tmp_path) -> FastAPI:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None, secrets={"path": str(tmp_path / "vault.json")}),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    return local_app


def test_admin_secrets_unavailable_without_vault_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_SECRET_VAULT_KEY", raising=False)
    client = TestClient(_secrets_app(monkeypatch, tmp_path))

    listed = client.get("/admin/api/secrets")
    assert listed.status_code == 200
    assert listed.json() == {"available": False, "key_env": "AGENT_SECRET_VAULT_KEY", "services": {}}

    # Setting a secret must fail clearly, not with a raw SecretVaultError leak.
    set_response = client.post("/admin/api/secrets", json={"service": "openai", "key": "api_key", "value": "sk-1"})
    assert set_response.status_code == 400
    assert "ybm setup" in set_response.json()["detail"]


def test_admin_secrets_set_list_and_delete_round_trip(monkeypatch, tmp_path) -> None:
    from agent_control.storage.secrets import SecretVault

    monkeypatch.setenv("AGENT_SECRET_VAULT_KEY", SecretVault.generate_key())
    client = TestClient(_secrets_app(monkeypatch, tmp_path))

    created = client.post("/admin/api/secrets", json={"service": "openai", "key": "api_key", "value": "sk-secret-value"})
    assert created.status_code == 200
    assert created.json() == {"service": "openai", "key": "api_key", "set": True}

    listed = client.get("/admin/api/secrets")
    body = listed.json()
    assert body["available"] is True
    assert body["services"] == {"openai": ["api_key"]}
    # The listing endpoint must never leak the value.
    assert "sk-secret-value" not in listed.text

    deleted = client.delete("/admin/api/secrets/openai/api_key")
    assert deleted.status_code == 200
    assert deleted.json() == {"service": "openai", "key": "api_key", "deleted": True}

    after = client.get("/admin/api/secrets")
    assert after.json()["services"] == {}


def test_admin_secrets_delete_missing_returns_404(monkeypatch, tmp_path) -> None:
    from agent_control.storage.secrets import SecretVault

    monkeypatch.setenv("AGENT_SECRET_VAULT_KEY", SecretVault.generate_key())
    client = TestClient(_secrets_app(monkeypatch, tmp_path))

    response = client.delete("/admin/api/secrets/nobody/nothing")

    assert response.status_code == 404


def test_admin_secrets_set_audits_service_and_key_but_not_value(monkeypatch, tmp_path) -> None:
    from agent_control.storage.secrets import SecretVault

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_SECRET_VAULT_KEY", SecretVault.generate_key())
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None, secrets={"path": str(tmp_path / "vault.json")}),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    client.post("/admin/api/secrets", json={"service": "openai", "key": "api_key", "value": "sk-must-not-be-logged"})

    events = repositories.audit.list_recent(limit=20)
    matching = [e for e in events if e.type == AuditEventType.CONFIG_UPDATED and e.payload.get("section") == "secrets"]
    assert matching
    assert matching[0].payload["patch"] == {"action": "set", "service": "openai", "key": "api_key"}
    assert "sk-must-not-be-logged" not in str(matching[0].payload)


def _chat_app(monkeypatch, tmp_path) -> FastAPI:
    monkeypatch.chdir(tmp_path)
    repositories = _repositories(f"sqlite:///{tmp_path / 'admin.db'}")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    return local_app, repositories


def test_admin_chat_send_creates_a_task_and_list_returns_it_oldest_first(monkeypatch, tmp_path) -> None:
    """docs/HISTORY.md Part 4 T2.8: the local web chat channel - a message
    becomes a normal task, going through the exact same worker/policy
    pipeline as any other channel, with no Telegram dependency."""
    app, repositories = _chat_app(monkeypatch, tmp_path)
    client = TestClient(app)

    first = client.post("/admin/api/chat/messages", json={"text": "what is the status?"})
    second = client.post("/admin/api/chat/messages", json={"text": "thanks"})

    assert first.status_code == 200
    assert first.json()["task"]["objective"] == "what is the status?"
    assert first.json()["task"]["metadata"]["source_chat_id"] == "local"
    assert second.status_code == 200

    listed = client.get("/admin/api/chat/messages")
    body = listed.json()
    assert [t["objective"] for t in body["tasks"]] == ["what is the status?", "thanks"]

    # Both messages share the same conversation - a real conversation_id
    # from the same fixed local web chat thread, not a fresh one per message.
    assert first.json()["conversation_id"] == second.json()["conversation_id"] == body["conversation_id"]
    real_task = repositories.tasks.get(first.json()["task"]["id"])
    assert real_task.conversation_id == body["conversation_id"]


def test_admin_chat_list_is_empty_before_any_message_sent(monkeypatch, tmp_path) -> None:
    app, _repositories = _chat_app(monkeypatch, tmp_path)
    client = TestClient(app)

    listed = client.get("/admin/api/chat/messages")

    assert listed.status_code == 200
    assert listed.json()["tasks"] == []


def test_admin_chat_send_rejects_empty_text(monkeypatch, tmp_path) -> None:
    app, _repositories = _chat_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post("/admin/api/chat/messages", json={"text": ""})

    assert response.status_code == 422


def test_admin_chat_send_audits_task_creation(monkeypatch, tmp_path) -> None:
    app, repositories = _chat_app(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post("/admin/api/chat/messages", json={"text": "hello"})

    task_id = response.json()["task"]["id"]
    events = repositories.audit.list_recent(limit=20)
    assert any(
        e.type == AuditEventType.TASK_CREATED and e.task_id == task_id and e.actor == "admin_chat"
        for e in events
    )
