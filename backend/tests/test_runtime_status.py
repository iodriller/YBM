from __future__ import annotations

from datetime import datetime, timezone
import json

from agent_control.config import AppSettings
from agent_control.runtime_status import KNOWN_SERVICE_NAMES, service_summary


def test_service_summary_marks_expected_supervised_services_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / ".agent_control" / "run"
    run_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    for name in ("backend", "worker", "coding_session_watcher", "scheduler", "telegram_polling"):
        (run_dir / f"{name}.status.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "status": "running",
                    "supervisor_pid": 100,
                    "child_pid": 101,
                    "restart_count": 1,
                    "updated_at": now,
                }
            ),
            encoding="utf-8",
        )

    summary = service_summary(AppSettings(_env_file=None, channels={"telegram": {"enabled": True}}))

    assert summary["ready"] is True
    assert all(item["ok"] for item in summary["items"] if item["expected"])


def test_service_summary_marks_stale_worker_not_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / ".agent_control" / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "worker.status.json").write_text(
        json.dumps(
            {
                "name": "worker",
                "status": "running",
                "supervisor_pid": 100,
                "child_pid": 101,
                "restart_count": 1,
                "updated_at": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    summary = service_summary(AppSettings(_env_file=None, scheduler={"enabled": False}, channels={"telegram": {"enabled": False}}))
    worker = next(item for item in summary["items"] if item["name"] == "worker")

    assert summary["ready"] is False
    assert worker["ok"] is False
    assert worker["status"] == "running"


def test_whatsapp_is_a_known_service_name() -> None:
    """Regression guard: the admin log-viewer endpoint (admin.py) rejects
    any service name not in KNOWN_SERVICE_NAMES before turning it into a
    file path - `ybm logs whatsapp` / the Diagnostics page's log viewer
    would 400 forever if this were missing, the way it briefly was."""
    assert "whatsapp" in KNOWN_SERVICE_NAMES


def test_whatsapp_expected_only_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".agent_control" / "run").mkdir(parents=True)

    disabled = service_summary(AppSettings(_env_file=None, channels={"whatsapp": {"enabled": False}}))
    enabled = service_summary(AppSettings(_env_file=None, channels={"whatsapp": {"enabled": True}}))

    disabled_item = next(item for item in disabled["items"] if item["name"] == "whatsapp")
    enabled_item = next(item for item in enabled["items"] if item["name"] == "whatsapp")
    assert disabled_item["expected"] is False
    assert disabled_item["ok"] is True  # not expected -> vacuously ok, doesn't drag down `ready`
    assert enabled_item["expected"] is True
    assert enabled_item["ok"] is False  # expected but no status.json written -> genuinely not ready
