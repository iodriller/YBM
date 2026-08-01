from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "run_all_e2e_tests.py"


def _runner_module():
    spec = importlib.util.spec_from_file_location("run_all_e2e_tests", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_case_suites_do_not_infer_recovery_from_metadata_assertions() -> None:
    runner = _runner_module()

    suites = runner._case_suites(
        {
            "size": "small",
            "tags": ["filesystem"],
            "assertions": {"metadata_any": ["file_manifest"]},
        }
    )

    assert "smoke" in suites
    assert "tools" in suites
    assert "recovery" not in suites
    assert "recovery" in runner._case_suites({"tags": ["recovery"]})


def test_admin_get_authenticates_with_configured_token(monkeypatch) -> None:
    runner = _runner_module()
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"ok": True}).encode()

    def fake_urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("AGENT_ADMIN_TOKEN", "e2e-admin-token")
    monkeypatch.setattr(runner.urlrequest, "urlopen", fake_urlopen)

    assert runner.admin_get("/admin/api/summary", timeout=7) == {"ok": True}
    request = captured["request"]
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers["x-agent-control-admin-token"] == "e2e-admin-token"
    assert captured["timeout"] == 7


def test_preflight_uses_localdeploy_health_endpoint(monkeypatch) -> None:
    runner = _runner_module()
    urls: list[tuple[str, float]] = []

    monkeypatch.setattr(runner, "admin_get", lambda _path: {"tasks": []})
    monkeypatch.setattr(runner, "ping", lambda url, *, timeout: urls.append((url, timeout)) or True)

    assert runner._preflight() == []
    assert urls == [(f"{runner.LOCALDEPLOY_BASE}/health", 5.0)]


def test_diagnose_turn_enforces_structured_assertions() -> None:
    runner = _runner_module()
    turn = runner.TurnResult(
        final_status="completed",
        tools_seen=["filesystem.manage:inspect_folder"],
        metadata={"file_manifest": {"entries": ["notes.txt"]}},
        artifact_count=1,
        telegram_media_count=1,
        changed_paths_count=2,
        bot_reply_text="The folder has files and notes.",
        telegram_sent_events=[{"payload": {"kind": "text", "text": "The folder has files and notes."}}],
        telegram_confirmed_text="The folder has files and notes.",
    )
    case = {
        "assertions": {
            "final_status_in": ["completed"],
            "tools_all": ["filesystem.manage:inspect_folder"],
            "metadata_any": ["file_manifest", "summary"],
            "artifacts_min": 1,
            "telegram_media_min": 1,
            "changed_paths_min": 1,
            "bot_reply_contains_any": ["notes", "budget"],
        }
    }

    passed, reason = runner._diagnose_turn(case, turn, is_followup=False)
    assert passed is True
    assert reason is None

    missing_tool_case = {"assertions": {**case["assertions"], "tools_all": ["browser.open:open"]}}
    passed, reason = runner._diagnose_turn(missing_tool_case, turn, is_followup=False)
    assert passed is False
    assert "missing required tool" in reason


def test_diagnose_turn_fails_bot_reply_without_confirmed_telegram_send() -> None:
    """Task metadata can claim a completed answer even if nothing was ever
    actually sent to Telegram — the audit log (message_sent events) is the
    only independent confirmation, so its absence must fail the assertion."""
    runner = _runner_module()
    turn = runner.TurnResult(
        final_status="completed",
        bot_reply_text="The folder has files and notes.",
        # No telegram_sent_events populated: nothing confirms a real send happened.
    )
    case = {"assertions": {"final_status_in": ["completed"], "bot_reply_contains_any": ["notes"]}}

    passed, reason = runner._diagnose_turn(case, turn, is_followup=False)

    assert passed is False
    assert "no message_sent audit record" in reason


def test_diagnose_turn_uses_confirmed_media_count_when_metadata_undercounts() -> None:
    runner = _runner_module()
    turn = runner.TurnResult(
        final_status="completed",
        telegram_media_count=0,
        telegram_confirmed_media_count=1,
    )
    case = {"assertions": {"final_status_in": ["completed"], "telegram_media_min": 1}}

    passed, reason = runner._diagnose_turn(case, turn, is_followup=False)

    assert passed is True
    assert reason is None


def test_diagnose_turn_fails_on_missing_artifact_media_and_reply() -> None:
    runner = _runner_module()
    turn = runner.TurnResult(
        final_status="completed",
        tools_seen=["artifact.deliver:send_file"],
        metadata={"artifact_delivery": {"delivered": True}},
        artifact_count=0,
        telegram_media_count=0,
        bot_reply_text="Done.",
    )

    passed, reason = runner._diagnose_turn(
        {
            "assertions": {
                "artifacts_min": 1,
                "telegram_media_min": 1,
                "bot_reply_contains_any": ["sent"],
            }
        },
        turn,
        is_followup=False,
    )

    assert passed is False
    assert "artifact_count" in reason
