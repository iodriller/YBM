from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_control.config import MCPConfig, MCPServerConfig
from agent_control.schemas import ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus


class MCPClientAdapter:
    """Call tools from configured external MCP servers.

    YBM's existing ``mcp_server.py`` exposes YBM to other clients. This adapter
    is the opposite direction: it lets YBM discover and call configured MCP
    servers as worker tools.
    """

    def __init__(self, config: MCPConfig) -> None:
        self.config = config

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        if not self.config.enabled:
            return _failed(request, "MCP client is disabled")
        operation = str(request.input.get("operation") or "list_tools")
        try:
            if operation in {"discover", "list_tools"}:
                output = await self._list_tools(request)
            elif operation == "health":
                output = await self._health(request)
            elif operation == "call_tool":
                output = await self._call_tool(request)
            else:
                return _failed(request, f"unsupported MCP operation: {operation}")
        except Exception as exc:
            return _failed(request, f"MCP operation failed: {exc}")

        output["operation"] = operation
        output["terminal_output"] = [_terminal_output(operation, output)]
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

    async def _list_tools(self, request: ToolCallRequest) -> dict[str, Any]:
        selected = str(request.input.get("server") or "")
        tools: list[dict[str, Any]] = []
        servers: list[dict[str, Any]] = []
        for name, server in self._servers(selected).items():
            try:
                server_tools = await _list_server_tools(name, server)
                tools.extend(server_tools)
                servers.append({"name": name, "enabled": server.enabled, "healthy": True, "tool_count": len(server_tools)})
            except Exception as exc:
                servers.append({"name": name, "enabled": server.enabled, "healthy": False, "error": str(exc)})
        return {
            "servers": servers,
            "tools": tools,
            "healthy": all(item.get("healthy") for item in servers) if servers else False,
            "summary": f"Discovered {len(tools)} MCP tool(s) across {len(servers)} server(s).",
        }

    async def _health(self, request: ToolCallRequest) -> dict[str, Any]:
        output = await self._list_tools(request)
        output["summary"] = _health_summary(output["servers"])
        return output

    async def _call_tool(self, request: ToolCallRequest) -> dict[str, Any]:
        server_name = str(request.input["server"])
        tool_name = str(request.input["tool"])
        server = self.config.servers.get(server_name)
        if server is None or not server.enabled:
            raise ValueError(f"MCP server is not configured or enabled: {server_name}")
        if tool_name in set(server.disabled_tools):
            raise ValueError(f"MCP tool is disabled by configuration: {server_name}.{tool_name}")
        result = await _call_server_tool(
            server_name,
            server,
            tool_name,
            dict(request.input.get("arguments") or {}),
        )
        return {
            "servers": [{"name": server_name, "enabled": True, "healthy": True}],
            "tools": [{"server": server_name, "name": tool_name}],
            "result": result,
            "healthy": True,
            "summary": f"Called MCP tool {server_name}.{tool_name}.",
        }

    def _servers(self, selected: str = "") -> dict[str, MCPServerConfig]:
        if selected:
            server = self.config.servers.get(selected)
            return {selected: server} if server and server.enabled else {}
        return {name: server for name, server in self.config.servers.items() if server.enabled}


async def _list_server_tools(name: str, server: MCPServerConfig) -> list[dict[str, Any]]:
    async with _session(server) as session:
        result = await session.list_tools()
        disabled = set(server.disabled_tools)
        tools = []
        for tool in result.tools:
            if tool.name in disabled:
                continue
            payload = tool.model_dump(mode="json") if hasattr(tool, "model_dump") else dict(tool)
            payload["server"] = name
            tools.append(payload)
        return tools


async def _call_server_tool(name: str, server: MCPServerConfig, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with _session(server) as session:
        result = await session.call_tool(tool_name, arguments)
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else {"result": str(result)}
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) > server.max_output_chars:
            payload = {"truncated": True, "preview": text[: server.max_output_chars]}
        payload["server"] = name
        payload["tool"] = tool_name
        return payload


class _session:
    def __init__(self, server: MCPServerConfig) -> None:
        self.server = server
        self._stdio_cm = None
        self._session_cm = None
        self.session: ClientSession | None = None

    async def __aenter__(self) -> ClientSession:
        params = StdioServerParameters(
            command=self.server.command,
            args=list(self.server.args),
            env=dict(self.server.env) or None,
            cwd=self.server.cwd,
        )
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(
            read,
            write,
            read_timeout_seconds=timedelta(seconds=self.server.timeout_seconds),
        )
        self.session = await self._session_cm.__aenter__()
        await self.session.initialize()
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session_cm is not None:
            await self._session_cm.__aexit__(exc_type, exc, tb)
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(exc_type, exc, tb)


def _health_summary(servers: list[dict[str, Any]]) -> str:
    if not servers:
        return "No enabled MCP servers are configured."
    healthy = sum(1 for item in servers if item.get("healthy"))
    return f"MCP health: {healthy}/{len(servers)} server(s) reachable."


def _terminal_output(operation: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": "local-worker",
        "terminal_id": "mcp-client",
        "content": output.get("summary") or f"MCP operation completed: {operation}",
        "is_final": True,
        "exit_code": 0,
        "source": "mcp_client",
    }


def _failed(request: ToolCallRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        request_id=request.id,
        status=ToolResultStatus.FAILED,
        error_class=ErrorClass.ADAPTER_FAILED,
        error_message=message,
    )
