from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from agent_control.schemas import AuditEventType, ScheduleRecord, ScheduleStatus, TaskRecord, utc_now
from agent_control.storage.audit import AuditLogger
from agent_control.storage.repositories import Repositories


def next_run_after(cadence: str, after: datetime | None = None) -> datetime:
    base = after or utc_now()
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    lowered = cadence.lower().strip()
    if match := re.search(r"every\s+(\d+)\s+minutes?", lowered):
        return base + timedelta(minutes=max(1, int(match.group(1))))
    if match := re.search(r"every\s+(\d+)\s+hours?", lowered):
        return base + timedelta(hours=max(1, int(match.group(1))))
    if "weekly" in lowered or "every week" in lowered:
        return base + timedelta(days=7)
    return base + timedelta(days=1)


def cadence_from_text(text: str) -> str:
    lowered = text.lower()
    if match := re.search(r"every\s+\d+\s+(?:minute|minutes|hour|hours|day|days|week|weeks)", lowered):
        return match.group(0)
    if "daily" in lowered or "every day" in lowered:
        return "daily"
    if "weekly" in lowered or "every week" in lowered:
        return "weekly"
    return "daily"


def objective_from_schedule_text(text: str) -> str:
    cleaned = re.sub(r"\b(set up|create|add|schedule|scheduled job|job|that runs|every day|daily|weekly)\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"every\s+\d+\s+(?:minute|minutes|hour|hours|day|days|week|weeks)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:")
    cleaned = re.sub(r"^(?:a\s+)?to\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned or text


def create_due_task(repositories: Repositories, audit: AuditLogger, schedule: ScheduleRecord) -> TaskRecord:
    conversation_id = (
        repositories.conversations.get_or_create(schedule.source_channel, schedule.source_chat_id)
        if schedule.source_chat_id
        else None
    )
    task = repositories.tasks.create(
        schedule.objective,
        conversation_id=conversation_id,
        metadata={
            "source_schedule_id": schedule.id,
            "source_chat_id": schedule.source_chat_id,
            "schedule_cadence": schedule.cadence,
            **schedule.metadata,
        },
    )
    now = utc_now()
    repositories.schedules.mark_run(schedule.id, task.id, now, next_run_after(schedule.cadence, now))
    audit.append(
        AuditEventType.TASK_CREATED,
        actor="scheduler",
        task_id=task.id,
        payload={"schedule_id": schedule.id, "objective": schedule.objective},
    )
    return task


async def run_scheduler_once(repositories: Repositories, audit: AuditLogger, *, now: datetime | None = None, limit: int = 20) -> list[TaskRecord]:
    due = repositories.schedules.list_due(now or utc_now(), limit=limit)
    created: list[TaskRecord] = []
    for schedule in due:
        if schedule.status != ScheduleStatus.ENABLED:
            continue
        try:
            created.append(create_due_task(repositories, audit, schedule))
        except Exception as exc:
            audit.append(
                AuditEventType.ERROR,
                actor="scheduler",
                payload={"schedule_id": schedule.id, "error": str(exc)},
            )
    return created


async def run_scheduler_forever(
    repositories: Repositories,
    audit: AuditLogger,
    *,
    poll_interval_seconds: float = 30.0,
) -> None:
    while True:
        await run_scheduler_once(repositories, audit)
        await asyncio.sleep(poll_interval_seconds)


def schedule_to_output(schedule: ScheduleRecord) -> dict[str, Any]:
    return schedule.model_dump(mode="json")
