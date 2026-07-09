from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from agent_control.policy import PolicyEngine
from agent_control.schemas import (
    AuditEventType,
    ErrorClass,
    ToolCallRequest,
    ToolCallResult,
    ToolResultStatus,
)
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories


class ToolAdapter(Protocol):
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        ...


class StaticToolAdapter:
    def __init__(self, output: dict | None = None) -> None:
        self.output = output or {"ok": True}
        self.requests: list[ToolCallRequest] = []

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        self.requests.append(request)
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.SUCCEEDED,
            output=self.output,
        )


class ToolExecutor:
    def __init__(
        self,
        policy: PolicyEngine,
        repositories: Repositories,
        audit: AuditLogger,
        adapters: dict[str, ToolAdapter] | None = None,
        tool_definitions: Iterable[Any] | None = None,
    ) -> None:
        self.policy = policy
        self.repositories = repositories
        self.audit = audit
        self.adapters = adapters or {}
        if isinstance(tool_definitions, dict):
            self.tool_definitions = tool_definitions
        else:
            self.tool_definitions = {definition.name: definition for definition in (tool_definitions or [])}

    async def execute(self, request: ToolCallRequest, approved: bool = False) -> ToolCallResult:
        request, validation_error = self._validated_request(request)
        self.repositories.tool_invocations.create(request)
        self.audit.append(
            AuditEventType.TOOL_REQUESTED,
            actor="orchestrator",
            task_id=request.task_id,
            payload=request.model_dump(mode="json"),
        )

        if validation_error:
            return self._complete(
                request,
                ToolCallResult(
                    request_id=request.id,
                    status=ToolResultStatus.FAILED,
                    error_class=ErrorClass.VALIDATION_FAILED,
                    error_message=validation_error,
                ),
            )

        decision = self.policy.evaluate(request, approved=approved)
        if decision.needs_approval:
            approval = self.policy.approval_request(request)
            self.repositories.approvals.create(approval)
            self.audit.append(
                AuditEventType.APPROVAL_REQUESTED,
                actor="policy",
                task_id=request.task_id,
                payload={"approval_id": approval.id, "tool_request_id": request.id},
            )
            return self._complete(
                request,
                ToolCallResult(
                    request_id=request.id,
                    status=ToolResultStatus.NEEDS_APPROVAL,
                    output={"approval_id": approval.id},
                )
            )

        if not decision.allowed:
            return self._complete(
                request,
                ToolCallResult(
                    request_id=request.id,
                    status=ToolResultStatus.DENIED,
                    error_class=ErrorClass.POLICY_DENIED,
                    error_message=decision.reason,
                )
            )

        adapter = self.adapters.get(request.tool_name)
        if adapter is None:
            return self._complete(
                request,
                ToolCallResult(
                    request_id=request.id,
                    status=ToolResultStatus.FAILED,
                    error_class=ErrorClass.ADAPTER_FAILED,
                    error_message=f"tool adapter not registered: {request.tool_name}",
                )
            )

        try:
            result = await adapter.execute(request)
            result, output_validation_error = self._validated_result(request, result)
            if output_validation_error:
                return self._complete(
                    request,
                    ToolCallResult(
                        request_id=request.id,
                        status=ToolResultStatus.FAILED,
                        output={"invalid_output": result.output},
                        error_class=ErrorClass.VALIDATION_FAILED,
                        error_message=output_validation_error,
                    ),
                )
            return self._complete(request, result)
        except Exception as exc:
            return self._complete(
                request,
                ToolCallResult(
                    request_id=request.id,
                    status=ToolResultStatus.FAILED,
                    error_class=ErrorClass.ADAPTER_FAILED,
                    error_message=str(exc),
                )
            )

    def _validated_request(self, request: ToolCallRequest) -> tuple[ToolCallRequest, str | None]:
        definition = self.tool_definitions.get(request.tool_name)
        if definition is None:
            return request, None
        try:
            validated_input = definition.validate_input(request.input)
        except ValueError as exc:
            return request, str(exc)
        return (
            request.model_copy(
                update={
                    "input": validated_input,
                    "scope_target": request.scope_target or validated_input.get("scope_target"),
                    "timeout_seconds": int(validated_input.get("timeout_seconds") or request.timeout_seconds),
                }
            ),
            None,
        )

    def _validated_result(
        self,
        request: ToolCallRequest,
        result: ToolCallResult,
    ) -> tuple[ToolCallResult, str | None]:
        if result.status != ToolResultStatus.SUCCEEDED:
            return result, None
        definition = self.tool_definitions.get(request.tool_name)
        if definition is None:
            return result, None
        output = dict(result.output or {})
        if "operation" not in output and request.input.get("operation"):
            output["operation"] = request.input["operation"]
        try:
            validated_output = definition.validate_output(output)
        except ValueError as exc:
            return result, str(exc)
        return result.model_copy(update={"output": validated_output}), None

    def _complete(self, request: ToolCallRequest, result: ToolCallResult) -> ToolCallResult:
        self.repositories.tool_invocations.complete(result)
        self.audit.append(
            AuditEventType.TOOL_COMPLETED,
            actor="orchestrator",
            task_id=request.task_id,
            payload=result.model_dump(mode="json"),
        )
        return result
