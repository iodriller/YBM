from __future__ import annotations

import base64

import pytest

from agent_control.config import LLMProfileConfig
from agent_control.llm.providers import OpenAICompatibleProvider


@pytest.mark.asyncio
async def test_openai_compatible_provider_sends_multimodal_image_payload(monkeypatch, tmp_path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"fake-image")
    profile = LLMProfileConfig(
        model="local-vision",
        base_url="http://127.0.0.1:8000/v1",
        api_key=None,
        max_tokens=128,
    )
    provider = OpenAICompatibleProvider(profile)
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "observed"}}]}

    class FakeAsyncClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, headers: dict, json: dict):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("agent_control.llm.providers.httpx.AsyncClient", FakeAsyncClient)

    result = await provider.generate_multimodal_text("system", "describe", [str(image)])

    payload = captured["payload"]
    user_content = payload["messages"][1]["content"]

    assert result == "observed"
    assert captured["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert user_content[0] == {"type": "text", "text": "describe"}
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"fake-image").decode("ascii")


@pytest.mark.asyncio
async def test_openai_compatible_provider_allows_localdeploy_clamping(monkeypatch) -> None:
    profile = LLMProfileConfig(
        model="gemma3_4b_ollama_safe",
        base_url="http://127.0.0.1:8000/v1",
        max_tokens=4096,
    )
    provider = OpenAICompatibleProvider(profile)
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeAsyncClient:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, *, headers: dict, json: dict):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("agent_control.llm.providers.httpx.AsyncClient", FakeAsyncClient)

    result = await provider.generate_text("system", "user")

    assert result == "ok"
    assert captured["payload"]["max_tokens"] == 4096
    assert captured["payload"]["allow_clamp"] is True
