"""docs/UI_UX_AUDIT.md Phase 14: format_audit_event's CATEGORY_BY_TYPE had
no dedicated test coverage at all before this - the timeline's category
labels are read straight from it now, so a wrong mapping here would
silently mislabel every event of that type across the console.
"""

from __future__ import annotations

from agent_control.schemas import AuditEvent, AuditEventType
from agent_control.storage.audit_view import format_audit_event


def _event(event_type: AuditEventType) -> AuditEvent:
    return AuditEvent(type=event_type, actor="worker", payload={})


def test_task_state_changed_categorizes_as_task_state() -> None:
    """The single most common event type in any task's timeline (fires on
    every status transition) - previously fell through to the generic
    "system" fallback, leaving almost every timeline row looking like
    miscellaneous noise instead of the actual state machine."""
    assert format_audit_event(_event(AuditEventType.TASK_STATE_CHANGED)).category == "task_state"


def test_task_cancelled_categorizes_as_task_state() -> None:
    assert format_audit_event(_event(AuditEventType.TASK_CANCELLED)).category == "task_state"


def test_artifact_created_categorizes_as_artifact() -> None:
    assert format_audit_event(_event(AuditEventType.ARTIFACT_CREATED)).category == "artifact"


def test_egress_contacted_categorizes_as_egress() -> None:
    assert format_audit_event(_event(AuditEventType.EGRESS_CONTACTED)).category == "egress"


def test_an_uncategorized_type_falls_back_to_system() -> None:
    """PLAN_CREATED is deliberately left uncategorized - PlanModel is dead
    (docs/HISTORY.md §1.1), nothing creates one anymore, so it isn't worth
    its own category."""
    assert format_audit_event(_event(AuditEventType.PLAN_CREATED)).category == "system"


def test_every_known_category_is_a_non_empty_string() -> None:
    """A cheap exhaustiveness guard: every AuditEventType produces some
    category, never a crash or an empty string, even ones not explicitly
    listed above."""
    for event_type in AuditEventType:
        category = format_audit_event(_event(event_type)).category
        assert isinstance(category, str) and category
