from __future__ import annotations

import re
from typing import Any


DEFAULT_PATTERNS = ("token", "api_key", "secret", "password", "authorization", "cookie")

PLACEHOLDER = "***"

# Key-name matching only sees a credential when it is the *value of its own
# field*. A credential read out of a user's file arrives as one free-text blob
# under an innocuous key like "summary", and it was never in YBM's vault, so
# neither existing rule could see it - a config file's API key reached both the
# chat reply and the audit trail (docs/E2E_FINDINGS.md P0-1). These patterns
# match on the shape of the value itself, which is what survives being embedded
# in prose.

# `NAME=value` / `"name": value` where the name itself announces a credential.
# The name is deliberately preserved: "the file also sets ACME_API_KEY" is
# useful, its value never is.
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_.-]*(?:API[_-]?KEY|SECRET|PASSWORD|PASSWD|TOKEN|CREDENTIALS?|PRIVATE[_-]?KEY)[A-Z0-9_.-]*)"
    r"(\s*[:=]\s*)"
    r"(\"[^\"\n]+\"|'[^'\n]+'|[^\s,;}]+)"
)

# Provider-issued credentials are recognizable on their own, with no key name
# nearby - these appear mid-sentence in summaries and error text.
_TOKEN_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),                      # OpenAI-style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),                # GitHub
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),              # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                        # AWS access key id
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}"),             # Telegram bot token
)


def redact_text(text: str) -> str:
    """Strip credential-shaped values out of free text.

    Applied to text that is about to be persisted or sent to a user. Redacts
    the value and keeps its surrounding context, so an answer can still say
    which setting exists without disclosing what it is worth.
    """
    if not text:
        return text
    redacted = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}{PLACEHOLDER}", text)
    for pattern in _TOKEN_SHAPES:
        redacted = pattern.sub(PLACEHOLDER, redacted)
    return redacted


def redact_payload(
    value: Any,
    patterns: list[str] | tuple[str, ...] | None = DEFAULT_PATTERNS,
    secret_values: list[str] | tuple[str, ...] | None = None,
) -> Any:
    patterns = patterns or DEFAULT_PATTERNS
    lowered_patterns = tuple(pattern.lower() for pattern in patterns)
    value_secrets = tuple(secret for secret in (secret_values or ()) if secret)

    def redact(inner: Any, key: str | None = None) -> Any:
        if key and any(pattern in key.lower() for pattern in lowered_patterns):
            return PLACEHOLDER
        if isinstance(inner, dict):
            return {item_key: redact(item_value, item_key) for item_key, item_value in inner.items()}
        if isinstance(inner, list):
            return [redact(item) for item in inner]
        if isinstance(inner, str):
            redacted = inner
            for secret in value_secrets:
                redacted = redacted.replace(secret, PLACEHOLDER)
            # Content scan last: catches credentials embedded in a blob whose
            # own key name says nothing, which the two rules above cannot see.
            return redact_text(redacted)
        return inner

    return redact(value)
