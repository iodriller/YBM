from __future__ import annotations

import shutil
from pathlib import Path

from agent_control import bootstrap
from agent_control.config import AppSettings
from agent_control.config_sync import read_env_value


REPO_EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_SECRET_VAULT_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("YBM_LOCALDEPLOY_ROOT", raising=False)


def test_setup_creates_config_from_example(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")

    exit_code = bootstrap.run_setup()

    assert exit_code == 0
    assert (tmp_path / "config" / "config.yaml").exists()


def test_setup_generates_admin_token_and_vault_key(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")

    bootstrap.run_setup()

    assert read_env_value("AGENT_ADMIN_TOKEN") is not None
    assert read_env_value("AGENT_SECRET_VAULT_KEY") is not None


def test_setup_is_idempotent_on_generated_secrets(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")

    bootstrap.run_setup()
    first_token = read_env_value("AGENT_ADMIN_TOKEN")

    bootstrap.run_setup()
    second_token = read_env_value("AGENT_ADMIN_TOKEN")

    assert first_token == second_token


def test_setup_saves_telegram_token_when_provided(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")

    bootstrap.run_setup(telegram_token="test-token-123")

    assert read_env_value("TELEGRAM_BOT_TOKEN") == "test-token-123"


def test_setup_fails_without_example_config(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    exit_code = bootstrap.run_setup()
    assert exit_code == 1


def test_doctor_fails_when_config_missing(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    exit_code = bootstrap.run_doctor()
    assert exit_code == 1


def test_doctor_reports_ok_after_setup(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")
    bootstrap.run_setup()

    exit_code = bootstrap.run_doctor()

    assert exit_code == 0


def test_check_admin_token_fails_when_unset_and_non_loopback(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(_env_file=None, server={"host": "0.0.0.0"})
    check = bootstrap._check_admin_token(settings)
    assert check.status == "fail"


def test_check_admin_token_warns_when_unset_and_loopback(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(_env_file=None)
    check = bootstrap._check_admin_token(settings)
    assert check.status == "warn"


def test_check_telegram_ok_when_disabled(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(_env_file=None)
    check = bootstrap._check_telegram(settings)
    assert check.status == "ok"


def test_check_telegram_fails_when_enabled_without_token(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(
        _env_file=None,
        channels={"telegram": {"enabled": True, "token_env": "TELEGRAM_BOT_TOKEN"}},
    )
    check = bootstrap._check_telegram(settings)
    assert check.status == "fail"


def test_desktop_capability_not_requested_by_default(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(_env_file=None)
    assert bootstrap._desktop_capability_requested(settings) is False
