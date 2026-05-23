from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import threading
import time
from typing import Any
from urllib import error, parse, request



ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "e2e" / "cases.json"
DEFAULT_LOG_ROOT = ROOT / ".agent_control" / "live_e2e_runs"
TERMINAL_STATUSES = {"completed", "blocked", "cancelled", "failed"}
GUARDED_TAGS = {"external_agent", "long", "fault_injection_needed"}


def main() -> None:
    args = _parse_args()
    cases = _load_cases(args.cases)
    if args.list_cases:
        _print_cases(cases)
        return
    selected = _select_cases(cases, args)
    if not selected:
        print("No cases selected. Use --list-cases, --case <id>, --tag <tag>, or --all.")
        return
    if args.dry_run:
        fixtures = prepare_fixtures(start_web_server=False)
        for case in selected:
            print(f"[{case['id']}] {_render_message(case, fixtures)}")
            for follow_up in case.get("follow_ups") or []:
                print(f"  -> {_render_text(str(follow_up['message']), fixtures)}")
        return
    _require_live_args(args)
    asyncio.run(run_cases(selected, args))


async def run_cases(cases: list[dict[str, Any]], args: argparse.Namespace) -> None:
    log_root = Path(args.log_root).expanduser().resolve() / _timestamp()
    log_root.mkdir(parents=True, exist_ok=True)
    fixtures = prepare_fixtures(start_web_server=any("local_web_fixture" in case.get("setup", []) for case in cases))
    admin = AdminClient(args.backend_url.rstrip("/"), args.admin_token or os.getenv("AGENT_ADMIN_TOKEN") or "")
    preflight = admin.preflight()
    _write_json(log_root / "preflight.json", {"backend_url": args.backend_url, "fixtures": fixtures.public(), "admin": preflight})

    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise SystemExit("Telethon is required for live Telegram E2E. Install with: pip install telethon") from exc

    session = args.telegram_session or os.getenv("TELEGRAM_USER_SESSION") or str(ROOT / ".agent_control" / "telegram_e2e_user")
    client = TelegramClient(session, int(args.telegram_api_id), args.telegram_api_hash)
    results: list[dict[str, Any]] = []
    completed: dict[str, dict[str, Any]] = {}
    async with client:
        peer = await client.get_entity(args.bot_username)
        for case in cases:
            if _guarded(case) and not args.include_guarded:
                result = _skipped(case, "guarded case; rerun with --include-guarded")
                _write_case_log(log_root, result)
                results.append(result)
                continue
            missing_dependency = next((item for item in case.get("depends_on", []) if completed.get(item, {}).get("status") != "passed"), None)
            if missing_dependency:
                result = _skipped(case, f"dependency did not pass: {missing_dependency}")
                _write_case_log(log_root, result)
                results.append(result)
                continue
            result = await run_case(case, fixtures, admin, client, peer)
            _write_case_log(log_root, result)
            results.append(result)
            completed[case["id"]] = result
            pause_seconds = float(case.get("pause_after_seconds") or args.pause_between_seconds or 0)
            if pause_seconds > 0:
                await asyncio.sleep(pause_seconds)

    summary = _summary(results)
    _write_json(log_root / "summary.json", summary)
    (log_root / "summary.md").write_text(_summary_markdown(summary, results), encoding="utf-8")
    print(f"Live E2E logs: {log_root}")
    print(f"Passed: {summary['passed']}  Failed: {summary['failed']}  Skipped: {summary['skipped']}")
    if summary["failed"]:
        raise SystemExit(1)


