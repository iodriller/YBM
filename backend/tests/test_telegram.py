from __future__ import annotations

import pytest

from agent_control.channels.telegram import TelegramAdapter, TelegramIntakeService, TelegramPollingRunner
from agent_control.config import AppSettings, CapabilityPolicy, DesktopAdapterConfig, StorageConfig, TelegramConfig
from agent_control.observation import ArtifactService, ScreenshotService
from agent_control.schemas import AuditEventType, TaskStatus
from agent_control.schemas import Capability, RiskLevel
from agent_control.storage import AuditLogger, Database, Repositories


def _service(
    tmp_path,
    config: TelegramConfig,
    settings: AppSettings | None = None,
    screenshot_service: ScreenshotService | None = None,
) -> tuple[TelegramIntakeService, Repositories]:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    adapter = TelegramAdapter(config, audit)
    return TelegramIntakeService(adapter, repos, audit, settings=settings, screenshot_service=screenshot_service), repos


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


def test_telegram_tasks_command_returns_summary(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
    )
    task = repos.tasks.create("Build app")

    result = service.handle_update(
        {
            "message": {
                "message_id": 3,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "/tasks",
            }
        }
    )

    assert result.outbound_message is not None
    assert task.id in (result.outbound_message.text or "")


def test_telegram_logs_command_returns_recent_events(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
    )
    task = repos.tasks.create("Build app")
    service.audit.append(
        AuditEventType.TASK_CREATED,
        actor="test",
        task_id=task.id,
        payload={"objective": task.objective},
    )

    result = service.handle_update(
        {
            "message": {
                "message_id": 4,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": f"/logs {task.id}",
            }
        }
    )

    assert result.outbound_message is not None
    assert "task_created" in (result.outbound_message.text or "")


def test_telegram_screenshot_command_reports_disabled(tmp_path) -> None:
    service, _ = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        settings=AppSettings(_env_file=None),
    )

    result = service.handle_update(
        {
            "message": {
                "message_id": 5,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "/screenshot",
            }
        }
    )

    assert result.outbound_message is not None
    assert result.outbound_message.text == "desktop.screenshot is disabled."


class FakeTelegramClient:
    def __init__(self, updates: list[dict]) -> None:
        self.updates = updates
        self.sent: list[tuple[str | int, str]] = []

    async def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        return self.updates

    async def send_message(self, chat_id: str | int, text: str) -> dict:
        self.sent.append((chat_id, text))
        return {"ok": True}

    async def send_photo_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict:
        self.sent.append((chat_id, f"photo:{caption}:{path}"))
        return {"ok": True}


@pytest.mark.asyncio
async def test_polling_runner_sends_outbound_command_response(tmp_path) -> None:
    service, _ = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
    )
    client = FakeTelegramClient(
        [
            {
                "update_id": 10,
                "message": {
                    "message_id": 6,
                    "from": {"id": 42},
                    "chat": {"id": 100},
                    "text": "/status",
                },
            }
        ]
    )
    runner = TelegramPollingRunner(client, service)  # type: ignore[arg-type]

    next_offset, _ = await runner.poll_once()

    assert next_offset == 11
    assert client.sent == [("100", "0 recent task(s), 0 active.")]


class FakeScreenshotAdapter:
    def capture_png(self) -> bytes:
        return b"png-bytes"


@pytest.mark.asyncio
async def test_polling_runner_sends_screenshot_artifact(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.DESKTOP_SCREENSHOT: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.MEDIUM,
            )
        },
    )
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        settings=settings,
    )
    screenshot_service = ScreenshotService(
        DesktopAdapterConfig(screenshot_enabled=True),
        ArtifactService(StorageConfig(artifact_dir=str(tmp_path / "artifacts")), repos.artifacts),
        adapter=FakeScreenshotAdapter(),
    )
    service.screenshot_service = screenshot_service
    client = FakeTelegramClient(
        [
            {
                "update_id": 11,
                "message": {
                    "message_id": 7,
                    "from": {"id": 42},
                    "chat": {"id": 100},
                    "text": "/screenshot",
                },
            }
        ]
    )
    runner = TelegramPollingRunner(client, service)  # type: ignore[arg-type]

    _, results = await runner.poll_once()

    assert results[0].outbound_message is not None
    assert results[0].outbound_message.artifact_ids
    assert client.sent[0] == ("100", "Screenshot captured.")
    assert client.sent[1][1].startswith("photo:desktop screenshot:")
