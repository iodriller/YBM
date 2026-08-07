from __future__ import annotations

import httpx
import pytest

from agent_control.config import HttpRequestAdapterConfig, SecretVaultConfig
from agent_control.schemas import AuditEventType, Capability, ToolCallRequest, ToolResultStatus
from agent_control.storage.secrets import SecretVault
from agent_control.tools.http_request import HttpRequestAdapter, _require_allowed_url


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[AuditEventType, dict]] = []

    def append(self, event_type, *, actor, task_id, payload):
        self.events.append((event_type, {"actor": actor, "task_id": task_id, **payload}))


@pytest.mark.asyncio
async def test_http_request_injects_and_redacts_secret(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_SECRET_VAULT_KEY", SecretVault.generate_key())
    secrets_config = SecretVaultConfig(path=str(tmp_path / "vault.json"))
    SecretVault(secrets_config).set_secret("demo", "token", "super-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer super-token"
        return httpx.Response(
            200,
            json={"echo": "super-token"},
            headers={"set-cookie": "session=super-token"},
        )

    adapter = HttpRequestAdapter(
        HttpRequestAdapterConfig(allowed_hosts=["api.example.com"]),
        secrets_config,
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_http",
            tool_name="http.request",
            capability=Capability.NETWORK_HTTP,
            input={
                "operation": "request",
                "method": "GET",
                "url": "https://api.example.com/me",
                "secret_refs": {
                    "headers.Authorization": {"ref": "demo.token", "template": "Bearer {secret}"},
                },
            },
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["json"]["echo"] == "***"
    assert result.output["headers"]["set-cookie"] == "***"


@pytest.mark.asyncio
async def test_http_request_records_egress_for_the_receipt(tmp_path) -> None:
    """docs/UI_UX_AUDIT.md Phase 2: a real, non-loopback call must show up
    as an EGRESS_CONTACTED audit event so Task Receipts can say what left
    the machine - egress.record_egress()."""
    secrets_config = SecretVaultConfig(path=str(tmp_path / "vault.json"))
    audit = _FakeAudit()
    adapter = HttpRequestAdapter(
        HttpRequestAdapterConfig(allowed_hosts=["api.example.com"]),
        secrets_config,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True})),
        audit=audit,
    )

    await adapter.execute(
        ToolCallRequest(
            task_id="task_egress",
            tool_name="http.request",
            capability=Capability.NETWORK_HTTP,
            input={"operation": "request", "method": "GET", "url": "https://api.example.com/status"},
        )
    )

    assert len(audit.events) == 1
    event_type, details = audit.events[0]
    assert event_type == AuditEventType.EGRESS_CONTACTED
    assert details["task_id"] == "task_egress"
    assert details["host"] == "api.example.com"


@pytest.mark.asyncio
async def test_http_request_rejects_non_allowlisted_host(tmp_path) -> None:
    adapter = HttpRequestAdapter(
        HttpRequestAdapterConfig(allowed_hosts=["api.example.com"]),
        SecretVaultConfig(path=str(tmp_path / "vault.json")),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="ok")),
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_http",
            tool_name="http.request",
            capability=Capability.NETWORK_HTTP,
            input={"operation": "request", "url": "https://not.example.com/me"},
        )
    )

    assert result.status == ToolResultStatus.FAILED
    assert "not allowlisted" in (result.error_message or "")


@pytest.mark.parametrize(
    "url",
    [
        # Look-alike subdomain: character sequence matches the prefix string
        # but the real, attacker-controlled host is "api.example.com.attacker.com".
        "https://api.example.com.attacker.com/steal",
        # Userinfo trick: everything before "@" looks like the allowed host
        # to a naive string-prefix check, but the real host is "attacker.com".
        "https://api.example.com@attacker.com/steal",
    ],
)
def test_require_allowed_url_rejects_prefix_lookalikes(url: str) -> None:
    config = HttpRequestAdapterConfig(allowed_url_prefixes=["https://api.example.com"])
    with pytest.raises(ValueError):
        _require_allowed_url(url, config)


def test_require_allowed_url_accepts_genuine_prefix_match() -> None:
    config = HttpRequestAdapterConfig(allowed_url_prefixes=["https://api.example.com"])
    _require_allowed_url("https://api.example.com/v1/users", config)  # must not raise
