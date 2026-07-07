from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "run_all_e2e_tests.py"


def _runner_module():
    spec = importlib.util.spec_from_file_location("run_all_e2e_tests", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_case_suites_do_not_infer_recovery_from_metadata_assertions() -> None:
    runner = _runner_module()

    suites = runner._case_suites(
        {
            "size": "small",
            "tags": ["filesystem"],
            "assertions": {"metadata_any": ["file_manifest"]},
        }
    )

    assert "smoke" in suites
    assert "tools" in suites
    assert "recovery" not in suites
    assert "recovery" in runner._case_suites({"tags": ["recovery"]})


def test_diagnose_turn_enforces_structured_assertions() -> None:
    runner = _runner_module()
    turn = runner.TurnResult(
        final_status="completed",
        tools_seen=["filesystem.manage:inspect_folder"],
        metadata={"file_manifest": {"entries": ["notes.txt"]}},
        artifact_count=1,
        telegram_media_count=1,
        changed_paths_count=2,
        bot_reply_text="The folder has files and notes.",
    )
    case = {
        "assertions": {
            "final_status_in": ["completed"],
            "tools_all": ["filesystem.manage:inspect_folder"],
            "metadata_any": ["file_manifest", "summary"],
            "artifacts_min": 1,
            "telegram_media_min": 1,
            "changed_paths_min": 1,
            "bot_reply_contains_any": ["notes", "budget"],
        }
    }

    passed, reason = runner._diagnose_turn(case, turn, is_followup=False)
    assert passed is True
    assert reason is None

    missing_tool_case = {"assertions": {**case["assertions"], "tools_all": ["browser.open:open"]}}
    passed, reason = runner._diagnose_turn(missing_tool_case, turn, is_followup=False)
    assert passed is False
    assert "missing required tool" in reason


def test_diagnose_turn_fails_on_missing_artifact_media_and_reply() -> None:
    runner = _runner_module()
    turn = runner.TurnResult(
        final_status="completed",
        tools_seen=["artifact.deliver:send_file"],
        metadata={"artifact_delivery": {"delivered": True}},
        artifact_count=0,
        telegram_media_count=0,
        bot_reply_text="Done.",
    )

    passed, reason = runner._diagnose_turn(
        {
            "assertions": {
                "artifacts_min": 1,
                "telegram_media_min": 1,
                "bot_reply_contains_any": ["sent"],
            }
        },
        turn,
        is_followup=False,
    )

    assert passed is False
    assert "artifact_count" in reason
