"""An error a human reads must never be empty.

`operator_decide_failed: ` with nothing after the colon is what three fixture
recordings reported while failing reproducibly; it read as a flake and was
retried instead of investigated.
"""
from __future__ import annotations

from agent_control.error_text import describe_exception


class SilentError(Exception):
    """Raised bare, so str() is empty - the case that started this."""


def test_message_is_kept_with_its_type() -> None:
    assert describe_exception(ValueError("bad input")) == "ValueError: bad input"


def test_empty_message_falls_back_to_the_type_name() -> None:
    assert describe_exception(SilentError()) == "SilentError"


def test_never_returns_an_empty_string() -> None:
    for exc in (SilentError(), ValueError(""), RuntimeError(), TimeoutError()):
        assert describe_exception(exc).strip(), type(exc).__name__


def test_empty_message_borrows_the_cause() -> None:
    """`raise X from Y` leaves Y reachable; when X says nothing, Y usually does."""
    try:
        try:
            raise ConnectionResetError("peer closed the connection")
        except ConnectionResetError as inner:
            raise SilentError from inner
    except SilentError as exc:
        described = describe_exception(exc)

    assert "SilentError" in described
    assert "peer closed the connection" in described


def test_long_message_is_truncated_but_keeps_the_type() -> None:
    described = describe_exception(ValueError("x" * 5000), limit=100)

    assert len(described) == 100
    assert described.startswith("ValueError: ")
