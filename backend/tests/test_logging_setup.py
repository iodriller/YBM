from __future__ import annotations

from agent_control.logging_setup import _redact_event, _secret_values_from_environment


def test_logging_redacts_secret_values_embedded_in_event_text(monkeypatch) -> None:
    token = "123456:telegram-bot-secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    patterns = ["token", "api_key", "secret", "password"]

    processor = _redact_event(patterns, _secret_values_from_environment(patterns))
    result = processor(
        None,
        "info",
        {"event": f'POST https://api.telegram.org/bot{token}/sendMessage "HTTP/1.1 200 OK"'},
    )

    assert token not in result["event"]
    assert "bot***/sendMessage" in result["event"]
