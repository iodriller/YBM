from __future__ import annotations

from datetime import timedelta
import json

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.schemas import (
    ApprovalRequest,
    AuditEventType,
    Capability,
    RiskLevel,
    StrictBaseModel,
    ToolCallRequest,
    utc_now,
)
from agent_control.storage.audit import AuditLogger


RISK_ORDER = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class PolicyDecision(StrictBaseModel):
    allowed: bool
    needs_approval: bool = False
    reason: str
    capability: Capability
    risk_level: RiskLevel


class PolicyEngine:
    def __init__(self, settings: AppSettings, audit: AuditLogger | None = None) -> None:
        self.settings = settings
        self.audit = audit

    def evaluate(self, request: ToolCallRequest, approved: bool = False) -> PolicyDecision:
        policy = self.settings.capabilities.get(request.capability)
        if policy is None or not policy.enabled:
            return self._decision(False, False, "capability_disabled", request)

        if RISK_ORDER[request.risk_level] > RISK_ORDER[policy.max_risk_level]:
            return self._decision(False, False, "risk_exceeds_capability_policy", request)

        if not self._scope_allowed(request, policy):
            return self._decision(False, False, "scope_not_allowed", request)

        if not self._patterns_allowed(request, policy):
            return self._decision(False, False, "pattern_denied", request)

        if not approved and self._requires_approval(request, policy):
            return self._decision(False, True, "approval_required", request)

        return self._decision(True, False, "allowed", request)

    def approval_request(self, request: ToolCallRequest, summary: str | None = None) -> ApprovalRequest:
        return ApprovalRequest(
            task_id=request.task_id,
            capability=request.capability,
            risk_level=request.risk_level,
            summary=summary or f"Approve {request.tool_name} using {request.capability.value}",
            action_payload=request.model_dump(mode="json"),
            expires_at=utc_now() + timedelta(seconds=self.settings.approval_policy.default_timeout_seconds),
        )

    def _requires_approval(self, request: ToolCallRequest, policy: CapabilityPolicy) -> bool:
        if not request.requires_approval and not policy.requires_approval:
            return False
        return (
            request.requires_approval
            or policy.requires_approval
            or RISK_ORDER[request.risk_level] >= RISK_ORDER[self.settings.approval_policy.require_approval_at_or_above]
        )

    @staticmethod
    def _scope_allowed(request: ToolCallRequest, policy: CapabilityPolicy) -> bool:
        if not policy.scopes:
            return True
        if not request.scope_target:
            return False
        normalized = request.scope_target.replace("\\", "/").lower()
        return any(PolicyEngine._matches_scope(normalized, scope) for scope in policy.scopes)

    @staticmethod
    def _matches_scope(normalized_target: str, scope: str) -> bool:
        raw_scope = scope.replace("\\", "/").lower()
        if raw_scope == "/":
            return normalized_target.startswith("/")
        normalized_scope = raw_scope.rstrip("/")
        if not normalized_scope:
            return False
        return normalized_target == normalized_scope or normalized_target.startswith(f"{normalized_scope}/")

    @staticmethod
    def _patterns_allowed(request: ToolCallRequest, policy: CapabilityPolicy) -> bool:
        haystack = json.dumps(
            {"scope_target": request.scope_target, "input": request.input},
            default=str,
            sort_keys=True,
        ).lower()
        if any(pattern.lower() in haystack for pattern in policy.deny_patterns):
            return False
        if policy.allow_patterns and not any(pattern.lower() in haystack for pattern in policy.allow_patterns):
            return False
        return True

    def _decision(
        self,
        allowed: bool,
        needs_approval: bool,
        reason: str,
        request: ToolCallRequest,
    ) -> PolicyDecision:
        decision = PolicyDecision(
            allowed=allowed,
            needs_approval=needs_approval,
            reason=reason,
            capability=request.capability,
            risk_level=request.risk_level,
        )
        if self.audit:
            self.audit.append(
                AuditEventType.POLICY_DECISION,
                actor="policy",
                task_id=request.task_id,
                payload={
                    "tool_request_id": request.id,
                    "allowed": allowed,
                    "needs_approval": needs_approval,
                    "reason": reason,
                    "capability": request.capability.value,
                    "risk_level": request.risk_level.value,
                },
            )
        return decision
