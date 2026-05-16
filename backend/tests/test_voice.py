from __future__ import annotations

import pytest

from agent_control.channels.telegram import TelegramAdapter, TelegramVoiceIntakeService
from agent_control.config import TelegramConfig
from agent_control.schemas import ArtifactType
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.stt import StaticSTTAdapter


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
