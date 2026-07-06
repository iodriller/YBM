from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, MCPConfig, MCPServerConfig
from agent_control.schemas import Capability, RiskLevel, ToolCallRequest, ToolResultStatus
from agent_control.tools.mcp_client import MCPClientAdapter
from agent_control.tools.registry import build_tool_registry


def test_mcp_config_and_registry_expose_client_when_enabled(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        capabilities={Capability.TERMINAL_RUN: CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)},
        mcp={
            "enabled": True,
            "servers": {
                "fake": {
                    "command": sys.executable,
                    "args": ["fake_server.py"],
                }
            },
        },
    )

    registry = build_tool_registry(settings, "http://127.0.0.1:8765")
    definitions = {definition.name: definition for definition in registry.definitions}

    assert definitions["mcp.client"].enabled is True
    assert "mcp.client" in registry.adapters
    assert "call_tool" in definitions["mcp.client"].operations


@pytest.mark.asyncio
async def test_mcp_client_discovers_and_calls_fake_stdio_server(tmp_path) -> None:
    server_path = _fake_mcp_server(tmp_path)
    adapter = MCPClientAdapter(
        MCPConfig(
            enabled=True,
            servers={
                "fake": MCPServerConfig(
                    command=sys.executable,
                    args=[str(server_path)],
                    timeout_seconds=10,
                )
            },
        )
    )

    listed = await adapter.execute(
        ToolCallRequest(
            task_id="task_mcp",
            tool_name="mcp.client",
            capability=Capability.TERMINAL_RUN,
            input={"operation": "list_tools"},
        )
    )
    called = await adapter.execute(
        ToolCallRequest(
            task_id="task_mcp",
            tool_name="mcp.client",
            capability=Capability.TERMINAL_RUN,
            input={"operation": "call_tool", "server": "fake", "tool": "echo", "arguments": {"text": "hello"}},
        )
    )

    assert listed.status == ToolResultStatus.SUCCEEDED
    assert any(tool["name"] == "echo" for tool in listed.output["tools"])
    assert called.status == ToolResultStatus.SUCCEEDED
    assert "hello" in str(called.output["result"])


def test_mcp_client_rejects_disabled_tool() -> None:
    adapter = MCPClientAdapter(
        MCPConfig(
            enabled=True,
            servers={
                "fake": MCPServerConfig(
                    command=sys.executable,
                    args=["server.py"],
                    disabled_tools=["echo"],
                )
            },
        )
    )

    # The adapter catches the disabled-tool error and returns a failed tool result.
    import asyncio

    result = asyncio.run(
        adapter.execute(
            ToolCallRequest(
                task_id="task_mcp",
                tool_name="mcp.client",
                capability=Capability.TERMINAL_RUN,
                input={"operation": "call_tool", "server": "fake", "tool": "echo", "arguments": {}},
            )
        )
    )

    assert result.status == ToolResultStatus.FAILED
    assert "disabled" in (result.error_message or "").lower()


def _fake_mcp_server(tmp_path: Path) -> Path:
    server_path = tmp_path / "fake_mcp_server.py"
    server_path.write_text(
        """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake")

@mcp.tool()
def echo(text: str) -> str:
    return text

if __name__ == "__main__":
    mcp.run()
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return server_path
