"""Unit tests for the Telegram gateway responder.

The responder builds a context summary of the runtime (which capabilities are
enabled, recent task list, conversation memory) and asks the LLM to draft a
chat reply. These tests pin the static stub for the no-op case and the
context-building branches of the LLM responder.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent_control.channels.responder import (
    LLMChatResponder,
    StaticChatResponder,
)
from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.schemas import (
    Capability,
    ChannelType,
    InboundMessage,
    MessageKind,
    RiskLevel,
)
from agent_control.storage import Database, Repositories


class _Provider:
    def __init__(self, reply: str = "hi there") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.reply

    async def generate_multimodal_text(self, *args: Any, **kwargs: Any) -> str:
        return ""

    async def generate_structured(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def _repos(tmp_path) -> Repositories:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    return Repositories.for_database(database)


def _message(text: str = "hello") -> InboundMessage:
    return InboundMessage(
        channel=ChannelType.TELEGRAM,
        kind=MessageKind.TEXT,
        sender_id="user-1",
        chat_id="chat-1",
        text=text,
    )


@pytest.mark.asyncio
async def test_static_responder_returns_fixed_reply_and_records_message() -> None:
    responder = StaticChatResponder("static answer")
    out = await responder.answer(_message("ping"))
    assert out == "static answer"
    assert len(responder.messages) == 1
    assert responder.messages[0].text == "ping"


@pytest.mark.asyncio
async def test_llm_responder_returns_provider_text(tmp_path) -> None:
    provider = _Provider(reply="hi from llm")
    responder = LLMChatResponder(provider, AppSettings(), _repos(tmp_path))
    out = await responder.answer(_message("hello"))
    assert out == "hi from llm"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_llm_responder_context_includes_recent_tasks(tmp_path) -> None:
    repos = _repos(tmp_path)
    repos.tasks.create("First task objective")
    repos.tasks.create("Second task objective")

    provider = _Provider()
    responder = LLMChatResponder(provider, AppSettings(), repos)
    await responder.answer(_message("status?"))

    user_prompt = provider.calls[0][1]
    # Recent task summary lines feed the LLM's context.
    assert "First task objective" in user_prompt
    assert "Second task objective" in user_prompt


@pytest.mark.asyncio
async def test_llm_responder_context_reflects_disabled_capability(tmp_path) -> None:
    settings = AppSettings()
    # Force terminal off; responder should mark route as disabled.
    settings.capabilities[Capability.TERMINAL_RUN] = CapabilityPolicy(
        enabled=False, max_risk_level=RiskLevel.LOW
    )

    provider = _Provider()
    responder = LLMChatResponder(provider, settings, _repos(tmp_path))
    await responder.answer(_message("can you run terminal?"))

    user_prompt = provider.calls[0][1]
    assert "Terminal command route: disabled" in user_prompt


@pytest.mark.asyncio
async def test_llm_responder_passes_message_text_into_user_prompt(tmp_path) -> None:
    provider = _Provider()
    responder = LLMChatResponder(provider, AppSettings(), _repos(tmp_path))
    await responder.answer(_message("what's up doc"))
    assert "what's up doc" in provider.calls[0][1]
