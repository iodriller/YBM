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
