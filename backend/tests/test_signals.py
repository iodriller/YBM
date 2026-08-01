"""Cancellation must clean up after itself (docs/UI_UX_AUDIT.md Phase 8) -
previously only the task's own status flipped to cancelled; a pending
approval stayed pending forever, a task-scoped grant stayed valid until its
own unrelated expiry, and a stuck-at-needs_approval tool invocation stayed
stuck. None of that is cosmetic: a stale pending approval keeps showing up
in the console as if it still needs a decision, on a task that is already
dead.
"""

from __future__ import annotations

import json
from datetime import timedelta

from agent_control.config import AppSettings
from agent_control.orchestration.signals import apply_task_signal
from agent_control.schemas import (
    ApprovalGrant,
    ApprovalRequest,
    ApprovalStatus,
    Capability,
    RiskLevel,
    TaskStatus,
    ToolCallRequest,
    ToolResultStatus,
    utc_now,
)
from helpers import make_repos


def _pending_approval(repositories, task_id: str) -> ApprovalRequest:
    return repositories.approvals.create(
        ApprovalRequest(
            task_id=task_id,
            capability=Capability.FILESYSTEM_WRITE,
            risk_level=RiskLevel.HIGH,
            summary="write a file",
            expires_at=utc_now() + timedelta(minutes=15),
        )
    )


def test_cancelling_a_task_rejects_its_pending_approval(tmp_path) -> None:
    repositories, audit = make_repos(tmp_path)
    task = repositories.tasks.create("write a report")
    approval = _pending_approval(repositories, task.id)

    apply_task_signal(repositories, audit, task.id, "cancel", "operator", {})

    updated = repositories.approvals.get(approval.id)
    assert updated is not None
    assert updated.status == ApprovalStatus.CANCELLED
    # And it must actually disappear from what the console calls "pending" -
    # the whole point is that it stops looking actionable.
    assert approval.id not in {a.id for a in repositories.approvals.list_pending()}


def test_cancelling_a_task_does_not_touch_another_tasks_pending_approval(tmp_path) -> None:
    repositories, audit = make_repos(tmp_path)
    cancelled_task = repositories.tasks.create("cancel me")
    other_task = repositories.tasks.create("leave me alone")
    _pending_approval(repositories, cancelled_task.id)
    other_approval = _pending_approval(repositories, other_task.id)

    apply_task_signal(repositories, audit, cancelled_task.id, "cancel", "operator", {})

    still_pending = repositories.approvals.get(other_approval.id)
    assert still_pending is not None
    assert still_pending.status == ApprovalStatus.PENDING


def test_cancelling_a_task_revokes_its_approval_grants(tmp_path) -> None:
    repositories, audit = make_repos(tmp_path)
    task = repositories.tasks.create("write a report")
    grant = repositories.approval_grants.create(
        ApprovalGrant(
            task_id=task.id,
            tool_name="filesystem.manage",
            capability=Capability.FILESYSTEM_WRITE,
            granted_from_approval_id="approval_x",
            expires_at=utc_now() + timedelta(minutes=30),
        )
    )

    apply_task_signal(repositories, audit, task.id, "cancel", "operator", {})

    assert repositories.approval_grants.find_matching(task.id, "filesystem.manage", Capability.FILESYSTEM_WRITE) is None
    # The row survives (audit history, not deleted) - only its validity window closes.
    remaining = repositories.approval_grants.list_for_task(task.id)
    assert len(remaining) == 1
    assert remaining[0].id == grant.id
    assert remaining[0].expires_at <= utc_now()


