from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from agent_control.schemas import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    Capability,
    ChannelType,
    InboundMessage,
    MessageKind,
    OrchestrationIntent,
    PlanModel,
    PlanPostcondition,
    PlanStep,
    PostconditionType,
    RiskLevel,
    ToolCallRequest,
    utc_now,
)


def test_structured_plan_requires_steps() -> None:
    with pytest.raises(ValidationError):
        PlanModel(objective="Build app", steps=[])


def test_structured_plan_validates() -> None:
    plan = PlanModel(
        objective="Build app",
        required_capabilities=[Capability.VSCODE_READ_STATE],
        steps=[
            PlanStep(
                title="Inspect workspace",
                description="Read VS Code workspace state.",
                required_capabilities=[Capability.VSCODE_READ_STATE],
            )
        ],
        success_criteria=["Workspace state is summarized."],
    )

    assert plan.steps[0].required_capabilities == [Capability.VSCODE_READ_STATE]


def test_structured_plan_accepts_typed_postconditions() -> None:
    plan = PlanModel(
        objective="Launch app",
        steps=[PlanStep(title="Launch", description="Launch app.")],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.PREVIEW_URL,
                description="A local preview URL is reported.",
            )
        ],
    )

    assert plan.postconditions[0].type == PostconditionType.PREVIEW_URL


def test_inbound_message_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        InboundMessage(
            channel=ChannelType.TELEGRAM,
            kind=MessageKind.TEXT,
            sender_id="123",
            chat_id="456",
            text="hello",
            unexpected=True,
        )


def test_orchestration_intent_ignores_harmless_llm_extra_fields() -> None:
    intent = OrchestrationIntent.model_validate(
        {
            "route": "desktop.observe",
            "operation": "screenshot",
            "objective": "Capture VS Code and file directory.",
            "reasoning": "The user asked for a screenshot.",
            "screenshot": True,
        }
    )

    assert intent.route.value == "desktop.observe"
    assert not hasattr(intent, "screenshot")


def test_tool_call_requires_valid_capability() -> None:
    request = ToolCallRequest(
        task_id="task_1",
        tool_name="vscode",
        capability=Capability.VSCODE_READ_STATE,
        risk_level=RiskLevel.LOW,
    )

    assert request.capability == Capability.VSCODE_READ_STATE


def test_pending_approval_decision_is_invalid() -> None:
    approval = ApprovalRequest(
        task_id="task_1",
        capability=Capability.TERMINAL_RUN,
        risk_level=RiskLevel.HIGH,
        summary="Run command",
        expires_at=utc_now() + timedelta(minutes=5),
    )

    with pytest.raises(ValidationError):
        ApprovalDecision(
            approval_request_id=approval.id,
            status=ApprovalStatus.PENDING,
            actor="user",
        )
