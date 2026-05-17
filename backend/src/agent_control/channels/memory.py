from __future__ import annotations

import re
from typing import Any


def update_memory_from_text(existing: dict[str, Any] | None, text: str) -> tuple[str, dict[str, Any]]:
    facts = dict((existing or {}).get("facts") or {})
    clean_text = " ".join(text.split())

    name = _extract_name(clean_text)
    if name:
        facts["user_name"] = name

    topics = list(facts.get("recent_topics") or [])
    topic = _topic(clean_text)
    if topic and topic not in topics:
        topics.append(topic)
    facts["recent_topics"] = topics[-4:]

    summary_parts: list[str] = []
    if facts.get("user_name"):
        summary_parts.append(f"user name: {facts['user_name']}")
    if facts.get("recent_topics"):
        summary_parts.append("recent topics: " + ", ".join(facts["recent_topics"]))
    summary = "; ".join(summary_parts) if summary_parts else "No durable user facts yet."
    return summary[:700], facts


def _extract_name(text: str) -> str | None:
    patterns = [
        r"\bmy name is\s+([A-Za-z][A-Za-z0-9 _'\-]{0,60})",
        r"\bcall me\s+([A-Za-z][A-Za-z0-9 _'\-]{0,60})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1)
            value = re.split(r"[.!?,;:]|\band\b|\bplease\b", value, maxsplit=1, flags=re.IGNORECASE)[0]
            return " ".join(value.strip().split())[:60]
    return None


def _topic(text: str) -> str | None:
    lowered = text.lower()
    if len(lowered) < 12:
        return None
    if "web app" in lowered:
        return "web app previews"
    if "copilot" in lowered:
        return "GitHub Copilot routing"
    if "status" in lowered or "task" in lowered:
        return "task status"
    if "vscode" in lowered or "vs code" in lowered:
        return "VS Code bridge"
    return None
