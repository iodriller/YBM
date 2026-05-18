from __future__ import annotations

import pytest

from agent_control.channels.telegram import TelegramAdapter, TelegramIntakeService, TelegramPollingRunner
from agent_control.channels.memory import ConversationMemoryService
from agent_control.channels.responder import StaticTelegramResponder
from agent_control.config import AppSettings, CapabilityPolicy, DesktopAdapterConfig, StorageConfig, TelegramConfig
from agent_control.llm import LLMMessageClassifier, StaticMessageClassifier
from agent_control.observation import ArtifactService, ScreenshotService
from agent_control.schemas import AuditEventType, MessageClassification, TaskStatus, TaskType
from agent_control.schemas import Capability, RiskLevel
from agent_control.storage import AuditLogger, Database, Repositories


def _service(
    tmp_path,
    config: TelegramConfig,
    settings: AppSettings | None = None,
    screenshot_service: ScreenshotService | None = None,
    classifier=None,
    memory_service: ConversationMemoryService | None = None,
) -> tuple[TelegramIntakeService, Repositories]:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    adapter = TelegramAdapter(config, audit)
    return TelegramIntakeService(
        adapter,
        repos,
        audit,
        settings=settings,
        screenshot_service=screenshot_service,
        classifier=classifier,
        memory_service=memory_service,
    ), repos


def test_telegram_text_update_creates_task(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        classifier=StaticMessageClassifier(),
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
    assert result.task.metadata["task_type"] == TaskType.DEVELOPMENT.value
    assert result.outbound_message is not None
    assert "Task spawned:" in (result.outbound_message.text or "")
    assert repos.tasks.get(result.task.id) is not None


def test_telegram_caption_update_creates_task(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        classifier=StaticMessageClassifier(),
    )

    result = service.handle_update(
        {
            "message": {
                "message_id": 8,
                "from": {"id": 42},
                "chat": {"id": 100},
                "caption": "Build from forwarded caption",
                "forward_origin": {"type": "user", "sender_user": {"id": 99}},
            }
        }
    )

    assert result.task is not None
    assert result.task.objective == "Build from forwarded caption"
    assert repos.tasks.get(result.task.id) is not None


def test_telegram_classifier_can_reject_task_spawn(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        classifier=StaticMessageClassifier(
            MessageClassification(
                is_task=False,
                task_type=TaskType.QUESTION,
                confidence=0.9,
                reason="question only",
            )
        ),
    )

    result = service.handle_update(
        {
            "message": {
                "message_id": 9,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "what is the status?",
            }
        }
    )

    events = repos.audit.list_by_type(AuditEventType.TASK_SPAWN_FAILED)

    assert result.task is None
    assert result.outbound_message is not None
    assert "No task spawned" in (result.outbound_message.text or "")
    assert events[0].payload["reason"] == "question only"


def test_telegram_non_task_question_gets_llm_response(tmp_path) -> None:
    responder = StaticTelegramResponder("I can answer questions and route development tasks.")
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        classifier=StaticMessageClassifier(
            MessageClassification(
                is_task=False,
                task_type=TaskType.QUESTION,
                confidence=0.9,
                reason="question only",
            )
        ),
    )
    service.responder = responder

    result = service.handle_update(
        {
            "message": {
                "message_id": 12,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "what can you do?",
            }
        }
    )

    assert result.task is None
    assert result.outbound_message is not None
    assert result.outbound_message.text == "I can answer questions and route development tasks."
    assert repos.audit.list_by_type(AuditEventType.TASK_SPAWN_FAILED) == []


def test_telegram_plain_status_does_not_require_slash(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
    )
    task = repos.tasks.create("Build app")

    result = service.handle_update(
        {
            "message": {
                "message_id": 13,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "status",
            }
        }
    )

    assert result.outbound_message is not None
    assert task.id in (result.outbound_message.text or "")


def test_telegram_updates_conversation_memory(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
    )

    service.handle_update(
        {
            "message": {
                "message_id": 15,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "My name is Oney.",
            }
        }
    )

    memory = repos.conversation_memory.get("conv_telegram_100")

    assert memory is not None
    assert "Oney" in memory["summary"]
    assert memory["facts"]["strategy"] == "rolling_summary_with_recent_turns"
    assert memory["facts"]["recent_turns"][-1]["text"] == "My name is Oney."


class StaticMemoryProvider:
    async def generate_text(self, system_prompt, user_prompt):
        return "User is Oney. They want concise Telegram gateway memory and local workspace automation."

    async def generate_structured(self, system_prompt, user_prompt, output_model):
        raise NotImplementedError


def test_telegram_memory_can_use_llm_summary(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    service = TelegramIntakeService(
        TelegramAdapter(TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]), audit),
        repos,
        audit,
        memory_service=ConversationMemoryService(repos, provider=StaticMemoryProvider()),
    )

    service.handle_update(
        {
            "message": {
                "message_id": 16,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "Remember that I prefer concise updates and local workspaces.",
            }
        }
    )

    memory = repos.conversation_memory.get("conv_telegram_100")

    assert memory is not None
    assert "local workspace automation" in memory["summary"]


class FailingClassifierProvider:
    async def generate_structured(self, system_prompt, user_prompt, output_model):
        raise ValueError("bad json")

    async def generate_text(self, system_prompt, user_prompt):
        return "unused"


def test_llm_classifier_fallback_allows_direct_greeting_response(tmp_path) -> None:
    service, _ = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        classifier=LLMMessageClassifier(FailingClassifierProvider()),
    )
    service.responder = StaticTelegramResponder("Hello. I can answer questions or route tasks.")

    result = service.handle_update(
        {
            "message": {
                "message_id": 14,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "Hi",
            }
        }
    )

    assert result.task is None
    assert result.outbound_message is not None
    assert result.outbound_message.text == "Hello. I can answer questions or route tasks."


def test_telegram_without_classifier_does_not_spawn_task(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
    )

    result = service.handle_update(
        {
            "message": {
                "message_id": 10,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "Build a todo app",
            }
        }
    )

    assert result.task is None
    assert result.outbound_message is not None
    assert repos.tasks.list_recent() == []


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

    events = repos.audit.list_by_type(AuditEventType.TELEGRAM_ACCESS_DECISION)

    assert result.authorized is False
    assert events[0].payload["allowed"] is False


def test_telegram_empty_allowlist_is_denied_and_audited(tmp_path) -> None:
    service, repos = _service(
        tmp_path,
        TelegramConfig(enabled=True),
    )

    result = service.handle_update(
        {
            "message": {
                "message_id": 11,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "Build a todo app",
            }
        }
    )
    events = repos.audit.list_by_type(AuditEventType.TELEGRAM_ACCESS_DECISION)

    assert result.authorized is False
    assert events[0].payload["reason"] == "allowlist_empty"


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


def test_telegram_screenshot_command_reports_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
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
