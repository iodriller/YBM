from __future__ import annotations

import re


def safe_path_segment(value: str, *, fallback: str) -> str:
    """Sanitize a model/user-derived value before using it as one path part."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or fallback
