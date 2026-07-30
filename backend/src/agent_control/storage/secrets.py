from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from agent_control.config import SecretVaultConfig
from agent_control.config_sync import read_env_value


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
        path.write_bytes(self._fernet().encrypt(raw))

    def _path(self) -> Path:
        return Path(self.config.path).expanduser()

    def _fernet(self) -> Fernet:
        raw_key = read_env_value(self.config.key_env)
        if not raw_key:
            raise SecretVaultError(f"{self.config.key_env} is required to read or write the secret vault")
        return Fernet(_normalize_fernet_key(raw_key))


def _normalize_fernet_key(raw_key: str) -> bytes:
    encoded = raw_key.encode("utf-8")
    try:
        Fernet(encoded)
        return encoded
    except Exception:
        digest = hashlib.sha256(encoded).digest()
        return base64.urlsafe_b64encode(digest)


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
