"""Run every YBM E2E case back-to-back through Telegram, capture detailed traces.

This is built to run UNATTENDED. Goals:

* Load every case from ``e2e/all_cases.json`` (consolidated catalogue, currently 70 cases).
* Use the same fixture setup as ``e2e/live_telegram_e2e.py`` so ``{{documents_folder}}``
  / ``{{episode_url}}`` template variables resolve to real paths/URLs.
* Send each message from your real Telegram user account; poll the admin API for
  task progress; honor each case's ``timeout_seconds``.
* For multi-turn cases (``follow_ups``), keep conversation memory across turns.
* Never crash mid-run — every per-stage failure is captured as a FAIL and the
  suite continues. Summary is rewritten after every stage so partial runs survive.
* By default, skip ``guarded`` cases (codex / copilot / external-quota cases that
  need real credentials). Pass ``--include-guarded`` to run them too.

Outputs:

    .agent_control/e2e_results/run_<timestamp>/
        summary.md           # at-a-glance pass/fail table
        summary.json         # same data, machine-readable
        <NN>_<case_id>/
            result.json      # full structured stage result
            timeline.txt     # human-readable status flow + plan + answer
            audit.json       # every audit event for the task
            diagnosis.md     # only present for failed stages

Usage:
    python scripts/run_all_e2e_tests.py
    python scripts/run_all_e2e_tests.py --only browser_dizibox_5_episodes,fibonacci
    python scripts/run_all_e2e_tests.py --include-guarded
    python scripts/run_all_e2e_tests.py --sizes small,medium
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import request as urlrequest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "agent_control.db"
ENV_PATH = ROOT / ".env"
RESULTS_ROOT = ROOT / ".agent_control" / "e2e_results"
CASES_PATH = ROOT / "e2e" / "all_cases.json"

ADMIN_BASE = "http://127.0.0.1:8765"
LOCALDEPLOY_BASE = "http://127.0.0.1:8000"

# Tags whose cases need external credentials or services we don't have local copies of.
GUARDED_TAGS = {"codex", "copilot", "external", "quota", "limit", "presentation"}

# Per-case absolute ceiling regardless of what the JSON declares. Protects the
# whole run from getting stuck on one runaway case.
HARD_CEILING_S = 900

# The runner must wait AT LEAST this long after the case's declared timeout so
# the worker's own per-task budget (settings.limits.task_budget_seconds,
# default 600s) has time to fire. Without this safety margin the runner
# moves on while the worker is still busy, blocking the next case in queue.
WORKER_BUDGET_SAFETY_S = 640


# ---------- HTTP / DB helpers ----------


def admin_get(path: str, timeout: int = 10) -> Any:
    url = f"{ADMIN_BASE}{path}"
    with urlrequest.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def admin_summary() -> dict:
    return admin_get("/admin/api/summary?task_limit=30")


def admin_trace(task_id: str) -> dict:
    return admin_get(f"/admin/api/tasks/{task_id}/trace")


def ping(url: str, timeout: float = 2.0) -> bool:
    try:
        with urlrequest.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def clear_conversation_memory() -> None:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM conversation_memory")
        conn.commit()
        conn.close()
    except Exception:
        pass


def _latest_classifier_verdict_for_message(message: str) -> tuple[bool, str] | None:
    """Find the most recent ``message_classified`` audit event whose ``text``
    matches ``message`` (within the last 5 minutes). Returns
    ``(is_task, reason)`` or ``None`` if no matching event was logged.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT payload_json FROM audit_events "
            "WHERE event_type='message_classified' "
            "AND created_at >= datetime('now','-5 minute') "
            "ORDER BY created_at DESC LIMIT 25"
        )
        for (payload_json,) in c.fetchall():
            try:
                payload = json.loads(payload_json)
            except Exception:
                continue
            text = str(payload.get("text") or "").strip()
            if text and text == message.strip():
                return bool(payload.get("is_task")), str(payload.get("reason") or "")
        return None
    except Exception:
        return None
    finally:
        try:
            conn.close()  # type: ignore[has-type]
        except Exception:
            pass


