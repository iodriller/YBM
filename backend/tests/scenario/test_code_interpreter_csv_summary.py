"""Scenario: "use the code interpreter to build a CSV, then summarize it into
JSON" through the real LLM planner -> code.interpreter (which generates its
own script via a second structured-output call) -> real local Python
execution -> artifact.deliver. Ports e2e/all_cases.json's
`code_interpreter_csv_summary` case down to the deterministic tier
(docs/ROADMAP.md P2) - the counterpart to test_code_interpreter.py's
single-number-result case, this one locks in a script that writes a file
rather than just printing a value, and a delivery-only completion with no
synthesizer/validator step at all.

Real gap surfaced while recording this fixture, deliberately not "fixed" by
re-recording until it looks better: the recorded code.interpreter script for
step 2 ran without error but did NOT actually compute the total - it wrote a
JSON echo of the objective text instead of reading expenses.csv and summing
`amount`. Nothing in the pipeline caught this, because `generate_and_run`
only regenerates on a `SyntaxError`/`ValueError` (invalid Python), not on
"valid Python that doesn't do what the objective asked" - and a
delivery-only plan (ending in `artifact.deliver`, no synthesis step) has no
validator reading the file's actual content against the objective the way
`test_document_pdf_summary.py`'s AnswerValidator round-trip does. Tracked as
a real architecture gap in docs/ROADMAP.md, not something this test tries to
paper over - it asserts the structural contract (ran, created a file,
delivered it) that the pipeline does guarantee, not correctness of
LLM-generated code content, which nothing here currently guarantees.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_control.config import CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir

pytestmark = pytest.mark.skip(
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/AnswerValidator prompts); the Operator loop (docs/ROADMAP.md P3 "
    "\u00a72.2) is now the sole execution path and needs its own fixture, recorded fresh "
    "against a live LLM - see orchestration/operator.py and test_operator_loop.py for the "
    "pattern. Left in place (not deleted) so the scenario this file documents survives as "
    "a checklist for that re-recording pass."
)


@pytest.mark.asyncio
async def test_code_interpreter_csv_summary_writes_json_totals(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_csv_summary")

    caps = default_capability_policies()
    caps[Capability.TERMINAL_RUN] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    settings = isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        # require_approval_for_untrusted_run_python: False - this test is
        # about execution correctness, not the approval gate; see
        # test_code_interpreter.py's
        # test_code_interpreter_generated_run_needs_approval_on_silent_docker_fallback
        # for that gate's own coverage.
        adapters={
            "code_interpreter": {
                "enabled": True,
                "workspace_root": str(workspace),
                "require_approval_for_untrusted_run_python": False,
            }
        },
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="code_interpreter_csv_summary")

    task = await run_task_to_completion(
        scenario,
        "Use the local code interpreter to create a small CSV with expenses for hosting 25, "
        "OCR review 45, and browser testing 120. Then run Python to calculate the total and "
        "write expense-summary.json.",
    )

    assert task.status == TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    interpreter_calls = [call for call in tool_calls if call["tool_name"] == "code.interpreter"]
    assert interpreter_calls
    created_files = [
        name
        for call in interpreter_calls
        for name in (call["result"] or {}).get("output", {}).get("files_created", [])
    ]
    assert any("expenses.csv" in name for name in created_files)
    assert any("expense-summary.json" in name for name in created_files)
    # Delivered via artifact.deliver, not synthesized text - this run has no
    # ResponseSynthesizer/AnswerValidator call at all, unlike the content-tool
    # scenario tests. See module docstring: content correctness of the
    # generated script is NOT verified anywhere in this path today.
    assert scenario.telegram.documents
    assert any("expense-summary.json" in path for _chat_id, path, _caption in scenario.telegram.documents)
    artifacts = scenario.repositories.artifacts.list_for_task(task.id)
    summary_artifact = next(a for a in artifacts if a.uri and "expense-summary.json" in a.uri)
    summary_path = Path(summary_artifact.uri.removeprefix("file://"))
    assert summary_path.exists()


@pytest.mark.asyncio
async def test_code_interpreter_csv_summary_disabled_by_capability_policy(tmp_path, monkeypatch) -> None:
    workspace = scenario_scratch_dir("code_interpreter_csv_summary")

    # TERMINAL_RUN left at its secure-by-default disabled state.
    settings = isolated_settings(
        monkeypatch, tmp_path,
        adapters={"code_interpreter": {"enabled": True, "workspace_root": str(workspace)}},
    )
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="code_interpreter_csv_summary")

    task = await run_task_to_completion(
        scenario,
        "Use the local code interpreter to create a small CSV with expenses for hosting 25, "
        "OCR review 45, and browser testing 120. Then run Python to calculate the total and "
        "write expense-summary.json.",
    )

    assert task.status != TaskStatus.COMPLETED
