from __future__ import annotations

import json
import base64
import mimetypes
from pathlib import Path
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from agent_control.config import AppSettings, LLMProfileConfig
from agent_control.config_sync import read_env_value
from agent_control.schemas import PlanModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        ...

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        ...

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T]) -> T:
        ...


class OpenAICompatibleProvider:
    def __init__(self, profile: LLMProfileConfig) -> None:
        if not profile.base_url:
            raise ValueError("base_url is required for OpenAI-compatible LLM provider")
        self.profile = profile

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        data = await self._chat(system_prompt, user_prompt, response_format=None)
        return str(data["choices"][0]["message"]["content"])

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        content: list[dict] = [{"type": "text", "text": user_prompt}]
        for image_path in image_paths:
            content.append({"type": "image_url", "image_url": {"url": _data_url(image_path)}})
        data = await self._chat(system_prompt, content, response_format=None)
        return str(data["choices"][0]["message"]["content"])

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T]) -> T:
        schema = output_model.model_json_schema()
        data = await self._chat(
            system_prompt,
            user_prompt,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": output_model.__name__, "schema": schema},
            },
        )
        content = data["choices"][0]["message"]["content"]
        try:
            payload = json.loads(content)
            return output_model.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"LLM structured output failed validation: {exc}") from exc

    async def _chat(
        self,
        system_prompt: str,
        user_prompt: str | list[dict],
        response_format: dict | None,
    ) -> dict:
        api_key = self._api_key()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        payload = {
            "model": self.profile.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.profile.temperature,
            "max_tokens": self.profile.max_tokens,
        }
        if self.profile.context_limit is not None:
            payload["context_limit"] = self.profile.context_limit
        if response_format:
            payload["response_format"] = response_format

        async with httpx.AsyncClient(timeout=self.profile.timeout_seconds) as client:
            response = await client.post(
                f"{self.profile.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return dict(response.json())

    def _api_key(self) -> str | None:
        if self.profile.api_key:
            return self.profile.api_key.get_secret_value()
        if self.profile.api_key_env:
            return read_env_value(self.profile.api_key_env)
        return None


def build_default_llm_provider(settings: AppSettings) -> LLMProvider | None:
    profile = settings.llm.profiles.get(settings.llm.default_profile)
    if profile is None:
        return None
    if profile.provider != "openai_compatible":
        raise ValueError(f"unsupported LLM provider: {profile.provider}")
    return OpenAICompatibleProvider(profile)


class StaticPlanProvider:
    def __init__(self, plan: PlanModel) -> None:
        self.plan = plan
        self.prompts: list[tuple[str, str]] = []

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return self.plan.model_dump_json()

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return self.plan.model_dump_json()

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T]) -> T:
        self.prompts.append((system_prompt, user_prompt))
        return output_model.model_validate(self.plan.model_dump(mode="json"))


def _data_url(path: str) -> str:
    file_path = Path(path)
    mime_type = mimetypes.guess_type(file_path.name)[0] or "image/png"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
