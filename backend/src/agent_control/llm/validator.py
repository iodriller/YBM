from __future__ import annotations

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt


class AnswerValidator:
    """LLM-based validator that checks whether a synthesized answer addresses the objective.

    Checks:
    - Count match for "first N" style requests
    - No fabrication vs the raw tool output (when a snippet is provided)
    - Topic alignment
    - Language match with the user's original message
    """

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def validate(
        self,
        objective: str,
        answer: str,
        *,
        original_message: str | None = None,
        raw_snippet: str | None = None,
    ) -> tuple[bool, str]:
        """Return (valid, reason).

        On any exception, returns (True, "validator_error") to avoid blocking completion.
        """
        if not answer.strip():
            return False, "empty answer"
        system_prompt = prompt_text("base/validator_system.md")
        user_prompt = render_prompt(
            "tasks/validator_user.md",
            objective=objective,
            answer=answer,
            original_message=(original_message or "(same as normalized objective)").strip()[:1000],
            raw_snippet=(raw_snippet or "(no raw output snippet provided)").strip()[:1500],
        )
        try:
            result = await self.provider.generate_text(system_prompt, user_prompt)
        except Exception:
            return True, "validator_error"
        text = result.strip()
        if text.upper().startswith("YES"):
            return True, "ok"
        # Anything else (including "NO: ...") counts as invalid; capture the reason.
        reason = text.split(":", 1)[1].strip() if ":" in text else text
        return False, reason[:300] or "validator_returned_no"
