"""The Concierge (docs/HISTORY.md P3 §2.1): classify chat-vs-task AND, when
it's chat, compose the reply - one LLM call, not two. Class names below stay
"classifier"-shaped since that's still an accurate description of the
Protocol/return type (MessageClassification, now carrying an optional
`reply`); prompts/base/concierge_system.md is where the merge actually lives.
"""

from __future__ import annotations

from typing import Protocol

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt
from agent_control.schemas import InboundMessage, IntentRoute, MessageClassification, OrchestrationIntent, TaskType


CLASSIFIER_SYSTEM_PROMPT = prompt_text("base/concierge_system.md")


class MessageClassifier(Protocol):
    async def classify(self, message: InboundMessage, context: str | None = None) -> MessageClassification:
        ...


class LLMMessageClassifier:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def classify(self, message: InboundMessage, context: str | None = None) -> MessageClassification:
        text = (message.text or "").strip()
        if not text:
            return MessageClassification(
                is_task=False,
                task_type=TaskType.OTHER,
                confidence=1.0,
                reason="message has no text content",
            )
        try:
            # Low temperature for classification — the choice of route / is_task
            # is a near-deterministic decision; sampling noise produces the
            # observed run-to-run flip-flops on borderline observation requests.
            classification = await self.provider.generate_structured(
                CLASSIFIER_SYSTEM_PROMPT,
                _classification_prompt(message, context=context),
                MessageClassification,
                temperature=0.1,
            )
            return _normalized_classification(message, classification)
        except Exception as exc:
            try:
                retry_prompt = render_prompt(
                    "tasks/structured_retry.md",
                    original_prompt=_classification_prompt(message, context=context),
                    error=str(exc),
                )
                classification = await self.provider.generate_structured(
                    CLASSIFIER_SYSTEM_PROMPT,
                    retry_prompt,
                    MessageClassification,
                    temperature=0.1,
                )
                return _normalized_classification(message, classification)
            except Exception as retry_exc:
                return emergency_classification(
                    message,
                    f"LLM intent classifier failed after retry: {retry_exc}",
                )


class StaticMessageClassifier:
    def __init__(self, classification: MessageClassification | None = None) -> None:
        self.classification = classification
        self.messages: list[InboundMessage] = []

    async def classify(self, message: InboundMessage, context: str | None = None) -> MessageClassification:
        self.messages.append(message)
        if self.classification:
            return self.classification
        return MessageClassification(
            is_task=True,
            task_type=TaskType.DEVELOPMENT,
            normalized_objective=message.text,
            confidence=1.0,
            reason="static test classifier",
        )


def _classification_prompt(message: InboundMessage, context: str | None = None) -> str:
    return render_prompt(
        "tasks/concierge_user.md",
        channel=message.channel.value,
        kind=message.kind.value,
        sender_id=message.sender_id,
        chat_id=message.chat_id,
        text=message.text or "",
        context=(context or "No prior conversation/task context."),
    )


def classification_trace(message: InboundMessage, context: str | None = None) -> dict[str, str]:
    return {
        "system_prompt": CLASSIFIER_SYSTEM_PROMPT,
        "user_prompt": _classification_prompt(message, context=context),
    }


def _normalized_classification(message: InboundMessage, classification: MessageClassification) -> MessageClassification:
    text = (message.text or "").strip()
    intent = classification.intent
    normalized = classification.normalized_objective
    updates: dict[str, object] = {}
    if classification.is_task and not normalized:
        updates["normalized_objective"] = (intent.objective if intent and intent.objective else text) or None
    if classification.is_task and intent is None:
        updates["intent"] = OrchestrationIntent(
            route=IntentRoute.UNKNOWN,
            operation=None,
            objective=updates.get("normalized_objective") if isinstance(updates.get("normalized_objective"), str) else text,
            reasoning="LLM classified the message as a task but did not provide a route.",
        )
    if intent is not None and not intent.objective and classification.is_task:
        updates["intent"] = intent.model_copy(update={"objective": normalized or text})
    if updates:
        return classification.model_copy(update=updates)
    return classification


def emergency_classification(message: InboundMessage, reason: str) -> MessageClassification:
    text = (message.text or "").strip()
    if text and _looks_actionable_request(text):
        return MessageClassification(
            is_task=True,
            task_type=TaskType.OTHER,
            normalized_objective=text,
            confidence=0.2,
            reason=reason,
            intent=OrchestrationIntent(
                route=IntentRoute.UNKNOWN,
                operation=None,
                objective=text,
                reasoning="LLM router was unavailable; the message is actionable but no route was selected.",
            ),
        )
    return MessageClassification(
        is_task=False,
        task_type=TaskType.OTHER,
        confidence=0.0,
        reason=reason if text else "message has no text content",
        intent=OrchestrationIntent(
            route=IntentRoute.CONVERSATION,
            operation=None,
            objective=None,
            reasoning="No task was spawned because the LLM router was unavailable.",
        ),
    )


def _looks_actionable_request(text: str) -> bool:
    """Broad heuristic for whether a message represents an actionable task.

    Used as a last-resort fallback when the LLM classifier fails. Bias toward
    YES — false positives ("act on this generic chat") get rejected by the
    planner, while false negatives ("ignore a real task") leave the user
    waiting with no acknowledgement.
    """
    lowered = text.lower()
    actionable_markers = (
        # explicit action verbs
        "inspect", "open", "create", "organize", "send", "schedule", "run",
        "search", "browse", "check", "fill", "summarize", "rename", "delete",
        "remove", "save", "download", "upload", "copy", "move", "extract",
        "scrape", "fetch", "get", "find", "list", "show", "tell me", "give me",
        "look", "watch", "monitor", "observe", "navigate", "visit", "click",
        "type", "write", "build", "make", "generate", "produce", "compute",
        "calculate", "translate", "convert",
        # natural-language openings for a request
        "go to", "head to", "i want", "i'd like", "can you", "could you",
        "please ", "would you",
        # nouns that almost always imply action context
        "screenshot", "desktop", "folder", "file", "job", "task", "chrome",
        "browser", "page", "url", "website", "site", ".com", ".net", ".org",
        # scheduling hints
        "every day", "daily", "weekly", "hourly", "remind me",
    )
    return any(marker in lowered for marker in actionable_markers)
