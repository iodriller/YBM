from __future__ import annotations

import json
import base64
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from agent_control.config import AppSettings, LLMProfileConfig
from agent_control.config_sync import read_env_value
from agent_control.prompts import render_prompt
from agent_control.schemas import PlanModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        ...

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        ...

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        *,
        temperature: float | None = None,
    ) -> T:
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

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        *,
        temperature: float | None = None,
    ) -> T:
        schema = output_model.model_json_schema()
        try:
            data = await self._chat(
                system_prompt,
                user_prompt,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": output_model.__name__, "schema": schema},
                },
                temperature=temperature,
            )
        except ValueError as exc:
            if not _should_retry_structured_without_response_format(exc):
                raise
            fallback_prompt = render_prompt(
                "tasks/structured_json_fallback.md",
                user_prompt=user_prompt,
                schema=json.dumps(schema, ensure_ascii=True),
            )
            data = await self._chat(
                f"{system_prompt}\nReturn JSON only.",
                fallback_prompt,
                response_format=None,
                temperature=temperature,
            )
        content = data["choices"][0]["message"]["content"]
        try:
            payload = _loads_json_object(str(content))
            return output_model.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"LLM structured output failed validation: {exc}") from exc

    async def _chat(
        self,
        system_prompt: str,
        user_prompt: str | list[dict],
        response_format: dict | None,
        *,
        temperature: float | None = None,
    ) -> dict:
        api_key = self._api_key()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        payload = {
            "model": self.profile.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature if temperature is not None else self.profile.temperature,
            "max_tokens": self.profile.max_tokens,
        }
        if self.profile.context_limit is not None:
            payload["context_limit"] = self.profile.context_limit
        if _looks_like_localdeploy(self.profile):
            payload["allow_clamp"] = True
        if response_format:
            payload["response_format"] = response_format

        async with httpx.AsyncClient(timeout=self.profile.timeout_seconds) as client:
            url = f"{self.profile.base_url.rstrip('/')}/chat/completions"
            response = await client.post(url, headers=headers, json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ValueError(_http_error_detail(exc.response)) from exc
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


def build_major_llm_provider(settings: AppSettings) -> LLMProvider | None:
    """Build the LLM provider for complex/major tasks, if a major_profile is configured."""
    if not settings.llm.major_profile:
        return None
    profile = settings.llm.profiles.get(settings.llm.major_profile)
    if profile is None:
        return None
    if profile.provider != "openai_compatible":
        raise ValueError(f"unsupported major LLM provider: {profile.provider}")
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

    async def generate_structured(
        self, system_prompt: str, user_prompt: str, output_model: type[T],
        *, temperature: float | None = None,
    ) -> T:
        self.prompts.append((system_prompt, user_prompt))
        return output_model.model_validate(self.plan.model_dump(mode="json"))


def _data_url(path: str) -> str:
    file_path = Path(path)
    mime_type = mimetypes.guess_type(file_path.name)[0] or "image/png"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _looks_like_localdeploy(profile: LLMProfileConfig) -> bool:
    base_url = profile.base_url or ""
    model = profile.model.lower()
    try:
        parsed = urlparse(base_url)
    except Exception:
        return False
    hostname = (parsed.hostname or "").lower()
    return (
        hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.port == 8000
        and (model.startswith("gemma3_") or model.startswith("qwen3vl_") or model.startswith("qwen25vl_") or "localdeploy" in model or "ollama_safe" in model)
    )


def _http_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
    else:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            text = str(error.get("message") or payload)
        else:
            text = str(payload)
    return f"LLM request failed with HTTP {response.status_code} at {response.url}: {text}"


def _should_retry_structured_without_response_format(exc: ValueError) -> bool:
    message = str(exc)
    return "HTTP 400" in message or "response_format" in message or "json_schema" in message


def _loads_json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Tier 1: strict json
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Tier 2: extract outermost {...} and try again
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                payload = _repair_json(text[start : end + 1])
        else:
            payload = _repair_json(text)
    if not isinstance(payload, dict):
        raise json.JSONDecodeError("structured response was not a JSON object", text, 0)
    return payload


def _repair_json(text: str) -> dict:
    """Tier 3: tolerate common LLM JSON mistakes (missing commas, single quotes, etc.).

    Uses the json-repair library, which is more permissive than json.loads.
    """
    try:
        import json_repair  # type: ignore
    except ImportError:
        raise json.JSONDecodeError("json_repair not installed and standard parse failed", text, 0)
    repaired = json_repair.loads(text)
    if not isinstance(repaired, dict):
        raise json.JSONDecodeError("repaired JSON was not an object", text, 0)
    return repaired
