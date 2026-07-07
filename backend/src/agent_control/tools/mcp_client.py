from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_control.config import MCPConfig, MCPServerConfig
from agent_control.schemas import ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus, utc_now


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
        catalog_entries: list[dict[str, Any]] = []
        servers: list[dict[str, Any]] = []
        for name, server in self._servers(selected).items():
            try:
                server_tools = await _list_server_tools(name, server)
                catalog_entries.extend(server_tools)
                tools.extend([tool for tool in server_tools if not tool.get("disabled")])
                servers.append(
                    {"name": name, "enabled": server.enabled, "healthy": True, "tool_count": len(server_tools)}
                )
            except Exception as exc:
                servers.append({"name": name, "enabled": server.enabled, "healthy": False, "error": str(exc)})
        catalog = write_mcp_catalog(self.config, servers=servers, tools=catalog_entries, replace_all=not bool(selected))
        return {
            "servers": servers,
            "tools": tools,
            "healthy": all(item.get("healthy") for item in servers) if servers else False,
            "summary": f"Discovered {len(tools)} MCP tool(s) across {len(servers)} server(s).",
            "catalog_path": catalog["catalog_path"],
            "catalog_updated_at": catalog["catalog_updated_at"],
            "catalog_entries": catalog_entries,
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
        catalog = load_mcp_catalog(self.config)
        return {
            "servers": [{"name": server_name, "enabled": True, "healthy": True}],
            "tools": [{"server": server_name, "name": tool_name}],
            "result": result,
            "healthy": True,
            "summary": f"Called MCP tool {server_name}.{tool_name}.",
            "selected_tool": {"server": server_name, "tool": tool_name},
            "catalog_path": str(_catalog_path(self.config)),
            "catalog_updated_at": catalog.get("updated_at"),
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
            payload = tool.model_dump(mode="json") if hasattr(tool, "model_dump") else dict(tool)
            tool_name = str(payload.get("name") or getattr(tool, "name", ""))
            payload["server"] = name
            payload["tool"] = tool_name
            payload["disabled"] = tool_name in disabled
            payload["capability"] = server.capability.value
            payload["risk_level"] = server.risk_level.value
            payload["healthy"] = True
            payload["last_seen"] = utc_now().isoformat()
            payload["last_error"] = None
            payload["input_schema"] = payload.get("inputSchema") or payload.get("input_schema") or {}
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


def _catalog_path(config: MCPConfig) -> Path:
    return Path(config.catalog_path).expanduser()


def write_mcp_catalog(
    config: MCPConfig,
    *,
    servers: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    replace_all: bool = True,
) -> dict[str, Any]:
    updated_at = utc_now().isoformat()
    path = _catalog_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    if replace_all:
        catalog_servers = servers
        catalog_tools = tools
    else:
        existing = load_mcp_catalog(config)
        refreshed_servers = {str(server.get("name")) for server in servers if server.get("name")}
        catalog_servers = [
            server
            for server in existing.get("servers", [])
            if isinstance(server, dict) and str(server.get("name")) not in refreshed_servers
        ]
        catalog_servers.extend(servers)
        catalog_tools = [
            tool
            for tool in existing.get("tools", [])
            if isinstance(tool, dict) and str(tool.get("server")) not in refreshed_servers
        ]
        catalog_tools.extend(tools)
    payload = {
        "updated_at": updated_at,
        "servers": catalog_servers,
        "tools": catalog_tools,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return {"catalog_path": str(path), "catalog_updated_at": updated_at}


def load_mcp_catalog(config: MCPConfig) -> dict[str, Any]:
    path = _catalog_path(config)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def mcp_catalog_summary(config: MCPConfig, *, max_tools: int = 20) -> str:
    if not config.enabled:
        return ""
    catalog = load_mcp_catalog(config)
    tools = [tool for tool in catalog.get("tools", []) if isinstance(tool, dict) and not tool.get("disabled")]
    servers = [server for server in catalog.get("servers", []) if isinstance(server, dict)]
    if not catalog:
        if not config.servers:
            return "Configured MCP tools: MCP is enabled but no servers are configured."
        return "Configured MCP tools: catalog not built yet. Use mcp.client list_tools before choosing an MCP tool."
    if not tools:
        server_bits = ", ".join(
            f"{server.get('name')}({'healthy' if server.get('healthy') else 'unreachable'})"
            for server in servers[:8]
        )
        return f"Configured MCP tools: none currently available. Servers: {server_bits or 'none'}."
    lines = [f"Configured MCP tools from {config.catalog_path}:"]
    for tool in tools[:max_tools]:
        name = tool.get("name") or tool.get("tool")
        server = tool.get("server")
        description = str(tool.get("description") or "").strip()
        capability = tool.get("capability") or "terminal.run"
        risk_level = tool.get("risk_level") or "high"
        desc = f": {description[:160]}" if description else ""
        lines.append(f"- {server}.{name}{desc}; capability={capability}; risk={risk_level}")
    if len(tools) > max_tools:
        lines.append(f"- ... {len(tools) - max_tools} more MCP tool(s) omitted")
    return "\n".join(lines)


def mcp_output_text(output: dict[str, Any]) -> str:
    if output.get("result") is not None:
        return json.dumps(output["result"], ensure_ascii=False, indent=2, default=str)
    lines: list[str] = []
    if output.get("summary"):
        lines.append(str(output["summary"]))
    servers = output.get("servers")
    if isinstance(servers, list) and servers:
        lines.append("Servers:")
        for server in servers[:20]:
            if not isinstance(server, dict):
                continue
            state = "healthy" if server.get("healthy") else "unreachable"
            suffix = f" ({server.get('error')})" if server.get("error") else ""
            lines.append(f"- {server.get('name')}: {state}{suffix}")
    tools = output.get("tools")
    if isinstance(tools, list) and tools:
        lines.append("Tools:")
        for tool in tools[:50]:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name") or tool.get("tool")
            desc = str(tool.get("description") or "").strip()
            suffix = f": {desc[:180]}" if desc else ""
            lines.append(f"- {tool.get('server')}.{name}{suffix}")
    return "\n".join(lines)


def _terminal_output(operation: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": "local-worker",
        "terminal_id": "mcp-client",
        "content": mcp_output_text(output) or output.get("summary") or f"MCP operation completed: {operation}",
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
