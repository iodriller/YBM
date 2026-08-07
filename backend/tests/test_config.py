from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_control.config import AppSettings, is_loopback_host
from agent_control.schemas import Capability


def test_default_invasive_capabilities_are_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    settings = AppSettings(_env_file=None)

    invasive = [
        Capability.TERMINAL_RUN,
        Capability.FILESYSTEM_READ,
        Capability.FILESYSTEM_WRITE,
        Capability.VSCODE_READ_STATE,
        Capability.VSCODE_WRITE_FILES,
        Capability.DESKTOP_SCREENSHOT,
        Capability.DESKTOP_CONTROL,
        Capability.BROWSER_OPEN,
        Capability.BROWSER_CONTROL,
        Capability.NETWORK_HTTP,
        Capability.SCHEDULE_MANAGE,
        Capability.GITHUB_PUSH,
        Capability.DEPENDENCIES_INSTALL,
    ]

    for capability in invasive:
        assert settings.capabilities[capability].enabled is False


def test_unknown_top_level_config_key_fails() -> None:
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, unknown_section={})


def test_invalid_capability_name_fails() -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            _env_file=None,
            capabilities={"not.a.capability": {"enabled": True}},
        )


def test_minimal_llm_profile_validates() -> None:
    settings = AppSettings(
        _env_file=None,
        llm={"profiles": {"default": {"model": "local-model"}}},
    )

    assert settings.llm.profiles["default"].model == "local-model"


def test_safe_summary_redacts_secret_values() -> None:
    settings = AppSettings(
        _env_file=None,
        channels={"telegram": {"token": "super-secret-token"}},
        llm={"profiles": {"default": {"model": "x", "api_key": "secret-key"}}},
    )

    summary = settings.safe_summary()

    assert summary["channels"]["telegram"]["token"] == "***"
    assert summary["llm"]["profiles"]["default"]["api_key"] == "***"


def test_safe_summary_reports_whatsapp_count_not_raw_numbers() -> None:
    settings = AppSettings(
        _env_file=None,
        channels={"whatsapp": {"enabled": True, "allowed_numbers": ["15551234567", "19998887777"]}},
    )

    summary = settings.safe_summary()

    whatsapp = summary["channels"]["whatsapp"]
    assert whatsapp["enabled"] is True
    assert whatsapp["allowed_number_count"] == 2
    assert "allowed_numbers" not in whatsapp
    assert "15551234567" not in str(whatsapp)


def test_safe_summary_strips_mcp_server_env_values() -> None:
    settings = AppSettings(
        _env_file=None,
        mcp={
            "servers": {
                "github": {
                    "command": "npx",
                    "env": {"GITHUB_TOKEN": "ghp_super_secret"},
                }
            }
        },
    )

    summary = settings.safe_summary()
    server = summary["mcp"]["servers"]["github"]

    assert server["env_keys"] == ["GITHUB_TOKEN"]
    assert "env" not in server
    assert "ghp_super_secret" not in str(summary)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.0.0.2",  # regression: only the exact 127.0.0.1 literal used to count
        "127.255.255.255",  # full 127.0.0.0/8 range
        "localhost",
        "::1",
        "0:0:0:0:0:0:0:1",
        "::ffff:127.0.0.1",  # regression: IPv4-mapped IPv6 loopback used to count as non-loopback
    ],
)
def test_is_loopback_host_covers_full_loopback_range(host: str) -> None:
    assert is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "10.0.0.5",
        "192.168.1.1",
        # An IPv4-mapped *public* address must not inherit loopback just
        # because it is unwrapped - the mapping is followed, not assumed safe.
        "::ffff:8.8.8.8",
        "example.com",
        "",
        "not-an-ip",
    ],
)
def test_is_loopback_host_rejects_non_loopback(host: str) -> None:
    assert is_loopback_host(host) is False
