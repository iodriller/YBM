from __future__ import annotations

from typing import Protocol

from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt
from agent_control.schemas import InboundMessage, IntentRoute, MessageClassification, OrchestrationIntent, TaskType


CLASSIFIER_SYSTEM_PROMPT = prompt_text("base/classifier_system.md")


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
            classification = await self.provider.generate_structured(
                CLASSIFIER_SYSTEM_PROMPT,
                _classification_prompt(message, context=context),
                MessageClassification,
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
        "tasks/classifier_user.md",
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
