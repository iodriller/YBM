from __future__ import annotations

import pytest

from agent_control.config import SecretVaultConfig
from agent_control.storage.secrets import SecretVault, SecretVaultError


def _vault(tmp_path, monkeypatch) -> SecretVault:
    monkeypatch.setenv("AGENT_SECRET_VAULT_KEY", SecretVault.generate_key())
    return SecretVault(SecretVaultConfig(path=str(tmp_path / "vault.json")))


def test_set_get_and_list_round_trip(tmp_path, monkeypatch) -> None:
    vault = _vault(tmp_path, monkeypatch)

    vault.set_secret("openai", "api_key", "sk-test-123")
    vault.set_secret("openai", "org_id", "org-1")
    vault.set_secret("stripe", "secret_key", "sk_live_x")

    assert vault.get_secret("openai", "api_key") == "sk-test-123"
    assert vault.get_ref("openai.api_key") == "sk-test-123"
    assert vault.get_ref("openai:api_key") == "sk-test-123"
    assert vault.list_services() == ["openai", "stripe"]
    assert vault.list_secrets() == {"openai": ["api_key", "org_id"], "stripe": ["secret_key"]}


def test_list_secrets_never_exposes_values(tmp_path, monkeypatch) -> None:
    """The whole point of list_secrets() is a UI/API-safe listing - it must
    return key names only, never the encrypted-but-in-memory values."""
    vault = _vault(tmp_path, monkeypatch)
    vault.set_secret("openai", "api_key", "sk-should-never-appear")

    listing = vault.list_secrets()

    assert "sk-should-never-appear" not in str(listing)
    assert listing == {"openai": ["api_key"]}


def test_delete_secret_removes_key_and_empty_service(tmp_path, monkeypatch) -> None:
    vault = _vault(tmp_path, monkeypatch)
    vault.set_secret("openai", "api_key", "sk-1")
    vault.set_secret("openai", "org_id", "org-1")

    assert vault.delete_secret("openai", "api_key") is True
    assert vault.list_secrets() == {"openai": ["org_id"]}

    assert vault.delete_secret("openai", "org_id") is True
    # Service disappears entirely once its last key is removed - list_secrets
    # should not report a service with zero keys.
    assert vault.list_secrets() == {}


def test_delete_secret_returns_false_for_unknown_service_or_key(tmp_path, monkeypatch) -> None:
    vault = _vault(tmp_path, monkeypatch)
    vault.set_secret("openai", "api_key", "sk-1")

    assert vault.delete_secret("openai", "missing_key") is False
    assert vault.delete_secret("unknown_service", "api_key") is False
    # Nothing was mutated by the failed deletes.
    assert vault.list_secrets() == {"openai": ["api_key"]}


def test_get_secret_raises_on_unknown_service_or_key(tmp_path, monkeypatch) -> None:
    vault = _vault(tmp_path, monkeypatch)
    vault.set_secret("openai", "api_key", "sk-1")

    with pytest.raises(SecretVaultError):
        vault.get_secret("openai", "missing")
    with pytest.raises(SecretVaultError):
        vault.get_secret("unknown", "api_key")


def test_list_secrets_on_empty_vault_returns_empty_dict(tmp_path, monkeypatch) -> None:
    vault = _vault(tmp_path, monkeypatch)

    assert vault.list_secrets() == {}
    assert vault.list_services() == []
