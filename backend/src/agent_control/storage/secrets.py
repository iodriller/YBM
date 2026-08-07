from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from agent_control.config import SecretVaultConfig
from agent_control.config_sync import read_env_value

_SCRYPT_SALT_BYTES = 16


class SecretVaultError(RuntimeError):
    pass


class SecretVault:
    def __init__(self, config: SecretVaultConfig) -> None:
        self.config = config

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode("ascii")

    def get_secret(self, service: str, key: str) -> str:
        service = service.strip()
        key = key.strip()
        data = self._read()
        try:
            value = data[service][key]
        except KeyError as exc:
            raise SecretVaultError(f"secret not found: {service}.{key}") from exc
        if not isinstance(value, str):
            raise SecretVaultError(f"secret value is not a string: {service}.{key}")
        return value

    def get_ref(self, ref: str) -> str:
        service, key = _split_ref(ref)
        return self.get_secret(service, key)

    def set_secret(self, service: str, key: str, value: str) -> None:
        service = service.strip()
        key = key.strip()
        if not service or not key:
            raise SecretVaultError("service and key are required")
        data = self._read()
        service_values = data.setdefault(service, {})
        if not isinstance(service_values, dict):
            raise SecretVaultError(f"invalid secret service object: {service}")
        service_values[key] = value
        self._write(data)

    def list_services(self) -> list[str]:
        return sorted(self._read())

    def list_secrets(self) -> dict[str, list[str]]:
        """Service -> sorted key names, never values. For a UI/API listing
        that must not leak secret contents."""
        data = self._read()
        return {service: sorted(keys) for service, keys in sorted(data.items())}

    def delete_secret(self, service: str, key: str) -> bool:
        service = service.strip()
        key = key.strip()
        data = self._read()
        service_values = data.get(service)
        if not isinstance(service_values, dict) or key not in service_values:
            return False
        del service_values[key]
        if not service_values:
            del data[service]
        self._write(data)
        return True

    def _read(self) -> dict[str, dict[str, str]]:
        path = self._path()
        if not path.exists():
            return {}
        token = path.read_bytes()
        try:
            raw = self._fernet().decrypt(token)
        except InvalidToken as exc:
            raise SecretVaultError("secret vault could not be decrypted with the configured key") from exc
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise SecretVaultError("secret vault payload must be a JSON object")
        return payload

    def _write(self, data: dict[str, Any]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        encrypted = self._fernet().encrypt(raw)
        # Write-then-rename: os.replace is atomic on both POSIX and Windows,
        # so a crash or kill mid-write can never leave a half-written,
        # permanently undecryptable vault.json behind.
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_bytes(encrypted)
        os.replace(tmp_path, path)

    def _path(self) -> Path:
        return Path(self.config.path).expanduser()

    def _salt_path(self) -> Path:
        return self._path().with_name(self._path().name + ".salt")

    def _fernet(self) -> Fernet:
        raw_key = read_env_value(self.config.key_env)
        if not raw_key:
            raise SecretVaultError(f"{self.config.key_env} is required to read or write the secret vault")
        return Fernet(_normalize_fernet_key(raw_key, self._salt_path()))


def _normalize_fernet_key(raw_key: str, salt_path: Path) -> bytes:
    encoded = raw_key.encode("utf-8")
    try:
        Fernet(encoded)
        return encoded
    except Exception:
        pass
    # Not a valid Fernet key as-is (e.g. a hand-typed passphrase rather than
    # SecretVault.generate_key() output) - derive one with a real KDF instead
    # of a single unsalted SHA-256 round, which is cheap to brute-force
    # against a weak passphrase if the vault file ever leaks. Salt is
    # persisted next to the vault so the same passphrase always derives the
    # same key across runs.
    salt = _load_or_create_salt(salt_path)
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(encoded))


def _load_or_create_salt(salt_path: Path) -> bytes:
    try:
        existing = salt_path.read_bytes()
        if len(existing) == _SCRYPT_SALT_BYTES:
            return existing
    except OSError:
        pass
    salt = os.urandom(_SCRYPT_SALT_BYTES)
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt_path.write_bytes(salt)
    return salt


def _split_ref(ref: str) -> tuple[str, str]:
    text = ref.strip()
    if ":" in text:
        service, key = text.split(":", 1)
    elif "." in text:
        service, key = text.rsplit(".", 1)
    else:
        raise SecretVaultError("secret reference must look like 'service.key' or 'service:key'")
    service = service.strip()
    key = key.strip()
    if not service or not key:
        raise SecretVaultError("secret reference requires both service and key")
    return service, key
