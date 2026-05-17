from __future__ import annotations

from typing import Protocol

from agent_control.llm.providers import LLMProvider
from agent_control.schemas import InboundMessage, MessageClassification, TaskType


CLASSIFIER_SYSTEM_PROMPT = """You classify inbound messages for a local agentic control system.
Return only JSON matching the requested schema.
Classify as a task only when the user is asking the system to do work, change configuration, inspect state, or start a workflow.
Questions, greetings, and status requests are not tasks unless they ask for an actionable change."""


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
        return await self.provider.generate_structured(
            CLASSIFIER_SYSTEM_PROMPT,
            _classification_prompt(message),
            MessageClassification,
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
    return f"""Classify this inbound message.

Channel: {message.channel.value}
Kind: {message.kind.value}
Sender: {message.sender_id}
Chat: {message.chat_id}
Text:
{message.text or ""}

Return:
- is_task true only if it should spawn a persisted task.
- task_type as one of the allowed enum values.
- normalized_objective as the concise work objective when is_task is true.
- reason explaining the decision."""