def force_fail_task(task_id: str, reason: str) -> bool:
    """Mark a task FAILED directly in the DB so the worker stops trying.

    Used by the runner when its own wait deadline has expired but the task is
    still in a workable status. Without this, the worker would keep grinding
    on the task and block every queued case behind it.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "UPDATE tasks "
            "SET status='failed', metadata_json = COALESCE(metadata_json, '{}') "
            "WHERE id=? AND status NOT IN ('completed','failed','blocked','cancelled')",
            (task_id,),
        )
        changed = c.rowcount
        conn.commit()
        conn.close()
        return changed > 0
    except Exception:
        return False


def fetch_classifier_verdict_for_text(message: str) -> dict | None:
    """Find the latest message_classified audit event matching ``message``.

    Returns a small dict of the most useful classifier fields, or None if not
    found. Used to surface route + reason + confidence in per-stage diagnostics,
    so failures at the classifier are visible without grepping audit.json.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT payload_json FROM audit_events "
            "WHERE event_type='message_classified' "
            "AND created_at >= datetime('now','-10 minute') "
            "ORDER BY created_at DESC LIMIT 25"
        )
        for (payload_json,) in c.fetchall():
            try:
                p = json.loads(payload_json)
            except Exception:
                continue
            text = str(p.get("text") or "").strip()
            if text and text == message.strip():
                return {
                    "is_task": p.get("is_task"),
                    "route": (p.get("intent") or {}).get("route"),
                    "task_type": p.get("task_type"),
                    "confidence": p.get("confidence"),
                    "reason": (p.get("reason") or "")[:400],
                }
        return None
    except Exception:
        return None
    finally:
        try:
            conn.close()  # type: ignore[has-type]
        except Exception:
            pass


def fetch_task_audit(task_id: str) -> list[dict]:
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT actor, event_type, payload_json, created_at "
            "FROM audit_events WHERE task_id=? ORDER BY created_at ASC",
            (task_id,),
        )
        out: list[dict] = []
        for actor, etype, payload_json, created_at in c.fetchall():
            try:
                payload = json.loads(payload_json)
            except Exception:
                payload = {"raw": payload_json}
            out.append({"time": created_at, "actor": actor, "event": etype, "payload": payload})
        conn.close()
        return out
    except Exception:
        return []


# ---------- Fixtures ----------


def _ensure_fixtures_path() -> None:
    """Make ``e2e/fixtures.py`` importable from this script."""
    e2e_dir = str(ROOT / "e2e")
    if e2e_dir not in sys.path:
        sys.path.insert(0, e2e_dir)


def prepare_fixtures(start_web: bool) -> dict[str, str]:
    """Build the file/folder fixtures cases depend on. Returns template values.

    On any error, fall back to a minimal dict so the run can still proceed with
    cases that don't need fixtures.
    """
    _ensure_fixtures_path()
    try:
        from fixtures import prepare_fixtures as _prep  # type: ignore[import]
        fx = _prep(start_web_server=start_web)
        return dict(fx.values)
    except Exception as exc:
        print(f"  [fixtures] WARN: fallback (no fixtures): {exc}")
        return {}


def render_text(text: str, fixtures: dict[str, str]) -> str:
    out = text
    for k, v in fixtures.items():
        out = out.replace(f"{{{{{k}}}}}", v)
    return out


# ---------- Case selection ----------


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def is_guarded(case: dict) -> bool:
    return bool(set(case.get("tags") or []) & GUARDED_TAGS)


def select_cases(
    cases: list[dict],
    *,
    only: set[str],
    skip: set[str],
    sizes: set[str],
    include_guarded: bool,
) -> list[dict]:
    selected: list[dict] = []
    for c in cases:
        cid = c.get("id")
        if only and cid not in only:
            continue
        if cid in skip:
            continue
        if sizes and (c.get("size") or "small") not in sizes:
            continue
        if not include_guarded and is_guarded(c):
            continue
        selected.append(c)
    return selected


# ---------- Test execution ----------


