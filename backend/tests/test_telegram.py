from __future__ import annotations

from agent_control.channels.telegram import TelegramAdapter, TelegramIntakeService
from agent_control.config import TelegramConfig
from agent_control.schemas import AuditEventType, TaskStatus
from agent_control.storage import AuditLogger, Database, Repositories


def _service(tmp_path, config: TelegramConfig) -> tuple[TelegramIntakeService, Repositories]:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    adapter = TelegramAdapter(config, audit)
    return TelegramIntakeService(adapter, repos, audit), repos


def test_telegram_text_update_creates_task(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
    )

    result = service.handle_update(
        {
            "message": {
                "message_id": 1,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "Build a todo app",
            }
        }
    )

    assert result.authorized is True
    assert result.task is not None
    assert result.task.objective == "Build a todo app"
    assert repos.tasks.get(result.task.id) is not None


def test_telegram_unauthorized_update_is_denied_and_audited(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
    )

    result = service.handle_update(
        {
            "message": {
                "message_id": 1,
                "from": {"id": 99},
                "chat": {"id": 100},
                "text": "Nope",
            }
        }
    )

    events = repos.audit.list_by_type(AuditEventType.POLICY_DECISION)

    assert result.authorized is False
    assert events[0].payload["allowed"] is False


def test_telegram_pause_command_updates_task(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
    )
    task = repos.tasks.create("Build app")

    result = service.handle_update(
        {
            "message": {
                "message_id": 2,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": f"/pause {task.id}",
            }
        }
    )

    updated = repos.tasks.get(task.id)

    assert result.signal is not None
    assert updated is not None
    assert updated.status == TaskStatus.PAUSED
