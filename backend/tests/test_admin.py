from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from agent_control.admin import create_admin_router
from agent_control.config import AppSettings
from agent_control.main import app, vscode_store
from agent_control.schemas import AuditEventType, Capability, CapabilityAccessMode, TaskStatus
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
    filtered = client.get("/admin/api/audit?category=spawned_task").json()["events"]

    assert tasks[0]["objective"] == "review admin dashboard"
    assert audit[0]["type"] == AuditEventType.TASK_CREATED.value
    assert audit[0]["category"] == "spawned_task"
    assert audit[0]["formatted_time"].endswith("UTC")
    assert filtered[0]["category"] == "spawned_task"


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
    assert updated.status == TaskStatus.PAUSED


def test_admin_task_resume_restores_paused_status(tmp_path) -> None:
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
    assert (tmp_path / ".env").read_text(encoding="utf-8").count("AGENT_LLM__DEFAULT_PROFILE=local") == 1


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
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "AGENT_CHANNELS__TELEGRAM__ALLOWED_USER_IDS=[123]" in env_text


def test_admin_llm_test_requires_configured_profile(tmp_path) -> None:
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


def test_admin_database_summary(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'admin.db'}"
    repositories = _repositories(database_url)
    repositories.tasks.create("inspect db")
    local_app = FastAPI()
    local_app.include_router(
        create_admin_router(
            lambda: AppSettings(_env_file=None, storage={"database_url": database_url}),
            lambda: repositories,
            VSCodeBridgeStore(),
        )
    )
    client = TestClient(local_app)

    response = client.get("/admin/api/database/summary")

    assert response.status_code == 200
    assert response.json()["table_counts"]["tasks"] == 1
