"""One-line, never-empty descriptions of an exception, for anything a human reads.

`str(exc)` is empty for a surprising number of real failures - a bare
`raise SomeError`, several httpx transport errors, and anything whose message
lives only in the class name. Formatting one of those into an error string
produces `operator_decide_failed: ` with nothing after the colon, which is what
a task's `last_worker_error` showed when three fixture recordings failed in a
row: a reproducible, diagnosable failure that read as a flake and was retried
instead of investigated.
"""

from __future__ import annotations


def describe_exception(exc: BaseException, *, limit: int = 400) -> str:
    """``TypeName: message``, or just ``TypeName`` when there is no message.

    ``limit`` truncates the message, never the type name - the type is the part
    that is always present and often the only clue.
    """
    name = type(exc).__name__
    message = str(exc).strip()
    if not message:
        # A cause is better than nothing: `raise X from Y` keeps Y reachable
        # even when X itself says nothing.
        cause = exc.__cause__ or exc.__context__
        if cause is not None:
            inner = str(cause).strip()
            inner_name = type(cause).__name__
            detail = f"{inner_name}: {inner}" if inner else inner_name
            return f"{name} (from {detail})"[:limit]
        return name
    combined = f"{name}: {message}"
    return combined[:limit]


def explain_voice_failure(exc: BaseException) -> str:
    """What to say when a voice note could not be turned into text.

    Two sentences: what happened, and what the person can do. The diagnostic
    string stays in the audit trail, where it is useful; `RuntimeError: STT
    adapter is disabled` is a correct log line and a terrible reply.
    """
    detail = f"{type(exc).__name__}: {exc}".lower()
    # "not configured" is what the default adapter actually says, and it is
    # the same situation as "disabled" from the user's point of view.
    if "disabled" in detail or "not configured" in detail:
        return (
            "I can't listen to voice messages yet - speech-to-text is turned off in this setup. "
            "Send it as text and I'll get straight on it, or turn on voice under Settings."
        )
    if "not installed" in detail or "modulenotfound" in detail or "no module named" in detail:
        return (
            "Voice transcription isn't installed in this setup, so I can't hear that one. "
            "Send it as text, or install the voice extra to enable it."
        )
    if "timeout" in detail or "timed out" in detail:
        return (
            "That recording took too long to transcribe and I gave up. "
            "A shorter message usually works, or send it as text."
        )
    return (
        "Something went wrong turning that recording into text, so I haven't acted on it. "
        "Send it as text and I'll pick it up - the details are in the task trace."
    )


#: Known failure shapes to a sentence about what happened and a sentence about
#: what to do. Order matters: the first match wins, so put specific before
#: general.
_USER_FACING_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("rejected the api key", "401", "authenticationerror", "invalid api key"),
        "The API key was rejected. Paste it again under Settings, Model.",
    ),
    (
        ("rate limit", "429", "too many requests"),
        "The model provider is rate limiting us. Wait a minute and try again.",
    ),
    (
        ("base_url is required",),
        "That provider needs a base URL. Add one under Settings, Model.",
    ),
    (
        ("allowlist_empty",),
        "I don't recognise that account, so I ignored the message. Add yourself under Settings, Telegram.",
    ),
    (
        ("connect", "refused", "could not reach", "connecterror"),
        "I couldn't reach the model. If it runs on this machine, check it's started; "
        "otherwise check the connection.",
    ),
    (
        ("http 5", "502", "503", "overloaded"),
        "The model server stopped responding partway through. "
        "It's usually back within a minute - try again, or switch models under Settings.",
    ),
    (
        ("timeout", "timed out"),
        "The model took too long to answer and I stopped waiting. Try again, or pick a faster model.",
    ),
    (
        ("refusal",),
        "The model declined to answer that one. Rephrasing usually helps.",
    ),
)


def explain_for_user(exc: BaseException) -> str:
    """A sentence on what happened and a sentence on what to do next.

    Never a class name, a status code, or an internal stage. `describe_exception`
    still produces those for the log and the task trace, which is where someone
    debugging will look; this is for the person who just wanted an answer.
    """
    haystack = f"{type(exc).__name__}: {exc}".lower()
    for needles, message in _USER_FACING_RULES:
        if any(needle in haystack for needle in needles):
            return message
    return "Something went wrong on my side and I couldn't finish that. The details are in the task trace."
