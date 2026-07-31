"""The Auditor (docs/HISTORY.md P3 §2.1): "did we actually achieve the
goal?" Merges the old two-call validator-then-synthesizer sequence into one
- check whether raw tool output actually grounds an answer, and if it does,
extract the focused answer, in a single LLM call.

Invoked from the Operator loop (orchestration/worker.py) right before a
`done` decision is allowed to complete, and only when the loop actually
called a "content tool" (browser, filesystem, document, code-interpreter,
etc.) - a task that never touched one (status checks, schedule management)
has nothing to audit and skips this entirely, same as the old
`ResponseSynthesizer.is_content_tool` gate did.
"""

from __future__ import annotations

import logging

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt
from agent_control.schemas import StrictBaseModel


logger = logging.getLogger(__name__)

AUDITOR_SYSTEM_PROMPT = prompt_text("base/auditor_system.md")

CONTENT_TOOLS = frozenset({
    "browser.open",
    "browser.control",
    "code.interpreter",
    "filesystem.manage",
    "document.manage",
    "computer.use",
    "http.request",
    "mcp.client",
    # A delegated sub-task's own tool calls update the parent task's
    # last_tool_output_text (worker.py's _run_delegate docstring explains
    # why that's deliberate, not a leak) - recognizing "delegate" here lets
    # a `done` that immediately follows one get grounded in that real
    # output, instead of the audit gate being skipped just because
    # "delegate" itself isn't a content-producing tool name.
    "delegate",
})


class AuditResult(StrictBaseModel):
    sufficient: bool
    answer: str | None = None
    reason: str | None = None


class AuditorService:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        # Usage from the most recent audit() call - see
        # docs/HISTORY.md Part 4 T1.4.
        self.last_usage: dict | None = None

    @staticmethod
    def is_content_tool(tool_name: str | None) -> bool:
        return tool_name in CONTENT_TOOLS

    async def audit(
        self, objective: str, raw_output: str, *, original_message: str | None = None
    ) -> AuditResult:
        """Checks raw_output is grounded evidence for objective, and if so,
        returns the focused answer extracted from it.

        On any provider failure, fails open (sufficient=True, answer=None) -
        the caller falls back to whatever answer it already had, the same
        "don't block completion on audit infrastructure issues" choice the
        old validator made (it returned (True, "validator_error") on
        exceptions rather than stalling the task).
        """
        if not raw_output.strip():
            return AuditResult(sufficient=False, reason="empty raw output")
        user_prompt = render_prompt(
            "tasks/auditor_user.md",
            objective=objective,
            original_message=(original_message or "(same as normalized objective)").strip()[:1000],
            raw_output=raw_output.strip()[:6000],
        )
        try:
            result = await self.provider.generate_text(AUDITOR_SYSTEM_PROMPT, user_prompt)
            self.last_usage = getattr(self.provider, "last_usage", None)
        except Exception:
            logger.warning("auditor provider call failed; not blocking completion on it", exc_info=True)
            return AuditResult(sufficient=True, answer=None, reason="auditor_error")
        text = result.strip()
        if not text:
            return AuditResult(sufficient=False, reason="auditor_returned_empty")
        if text.upper().startswith("INSUFFICIENT"):
            reason = text.split(":", 1)[1].strip() if ":" in text else text
            return AuditResult(sufficient=False, reason=(reason[:300] or "insufficient"))
        return AuditResult(sufficient=True, answer=text)
