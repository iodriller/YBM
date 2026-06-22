from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from agent_control.config import AppSettings


logger = logging.getLogger(__name__)

SERVICE_STALE_SECONDS = 30


def service_summary(settings: AppSettings) -> dict[str, Any]:
    services = []
    for name, expected in _expected_services(settings).items():
        services.append(_service_record(name, expected))
    ready = all(not item["expected"] or item["ok"] for item in services)
    return {
        "ready": ready,
        "stale_after_seconds": SERVICE_STALE_SECONDS,
        "items": services,
    }


def _expected_services(settings: AppSettings) -> dict[str, bool]:
    return {
        "localdeploy": _expects_localdeploy(settings),
        "backend": True,
        "worker": True,
        "scheduler": bool(settings.scheduler.enabled),
        "telegram_polling": bool(settings.channels.telegram.enabled and settings.channels.telegram.polling),
        "admin_ui": bool(settings.server.admin_enabled),
    }


def _expects_localdeploy(settings: AppSettings) -> bool:
    profile = settings.llm.profiles.get(settings.llm.default_profile)
    if profile is None:
        return False
    base_url = (profile.base_url or "").lower()
    return "127.0.0.1:8000" in base_url or "localhost:8000" in base_url or "localdeploy" in settings.llm.default_profile.lower()


def _service_record(name: str, expected: bool) -> dict[str, Any]:
    status_path = _run_dir() / f"{name}.status.json"
    status = _read_json(status_path)
    updated_at = _parse_datetime(status.get("updated_at")) if status else None
    age_seconds = _age_seconds(updated_at)
    state = str(status.get("status") or "missing") if status else "missing"
    supervisor_pid = status.get("supervisor_pid") if status else None
    child_pid = status.get("child_pid") if status else None
    ok = (
        expected
        and state == "running"
        and age_seconds is not None
        and age_seconds <= SERVICE_STALE_SECONDS
        and bool(supervisor_pid)
    )
    if not expected:
        ok = True
    return {
        "name": name,
        "expected": expected,
        "ok": ok,
        "status": state,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "age_seconds": age_seconds,
        "supervisor_pid": supervisor_pid,
        "child_pid": child_pid,
        "restart_count": status.get("restart_count") if status else None,
        "last_exit_code": status.get("last_exit_code") if status else None,
        "message": status.get("message") if status else "no supervisor status file",
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        logger.debug("failed to read service status file %s", path, exc_info=True)
        return {}


def _run_dir() -> Path:
    cwd_run_dir = Path.cwd() / ".agent_control" / "run"
    if cwd_run_dir.exists():
        return cwd_run_dir
    return Path(__file__).resolve().parents[3] / ".agent_control" / "run"


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_seconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds()))
