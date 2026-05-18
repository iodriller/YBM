from __future__ import annotations

import asyncio
from typing import Any

from agent_control.llm.providers import LLMProvider
from agent_control.storage.repositories import Repositories


DEFAULT_SUMMARY = "No durable conversation memory yet."


class ConversationMemoryService:
    """Rolling conversation memory with a concise summary plus a small recent-turn window."""

    def __init__(
        self,
        repositories: Repositories,
        provider: LLMProvider | None = None,
        *,
        max_summary_chars: int = 1200,
        max_recent_turns: int = 6,
        summarization_timeout_seconds: int = 20,
    ) -> None:
        self.repositories = repositories
        self.provider = provider
        self.max_summary_chars = max_summary_chars
        self.max_recent_turns = max_recent_turns
        self.summarization_timeout_seconds = summarization_timeout_seconds

    async def update_from_user_message(self, conversation_id: str, text: str) -> dict[str, Any]:
        existing = self.repositories.conversation_memory.get(conversation_id)
        state = dict((existing or {}).get("facts") or {})
        turns = _recent_turns(state)
        turns.append({"role": "user", "text": _trim_turn(text)})
        turns = turns[-self.max_recent_turns :]
        state["recent_turns"] = turns
        state["message_count"] = int(state.get("message_count") or 0) + 1
        state["strategy"] = "rolling_summary_with_recent_turns"

        existing_summary = str((existing or {}).get("summary") or DEFAULT_SUMMARY)
        summary = await self._summarize(existing_summary, turns)
        state["summary"] = summary
        return self.repositories.conversation_memory.upsert(conversation_id, summary, state)

    async def _summarize(self, existing_summary: str, recent_turns: list[dict[str, str]]) -> str:
        if self.provider is None:
            return _fallback_summary(existing_summary, recent_turns, self.max_summary_chars)
        try:
            output = await asyncio.wait_for(
                self.provider.generate_text(
                    _summary_system_prompt(self.max_summary_chars),
                    _summary_user_prompt(existing_summary, recent_turns),
                ),
                timeout=self.summarization_timeout_seconds,
            )
        except Exception:
            return _fallback_summary(existing_summary, recent_turns, self.max_summary_chars)
        return _clean_summary(output, self.max_summary_chars)


def memory_context(memory_record: dict[str, Any] | None, *, recent_turns: int = 4) -> str:
    if not memory_record:
        return DEFAULT_SUMMARY
    summary = str(memory_record.get("summary") or DEFAULT_SUMMARY)
    state = dict(memory_record.get("facts") or {})
    turns = _recent_turns(state)[-recent_turns:]
    if not turns:
        return summary
    recent = "\n".join(f"- {turn['role']}: {turn['text']}" for turn in turns)
    return f"{summary}\nRecent turns:\n{recent}"


def _summary_system_prompt(max_summary_chars: int) -> str:
    return f"""Maintain concise memory for a Telegram LLM gateway.
Return only a compact plain-text memory summary, no JSON and no preamble.
Keep durable facts, user preferences, project goals, decisions, constraints, and unresolved follow-ups.
Drop greetings, duplicate wording, and transient chatter.
Do not invent details.
Maximum length: {max_summary_chars} characters."""


def _summary_user_prompt(existing_summary: str, recent_turns: list[dict[str, str]]) -> str:
    turns = "\n".join(f"{turn['role']}: {turn['text']}" for turn in recent_turns)
    return f"""Existing memory:
{existing_summary}

Recent turns:
{turns}

Update the memory summary."""


def _fallback_summary(existing_summary: str, recent_turns: list[dict[str, str]], limit: int) -> str:
    useful_existing = "" if existing_summary == DEFAULT_SUMMARY else existing_summary.strip()
    latest = "; ".join(turn["text"] for turn in recent_turns[-3:] if turn.get("text"))
    if useful_existing and latest:
        summary = f"{useful_existing}\nRecent user context: {latest}"
    elif latest:
        summary = f"Recent user context: {latest}"
    else:
        summary = DEFAULT_SUMMARY
    return _clean_summary(summary, limit)


def _recent_turns(state: dict[str, Any]) -> list[dict[str, str]]:
    turns = state.get("recent_turns") or []
    clean: list[dict[str, str]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "user")[:20]
        text = str(turn.get("text") or "").strip()
        if text:
            clean.append({"role": role, "text": _trim_turn(text)})
    return clean


def _trim_turn(text: str, limit: int = 600) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else f"{clean[: limit - 3]}..."


def _clean_summary(text: str, limit: int) -> str:
    clean = " ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    if not clean:
        return DEFAULT_SUMMARY
    return clean if len(clean) <= limit else f"{clean[: limit - 3]}..."
