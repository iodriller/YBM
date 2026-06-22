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


def test_task_claim_next_is_atomic_across_concurrent_workers(tmp_path) -> None:
    """Two workers calling claim_next on the same task must NOT both succeed.

    This is the structural guarantee that prevents the duplicate-worker race
    we hit during e2e runs (the cause of the artifact_delivered cluster):
    one worker gets the row, the other gets None.
    """
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)

    conversation_id = repos.conversations.get_or_create(ChannelType.TELEGRAM, "1")
    task = repos.tasks.create("do a thing", conversation_id)
    assert task.status == TaskStatus.RECEIVED

    # Two workers race to claim. SQLite serializes writes; exactly one wins.
    claim_a = repos.tasks.claim_next([TaskStatus.RECEIVED], worker_id="A")
    claim_b = repos.tasks.claim_next([TaskStatus.RECEIVED], worker_id="B")

    assert claim_a is not None
    assert claim_b is None  # B was locked out by A's claim
    assert claim_a.id == task.id

    # The same worker (A) CAN re-claim its own task on a subsequent poll.
    claim_a_again = repos.tasks.claim_next([TaskStatus.RECEIVED], worker_id="A")
    assert claim_a_again is not None
    assert claim_a_again.id == task.id


def test_task_claim_releases_when_terminal(tmp_path) -> None:
    """release_claim clears the worker hint so the task is free for reclaim
    (mostly cosmetic; the claim would also expire naturally)."""
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)

    conversation_id = repos.conversations.get_or_create(ChannelType.TELEGRAM, "1")
    task = repos.tasks.create("do a thing", conversation_id)
    repos.tasks.claim_next([TaskStatus.RECEIVED], worker_id="A")

    # Without release, worker B can't grab it.
    assert repos.tasks.claim_next([TaskStatus.RECEIVED], worker_id="B") is None

    repos.tasks.release_claim(task.id)

    # After release, worker B can claim it.
    claim_b = repos.tasks.claim_next([TaskStatus.RECEIVED], worker_id="B")
    assert claim_b is not None
    assert claim_b.id == task.id


def test_task_claim_recovers_after_expiry(tmp_path) -> None:
    """An expired claim becomes eligible again, so a crashed worker doesn't
    permanently strand the task."""
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)

    conversation_id = repos.conversations.get_or_create(ChannelType.TELEGRAM, "1")
    task = repos.tasks.create("do a thing", conversation_id)

    # Worker A claims with a 0-second expiry → immediately expired.
    repos.tasks.claim_next([TaskStatus.RECEIVED], worker_id="A", claim_expiry_seconds=0)

    # Worker B can then claim it.
    claim_b = repos.tasks.claim_next([TaskStatus.RECEIVED], worker_id="B")
    assert claim_b is not None
    assert claim_b.id == task.id
