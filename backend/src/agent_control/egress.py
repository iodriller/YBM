"""Records when a task's own tool call reaches beyond this machine.

Backs Task Receipts' "did anything leave the machine" line
(docs/UI_UX_AUDIT.md Phase 2) with a real signal instead of a guess: an
EGRESS_CONTACTED audit event, task-scoped like every other audit event, so
a receipt just filters the same audit_events table it already reads.
Loopback hosts (local Ollama/LocalDeploy, the VS Code bridge) are excluded
on purpose - they never leave the machine, so counting them would make
every task look like it phoned home.
"""

from __future__ import annotations

from typing import Any

from agent_control.config import is_loopback_host
from agent_control.schemas import AuditEventType


def record_egress(audit: Any | None, task_id: str | None, host: str, tool_name: str) -> None:
    if audit is None or not task_id or is_loopback_host(host):
        return
    audit.append(
        AuditEventType.EGRESS_CONTACTED,
        actor=tool_name,
        task_id=task_id,
        payload={"host": host, "tool_name": tool_name},
    )
