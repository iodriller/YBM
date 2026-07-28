"""Scenario: "send me the PDF at this path" through the real LLM planner ->
a single artifact.deliver send_file call -> the fake Telegram client. Ports
e2e/all_cases.json's `send_found_pdf` case down to the deterministic tier
(docs/ROADMAP.md P2) - the simplest possible delivery-only case (single
step, a known literal path, no filesystem search first), chosen deliberately
after `output_delivery` turned out to need an unusually flaky multi-attempt
plan; this one locks in the harness's FakeTelegramClient path with minimal
surface area for planner non-determinism.
"""

from __future__ import annotations

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, default_capability_policies
from agent_control.schemas import Capability, RiskLevel, TaskStatus

from .harness import build_scenario, isolated_settings, run_task_to_completion, scenario_scratch_dir

pytestmark = pytest.mark.skip(
    reason="fixture recorded against the deleted plan-once path (PlannerService/ResponseSynthesizer/AnswerValidator prompts); the Operator loop (docs/ROADMAP.md P3 "
    "\u00a72.2) is now the sole execution path and needs its own fixture, recorded fresh "
    "against a live LLM - see orchestration/operator.py and test_operator_loop.py for the "
    "pattern. Left in place (not deleted) so the scenario this file documents survives as "
    "a checklist for that re-recording pass."
)


def _write_minimal_pdf(path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream.encode('latin-1'))} >> stream\n{stream}\nendstream endobj\n",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(output))
        output.extend(obj.encode("latin-1"))
    xref_at = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.extend(
        f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref_at}\n%%EOF\n".encode("latin-1")
    )
    path.write_bytes(output)


def _settings(monkeypatch, tmp_path, allowed_root: str) -> AppSettings:
    caps = default_capability_policies()
    caps[Capability.FILESYSTEM_WRITE] = CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)
    return isolated_settings(
        monkeypatch, tmp_path,
        capabilities=caps,
        adapters={"computer_use": {"enabled": True, "allowed_roots": [allowed_root]}},
    )


@pytest.mark.asyncio
async def test_send_found_pdf_delivers_the_file(tmp_path, monkeypatch) -> None:
    desktop_dir = scenario_scratch_dir("send_found_pdf")
    pdf_path = desktop_dir / "agent-control-sample.pdf"
    _write_minimal_pdf(pdf_path, "Agent Control E2E PDF sample content.")

    settings = _settings(monkeypatch, tmp_path, str(desktop_dir))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="send_found_pdf")

    task = await run_task_to_completion(scenario, f"Send me the PDF file at {pdf_path}.")

    assert task.status == TaskStatus.COMPLETED
    tool_calls = scenario.repositories.tool_invocations.list_for_task(task.id)
    assert any(call["tool_name"] == "artifact.deliver" for call in tool_calls)
    assert scenario.telegram.documents
    assert any("agent-control-sample.pdf" in path for _chat_id, path, _caption in scenario.telegram.documents)


@pytest.mark.asyncio
async def test_send_found_pdf_rejects_path_outside_allowed_roots(tmp_path, monkeypatch) -> None:
    desktop_dir = scenario_scratch_dir("send_found_pdf")
    pdf_path = desktop_dir / "agent-control-sample.pdf"
    _write_minimal_pdf(pdf_path, "Agent Control E2E PDF sample content.")

    settings = _settings(monkeypatch, tmp_path, str(tmp_path / "somewhere_else"))
    scenario = build_scenario(settings, tmp_path=tmp_path, fixture_name="send_found_pdf")

    task = await run_task_to_completion(scenario, f"Send me the PDF file at {pdf_path}.")

    assert task.status != TaskStatus.COMPLETED
    assert not scenario.telegram.documents
