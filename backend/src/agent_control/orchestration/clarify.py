from __future__ import annotations

from agent_control.schemas import TaskRecord


# Pattern table mapping failure text to a targeted question. Matched against
# the combined reason + last error, first hit wins; the fallback question
# covers everything else. Keep this a data table — do not grow per-case logic.
_QUESTION_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("usage limit", "usage_limited", "quota", "rate limit", "rate_limited"),
        "It hit a usage limit. Should I wait and retry later, switch to another provider/tool, or drop this?",
    ),
    (
        ("login", "log in", "sign in", "credential", "auth", "password", "unauthorized", "401", "403"),
        "It needs a login or credentials I don't have. Log in (or send what I need) and reply 'continue', "
        "or tell me a different way to get this done.",
    ),
    (
        ("chrome", "devtools", "browser", "websocket"),
        "I couldn't reach the browser. Should I retry without the browser (direct fetch/script), "
        "or will you start Chrome with remote debugging so I can use it?",
    ),
    (
        ("not found", "no such file", "does not exist", "missing path", "path"),
        "I couldn't find the file or folder this needs. What exact path or name should I use?",
    ),
    (
        ("capability", "disabled", "not enabled", "policy"),
        "A capability this needs is disabled in my configuration. Should I try a different approach, "
        "or do you want to enable it and have me retry?",
    ),
)


def build_clarifying_question(task: TaskRecord, reason: str) -> str:
    """One targeted question for a task that exhausted its safe attempts."""
    error = _last_error_text(task)
    haystack = f"{reason} {error}".lower()
    question = next(
        (text for markers, text in _QUESTION_RULES if any(marker in haystack for marker in markers)),
        None,
    )
    if question is None:
        detail = (error or reason).strip()
        question = (
            "I tried a few approaches and could not finish. "
            + (f"Last error: {detail[:300]}. " if detail else "")
            + "How should I proceed — different approach, more details, or 'cancel'?"
        )
    objective = task.objective.strip()
    return f"Question about: {objective[:180]}\n\n{question}"


def _last_error_text(task: TaskRecord) -> str:
    result = task.metadata.get("last_tool_result")
    if isinstance(result, dict) and result.get("error_message"):
        return str(result["error_message"])
    for key in ("last_worker_error", "fulfillment_gap", "planning_error", "last_replan_reason"):
        value = task.metadata.get(key)
        if value:
            return str(value)
    return ""
