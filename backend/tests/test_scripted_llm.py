from __future__ import annotations

import tempfile
from pathlib import Path

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


class _CodeAnswer(BaseModel):
    code: str
    summary: str


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


def test_fixture_key_normalizes_embedded_tempdir_names() -> None:
    # tempfile.TemporaryDirectory()/pytest's tmp_path fixture name dirs "tmp"
    # + 8 chars from [a-z0-9] - not restricted to hex, so _HEX_RUN alone
    # misses names like "tmpor3q4148". A relative plan-step path that
    # resolves against that randomized CWD leaks the name into the
    # policy-denial error text, which then feeds the next replan prompt -
    # different every run, so no replan fixture could ever replay twice
    # (real failure hit recording output_delivery.json).
    prompt_a = "path is outside allowed roots: C:\\Users\\x\\AppData\\Local\\Temp\\tmpor3q4148\\out.txt"
    prompt_b = "path is outside allowed roots: C:\\Users\\x\\AppData\\Local\\Temp\\tmpzz9k2b7c\\out.txt"

    assert fixture_key("generate_structured", "sys", prompt_a) == fixture_key(
        "generate_structured", "sys", prompt_b
    )


def test_fixture_key_normalizes_cross_platform_line_endings() -> None:
    windows = "Stdout:\r\n6765\r\n\r\nAvailable tools:"
    linux = "Stdout:\n6765\n\nAvailable tools:"

    assert fixture_key("generate_structured", "sys", windows) == fixture_key(
        "generate_structured", "sys", linux
    )


def test_fixture_key_normalizes_scenario_scratch_root_across_platforms() -> None:
    windows = (
        r"search C:\Users\recording-user\AppData\Local\Temp"
        r"\ybm_scenario_scratch\file_search"
    )
    linux = "search /tmp/ybm_scenario_scratch/file_search"

    assert fixture_key("generate_text", "sys", windows) == fixture_key(
        "generate_text", "sys", linux
    )


def test_fixture_key_normalizes_pytest_tmp_path_across_runs_and_platforms() -> None:
    """pytest's tmp_path carries a run counter that increments every single
    run, so a prompt containing one keyed differently each time and could never
    replay - not on CI, and not on the recording machine a minute later.

    The scenario tests that pass an out-of-roots directory all do this, and all
    were failing on a missing fixture while still satisfying their
    `status != COMPLETED` assertion, so none of them ever exercised the policy
    refusal they exist to prove.
    """
    first_run = (
        r"roots: C:\Users\recording-user\AppData\Local\Temp\pytest-of-recording-user"
        r"\pytest-2292\test_filesystem_search_rejects0\somewhere_else"
    )
    later_run = (
        r"roots: C:\Users\recording-user\AppData\Local\Temp\pytest-of-recording-user"
        r"\pytest-9999\test_filesystem_search_rejects0\somewhere_else"
    )
    on_linux = (
        "roots: /tmp/pytest-of-runner/pytest-7"
        "/test_filesystem_search_rejects0/somewhere_else"
    )

    key = fixture_key("generate_structured", "sys", first_run)
    assert key == fixture_key("generate_structured", "sys", later_run)
    assert key == fixture_key("generate_structured", "sys", on_linux)


def test_fixture_key_still_separates_different_pytest_test_directories() -> None:
    """Only the volatile prefix is collapsed; the per-test directory name is
    stable and must keep two different tests keyed apart."""
    one = r"C:\Temp\pytest-of-u\pytest-1\test_alpha0\somewhere_else"
    two = r"C:\Temp\pytest-of-u\pytest-1\test_beta0\somewhere_else"

    assert fixture_key("generate_structured", "sys", one) != fixture_key(
        "generate_structured", "sys", two
    )


def test_fixture_key_normalizes_macos_scenario_scratch_aliases() -> None:
    windows = (
        r"search C:\Users\recording-user\AppData\Local\Temp"
        r"\ybm_scenario_scratch\file_search"
    )
    macos = "search /var/folders/ab/random-hash/T/ybm_scenario_scratch/file_search"
    resolved_macos = (
        "search /private/var/folders/ab/random-hash/T/"
        "ybm_scenario_scratch/file_search"
    )

    expected = fixture_key("generate_text", "sys", windows)
    assert fixture_key("generate_text", "sys", macos) == expected
    assert fixture_key("generate_text", "sys", resolved_macos) == expected


def test_fixture_key_normalizes_escaped_scenario_paths_in_history() -> None:
    recorded = (
        r"input: {'root': 'C:\\Users\\recording-user\\AppData\\Local\\Temp"
        r"\\ybm_scenario_scratch\\file_search'}"
    )
    linux = "input: {'root': '/tmp/ybm_scenario_scratch/file_search'}"

    assert fixture_key("generate_structured", "sys", recorded) == fixture_key(
        "generate_structured", "sys", linux
    )


@pytest.mark.asyncio
async def test_replay_rebases_recorded_scenario_response_path(tmp_path) -> None:
    fixture = tmp_path / "fixture.json"
    recorded_root = (
        r"C:\Users\recording-user\AppData\Local\Temp"
        r"\ybm_scenario_scratch\file_search"
    )
    current_root = Path(tempfile.gettempdir()) / "ybm_scenario_scratch" / "file_search"
    _write_fixture(
        fixture,
        {
            "legacy-key": {
                "method": "generate_text",
                "system_prompt": "sys",
                "user_prompt": f"search {recorded_root}",
                "response": f"read {recorded_root}\\resume.txt",
            }
        },
    )

    provider = ScriptedLLMProvider(fixture)
    result = await provider.generate_text("sys", f"search {current_root}")

    assert result == f"read {current_root / 'resume.txt'}"


@pytest.mark.asyncio
async def test_replay_does_not_rewrite_recorded_source_code(tmp_path) -> None:
    fixture = tmp_path / "fixture.json"
    recorded_root = (
        r"C:\Users\recording-user\AppData\Local\Temp"
        r"\ybm_scenario_scratch\code_interpreter"
    )
    current_root = Path(tempfile.gettempdir()) / "ybm_scenario_scratch" / "code_interpreter"
    recorded_code = f"print({recorded_root!r})"
    _write_fixture(
        fixture,
        {
            "legacy-key": {
                "method": "generate_structured",
                "system_prompt": "sys",
                "user_prompt": f"write under {recorded_root}",
                "response": {
                    "code": recorded_code,
                    "summary": f"write under {recorded_root}",
                },
            }
        },
    )

    provider = ScriptedLLMProvider(fixture)
    result = await provider.generate_structured(
        "sys", f"write under {current_root}", _CodeAnswer
    )

    assert result.code == recorded_code
    assert result.summary == f"write under {current_root}"


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
