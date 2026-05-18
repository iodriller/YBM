from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from agent_control.config import AppSettings
from agent_control.schemas import Capability
from agent_control.tools.adapter_factory import AdapterFactoryAdapter
from agent_control.tools.coding_assistant import GenericTerminalAgentAdapter
from agent_control.tools.contracts import (
    AdapterFactoryAssessInput,
    AdapterFactoryScaffoldInput,
    CodingAssistantInput,
    VSCodeCopilotTerminalInput,
    VSCodeTerminalCommandInput,
    WorkspaceLaunchStaticInput,
    WorkspaceMaterializeStaticAppInput,
    WorkspacePrepareInput,
    WorkspaceWebAppPreviewInput,
    WorkspaceWriteFilesInput,
)
from agent_control.tools.local_workspace import LocalWorkspaceAdapter
from agent_control.tools.vscode_bridge import VSCodeBridgeTerminalAdapter


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    capability: Capability
    enabled: bool
    description: str
    operations: tuple[str, ...] = ()
    lifecycle: str = "runtime"
    input_schema: type[BaseModel] | None = None
    operation_schemas: dict[str, type[BaseModel]] | None = None
    default_operation: str | None = None

    def validate_input(self, value: dict) -> dict:
        schema = self.input_schema
        payload = dict(value or {})
        if self.operation_schemas:
            operation = str(payload.get("operation") or self.default_operation or "")
            schema = self.operation_schemas.get(operation)
            if schema is None:
                expected = ", ".join(sorted(self.operation_schemas))
                raise ValueError(f"unsupported operation for {self.name}: {operation or '<missing>'}; expected one of: {expected}")
            payload["operation"] = operation
        if schema is None:
            return payload
        try:
            return schema.model_validate(payload).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            raise ValueError(f"invalid input for {self.name}: {exc}") from exc


@dataclass(frozen=True)
class ToolRegistry:
    adapters: dict[str, object]
    definitions: tuple[ToolDefinition, ...]

    def context(self) -> str:
        lines = ["Available worker tools:"]
        for definition in self.definitions:
            status = "enabled" if definition.enabled else "disabled"
            operations = f" operations={','.join(definition.operations)}" if definition.operations else ""
            lines.append(
                f"- {definition.name}: {status}; capability={definition.capability.value}; "
                f"lifecycle={definition.lifecycle}; {definition.description}{operations}"
            )
        return "\n".join(lines)

    def vault_summary(self) -> str:
        lines = ["Capability vault:"]
        for definition in self.definitions:
            state = "available" if definition.enabled else "known_gap"
            lines.append(f"- {definition.name}: {state}; {definition.description}")
        return "\n".join(lines)


def build_tool_registry(settings: AppSettings, backend_base_url: str) -> ToolRegistry:
    adapters: dict[str, object] = {}
    definitions: list[ToolDefinition] = []

    workspace_enabled = _capability_enabled(settings, Capability.FILESYSTEM_WRITE) and settings.adapters.workspace.enabled
    definitions.append(
        ToolDefinition(
            name="workspace.manage",
            capability=Capability.FILESYSTEM_WRITE,
            enabled=workspace_enabled,
            description=f"manage task workspaces under {settings.adapters.workspace.root_dir}",
            operations=("prepare", "write_files", "materialize_static_app", "launch_static", "web_app_preview"),
            operation_schemas={
                "prepare": WorkspacePrepareInput,
                "write_files": WorkspaceWriteFilesInput,
                "materialize_static_app": WorkspaceMaterializeStaticAppInput,
                "launch_static": WorkspaceLaunchStaticInput,
                "web_app_preview": WorkspaceWebAppPreviewInput,
            },
            default_operation="prepare",
        )
    )
    if settings.adapters.workspace.enabled:
        workspace = LocalWorkspaceAdapter(settings.adapters.workspace)
        adapters["workspace.manage"] = workspace
        adapters["workspace.web_app"] = workspace

    factory_enabled = _capability_enabled(settings, Capability.FILESYSTEM_WRITE) and settings.adapters.adapter_factory.enabled
    definitions.append(
        ToolDefinition(
            name="adapter.factory",
            capability=Capability.FILESYSTEM_WRITE,
            enabled=factory_enabled,
            description=f"scaffold generated adapter proposals under {settings.adapters.adapter_factory.root_dir}",
            operations=("assess", "scaffold"),
            lifecycle="scaffold",
            operation_schemas={
                "assess": AdapterFactoryAssessInput,
                "scaffold": AdapterFactoryScaffoldInput,
            },
            default_operation="scaffold",
        )
    )
    if settings.adapters.adapter_factory.enabled:
        adapters["adapter.factory"] = AdapterFactoryAdapter(settings.adapters.adapter_factory)

    vscode_enabled = _capability_enabled(settings, Capability.VSCODE_WRITE_FILES) and settings.adapters.vscode.enabled
    definitions.append(
        ToolDefinition(
            name="vscode.copilot_terminal",
            capability=Capability.VSCODE_WRITE_FILES,
            enabled=vscode_enabled,
            description="send a prompt to VS Code/Copilot terminal or local Copilot CLI fallback",
            input_schema=VSCodeCopilotTerminalInput,
        )
    )
    definitions.append(
        ToolDefinition(
            name="vscode.terminal_command",
            capability=Capability.VSCODE_WRITE_FILES,
            enabled=vscode_enabled,
            description="queue an explicit terminal command through the VS Code bridge",
            input_schema=VSCodeTerminalCommandInput,
        )
    )
    if settings.adapters.vscode.enabled:
        vscode = VSCodeBridgeTerminalAdapter(settings.adapters.vscode, backend_base_url)
        adapters["vscode.terminal_command"] = vscode
        adapters["vscode.copilot_terminal"] = vscode

    coding_enabled = _capability_enabled(settings, Capability.TERMINAL_RUN) and settings.adapters.coding_assistant.enabled
    definitions.append(
        ToolDefinition(
            name="coding_assistant",
            capability=Capability.TERMINAL_RUN,
            enabled=coding_enabled,
            description="run the configured local coding assistant command template",
            input_schema=CodingAssistantInput,
        )
    )
    if settings.adapters.coding_assistant.enabled:
        adapters["coding_assistant"] = GenericTerminalAgentAdapter(settings.adapters.coding_assistant)

    definitions.extend(
        [
            ToolDefinition(
                name="desktop.screenshot",
                capability=Capability.DESKTOP_SCREENSHOT,
                enabled=_capability_enabled(settings, Capability.DESKTOP_SCREENSHOT)
                and settings.adapters.desktop.screenshot_enabled,
                description="capture a desktop screenshot through the Telegram command path",
            ),
            ToolDefinition(
                name="browser.open",
                capability=Capability.BROWSER_OPEN,
                enabled=False,
                description="not implemented yet; capability exists but no browser adapter is registered",
            ),
            ToolDefinition(
                name="browser.control",
                capability=Capability.BROWSER_CONTROL,
                enabled=False,
                description="not implemented yet; capability exists but no browser control adapter is registered",
            ),
        ]
    )

    return ToolRegistry(adapters=adapters, definitions=tuple(definitions))


def _capability_enabled(settings: AppSettings, capability: Capability) -> bool:
    policy = settings.capabilities.get(capability)
    return bool(policy and policy.enabled)
