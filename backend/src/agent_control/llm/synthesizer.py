from __future__ import annotations

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt

_CONTENT_TOOLS = frozenset({
    "browser.open",
    "browser.control",
    "code.interpreter",
    "filesystem.manage",
    "document.manage",
    "computer.use",
})


class ResponseSynthesizer:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    @staticmethod
    def is_content_tool(tool_name: str) -> bool:
        return tool_name in _CONTENT_TOOLS

    async def synthesize(self, objective: str, raw_content: str) -> str | None:
        """Return a focused answer, or None if content is insufficient to answer the question."""
        if not raw_content.strip():
            return None
        system_prompt = prompt_text("base/synthesizer_system.md")
        user_prompt = f"Question: {objective}\n\nRaw content:\n{raw_content[:6000]}"
        try:
            result = await self.provider.generate_text(system_prompt, user_prompt)
        except Exception:
            return None
        text = result.strip()
        if not text or text.upper().startswith("INSUFFICIENT"):
            return None
        return text
