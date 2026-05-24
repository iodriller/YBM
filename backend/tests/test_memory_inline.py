"""Inline checks for the placeholder-path scrubber used by memory."""
from agent_control.channels.memory import _strip_placeholder_paths


def test_strips_me_path_keeps_basename():
    assert _strip_placeholder_paths(
        r"found and read C:\Users\me\Desktop\resume.pdf today"
    ) == "found and read resume.pdf today"


def test_strips_forward_slash_user_path():
    assert _strip_placeholder_paths(
        "opened C:/Users/user/Documents/report.docx for review"
    ) == "opened report.docx for review"


def test_leaves_real_path_alone():
    text = r"the real path C:\Users\oneye\Desktop\foo.pdf"
    assert _strip_placeholder_paths(text) == text


def test_no_path_unchanged():
    assert _strip_placeholder_paths("no path here just text") == "no path here just text"


def test_strips_path_only_no_filename():
    # A path without filename → completely removed (empty replacement).
    assert _strip_placeholder_paths(r"go to C:\Users\me\Desktop now").strip() == "go to  now".strip() or \
           _strip_placeholder_paths(r"go to C:\Users\me\Desktop now").strip() == "go to now"
