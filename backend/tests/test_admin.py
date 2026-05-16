from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_control.admin import create_admin_router
from agent_control.config import AppSettings
from agent_control.main import app, vscode_store
from agent_control.schemas import AuditEventType, Capability
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.vscode_bridge import VSCodeBridgeStore


def _repositories(database_url: str) -> Repositories:
    database = Database(database_url)
    database.initialize()
    return Repositories.for_database(database)


def test_admin_page_and_summary(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_STORAGE__DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    client = TestClient(app)

    page = client.get("/admin")
    summary = client.get("/admin/api/summary")

    assert page.status_code == 200
    assert "Agent Control Admin" in page.text
    assert summary.status_code == 200
    assert summary.json()["config"]["identity"]["instance_name"] == "local-agent-control"


def test_admin_lists_tasks_and_audit(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    monkeypatch.setenv("AGENT_STORAGE__DATABASE_URL", database_url)
    repositories = _repositories(database_url)
    task = repositories.tasks.create("review admin dashboard")
    AuditLogger(repositories.audit).append(AuditEventType.TASK_CREATED, actor="test", task_id=task.id)
    client = TestClient(app)

    tasks = client.get("/admin/api/tasks").json()["tasks"]
    audit = client.get("/admin/api/audit").json()["events"]

    assert tasks[0]["objective"] == "review admin dashboard"
    assert audit[0]["type"] == AuditEventType.TASK_CREATED.value


def test_admin_task_signal_updates_task(tmp_path) -> None:
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
    assert updated.status.value == "paused"


def test_admin_rejects_vscode_terminal_command_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AGENT_STORAGE__DATABASE_URL", f"sqlite:///{tmp_path / 'admin.db'}")
    vscode_store.terminal_commands = []
    client = TestClient(app)

    response = client.post("/admin/api/vscode/terminal-commands", json={"command": "echo blocked"})

    assert response.status_code == 403
    assert vscode_store.terminal_commands == []


def test_admin_can_queue_vscode_terminal_command_when_enabled(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    settings = AppSettings(_env_file=None, adapters={"vscode": {"enabled": True}})
    settings.capabilities[Capability.TERMINAL_RUN].enabled = True
    settings.capabilities[Capability.TERMINAL_RUN].requires_approval = False
    store = VSCodeBridgeStore()
    local_app = FastAPI()
    local_app.include_router(create_admin_router(lambda: settings, lambda: repositories, store))
    client = TestClient(local_app)

    response = client.post("/admin/api/vscode/terminal-commands", json={"command": "echo hi"})

    assert response.status_code == 200
    assert store.terminal_commands[0].command == "echo hi"
