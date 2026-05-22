from __future__ import annotations

import json
from pathlib import Path

from agent_control.testing.smoke import SMOKE_LOG_KEYS, write_smoke_log


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "e2e" / "cases.json"
EXPECTED_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, *range(10, 47)]
REQUIRED_FIELDS = {
    "number",
    "id",
    "requirement",
    "small_test",
    "larger_test",
    "message",
    "setup",
    "tags",
    "tools_required",
    "expected_behavior",
    "pass_criteria",
    "failure_cases",
    "size",
    "timeout_seconds",
    "assertions",
}


def _load_specs() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


REQUIREMENT_SPECS = _load_specs()


def test_every_user_requirement_has_a_live_e2e_spec() -> None:
    ids = [item["id"] for item in REQUIREMENT_SPECS]
    numbers = [item["number"] for item in REQUIREMENT_SPECS]

    assert len(ids) == 45
    assert len(ids) == len(set(ids))
    assert numbers == EXPECTED_NUMBERS

    for item in REQUIREMENT_SPECS:
        assert REQUIRED_FIELDS <= set(item)
        assert item["requirement"]
        assert item["small_test"]
        assert item["larger_test"]
        assert item["message"]
        assert item["tools_required"]
        assert item["expected_behavior"]
        assert item["pass_criteria"]
        assert item["failure_cases"]
        assert item["timeout_seconds"] > 0
        assert "final_status_in" in item["assertions"]


def test_real_interaction_cases_have_failure_diagnostics() -> None:
    for item in REQUIREMENT_SPECS:
        assert len(item["failure_cases"]) >= 3
        assert any(
            key in item["assertions"]
            for key in (
                "tools_all",
                "tools_any",
                "metadata_any",
                "telegram_media_min",
                "progress_updates_min",
                "schedule_created",
                "plan_required",
            )
        ), item["id"]


def test_high_risk_requirements_are_guarded_for_default_live_runs() -> None:
    guarded_tags = {"external_agent", "long", "fault_injection_needed"}
    risky_numbers = {8, 12, 15, 18, 20, 21, 23, *range(25, 30), *range(32, 38), 39, 40, 42, 44}
    unguarded = [
        item["id"]
        for item in REQUIREMENT_SPECS
        if item["number"] in risky_numbers and not (set(item["tags"]) & guarded_tags)
    ]

    assert unguarded == []


def test_presentation_requirement_keeps_local_llm_as_orchestrator_only() -> None:
    presentation = next(item for item in REQUIREMENT_SPECS if item["id"] == "presentation_external_tool")

    assert "coding.agent:run_goal" in presentation["tools_required"]
    assert "document.manage:create_presentation" in presentation["tools_required"]
    assert "Local LLM directly" in " ".join(presentation["failure_cases"])


def test_browser_form_case_verifies_same_flow_and_no_submit() -> None:
    form = next(item for item in REQUIREMENT_SPECS if item["id"] == "browser_form_fill")
    assertions = form["assertions"]

    assert "browser.control:extract_page_state" in assertions["tools_all"]
    assert "browser.control:fill_form_step" in assertions["tools_all"]
    assert assertions["filled_fields_all"] == ["name", "email", "message"]
    assert assertions["form_submitted"] is False
    assert assertions["telegram_media_min"] == 1


def test_smoke_log_writer_uses_required_debug_fields(tmp_path) -> None:
    path = write_smoke_log(
        tmp_path,
        "desktop screenshot",
        {
            "input_message": "send me a screenshot",
            "task_id": "task_1",
            "route_decision": {"selected_tools": ["computer.use", "artifact.deliver"]},
            "final_status": "completed",
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "desktop_screenshot.json"
    for key in SMOKE_LOG_KEYS:
        assert key in payload
    assert payload["input_message"] == "send me a screenshot"
    assert payload["route_decision"]["selected_tools"] == ["computer.use", "artifact.deliver"]
