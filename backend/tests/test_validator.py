"""Unit tests for the pre-synthesis answer validator.

The validator decides whether raw tool output is sufficient to answer the
user's question. A YES means run the synthesizer; anything else triggers a
replan with the validator's reason.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent_control.llm.validator import AnswerValidator


class _Provider:
    def __init__(self, reply: str = "YES", raise_on_call: Exception | None = None) -> None:
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


@pytest.mark.asyncio
async def test_validate_returns_false_on_empty_raw_output() -> None:
    validator = AnswerValidator(_Provider())
    valid, reason = await validator.validate("anything", "")
    assert valid is False
    assert "empty" in reason.lower()


@pytest.mark.asyncio
async def test_validate_returns_false_on_whitespace_only() -> None:
    validator = AnswerValidator(_Provider())
    valid, reason = await validator.validate("anything", "   \n\t")
    assert valid is False
    assert "empty" in reason.lower()


@pytest.mark.asyncio
async def test_validate_returns_true_when_provider_says_yes() -> None:
    validator = AnswerValidator(_Provider(reply="YES"))
    valid, reason = await validator.validate("list episodes", "ep1\nep2")
    assert valid is True
    assert reason == "ok"


@pytest.mark.asyncio
async def test_validate_returns_true_for_yes_with_trailing_text() -> None:
    validator = AnswerValidator(_Provider(reply="YES, sufficient content present"))
    valid, _ = await validator.validate("q", "content")
    assert valid is True


@pytest.mark.asyncio
async def test_validate_returns_false_with_reason_after_colon() -> None:
    validator = AnswerValidator(_Provider(reply="NO: only 3 of 5 episodes returned"))
    valid, reason = await validator.validate("list 5 episodes", "ep1\nep2\nep3")
    assert valid is False
    assert "only 3 of 5" in reason


@pytest.mark.asyncio
async def test_validate_returns_false_with_raw_reason_when_no_colon() -> None:
    validator = AnswerValidator(_Provider(reply="missing items"))
    valid, reason = await validator.validate("q", "raw")
    assert valid is False
    assert reason == "missing items"


@pytest.mark.asyncio
async def test_validate_truncates_long_reason() -> None:
    validator = AnswerValidator(_Provider(reply="NO: " + ("x" * 500)))
    valid, reason = await validator.validate("q", "raw")
    assert valid is False
    assert len(reason) <= 300


@pytest.mark.asyncio
async def test_validate_does_not_block_on_provider_exception() -> None:
    # Validator failures must not block completion — return YES with a
    # diagnostic reason so the synthesizer still gets a chance.
    validator = AnswerValidator(_Provider(raise_on_call=RuntimeError("boom")))
    valid, reason = await validator.validate("q", "raw")
    assert valid is True
    assert reason == "validator_error"


@pytest.mark.asyncio
async def test_validate_passes_original_message_for_language_context() -> None:
    provider = _Provider(reply="YES")
    validator = AnswerValidator(provider)
    await validator.validate(
        "list five new episodes",
        "ep1\nep2",
        original_message="dizibox.com'a git",
    )
    user_prompt = provider.calls[0][1]
    assert "dizibox.com" in user_prompt
    assert "list five new episodes" in user_prompt


@pytest.mark.asyncio
async def test_validate_empty_provider_reply_returns_false() -> None:
    validator = AnswerValidator(_Provider(reply=""))
    valid, reason = await validator.validate("q", "raw")
    assert valid is False
    assert reason == "validator_returned_no"