@dataclass
class TurnResult:
    """One Telegram turn — initial message OR a follow-up."""
    label: str = ""                       # "initial" or follow-up id
    message: str = ""
    task_id: str | None = None
    final_status: str | None = None
    duration_s: float = 0.0
    replan_count: int = 0
    synth_answer: str | None = None
    last_tool_output: str | None = None
    last_worker_error: str | None = None
    planning_error: str | None = None
    last_replan_reason: str | None = None
    fulfillment_gap: str | None = None
    plan_steps: list[dict] = field(default_factory=list)
    status_transitions: list[dict] = field(default_factory=list)
    audit_event_count: int = 0
    error: str | None = None              # exception in the runner itself
    # Captured from the classifier's audit event for this turn's message —
    # surfaced in timeline.txt and diagnosis.md so model-judgment failures
    # don't require grepping audit.json.
    classifier_verdict: dict | None = None
    forced_failed_by_runner: bool = False


@dataclass
class StageResult:
    case_id: str
    category: str
    size: str
    message: str
    source_file: str
    started_at: str = ""
    ended_at: str = ""
    total_duration_s: float = 0.0
    turns: list[TurnResult] = field(default_factory=list)
    passed: bool = False
    failure_reason: str | None = None
    runner_exception: str | None = None    # set if the stage itself crashed


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


async def _send_message(message: str) -> int:
    from telethon import TelegramClient

    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    bot_username = os.environ["TELEGRAM_BOT_USERNAME"]
    session = str(ROOT / ".agent_control" / "telegram_e2e_user")

    client = TelegramClient(session, api_id, api_hash)
    async with client:
        peer = await client.get_entity(bot_username)
        sent = await client.send_message(peer, message)
        return sent.id


async def _find_new_task(known_ids: set[str], spawn_timeout_s: int = 80) -> str | None:
    deadline = time.monotonic() + spawn_timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        try:
            data = admin_summary()
        except Exception:
            continue
        for t in data.get("tasks", []):
            if t["id"] not in known_ids:
                return t["id"]
    return None


def _diagnose_turn(case: dict, turn: TurnResult, *, is_followup: bool) -> tuple[bool, str | None]:
    """Return (passed, reason). Uses case assertions when present, plus generic checks."""
    if turn.error:
        return False, f"runner_error: {turn.error[:200]}"

    status = (turn.final_status or "").lower()
    assertions = (case.get("assertions") or {})
    final_status_allowed = assertions.get("final_status_in")
    if final_status_allowed and status not in [s.lower() for s in final_status_allowed]:
        why_parts: list[str] = [f"status={status} not in {final_status_allowed}"]
        if turn.last_worker_error:
            why_parts.append(f"worker_error: {turn.last_worker_error[:200]}")
        if turn.planning_error:
            why_parts.append(f"planning_error: {turn.planning_error[:200]}")
        if turn.last_replan_reason:
            why_parts.append(f"last_replan_reason: {turn.last_replan_reason[:200]}")
        if turn.fulfillment_gap:
            why_parts.append(f"fulfillment_gap: {turn.fulfillment_gap}")
        return False, "; ".join(why_parts)

    if status not in {"completed", "blocked"}:
        return False, f"unexpected terminal status: {status or '(timed out)'}"

    return True, None


