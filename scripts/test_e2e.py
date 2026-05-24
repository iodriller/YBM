"""Generic E2E driver: send any message to the bot and trace the full pipeline.

Usage:
    python scripts/test_e2e.py "your message here"
    python scripts/test_e2e.py "use python to compute the 20th fibonacci number"
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
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


def admin_summary() -> dict:
    r = request.urlopen("http://127.0.0.1:8765/admin/api/summary?task_limit=20", timeout=10)
    return json.loads(r.read())


def admin_trace(task_id: str) -> dict:
    r = request.urlopen(f"http://127.0.0.1:8765/admin/api/tasks/{task_id}/trace", timeout=10)
    return json.loads(r.read())


async def main(message: str, max_wait_s: int = 360) -> None:
    from telethon import TelegramClient

    before = admin_summary()
    known_ids = {t["id"] for t in before.get("tasks", [])}

    client = TelegramClient(SESSION, API_ID, API_HASH)
    print(f"Connecting Telegram client...")
    print(f"Message: {message}")
    print()
    async with client:
        peer = await client.get_entity(BOT_USERNAME)
        sent = await client.send_message(peer, message)
        print(f"Sent message id={sent.id}")
        print()

        new_task_id = None
        for _ in range(40):
            await asyncio.sleep(2)
            data = admin_summary()
            for t in data.get("tasks", []):
                if t["id"] not in known_ids:
                    new_task_id = t["id"]
                    print(f"New task: {t['id']} | obj={t.get('objective','')[:70]}")
                    break
            if new_task_id:
                break
        if not new_task_id:
            print("FAILED: no task spawned within 80s")
            return
        print()

        last_status = None
        last_step = None
        last_replan = None
        poll_count = max_wait_s // 3
        for i in range(poll_count):
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

        trace = admin_trace(new_task_id)
        task = trace.get("task") or {}
        meta = task.get("metadata") or {}
        print()
        print("=" * 70)
        print(f"FINAL status: {task.get('status')}")
        print(f"synthesized_answer:")
        print((meta.get("synthesized_answer") or "(none)")[:1200])
        print()
        print(f"replan_count: {meta.get('replan_count', 0)}")
        print(f"last_worker_error: {(meta.get('last_worker_error') or '(none)')[:300]}")
        print()
        print("PLAN STEPS:")
        plan = trace.get("plan") or {}
        for s in plan.get("steps") or []:
            print(f"  - {s.get('tool_name')} | op={(s.get('tool_input') or {}).get('operation')} | status={s.get('status')}")
        print()
        last_result = meta.get("last_tool_result") or {}
        last_output = last_result.get("output") or {}
        print("LAST TOOL OUTPUT (first 800 chars):")
        text = last_output.get("stdout") or last_output.get("summary") or last_output.get("text") or last_output.get("final_summary") or json.dumps(last_output)[:800]
        print(str(text)[:800])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_e2e.py \"<message>\"")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
