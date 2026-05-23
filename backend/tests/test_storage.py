from __future__ import annotations

from agent_control.schemas import AuditEventType, ChannelType, InboundMessage, MessageKind, TaskStatus
from agent_control.storage import AuditLogger, Database, Repositories


def test_task_lifecycle_and_audit(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)

    conversation_id = repos.conversations.get_or_create(ChannelType.TELEGRAM, "123")
    message = InboundMessage(
        channel=ChannelType.TELEGRAM,
        kind=MessageKind.TEXT,
        sender_id="42",
        chat_id="123",
        text="Build a todo app",
    )
    repos.messages.create(message, conversation_id)
    task = repos.tasks.create(message.text or "", conversation_id)

    audit.append(AuditEventType.TASK_CREATED, actor="test", task_id=task.id, payload={"token": "secret"})
    updated = repos.tasks.update_status(task.id, TaskStatus.PAUSED)
    audit.task_state_changed("test", task.id, task.status, updated.status)

    events = repos.audit.list_for_task(task.id)

    assert updated.status == TaskStatus.PAUSED
    assert events[0].payload["token"] == "***"
    assert events[1].payload == {"old_status": "received", "new_status": "paused"}


def test_message_repository_duplicate_create_is_idempotent(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)

    conversation_id = repos.conversations.get_or_create(ChannelType.TELEGRAM, "123")
    message = InboundMessage(
        id="telegram_1",
        channel=ChannelType.TELEGRAM,
        kind=MessageKind.TEXT,
        sender_id="42",
        chat_id="123",
        text="Build a todo app",
    )

    assert repos.messages.try_create(message, conversation_id) is True
    assert repos.messages.try_create(message, conversation_id) is False
    repos.messages.create(message, conversation_id)