async def _run_turn(label: str, message: str, max_seconds: int) -> TurnResult:
    """Send one message + poll the spawned task until terminal/timeout. All errors caught."""
    turn = TurnResult(label=label, message=message)
    start_mono = time.monotonic()
    try:
        # Snapshot known task ids so we can detect the new one.
        try:
            known = {t["id"] for t in admin_summary().get("tasks", [])}
        except Exception as exc:
            turn.error = f"admin API unreachable before send: {exc}"
            turn.duration_s = round(time.monotonic() - start_mono, 1)
            return turn

        try:
            await _send_message(message)
        except Exception as exc:
            turn.error = f"telegram send failed: {exc}"
            turn.duration_s = round(time.monotonic() - start_mono, 1)
            return turn

        task_id = await _find_new_task(known)
        if not task_id:
            # Distinguish "classifier said is_task=False" from "polling didn't see the message"
            # so the report can blame the right component.
            classifier_verdict = _latest_classifier_verdict_for_message(message)
            if classifier_verdict is not None:
                is_task, reason = classifier_verdict
                if is_task is False:
                    turn.error = (
                        f"classifier ruled is_task=False (no persisted task spawned). "
                        f"reason: {reason[:200]}"
                    )
                else:
                    turn.error = (
                        f"classifier said is_task=True but no task spawned within 80s "
                        f"(intake or worker layer dropped it). reason: {reason[:200]}"
                    )
            else:
                turn.error = "no task spawned within 80s — message never classified (telegram intake stuck?)"
            # Also capture the full classifier verdict for the diagnosis dump.
            turn.classifier_verdict = fetch_classifier_verdict_for_text(message)
            turn.duration_s = round(time.monotonic() - start_mono, 1)
            return turn
        turn.task_id = task_id
        print(f"    [task] {task_id}")

        last_status = None
        last_step = None
        last_replan = None
        # Wait at least long enough for the worker's per-task budget to fire,
        # then add a small headroom. Cap at HARD_CEILING_S as a hard backstop.
        ceiling = min(max(max_seconds, WORKER_BUDGET_SAFETY_S) + 30, HARD_CEILING_S)
        deadline = start_mono + ceiling
        reached_terminal = False
        while time.monotonic() < deadline:
            try:
                trace = admin_trace(task_id)
            except Exception:
                await asyncio.sleep(3)
                continue
            task = trace.get("task") or {}
            meta = task.get("metadata") or {}
            status = task.get("status")
            step = meta.get("last_tool_name") or ""
            replan = int(meta.get("replan_count") or 0)
            if status != last_status or step != last_step or replan != last_replan:
                elapsed = round(time.monotonic() - start_mono, 1)
                turn.status_transitions.append({
                    "at_s": elapsed,
                    "status": status,
                    "tool": step,
                    "replan_count": replan,
                })
                synth_present = bool(meta.get("synthesized_answer"))
                print(f"    [{elapsed:>6.1f}s] status={status:12} tool={step:22} replan={replan} synth={synth_present}")
                last_status = status
                last_step = step
                last_replan = replan
            if status in {"completed", "failed", "blocked", "cancelled"}:
                reached_terminal = True
                break
            await asyncio.sleep(3)

        # If the wait expired and the task is STILL non-terminal, force it
        # FAILED. The worker is presumably still grinding on it; without this
        # the next queued case would be starved indefinitely.
        if not reached_terminal:
            forced = force_fail_task(task_id, "runner deadline exceeded")
            if forced:
                turn.forced_failed_by_runner = True
                elapsed = round(time.monotonic() - start_mono, 1)
                print(f"    [{elapsed:>6.1f}s] FORCE-FAILED stuck task to unblock queue")

        # Capture the classifier verdict for this turn's message regardless
        # of pass/fail, so the timeline always shows what the classifier said.
        turn.classifier_verdict = fetch_classifier_verdict_for_text(message)

        # Final capture
        try:
            trace = admin_trace(task_id)
        except Exception:
            trace = {}
        task = trace.get("task") or {}
        meta = task.get("metadata") or {}
        plan = trace.get("plan") or {}
        last_tool_result = meta.get("last_tool_result") or {}
        out = last_tool_result.get("output") or {}

        turn.final_status = task.get("status")
        turn.synth_answer = meta.get("synthesized_answer")
        turn.replan_count = int(meta.get("replan_count") or 0)
        turn.last_worker_error = meta.get("last_worker_error") or None
        turn.planning_error = meta.get("planning_error") or None
        turn.last_replan_reason = meta.get("last_replan_reason") or None
        turn.fulfillment_gap = meta.get("fulfillment_gap") or None
        turn.last_tool_output = (
            out.get("summary")
            or out.get("stdout")
            or out.get("text")
            or out.get("final_summary")
            or (json.dumps(out)[:600] if isinstance(out, dict) else None)
        )
        turn.plan_steps = [
            {
                "tool_name": s.get("tool_name"),
                "operation": (s.get("tool_input") or {}).get("operation"),
                "status": s.get("status"),
                "title": s.get("title"),
            }
            for s in (plan.get("steps") or [])
        ]
        turn.duration_s = round(time.monotonic() - start_mono, 1)
        turn.audit_event_count = len(fetch_task_audit(task_id))
    except Exception as exc:
        turn.error = f"runner exception: {exc}\n{traceback.format_exc()[-1500:]}"
        turn.duration_s = round(time.monotonic() - start_mono, 1)
    return turn