def test_cancelling_a_task_cancels_stuck_tool_invocations(tmp_path) -> None:
    repositories, audit = make_repos(tmp_path)
    task = repositories.tasks.create("write a report")
    request = ToolCallRequest(
        task_id=task.id, tool_name="filesystem.manage", capability=Capability.FILESYSTEM_WRITE,
        input={"operation": "write_text_file"},
    )
    repositories.tool_invocations.create(request, status=ToolResultStatus.NEEDS_APPROVAL)

    apply_task_signal(repositories, audit, task.id, "cancel", "operator", {})

    invocations = repositories.tool_invocations.list_for_task(task.id)
    assert len(invocations) == 1
    assert invocations[0]["status"] == "cancelled"
    assert invocations[0]["result"]["error_message"]


def test_cancelling_a_task_with_nothing_pending_is_a_clean_no_op(tmp_path) -> None:
    """No approval, no grant, no stuck invocation - cancel must not raise
    just because there was nothing to clean up."""
    repositories, audit = make_repos(tmp_path)
    task = repositories.tasks.create("nothing pending here")

    signal, old_status, new_status = apply_task_signal(repositories, audit, task.id, "cancel", "operator", {})

    assert new_status == TaskStatus.CANCELLED
    assert signal.signal == "cancel"


def test_cancelling_a_task_awaiting_a_coding_session_stops_the_process(tmp_path) -> None:
    """AWAITING_EXTERNAL is the one status the worker doesn't busy-poll - it
    only resumes via the session watcher noticing completion. A cancel while
    a session is genuinely running must stop that process itself, not just
    leave it running unsupervised in the background.
    """
    repositories, audit = make_repos(tmp_path)
    session_root = tmp_path / "coding_sessions"
    session_root.mkdir()
    (session_root / "sess_abc.json").write_text(json.dumps({"session_id": "sess_abc", "pid": 999999}), encoding="utf-8")
    settings = AppSettings(_env_file=None, adapters={"coding_agent": {"session_root": str(session_root)}})
    task = repositories.tasks.create("run some coding session")
    repositories.tasks.update_metadata(
        task.id,
        {"awaiting_external": {"tool_name": "coding.agent", "session_id": "sess_abc"}},
        TaskStatus.AWAITING_EXTERNAL,
    )
    task = repositories.tasks.get(task.id)

    apply_task_signal(repositories, audit, task.id, "cancel", "operator", {}, settings=settings)

    events = repositories.audit.list_for_task(task.id)
    stop_events = [e for e in events if e.payload.get("action") == "cancellation_stop_external_session"]
    assert len(stop_events) == 1
    assert stop_events[0].payload["session_id"] == "sess_abc"
    assert stop_events[0].payload["stopped"] is True


def test_cancelling_a_task_with_no_settings_skips_the_session_stop_without_raising(tmp_path) -> None:
    """admin.py and telegram.py both pass settings; a hypothetical caller
    that doesn't must not crash - it just can't stop a live session."""
    repositories, audit = make_repos(tmp_path)
    task = repositories.tasks.create("run some coding session")
    repositories.tasks.update_metadata(
        task.id,
        {"awaiting_external": {"tool_name": "coding.agent", "session_id": "sess_abc"}},
        TaskStatus.AWAITING_EXTERNAL,
    )

    _signal, _old, new_status = apply_task_signal(repositories, audit, task.id, "cancel", "operator", {})

    assert new_status == TaskStatus.CANCELLED


def test_cancelling_an_already_completed_tool_invocation_leaves_it_alone(tmp_path) -> None:
    """Only needs_approval is "stuck" - a succeeded call must keep its real
    result, not get overwritten just because the task was later cancelled."""
    repositories, audit = make_repos(tmp_path)
    task = repositories.tasks.create("write a report")
    request = ToolCallRequest(
        task_id=task.id, tool_name="filesystem.manage", capability=Capability.FILESYSTEM_READ,
        input={"operation": "read_file"},
    )
    repositories.tool_invocations.create(request, status=ToolResultStatus.SUCCEEDED)

    apply_task_signal(repositories, audit, task.id, "cancel", "operator", {})

    invocations = repositories.tool_invocations.list_for_task(task.id)
    assert invocations[0]["status"] == "succeeded"
