from __future__ import annotations

from typing import Protocol

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt
from agent_control.schemas import InboundMessage, MessageClassification, TaskType


CLASSIFIER_SYSTEM_PROMPT = prompt_text("base/classifier_system.md")


class MessageClassifier(Protocol):
    async def classify(self, message: InboundMessage) -> MessageClassification:
        ...


class LLMMessageClassifier:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def classify(self, message: InboundMessage) -> MessageClassification:
        text = (message.text or "").strip()
        if not text:
            return MessageClassification(
                is_task=False,
                task_type=TaskType.OTHER,
                confidence=1.0,
                reason="message has no text content",
            )
        try:
            return await self.provider.generate_structured(
                CLASSIFIER_SYSTEM_PROMPT,
                _classification_prompt(message),
                MessageClassification,
            )
        except Exception as exc:
            fallback = heuristic_classification(message)
            return fallback.model_copy(
                update={"reason": f"{fallback.reason}; LLM classifier fallback after: {exc}"}
            )


class StaticMessageClassifier:
    def __init__(self, classification: MessageClassification | None = None) -> None:
        self.classification = classification
        self.messages: list[InboundMessage] = []

    async def classify(self, message: InboundMessage) -> MessageClassification:
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


def _classification_prompt(message: InboundMessage) -> str:
    return render_prompt(
        "tasks/classifier_user.md",
        channel=message.channel.value,
        kind=message.kind.value,
        sender_id=message.sender_id,
        chat_id=message.chat_id,
        text=message.text or "",
    )


def classification_trace(message: InboundMessage) -> dict[str, str]:
    return {
        "system_prompt": CLASSIFIER_SYSTEM_PROMPT,
        "user_prompt": _classification_prompt(message),
    }


def heuristic_classification(message: InboundMessage) -> MessageClassification:
    text = (message.text or "").strip()
    lowered = text.lower()
    if not text:
        return MessageClassification(
            is_task=False,
            task_type=TaskType.OTHER,
            confidence=1.0,
            reason="message has no text content",
        )

    if lowered in {"hi", "hello", "hey", "yo", "thanks", "thank you"}:
        return MessageClassification(
            is_task=False,
            task_type=TaskType.OTHER,
            confidence=0.85,
            reason="short conversational message",
        )

    if lowered in {"status", "task status", "tasks status", "what is the status"}:
        return MessageClassification(
            is_task=False,
            task_type=TaskType.STATUS_REQUEST,
            confidence=0.9,
            reason="status request",
        )

    if any(phrase in lowered for phrase in {"what can you do", "what are your capabilities", "help", "who are you"}):
        return MessageClassification(
            is_task=False,
            task_type=TaskType.QUESTION,
            confidence=0.85,
            reason="capability or help question",
        )

    desktop_markers = ("my desktop", "desktop", "my screen", "screen shot", "take a screenshot", "screenshot")
    browser_target_markers = ("browser", "chrome", "website", "web page", "webpage", "http://", "https://", "www.")
    if any(marker in lowered for marker in desktop_markers) and not any(marker in lowered for marker in browser_target_markers):
        return MessageClassification(
            is_task=True,
            task_type=TaskType.DESKTOP_OBSERVATION,
            normalized_objective=text,
            confidence=0.75,
            reason="heuristic desktop observation request",
        )

    browser_markers = (
        "open browser",
        "open chrome",
        "search ",
        "google ",
        "bing ",
        "go to ",
        "open http",
        "open www.",
        "website",
        "web page",
        "webpage",
        "what tabs",
        "current tabs",
        "close tab",
        "fill the form",
        "click ",
    )
    if any(marker in lowered for marker in browser_markers):
        return MessageClassification(
            is_task=True,
            task_type=TaskType.OTHER,
            normalized_objective=text,
            confidence=0.7,
            reason="heuristic browser action request",
        )

    if any(marker in lowered for marker in ("scheduled job", "schedule ", "set up a scheduled", "every day", "every week", "every hour", "every minute")):
        return MessageClassification(
            is_task=True,
            task_type=TaskType.OTHER,
            normalized_objective=text,
            confidence=0.75,
            reason="heuristic scheduled task request",
        )

    task_markers = (
        "build",
        "create",
        "write",
        "implement",
        "fix",
        "change",
        "update",
        "inspect",
        "run",
        "generate",
        "make",
        "set up",
    )
    if any(marker in lowered for marker in task_markers):
        task_type = TaskType.CONFIGURATION if "config" in lowered or "setting" in lowered else TaskType.DEVELOPMENT
        return MessageClassification(
            is_task=True,
            task_type=task_type,
            normalized_objective=text,
            confidence=0.65,
            reason="heuristic actionable task request",
        )

    if lowered.endswith("?"):
        return MessageClassification(
            is_task=False,
            task_type=TaskType.QUESTION,
            confidence=0.75,
            reason="question",
        )

    return MessageClassification(
        is_task=False,
        task_type=TaskType.OTHER,
        confidence=0.55,
        reason="no clear actionable task marker",
    )
