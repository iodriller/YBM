from agent_control.tools.path_utils import safe_path_segment


def test_safe_path_segment_replaces_separators_and_traversal_punctuation() -> None:
    assert safe_path_segment("../unsafe folder\\child", fallback="task") == "unsafe_folder_child"


def test_safe_path_segment_uses_caller_fallback_when_nothing_safe_remains() -> None:
    assert safe_path_segment("../..", fallback="generated_adapter") == "generated_adapter"
