from __future__ import annotations

from agent_control.llm.classifier import heuristic_classification
from agent_control.schemas import ChannelType, InboundMessage, MessageKind, TaskType


def test_heuristic_classifier_routes_desktop_screenshot_to_desktop_observation() -> None:
    classification = heuristic_classification(
        InboundMessage(
            channel=ChannelType.TELEGRAM,
            kind=MessageKind.TEXT,
            sender_id="42",
            chat_id="100",
            text="Take a screenshot of my desktop and send it to me now",
        )
    )

    assert classification.is_task is True
    assert classification.task_type == TaskType.DESKTOP_OBSERVATION
    assert classification.normalized_objective == "Take a screenshot of my desktop and send it to me now"
