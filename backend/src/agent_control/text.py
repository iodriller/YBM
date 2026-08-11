from __future__ import annotations


def trim_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."
