from __future__ import annotations

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt


class AnswerValidator:
    """LLM-based validator that checks whether a synthesized answer addresses the objective."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def validate(self, objective: str, answer: str) -> bool:
        """Return True if the answer addresses the objective, False if it does not.

        On any exception, returns True to avoid blocking completion on validator failure.
        """
        if not answer.strip():
            return False
        system_prompt = prompt_text("base/validator_system.md")
        user_prompt = render_prompt("tasks/validator_user.md", objective=objective, answer=answer)
        try:
            result = await self.provider.generate_text(system_prompt, user_prompt)
        except Exception:
            return True  # validator failure should not block completion
        return result.strip().upper().startswith("YES")
