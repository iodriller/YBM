from __future__ import annotations

import pytest

from agent_control.llm.classifier import LLMMessageClassifier, emergency_classification
from agent_control.schemas import (
    ChannelType,
    InboundMessage,
    IntentRoute,
    MessageClassification,
    MessageKind,
    OrchestrationIntent,
    TaskType,
)


class RetryProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, system_prompt, user_prompt, output_model, **_ignored_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ValueError("bad json")
        return MessageClassification(
            is_task=True,
            task_type=TaskType.OTHER,
            normalized_objective=None,
            confidence=0.91,
            reason="LLM routed recurring work",
            intent=OrchestrationIntent(
                route=IntentRoute.SCHEDULE_MANAGE,
                operation="create",
                objective="Check the product page every morning",
                reasoning="The user asked for recurring monitoring.",
                cadence="daily",
                scheduled_objective="Check the product page",
            ),
        )

    async def generate_text(self, system_prompt, user_prompt):
        return "unused"


@pytest.mark.asyncio
async def test_llm_classifier_retries_and_returns_structured_intent() -> None:
    provider = RetryProvider()
    classifier = LLMMessageClassifier(provider)

    classification = await classifier.classify(
        InboundMessage(
            channel=ChannelType.TELEGRAM,
            kind=MessageKind.TEXT,
            sender_id="42",
            chat_id="100",
            text="handle this recurring monitor",
        )
    )

    assert provider.calls == 2
    assert classification.is_task is True
    assert classification.normalized_objective == "Check the product page every morning"
    assert classification.intent is not None
    assert classification.intent.route == IntentRoute.SCHEDULE_MANAGE
    assert classification.intent.cadence == "daily"


def test_emergency_classification_spawns_unknown_task_for_actionable_text_without_route_guessing() -> None:
    classification = emergency_classification(
        InboundMessage(
            channel=ChannelType.TELEGRAM,
            kind=MessageKind.TEXT,
            sender_id="42",
            chat_id="100",
            text="Take a screenshot of my desktop and send it to me now",
        ),
        "LLM down",
    )

    assert classification.is_task is True
    assert classification.intent is not None
    assert classification.intent.route == IntentRoute.UNKNOWN
    assert classification.normalized_objective == "Take a screenshot of my desktop and send it to me now"
    assert classification.reason == "LLM down"


def test_emergency_classification_keeps_plain_chat_as_conversation() -> None:
    classification = emergency_classification(
        InboundMessage(
            channel=ChannelType.TELEGRAM,
            kind=MessageKind.TEXT,
            sender_id="42",
            chat_id="100",
            text="thanks",
        ),
        "LLM down",
    )

    assert classification.is_task is False
    assert classification.intent is not None
    assert classification.intent.route == IntentRoute.CONVERSATION


def test_message_classification_accepts_route_alias_task_type() -> None:
    classification = MessageClassification.model_validate(
        {
            "is_task": True,
            "task_type": "browser.control",
            "normalized_objective": "Fill the form and send a screenshot",
            "confidence": 0.88,
            "reason": "The user asked to fill a browser form.",
            "intent": {
                "route": "browser.control",
                "operation": "fill_form_step",
                "objective": "Fill the form and send a screenshot",
                "reasoning": "Browser form filling is required.",
                "url": "https://form.test",
                "form_fields": {"name": "Oney"},
                "submit": False,
            },
        }
    )

    assert classification.task_type == TaskType.OTHER
    assert classification.intent is not None
    assert classification.intent.route == IntentRoute.BROWSER_CONTROL


def test_message_classification_accepts_file_safety_flags() -> None:
    classification = MessageClassification.model_validate(
        {
            "is_task": True,
            "task_type": "other",
            "normalized_objective": "Clean up a folder without deleting or overwriting files.",
            "confidence": 0.86,
            "reason": "The user asked for safe folder cleanup.",
            "intent": {
                "route": "filesystem.manage",
                "operation": "organize",
                "objective": "Organize safe-to-move files only.",
                "reasoning": "Folder cleanup should use scoped filesystem operations.",
                "folder_path": "C:/tmp/docs",
                "allow_deletion": False,
                "allow_overwrite": False,
            },
        }
    )

    assert classification.intent is not None
    assert classification.intent.allow_deletion is False
    assert classification.intent.allow_overwrite is False
