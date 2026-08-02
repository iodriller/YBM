from __future__ import annotations

import pytest

from agent_control.channels.task_notify import format_task_message
from agent_control.channels.whatsapp_notifications import WhatsAppTaskNotifier
from agent_control.schemas import ChannelType, TaskRecord, TaskStatus


JID = "15551234567@s.whatsapp.net"


class FakeWhatsAppBridgeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str) -> dict:
        self.sent.append((chat_id, text))
        return {"ok": True}


@pytest.mark.asyncio
async def test_notifier_sends_the_shared_task_message_to_the_source_chat() -> None:
    client = FakeWhatsAppBridgeClient()
    notifier = WhatsAppTaskNotifier(client)  # type: ignore[arg-type]
    task = TaskRecord(
        objective="Organize my Downloads folder",
        status=TaskStatus.COMPLETED,
        metadata={"source_channel": ChannelType.WHATSAPP.value, "source_chat_id": JID},
    )

    await notifier.notify(task)

    assert len(client.sent) == 1
    chat_id, text = client.sent[0]
    assert chat_id == JID
    assert text == format_task_message(task)


@pytest.mark.asyncio
async def test_notifier_ignores_tasks_from_other_channels() -> None:
    client = FakeWhatsAppBridgeClient()
    notifier = WhatsAppTaskNotifier(client)  # type: ignore[arg-type]
    task = TaskRecord(
        objective="answer in Telegram",
        status=TaskStatus.COMPLETED,
        metadata={"source_channel": ChannelType.TELEGRAM.value, "source_chat_id": "100"},
    )

    await notifier.notify(task)

    assert client.sent == []


@pytest.mark.asyncio
async def test_notifier_no_ops_without_a_source_chat_id() -> None:
    client = FakeWhatsAppBridgeClient()
    notifier = WhatsAppTaskNotifier(client)  # type: ignore[arg-type]
    task = TaskRecord(objective="no source chat", status=TaskStatus.COMPLETED, metadata={})

    await notifier.notify(task)

    assert client.sent == []
