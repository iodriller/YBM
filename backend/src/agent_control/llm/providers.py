from __future__ import annotations

import json
import logging
import base64
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from agent_control.config import AppSettings, LLMProfileConfig
from agent_control.config_sync import read_env_value
from agent_control.prompts import render_prompt


logger = logging.getLogger(__name__)

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


def _normalize_usage(raw: dict | None, *, model: str) -> dict | None:
    """OpenAI-compatible `usage` objects use prompt_tokens/completion_tokens/
    total_tokens; a few servers (notably some Ollama-backed proxies) omit the
    field or use eval_count/prompt_eval_count instead. Return a normalized
    dict, or None when nothing usable was reported - callers must treat that
    as "unknown", not "zero"."""
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens", raw.get("prompt_eval_count"))
    completion = raw.get("completion_tokens", raw.get("eval_count"))
    total = raw.get("total_tokens")
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    if prompt is None and completion is None and total is None:
        return None
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "total_tokens": int(total or 0),
        "model": model,
    }


class OpenAICompatibleProvider:
    def __init__(self, profile: LLMProfileConfig) -> None:
        if not profile.base_url:
            raise ValueError("base_url is required for OpenAI-compatible LLM provider")
        self.profile = profile
        # Usage from the most recent successful _chat() call - see
        # docs/HISTORY.md Part 4 T1.4. None until a call completes, or if the
        # server never reported usage; never a fabricated zero.
        self.last_usage: dict | None = None
        # Siblings to last_usage, for LLM-call persistence (docs/UI_UX_AUDIT.md
        # Phase 14d) - same "None until a call completes" contract.
        self.last_request: list[dict] | None = None
        self.last_response_text: str | None = None
        self.last_model: str | None = None
        self.last_started_at: datetime | None = None
        self.last_latency_ms: float | None = None

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

        started_at = datetime.now(timezone.utc)
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=self.profile.timeout_seconds) as client:
            url = f"{self.profile.base_url.rstrip('/')}/chat/completions"
            response = await client.post(url, headers=headers, json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ValueError(_http_error_detail(exc.response)) from exc
            data = dict(response.json())
            self.last_usage = _normalize_usage(data.get("usage"), model=self.profile.model)
            self.last_request = payload["messages"]
            try:
                self.last_response_text = str(data["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError):
                self.last_response_text = None
            self.last_model = self.profile.model
            self.last_started_at = started_at
            self.last_latency_ms = (time.monotonic() - start) * 1000
            return data

    def _api_key(self) -> str | None:
        if self.profile.api_key:
            return self.profile.api_key.get_secret_value()
        if self.profile.api_key_env:
            return read_env_value(self.profile.api_key_env)
        return None


class FailoverLLMProvider:
    """Try the primary provider first; on unavailability fall back to the secondary.

    Unavailability means the endpoint could not serve the request at all —
    connection errors, timeouts, or HTTP 5xx. Model-quality failures (invalid
    structured output, HTTP 4xx) are NOT failed over: they would fail the same
    way against the fallback or indicate a request bug, and silently switching
    models on them would mask the real problem.
    """

    def __init__(self, primary: LLMProvider, fallback: LLMProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        # Proxies whichever inner provider actually served the last call -
        # the fallback's usage after a failover, the primary's otherwise.
        self.last_usage: dict | None = None
        self.last_request: list[dict] | None = None
        self.last_response_text: str | None = None
        self.last_model: str | None = None
        self.last_started_at: datetime | None = None
        self.last_latency_ms: float | None = None

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        return await self._call("generate_text", system_prompt, user_prompt)

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        return await self._call("generate_multimodal_text", system_prompt, user_prompt, image_paths)

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[T],
        *,
        temperature: float | None = None,
    ) -> T:
        return await self._call(
            "generate_structured", system_prompt, user_prompt, output_model, temperature=temperature
        )

    async def _call(self, method: str, *args, **kwargs):
        try:
            result = await getattr(self.primary, method)(*args, **kwargs)
            self._copy_last_call_state(self.primary)
            return result
        except Exception as exc:
            if not _is_unavailability(exc):
                raise
            logger.warning("primary LLM unavailable (%s); using fallback profile", exc)
            result = await getattr(self.fallback, method)(*args, **kwargs)
            self._copy_last_call_state(self.fallback)
            return result

    def _copy_last_call_state(self, provider: LLMProvider) -> None:
        self.last_usage = getattr(provider, "last_usage", None)
        self.last_request = getattr(provider, "last_request", None)
        self.last_response_text = getattr(provider, "last_response_text", None)
        self.last_model = getattr(provider, "last_model", None)
        self.last_started_at = getattr(provider, "last_started_at", None)
        self.last_latency_ms = getattr(provider, "last_latency_ms", None)


def _is_unavailability(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    # OpenAICompatibleProvider wraps HTTP errors in ValueError with the status
    # in the message; 5xx means the endpoint itself is broken/overloaded.
    if isinstance(exc, ValueError):
        message = str(exc)
        return "failed with HTTP 5" in message
    return False


def _build_profile_provider(settings: AppSettings, profile_name: str | None, role: str) -> LLMProvider | None:
    if not profile_name:
        return None
    profile = settings.llm.profiles.get(profile_name)
    if profile is None:
        return None
    if profile.provider != "openai_compatible":
        raise ValueError(f"unsupported {role} LLM provider: {profile.provider}")
    return OpenAICompatibleProvider(profile)


def _with_fallback(settings: AppSettings, provider: LLMProvider | None, primary_name: str | None) -> LLMProvider | None:
    if provider is None:
        return None
    fallback_name = settings.llm.fallback_profile
    if not fallback_name or fallback_name == primary_name:
        return provider
    fallback = _build_profile_provider(settings, fallback_name, "fallback")
    if fallback is None:
        return provider
    return FailoverLLMProvider(provider, fallback)


def build_default_llm_provider(settings: AppSettings) -> LLMProvider | None:
    provider = _build_profile_provider(settings, settings.llm.default_profile, "default")
    return _with_fallback(settings, provider, settings.llm.default_profile)


def build_major_llm_provider(settings: AppSettings) -> LLMProvider | None:
    """Build the LLM provider for complex/major tasks, if a major_profile is configured."""
    provider = _build_profile_provider(settings, settings.llm.major_profile, "major")
    return _with_fallback(settings, provider, settings.llm.major_profile)


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
        logger.warning("failed to parse profile base_url %r for localdeploy detection", base_url, exc_info=True)
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


def strip_code_fences(text: str) -> str:
    """Drop a leading/trailing ``` fence from an LLM response.

    Models wrap structured output in markdown fences constantly, regardless of
    instructions. Both the JSON parser here and the generated-code cleaner in
    `tools/code_interpreter.py` need this and had identical copies.
    """
    text = str(text).strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _loads_json_object(content: str) -> dict:
    text = strip_code_fences(content)
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
