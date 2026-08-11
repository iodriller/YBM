"""When a provider says "you're out of quota", work out when to try again.

Coding assistants (Copilot, Claude, Codex) fail a long build with a usage
limit rather than a transient rate limit: the quota is gone for hours, not
seconds. The worker used to escalate that straight to the user - the task
stopped and waited for a human reply. For an unattended overnight build that
is the wrong end of the trade: the limit resolves itself, and the only thing
a human adds is the delay until they happen to look at their phone.

These providers usually say when they reset. Parsing that is strictly better
than a fixed backoff, because the guess is either far too short (hammering a
provider that is still refusing) or far too long (idling for hours after the
quota came back).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re

from agent_control.schemas import utc_now


# Deliberately conservative: probe an hour out when the provider says nothing
# useful. Long enough not to hammer, short enough that a quota that resets on
# the hour is picked up in one cycle rather than sitting idle until morning.
DEFAULT_RETRY_AFTER_SECONDS = 3600

# Never park a task for longer than this, whatever the message claims. A
# provider that reports "resets in 30 days" (or a parse that goes wrong) must
# not silently retire a task into a state nobody ever looks at again.
MAX_RETRY_AFTER_SECONDS = 6 * 3600

_RELATIVE = re.compile(
    r"(?:try again|retry|resets?|available again|back)\D{0,20}?"
    r"(\d{1,4})\s*(second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs|day|days)\b",
    re.IGNORECASE,
)
_IN_DURATION = re.compile(
    r"\bin\s+(\d{1,4})\s*(second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs)\b",
    re.IGNORECASE,
)
_CLOCK_TIME = re.compile(
    r"(?:resets?|try again|available again)\D{0,20}?\b(\d{1,2}):(\d{2})\b",
    re.IGNORECASE,
)

_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
}


def parse_retry_after_seconds(message: str | None) -> int | None:
    """Seconds until the provider says it will accept work again, or None.

    None means "nothing parseable" - the caller decides the fallback, rather
    than this function inventing a number that looks like it came from the
    provider.
    """
    if not message:
        return None
    for pattern in (_RELATIVE, _IN_DURATION):
        match = pattern.search(message)
        if match:
            amount = int(match.group(1))
            seconds = amount * _UNIT_SECONDS.get(match.group(2).lower(), 60)
            return max(1, min(seconds, MAX_RETRY_AFTER_SECONDS))
    clock = _CLOCK_TIME.search(message)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            now = utc_now()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            seconds = int((target - now).total_seconds())
            return max(1, min(seconds, MAX_RETRY_AFTER_SECONDS))
    return None


def next_attempt_at(message: str | None) -> tuple[datetime, int, bool]:
    """(when to resume, seconds waited, whether the provider told us).

    The third value matters for what the operator is told: "waiting until the
    limit resets at 14:00" is a very different message from "no reset time
    given, checking back in an hour", and reporting the second as the first
    would be inventing certainty.
    """
    parsed = parse_retry_after_seconds(message)
    seconds = parsed if parsed is not None else DEFAULT_RETRY_AFTER_SECONDS
    return utc_now() + timedelta(seconds=seconds), seconds, parsed is not None


def describe_wait(seconds: int, from_provider: bool) -> str:
    if seconds >= 3600:
        amount = f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    elif seconds >= 60:
        amount = f"{seconds // 60} min"
    else:
        amount = f"{seconds}s"
    source = "the provider's reset time" if from_provider else "no reset time given, so checking back"
    return f"{amount} ({source})"
