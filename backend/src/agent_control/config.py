from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource

from agent_control.config_sync import read_env_file, read_env_value
from agent_control.schemas import Capability, RiskLevel, StrictBaseModel


class ServerConfig(StrictBaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    public_base_url: str | None = None
    admin_enabled: bool = True
    admin_token_env: str = "AGENT_ADMIN_TOKEN"


class IdentityConfig(StrictBaseModel):
    instance_name: str = "local-agent-control"
    owner_label: str = "local-user"


class TelegramConfig(StrictBaseModel):
    enabled: bool = False
    token_env: str = "TELEGRAM_BOT_TOKEN"
    token: SecretStr | None = None
    allowed_user_ids: list[int] = Field(default_factory=list)
    allowed_chat_ids: list[int] = Field(default_factory=list)
    polling: bool = True


class ChannelsConfig(StrictBaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


class LLMProfileConfig(StrictBaseModel):
    provider: str = "openai_compatible"
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: SecretStr | None = None
    timeout_seconds: int = Field(default=60, ge=1)
    max_tokens: int = Field(default=4096, ge=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


class LLMConfig(StrictBaseModel):
    default_profile: str = "default"
    profiles: dict[str, LLMProfileConfig] = Field(default_factory=dict)

    @field_validator("profiles")
    @classmethod
    def profile_names_required(cls, value: dict[str, LLMProfileConfig]) -> dict[str, LLMProfileConfig]:
        for name in value:
            if not name:
                raise ValueError("LLM profile names cannot be empty")
        return value


class CapabilityPolicy(StrictBaseModel):
    enabled: bool = False
    scopes: list[str] = Field(default_factory=list)
    requires_approval: bool = True
    max_risk_level: RiskLevel = RiskLevel.LOW
    allow_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)


class ApprovalPolicyConfig(StrictBaseModel):
    default_timeout_seconds: int = Field(default=900, ge=1)
    require_approval_at_or_above: RiskLevel = RiskLevel.MEDIUM
    approval_token_ttl_seconds: int = Field(default=900, ge=1)


class StorageConfig(StrictBaseModel):
    database_url: str = "sqlite:///./agent_control.db"
    artifact_dir: str = ".agent_control/artifacts"


class LoggingConfig(StrictBaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_logs: bool = True
    redact_patterns: list[str] = Field(default_factory=lambda: ["token", "api_key", "secret", "password"])


class LimitsConfig(StrictBaseModel):
    max_parallel_tasks: int = Field(default=1, ge=1)
    max_retries: int = Field(default=3, ge=0)
    tool_timeout_seconds: int = Field(default=900, ge=1)
    max_log_chars: int = Field(default=12000, ge=100)
    retry_backoff_seconds: int = Field(default=30, ge=1)


class VSCodeAdapterConfig(StrictBaseModel):
    enabled: bool = False
    bridge_host: str = "127.0.0.1"
    bridge_port: int = Field(default=8766, ge=1, le=65535)
    auth_token_env: str = "VSCODE_BRIDGE_TOKEN"


class WorkspaceAdapterConfig(StrictBaseModel):
    enabled: bool = True
    root_dir: str = ".agent_control/workspaces"
    web_host: str = "127.0.0.1"
    web_port_start: int = Field(default=8890, ge=1, le=65535)
    open_browser: bool = True


class AdapterFactoryConfig(StrictBaseModel):
    enabled: bool = True
    root_dir: str = ".agent_control/adapters"


class TerminalAdapterConfig(StrictBaseModel):
    enabled: bool = False
    allowed_working_dirs: list[str] = Field(default_factory=list)
    blocked_command_patterns: list[str] = Field(default_factory=lambda: ["rm -rf", "del /s", "format "])


class CodingAssistantAdapterConfig(StrictBaseModel):
    enabled: bool = False
    command_template: list[str] = Field(default_factory=list)
    working_dir: str | None = None
    timeout_seconds: int = Field(default=900, ge=1)
    output_limit_chars: int = Field(default=12000, ge=100)
    rate_limit_patterns: list[str] = Field(default_factory=lambda: ["rate limit", "too many requests"])
    usage_limit_patterns: list[str] = Field(default_factory=lambda: ["usage limit", "quota exceeded"])


class DesktopAdapterConfig(StrictBaseModel):
    screenshot_enabled: bool = False
    control_enabled: bool = False
    screenshot_interval_seconds: int = Field(default=10, ge=1)
    screenshot_format: Literal["png"] = "png"


class STTAdapterConfig(StrictBaseModel):
    enabled: bool = False
    provider: str = "local_whisper"
    model: str = "base"


class AdaptersConfig(StrictBaseModel):
    vscode: VSCodeAdapterConfig = Field(default_factory=VSCodeAdapterConfig)
    workspace: WorkspaceAdapterConfig = Field(default_factory=WorkspaceAdapterConfig)
    adapter_factory: AdapterFactoryConfig = Field(default_factory=AdapterFactoryConfig)
    terminal: TerminalAdapterConfig = Field(default_factory=TerminalAdapterConfig)
    coding_assistant: CodingAssistantAdapterConfig = Field(default_factory=CodingAssistantAdapterConfig)
    desktop: DesktopAdapterConfig = Field(default_factory=DesktopAdapterConfig)
    stt: STTAdapterConfig = Field(default_factory=STTAdapterConfig)


def default_capability_policies() -> dict[Capability, CapabilityPolicy]:
    policies = {capability: CapabilityPolicy() for capability in Capability}
    policies[Capability.TELEGRAM_RECEIVE] = CapabilityPolicy(
        enabled=True,
        requires_approval=False,
        max_risk_level=RiskLevel.LOW,
    )
    policies[Capability.TELEGRAM_SEND] = CapabilityPolicy(
        enabled=True,
        requires_approval=False,
        max_risk_level=RiskLevel.LOW,
    )
    policies[Capability.LLM_GENERATE] = CapabilityPolicy(
        enabled=True,
        requires_approval=False,
        max_risk_level=RiskLevel.LOW,
    )
    return policies


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        yaml_file="config/config.yaml",
        yaml_file_encoding="utf-8",
        extra="forbid",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    capabilities: dict[Capability, CapabilityPolicy] = Field(default_factory=default_capability_policies)
    approval_policy: ApprovalPolicyConfig = Field(default_factory=ApprovalPolicyConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    adapters: AdaptersConfig = Field(default_factory=AdaptersConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        _load_env_file_into_process()
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    def safe_summary(self) -> dict[str, Any]:
        return {
            "server": self.server.model_dump(),
            "identity": self.identity.model_dump(),
            "channels": {
                "telegram": {
                    "enabled": self.channels.telegram.enabled,
                    "token_env": self.channels.telegram.token_env,
                    "token": "***" if self.channels.telegram.token else None,
                    "token_present": bool(self.channels.telegram.token)
                    or bool(read_env_value(self.channels.telegram.token_env)),
                    "allowed_user_ids": self.channels.telegram.allowed_user_ids,
                    "allowed_chat_ids": self.channels.telegram.allowed_chat_ids,
                    "allowed_user_count": len(self.channels.telegram.allowed_user_ids),
                    "allowed_chat_count": len(self.channels.telegram.allowed_chat_ids),
                    "polling": self.channels.telegram.polling,
                }
            },
            "llm": {
                "default_profile": self.llm.default_profile,
                "profiles": {
                    name: {
                        "provider": profile.provider,
                        "model": profile.model,
                        "base_url": profile.base_url,
                        "api_key_env": profile.api_key_env,
                        "api_key": "***" if profile.api_key else None,
                        "api_key_present": bool(profile.api_key)
                        or bool(profile.api_key_env and read_env_value(profile.api_key_env)),
                        "timeout_seconds": profile.timeout_seconds,
                        "max_tokens": profile.max_tokens,
                        "temperature": profile.temperature,
                    }
                    for name, profile in self.llm.profiles.items()
                },
            },
            "capabilities": {
                capability.value: policy.model_dump(mode="json")
                for capability, policy in sorted(self.capabilities.items(), key=lambda item: item[0].value)
            },
            "approval_policy": self.approval_policy.model_dump(mode="json"),
            "storage": self.storage.model_dump(),
            "logging": self.logging.model_dump(),
            "limits": self.limits.model_dump(),
            "adapters": self.adapters.model_dump(),
        }


def load_settings(config_path: str | Path | None = None, **overrides: Any) -> AppSettings:
    if config_path is not None:
        config_file = str(Path(config_path))

        class RuntimePathSettings(AppSettings):
            model_config = SettingsConfigDict(
                **{
                    **AppSettings.model_config,
                    "yaml_file": config_file,
                }
            )

        return RuntimePathSettings(**overrides)
    return AppSettings(**overrides)


def _load_env_file_into_process() -> None:
    for key, value in read_env_file().items():
        os.environ.setdefault(key, value)
