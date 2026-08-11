"""Voice messages, and what a person is told when one cannot be handled.

Written after a real report: "I recorded my voice at telegram and sent and it
didn't work." The pipeline was implemented; the reply was
"Voice transcription failed: RuntimeError: STT adapter is disabled".
"""

from __future__ import annotations

import pytest

from agent_control.error_text import describe_exception, explain_for_user, explain_voice_failure


def test_disabled_transcription_is_explained_not_reported_as_a_crash() -> None:
    """Speech-to-text is off by default, so this was the common path. The user
    should learn the feature is off and what to do instead."""
    message = explain_voice_failure(RuntimeError("STT adapter is disabled"))
    assert "RuntimeError" not in message
    assert "disabled" not in message.lower() or "turned off" in message.lower()
    assert "text" in message.lower(), "must offer a way forward"


def test_a_missing_voice_package_says_so_plainly() -> None:
    message = explain_voice_failure(ModuleNotFoundError("No module named 'faster_whisper'"))
    assert "faster_whisper" not in message
    assert "install" in message.lower()


def test_a_slow_transcription_suggests_something_shorter() -> None:
    message = explain_voice_failure(TimeoutError("timed out after 120s"))
    assert "shorter" in message.lower()


def test_an_unknown_voice_failure_still_says_what_to_do() -> None:
    message = explain_voice_failure(ValueError("something odd"))
    assert "something odd" not in message
    assert message.endswith(".")


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RuntimeError("Anthropic rejected the API key"), "Settings"),
        (RuntimeError("Anthropic rate limit reached"), "Wait"),
        (ValueError("base_url is required for OpenAI-compatible LLM provider"), "base URL"),
        (RuntimeError("allowlist_empty"), "Telegram"),
        (RuntimeError("request failed with HTTP 502"), "minute"),
        (TimeoutError("timed out"), "faster"),
    ],
)
def test_known_failures_say_what_happened_and_what_to_do(exc: BaseException, expected: str) -> None:
    message = explain_for_user(exc)
    assert expected in message
    # Never a class name or an internal stage.
    assert type(exc).__name__ not in message
    assert "operator_decide" not in message


def test_the_diagnostic_string_is_still_available_for_the_trace() -> None:
    """explain_for_user is for the person; describe_exception stays for the log,
    where the class name is the useful part."""
    exc = RuntimeError("request failed with HTTP 502")
    assert "RuntimeError" in describe_exception(exc)
    assert "RuntimeError" not in explain_for_user(exc)


def test_every_user_facing_message_is_a_sentence_not_a_token() -> None:
    for exc in (
        RuntimeError("STT adapter is disabled"),
        RuntimeError("allowlist_empty"),
        RuntimeError("HTTP 503"),
    ):
        for message in (explain_voice_failure(exc), explain_for_user(exc)):
            assert message[0].isupper(), message
            assert " " in message.strip(), message
            assert "_" not in message, f"internal token leaked: {message}"
