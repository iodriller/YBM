"""One-shot E2E driver: send 'first 5 episodes' to bot and trace full pipeline."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from urllib import request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
BOT_USERNAME = os.environ["TELEGRAM_BOT_USERNAME"]
SESSION = str(ROOT / ".agent_control" / "telegram_e2e_user")

MESSAGE = "go to dizibox.com and tell me the first 5 new episodes listed under Yeni Eklenen Bolumler"


def admin_summary() -> dict:
    r = request.urlopen("http://127.0.0.1:8765/admin/api/summary?task_limit=20", timeout=10)
    return json.loads(r.read())


def admin_trace(task_id: str) -> dict:
    r = request.urlopen(f"http://127.0.0.1:8765/admin/api/tasks/{task_id}/trace", timeout=10)
    return json.loads(r.read())


async def main() -> None:
    from telethon import TelegramClient

    before = admin_summary()
    known_ids = {t["id"] for t in before.get("tasks", [])}

    client = TelegramClient(SESSION, API_ID, API_HASH)
    print("Connecting Telegram client...")
    async with client:
        peer = await client.get_entity(BOT_USERNAME)
        sent = await client.send_message(peer, MESSAGE)
        print(f"Sent message id={sent.id}: {MESSAGE}")
        print()

        # Wait for new task to appear (any task not in known_ids)
        new_task_id = None
        for i in range(40):
            await asyncio.sleep(2)
            data = admin_summary()
            for t in data.get("tasks", []):
                if t["id"] not in known_ids:
                    new_task_id = t["id"]
                    print(f"New task spotted: {t['id']} | obj={t.get('objective','')[:60]}")
                    break
            if new_task_id:
                break
        if not new_task_id:
            print("FAILED: no new task spawned within 80s")
            return
        print(f"Task spawned: {new_task_id}")
        print()

        # Watch task progress
        last_status = None
        last_step = None
        last_replan = None
        for i in range(120):
            trace = admin_trace(new_task_id)
            task = trace.get("task") or {}
            meta = task.get("metadata") or {}
            status = task.get("status")
            step = meta.get("last_tool_name", "")
            replan = meta.get("replan_count", 0)
            if status != last_status or step != last_step or replan != last_replan:
                synth = bool(meta.get("synthesized_answer"))
                print(f"[{i*3:>4}s] status={status:12} step={step:20} replan={replan} synth={synth}")
                last_status = status
                last_step = step
                last_replan = replan
            if status in {"completed", "failed", "blocked", "cancelled"}:
                break
            await asyncio.sleep(3)

        # Final dump
        trace = admin_trace(new_task_id)
        task = trace.get("task") or {}
        meta = task.get("metadata") or {}
        print()
        print("=" * 70)
        print(f"FINAL status: {task.get('status')}")
        print(f"synthesized_answer: {(meta.get('synthesized_answer') or '(none)')[:500]}")
        print(f"replan_count: {meta.get('replan_count', 0)}")
        print(f"last_replan_reason: {meta.get('last_replan_reason', '(none)')[:300]}")
        print(f"fulfillment_gap: {meta.get('fulfillment_gap', '(none)')}")
        print(f"last_worker_error: {meta.get('last_worker_error', '(none)')[:300]}")
        print()
        print("PLAN STEPS:")
        plan = trace.get("plan") or {}
        for s in plan.get("steps") or []:
            print(f"  - {s.get('tool_name')} | op={s.get('tool_input', {}).get('operation')} | status={s.get('status')}")
        print()
        last_result = meta.get("last_tool_result") or {}
        last_output = last_result.get("output") or {}
        print("LAST TOOL OUTPUT (first 600 chars):")
        text = last_output.get("summary") or last_output.get("text") or last_output.get("final_summary") or json.dumps(last_output)[:600]
        print(str(text)[:600])
        print()
        # Also fetch audit events for this task to see synthesizer/validator activity
        try:
            r = request.urlopen(f"http://127.0.0.1:8765/admin/api/tasks/{new_task_id}/audit?limit=80", timeout=10)
            audit = json.loads(r.read())
            print("AUDIT EVENTS (relevant):")
            for ev in audit.get("events") or []:
                actor = ev.get("actor", "")
                if actor in {"synthesizer", "validator", "planner", "orchestrator"}:
                    payload = ev.get("payload") or {}
                    action = payload.get("action") or payload.get("reason") or ev.get("event_type", "")
                    print(f"  [{actor}] {action} | {str(payload)[:200]}")
        except Exception as exc:
            print(f"audit fetch failed: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