async def _run_one(case: dict, fixtures: dict[str, str]) -> StageResult:
    stage = StageResult(
        case_id=case.get("id") or "(unknown)",
        category=(case.get("tags") or [None])[0] or "uncategorized",
        size=case.get("size") or "small",
        message=case.get("message") or "",
        source_file=case.get("source_file") or "?",
    )
    stage.started_at = datetime.now().isoformat(timespec="seconds")
    start_mono = time.monotonic()

    print(f"\n{'=' * 78}")
    print(f"  {stage.case_id}  [{stage.size}/{stage.source_file}]")
    print(f"  > {stage.message[:140]}")
    print(f"{'=' * 78}")

    try:
        # Clear memory at the start of each top-level stage, EXCEPT when this
        # case is explicitly a follow-up-with-memory case.
        if not any(t in (case.get("tags") or []) for t in ("followup", "memory")):
            clear_conversation_memory()

        rendered_message = render_text(stage.message, fixtures)
        max_seconds = int(case.get("timeout_seconds") or 360)

        initial = await _run_turn("initial", rendered_message, max_seconds=max_seconds)
        stage.turns.append(initial)
        passed, reason = _diagnose_turn(case, initial, is_followup=False)

        # Follow-ups (keep memory) — declared in JSON
        for fu in (case.get("follow_ups") or []):
            fu_msg = render_text(str(fu.get("message") or ""), fixtures)
            fu_to = int(fu.get("timeout_seconds") or max_seconds)
            print(f"    [follow-up] {fu.get('id')}: {fu_msg[:120]}")
            fu_turn = await _run_turn(f"followup:{fu.get('id')}", fu_msg, max_seconds=fu_to)
            stage.turns.append(fu_turn)
            fu_ok, fu_reason = _diagnose_turn(case, fu_turn, is_followup=True)
            if passed and not fu_ok:
                passed, reason = False, f"follow-up `{fu.get('id')}` failed: {fu_reason}"

        stage.passed = passed
        stage.failure_reason = reason
    except Exception as exc:
        stage.runner_exception = f"{exc}\n{traceback.format_exc()[-1500:]}"
        stage.failure_reason = f"runner exception: {exc}"
        stage.passed = False
    finally:
        stage.total_duration_s = round(time.monotonic() - start_mono, 1)
        stage.ended_at = datetime.now().isoformat(timespec="seconds")
    return stage


# ---------- Output / reporting ----------


