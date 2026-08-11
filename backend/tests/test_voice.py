from __future__ import annotations

import pytest

from agent_control.channels.telegram import TelegramAdapter, TelegramIntakeService, TelegramVoiceIntakeService
from agent_control.config import TelegramConfig
from agent_control.llm.classifier import StaticMessageClassifier
from agent_control.schemas import ArtifactType, TaskType
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.stt import StaticSTTAdapter, build_stt_adapter
from agent_control.config import STTAdapterConfig


class FakeTelegramBotApi:
    async def get_file(self, file_id: str) -> dict:
        return {"file_path": "voice/file.ogg"}

    async def download_file(self, file_path: str) -> bytes:
        return b"audio"


@pytest.mark.asyncio
async def test_voice_update_transcribes_and_creates_task(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    adapter = TelegramAdapter(
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        audit,
    )
    service = TelegramVoiceIntakeService(
        adapter,
        FakeTelegramBotApi(),  # type: ignore[arg-type]
        StaticSTTAdapter("Build a calendar app"),
        repos,
        audit,
    )

    result = await service.handle_update(
        {
            "message": {
                "message_id": 3,
                "from": {"id": 42},
                "chat": {"id": 100},
                "voice": {
                    "file_id": "file_1",
                    "file_unique_id": "unique_1",
                    "duration": 2,
                    "mime_type": "audio/ogg",
                    "file_size": 128,
                },
            }
        }
    )

    assert result.task is not None
    assert result.task.objective == "Build a calendar app"
    artifacts = repos.artifacts.list_for_task(result.task.id)
    assert artifacts[0].type == ArtifactType.TRANSCRIPT
    assert artifacts[0].content_preview == "Build a calendar app"


@pytest.mark.asyncio
async def test_main_telegram_intake_transcribes_voice_before_classification(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    adapter = TelegramAdapter(
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        audit,
    )
    service = TelegramIntakeService(
        adapter,
        repos,
        audit,
        bot_api=FakeTelegramBotApi(),  # type: ignore[arg-type]
        stt=StaticSTTAdapter("Tell me what is on my desktop right now."),
        classifier=StaticMessageClassifier(),
    )

    result = await service.handle_update_async(
        {
            "message": {
                "message_id": 4,
                "from": {"id": 42},
                "chat": {"id": 100},
                "voice": {
                    "file_id": "file_2",
                    "file_unique_id": "unique_2",
                    "duration": 2,
                    "mime_type": "audio/ogg",
                    "file_size": 128,
                },
            }
        }
    )

    assert result.task is not None
    assert result.inbound_message is not None
    assert result.inbound_message.text == "Tell me what is on my desktop right now."
    assert result.task.objective == "Tell me what is on my desktop right now."
    assert result.task.metadata["voice_file_id"] == "file_2"
    assert result.task.metadata["voice_transcript"] == "Tell me what is on my desktop right now."
    assert result.task.metadata["task_type"] == TaskType.DEVELOPMENT.value


@pytest.mark.asyncio
async def test_main_telegram_intake_voice_reports_clear_stt_error(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    adapter = TelegramAdapter(
        TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100]),
        audit,
    )
    service = TelegramIntakeService(adapter, repos, audit)

    result = await service.handle_update_async(
        {
            "message": {
                "message_id": 5,
                "from": {"id": 42},
                "chat": {"id": 100},
                "voice": {
                    "file_id": "file_3",
                    "file_unique_id": "unique_3",
                    "duration": 2,
                    "mime_type": "audio/ogg",
                    "file_size": 128,
                },
            }
        }
    )

    assert result.task is None
    assert result.outbound_message is not None
    # Was: "Voice transcription failed: RuntimeError: STT adapter is disabled".
    # A person cannot act on a class name, and a feature being switched off is
    # not a failure - so the reply now names the situation and a way forward,
    # and the diagnostic stays in the audit trail.
    text = result.outbound_message.text or ""
    assert "turned off" in text or "isn't installed" in text
    assert "Send it as text" in text
    assert "RuntimeError" not in text
    assert "STT" not in text


@pytest.mark.asyncio
async def test_static_stt_builder_uses_env_transcript(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STT_STATIC_TRANSCRIPT", "Voice routed task")

    adapter = build_stt_adapter(STTAdapterConfig(enabled=True, provider="static"))
    transcript = await adapter.transcribe(b"audio", file_name="voice.ogg", mime_type="audio/ogg")

    assert transcript.text == "Voice routed task"
    assert transcript.metadata["file_name"] == "voice.ogg"
