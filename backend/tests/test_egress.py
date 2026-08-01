from __future__ import annotations

from agent_control.egress import record_egress
from agent_control.schemas import AuditEventType


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[AuditEventType, dict]] = []

    def append(self, event_type, *, actor, task_id, payload):
        self.events.append((event_type, {"actor": actor, "task_id": task_id, **payload}))


def test_record_egress_ignores_loopback_hosts() -> None:
    """A call to local Ollama/LocalDeploy never left the machine - counting
    it would make every task look like it phoned home."""
    audit = _FakeAudit()

    record_egress(audit, "task_1", "127.0.0.1", "llm")
    record_egress(audit, "task_1", "localhost", "llm")

    assert audit.events == []


def test_record_egress_records_a_real_external_host() -> None:
    audit = _FakeAudit()

    record_egress(audit, "task_1", "api.example.com", "http.request")

    assert len(audit.events) == 1
    event_type, details = audit.events[0]
    assert event_type == AuditEventType.EGRESS_CONTACTED
    assert details == {"actor": "http.request", "task_id": "task_1", "host": "api.example.com", "tool_name": "http.request"}


def test_record_egress_is_a_no_op_without_a_task_id_or_audit_logger() -> None:
    audit = _FakeAudit()

    record_egress(None, "task_1", "api.example.com", "http.request")
    record_egress(audit, None, "api.example.com", "http.request")

    assert audit.events == []
