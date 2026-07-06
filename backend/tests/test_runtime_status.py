from __future__ import annotations

from datetime import datetime, timezone
import json

from agent_control.config import AppSettings
from agent_control.runtime_status import service_summary


def test_service_summary_marks_expected_supervised_services_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / ".agent_control" / "run"
    run_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    for name in ("backend", "worker", "coding_session_watcher", "scheduler", "telegram_polling", "admin_ui"):
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
