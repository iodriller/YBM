from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agent_control.scheduler import (
    cadence_from_text,
    next_run_after,
    objective_from_schedule_text,
    run_scheduler_forever,
)
from agent_control.schemas import utc_now
from helpers import make_repos


def _backdate_task(repositories, task_id: str, *, days_old: int) -> None:
    created_at = (utc_now() - timedelta(days=days_old)).isoformat()
    with repositories.tasks.database.connect() as connection:
        connection.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (created_at, task_id))


def test_cadence_from_text_captures_full_plural_unit() -> None:
    """Regression: alternation without a trailing boundary previously matched
    the shorter "day" branch inside "days" (regex alternation is first-match,
    not longest-match), truncating "every 3 days" to "every 3 day"."""
    assert cadence_from_text("please run every 3 days") == "every 3 days"
    assert cadence_from_text("sync every 2 weeks") == "every 2 weeks"
    assert cadence_from_text("check every 15 minutes") == "every 15 minutes"
    assert cadence_from_text("ping every 6 hours") == "every 6 hours"


def test_next_run_after_honors_day_and_week_intervals() -> None:
    """Regression: next_run_after had no day/week branch at all, so any
    "every N days"/"every N weeks" cadence silently fell through to the
    generic +1 day default."""
    base = datetime(2026, 1, 1, 9, 0, tzinfo=ZoneInfo("UTC"))

    assert next_run_after("every 3 days", base) == base + timedelta(days=3)
    assert next_run_after("every 2 weeks", base) == base + timedelta(weeks=2)
    assert next_run_after("every 1 day", base) == base + timedelta(days=1)
    assert next_run_after("weekly", base) == base + timedelta(days=7)
    assert next_run_after("daily", base) == base + timedelta(days=1)


def test_objective_from_schedule_text_strips_full_plural_cadence() -> None:
    cleaned = objective_from_schedule_text("schedule a job every 3 days to check the site")
    assert "day" not in cleaned.replace("days", "")  # no stray trailing "s" leaked through
    assert "every 3 days" not in cleaned


def test_next_run_after_preserves_local_wall_clock_across_dst() -> None:
    """A daily cadence should keep firing at the same local wall-clock time
    even when the interval crosses a DST transition - naive +24h UTC
    arithmetic would drift the local fire time by an hour. March 8, 2026 is
    the US DST start (second Sunday in March): clocks spring forward
    overnight, so the elapsed UTC time is 23h, not 24h, but the local wall
    clock must still read 09:00."""
    tz = ZoneInfo("America/Chicago")
    base = datetime(2026, 3, 7, 9, 0, tzinfo=tz)  # day before the DST jump

    next_run = next_run_after("daily", base, "America/Chicago")

    local_next = next_run.astimezone(tz)
    assert local_next.hour == 9
    assert local_next.minute == 0
    assert local_next.date() == (base.date() + timedelta(days=1))
    # Confirms a DST transition actually occurred between base and next_run:
    # elapsed UTC time is 23h (spring-forward), not a flat 24h.
    assert next_run - base == timedelta(hours=23)


def test_next_run_after_falls_back_to_utc_for_unknown_timezone() -> None:
    base = datetime(2026, 1, 1, 9, 0, tzinfo=ZoneInfo("UTC"))
    next_run = next_run_after("daily", base, "Not/A_Real_Zone")
    assert next_run == base + timedelta(days=1)


@pytest.mark.asyncio
async def test_run_scheduler_forever_sweeps_retention_when_configured(tmp_path) -> None:
    """retention_days is opt-in (config.py's storage.retention_days defaults
    to None) - when set, the scheduler's own long-running loop is what
    applies it, with no separate periodic job to remember to run. The loop
    never returns on its own, so this bounds the run with wait_for and
    expects the TimeoutError that produces."""
    repositories, audit = make_repos(tmp_path)
    old_task = repositories.tasks.create(objective="old task")
    _backdate_task(repositories, old_task.id, days_old=60)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            run_scheduler_forever(repositories, audit, poll_interval_seconds=0.01, retention_days=30),
            timeout=0.5,
        )

    assert repositories.tasks.get(old_task.id) is None


@pytest.mark.asyncio
async def test_run_scheduler_forever_leaves_history_alone_when_retention_is_unset(tmp_path) -> None:
    repositories, audit = make_repos(tmp_path)
    old_task = repositories.tasks.create(objective="old task")
    _backdate_task(repositories, old_task.id, days_old=60)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            run_scheduler_forever(repositories, audit, poll_interval_seconds=0.01, retention_days=None),
            timeout=0.3,
        )

    assert repositories.tasks.get(old_task.id) is not None