async def run_case(case: dict[str, Any], fixtures: "Fixtures", admin: "AdminClient", client: Any, peer: Any) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    resolved_message = _render_message(case, fixtures)
    before_summary = admin.summary()
    if case.get("input_kind") == "voice":
        voice_path = Path(case.get("voice_file") or fixtures.values.get("voice_ogg_path") or "")
        if voice_path.exists():
            sent = await client.send_file(peer, str(voice_path), voice_note=True)
        else:
            sent = await client.send_file(peer, io.BytesIO(_fake_ogg_voice_bytes()), file_name="voice.ogg", voice_note=True)
    else:
        sent = await client.send_message(peer, resolved_message)
    telegram_replies: list[dict[str, Any]] = []
    task_id: str | None = None
    failure_reason = None
    trace: dict[str, Any] | None = None
    schedule_wait_result: dict[str, Any] | None = None

    try:
        spawn_timeout = min(
            int(case.get("timeout_seconds", 300)),
            int(case.get("spawn_timeout_seconds") or 180),
        )
        spawn_replies = await _wait_for_task_spawn_replies(
            client,
            peer,
            sent.id,
            timeout_seconds=spawn_timeout,
        )
        telegram_replies.extend(spawn_replies)
        task_id = _extract_task_id(spawn_replies)
        if task_id:
            trace = await _wait_for_terminal_trace(admin, task_id, timeout_seconds=int(case.get("timeout_seconds", 300)))
        else:
            failure_reason = "No task id was returned by Telegram. The message may have been answered as non-task or intake failed."
        assertions = case.get("assertions") or {}
        telegram_replies = await _wait_for_replies(
            client,
            peer,
            sent.id,
            timeout_seconds=int(case.get("reply_timeout_seconds") or 45),
            min_count=len(spawn_replies) + (1 if task_id else 0),
            require_media=bool(assertions.get("telegram_media_min")),
            contains_any=assertions.get("bot_reply_contains_any") or [],
        )
        if task_id and case.get("post_wait_schedule_seconds"):
            schedule_wait_result = await _wait_for_scheduled_task(admin, trace, int(case["post_wait_schedule_seconds"]))
    except Exception as exc:
        failure_reason = str(exc)

    after_summary = admin.summary()
    validation = _validate_case(case, trace, telegram_replies, before_summary, after_summary, schedule_wait_result)
    follow_up_results: list[dict[str, Any]] = []
    for follow_up in case.get("follow_ups") or []:
        follow_up_result = await run_follow_up(case, follow_up, fixtures, admin, client, peer)
        follow_up_results.append(follow_up_result)
        if follow_up_result["status"] != "passed":
            validation["errors"].extend(f"follow-up {follow_up_result['id']}: {error}" for error in follow_up_result["validation"]["errors"])
            if follow_up_result.get("failure_reason"):
                validation["errors"].append(f"follow-up {follow_up_result['id']}: {follow_up_result['failure_reason']}")
    validation["ok"] = not validation["errors"]
    status = "passed" if validation["ok"] and not failure_reason else "failed"
    if failure_reason:
        validation["errors"].append(failure_reason)

    payload = {
        "id": case["id"],
        "requirement": case.get("requirement"),
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "input_message": case.get("message"),
        "resolved_message": resolved_message,
        "telegram_sent": _telegram_message(sent),
        "telegram_replies": telegram_replies,
        "task_id": task_id,
        "final_status": ((trace or {}).get("task") or {}).get("status"),
        "validation": validation,
        "failure_reason": "; ".join(validation["errors"]) if validation["errors"] else failure_reason,
        "route_decision": _route_decision(trace),
        "plan_steps": _plan_steps(trace),
        "tool_invocations": (trace or {}).get("tool_invocations") or [],
        "artifacts": (trace or {}).get("artifacts") or [],
        "signals": (trace or {}).get("signals") or [],
        "timeline": (trace or {}).get("timeline") or [],
        "trace": trace,
        "follow_up_results": follow_up_results,
        "schedule_wait_result": schedule_wait_result,
        "local_paths": _local_paths(trace),
        "urls": _urls(trace, telegram_replies),
        "fixtures": fixtures.public(),
        "admin_before": _summary_excerpt(before_summary),
        "admin_after": _summary_excerpt(after_summary),
    }
    print(f"[{status.upper()}] {case['id']}: {payload['failure_reason'] or 'ok'}")
    return payload


