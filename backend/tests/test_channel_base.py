"""channels/base.py (docs/UI_UX_AUDIT.md Phase 16) - the channel-agnostic
"classify -> task" core extracted out of TelegramIntakeService. These tests
deliberately use a non-Telegram ChannelType to prove the extraction is
real: a future channel calling classify_and_spawn_task directly gets
correct behavior, not something that only happens to still work because
it's secretly still Telegram-shaped.
"""

from __future__ import annotations

import pytest

from agent_control.channels.base import classify_and_spawn_task, resume_clarifying_reply, status_summary
from agent_control.schemas import ChannelType, InboundMessage, MessageClassification, MessageKind, TaskType
from helpers import make_repos


def _message(text: str = "build me a script", channel: ChannelType = ChannelType.DISCORD) -> InboundMessage:
    return InboundMessage(channel=channel, kind=MessageKind.TEXT, sender_id="user-1", chat_id="chat-1", text=text)


class _StaticClassifier:
    def __init__(self, classification: MessageClassification) -> None:
        self.classification = classification

    async def classify(self, message: InboundMessage, context: str | None = None) -> MessageClassification:
        return self.classification


async def _noop_progress(chat_id: str, text: str) -> None:
    return None


@pytest.mark.asyncio
async def test_classify_and_spawn_task_creates_a_task_for_a_non_telegram_channel(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    conversation_id = repos.conversations.get_or_create(ChannelType.DISCORD, "chat-1")
    classifier = _StaticClassifier(
        MessageClassification(is_task=True, task_type=TaskType.DEVELOPMENT, normalized_objective="build me a script", reason="looks like work")
    )
    inbound = _message()

    result = await classify_and_spawn_task(
        inbound, conversation_id,
        repositories=repos, audit=audit, classifier=classifier, send_progress=_noop_progress,
    )

    assert result.task is not None
    assert result.task.metadata["source_channel"] == "discord"
    assert result.task.metadata["source_chat_id"] == "chat-1"
    # The audit trail must say which channel this came from, not silently
    # assume Telegram (docs/UI_UX_AUDIT.md Phase 16's whole point).
    events = repos.audit.list_for_task(result.task.id)
    assert any(event.actor.startswith("discord:user:") for event in events)


@pytest.mark.asyncio
async def test_classify_and_spawn_task_chat_only_reply_uses_the_inbound_channel(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    conversation_id = repos.conversations.get_or_create(ChannelType.SLACK, "chat-1")
    classifier = _StaticClassifier(
        MessageClassification(is_task=False, reason="just chatting", reply="Hello from the Slack path")
    )
    inbound = _message(text="hey", channel=ChannelType.SLACK)

    result = await classify_and_spawn_task(
        inbound, conversation_id,
        repositories=repos, audit=audit, classifier=classifier, send_progress=_noop_progress,
    )

    assert result.task is None
    assert result.outbound_message is not None
    assert result.outbound_message.channel == ChannelType.SLACK
    assert result.outbound_message.text == "Hello from the Slack path"


@pytest.mark.asyncio
async def test_classify_and_spawn_task_with_no_classifier_fails_clearly(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    conversation_id = repos.conversations.get_or_create(ChannelType.DISCORD, "chat-1")

    result = await classify_and_spawn_task(
        _message(), conversation_id,
        repositories=repos, audit=audit, classifier=None, send_progress=_noop_progress,
    )

    assert result.task is None
    assert "not configured" in (result.outbound_message.text or "")


def test_status_summary_is_pure_repository_text_no_channel_involved(tmp_path) -> None:
    repos, _audit = make_repos(tmp_path)
    repos.tasks.create("do something")

    summary = status_summary(repos)

    assert "1 recent task" in summary


def test_resume_clarifying_reply_returns_none_without_a_pending_clarification(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    conversation_id = repos.conversations.get_or_create(ChannelType.DISCORD, "chat-1")

    assert resume_clarifying_reply(repos, audit, _message(), conversation_id) is None
