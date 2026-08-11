from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from agent_control.scheduler import cadence_from_text, next_run_after, objective_from_schedule_text


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
