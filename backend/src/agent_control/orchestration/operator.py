"""The Operator loop: observe -> decide -> act - the sole execution path
(docs/HISTORY.md P3 §2.2), replacing the old plan-once-then-replan path.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt
from agent_control.schemas import OperatorDecision


OPERATOR_SYSTEM_PROMPT = prompt_text("base/operator_system.md")

_MAX_HISTORY_ENTRIES = 12  # bound the prompt; older entries are summarized away


class OperatorLoopService:
    def __init__(self, provider: LLMProvider, major_provider: LLMProvider | None = None) -> None:
        self.provider = provider
        self.major_provider = major_provider
        # Usage from the most recent decide() call, read by worker.py right
        # after each call to accumulate per-task token/cost totals. See
        # docs/HISTORY.md Part 4 T1.4.
        self.last_usage: dict | None = None
        # Siblings to last_usage, read by worker.py to persist the full call
        # record (docs/UI_UX_AUDIT.md Phase 14d).
        self.last_request: list[dict] | None = None
        self.last_response_text: str | None = None
        self.last_model: str | None = None
        self.last_started_at: datetime | None = None
        self.last_latency_ms: float | None = None

    async def decide(
        self,
        objective: str,
        config_context: str,
        history: list[dict],
        *,
        memory_context: str = "",
        prefer_major: bool = False,
    ) -> OperatorDecision:
        """`prefer_major` (docs/HISTORY.md Part 4 T2.6): start this call on
        major_provider instead of provider, when the caller already has a
        concrete, local, zero-cost-to-check signal that the default model is
        struggling on this task - worker.py sets it once history contains an
        audit-gap or fulfillment-gap retry marker, meaning a `done` was
        already rejected once. This extends, rather than replaces, the
        parse-failure escalation below: both exist because guessing
        complexity from objective text up front is worse than reacting to
        observed difficulty (see that escalation's own comment) - this is
        still reactive, just to a signal available before the call instead
        of only from a caught exception during it.

        Deliberately NOT applied to delegated sub-tasks (worker.py's
        _run_delegate never sets this): a sub-task is the "worker" side of
        the 2026 supervisor/worker cost pattern - it should default to the
        cheaper model, the same as any other step, not inherit escalation
        just because delegation itself is happening.
        """
        user_prompt = self._prompt(objective, config_context, history, memory_context)
        provider = self.major_provider if (prefer_major and self.major_provider is not None) else self.provider
        last_error: Exception | None = None
        current_prompt = user_prompt
        for _attempt in range(3):
            try:
                candidate = await provider.generate_structured(
                    OPERATOR_SYSTEM_PROMPT, current_prompt, OperatorDecision, temperature=0.1
                )
                self.last_usage = getattr(provider, "last_usage", None)
                self.last_request = getattr(provider, "last_request", None)
                self.last_response_text = getattr(provider, "last_response_text", None)
                self.last_model = getattr(provider, "last_model", None)
                self.last_started_at = getattr(provider, "last_started_at", None)
                self.last_latency_ms = getattr(provider, "last_latency_ms", None)
                return candidate
            except (ValueError, ValidationError) as exc:
                last_error = exc
                current_prompt = render_prompt(
                    "tasks/structured_retry.md",
                    original_prompt=user_prompt,
                    error=str(exc)[:2000],
                )
                # Escalate to the major provider (larger context/capacity)
                # after an OBSERVED decide() failure, same reasoning as the
                # old planner's escalation (docs/HISTORY.md P3 item 5): a
                # retry costs one extra call on a genuinely hard step, cheaper
                # than guessing complexity from objective text up front.
                if self.major_provider is not None and provider is not self.major_provider:
                    provider = self.major_provider
        assert last_error is not None
        raise last_error

    @staticmethod
    def _prompt(objective: str, config_context: str, history: list[dict], memory_context: str) -> str:
        memory_section = f"## Conversation context\n{memory_context}\n\n" if memory_context.strip() else ""
        return render_prompt(
            "tasks/operator_user.md",
            objective=objective,
            config_context=config_context,
            memory_context=memory_section,
            history=_format_history(history),
        )


def _format_history(history: list[dict]) -> str:
    if not history:
        return "(none yet - this is the first step)"
    recent = history[-_MAX_HISTORY_ENTRIES:]
    lines = []
    if len(history) > len(recent):
        lines.append(f"[{len(history) - len(recent)} earlier step(s) omitted]")
    for index, entry in enumerate(recent, start=1):
        tool_name = entry.get("tool_name", "?")
        status = entry.get("status", "?")
        line = f"{index}. {tool_name} ({status})"
        tool_input = entry.get("input")
        if tool_input:
            line += f"\n   input: {tool_input}"
        if entry.get("error"):
            line += f"\n   error: {entry['error']}"
        elif entry.get("output_summary"):
            line += f"\n   output: {entry['output_summary']}"
        lines.append(line)
    return "\n".join(lines)
