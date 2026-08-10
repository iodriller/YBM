"""Multi-provider LLM support.

The Anthropic tests exist because a base_url swap would have failed every
request: current Claude models reject `temperature` with a 400, and every YBM
profile carries one.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from agent_control.config import LLMProfileConfig
from agent_control.llm import catalog
from agent_control.llm.anthropic_provider import AnthropicProvider, _safe_error, rejects_temperature
from agent_control.llm.providers import (
    OpenAICompatibleProvider,
    _is_unavailability,
    build_provider_for_profile,
)


def _profile(**kwargs) -> LLMProfileConfig:
    base = {"provider": "anthropic", "model": "claude-sonnet-5", "api_key": "sk-test"}
    base.update(kwargs)
    return LLMProfileConfig(**base)


class _Shape(BaseModel):
    answer: str


class _Status:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _fake_anthropic(monkeypatch, message, captured: dict) -> None:
    """Stand in for anthropic.AsyncAnthropic, recording the request kwargs."""

    class _Client:
        def __init__(self, **_kwargs):
            async def _create(**kw):
                captured.update(kw)
                return message

            self.messages = type("_Messages", (), {"create": staticmethod(_create)})()

        async def close(self):
            return None

    import anthropic

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Client)


def _block(**attrs):
    return type("_Block", (), attrs)()


# -- the catalog -------------------------------------------------------


def test_every_catalog_provider_has_a_usable_shape() -> None:
    assert catalog.PROVIDERS, "catalog must not be empty"
    for spec in catalog.PROVIDERS:
        assert spec.kind in {"openai_compatible", "anthropic"}, spec.key
        # A remote provider the user configures needs somewhere to get a key,
        # or the UI has nothing to link to.
        if spec.needs_key and spec.key != "custom":
            assert spec.keys_url, f"{spec.key} has no keys_url"
        # Local runtimes must not demand a key.
        if spec.local:
            assert spec.api_key_env is None, f"{spec.key} is local but wants a key"
        # Anything not user-supplied must carry its own base URL.
        if spec.kind == "openai_compatible" and spec.key != "custom":
            assert spec.base_url, f"{spec.key} has no base_url"


def test_catalog_keys_are_unique() -> None:
    keys = [spec.key for spec in catalog.PROVIDERS]
    assert len(keys) == len(set(keys))


# -- routing -----------------------------------------------------------


def test_provider_field_routes_to_the_right_implementation() -> None:
    assert isinstance(build_provider_for_profile(_profile()), AnthropicProvider)
    openai_like = _profile(provider="openai_compatible", base_url="https://api.example/v1")
    assert isinstance(build_provider_for_profile(openai_like), OpenAICompatibleProvider)


def test_unknown_provider_is_rejected_by_name() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        build_provider_for_profile(_profile(provider="not_a_provider"))


# -- the temperature trap ---------------------------------------------


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5", "claude-sonnet-5", "claude-opus-4-8", "claude-opus-4-7", "claude-fable-5"],
)
def test_current_claude_models_reject_temperature(model: str) -> None:
    """Sending temperature to these is a 400, so the provider must omit it."""
    assert rejects_temperature(model) is True


def test_older_claude_models_still_take_temperature() -> None:
    assert rejects_temperature("claude-3-haiku-20240307") is False


@pytest.mark.anyio
async def test_anthropic_call_omits_temperature_on_a_current_model(monkeypatch) -> None:
    captured: dict = {}
    message = type(
        "_M",
        (),
        {
            "content": [_block(type="text", text="hi")],
            "stop_reason": "end_turn",
            "usage": _block(input_tokens=3, output_tokens=4),
        },
    )()
    _fake_anthropic(monkeypatch, message, captured)

    provider = AnthropicProvider(_profile(model="claude-sonnet-5", temperature=0.7))
    assert await provider.generate_text("sys", "user") == "hi"

    assert "temperature" not in captured, "temperature would 400 on this model"
    # The system prompt is a top-level argument on Anthropic, not a message.
    assert captured["system"] == "sys"
    assert [m["role"] for m in captured["messages"]] == ["user"]
    assert provider.last_usage == {
        "prompt_tokens": 3,
        "completion_tokens": 4,
        "total_tokens": 7,
        "model": "claude-sonnet-5",
    }


@pytest.mark.anyio
async def test_anthropic_call_keeps_temperature_on_a_model_that_accepts_it(monkeypatch) -> None:
    captured: dict = {}
    message = type(
        "_M",
        (),
        {"content": [_block(type="text", text="ok")], "stop_reason": "end_turn", "usage": None},
    )()
    _fake_anthropic(monkeypatch, message, captured)

    provider = AnthropicProvider(_profile(model="claude-3-haiku-20240307", temperature=0.4))
    await provider.generate_text("sys", "user")
    assert captured["temperature"] == 0.4


@pytest.mark.anyio
async def test_anthropic_refusal_is_reported_not_indexed(monkeypatch) -> None:
    """A refusal is HTTP 200 with no text block, so reading content[0] blind
    would raise something the UI cannot explain."""
    message = type("_M", (), {"content": [], "stop_reason": "refusal", "usage": None})()
    _fake_anthropic(monkeypatch, message, {})

    provider = AnthropicProvider(_profile())
    with pytest.raises(ValueError, match="refusal"):
        await provider.generate_text("sys", "user")


@pytest.mark.anyio
async def test_anthropic_structured_output_uses_a_forced_tool(monkeypatch) -> None:
    captured: dict = {}
    message = type(
        "_M",
        (),
        {
            "content": [_block(type="tool_use", input={"answer": "42"})],
            "stop_reason": "tool_use",
            "usage": None,
        },
    )()
    _fake_anthropic(monkeypatch, message, captured)

    provider = AnthropicProvider(_profile())
    result = await provider.generate_structured("sys", "user", _Shape)

    assert result.answer == "42"
    # Forcing the tool is Anthropic's equivalent of a json_schema response
    # format: emitting the shape becomes the only option.
    assert captured["tool_choice"]["type"] == "tool"
    assert captured["tools"][0]["name"] == captured["tool_choice"]["name"]
    assert captured["tools"][0]["input_schema"]["properties"]["answer"]["type"] == "string"


@pytest.mark.anyio
async def test_anthropic_missing_key_fails_before_any_request(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider(
        LLMProfileConfig(provider="anthropic", model="claude-sonnet-5")
    )
    with pytest.raises(ValueError, match="not configured"):
        await provider.generate_text("sys", "user")


def test_anthropic_5xx_wording_matches_what_failover_looks_for() -> None:
    """FailoverLLMProvider decides an endpoint is down by matching
    'failed with HTTP 5'. If this wording drifts, an Anthropic outage silently
    stops failing over to the local model."""
    assert _is_unavailability(ValueError(_safe_error(_Status(503)))) is True


def test_anthropic_client_errors_do_not_trigger_failover() -> None:
    """A bad key would fail identically against the fallback."""
    assert _is_unavailability(ValueError(_safe_error(_Status(401)))) is False


def test_anthropic_errors_never_echo_the_key() -> None:
    message = _safe_error(_Status(401))
    assert "sk-" not in message
    assert "rejected" in message
