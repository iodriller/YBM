from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from test_capability_requirements_matrix import REQUIREMENT_SPECS


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "e2e" / "cases.json"
RUNNER_PATH = ROOT / "e2e" / "live_telegram_e2e.py"


def test_live_e2e_cases_cover_all_capability_requirements() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    case_ids = [case["id"] for case in cases]
    requirement_ids = [item["id"] for item in REQUIREMENT_SPECS]

    assert len(case_ids) == 33
    assert case_ids == requirement_ids
    assert len(case_ids) == len(set(case_ids))

    for case in cases:
        assert case["message"]
        assert case["requirement"]
        assert case.get("assertions")
        assert "final_status_in" in case["assertions"]


def test_live_e2e_runner_lists_cases_without_telethon() -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--list-cases"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "desktop_observation" in result.stdout
    assert "natural_workflow" in result.stdout
