"""The Operator loop: observe -> decide -> act - the sole execution path
(docs/HISTORY.md P3 §2.2), replacing the old plan-once-then-replan path.
"""

from __future__ import annotations

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

    async def decide(
        self,
        objective: str,
        config_context: str,
        history: list[dict],
        *,
        memory_context: str = "",
    ) -> OperatorDecision:
        user_prompt = self._prompt(objective, config_context, history, memory_context)
        provider = self.provider
        last_error: Exception | None = None
        current_prompt = user_prompt
        for _attempt in range(3):
            try:
                candidate = await provider.generate_structured(
                    OPERATOR_SYSTEM_PROMPT, current_prompt, OperatorDecision, temperature=0.1
                )
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
