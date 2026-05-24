from __future__ import annotations

import asyncio
import re
from typing import Any

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import render_prompt
from agent_control.storage.repositories import Repositories


DEFAULT_SUMMARY = "No durable conversation memory yet."


class ConversationMemoryService:
    """Rolling conversation memory with a concise summary plus a small recent-turn window."""

    def __init__(
        self,
        repositories: Repositories,
        provider: LLMProvider | None = None,
        *,
        max_summary_chars: int = 2400,
        max_recent_turns: int = 10,
        summarization_timeout_seconds: int = 20,
    ) -> None:
        self.repositories = repositories
        self.provider = provider
        self.max_summary_chars = max_summary_chars
        self.max_recent_turns = max_recent_turns
        self.summarization_timeout_seconds = summarization_timeout_seconds

    async def update_from_user_message(self, conversation_id: str, text: str) -> dict[str, Any]:
        return await self.update_from_turn(conversation_id, "user", text)

    async def update_from_assistant_message(self, conversation_id: str, text: str) -> dict[str, Any]:
        return await self.update_from_turn(conversation_id, "assistant", text)

    async def update_from_task_summary(self, conversation_id: str, text: str) -> dict[str, Any]:
        return await self.update_from_turn(conversation_id, "task", text)

    async def update_from_turn(self, conversation_id: str, role: str, text: str) -> dict[str, Any]:
        existing = self.repositories.conversation_memory.get(conversation_id)
        state = dict((existing or {}).get("facts") or {})
        turns = _recent_turns(state)
        turns.append({"role": _clean_role(role), "text": _trim_turn(text)})
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


def memory_context(memory_record: dict[str, Any] | None, *, recent_turns: int = 4, max_chars: int = 1800) -> str:
    if not memory_record:
        return DEFAULT_SUMMARY
    summary = str(memory_record.get("summary") or DEFAULT_SUMMARY)
    state = dict(memory_record.get("facts") or {})
    turns = _recent_turns(state)[-recent_turns:]
    if not turns:
        return _clean_summary(summary, max_chars)
    recent = "\n".join(f"- {turn['role']}: {turn['text']}" for turn in turns)
    return _clean_summary(f"{summary}\nRecent turns:\n{recent}", max_chars)


def _summary_system_prompt(max_summary_chars: int) -> str:
    return render_prompt("base/conversation_memory_system.md", max_summary_chars=max_summary_chars)


def _summary_user_prompt(existing_summary: str, recent_turns: list[dict[str, str]]) -> str:
    turns = "\n".join(f"{turn['role']}: {turn['text']}" for turn in recent_turns)
    return render_prompt(
        "tasks/conversation_memory_user.md",
        existing_summary=existing_summary,
        recent_turns=turns,
    )


def _fallback_summary(existing_summary: str, recent_turns: list[dict[str, str]], limit: int) -> str:
    useful_existing = "" if existing_summary == DEFAULT_SUMMARY else _stable_existing_summary(existing_summary)
    latest = "; ".join(f"{turn['role']}: {turn['text']}" for turn in recent_turns[-3:] if turn.get("text"))
    if useful_existing and latest:
        summary = f"{useful_existing}\nRecent conversation context: {latest}"
    elif latest:
        summary = f"Recent conversation context: {latest}"
    else:
        summary = DEFAULT_SUMMARY
    return _clean_summary(summary, limit)


def _stable_existing_summary(existing_summary: str) -> str:
    text = existing_summary.strip()
    for marker in ("Recent conversation context:", "Recent user context:", "Recent turns:"):
        if marker in text:
            text = text.split(marker, 1)[0].strip()
    return text


def _recent_turns(state: dict[str, Any]) -> list[dict[str, str]]:
    turns = state.get("recent_turns") or []
    clean: list[dict[str, str]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        role = _clean_role(str(turn.get("role") or "user"))
        text = str(turn.get("text") or "").strip()
        if text:
            clean.append({"role": role, "text": _trim_turn(text)})
    return clean


def _clean_role(value: str) -> str:
    role = value.strip().lower().replace(" ", "_")
    if role in {"user", "assistant", "task", "system", "tool"}:
        return role
    return "user"


_PLACEHOLDER_PATH_PATTERN = re.compile(
    r"[A-Za-z]:[\\/]Users[\\/]"
    r"(?:me|user|username|youruser|your_user)"
    r"(?:[\\/][^\s\"'<>]*)?",
    re.IGNORECASE,
)


def _trim_turn(text: str, limit: int = 600) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else f"{clean[: limit - 3]}..."


def _strip_placeholder_paths(text: str) -> str:
    """Remove hallucinated paths like ``C:\\Users\\me\\Desktop\\foo.pdf`` from memory.

    If the LLM stored a placeholder username path in memory, the planner will
    happily re-use it on the next turn. Strip these before they propagate.
    Keep the basename if present so 'send me that <file>' still has the filename.
    """
    def replace(match: "re.Match[str]") -> str:
        path = match.group(0)
        # Try to keep the basename (e.g. "resume.pdf") since that's the useful part.
        tail = path.replace("/", "\\").rsplit("\\", 1)[-1]
        if "." in tail and len(tail) > 2:
            return tail
        return ""

    return _PLACEHOLDER_PATH_PATTERN.sub(replace, text)


def _clean_summary(text: str, limit: int) -> str:
    cleaned_lines = (
        _strip_placeholder_paths(line.strip())
        for line in text.strip().splitlines()
        if line.strip()
    )
    clean = " ".join(line for line in cleaned_lines if line)
    if not clean:
        return DEFAULT_SUMMARY
    return clean if len(clean) <= limit else f"{clean[: limit - 3]}..."
