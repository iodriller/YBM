from __future__ import annotations

import logging

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt


logger = logging.getLogger(__name__)


class AnswerValidator:
    """LLM-based pre-synthesis validator: checks whether raw tool output is sufficient.

    Runs BEFORE the synthesizer. If the raw output doesn't contain the data needed to
    answer the question, the worker can replan immediately with a specific reason rather
    than wasting a synthesizer call on insufficient content.

    Checks:
    - Count match for "first N" / "top N" style requests
    - Topic alignment (raw mentions what was asked)
    - Section presence (named sections actually contain items)
    """

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def validate(
        self,
        objective: str,
        raw_output: str,
        *,
        original_message: str | None = None,
    ) -> tuple[bool, str]:
        """Return (valid, reason).

        valid=True means raw output is sufficient; the synthesizer should run next.
        valid=False means replan needed; ``reason`` explains what is missing.
        On any provider exception, returns (True, "validator_error") to avoid blocking
        completion on validator infrastructure issues.
        """
        if not raw_output.strip():
            return False, "empty raw output"
        system_prompt = prompt_text("base/validator_system.md")
        user_prompt = render_prompt(
            "tasks/validator_user.md",
            objective=objective,
            original_message=(original_message or "(same as normalized objective)").strip()[:1000],
            raw_output=raw_output.strip()[:4000],
        )
        try:
            result = await self.provider.generate_text(system_prompt, user_prompt)
        except Exception:
            logger.debug("validator provider call failed; passing through to synthesizer", exc_info=True)
            return True, "validator_error"
        text = result.strip()
        if text.upper().startswith("YES"):
            return True, "ok"
        reason = text.split(":", 1)[1].strip() if ":" in text else text
        return False, reason[:300] or "validator_returned_no"
