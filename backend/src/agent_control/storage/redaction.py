from __future__ import annotations

from typing import Any


DEFAULT_PATTERNS = ("token", "api_key", "secret", "password")


def redact_payload(value: Any, patterns: list[str] | tuple[str, ...] | None = DEFAULT_PATTERNS) -> Any:
    patterns = patterns or DEFAULT_PATTERNS
    lowered_patterns = tuple(pattern.lower() for pattern in patterns)

    def redact(inner: Any, key: str | None = None) -> Any:
        if key and any(pattern in key.lower() for pattern in lowered_patterns):
            return "***"
        if isinstance(inner, dict):
            return {item_key: redact(item_value, item_key) for item_key, item_value in inner.items()}
        if isinstance(inner, list):
            return [redact(item) for item in inner]
        return inner

    return redact(value)
