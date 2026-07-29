"""Shared types for tool registration.

Lives separately from registry.py so each tool's own module (code_interpreter.py,
filesystem_manage.py, ...) can import ToolDefinition/RegistryDeps and declare its
own register() function without importing registry.py itself - registry.py is
the one importing *them*, and a two-way import would be a cycle. New tool =
new adapter module with a register() function, one import line added to
registry.py's _REGISTRARS - no editing this file or any other tool's code
(docs/HISTORY.md P3).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from agent_control.config import AppSettings
from agent_control.schemas import Capability


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
    output_schema: type[BaseModel] | None = None
    operation_output_schemas: dict[str, type[BaseModel]] | None = None
    default_operation: str | None = None
    # Worked usage examples shown to the Operator's decide() call. Each entry
    # is a `tool_input` dict the model can imitate - the 8B local model
    # imitates concrete examples much more reliably than it follows abstract
    # descriptions.
    examples: tuple[dict, ...] = ()

    def validate_input(self, value: dict) -> dict:
        return self._validate_schema(value, self.input_schema, self.operation_schemas, "input")

    def validate_output(self, value: dict) -> dict:
        return self._validate_schema(value, self.output_schema, self.operation_output_schemas, "output")

    def _validate_schema(
        self,
        value: dict,
        base_schema: type[BaseModel] | None,
        operation_schemas: dict[str, type[BaseModel]] | None,
        kind: str,
    ) -> dict:
        schema = base_schema
        payload = dict(value or {})
        if operation_schemas:
            operation = str(payload.get("operation") or self.default_operation or "")
            schema = operation_schemas.get(operation)
            if schema is None:
                expected = ", ".join(sorted(operation_schemas))
                raise ValueError(
                    f"unsupported operation for {self.name}: {operation or '<missing>'}; "
                    f"expected one of: {expected}"
                )
            payload["operation"] = operation
        if schema is None:
            return payload
        try:
            return schema.model_validate(payload).model_dump(mode="json", exclude_none=True, by_alias=True)
        except ValidationError as exc:
            raise ValueError(f"invalid {kind} for {self.name}: {exc}") from exc


@dataclass
class ToolRegistry:
    adapters: dict[str, object]
    definitions: tuple[ToolDefinition, ...]
    definition_index: dict[str, ToolDefinition] | None = None
    mcp_summary: str = ""
    mcp_summary_factory: Callable[[], str] | None = None

    def __post_init__(self) -> None:
        if self.definition_index is None:
            self.definition_index = {definition.name: definition for definition in self.definitions}

    def register_dynamic_tool(self, definition: ToolDefinition, adapter: object) -> None:
        assert self.definition_index is not None
        if definition.name in self.definition_index:
            raise ValueError(f"tool already registered: {definition.name}")
        self.adapters[definition.name] = adapter
        self.definition_index[definition.name] = definition
        self.definitions = (*self.definitions, definition)

    def context(self) -> str:
        lines = ["Available worker tools:"]
        for definition in self.definitions:
            status = "enabled" if definition.enabled else "disabled"
            operations = f" operations={','.join(definition.operations)}" if definition.operations else ""
            lines.append(
                f"- {definition.name}: {status}; capability={definition.capability.value}; "
                f"lifecycle={definition.lifecycle}; {definition.description}{operations}"
            )
            if definition.enabled and definition.examples:
                # Show worked examples inline — the model imitates these
                # better than abstract descriptions of input shape.
                for ex in definition.examples:
                    lines.append(f"    example tool_input: {json.dumps(ex, ensure_ascii=False)}")
        mcp_summary = self.mcp_summary_factory() if self.mcp_summary_factory is not None else self.mcp_summary
        if mcp_summary:
            lines.append("")
            lines.append(mcp_summary)
        return "\n".join(lines)

    def vault_summary(self) -> str:
        lines = ["Capability vault:"]
        for definition in self.definitions:
            state = "available" if definition.enabled else "known_gap"
            lines.append(f"- {definition.name}: {state}; {definition.description}")
        return "\n".join(lines)


@dataclass(frozen=True)
class RegistryDeps:
    """Bundle of optional dependencies the per-tool registrars consume."""
    settings: AppSettings
    backend_base_url: str
    provider: object | None = None
    should_continue: Callable[[str], bool] | None = None
    artifact_repository: object | None = None
    task_repository: object | None = None
    repositories: object | None = None
    audit_logger: object | None = None
    telegram_client: object | None = None


Definitions = list[ToolDefinition]
Adapters = dict[str, object]
Registrar = Callable[[RegistryDeps, Definitions, Adapters], None]


def capability_enabled(settings: AppSettings, capability: Capability) -> bool:
    policy = settings.capabilities.get(capability)
    return bool(policy and policy.enabled)


def same_output_schema(operations: tuple[str, ...], schema: type[BaseModel]) -> dict[str, type[BaseModel]]:
    return {operation: schema for operation in operations}
