from __future__ import annotations

import pytest
from pydantic import BaseModel

from agent_control.testing.scripted_llm import (
    RecordingLLMProvider,
    ScriptedLLMError,
    ScriptedLLMProvider,
    fixture_key,
)


class _Answer(BaseModel):
    text: str
    confidence: float


def _write_fixture(path, entries: dict) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


@pytest.mark.asyncio
async def test_generate_text_replays_recorded_response(tmp_path) -> None:
    fixture = tmp_path / "fixture.json"
    key = fixture_key("generate_text", "sys", "hello")
    _write_fixture(fixture, {key: {"method": "generate_text", "response": "hi there"}})

    provider = ScriptedLLMProvider(fixture)
    result = await provider.generate_text("sys", "hello")

    assert result == "hi there"


@pytest.mark.asyncio
async def test_generate_structured_replays_and_validates(tmp_path) -> None:
    fixture = tmp_path / "fixture.json"
    key = fixture_key("generate_structured", "sys", "user")
    _write_fixture(fixture, {
        key: {"method": "generate_structured", "response": {"text": "answer", "confidence": 0.9}},
    })

    provider = ScriptedLLMProvider(fixture)
    result = await provider.generate_structured("sys", "user", _Answer)

    assert isinstance(result, _Answer)
    assert result.text == "answer"
    assert result.confidence == 0.9


@pytest.mark.asyncio
async def test_unrecorded_prompt_raises_loudly(tmp_path) -> None:
    fixture = tmp_path / "fixture.json"
    _write_fixture(fixture, {})

    provider = ScriptedLLMProvider(fixture)

    with pytest.raises(ScriptedLLMError, match="No recorded"):
        await provider.generate_text("sys", "unrecorded prompt")


@pytest.mark.asyncio
async def test_missing_fixture_file_raises_on_any_call(tmp_path) -> None:
    provider = ScriptedLLMProvider(tmp_path / "does_not_exist.json")

    with pytest.raises(ScriptedLLMError):
        await provider.generate_text("sys", "anything")


def test_fixture_key_normalizes_embedded_random_ids() -> None:
    # schemas.new_id() mints "<prefix>_<uuid4().hex>" and some tools (e.g.
    # code.interpreter's per-task workspace dir) embed that id straight into
    # their prompt text - a fresh id every task creation. Prompts that are
    # otherwise identical must key the same regardless of which id they embed,
    # or no fixture could ever replay twice (this was a real bug, caught by
    # running the same scenario test setup twice with different random ids).
    prompt_a = "Workspace: C:\\scratch\\task_7f19962bf3054ddcb306a5c3cec28e44_b7de3ab1"
    prompt_b = "Workspace: C:\\scratch\\task_0011223344556677889900aabbccdd_12ab34cd"

    assert fixture_key("generate_text", "sys", prompt_a) == fixture_key("generate_text", "sys", prompt_b)


def test_fixture_key_still_distinguishes_genuinely_different_prompts() -> None:
    assert fixture_key("generate_text", "sys", "search for a resume") != fixture_key(
        "generate_text", "sys", "search for an invoice"
    )


@pytest.mark.asyncio
async def test_prompt_change_is_a_cache_miss_not_a_stale_hit(tmp_path) -> None:
    fixture = tmp_path / "fixture.json"
    key = fixture_key("generate_text", "sys", "original prompt")
    _write_fixture(fixture, {key: {"method": "generate_text", "response": "stale answer"}})

    provider = ScriptedLLMProvider(fixture)

    with pytest.raises(ScriptedLLMError):
        await provider.generate_text("sys", "changed prompt")


@pytest.mark.asyncio
async def test_calls_are_tracked_for_assertions(tmp_path) -> None:
    fixture = tmp_path / "fixture.json"
    key = fixture_key("generate_text", "sys", "hello")
    _write_fixture(fixture, {key: {"method": "generate_text", "response": "hi"}})

    provider = ScriptedLLMProvider(fixture)
    await provider.generate_text("sys", "hello")

    assert len(provider.calls) == 1
    assert provider.calls[0]["method"] == "generate_text"


class _FakeLiveProvider:
    def __init__(self) -> None:
        self.generate_text_calls = 0

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.generate_text_calls += 1
        return f"live response to: {user_prompt}"

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        return "live multimodal response"

    async def generate_structured(self, system_prompt, user_prompt, output_model, *, temperature=None):
        return output_model.model_validate({"text": "live structured", "confidence": 1.0})


@pytest.mark.asyncio
async def test_recording_provider_persists_to_fixture_file(tmp_path) -> None:
    fixture = tmp_path / "recorded.json"
    live = _FakeLiveProvider()
    recorder = RecordingLLMProvider(live, fixture)

    result = await recorder.generate_text("sys", "record me")

    assert result == "live response to: record me"
    assert fixture.exists()
    replay = ScriptedLLMProvider(fixture)
    replayed = await replay.generate_text("sys", "record me")
    assert replayed == result


@pytest.mark.asyncio
async def test_recording_provider_persists_structured_response(tmp_path) -> None:
    fixture = tmp_path / "recorded.json"
    live = _FakeLiveProvider()
    recorder = RecordingLLMProvider(live, fixture)

    result = await recorder.generate_structured("sys", "user", _Answer)

    assert result.text == "live structured"
    replay = ScriptedLLMProvider(fixture)
    replayed = await replay.generate_structured("sys", "user", _Answer)
    assert replayed.text == "live structured"
    assert replayed.confidence == 1.0


@pytest.mark.asyncio
async def test_recording_provider_appends_without_clobbering_existing_entries(tmp_path) -> None:
    fixture = tmp_path / "recorded.json"
    live = _FakeLiveProvider()
    recorder = RecordingLLMProvider(live, fixture)
    await recorder.generate_text("sys", "first")
    await recorder.generate_text("sys", "second")

    replay = ScriptedLLMProvider(fixture)
    assert await replay.generate_text("sys", "first") == "live response to: first"
    assert await replay.generate_text("sys", "second") == "live response to: second"
