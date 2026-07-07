from __future__ import annotations

import logging

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt


logger = logging.getLogger(__name__)

_CONTENT_TOOLS = frozenset({
    "browser.open",
    "browser.control",
    "code.interpreter",
    "filesystem.manage",
    "document.manage",
    "computer.use",
    "mcp.client",
})


class ResponseSynthesizer:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    @staticmethod
    def is_content_tool(tool_name: str) -> bool:
        return tool_name in _CONTENT_TOOLS

    async def synthesize(
        self, objective: str, raw_content: str, *, original_message: str | None = None
    ) -> str | None:
        """Return a focused answer, or None if content is insufficient to answer the question.

        ``original_message`` is the user's verbatim message — if provided, the LLM gets
        both the normalized objective and the original wording so it can preserve URLs,
        section names, and respond in the user's language.
        """
        if not raw_content.strip():
            return None
        system_prompt = prompt_text("base/synthesizer_system.md")
        question_block = f"Question: {objective}"
        if original_message and original_message.strip() and original_message.strip() != objective.strip():
            question_block = (
                f"User's original message (use this for language and exact terminology):\n"
                f"{original_message.strip()}\n\n"
                f"Normalized question: {objective}"
            )
        user_prompt = f"{question_block}\n\nRaw content:\n{raw_content[:6000]}"
        try:
            result = await self.provider.generate_text(system_prompt, user_prompt)
        except Exception:
            logger.debug("synthesizer provider call failed; treating as insufficient", exc_info=True)
            return None
        text = result.strip()
        if not text or text.upper().startswith("INSUFFICIENT"):
            return None
        return text
