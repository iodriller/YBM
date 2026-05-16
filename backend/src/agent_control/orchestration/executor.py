from __future__ import annotations

from typing import Protocol

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
    ) -> None:
        self.policy = policy
        self.repositories = repositories
        self.audit = audit
        self.adapters = adapters or {}

    async def execute(self, request: ToolCallRequest, approved: bool = False) -> ToolCallResult:
        self.repositories.tool_invocations.create(request)
        self.audit.append(
            AuditEventType.TOOL_REQUESTED,
            actor="orchestrator",
            task_id=request.task_id,
            payload=request.model_dump(mode="json"),
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
            return self._complete(request, await adapter.execute(request))
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

    def _complete(self, request: ToolCallRequest, result: ToolCallResult) -> ToolCallResult:
        self.repositories.tool_invocations.complete(result)
        self.audit.append(
            AuditEventType.TOOL_COMPLETED,
            actor="orchestrator",
            task_id=request.task_id,
            payload=result.model_dump(mode="json"),
        )
        return result
