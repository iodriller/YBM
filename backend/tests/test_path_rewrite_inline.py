"""Quick check that the placeholder-user path rewriter works."""
from pathlib import Path

from agent_control.tools.filesystem_manage import _rewrite_placeholder_user_path


def test_rewrites_me_placeholder():
    home = str(Path.home())
    assert _rewrite_placeholder_user_path(r"C:\Users\me\Desktop\resume.pdf").lower() == \
        (home + r"\Desktop\resume.pdf").lower()


def test_rewrites_user_placeholder_with_forward_slashes():
    home = str(Path.home())
    assert _rewrite_placeholder_user_path("C:/Users/user/Documents").lower() == \
        (home + r"\Documents").lower()


def test_leaves_real_username_untouched():
    assert _rewrite_placeholder_user_path(r"C:\Users\oneye\Desktop") == r"C:\Users\oneye\Desktop"


def test_leaves_alias_untouched():
    assert _rewrite_placeholder_user_path(r"desktop\foo.pdf") == r"desktop\foo.pdf"


def test_leaves_bare_filename_untouched():
    assert _rewrite_placeholder_user_path("resume.pdf") == "resume.pdf"
