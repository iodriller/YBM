"""Unit tests for the tool registry.

Focuses on the registry-side contract: per-operation schema validation,
plan validation (catches unregistered/disabled tools and invalid inputs),
and the context/vault summary strings shown to the planner.

We use ad-hoc toy ToolDefinitions instead of pulling the full
build_tool_registry — that path is covered indirectly by every other test
that builds a real worker.
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from agent_control.schemas import Capability, PlanModel, PlanStep
from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.tools.registry import ToolDefinition, ToolRegistry, build_tool_registry


class _ToyInput(BaseModel):
    operation: str
    path: str = Field(min_length=1)


class _ToyOutput(BaseModel):
    operation: str
    status: str = "ok"


def _toy_definition(*, enabled: bool = True) -> ToolDefinition:
    return ToolDefinition(
        name="toy.tool",
        capability=Capability.FILESYSTEM_READ,
        enabled=enabled,
        description="toy tool for tests",
        operations=("read",),
        operation_schemas={"read": _ToyInput},
        operation_output_schemas={"read": _ToyOutput},
        default_operation="read",
        examples=({"operation": "read", "path": "/tmp/a.txt"},),
    )


def test_validate_input_succeeds_for_valid_payload() -> None:
    definition = _toy_definition()
    out = definition.validate_input({"operation": "read", "path": "/tmp/x.txt"})
    assert out == {"operation": "read", "path": "/tmp/x.txt"}


def test_validate_input_raises_value_error_on_invalid_payload() -> None:
    definition = _toy_definition()
    with pytest.raises(ValueError) as excinfo:
        definition.validate_input({"operation": "read", "path": ""})
    assert "invalid input for toy.tool" in str(excinfo.value)


def test_validate_input_rejects_unknown_operation() -> None:
    definition = _toy_definition()
    with pytest.raises(ValueError) as excinfo:
        definition.validate_input({"operation": "delete", "path": "/x"})
    assert "unsupported operation" in str(excinfo.value)


def test_validate_input_defaults_missing_operation_to_default() -> None:
    definition = _toy_definition()
    out = definition.validate_input({"path": "/tmp/x.txt"})
    assert out["operation"] == "read"


def test_validate_output_routes_by_operation() -> None:
    definition = _toy_definition()
    out = definition.validate_output({"operation": "read"})
    # status defaults to "ok"; output schema fills it in
    assert out == {"operation": "read", "status": "ok"}


def test_registry_context_lists_enabled_and_disabled_tools() -> None:
    enabled = _toy_definition(enabled=True)
    disabled = ToolDefinition(
        name="disabled.tool",
        capability=Capability.FILESYSTEM_READ,
        enabled=False,
        description="not currently available",
    )
    registry = ToolRegistry(adapters={}, definitions=(enabled, disabled))
    ctx = registry.context()
    assert "toy.tool: enabled" in ctx
    assert "disabled.tool: disabled" in ctx
    # Examples are shown for enabled tools only.
    assert "/tmp/a.txt" in ctx


def test_registry_context_omits_examples_for_disabled_tool() -> None:
    disabled = ToolDefinition(
        name="toy.tool",
        capability=Capability.FILESYSTEM_READ,
        enabled=False,
        description="disabled",
        examples=({"operation": "read", "path": "/should-not-appear.txt"},),
    )
    registry = ToolRegistry(adapters={}, definitions=(disabled,))
    assert "/should-not-appear.txt" not in registry.context()


def test_vault_summary_marks_disabled_as_known_gap() -> None:
    disabled = ToolDefinition(
        name="missing.tool",
        capability=Capability.FILESYSTEM_READ,
        enabled=False,
        description="not wired",
    )
    summary = ToolRegistry(adapters={}, definitions=(disabled,)).vault_summary()
    assert "missing.tool: known_gap" in summary


def test_validate_plan_rejects_unregistered_tool() -> None:
    registry = ToolRegistry(adapters={}, definitions=(_toy_definition(),))
    plan = PlanModel(
        objective="o",
        steps=[
            PlanStep(
                title="t",
                description="d",
                tool_name="not.registered",
                tool_input={},
            )
        ],
        success_criteria=["done"],
    )
    with pytest.raises(ValueError) as excinfo:
        registry.validate_plan(plan)
    assert "unregistered tool" in str(excinfo.value)


def test_validate_plan_rejects_disabled_tool() -> None:
    registry = ToolRegistry(adapters={}, definitions=(_toy_definition(enabled=False),))
    plan = PlanModel(
        objective="o",
        steps=[
            PlanStep(
                title="t",
                description="d",
                tool_name="toy.tool",
                tool_input={"operation": "read", "path": "/x"},
            )
        ],
        success_criteria=["done"],
    )
    with pytest.raises(ValueError) as excinfo:
        registry.validate_plan(plan)
    assert "disabled tool" in str(excinfo.value)


def test_validate_plan_propagates_input_validation_error() -> None:
    registry = ToolRegistry(adapters={}, definitions=(_toy_definition(),))
    plan = PlanModel(
        objective="o",
        steps=[
            PlanStep(
                title="t",
                description="d",
                tool_name="toy.tool",
                tool_input={"operation": "read", "path": ""},  # empty path
            )
        ],
        success_criteria=["done"],
    )
    with pytest.raises(ValueError) as excinfo:
        registry.validate_plan(plan)
    assert "invalid input for toy.tool" in str(excinfo.value)


def test_validate_plan_inserts_capability_when_missing() -> None:
    registry = ToolRegistry(adapters={}, definitions=(_toy_definition(),))
    plan = PlanModel(
        objective="o",
        steps=[
            PlanStep(
                title="t",
                description="d",
                tool_name="toy.tool",
                tool_input={"operation": "read", "path": "/x"},
                required_capabilities=[],
            )
        ],
        success_criteria=["done"],
    )
    validated = registry.validate_plan(plan)
    # Step gets the tool's capability auto-inserted.
    assert Capability.FILESYSTEM_READ in validated.steps[0].required_capabilities
    # Plan-level required_capabilities also gets it.
    assert Capability.FILESYSTEM_READ in validated.required_capabilities


def test_validate_plan_passes_through_steps_without_tool() -> None:
    registry = ToolRegistry(adapters={}, definitions=(_toy_definition(),))
    plan = PlanModel(
        objective="o",
        steps=[PlanStep(title="t", description="d", tool_name=None)],
        success_criteria=["done"],
    )
    # Plan-only steps don't get registry-validated and don't raise.
    validated = registry.validate_plan(plan)
    assert validated.steps[0].tool_name is None


def test_build_registry_exposes_http_request_only_with_allowlist() -> None:
    settings = AppSettings(
        _env_file=None,
        capabilities={Capability.NETWORK_HTTP: CapabilityPolicy(enabled=True, requires_approval=False)},
        adapters={"http_request": {"enabled": True, "allowed_hosts": ["api.example.com"]}},
    )

    registry = build_tool_registry(settings, "http://127.0.0.1:8765")
    definitions = {definition.name: definition for definition in registry.definitions}

    assert definitions["http.request"].enabled is True
    assert "http.request" in registry.adapters
    assert "api.example.com" in registry.context()