async def run_follow_up(
    parent_case: dict[str, Any],
    follow_up: dict[str, Any],
    fixtures: "Fixtures",
    admin: "AdminClient",
    client: Any,
    peer: Any,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    message = _render_text(str(follow_up["message"]), fixtures)
    sent = await client.send_message(peer, message)
    replies: list[dict[str, Any]] = []
    task_id: str | None = None
    trace: dict[str, Any] | None = None
    failure_reason = None
    try:
        replies = await _wait_for_replies(
            client,
            peer,
            sent.id,
            timeout_seconds=int(follow_up.get("reply_timeout_seconds") or parent_case.get("reply_timeout_seconds") or 90),
            min_count=1,
            require_media=bool((follow_up.get("assertions") or {}).get("telegram_media_min")),
            contains_any=(follow_up.get("assertions") or {}).get("bot_reply_contains_any") or [],
        )
        task_id = _extract_task_id(replies)
        if task_id:
            trace = await _wait_for_terminal_trace(
                admin,
                task_id,
                timeout_seconds=int(follow_up.get("timeout_seconds") or parent_case.get("timeout_seconds", 300)),
            )
            replies = await _wait_for_replies(
                client,
                peer,
                sent.id,
                timeout_seconds=int(follow_up.get("reply_timeout_seconds") or 60),
                min_count=len(replies) + 1,
                require_media=bool((follow_up.get("assertions") or {}).get("telegram_media_min")),
                contains_any=(follow_up.get("assertions") or {}).get("bot_reply_contains_any") or [],
            )
    except Exception as exc:
        failure_reason = str(exc)

    validation = _validate_follow_up(follow_up, trace, replies)
    if failure_reason:
        validation["errors"].append(failure_reason)
        validation["ok"] = False
    status = "passed" if validation["ok"] else "failed"
    return {
        "id": follow_up.get("id") or "follow_up",
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "input_message": follow_up["message"],
        "resolved_message": message,
        "telegram_sent": _telegram_message(sent),
        "telegram_replies": replies,
        "task_id": task_id,
        "trace": trace,
        "validation": validation,
        "failure_reason": "; ".join(validation["errors"]) if validation["errors"] else failure_reason,
        "route_decision": _route_decision(trace),
        "plan_steps": _plan_steps(trace),
        "tool_invocations": (trace or {}).get("tool_invocations") or [],
        "artifacts": (trace or {}).get("artifacts") or [],
    }


def _validate_follow_up(
    follow_up: dict[str, Any],
    trace: dict[str, Any] | None,
    replies: list[dict[str, Any]],
) -> dict[str, Any]:
    assertions = follow_up.get("assertions") or {}
    errors: list[str] = []
    outputs = _tool_outputs(trace)
    metadata = (((trace or {}).get("task") or {}).get("metadata") or {})
    observed_tools = _observed_tools(trace)
    if follow_up.get("spawn_expected") is True and trace is None:
        errors.append("expected a spawned task, but no task id was returned")
    if follow_up.get("spawn_expected") is False and trace is not None:
        errors.append(f"expected a direct conversational answer, but spawned task {((trace.get('task') or {}).get('id'))}")
    final_status_in = assertions.get("final_status_in")
    if final_status_in and trace is not None:
        status = ((trace.get("task") or {}).get("status"))
        if status not in set(final_status_in):
            errors.append(f"final status {status!r} not in {final_status_in!r}")
    for expected in assertions.get("tools_all") or []:
        if not _tool_seen(expected, observed_tools):
            errors.append(f"expected tool/operation not seen: {expected}; observed={observed_tools}")
    tools_any = assertions.get("tools_any") or []
    if tools_any and not any(_tool_seen(expected, observed_tools) for expected in tools_any):
        errors.append(f"none of expected tools/operations were seen: {tools_any}; observed={observed_tools}")
    metadata_any = assertions.get("metadata_any") or []
    if metadata_any and not any(_truthy_nested(metadata, key) or _truthy_in_outputs(outputs, key) for key in metadata_any):
        errors.append(f"none of metadata/output evidence keys were found: {metadata_any}")
    reply_text = "\n".join(str(reply.get("text") or "") for reply in replies)
    contains_any = assertions.get("bot_reply_contains_any") or []
    if contains_any and not any(value.lower() in reply_text.lower() for value in contains_any):
        errors.append(f"Telegram replies did not contain any of: {contains_any}")
    media_count = len([reply for reply in replies if reply.get("has_media")])
    if media_count < int(assertions.get("telegram_media_min") or 0):
        errors.append(f"expected at least {assertions['telegram_media_min']} Telegram media replie(s), got {media_count}")
    return {
        "ok": not errors,
        "errors": errors,
        "observed_tools": observed_tools,
        "metadata_keys": sorted(metadata.keys()),
        "telegram_media_count": media_count,
    }


def _validate_case(
    case: dict[str, Any],
    trace: dict[str, Any] | None,
    replies: list[dict[str, Any]],
    before_summary: dict[str, Any],
    after_summary: dict[str, Any],
    schedule_wait_result: dict[str, Any] | None,
) -> dict[str, Any]:
    assertions = case.get("assertions") or {}
    errors: list[str] = []
    observed_tools = _observed_tools(trace)
    task = (trace or {}).get("task") or {}
    metadata = task.get("metadata") or {}
    outputs = _tool_outputs(trace)
    external_limit_block = _external_limit_blocked(assertions, task, metadata, outputs)

    final_status_in = assertions.get("final_status_in")
    if final_status_in and task.get("status") not in set(final_status_in):
        errors.append(f"final status {task.get('status')!r} not in {final_status_in!r}")

    for expected in assertions.get("tools_all") or []:
        if external_limit_block and not _external_tool_expected(expected):
            continue
        if not _tool_seen(expected, observed_tools):
            errors.append(f"expected tool/operation not seen: {expected}; observed={observed_tools}")
    tools_any = assertions.get("tools_any") or []
    if tools_any and not any(_tool_seen(expected, observed_tools) for expected in tools_any):
        errors.append(f"none of expected tools/operations were seen: {tools_any}; observed={observed_tools}")
    for forbidden in assertions.get("tools_forbidden") or []:
        if any(forbidden in tool for tool in observed_tools):
            errors.append(f"forbidden tool was used: {forbidden}; observed={observed_tools}")

    metadata_any = assertions.get("metadata_any") or []
    if metadata_any and not any(_truthy_nested(metadata, key) or _truthy_in_outputs(outputs, key) for key in metadata_any):
        errors.append(f"none of metadata/output evidence keys were found: {metadata_any}")

    artifacts = (trace or {}).get("artifacts") or []
    if not external_limit_block and len(artifacts) < int(assertions.get("artifacts_min") or 0):
        errors.append(f"expected at least {assertions['artifacts_min']} artifact(s), got {len(artifacts)}")

    media_count = len([reply for reply in replies if reply.get("has_media")])
    if not external_limit_block and media_count < int(assertions.get("telegram_media_min") or 0):
        errors.append(f"expected at least {assertions['telegram_media_min']} Telegram media replie(s), got {media_count}")

    urls = _urls(trace, replies)
    if len(urls) < int(assertions.get("urls_min") or 0):
        errors.append(f"expected at least {assertions['urls_min']} URL(s), got {len(urls)}")

    local_paths = _local_paths(trace)
    if len(local_paths) < int(assertions.get("local_paths_min") or 0):
        errors.append(f"expected at least {assertions['local_paths_min']} local path(s), got {len(local_paths)}")

    changed_paths = _changed_paths(metadata, outputs)
    if not external_limit_block and len(changed_paths) < int(assertions.get("changed_paths_min") or 0):
        errors.append(f"expected at least {assertions['changed_paths_min']} changed path(s), got {len(changed_paths)}")

    provider = assertions.get("provider")
    if provider and not _provider_seen(provider, outputs):
        errors.append(f"expected provider {provider!r} not seen in tool outputs")
    provider_any = assertions.get("provider_any") or []
    if provider_any and not any(_provider_seen(provider, outputs) for provider in provider_any):
        errors.append(f"none of expected providers were seen: {provider_any}")

    reply_text = "\n".join(str(reply.get("text") or "") for reply in replies)
    contains_any = assertions.get("bot_reply_contains_any") or []
    if contains_any and not any(value.lower() in reply_text.lower() for value in contains_any):
        errors.append(f"Telegram replies did not contain any of: {contains_any}")

    if assertions.get("schedule_created"):
        before_count = ((before_summary.get("schedules") or {}).get("total") or 0)
        after_count = ((after_summary.get("schedules") or {}).get("total") or 0)
        if not metadata.get("schedule_id") and after_count <= before_count:
            errors.append("schedule_created expected, but no schedule_id or increased schedule count was found")

    if assertions.get("generated_scheduled_task") and not ((schedule_wait_result or {}).get("generated_task_id")):
        errors.append("scheduled task was not generated during post-wait window")

    min_tools = int(assertions.get("tool_invocations_min") or 0)
    if min_tools and len((trace or {}).get("tool_invocations") or []) < min_tools:
        errors.append(f"expected at least {min_tools} tool invocations")

    if assertions.get("plan_required") and not ((trace or {}).get("plan") or {}).get("steps"):
        errors.append("plan_required expected a persisted plan with steps")

    filled_fields_all = assertions.get("filled_fields_all") or []
    if filled_fields_all:
        filled = set(_filled_form_fields(outputs))
        missing = [field for field in filled_fields_all if field not in filled]
        if missing:
            errors.append(f"expected filled form fields missing: {missing}; filled={sorted(filled)}")

    if "form_submitted" in assertions:
        submitted = _form_submitted(outputs)
        expected = bool(assertions["form_submitted"])
        if submitted is not None and submitted != expected:
            errors.append(f"expected form_submitted={expected}, got {submitted}")
        if submitted is None:
            errors.append("expected form submission evidence, but no filled_fields output was found")

    progress_min = int(assertions.get("progress_updates_min") or 0)
    if progress_min:
        progress_count = _progress_update_count(trace, replies)
        if progress_count < progress_min:
            errors.append(f"expected at least {progress_min} progress update(s), got {progress_count}")

    return {
        "ok": not errors,
        "errors": errors,
        "observed_tools": observed_tools,
        "metadata_keys": sorted(metadata.keys()),
        "artifact_count": len(artifacts),
        "telegram_media_count": media_count,
        "url_count": len(urls),
        "local_path_count": len(local_paths),
        "changed_paths": changed_paths,
        "external_limit_block": external_limit_block,
    }


async def _wait_for_replies(
    client: Any,
    peer: Any,
    min_id: int,
    *,
    timeout_seconds: int,
    min_count: int = 1,
    require_media: bool = False,
    contains_any: list[str] | None = None,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    latest: list[dict[str, Any]] = []
    needles = [item.lower() for item in contains_any or []]
    while time.monotonic() < deadline:
        messages = await client.get_messages(peer, min_id=min_id, limit=50)
        latest = [_telegram_message(message) for message in sorted(messages, key=lambda item: item.id) if not getattr(message, "out", False)]
        if _reply_conditions_met(latest, min_count=min_count, require_media=require_media, needles=needles):
            return latest
        await asyncio.sleep(2)
    return latest


def _reply_conditions_met(replies: list[dict[str, Any]], *, min_count: int, require_media: bool, needles: list[str]) -> bool:
    if len(replies) < min_count:
        return False
    if require_media and not any(reply.get("has_media") for reply in replies):
        return False
    if needles:
        text = "\n".join(str(reply.get("text") or "") for reply in replies).lower()
        if not any(needle in text for needle in needles):
            return False
    return True


def _external_limit_blocked(
    assertions: dict[str, Any],
    task: dict[str, Any],
    metadata: dict[str, Any],
    outputs: list[dict[str, Any]],
) -> bool:
    if not assertions.get("allow_external_limit_block"):
        return False
    if str(task.get("status") or "").lower() not in {"blocked", "failed", "retrying"}:
        return False
    limit_state = metadata.get("coding_agent_limit_state")
    if isinstance(limit_state, dict) and limit_state.get("limited"):
        return True
    for output in outputs:
        value = output.get("limit_state") if isinstance(output, dict) else None
        if isinstance(value, dict) and value.get("limited"):
            return True
    return False


def _external_tool_expected(expected: str) -> bool:
    lowered = expected.lower()
    return lowered.startswith("coding.agent") or "copilot" in lowered or "codex" in lowered


async def _wait_for_task_spawn_replies(client: Any, peer: Any, min_id: int, *, timeout_seconds: int) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    latest: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        messages = await client.get_messages(peer, min_id=min_id, limit=80)
        latest = [_telegram_message(message) for message in sorted(messages, key=lambda item: item.id) if not getattr(message, "out", False)]
        if _extract_task_id(latest):
            return latest
        await asyncio.sleep(2)
    return latest


async def _wait_for_terminal_trace(admin: "AdminClient", task_id: str, *, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_trace: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_trace = admin.task_trace(task_id)
        status = ((last_trace.get("task") or {}).get("status") or "").lower()
        if status in TERMINAL_STATUSES:
            return last_trace
        await asyncio.sleep(3)
    return last_trace or admin.task_trace(task_id)


async def _wait_for_scheduled_task(admin: "AdminClient", trace: dict[str, Any] | None, timeout_seconds: int) -> dict[str, Any]:
    schedule_id = (((trace or {}).get("task") or {}).get("metadata") or {}).get("schedule_id")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        schedules = admin.schedules().get("schedules") or []
        match = next((item for item in schedules if item.get("id") == schedule_id), None)
        if match and match.get("last_task_id"):
            return {"schedule_id": schedule_id, "generated_task_id": match["last_task_id"], "schedule": match}
        await asyncio.sleep(5)
    return {"schedule_id": schedule_id, "generated_task_id": None}


class AdminClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def preflight(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for name, fn in (("health", self.health), ("summary", self.summary)):
            try:
                payload[name] = fn()
            except Exception as exc:
                payload[name] = {"error": str(exc)}
        return payload

    def health(self) -> dict[str, Any]:
        return self._json("/health", admin=False)

    def summary(self) -> dict[str, Any]:
        return self._json("/admin/api/summary?task_limit=50")

    def schedules(self) -> dict[str, Any]:
        return self._json("/admin/api/schedules?limit=100")

    def task_trace(self, task_id: str) -> dict[str, Any]:
        return self._json(f"/admin/api/tasks/{parse.quote(task_id)}/trace")

    def _json(self, path: str, *, admin: bool = True) -> dict[str, Any]:
        headers = {}
        if admin and self.token:
            headers["X-Agent-Control-Admin-Token"] = self.token
        req = request.Request(f"{self.base_url}{path}", headers=headers)
        try:
            with request.urlopen(req, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{path} failed with HTTP {exc.code}: {body}") from exc


class Fixtures:
    def __init__(self, values: dict[str, str], server: ThreadingHTTPServer | None = None) -> None:
        self.values = values
        self.server = server

    def public(self) -> dict[str, str]:
        return dict(self.values)


def prepare_fixtures(*, start_web_server: bool) -> Fixtures:
    fixture_root = ROOT / ".agent_control" / "e2e_fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    desktop_folder = Path.home() / "Desktop" / "AgentControlE2E"
    desktop_folder.mkdir(parents=True, exist_ok=True)
    pdf_path = desktop_folder / "agent-control-sample.pdf"
    _write_minimal_pdf(pdf_path, "Agent Control E2E PDF. This file describes desktop automation, browser testing, and artifact delivery.")

    documents_folder = fixture_root / "documents"
    if documents_folder.exists():
        shutil.rmtree(documents_folder)
    documents_folder.mkdir(parents=True)
    (documents_folder / "notes.txt").write_text("notes for e2e organization", encoding="utf-8")
    (documents_folder / "budget.csv").write_text("name,amount\nsample,10\n", encoding="utf-8")
    shutil.copy2(pdf_path, documents_folder / "sample.pdf")

    mixed_content_folder = fixture_root / "mixed_content"
    if mixed_content_folder.exists():
        shutil.rmtree(mixed_content_folder)
    mixed_content_folder.mkdir(parents=True)
    (mixed_content_folder / "automation-notes.txt").write_text(
        "Alpha automation notes. This text file describes desktop inspection and folder summaries.",
        encoding="utf-8",
    )
    (mixed_content_folder / "budget-data.csv").write_text("category,amount\nbrowser-testing,120\nocr-review,45\n", encoding="utf-8")
    (mixed_content_folder / "release-summary.md").write_text(
        "# Release Summary\n\nThe folder contains notes, budget data, a PDF, an HTML page, and an image fixture.",
        encoding="utf-8",
    )
    (mixed_content_folder / "landing-page.html").write_text(
        "<html><body><h1>Fixture Page</h1><p>This HTML file is part of the mixed content folder.</p></body></html>",
        encoding="utf-8",
    )
    _write_minimal_pdf(mixed_content_folder / "mixed-folder-summary.pdf", "Mixed folder PDF. It covers OCR, documents, and local file explanation.")

    image_folder = fixture_root / "images"
    if image_folder.exists():
        shutil.rmtree(image_folder)
    image_folder.mkdir(parents=True)
    tiny_png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    (image_folder / "desktop-screenshot.png").write_bytes(tiny_png)
    (image_folder / "receipt-sample.png").write_bytes(tiny_png)
    try:
        from PIL import Image, ImageDraw

        ocr_image = Image.new("RGB", (320, 100), color="white")
        draw = ImageDraw.Draw(ocr_image)
        draw.text((16, 38), "OCR SAMPLE TEXT", fill="black")
        ocr_image.save(mixed_content_folder / "ocr-sample.png")
    except Exception:
        (mixed_content_folder / "ocr-sample.png").write_bytes(tiny_png)
    voice_ogg_path = fixture_root / "voice-command.ogg"
    voice_ogg_path.write_bytes(_fake_ogg_voice_bytes())

    values = {
        "desktop_folder": str(desktop_folder),
        "pdf_path": str(pdf_path),
        "documents_folder": str(documents_folder),
        "mixed_content_folder": str(mixed_content_folder),
        "image_folder": str(image_folder),
        "voice_ogg_path": str(voice_ogg_path),
    }
    server = None
    if start_web_server:
        web_root = fixture_root / "web"
        web_root.mkdir(parents=True, exist_ok=True)
        (web_root / "index.html").write_text(
            "<html><head><title>Agent Control E2E Site</title></head><body><h1>Agent Control E2E Site</h1><p>This page exists for browser screenshot tests.</p></body></html>",
            encoding="utf-8",
        )
        (web_root / "episode.html").write_text(
            "<html><head><title>E2E Episode Tracker</title></head><body><h1>New Episode Released</h1><p>Episode 4 came out on May 21, 2026.</p></body></html>",
            encoding="utf-8",
        )
        (web_root / "form.html").write_text(
            "<html><head><title>E2E Contact Form</title></head><body><form><label>Name <input name='name'></label><label>Email <input name='email'></label><label>Message <textarea name='message'></textarea></label><button type='submit'>Submit</button></form></body></html>",
            encoding="utf-8",
        )
        server, base_url = _start_static_server(web_root)
        values.update(
            {
                "fixture_base_url": base_url,
                "episode_url": f"{base_url}/episode.html",
                "form_url": f"{base_url}/form.html",
            }
        )
    else:
        values.update({"fixture_base_url": "", "episode_url": "", "form_url": ""})
    return Fixtures(values, server)


def _start_static_server(root: Path) -> tuple[ThreadingHTTPServer, str]:
    port = _free_port()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_minimal_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
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
    output.extend(f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\nstartxref\n{xref_at}\n%%EOF\n".encode("latin-1"))
    path.write_bytes(output)


def _fake_ogg_voice_bytes() -> bytes:
    return (
        b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x01\x00\x00\x00\x00\x00\x00\x00\x1e\x01OpusHead\x01\x01"
        b"\x38\x01\x80\xbb\x00\x00\x00\x00\x00OpusTags\r\x00\x00\x00"
        b"AgentControl\x00\x00\x00\x00"
    )


def _load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _select_cases(cases: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.all:
        return cases
    selected = cases
    if args.case:
        wanted = set(args.case)
        selected = [case for case in cases if case["id"] in wanted]
    if args.tag:
        tags = set(args.tag)
        selected = [case for case in selected if tags & set(case.get("tags") or [])]
    if not args.case and not args.tag:
        return []
    return selected


def _render_message(case: dict[str, Any], fixtures: Fixtures) -> str:
    return _render_text(str(case["message"]), fixtures)


def _render_text(message: str, fixtures: Fixtures) -> str:
    for key, value in fixtures.values.items():
        message = message.replace(f"{{{{{key}}}}}", value)
    return message


def _guarded(case: dict[str, Any]) -> bool:
    return bool(set(case.get("tags") or []) & GUARDED_TAGS)


def _extract_task_id(messages: list[dict[str, Any]]) -> str | None:
    text = "\n".join(str(message.get("text") or "") for message in messages)
    match = re.search(r"\bTask spawned:\s*(task_[A-Za-z0-9]+)\b", text)
    return match.group(1) if match else None


def _telegram_message(message: Any) -> dict[str, Any]:
    return {
        "id": getattr(message, "id", None),
        "date": getattr(getattr(message, "date", None), "isoformat", lambda: None)(),
        "text": getattr(message, "message", None) or getattr(message, "text", None),
        "out": bool(getattr(message, "out", False)),
        "has_media": bool(getattr(message, "media", None)),
    }


def _observed_tools(trace: dict[str, Any] | None) -> list[str]:
    if not trace:
        return []
    observed: list[str] = []
    for step in ((trace.get("plan") or {}).get("steps") or []):
        tool = step.get("tool_name")
        operation = (step.get("tool_input") or {}).get("operation")
        if tool:
            observed.append(f"{tool}:{operation}" if operation else str(tool))
    for invocation in trace.get("tool_invocations") or []:
        tool = invocation.get("tool_name")
        operation = (((invocation.get("request") or {}).get("input") or {}).get("operation"))
        if tool:
            observed.append(f"{tool}:{operation}" if operation else str(tool))
    return observed


def _tool_seen(expected: str, observed: list[str]) -> bool:
    return any(item == expected or item.startswith(f"{expected}:") or expected in item for item in observed)


def _tool_outputs(trace: dict[str, Any] | None) -> list[dict[str, Any]]:
    outputs = []
    for invocation in (trace or {}).get("tool_invocations") or []:
        output = (((invocation.get("result") or {}).get("output")) or {})
        if isinstance(output, dict):
            outputs.append(output)
    last = ((((trace or {}).get("task") or {}).get("metadata") or {}).get("last_tool_result") or {}).get("output")
    if isinstance(last, dict):
        outputs.append(last)
    return outputs


def _provider_seen(provider: str, outputs: list[dict[str, Any]]) -> bool:
    return any(str(output.get("provider") or "").lower() == provider.lower() for output in outputs)


def _truthy_in_outputs(outputs: list[dict[str, Any]], key: str) -> bool:
    return any(_truthy_nested(output, key) for output in outputs)


def _truthy_nested(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        if value.get(key):
            return True
        return any(_truthy_nested(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_truthy_nested(child, key) for child in value)
    return False


def _changed_paths(metadata: dict[str, Any], outputs: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for value in (metadata.get("organized_paths"), metadata.get("changed_paths")):
        if isinstance(value, list):
            paths.extend(map(str, value))
    for output in outputs:
        for key in ("organized_paths", "changed_paths", "moved_files", "files"):
            value = output.get(key)
            if isinstance(value, list):
                paths.extend(map(str, value))
    return sorted(set(paths))


def _filled_form_fields(outputs: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for output in outputs:
        browser_state = output.get("browser_state") if isinstance(output.get("browser_state"), dict) else {}
        filled = browser_state.get("filled_fields") if isinstance(browser_state, dict) else None
        if isinstance(filled, dict) and isinstance(filled.get("filled"), list):
            fields.extend(str(item) for item in filled["filled"])
    return fields


def _form_submitted(outputs: list[dict[str, Any]]) -> bool | None:
    for output in outputs:
        browser_state = output.get("browser_state") if isinstance(output.get("browser_state"), dict) else {}
        filled = browser_state.get("filled_fields") if isinstance(browser_state, dict) else None
        if isinstance(filled, dict) and "submitted" in filled:
            return bool(filled["submitted"])
    return None


def _progress_update_count(trace: dict[str, Any] | None, replies: list[dict[str, Any]]) -> int:
    count = 0
    for event in (trace or {}).get("timeline") or []:
        title = str(event.get("title") or event.get("summary") or "").lower()
        if "progress" in title or "update" in title:
            count += 1
    for reply in replies:
        text = str(reply.get("text") or "").lower()
        if any(marker in text for marker in ("progress", "% done", "scanned", "completed", "currently")):
            count += 1
    return count


def _route_decision(trace: dict[str, Any] | None) -> dict[str, Any] | None:
    for item in ((trace or {}).get("raw_audit") or []):
        payload = item.get("payload") or {}
        if payload.get("route_decision"):
            return payload["route_decision"]
    context = (trace or {}).get("context") or {}
    return (((context.get("planner_or_default_plan") or {}).get("audit_payload") or {}).get("route_decision"))


def _plan_steps(trace: dict[str, Any] | None) -> list[dict[str, Any]]:
    return ((trace or {}).get("plan") or {}).get("steps") or []


def _local_paths(trace: dict[str, Any] | None) -> list[str]:
    paths: list[str] = []
    pattern = re.compile(r"[A-Za-z]:\\[^\n\r\"<>]+")
    for text in _walk_strings(trace or {}):
        paths.extend(pattern.findall(text))
    return sorted(set(path.rstrip(".,") for path in paths))


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for child in value.values():
            strings.extend(_walk_strings(child))
        return strings
    if isinstance(value, list):
        strings = []
        for child in value:
            strings.extend(_walk_strings(child))
        return strings
    return []


def _urls(trace: dict[str, Any] | None, replies: list[dict[str, Any]]) -> list[str]:
    text = json.dumps(trace or {}, default=str) + "\n" + "\n".join(str(reply.get("text") or "") for reply in replies)
    return sorted(set(re.findall(r"https?://[^\s\"'<>]+", text)))


def _summary_excerpt(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": summary.get("status"),
        "services": summary.get("services"),
        "schedules": summary.get("schedules"),
        "task_count": ((summary.get("task_pagination") or {}).get("total")),
        "warnings": summary.get("warnings"),
    }


def _write_case_log(root: Path, payload: dict[str, Any]) -> None:
    path = root / f"{_safe_name(payload['id'])}.json"
    _write_json(path, payload)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": len([item for item in results if item["status"] == "passed"]),
        "failed": len([item for item in results if item["status"] == "failed"]),
        "skipped": len([item for item in results if item["status"] == "skipped"]),
        "results": [
            {
                "id": item["id"],
                "status": item["status"],
                "task_id": item.get("task_id"),
                "final_status": item.get("final_status"),
                "failure_reason": item.get("failure_reason"),
            }
            for item in results
        ],
    }


def _summary_markdown(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "# Live Telegram E2E Summary",
        "",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Skipped: {summary['skipped']}",
        "",
        "| Case | Status | Task | Final | Failure |",
        "|---|---:|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item['id']} | {item['status']} | {item.get('task_id') or ''} | {item.get('final_status') or ''} | {(item.get('failure_reason') or '').replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


def _skipped(case: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "id": case["id"],
        "requirement": case.get("requirement"),
        "status": "skipped",
        "input_message": case.get("message"),
        "failure_reason": reason,
        "validation": {"ok": False, "errors": [reason]},
    }


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "case"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _print_cases(cases: list[dict[str, Any]]) -> None:
    for case in cases:
        guarded = " guarded" if _guarded(case) else ""
        print(f"{case['id']}\t{','.join(case.get('tags') or [])}{guarded}\t{case.get('requirement')}")


def _require_live_args(args: argparse.Namespace) -> None:
    missing = []
    for attr, env in (
        ("telegram_api_id", "TELEGRAM_API_ID"),
        ("telegram_api_hash", "TELEGRAM_API_HASH"),
        ("bot_username", "TELEGRAM_BOT_USERNAME"),
    ):
        if not getattr(args, attr):
            value = os.getenv(env)
            if value:
                setattr(args, attr, value)
            else:
                missing.append(env)
    if missing:
        raise SystemExit(f"Missing live Telegram env/config: {', '.join(missing)}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live Telegram end-to-end capability tests.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--case", action="append", help="Case id to run. Can be supplied more than once.")
    parser.add_argument("--tag", action="append", help="Run cases with a tag. Can be supplied more than once.")
    parser.add_argument("--all", action="store_true", help="Run all cases. Guarded cases still need --include-guarded.")
    parser.add_argument("--include-guarded", action="store_true", help="Run external-agent, long, and fault-injection cases.")
    parser.add_argument("--pause-between-seconds", type=float, default=5.0, help="Wait between live cases so asynchronous services settle.")
    parser.add_argument("--dry-run", action="store_true", help="Print resolved messages without sending Telegram messages.")
    parser.add_argument("--backend-url", default=os.getenv("AGENT_ADMIN_BACKEND_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--admin-token", default=os.getenv("AGENT_ADMIN_TOKEN", ""))
    parser.add_argument("--log-root", default=str(DEFAULT_LOG_ROOT))
    parser.add_argument("--telegram-api-id", default=os.getenv("TELEGRAM_API_ID"))
    parser.add_argument("--telegram-api-hash", default=os.getenv("TELEGRAM_API_HASH"))
    parser.add_argument("--telegram-session", default=os.getenv("TELEGRAM_USER_SESSION"))
    parser.add_argument("--bot-username", default=os.getenv("TELEGRAM_BOT_USERNAME"))
    return parser.parse_args()


if __name__ == "__main__":
    main()
