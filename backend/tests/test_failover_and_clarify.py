from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from agent_control.channels.telegram import TelegramAdapter, TelegramIntakeService
from agent_control.config import AppSettings, LLMConfig, LLMProfileConfig, TelegramConfig
from agent_control.llm.providers import FailoverLLMProvider, build_default_llm_provider
from agent_control.schemas import (
    ChannelType,
    TaskStatus,
)
from helpers import make_repos


# --- FailoverLLMProvider ---


class _Recorder:
    def __init__(self, response: str = "ok", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        return await self.generate_text(system_prompt, user_prompt)

    async def generate_structured(self, system_prompt, user_prompt, output_model, *, temperature=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return output_model()


class _EmptyModel(BaseModel):
    pass


@pytest.mark.asyncio
async def test_failover_uses_fallback_on_connection_error() -> None:
    primary = _Recorder(error=httpx.ConnectError("connection refused"))
    fallback = _Recorder(response="from fallback")
    provider = FailoverLLMProvider(primary, fallback)

    assert await provider.generate_text("s", "u") == "from fallback"
    assert primary.calls == 1 and fallback.calls == 1


@pytest.mark.asyncio
async def test_failover_uses_fallback_on_timeout_and_5xx() -> None:
    for error in (httpx.ReadTimeout("timed out"), ValueError("LLM request failed with HTTP 503 at x: overloaded")):
        primary = _Recorder(error=error)
        fallback = _Recorder(response="fallback")
        provider = FailoverLLMProvider(primary, fallback)
        assert await provider.generate_text("s", "u") == "fallback"


@pytest.mark.asyncio
async def test_failover_does_not_mask_request_bugs() -> None:
    primary = _Recorder(error=ValueError("LLM request failed with HTTP 400 at x: bad request"))
    fallback = _Recorder()
    provider = FailoverLLMProvider(primary, fallback)

    with pytest.raises(ValueError):
        await provider.generate_text("s", "u")
    assert fallback.calls == 0


def test_build_default_provider_wraps_fallback_profile() -> None:
    settings = AppSettings(
        _env_file=None,
        llm=LLMConfig(
            default_profile="local",
            fallback_profile="cloud",
            profiles={
                "local": LLMProfileConfig(model="local-model", base_url="http://127.0.0.1:8000/v1"),
                "cloud": LLMProfileConfig(model="cloud-model", base_url="https://api.example.com/v1"),
            },
        ),
    )
    provider = build_default_llm_provider(settings)
    assert isinstance(provider, FailoverLLMProvider)


def test_build_default_provider_without_fallback_stays_plain() -> None:
    settings = AppSettings(
        _env_file=None,
        llm=LLMConfig(
            default_profile="local",
            profiles={"local": LLMProfileConfig(model="local-model", base_url="http://127.0.0.1:8000/v1")},
        ),
    )
    provider = build_default_llm_provider(settings)
    assert not isinstance(provider, FailoverLLMProvider)


# --- CLARIFYING ask-user loop ---




@pytest.mark.asyncio
async def test_clarifying_reply_resumes_the_same_task(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    config = TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100])
    adapter = TelegramAdapter(config, audit)
    service = TelegramIntakeService(adapter, repos, audit)

    task = repos.tasks.create(
        "Summarize the report",
        conversation_id=repos.conversations.get_or_create(ChannelType.TELEGRAM, "100"),
        metadata={"source_chat_id": "100", "clarifying_question": "Which report?", "clarify_count": 1},
    )
    repos.tasks.update_status(task.id, TaskStatus.CLARIFYING)

    result = await service.handle_update_async(
        {
            "message": {
                "message_id": 7,
                "from": {"id": 42},
                "chat": {"id": 100},
                "text": "The Q3 sales report on my desktop",
            }
        }
    )

    resumed = repos.tasks.get(task.id)
    assert resumed.status == TaskStatus.RECEIVED
    assert "Q3 sales report" in resumed.objective
    assert resumed.metadata["clarification_answer"] == "The Q3 sales report on my desktop"
    assert resumed.metadata["retry_count"] == 0
    assert result.outbound_message is not None
    assert "resuming" in result.outbound_message.text.lower()
    # No new task was spawned for the reply.
    assert len(repos.tasks.list_recent(10)) == 1


@pytest.mark.asyncio
async def test_clarifying_cancel_reply_cancels_task(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    config = TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100])
    service = TelegramIntakeService(TelegramAdapter(config, audit), repos, audit)

    task = repos.tasks.create(
        "Summarize the report",
        conversation_id=repos.conversations.get_or_create(ChannelType.TELEGRAM, "100"),
        metadata={"source_chat_id": "100", "clarifying_question": "Which report?"},
    )
    repos.tasks.update_status(task.id, TaskStatus.CLARIFYING)

    result = await service.handle_update_async(
        {"message": {"message_id": 8, "from": {"id": 42}, "chat": {"id": 100}, "text": "cancel"}}
    )

    assert repos.tasks.get(task.id).status == TaskStatus.CANCELLED
    assert "cancelled" in result.outbound_message.text.lower()


# --- Telegram deterministic fast lane for coding sessions ---


@pytest.mark.asyncio
async def test_codex_status_question_is_answered_from_session_files(tmp_path) -> None:
    import json

    session_root = tmp_path / "sessions"
    session_root.mkdir()
    log_path = session_root / "codex_abc.log"
    log_path.write_text("applying patch to app.py", encoding="utf-8")
    (session_root / "codex_abc.json").write_text(
        json.dumps(
            {
                "session_id": "codex_abc",
                "provider": "codex",
                "status": "running",
                "pid": 1234,
                "workspace_dir": str(tmp_path),
                "log_path": str(log_path),
                "started_at": "2026-07-05T00:00:00+00:00",
                "files_before": {},
            }
        ),
        encoding="utf-8",
    )

    repos, audit = make_repos(tmp_path)
    config = TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100])
    settings = AppSettings(
        _env_file=None,
        adapters={"coding_agent": {"enabled": True, "session_root": str(session_root)}},
    )
    service = TelegramIntakeService(TelegramAdapter(config, audit), repos, audit, settings=settings)

    result = await service.handle_update_async(
        {"message": {"message_id": 9, "from": {"id": 42}, "chat": {"id": 100}, "text": "what is codex doing"}}
    )

    # Answered instantly from session files: no classifier, no task, no LLM.
    assert result.task is None
    text = result.outbound_message.text
    assert "codex" in text and "running" in text
    assert "applying patch to app.py" in text


@pytest.mark.asyncio
async def test_provider_mention_without_status_intent_falls_through(tmp_path) -> None:
    repos, audit = make_repos(tmp_path)
    config = TelegramConfig(enabled=True, allowed_user_ids=[42], allowed_chat_ids=[100])
    settings = AppSettings(
        _env_file=None,
        adapters={"coding_agent": {"enabled": True, "session_root": str(tmp_path / "none")}},
    )
    service = TelegramIntakeService(TelegramAdapter(config, audit), repos, audit, settings=settings)

    result = await service.handle_update_async(
        {"message": {"message_id": 10, "from": {"id": 42}, "chat": {"id": 100}, "text": "use codex to fix my repo tests"}}
    )

    # No fast-lane hijack: the request needs real classification (none is
    # configured here, so intake reports the spawn failure).
    assert result.outbound_message is not None
    assert "could not start" in result.outbound_message.text.lower()
