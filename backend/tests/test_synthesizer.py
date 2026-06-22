"""Unit tests for the response synthesizer.

The synthesizer turns raw tool output + the user's question into a focused
natural-language answer. These tests pin its contract using stub providers so
the LLM is never actually called.
"""
from __future__ import annotations

from typing import Any, TypeVar
from pydantic import BaseModel

import pytest

from agent_control.llm.synthesizer import ResponseSynthesizer


T = TypeVar("T", bound=BaseModel)


class _Provider:
    """Stub LLM provider that returns a fixed text reply per call."""

    def __init__(self, reply: str = "answer", raise_on_call: Exception | None = None) -> None:
        self.reply = reply
        self.raise_on_call = raise_on_call
        self.calls: list[tuple[str, str]] = []

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.reply

    async def generate_multimodal_text(self, *args: Any, **kwargs: Any) -> str:
        return ""

    async def generate_structured(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def test_is_content_tool_classifies_known_tools() -> None:
    assert ResponseSynthesizer.is_content_tool("browser.open") is True
    assert ResponseSynthesizer.is_content_tool("filesystem.manage") is True
    assert ResponseSynthesizer.is_content_tool("code.interpreter") is True
    assert ResponseSynthesizer.is_content_tool("artifact.deliver") is False
    assert ResponseSynthesizer.is_content_tool("desktop.observe") is False


@pytest.mark.asyncio
async def test_synthesize_returns_provider_reply_on_normal_path() -> None:
    provider = _Provider(reply="5 episodes listed.")
    synth = ResponseSynthesizer(provider)
    result = await synth.synthesize(
        "list the first 5 episodes", "ep1\nep2\nep3\nep4\nep5", original_message=None
    )
    assert result == "5 episodes listed."
    # The synthesizer must pass the user's question and raw content.
    assert "list the first 5 episodes" in provider.calls[0][1]
    assert "ep1" in provider.calls[0][1]


@pytest.mark.asyncio
async def test_synthesize_returns_none_on_empty_raw_content() -> None:
    synth = ResponseSynthesizer(_Provider())
    # Empty raw content short-circuits to None before any LLM call.
    assert await synth.synthesize("anything", "") is None
    assert await synth.synthesize("anything", "   \n") is None


@pytest.mark.asyncio
async def test_synthesize_returns_none_when_provider_reports_insufficient() -> None:
    synth = ResponseSynthesizer(_Provider(reply="INSUFFICIENT"))
    assert await synth.synthesize("q", "some raw") is None


@pytest.mark.asyncio
async def test_synthesize_returns_none_on_provider_exception() -> None:
    synth = ResponseSynthesizer(_Provider(raise_on_call=RuntimeError("boom")))
    # Provider failures must not propagate — the worker treats None as
    # "synthesis didn't produce an answer" and replans accordingly.
    assert await synth.synthesize("q", "some raw") is None


@pytest.mark.asyncio
async def test_synthesize_includes_original_message_for_language_preservation() -> None:
    provider = _Provider(reply="ok")
    synth = ResponseSynthesizer(provider)
    await synth.synthesize(
        "list five new episodes",
        "raw content",
        original_message="dizibox.com'a git ve yeni eklenen bolumleri soyle",
    )
    user_prompt = provider.calls[0][1]
    # Both the original message (for language) and normalized objective
    # should be visible to the synthesizer.
    assert "dizibox.com" in user_prompt
    assert "list five new episodes" in user_prompt
