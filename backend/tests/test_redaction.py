"""Credential handling at the two sinks a secret can escape through.

A config file's API key reached both the Telegram reply and the audit trail
(docs/E2E_FINDINGS.md P0-1): key-name matching only sees a credential that is
the value of its own field, and vault-value matching only sees secrets YBM
already knows. A key read out of a *user's* file is neither.
"""
from __future__ import annotations

from agent_control.schemas import TaskRecord, TaskStatus
from agent_control.storage.redaction import redact_payload, redact_text


CANARY = "sk-live-EVOLEAK-9931-DO-NOT-ECHO"
CONFIG_BLOB = (
    "# Billing exporter configuration\n"
    "EXPORTER_NAME=billing-exporter\n"
    "EXPORTER_REGION=eu-west-1\n"
    f"ACME_API_KEY={CANARY}\n"
    "RETRY_LIMIT=5\n"
    "TIMEOUT_SECONDS=45\n"
)


def test_credential_embedded_in_free_text_is_redacted() -> None:
    redacted = redact_text(CONFIG_BLOB)

    assert CANARY not in redacted
    assert "EVOLEAK" not in redacted


def test_redaction_keeps_everything_that_is_not_the_secret() -> None:
    """Over-redaction has its own cost: the answer the user actually asked for
    is the non-sensitive fields, and the key's *name* is legitimate context."""
    redacted = redact_text(CONFIG_BLOB)

    for kept in ("billing-exporter", "eu-west-1", "RETRY_LIMIT=5", "TIMEOUT_SECONDS=45"):
        assert kept in redacted
    assert "ACME_API_KEY" in redacted


def test_provider_token_shapes_are_redacted_without_a_key_name() -> None:
    """These turn up mid-sentence in summaries and error text, where there is
    no adjacent field name to match on."""
    samples = [
        "the token is ghp_abcdefghijklmnopqrstuvwxyz012345 here",
        "using xoxb-1234567890-abcdefghijkl now",
        "AKIAIOSFODNN7EXAMPLE was rejected",
        "bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    ]
    for sample in samples:
        redacted = redact_text(sample)
        assert "***" in redacted, sample
        for token in sample.split():
            if len(token) > 20:
                assert token not in redacted, sample


def test_ordinary_text_is_untouched() -> None:
    plain = "The folder holds notes.txt, budget.csv, and a PDF. Retention is 400 days."

    assert redact_text(plain) == plain


def test_audit_payload_scans_string_values_not_just_key_names() -> None:
    """AuditLogger.append funnels every payload through redact_payload, so the
    content scan there covers the whole audit sink."""
    payload = redact_payload({"summary": CONFIG_BLOB, "count": 3})

    assert CANARY not in payload["summary"]
    assert payload["count"] == 3


def test_existing_key_name_and_vault_value_rules_still_apply() -> None:
    payload = redact_payload({"api_key": "abc123", "note": "vault value zzz9"}, secret_values=["zzz9"])

    assert payload["api_key"] == "***"
    assert "zzz9" not in payload["note"]


def test_task_notification_text_is_redacted_for_every_channel() -> None:
    """format_task_message is the one formatter every channel renders, so it is
    where a credential can be stopped before it leaves as a chat message."""
    from agent_control.channels.task_notify import format_task_message

    task = TaskRecord(
        objective="summarize the config",
        status=TaskStatus.COMPLETED,
        metadata={"synthesized_answer": f"The service config contains {CONFIG_BLOB}"},
    )

    message = format_task_message(task)

    assert CANARY not in message
    assert "billing-exporter" in message
