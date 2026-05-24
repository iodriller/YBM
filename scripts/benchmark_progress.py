"""Live progress monitor for benchmark_models.py — read-only.

Usage:
    python scripts/benchmark_progress.py          # one snapshot
    python scripts/benchmark_progress.py --watch  # refresh every 15s
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT            = Path(__file__).resolve().parents[1]
LOG_PATH        = ROOT / ".agent_control" / "benchmark_run.log"
CHECKPOINT_PATH = ROOT / ".agent_control" / "benchmark_checkpoint.json"

MODELS = [
    "qwen3-vl:8b-instruct",
    "gemma3:12b",
]
N_PLAN  = 6
N_CLASS = 8
TOTAL   = len(MODELS) * (N_PLAN + N_CLASS)   # 28


def _bar(done: int, total: int, width: int = 32) -> str:
    if total <= 0:
        return "[" + " " * width + "]"
    filled = int(width * done / total)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _eta(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    td  = timedelta(seconds=int(seconds))
    eta = datetime.now() + td
    return f"{td} (≈{eta.strftime('%H:%M:%S')})"


def snapshot() -> None:
    ckpt: dict = {}
    if CHECKPOINT_PATH.exists():
        try:
            ckpt = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    done      = len(ckpt)
    pct       = (done / TOTAL) * 100 if TOTAL else 0.0
    elapsed_vals = [float(v.get("elapsed_seconds") or 0) for v in ckpt.values()]
    avg_s     = sum(elapsed_vals) / done if done else 0.0
    remaining = TOTAL - done
    eta_s     = remaining * avg_s if done else -1

    # Start time from log
    start_str = ""
    if LOG_PATH.exists():
        first = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        if first:
            start_str = first[0].split("]")[0].strip("[") if "]" in first[0] else ""

    print()
    print("=" * 74)
    print(f"  YBM Benchmark v2 — progress   {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 74)
    if start_str:
        print(f"  Started   : {start_str}")
    print(f"  Progress  : {_bar(done, TOTAL)}  {done}/{TOTAL}  ({pct:.0f}%)")
    print(f"  Avg/case  : {avg_s:.1f}s")
    print(f"  Remaining : {remaining} cases  →  ETA {_eta(eta_s)}")
    print()

    # Per-model table
    print(f"  {'MODEL':<28} {'PLAN':^10} {'CLASS':^10} {'PASS':>6}  {'ERR'}")
    print(f"  {'-'*28} {'-'*10} {'-'*10} {'-'*6}  {'-'*20}")
    for model in MODELS:
        p_done = sum(1 for k in ckpt if k.startswith(f"{model}|plan|"))
        p_pass = sum(1 for k, v in ckpt.items() if k.startswith(f"{model}|plan|") and v.get("success"))
        c_done = sum(1 for k in ckpt if k.startswith(f"{model}|class|"))
        c_pass = sum(1 for k, v in ckpt.items() if k.startswith(f"{model}|class|") and v.get("success"))

        # error breakdown for this model
        errs: dict[str, int] = {}
        for k, v in ckpt.items():
            if k.startswith(f"{model}|") and not v.get("success"):
                cat = v.get("error_category") or "?"
                errs[cat] = errs.get(cat, 0) + 1
        err_str = " ".join(f"{c}:{n}" for c, n in errs.items()) or "—"

        p_str = f"{p_done}/{N_PLAN}" + ("✓" if p_done == N_PLAN else " ")
        c_str = f"{c_done}/{N_CLASS}" + ("✓" if c_done == N_CLASS else " ")
        total_pass = p_pass + c_pass
        total_done = p_done + c_done
        print(f"  {model:<28} {p_str:^10} {c_str:^10} {total_pass:>2}/{total_done:<3}  {err_str}")
    print()

    # Latest log lines
    if LOG_PATH.exists():
        tail = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
        print("  Latest log:")
        for line in tail:
            print(f"    {line}")
    print()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--watch", action="store_true", help="refresh every 15s")
    args = p.parse_args()
    if not args.watch:
        snapshot()
        return
    try:
        while True:
            snapshot()
            time.sleep(15)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
