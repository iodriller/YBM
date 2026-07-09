from __future__ import annotations

import httpx
import pytest

from agent_control.config import HttpRequestAdapterConfig, SecretVaultConfig
from agent_control.schemas import Capability, ToolCallRequest, ToolResultStatus
from agent_control.storage.secrets import SecretVault
from agent_control.tools.http_request import HttpRequestAdapter


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
