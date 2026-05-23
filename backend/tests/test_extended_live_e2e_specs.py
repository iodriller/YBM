from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
EXTENDED_CASES_PATH = ROOT / "e2e" / "extended_cases.json"
RUNNER_PATH = ROOT / "e2e" / "live_telegram_e2e.py"


def test_extended_e2e_cases_define_folder_explanation_and_code_interpreter() -> None:
    cases = json.loads(EXTENDED_CASES_PATH.read_text(encoding="utf-8"))
    by_id = {case["id"]: case for case in cases}

    assert {
        "code_interpreter_generate_file",
        "code_interpreter_csv_summary",
        "code_interpreter_markdown_report",
        "code_interpreter_json_transform",
        "desktop_file_listing_uses_filesystem",
        "desktop_file_search_then_delivery",
        "implicit_code_interpreter_numbers_report",
        "implicit_code_interpreter_csv_transform",
        "implicit_code_interpreter_markdown_from_notes",
    } <= set(by_id)

    folder_case = by_id["folder_mixed_file_explanation"]
    assert folder_case["message"].count("{{mixed_content_folder}}") == 1
    assert folder_case["assertions"]["tools_all"] == ["filesystem.manage:describe_folder"]
    assert "ocr_status" in folder_case["assertions"]["metadata_any"]

    for case_id in [
        "code_interpreter_generate_file",
        "code_interpreter_csv_summary",
        "code_interpreter_markdown_report",
        "code_interpreter_json_transform",
    ]:
        interpreter_case = by_id[case_id]
        assert interpreter_case["assertions"]["tools_all"] == ["code.interpreter:generate_and_run"]
        assert "workspace_dir" in interpreter_case["assertions"]["metadata_any"]
        assert all("{{" not in item for item in interpreter_case["assertions"].get("bot_reply_contains_any", []))

    listing_case = by_id["desktop_file_listing_uses_filesystem"]
    assert listing_case["assertions"]["tools_all"] == ["filesystem.manage:inspect_folder"]
    assert "computer.use" in listing_case["assertions"]["tools_forbidden"]

    delivery_case = by_id["desktop_file_search_then_delivery"]
    assert delivery_case["assertions"]["tools_all"] == ["filesystem.manage:search", "artifact.deliver:send_file"]
    assert delivery_case["assertions"]["telegram_media_min"] == 1

    for case_id in [
        "implicit_code_interpreter_numbers_report",
        "implicit_code_interpreter_csv_transform",
        "implicit_code_interpreter_markdown_from_notes",
    ]:
        implicit_case = by_id[case_id]
        assert "implicit_route" in implicit_case["tags"]
        assert implicit_case["assertions"]["tools_all"] == ["code.interpreter:generate_and_run"]
        assert "coding.agent" in implicit_case["assertions"]["tools_forbidden"]


def test_live_e2e_runner_lists_extended_cases() -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--cases", str(EXTENDED_CASES_PATH), "--list-cases"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "folder_mixed_file_explanation" in result.stdout
    assert "code_interpreter_generate_file" in result.stdout
    assert "code_interpreter_csv_summary" in result.stdout
    assert "desktop_file_search_then_delivery" in result.stdout
    assert "implicit_code_interpreter_numbers_report" in result.stdout
