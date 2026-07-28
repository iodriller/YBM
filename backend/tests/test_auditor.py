from __future__ import annotations

import pytest

from agent_control.orchestration.auditor import AuditorService, CONTENT_TOOLS


class QueueProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[tuple[str, str]] = []

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return self.responses.pop(0)

    async def generate_multimodal_text(self, system_prompt, user_prompt, image_paths) -> str:
        raise NotImplementedError

    async def generate_structured(self, system_prompt, user_prompt, output_model, **_ignored_kwargs):
        raise NotImplementedError


class RaisingProvider:
    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("provider down")

    async def generate_multimodal_text(self, system_prompt, user_prompt, image_paths) -> str:
        raise NotImplementedError

    async def generate_structured(self, system_prompt, user_prompt, output_model, **_ignored_kwargs):
        raise NotImplementedError


def test_is_content_tool_matches_the_old_synthesizer_gate() -> None:
    assert AuditorService.is_content_tool("browser.open")
    assert AuditorService.is_content_tool("filesystem.manage")
    assert not AuditorService.is_content_tool("task.status")
    assert not AuditorService.is_content_tool("schedule.manage")
    assert not AuditorService.is_content_tool(None)


@pytest.mark.asyncio
async def test_audit_returns_grounded_answer_when_sufficient() -> None:
    provider = QueueProvider(["The invoice total is $250.00."])
    auditor = AuditorService(provider)

    result = await auditor.audit("what is the invoice total?", "Invoice #4471 - $250.00")

    assert result.sufficient is True
    assert result.answer == "The invoice total is $250.00."
    assert "what is the invoice total?" in provider.prompts[0][1]


@pytest.mark.asyncio
async def test_audit_returns_gap_reason_when_insufficient() -> None:
    provider = QueueProvider(["INSUFFICIENT: only 2 of 5 requested episodes present in raw output"])
    auditor = AuditorService(provider)

    result = await auditor.audit("list the first 5 episodes", "1. Pilot\n2. Second episode")

    assert result.sufficient is False
    assert result.answer is None
    assert "2 of 5" in (result.reason or "")


@pytest.mark.asyncio
async def test_audit_empty_raw_output_is_insufficient_without_calling_provider() -> None:
    provider = QueueProvider([])
    auditor = AuditorService(provider)

    result = await auditor.audit("what happened?", "   ")

    assert result.sufficient is False
    assert provider.prompts == []


@pytest.mark.asyncio
async def test_audit_fails_open_on_provider_error() -> None:
    auditor = AuditorService(RaisingProvider())

    result = await auditor.audit("what happened?", "some raw content")

    assert result.sufficient is True
    assert result.answer is None
    assert result.reason == "auditor_error"


@pytest.mark.asyncio
async def test_audit_passes_original_message_for_language_and_terminology() -> None:
    provider = QueueProvider(["ok"])
    auditor = AuditorService(provider)

    await auditor.audit("summarize the page", "raw html", original_message="Résume la page en français")

    assert "Résume la page en français" in provider.prompts[0][1]
