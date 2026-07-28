from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_control.orchestration.operator import OperatorLoopService, _format_history
from agent_control.schemas import OperatorAction, OperatorDecision


class QueueDecisionProvider:
    def __init__(self, decisions: list[OperatorDecision]) -> None:
        self.decisions = decisions
        self.prompts: list[tuple[str, str]] = []

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model, **_ignored_kwargs):
        self.prompts.append((system_prompt, user_prompt))
        return output_model.model_validate(self.decisions.pop(0).model_dump(mode="json"))

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        raise NotImplementedError


class RaisingProvider:
    """Always raises ValidationError - proves decide() surfaces failure after
    exhausting retries rather than hanging or returning something invalid."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model, **_ignored_kwargs):
        self.calls += 1
        raise ValidationError.from_exception_data("OperatorDecision", [])

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_decide_returns_call_tool_on_first_step() -> None:
    provider = QueueDecisionProvider([
        OperatorDecision(
            action=OperatorAction.CALL_TOOL,
            tool_name="filesystem.manage",
            tool_input={"operation": "search", "root": "desktop", "query": "resume"},
        ),
    ])
    operator = OperatorLoopService(provider)

    decision = await operator.decide("find my resume", "tool catalog text", history=[])

    assert decision.action == OperatorAction.CALL_TOOL
    assert decision.tool_name == "filesystem.manage"
    assert "(none yet" in provider.prompts[0][1]


@pytest.mark.asyncio
async def test_decide_includes_history_in_prompt() -> None:
    provider = QueueDecisionProvider([
        OperatorDecision(action=OperatorAction.DONE, final_answer="resume.txt"),
    ])
    operator = OperatorLoopService(provider)
    history = [
        {"tool_name": "filesystem.manage", "status": "succeeded", "input": {"operation": "search"}, "output_summary": "found resume.txt"},
    ]

    await operator.decide("find my resume", "tool catalog text", history=history)

    assert "filesystem.manage" in provider.prompts[0][1]
    assert "found resume.txt" in provider.prompts[0][1]


@pytest.mark.asyncio
async def test_decide_retries_on_validation_error_then_succeeds() -> None:
    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_structured(self, system_prompt, user_prompt, output_model, **_ignored_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ValidationError.from_exception_data("OperatorDecision", [])
            return output_model.model_validate(
                OperatorDecision(action=OperatorAction.DONE, final_answer="ok").model_dump(mode="json")
            )

        async def generate_text(self, system_prompt, user_prompt) -> str:
            raise NotImplementedError

        async def generate_multimodal_text(self, system_prompt, user_prompt, image_paths) -> str:
            raise NotImplementedError

    provider = FlakyProvider()
    operator = OperatorLoopService(provider)

    decision = await operator.decide("objective", "context", history=[])

    assert decision.action == OperatorAction.DONE
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_decide_raises_after_exhausting_retries() -> None:
    provider = RaisingProvider()
    operator = OperatorLoopService(provider)

    with pytest.raises(ValidationError):
        await operator.decide("objective", "context", history=[])

    assert provider.calls == 3


@pytest.mark.asyncio
async def test_decide_escalates_to_major_provider_after_observed_failure() -> None:
    class OnceFailingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_structured(self, system_prompt, user_prompt, output_model, **_ignored_kwargs):
            self.calls += 1
            raise ValidationError.from_exception_data("OperatorDecision", [])

        async def generate_text(self, system_prompt, user_prompt) -> str:
            raise NotImplementedError

        async def generate_multimodal_text(self, system_prompt, user_prompt, image_paths) -> str:
            raise NotImplementedError

    provider = OnceFailingProvider()
    major_provider = QueueDecisionProvider([OperatorDecision(action=OperatorAction.DONE, final_answer="ok")])
    operator = OperatorLoopService(provider, major_provider=major_provider)

    decision = await operator.decide("objective", "context", history=[])

    assert decision.action == OperatorAction.DONE
    assert provider.calls == 1
    assert len(major_provider.prompts) == 1


@pytest.mark.asyncio
async def test_decide_does_not_escalate_when_default_provider_succeeds() -> None:
    provider = QueueDecisionProvider([OperatorDecision(action=OperatorAction.DONE, final_answer="ok")])
    major_provider = QueueDecisionProvider([])
    operator = OperatorLoopService(provider, major_provider=major_provider)

    decision = await operator.decide("objective", "context", history=[])

    assert decision.action == OperatorAction.DONE
    assert major_provider.prompts == []


def test_format_history_empty() -> None:
    assert "none yet" in _format_history([])


def test_format_history_shows_error() -> None:
    history = [{"tool_name": "code.interpreter", "status": "failed", "error": "timeout after 60s"}]

    formatted = _format_history(history)

    assert "code.interpreter" in formatted
    assert "timeout after 60s" in formatted


def test_format_history_truncates_to_recent_entries() -> None:
    history = [{"tool_name": f"tool_{i}", "status": "succeeded"} for i in range(20)]

    formatted = _format_history(history)

    assert "earlier step(s) omitted" in formatted
    assert "tool_0" not in formatted
    assert "tool_19" in formatted
