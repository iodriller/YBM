from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


SMOKE_LOG_KEYS = (
    "input_message",
    "task_id",
    "route_decision",
    "plan_name",
    "plan_steps",
    "tool_invocations",
    "artifacts",
    "notification_summary",
    "final_status",
    "validator_result",
    "failure_reason",
    "local_paths",
    "urls",
)


def write_smoke_log(root: str | Path, test_name: str, payload: dict[str, Any]) -> Path:
    """Write a stable JSON smoke-test log for manual end-to-end runs."""
    run_dir = Path(root).expanduser().resolve() / _timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / f"{_safe_name(test_name)}.json"
    normalized = {key: payload.get(key) for key in SMOKE_LOG_KEYS}
    normalized["logged_at"] = datetime.now(timezone.utc).isoformat()
    log_path.write_text(json.dumps(normalized, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return log_path


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "smoke"
