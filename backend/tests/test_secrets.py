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


def test_passphrase_key_derives_via_salted_kdf_and_round_trips(tmp_path, monkeypatch) -> None:
    """A key that isn't a valid Fernet key on its own (a hand-typed
    passphrase) must still work end-to-end, deterministically, across
    separate SecretVault instances - proving the derived key is stable given
    the same passphrase and persisted salt, not just working by accident
    within a single instance."""
    monkeypatch.setenv("AGENT_SECRET_VAULT_KEY", "a plain human passphrase, not a Fernet key")
    config = SecretVaultConfig(path=str(tmp_path / "vault.json"))

    SecretVault(config).set_secret("openai", "api_key", "sk-test-123")

    assert SecretVault(config).get_secret("openai", "api_key") == "sk-test-123"
    assert (tmp_path / "vault.json.salt").exists()


def test_passphrase_key_salt_is_persisted_not_random_per_call(tmp_path, monkeypatch) -> None:
    """The derived key must be stable across process restarts: if the salt
    weren't persisted, a fresh SecretVault instance would derive a different
    key and fail to decrypt the existing vault."""
    monkeypatch.setenv("AGENT_SECRET_VAULT_KEY", "another passphrase")
    config = SecretVaultConfig(path=str(tmp_path / "vault.json"))
    SecretVault(config).set_secret("svc", "key", "value")

    salt_first = (tmp_path / "vault.json.salt").read_bytes()
    SecretVault(config).get_secret("svc", "key")  # second, independent instance
    salt_second = (tmp_path / "vault.json.salt").read_bytes()

    assert salt_first == salt_second


def test_write_leaves_no_tmp_file_and_vault_is_valid(tmp_path, monkeypatch) -> None:
    """Atomic write (write-to-.tmp, then os.replace) must not leave the
    temporary file behind on success."""
    vault = _vault(tmp_path, monkeypatch)
    vault.set_secret("openai", "api_key", "sk-1")

    vault_path = tmp_path / "vault.json"
    tmp_path_candidate = tmp_path / "vault.json.tmp"
    assert vault_path.exists()
    assert not tmp_path_candidate.exists()
    assert vault.get_secret("openai", "api_key") == "sk-1"
