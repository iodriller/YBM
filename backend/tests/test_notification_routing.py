from __future__ import annotations

import pytest

from agent_control import cli
from agent_control.cli import RoutingNotificationSink
from agent_control.config import AppSettings
from agent_control.schemas import AuditEventType, ChannelType, TaskRecord, TaskStatus
from helpers import make_repos


class FakeSink:
    def __init__(self, fail: bool = False) -> None:
        self.notified: list[TaskRecord] = []
        self.fail = fail

    async def notify(self, task: TaskRecord) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.notified.append(task)


def _unexpected(*_args, **_kwargs):
    raise AssertionError("this notifier constructor should not have been called")


@pytest.mark.asyncio
async def test_routes_telegram_sourced_tasks_to_the_telegram_notifier(monkeypatch, tmp_path) -> None:
    _repos, audit = make_repos(tmp_path)
    telegram_sink = FakeSink()
    monkeypatch.setattr(cli, "_telegram_notifier", lambda *a, **k: telegram_sink)
    monkeypatch.setattr(cli, "_whatsapp_notifier", _unexpected)
    router = RoutingNotificationSink(AppSettings(_env_file=None), audit)
    task = TaskRecord(objective="x", status=TaskStatus.COMPLETED, metadata={"source_channel": ChannelType.TELEGRAM.value})

    await router.notify(task)

    assert telegram_sink.notified == [task]


@pytest.mark.asyncio
async def test_routes_whatsapp_sourced_tasks_to_the_whatsapp_notifier(monkeypatch, tmp_path) -> None:
    _repos, audit = make_repos(tmp_path)
    whatsapp_sink = FakeSink()
    monkeypatch.setattr(cli, "_telegram_notifier", _unexpected)
    monkeypatch.setattr(cli, "_whatsapp_notifier", lambda: whatsapp_sink)
    router = RoutingNotificationSink(AppSettings(_env_file=None), audit)
    task = TaskRecord(objective="x", status=TaskStatus.COMPLETED, metadata={"source_channel": ChannelType.WHATSAPP.value})

    await router.notify(task)

    assert whatsapp_sink.notified == [task]


@pytest.mark.asyncio
async def test_web_chat_sourced_tasks_are_not_routed_to_any_channel_notifier(monkeypatch, tmp_path) -> None:
    _repos, audit = make_repos(tmp_path)
    monkeypatch.setattr(cli, "_telegram_notifier", _unexpected)
    monkeypatch.setattr(cli, "_whatsapp_notifier", _unexpected)
    router = RoutingNotificationSink(AppSettings(_env_file=None), audit)
    task = TaskRecord(objective="x", status=TaskStatus.COMPLETED, metadata={"source_channel": ChannelType.WEB.value})

    await router.notify(task)  # must not raise, and must not touch either notifier


@pytest.mark.asyncio
async def test_a_task_with_no_source_channel_at_all_defaults_to_telegram(monkeypatch, tmp_path) -> None:
    """Legacy, pre-channel-migration task records have no source_channel
    key at all (not even "web") - schemas.py's channel_chat_id has the
    matching fallback for these; this is the routing half of that same
    contract, not a bug (a task with an explicit non-Telegram
    source_channel is routed correctly by the tests above)."""
    _repos, audit = make_repos(tmp_path)
    telegram_sink = FakeSink()
    monkeypatch.setattr(cli, "_telegram_notifier", lambda *a, **k: telegram_sink)
    router = RoutingNotificationSink(AppSettings(_env_file=None), audit)
    task = TaskRecord(objective="x", status=TaskStatus.COMPLETED, metadata={})

    await router.notify(task)

    assert telegram_sink.notified == [task]


@pytest.mark.asyncio
async def test_a_notify_failure_is_audited_but_does_not_raise(monkeypatch, tmp_path) -> None:
    _repos, audit = make_repos(tmp_path)
    monkeypatch.setattr(cli, "_telegram_notifier", lambda *a, **k: FakeSink(fail=True))
    router = RoutingNotificationSink(AppSettings(_env_file=None), audit)
    task = TaskRecord(objective="x", status=TaskStatus.COMPLETED, metadata={"source_channel": ChannelType.TELEGRAM.value})

    await router.notify(task)  # must not raise - notifying is best-effort

    errors = audit.repository.list_by_type(AuditEventType.ERROR)
    assert any(e.payload.get("error") == "notify_failed" for e in errors)


@pytest.mark.asyncio
async def test_an_unconfigured_channel_is_a_silent_no_op_not_audited(monkeypatch, tmp_path) -> None:
    """The 'nothing to notify' path (sink is None) is distinct from an
    actual notify failure above - it must not add audit noise for every
    web-chat task or every WhatsApp task sent while the bridge isn't
    running."""
    _repos, audit = make_repos(tmp_path)
    monkeypatch.setattr(cli, "_whatsapp_notifier", lambda: None)
    router = RoutingNotificationSink(AppSettings(_env_file=None), audit)
    task = TaskRecord(objective="x", status=TaskStatus.COMPLETED, metadata={"source_channel": ChannelType.WHATSAPP.value})

    await router.notify(task)

    assert audit.repository.list_by_type(AuditEventType.ERROR) == []
