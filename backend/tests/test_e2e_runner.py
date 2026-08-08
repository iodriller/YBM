from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import yaml


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


def test_preflight_checks_the_configured_llm_not_localdeploy(monkeypatch, tmp_path) -> None:
    """Preflight must validate whichever LLM the stack is configured to use.

    It used to ping LocalDeploy's :8000/health unconditionally, so an install
    pointed at Ollama or a cloud endpoint failed every case before starting,
    reporting "LocalDeploy not responding" for a service it was never meant to
    run.
    """
    runner = _runner_module()

    env_path = tmp_path / ".env"
    db_path = tmp_path / "agent_control.db"
    cases_path = tmp_path / "all_cases.json"
    for path in (env_path, db_path, cases_path):
        path.touch()

    monkeypatch.setattr(runner, "ENV_PATH", env_path)
    monkeypatch.setattr(runner, "DB_PATH", db_path)
    monkeypatch.setattr(runner, "CASES_PATH", cases_path)
    monkeypatch.setattr(runner, "admin_get", lambda _path: {"tasks": []})

    from agent_control import bootstrap

    monkeypatch.setattr(bootstrap, "check_llm_configured", lambda _settings: True)
    assert runner._preflight() == []

    monkeypatch.setattr(bootstrap, "check_llm_configured", lambda _settings: False)
    issues = runner._preflight()
    assert len(issues) == 1
    assert "not reachable" in issues[0]
    assert "LocalDeploy" not in issues[0]


def test_fake_mcp_fixture_provisions_and_restores_exact_local_state(monkeypatch, tmp_path) -> None:
    runner = _runner_module()
    config_path = tmp_path / "config.yaml"
    catalog_path = tmp_path / "tool_catalog.json"
    server_path = tmp_path / "fake_mcp_server.py"
    original_config = (
        "mcp:\n"
        "  enabled: false\n"
        f"  catalog_path: {catalog_path.as_posix()}\n"
        "  servers: {}\n"
    ).encode()
    original_catalog = b'{"original": true}\n'
    config_path.write_bytes(original_config)
    catalog_path.write_bytes(original_catalog)
    server_path.write_text("# fake MCP server\n", encoding="utf-8")
    restarts: list[str] = []
    monkeypatch.setattr(runner, "CONFIG_PATH", config_path)

    fixture = runner.provision_fake_mcp_fixture(
        server_path,
        restart_services=lambda: restarts.append("restart"),
    )

    configured = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert configured["mcp"]["enabled"] is True
    assert configured["mcp"]["servers"]["fake"]["args"] == [str(server_path.resolve())]
    assert catalog["tools"][0]["server"] == "fake"
    assert catalog["tools"][0]["tool"] == "echo"
    assert restarts == ["restart"]

    fixture.restore()

    assert config_path.read_bytes() == original_config
    assert catalog_path.read_bytes() == original_catalog
    assert restarts == ["restart", "restart"]


def test_local_fake_mcp_case_is_not_guarded() -> None:
    runner = _runner_module()
    case = next(item for item in runner.load_cases() if item["id"] == "mcp_call_fake_echo")

    assert runner.is_guarded(case) is False


def test_find_new_task_correlates_exact_telegram_message_text(monkeypatch) -> None:
    runner = _runner_module()
    responses = iter(
        [
            {
                "tasks": [
                    {
                        "id": "task_late_previous_turn",
                        "metadata": {
                            "source_channel": "telegram",
                            "source_message_id": "telegram_41",
                            "original_message_text": "previous message",
                        },
                    }
                ]
            },
            {
                "tasks": [
                    {
                        "id": "task_late_previous_turn",
                        "metadata": {
                            "source_channel": "telegram",
                            "source_message_id": "telegram_41",
                            "original_message_text": "previous message",
                        },
                    },
                    {
                        "id": "task_current_turn",
                        "metadata": {
                            "source_channel": "telegram",
                            # Deliberately differs from any Telethon id. The
                            # Bot API owns this id namespace.
                            "source_message_id": "telegram_9001",
                            "original_message_text": "current message",
                        },
                    },
                ]
            },
        ]
    )

    monkeypatch.setattr(runner, "admin_summary", lambda: next(responses))

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(runner.asyncio, "sleep", no_sleep)

    task_id = asyncio.run(
        runner._find_new_task(set(), "current message", spawn_timeout_s=5)
    )

    assert task_id == "task_current_turn"


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