def _write_stage_artifacts(stage_dir: Path, case: dict, stage: StageResult) -> None:
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "result.json").write_text(
            json.dumps({"case": case, "result": asdict(stage)}, indent=2, default=str),
            encoding="utf-8",
        )

        timeline: list[str] = [
            f"# {stage.case_id}  [{stage.size}]",
            "",
            f"Source:        {stage.source_file}",
            f"Final:         {'PASS' if stage.passed else 'FAIL'}",
            f"Duration:      {stage.total_duration_s}s",
            f"Reason:        {stage.failure_reason or 'ok'}",
            "",
        ]
        for turn in stage.turns:
            timeline.extend([
                "",
                f"## Turn: {turn.label}",
                f"  Message:     {turn.message[:200]}",
                f"  Task ID:     {turn.task_id}",
                f"  Status:      {turn.final_status}",
                f"  Replans:     {turn.replan_count}",
                f"  Duration:    {turn.duration_s}s",
                f"  Audit ev:    {turn.audit_event_count}",
                f"  Force-failed by runner: {turn.forced_failed_by_runner}",
            ])
            if turn.classifier_verdict:
                v = turn.classifier_verdict
                timeline.append(
                    f"  Classifier:  is_task={v.get('is_task')} "
                    f"route={v.get('route')} conf={v.get('confidence')} "
                    f"task_type={v.get('task_type')}"
                )
                timeline.append(f"    reason: {(v.get('reason') or '')[:300]}")
            if turn.error:
                timeline.extend([f"  Runner err:  {turn.error[:400]}"])
            timeline.append("  Transitions:")
            for ev in turn.status_transitions:
                timeline.append(
                    f"    [{ev['at_s']:>6.1f}s] status={ev['status']:12} "
                    f"tool={ev['tool']:22} replan={ev['replan_count']}"
                )
            timeline.append("  Plan steps:")
            for s in turn.plan_steps:
                timeline.append(f"    - {s.get('tool_name')} | op={s.get('operation')} | {s.get('title')}")
            timeline.extend([
                "  Synthesized answer:",
                "    " + (turn.synth_answer or "(none)")[:1200].replace("\n", "\n    "),
                "  Last tool output:",
                "    " + (turn.last_tool_output or "(none)")[:800].replace("\n", "\n    "),
            ])
        (stage_dir / "timeline.txt").write_text("\n".join(timeline), encoding="utf-8")

        # Aggregated audit across all turns
        combined_audit: dict[str, list[dict]] = {}
        for turn in stage.turns:
            if turn.task_id:
                combined_audit[turn.task_id] = fetch_task_audit(turn.task_id)
        if combined_audit:
            (stage_dir / "audit.json").write_text(
                json.dumps(combined_audit, indent=2, default=str),
                encoding="utf-8",
            )

        if not stage.passed:
            diag = [
                f"# Why `{stage.case_id}` failed",
                "",
                f"Reason: {stage.failure_reason}",
                "",
            ]
            if stage.runner_exception:
                diag.extend(["## Runner exception", "```", stage.runner_exception, "```", ""])
            for turn in stage.turns:
                diag.extend([
                    f"## Turn `{turn.label}`",
                    f"- final_status: `{turn.final_status}`",
                    f"- task_id: `{turn.task_id}`",
                    f"- duration: {turn.duration_s}s",
                    f"- replan_count: {turn.replan_count}",
                    f"- force_failed_by_runner: `{turn.forced_failed_by_runner}`",
                ])
                if turn.classifier_verdict:
                    v = turn.classifier_verdict
                    diag.extend([
                        "",
                        "**Classifier verdict for this turn:**",
                        f"- is_task: `{v.get('is_task')}`",
                        f"- route: `{v.get('route')}`",
                        f"- task_type: `{v.get('task_type')}`",
                        f"- confidence: `{v.get('confidence')}`",
                        f"- reason: {(v.get('reason') or '')[:400]}",
                    ])
                if turn.error:
                    diag.extend(["", "**runner_error:**", "```", turn.error[:800], "```"])
                if turn.last_worker_error:
                    diag.extend(["", "**last_worker_error:**", "```", turn.last_worker_error, "```"])
                if turn.planning_error:
                    diag.extend(["", "**planning_error:**", "```", turn.planning_error, "```"])
                if turn.last_replan_reason:
                    diag.extend(["", "**last_replan_reason:**", "```", turn.last_replan_reason, "```"])
                if turn.fulfillment_gap:
                    diag.extend(["", "**fulfillment_gap:**", "```", turn.fulfillment_gap, "```"])
                diag.extend(["", "**Plan steps:**"])
                for s in turn.plan_steps:
                    diag.append(f"- `{s.get('tool_name')}` op=`{s.get('operation')}` — {s.get('title')}")
                diag.extend([
                    "",
                    "**Last tool output (800 chars):**",
                    "```",
                    (turn.last_tool_output or "(none)")[:800],
                    "```",
                    "",
                ])
            diag.append("See `audit.json` in this folder for the full event trail.")
            (stage_dir / "diagnosis.md").write_text("\n".join(diag), encoding="utf-8")
    except Exception as exc:
        # NEVER let report-writing crash the run.
        print(f"    [warn] failed to write stage artifacts: {exc}")


