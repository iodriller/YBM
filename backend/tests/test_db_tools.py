from __future__ import annotations

from datetime import timedelta

from agent_control import db_tools
from agent_control.storage.database import Database
from agent_control.storage.repositories import Repositories
from agent_control.schemas import (
    ApprovalRequest,
    Capability,
    ChannelType,
    RiskLevel,
    TaskRecord,
    utc_now,
)


def _repositories(tmp_path, monkeypatch) -> tuple[Repositories, Database]:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("storage:\n  database_url: sqlite:///./test.db\n", encoding="utf-8")
    database = Database("sqlite:///./test.db")
    database.initialize()
    return Repositories.for_database(database), database


def _backdate_task(database: Database, task: TaskRecord, *, days_old: int) -> None:
    created_at = (utc_now() - timedelta(days=days_old)).isoformat()
    with database.connect() as connection:
        connection.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (created_at, task.id))


def test_db_inspect_runs_without_error(tmp_path, monkeypatch, capsys) -> None:
    _repositories(tmp_path, monkeypatch)

    exit_code = db_tools.db_inspect()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "tasks" in out


def test_db_clean_removes_old_tasks_keeps_recent(tmp_path, monkeypatch) -> None:
    repositories, database = _repositories(tmp_path, monkeypatch)
    old_task = repositories.tasks.create(objective="old task")
    recent_task = repositories.tasks.create(objective="recent task")
    _backdate_task(database, old_task, days_old=60)
    _backdate_task(database, recent_task, days_old=1)

    exit_code = db_tools.db_clean(days=30)

    assert exit_code == 0
    assert repositories.tasks.get(old_task.id) is None
    assert repositories.tasks.get(recent_task.id) is not None


def test_db_clean_rejects_invalid_days(tmp_path, monkeypatch) -> None:
    _repositories(tmp_path, monkeypatch)

    exit_code = db_tools.db_clean(days=0)

    assert exit_code == 1


def test_db_reset_requires_confirmation(tmp_path, monkeypatch) -> None:
    _repositories(tmp_path, monkeypatch)

    exit_code = db_tools.db_reset(yes=False)

    assert exit_code == 1


def test_db_reset_clears_all_tasks(tmp_path, monkeypatch) -> None:
    repositories, _database = _repositories(tmp_path, monkeypatch)
    repositories.tasks.create(objective="some task")

    exit_code = db_tools.db_reset(yes=True)

    assert exit_code == 0
    assert repositories.tasks.list_recent(10) == []


def _full_schema_fixture(repositories: Repositories) -> TaskRecord:
    """A task wired to every FK-referencing table, to catch deletion-order bugs.

    tasks.conversation_id -> conversations, approvals.task_id -> tasks: a
    delete order that clears `conversations` before `tasks`, or skips
    `approvals` in the per-task cascade, trips a real FOREIGN KEY violation
    here exactly like it did against the live database during manual testing.
    """
    conversation_id = repositories.conversations.get_or_create(ChannelType.TELEGRAM, "chat-1")
    task = repositories.tasks.create(objective="full schema task", conversation_id=conversation_id)
    repositories.approvals.create(ApprovalRequest(
        task_id=task.id,
        capability=Capability.FILESYSTEM_WRITE,
        risk_level=RiskLevel.HIGH,
        summary="test approval",
        expires_at=utc_now() + timedelta(hours=1),
    ))
    return task


def test_db_reset_handles_full_foreign_key_graph(tmp_path, monkeypatch) -> None:
    repositories, _database = _repositories(tmp_path, monkeypatch)
    _full_schema_fixture(repositories)

    exit_code = db_tools.db_reset(yes=True)

    assert exit_code == 0


def test_db_clean_handles_full_foreign_key_graph(tmp_path, monkeypatch) -> None:
    repositories, database = _repositories(tmp_path, monkeypatch)
    task = _full_schema_fixture(repositories)
    _backdate_task(database, task, days_old=60)

    exit_code = db_tools.db_clean(days=30)

    assert exit_code == 0
    assert repositories.tasks.get(task.id) is None
