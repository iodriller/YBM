from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
FOLLOWUP_CASES_PATH = ROOT / "e2e" / "followup_cases.json"
RUNNER_PATH = ROOT / "e2e" / "live_telegram_e2e.py"


def test_followup_e2e_cases_define_memory_checks() -> None:
    cases = json.loads(FOLLOWUP_CASES_PATH.read_text(encoding="utf-8"))

    assert [case["id"] for case in cases] == [
        "chat_history_desktop_followup",
        "chat_history_folder_followup",
        "chat_history_pdf_followup",
    ]
    for case in cases:
        assert case["message"]
        assert case["assertions"]["final_status_in"]
        assert case["follow_ups"]
        for follow_up in case["follow_ups"]:
            assert follow_up["message"]
            assert follow_up["assertions"]["bot_reply_contains_any"]


def test_live_e2e_runner_dry_runs_followup_cases() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER_PATH),
            "--cases",
            str(FOLLOWUP_CASES_PATH),
            "--case",
            "chat_history_folder_followup",
            "--dry-run",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Inspect the folder" in result.stdout
    assert "-> What is in that folder? Give me the full list." in result.stdout
