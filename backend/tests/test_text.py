from agent_control.text import trim_text


def test_trim_text_preserves_short_text_and_marks_truncation() -> None:
    assert trim_text("short", 10) == "short"
    assert trim_text("abcdefghij", 7) == "abcd..."
