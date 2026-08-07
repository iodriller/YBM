from __future__ import annotations

from agent_control.storage.redaction import redact_payload


def test_redact_payload_covers_widened_default_patterns() -> None:
    """Regression: DEFAULT_PATTERNS originally missed several common
    secret-shaped key names entirely - a field literally named
    "private_key" or "credential" passed through audit/task output
    unredacted."""
    payload = {
        "private_key": "-----BEGIN PRIVATE KEY-----abc",
        "access_key": "AKIA-example",
        "credentials": {"nested": "value"},
        "db_pwd": "hunter2",
        "passwd": "hunter2",
        "bearer_token_value": "abc.def.ghi",
        "harmless_field": "keep me",
    }

    redacted = redact_payload(payload)

    assert redacted["private_key"] == "***"
    assert redacted["access_key"] == "***"
    assert redacted["credentials"] == "***"
    assert redacted["db_pwd"] == "***"
    assert redacted["passwd"] == "***"
    assert redacted["bearer_token_value"] == "***"
    assert redacted["harmless_field"] == "keep me"


def test_redact_payload_still_redacts_known_secret_values_in_strings() -> None:
    redacted = redact_payload({"summary": "used key sk-live-abc123 to call the API"}, secret_values=["sk-live-abc123"])
    assert "sk-live-abc123" not in redacted["summary"]
