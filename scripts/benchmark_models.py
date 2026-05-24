"""Benchmark Ollama models on structured output: planning + classification.

Goals:
- Measure real capability on YBM-relevant tasks (no artificial time caps).
- Force num_ctx=4096 to keep models on GPU; verify GPU placement before each model.
- Checkpoint per-case to JSON so re-runs skip completed work.
- Comprehensive per-case metrics: latency, attempt count, error category.

Run from project root:
    python scripts/benchmark_models.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from urllib import request as urlrequest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from pydantic import ValidationError

from agent_control.prompts import prompt_text, render_prompt
from agent_control.schemas import MessageClassification, PlanModel

LOG_PATH        = ROOT / ".agent_control" / "benchmark_run.log"
CHECKPOINT_PATH = ROOT / ".agent_control" / "benchmark_checkpoint.json"
RESULTS_PATH    = ROOT / ".agent_control" / "model_benchmark.json"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

OLLAMA_URL = "http://localhost:11434/api/chat"

# Match LocalDeploy project settings exactly (api_server.py options_payload):
#   num_ctx=8192, num_predict=2048, top_p=0.9, repeat_penalty=1.1, temperature=0.2
#   NO format:"json" — JSON is prompted, not grammar-forced (matching real project behavior)
NUM_CTX            = 8192
NUM_GPU            = 999   # force full GPU offload; LocalDeploy omits this but Ollama auto-detects
PER_CALL_TIMEOUT_S = 240   # matches LocalDeploy gemma3_12b_ollama_safe timeout_seconds

MODELS_TO_TEST = [
    # Only GPU-confirmed models on RTX 3080 8 GB VRAM.
    ("qwen3-vl:8b-instruct",  "Qwen3-VL 8B Q4_K_M (~6.1 GB)"),
    ("gemma3:12b",            "Gemma3 12B  Q4_K_M (~8.1 GB)"),
]

# --- Tool/config context fed to planner ---
PLANNER_CONFIG_CONTEXT = """\
Available tools and operations (use ONLY these tool_name values):

- browser.open
    operations: open, summarize_page, screenshot, research, research_pages
    risk: low.  capabilities: browser.open

- browser.control
    operations: navigate, click, fill_form, extract_page_state
    risk: high. capabilities: browser.control

- filesystem.manage
    operations: inspect_folder, search, read_file, write_text_file, describe_folder
    risk: low.  capabilities: filesystem.read  (write_text_file → filesystem.write)

- code.interpreter
    operations: generate_and_run
    risk: high. capabilities: terminal.run

- desktop.observe
    operations: screenshot, observe
    risk: low.  capabilities: desktop.screenshot

- task.status
    operations: status
    risk: low.  capabilities: llm.generate