def _write_summary(run_dir: Path, results: list[StageResult]) -> None:
    try:
        rows: list[str] = []
        for r in results:
            icon = "✅" if r.passed else "❌"
            outcome = "PASS" if r.passed else f"FAIL ({(r.failure_reason or '')[:80]})"
            final_statuses = ",".join(t.final_status or "-" for t in r.turns)
            rows.append(
                f"| {icon} | `{r.case_id}` | {r.size} | {final_statuses} | "
                f"{r.total_duration_s:.1f}s | {sum(t.replan_count for t in r.turns)} | {outcome} |"
            )
        md = [
            f"# YBM E2E Run — {run_dir.name}",
            "",
            f"Started: {results[0].started_at if results else '—'}",
            f"Total:   {len(results)}",
            f"Passed:  {sum(1 for r in results if r.passed)}",
            f"Failed:  {sum(1 for r in results if not r.passed)}",
            "",
            "| | Case | Size | Final statuses | Duration | Replans | Outcome |",
            "|---|---|---|---|---|---|---|",
            *rows,
            "",
            "Per-stage artifacts: `<index>_<case_id>/` (timeline.txt, audit.json, diagnosis.md if failed)",
        ]
        (run_dir / "summary.md").write_text("\n".join(md), encoding="utf-8")
        (run_dir / "summary.json").write_text(
            json.dumps([asdict(r) for r in results], indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"  [warn] failed to write summary: {exc}")


def _preflight() -> list[str]:
    issues: list[str] = []
    if not ENV_PATH.exists():
        issues.append(f".env not found at {ENV_PATH}")
    if not DB_PATH.exists():
        issues.append(f"agent_control.db not found at {DB_PATH}")
    if not CASES_PATH.exists():
        issues.append(f"case catalogue not found at {CASES_PATH}")
    if not ping(f"{ADMIN_BASE}/admin/api/summary?task_limit=1"):
        issues.append(f"YBM admin API not responding at {ADMIN_BASE}")
    if not ping(f"{LOCALDEPLOY_BASE}/v1/models"):
        issues.append(f"LocalDeploy not responding at {LOCALDEPLOY_BASE}")
    return issues


def _warn_if_chrome_down() -> None:
    """Browser cases need Chrome with remote debugging on port 9222.

    The browser adapter auto-launches Chrome on first use, but a heads-up here
    lets the user fix it before half the suite fails. We do NOT bail — many
    cases don't need a browser, so we just warn.
    """
    if not ping("http://127.0.0.1:9222/json/version", timeout=2.0):
        try:
            _try_launch_chrome()
        except Exception as exc:
            print(f"  [chrome] WARN: not on :9222 and auto-launch failed: {exc}")
            print(f"           browser cases will fail. start Chrome with:")
            print(f"           chrome --remote-debugging-port=9222 --remote-allow-origins=*")
            return
        # Re-check after a short pause to give Chrome a moment to bind.
        import time as _t
        for _ in range(15):
            _t.sleep(1)
            if ping("http://127.0.0.1:9222/json/version", timeout=1.0):
                print("  [chrome] auto-launched, remote debugging on :9222")
                return
        print("  [chrome] WARN: launched but not yet responding on :9222")
    else:
        print("  [chrome] already running with remote debugging on :9222")


def _try_launch_chrome() -> None:
    """Best-effort Chrome launch with remote debugging. Windows-focused paths."""
    import os
    import shutil
    import subprocess
    candidates = [
        os.environ.get("CHROME_PATH"),
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    exe = next((p for p in candidates if p and Path(p).exists()), None)
    if not exe:
        raise RuntimeError("Chrome executable not found")
    user_data_dir = ROOT / ".agent_control" / "browser" / "chrome-profile"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            exe,
            "--remote-debugging-port=9222",
            "--remote-allow-origins=*",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )


# ---------- Main ----------


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--skip", default="", help="comma-separated case ids to skip")
    parser.add_argument("--sizes", default="", help="comma-separated sizes: small,medium,long-running")
    parser.add_argument("--include-guarded", action="store_true",
                        help="include codex/copilot/quota/limit cases (usually need external creds)")
    parser.add_argument("--no-web-fixture", action="store_true",
                        help="skip the local fixture web server (some cases will be skipped/fail)")
    args = parser.parse_args()

    _load_env()
    issues = _preflight()
    if issues:
        print("Preflight failed — start the missing services first:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    cases_all = load_cases()
    selected = select_cases(
        cases_all,
        only={s.strip() for s in args.only.split(",") if s.strip()},
        skip={s.strip() for s in args.skip.split(",") if s.strip()},
        sizes={s.strip() for s in args.sizes.split(",") if s.strip()},
        include_guarded=args.include_guarded,
    )

    # Pre-flight inventory: what's actually going to run, how long it might take,
    # what skipped. This is the single most useful thing to glance at before
    # walking away from the machine.
    from collections import Counter
    size_count = Counter(c.get("size") or "small" for c in selected)
    declared_total_s = sum(int(c.get("timeout_seconds") or 360) for c in selected)
    print(f"Loaded {len(cases_all)} cases. Selected {len(selected)} (guarded={'included' if args.include_guarded else 'skipped'}).")
    print(f"  By size: " + ", ".join(f"{size}={n}" for size, n in size_count.items()))
    print(f"  Sum of declared timeouts: {declared_total_s}s "
          f"(~{declared_total_s // 60}m; actual will differ from this upper bound)")
    skipped = [c for c in cases_all if c not in selected]
    if skipped:
        skipped_size = Counter(c.get("size") or "small" for c in skipped)
        print(f"  Skipped {len(skipped)}: " + ", ".join(f"{size}={n}" for size, n in skipped_size.items()))

    run_dir = RESULTS_ROOT / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Writing results to: {run_dir}")

    needs_web = any(
        "{{fixture_base_url}}" in (c.get("message") or "")
        or "{{episode_url}}" in (c.get("message") or "")
        or "{{form_url}}" in (c.get("message") or "")
        for c in selected
    )
    fixtures = prepare_fixtures(start_web=needs_web and not args.no_web_fixture)

    # Browser cases need Chrome with remote debugging on :9222. Warn (and
    # try to auto-launch) before the run rather than after we've burned 20min
    # on a queue full of browser cases that all fail at the adapter.
    needs_browser = any(
        any(t in (c.get("tags") or []) for t in ("browser",))
        or (c.get("tools_required") and any("browser" in t for t in c["tools_required"]))
        for c in selected
    )
    if needs_browser:
        _warn_if_chrome_down()

    results: list[StageResult] = []
    suite_start = time.monotonic()
    total = len(selected)
    for idx, case in enumerate(selected, start=1):
        stage_dir = run_dir / f"{idx:02d}_{case.get('id','case')}"

        # Progress banner BEFORE each case so the user knows where we are.
        passed_so_far = sum(1 for r in results if r.passed)
        failed_so_far = len(results) - passed_so_far
        suite_elapsed = time.monotonic() - suite_start
        eta_s = _estimate_eta(suite_elapsed, idx - 1, total)
        bar = _progress_bar(idx - 1, total, width=24)
        print()
        print(f"{bar} {idx:>3}/{total} ({(idx-1)*100//max(1,total)}%) "
              f"| pass {passed_so_far} fail {failed_so_far} "
              f"| elapsed {_fmt_duration(suite_elapsed)} "
              f"| ETA {eta_s}")

        try:
            stage = await _run_one(case, fixtures)
        except Exception as exc:
            # The outermost net: even if _run_one itself blows up we keep going.
            stage = StageResult(
                case_id=case.get("id") or "(unknown)",
                category="uncategorized",
                size=case.get("size") or "small",
                message=case.get("message") or "",
                source_file=case.get("source_file") or "?",
                runner_exception=f"{exc}\n{traceback.format_exc()[-1500:]}",
                failure_reason=f"outer runner exception: {exc}",
            )
        results.append(stage)
        _write_stage_artifacts(stage_dir, case, stage)
        _write_summary(run_dir, results)
        status = "PASS" if stage.passed else "FAIL"
        print(f"  --> {status}  ({stage.total_duration_s:.1f}s)  {stage.failure_reason or 'ok'}")

    print(f"\n{'=' * 78}")
    passed = sum(1 for r in results if r.passed)
    total_time = _fmt_duration(time.monotonic() - suite_start)
    print(f"{_progress_bar(len(results), total, width=24)} {len(results)}/{total} done")
    print(f"Summary: {run_dir / 'summary.md'}")
    print(f"Passed {passed} / {len(results)}  |  total {total_time}")
    return 0 if passed == len(results) else 2


def _progress_bar(done: int, total: int, *, width: int = 24) -> str:
    if total <= 0:
        return "[" + " " * width + "]"
    filled = int(round(width * done / total))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _estimate_eta(elapsed_s: float, done: int, total: int) -> str:
    if done <= 0 or done >= total:
        return "—"
    per_case = elapsed_s / done
    remaining = (total - done) * per_case
    return _fmt_duration(remaining)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
