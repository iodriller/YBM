from __future__ import annotations

from agent_control.storage.redaction import redact_payload


def test_redact_payload_covers_widened_default_patterns() -> None:
    """Regression: DEFAULT_PATTERNS originally missed several common
    secret-shaped key names entirely - a field literally named
    "private_key" or "credential" passed through audit/task output
    unredacted."""
    # Values here are deliberately NOT secret-shaped: redaction keys off the
    # field *name*, so realistic-looking material would add nothing to the
    # test while tripping the repository's own secret scanner in CI.
    payload = {
        "private_key": "placeholder-value",
        "access_key": "placeholder-value",
        "credentials": {"nested": "placeholder-value"},
        "db_pwd": "placeholder-value",
        "passwd": "placeholder-value",
        "bearer_token_value": "placeholder-value",
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
    """Value-based redaction: a known secret is scrubbed out of free text even
    when the field name itself looks harmless."""
    injected = "vault-value-placeholder"
    redacted = redact_payload({"summary": f"used {injected} to call the API"}, secret_values=[injected])
    assert injected not in redacted["summary"]