"""

# ---------- Classification cases ----------
# Each has a "text" (user message), expected "is_task", and expected "route" for grading.
CLASSIFICATION_CASES = [
    {
        "name":      "browse_dizibox",
        "text":      "go to dizibox.com and tell me the first 5 new episodes listed under Yeni Eklenen Bolumler",
        "is_task":   True,
        "exp_route": "browser.open",
    },
    {
        "name":      "chat_greeting",
        "text":      "hey, how are you doing today?",
        "is_task":   False,
        "exp_route": "conversation",
    },
    {
        "name":      "status_query",
        "text":      "what tasks are running right now?",
        "is_task":   True,
        "exp_route": "status",
    },
    {
        "name":      "file_desktop_list",
        "text":      "list all the files that are on my desktop",
        "is_task":   True,
        "exp_route": "filesystem.manage",
    },
    {
        "name":      "code_script",
        "text":      "write me a python script that reads sales.csv and calculates total revenue per region",
        "is_task":   True,
        "exp_route": "code.interpreter",
    },
    {
        "name":      "desktop_screenshot",
        "text":      "take a screenshot of my desktop right now",
        "is_task":   True,
        "exp_route": "desktop.observe",
    },
    {
        "name":      "turkish_browse",
        "text":      "dizibox.com'a git ve yeni eklenen bölümleri söyle",
        "is_task":   True,
        "exp_route": "browser.open",
    },
    {
        "name":      "browser_control_email",
        "text":      "open Chrome and navigate to my Gmail inbox",
        "is_task":   True,
        "exp_route": "browser.control",
    },
]

# ---------- Planning cases ----------
# Each tests multi-step structured output. "expected_tools" are what a correct plan should include.
PLANNING_CASES = [
    {
        "name":           "dizibox_5_episodes",
        "objective":      "go to dizibox.com and tell me the first 5 new episodes listed under Yeni Eklenen Bolumler",
        "expected_tools": ["browser.open"],
    },
    {
        "name":           "desktop_resume_read",
        "objective":      "find resume.pdf on my desktop and read its contents to me",
        "expected_tools": ["filesystem.manage"],
    },
    {
        "name":           "python_chart",
        "objective":      "create a python script that reads data.csv and generates a bar chart saved as chart.png using matplotlib",
        "expected_tools": ["code.interpreter"],
    },
    {
        "name":           "github_org_repos",
        "objective":      "go to github.com/anthropics and find the number of public repositories",
        "expected_tools": ["browser.open"],
    },
    {
        "name":           "docs_pdf_search",
        "objective":      "search my Documents folder for any PDF files and list their names",
        "expected_tools": ["filesystem.manage"],
    },
    {
        "name":           "email_unread_summary",
        "objective":      "open my Gmail in Chrome, find unread messages from today, and give me a short summary of each",
        "expected_tools": ["browser.control"],
    },
]


def _categorize_error(exc: Exception) -> str:
    name = type(exc).__name__
    msg  = str(exc)
    if "timed out" in msg.lower() or "timeout" in msg.lower() or name == "TimeoutError":
        return "timeout"
    if name in ("JSONDecodeError", "ValueError") and "json" in msg.lower():
        return "json_parse"
    if name == "ValidationError":
        return "schema_validation"
    return "network_or_other"


def log(line: str) -> None:
    ts   = datetime.now().strftime("%H:%M:%S")
    text = f"[{ts}] {line}"
    print(text, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")
        fh.flush()


def ollama_ps() -> str:
    try:
        return subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception as exc:
        return f"(ollama ps failed: {exc})"


def nvidia_used_mib() -> int:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return int(out.splitlines()[0])
    except Exception:
        return -1


def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_checkpoint(data: dict) -> None:
    CHECKPOINT_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def ollama_chat(model: str, system_prompt: str, user_prompt: str,
                want_json: bool, timeout_s: float = PER_CALL_TIMEOUT_S) -> tuple[str, float]:
    """Call Ollama with a hard wall-clock timeout via a worker thread.

    urllib's socket timeout only fires when the socket is *idle* — Ollama's
    slow token stream keeps the socket alive indefinitely. We run the HTTP
    call in a daemon thread and join() with a hard deadline instead.
    """
    payload = {
        "model":    model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "num_ctx":        NUM_CTX,
            "num_predict":    2048,
            "num_gpu":        NUM_GPU,
            "temperature":    0.2,
            "top_p":          0.9,
            "repeat_penalty": 1.1,
        },
    }
    # No "format":"json" — matches real project behavior (LocalDeploy never grammar-forces JSON)
    body = json.dumps(payload).encode("utf-8")
    req  = urlrequest.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})

    result: dict = {}

    def _do_request() -> None:
        try:
            with urlrequest.urlopen(req, timeout=timeout_s + 10) as resp:
                result["data"] = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            result["error"] = exc

    start  = time.monotonic()
    thread = threading.Thread(target=_do_request, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)

    elapsed = time.monotonic() - start

    if thread.is_alive():
        # Hard wall-clock timeout — thread keeps running in background but we stop waiting
        raise TimeoutError(f"timed out after {timeout_s}s (wall clock)")
    if "error" in result:
        raise result["error"]

    content = str(result["data"].get("message", {}).get("content", ""))
    return content, elapsed


def ollama_stop(model: str) -> None:
    try:
        subprocess.run(["ollama", "stop", model], capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def parse_json(content: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    start = content.find("{")
    if start < 0:
        raise ValueError(f"no JSON object found: {content[:200]}")
    depth = 0
    for i in range(start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(content[start:i + 1])
    raise ValueError(f"unbalanced JSON: {content[:200]}")


def run_one_case(model: str, system_prompt: str, user_prompt: str, model_type) -> dict:
    """Run a case with up to 3 attempts. No hard time cap — rely on per-call timeout."""
    error_msg      = ""
    error_category = ""
    attempts       = 0
    total_elapsed  = 0.0
    current_prompt = user_prompt

    for attempt in range(3):
        attempts += 1
        attempt_start = time.monotonic()
        try:
            content, elapsed = ollama_chat(model, system_prompt, current_prompt,
                                           want_json=True, timeout_s=PER_CALL_TIMEOUT_S)
            total_elapsed += elapsed
            try:
                obj = parse_json(content)
                model_type.model_validate(obj)
                return {
                    "success":        True,
                    "attempts":       attempts,
                    "elapsed_seconds": round(total_elapsed, 1),
                    "error_category": "",
                    "error":          "",
                }
            except (json.JSONDecodeError, ValueError, ValidationError) as exc:
                error_category = _categorize_error(exc)
                error_msg      = f"{type(exc).__name__}: {str(exc)[:300]}"
                current_prompt = render_prompt(
                    "tasks/structured_retry.md",
                    original_prompt=user_prompt,
                    error=str(exc)[:2000],
                )
        except Exception as exc:
            total_elapsed += time.monotonic() - attempt_start
            error_category = _categorize_error(exc)
            error_msg      = f"{type(exc).__name__}: {str(exc)[:300]}"
            break

    return {
        "success":        False,
        "attempts":       attempts,
        "elapsed_seconds": round(total_elapsed, 1),
        "error_category": error_category,
        "error":          error_msg,
    }


def warmup_and_check_gpu(model: str) -> tuple[str, int]:
    """Unload prior copy, warm up with exact options. Returns (gpu_status, vram_mib)."""
    log(f"  Stopping any prior copy of {model} ...")
    ollama_stop(model)
    time.sleep(2)
    log(f"  Warming up {model} (num_ctx={NUM_CTX}, num_gpu={NUM_GPU}) ...")
    try:
        ollama_chat(model, "ping", "reply with ok", want_json=False, timeout_s=120)
    except Exception as exc:
        log(f"  WARMUP FAILED: {exc}")
        return "WARMUP_FAILED", -1

    ps   = ollama_ps()
    vram = nvidia_used_mib()
    log(f"  ollama ps:")
    for line in ps.splitlines()[:6]:
        log(f"    {line}")
    log(f"  nvidia-smi memory.used: {vram} MiB")

    gpu_status = "UNKNOWN"
    for line in ps.splitlines():
        tag = model.split(":")[0]
        if tag in line or model in line:
            if "100% GPU" in line:
                gpu_status = "GPU"
            elif "CPU" in line and "GPU" in line:
                gpu_status = "SPLIT"
            elif "CPU" in line:
                gpu_status = "CPU"
            break
    return gpu_status, vram


def grade_classification(result: dict, case: dict) -> dict:
    """Add correctness fields: is_task_correct, route_correct."""
    out = dict(result)
    out["expected_is_task"] = case["is_task"]
    out["expected_route"]   = case["exp_route"]
    # We can only grade if we saved the parsed output — for now record N/A if failed
    out["is_task_correct"]  = None
    out["route_correct"]    = None
    return out


def grade_planning(result: dict, case: dict) -> dict:
    """Add expected_tools field."""
    out = dict(result)
    out["expected_tools"] = case.get("expected_tools", [])
    return out


def _bar(done: int, total: int, width: int = 28) -> str:
    filled = int(width * done / total) if total else 0
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def main() -> None:
    log("=" * 80)
    log(f"YBM Model Benchmark v2 — Planning + Classification (num_ctx={NUM_CTX}, no format:json)")
    log("=" * 80)

    checkpoint = load_checkpoint()
    log(f"Checkpoint: {len(checkpoint)} prior entries loaded")

    plan_system  = prompt_text("base/planner_system.md")
    class_system = prompt_text("base/classifier_system.md")

    total_cases = len(MODELS_TO_TEST) * (len(PLANNING_CASES) + len(CLASSIFICATION_CASES))
    done_count  = len(checkpoint)
    log(f"Total cases: {total_cases}  |  Already done: {done_count}")

    summary_table: list[dict] = []

    for model, label in MODELS_TO_TEST:
        log("")
        log(f"{'#' * 10}  MODEL: {model}  ({label})  {'#' * 10}")

        gpu_status, vram_mib = warmup_and_check_gpu(model)
        log(f"  GPU status: {gpu_status}  VRAM used: {vram_mib} MiB")

        model_start = time.monotonic()

        # -------- Planning --------
        plan_details: list[dict] = []
        for case in PLANNING_CASES:
            key = f"{model}|plan|{case['name']}"
            done_count_now = len(checkpoint)
            log(f"  {_bar(done_count_now, total_cases, 24)} {done_count_now}/{total_cases}  "
                f"[plan/{case['name']}]", )
            if key in checkpoint:
                log(f"    CACHED  attempts={checkpoint[key]['attempts']}  "
                    f"elapsed={checkpoint[key]['elapsed_seconds']}s  success={checkpoint[key]['success']}")
                plan_details.append(grade_planning({"case": case["name"], **checkpoint[key]}, case))
                continue

            log(f"    running ...")
            user_prompt = render_prompt(
                "tasks/planner_user.md",
                objective=case["objective"],
                config_context=PLANNER_CONFIG_CONTEXT,
                memory_context="",
            )
            result = run_one_case(model, plan_system, user_prompt, PlanModel)
            checkpoint[key] = result
            save_checkpoint(checkpoint)

            status_str = "OK" if result["success"] else f"FAIL [{result['error_category']}]"
            log(f"    {status_str}  attempts={result['attempts']}  elapsed={result['elapsed_seconds']}s"
                + (f"  err={result['error'][:120]}" if not result["success"] else ""))
            plan_details.append(grade_planning({"case": case["name"], **result}, case))

        # -------- Classification --------
        class_details: list[dict] = []
        for case in CLASSIFICATION_CASES:
            key = f"{model}|class|{case['name']}"
            done_count_now = len(checkpoint)
            log(f"  {_bar(done_count_now, total_cases, 24)} {done_count_now}/{total_cases}  "
                f"[class/{case['name']}]")
            if key in checkpoint:
                log(f"    CACHED  attempts={checkpoint[key]['attempts']}  "
                    f"elapsed={checkpoint[key]['elapsed_seconds']}s  success={checkpoint[key]['success']}")
                class_details.append(grade_classification({"case": case["name"], **checkpoint[key]}, case))
                continue

            log(f"    running ...")
            user_prompt = render_prompt(
                "tasks/classifier_user.md",
                channel="telegram", kind="text",
                sender_id="user", chat_id="chat",
                context="No prior conversation.",
                text=case["text"],
            )
            result = run_one_case(model, class_system, user_prompt, MessageClassification)
            checkpoint[key] = result
            save_checkpoint(checkpoint)

            status_str = "OK" if result["success"] else f"FAIL [{result['error_category']}]"
            log(f"    {status_str}  attempts={result['attempts']}  elapsed={result['elapsed_seconds']}s"
                + (f"  err={result['error'][:120]}" if not result["success"] else ""))
            class_details.append(grade_classification({"case": case["name"], **result}, case))

        model_elapsed = round(time.monotonic() - model_start, 1)

        # Per-model stats
        plan_pass    = sum(1 for r in plan_details  if r["success"])
        plan_1st     = sum(1 for r in plan_details  if r["success"] and r["attempts"] == 1)
        plan_avg     = round(sum(r["elapsed_seconds"] for r in plan_details) / max(1, len(plan_details)), 1)
        class_pass   = sum(1 for r in class_details if r["success"])
        class_1st    = sum(1 for r in class_details if r["success"] and r["attempts"] == 1)
        class_avg    = round(sum(r["elapsed_seconds"] for r in class_details) / max(1, len(class_details)), 1)

        # Error breakdown
        all_results  = plan_details + class_details
        err_counts: dict[str, int] = {}
        for r in all_results:
            if not r["success"]:
                cat = r.get("error_category") or "unknown"
                err_counts[cat] = err_counts.get(cat, 0) + 1

        summary_table.append({
            "model":       model,
            "label":       label,
            "gpu_status":  gpu_status,
            "vram_mib":    vram_mib,
            "total_time_s": model_elapsed,
            "planning": {
                "pass": plan_pass, "total": len(plan_details), "first_try": plan_1st,
                "avg_latency_s": plan_avg, "details": plan_details,
            },
            "classification": {
                "pass": class_pass, "total": len(class_details), "first_try": class_1st,
                "avg_latency_s": class_avg, "details": class_details,
            },
            "error_breakdown": err_counts,
        })

        log(f"  >>> {model}  planning   {plan_pass}/{len(plan_details)} pass "
            f"({plan_1st} first-try)  avg {plan_avg}s")
        log(f"  >>> {model}  classify   {class_pass}/{len(class_details)} pass "
            f"({class_1st} first-try)  avg {class_avg}s")
        log(f"  >>> {model}  total time {model_elapsed}s  errors: {err_counts}")

    # -------- Final summary --------
    log("")
    log("=" * 80)
    log("FINAL SUMMARY")
    log("=" * 80)
    log(f"{'MODEL':<28} {'GPU':<8} {'VRAM':>6}  {'PLAN':^12} {'CLASS':^12} {'AVG_LAT':>8} {'TIME':>7}")
    log("-" * 80)
    for r in summary_table:
        p   = r["planning"]
        c   = r["classification"]
        p_s = f"{p['pass']}/{p['total']} ({p['first_try']})"
        c_s = f"{c['pass']}/{c['total']} ({c['first_try']})"
        avg = (p["avg_latency_s"] + c["avg_latency_s"]) / 2
        vram_s = f"{r['vram_mib']}M" if r["vram_mib"] > 0 else "N/A"
        log(f"{r['model']:<28} {r['gpu_status']:<8} {vram_s:>6}  {p_s:^12} {c_s:^12} {avg:>7.1f}s {r['total_time_s']:>6.0f}s")
    log("")
    log("Error breakdown per model:")
    for r in summary_table:
        log(f"  {r['model']:<28}  {r['error_breakdown']}")

    RESULTS_PATH.write_text(json.dumps(summary_table, indent=2, default=str), encoding="utf-8")
    log(f"\nFull results saved → {RESULTS_PATH}")


if __name__ == "__main__":
    main()
