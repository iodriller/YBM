"""The catalog of LLM providers YBM can talk to.

Adding a provider should be a row here, not a new code path. Everything that
speaks the OpenAI chat-completions shape differs only in a base URL, a key
env var, and a default model, so those are data. Anthropic is the one
exception - its API is not OpenAI-compatible and Anthropic's own guidance is to
use the official SDK rather than a compatibility shim - so it carries
`kind="anthropic"` and is served by AnthropicProvider.

Base URLs were verified against each provider's own documentation rather than
recalled. Model IDs rot faster than endpoints: `default_model` is only a
starting suggestion, and the console populates its real list from the
provider's own `/models` endpoint (`list_models`) wherever one exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    #: "openai_compatible" routes to OpenAICompatibleProvider; "anthropic" to
    #: the native provider. These are the values stored in a profile's
    #: `provider` field, so they are part of the config contract.
    kind: str
    #: None means the provider's own SDK supplies it (Anthropic).
    base_url: str | None
    #: Conventional env var. Users can override per profile.
    api_key_env: str | None
    default_model: str
    #: Whether GET {base_url}/models works and is worth offering as a picker.
    lists_models: bool = True
    #: Local runtimes need no key and no network.
    local: bool = False
    #: Where a user goes to get a key. Shown as a link next to the key field.
    keys_url: str | None = None
    notes: str = ""
    #: Suggestions shown when the provider cannot list models itself.
    example_models: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_key(self) -> bool:
        return not self.local


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="anthropic",
        label="Anthropic (Claude)",
        kind="anthropic",
        base_url=None,
        api_key_env="ANTHROPIC_API_KEY",
        default_model="claude-sonnet-5",
        lists_models=True,
        keys_url="https://console.anthropic.com/settings/keys",
        notes="Claude models. Not OpenAI-compatible - uses the official SDK.",
        example_models=("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"),
    ),
    ProviderSpec(
        key="openai",
        label="OpenAI",
        kind="openai_compatible",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-4.1",
        keys_url="https://platform.openai.com/api-keys",
    ),
    ProviderSpec(
        key="openrouter",
        label="OpenRouter",
        kind="openai_compatible",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        default_model="anthropic/claude-sonnet-4.5",
        keys_url="https://openrouter.ai/keys",
        notes="One key, many providers' models.",
    ),
    ProviderSpec(
        key="google",
        label="Google (Gemini)",
        kind="openai_compatible",
        # The trailing slash is load-bearing in Google's own examples; the
        # request builder strips it before appending the path, so it is kept
        # here to match their docs exactly.
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY",
        default_model="gemini-2.5-flash",
        keys_url="https://aistudio.google.com/apikey",
        notes="Gemini through Google's OpenAI-compatible endpoint.",
    ),
    ProviderSpec(
        key="groq",
        label="Groq",
        kind="openai_compatible",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        default_model="llama-3.3-70b-versatile",
        keys_url="https://console.groq.com/keys",
        notes="Very fast inference for open models.",
    ),
    ProviderSpec(
        key="deepseek",
        label="DeepSeek",
        kind="openai_compatible",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        default_model="deepseek-chat",
        keys_url="https://platform.deepseek.com/api_keys",
    ),
    ProviderSpec(
        key="mistral",
        label="Mistral",
        kind="openai_compatible",
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        default_model="mistral-large-latest",
        keys_url="https://console.mistral.ai/api-keys",
    ),
    ProviderSpec(
        key="xai",
        label="xAI (Grok)",
        kind="openai_compatible",
        base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
        default_model="grok-4",
        keys_url="https://console.x.ai",
    ),
    ProviderSpec(
        key="together",
        label="Together AI",
        kind="openai_compatible",
        base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        keys_url="https://api.together.ai/settings/api-keys",
    ),
    ProviderSpec(
        key="ollama",
        label="Ollama (on this machine)",
        kind="openai_compatible",
        base_url="http://127.0.0.1:11434/v1",
        api_key_env=None,
        default_model="qwen3:8b",
        local=True,
        notes="Free. Runs on your own hardware; nothing leaves the machine.",
    ),
    ProviderSpec(
        key="lmstudio",
        label="LM Studio (on this machine)",
        kind="openai_compatible",
        base_url="http://127.0.0.1:1234/v1",
        api_key_env=None,
        default_model="local-model",
        local=True,
        notes="Free. Runs on your own hardware.",
    ),
    ProviderSpec(
        key="localdeploy",
        label="LocalDeploy (on this machine)",
        kind="openai_compatible",
        base_url="http://127.0.0.1:8000/v1",
        api_key_env=None,
        default_model="qwen3vl_8b_ollama",
        local=True,
        notes="Free. The bundled local model server.",
    ),
    ProviderSpec(
        key="custom",
        label="Other OpenAI-compatible endpoint",
        kind="openai_compatible",
        base_url=None,
        api_key_env=None,
        default_model="",
        lists_models=False,
        notes="Anything that speaks the OpenAI chat-completions API.",
    ),
)

BY_KEY: dict[str, ProviderSpec] = {spec.key: spec for spec in PROVIDERS}


def get(key: str) -> ProviderSpec | None:
    return BY_KEY.get(key)
