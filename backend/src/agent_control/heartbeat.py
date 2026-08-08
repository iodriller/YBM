"""Say something while a long step is still running.

Every progress message was triggered by a *status or step change*. A task that
spends forty minutes inside one `coding.agent` session, one browser sequence,
or one large file scan changes neither - so it went completely silent. Not
because nothing was happening, but because nothing had finished happening.

From the operator's side those two are indistinguishable, and the honest
reading of silence is "it died". So this runs beside the worker loops rather
than inside them: a worker sitting in `await` on a forty-minute subprocess
cannot emit anything, which is precisely when an update is worth most.

Deliberately quiet: one line per interval per task, never a countdown, and
nothing at all for tasks that are merely queued. A heartbeat that becomes
noise gets muted, and then the real ones are missed too.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any, Protocol

from agent_control.schemas import TaskRecord, TaskStatus, utc_now
from agent_control.storage.repositories import Repositories


logger = logging.getLogger(__name__)

# Statuses where the agent is actively working. A task waiting on an approval
# or parked on a usage limit already told the operator why it is waiting -
# repeating that on a timer is nagging, not information.
WORKING_STATUSES = [TaskStatus.RUNNING, TaskStatus.RETRYING]

LAST_HEARTBEAT_KEY = "last_heartbeat_at"


class ProgressSink(Protocol):
    async def notify(self, task: TaskRecord) -> None: ...


def _last_heartbeat(task: TaskRecord) -> datetime | None:
    raw = task.metadata.get(LAST_HEARTBEAT_KEY)
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def due_for_heartbeat(task: TaskRecord, *, interval_seconds: float, now: datetime | None = None) -> bool:
    """True when this task has been quiet long enough to be worth a line.

    Measured from the last heartbeat, or from the task's own last update when
    there has never been one - so a task that only just changed status does
    not get a redundant "still working" a second later.
    """
    if task.status not in WORKING_STATUSES:
        return False
    moment = now or utc_now()
    since = _last_heartbeat(task) or task.updated_at
    if since is None:
        return False
    return moment - since >= timedelta(seconds=interval_seconds)


async def run_heartbeat_forever(
    repositories: Repositories,
    sink: ProgressSink | None,
    *,
    interval_seconds: float = 300.0,
    poll_seconds: float = 30.0,
    should_continue: Any = None,
) -> None:
    """Emit a progress line for any task that has been working quietly.

    Runs forever alongside the worker loops. Every failure is swallowed and
    logged: a heartbeat is the least important thing in the process and must
    never be able to stop the workers beside it.
    """
    if sink is None:
        return
    while True if should_continue is None else should_continue():
        try:
            for task in repositories.tasks.list_by_statuses(WORKING_STATUSES, limit=20):
                if not due_for_heartbeat(task, interval_seconds=interval_seconds):
                    continue
                # Stamped before sending: a send that fails should not queue up
                # a burst of retries on the next tick.
                repositories.tasks.update_metadata(
                    task.id, {**task.metadata, LAST_HEARTBEAT_KEY: utc_now().isoformat()}
                )
                refreshed = repositories.tasks.get(task.id)
                if refreshed is not None:
                    await sink.notify(refreshed)
        except Exception:  # noqa: BLE001 - never take the workers down with it
            logger.warning("heartbeat pass failed", exc_info=True)
        await asyncio.sleep(poll_seconds)
