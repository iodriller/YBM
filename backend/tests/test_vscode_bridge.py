from __future__ import annotations

from fastapi.testclient import TestClient

from agent_control.main import app, vscode_store
from agent_control.tools.vscode_bridge import _extract_copilot_usage, _has_materializable_file_blocks


def test_vscode_bridge_updates_and_reads_state(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ADAPTERS__VSCODE__AUTH_TOKEN_ENV", "__TEST_NO_VSCODE_TOKEN__")
    vscode_store.state = None
    client = TestClient(app)

    response = client.post(
        "/vscode/state",
        json={
            "instance_id": "machine",
            "workspace_folders": ["C:/repo"],
            "active_file": "C:/repo/app.py",
            "open_files": ["C:/repo/app.py"],
            "diagnostics_count": 2,
        },
    )
    read_response = client.get("/vscode/state")

    assert response.status_code == 200
    assert read_response.status_code == 200
    assert read_response.json()["active_file"] == "C:/repo/app.py"


def test_vscode_bridge_rejects_missing_token(monkeypatch) -> None:
    monkeypatch.setenv("VSCODE_BRIDGE_TOKEN", "secret")
    client = TestClient(app)

    response = client.post("/vscode/heartbeat", json={"instance_id": "machine"})

    assert response.status_code == 401


def test_vscode_terminal_command_queue(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ADAPTERS__VSCODE__AUTH_TOKEN_ENV", "__TEST_NO_VSCODE_TOKEN__")
    vscode_store.terminal_commands = []
    client = TestClient(app)

    response = client.post(
        "/vscode/terminal-commands",
        json={"instance_id": "machine", "terminal_id": "agent-control", "command": "echo hi"},
    )
    pending = client.get("/vscode/terminal-commands?instance_id=other")
    taken = client.get("/vscode/terminal-commands?instance_id=machine")
    empty = client.get("/vscode/terminal-commands?instance_id=machine")

    assert response.status_code == 200
    assert pending.json() == []
    assert taken.json()[0]["command"] == "echo hi"
    assert empty.json() == []


def test_vscode_terminal_output_can_be_filtered_by_command(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ADAPTERS__VSCODE__AUTH_TOKEN_ENV", "__TEST_NO_VSCODE_TOKEN__")
    vscode_store.terminal_outputs = []
    client = TestClient(app)

    first = client.post(
        "/vscode/terminal-output",
        json={
            "instance_id": "machine",
            "terminal_id": "agent-control",
            "command_id": "cmd_1",
            "content": "first",
            "is_final": True,
        },
    )
    client.post(
        "/vscode/terminal-output",
        json={
            "instance_id": "machine",
            "terminal_id": "agent-control",
            "command_id": "cmd_2",
            "content": "second",
            "is_final": True,
        },
    )
    filtered = client.get("/vscode/terminal-output?command_id=cmd_1")

    assert first.status_code == 200
    assert filtered.json()["outputs"] == [first.json()]


def test_vscode_terminal_output_strips_shell_control_sequences(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ADAPTERS__VSCODE__AUTH_TOKEN_ENV", "__TEST_NO_VSCODE_TOKEN__")
    vscode_store.terminal_outputs = []
    client = TestClient(app)

    response = client.post(
        "/vscode/terminal-output",
        json={
            "instance_id": "machine",
            "terminal_id": "agent-control",
            "command_id": "cmd_1",
            "content": "\u001b]633;C\u0007agent-control-bridge-ok\u001b[0m",
            "is_final": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["content"] == "agent-control-bridge-ok"


def test_copilot_usage_lines_are_extracted() -> None:
    usage = _extract_copilot_usage("answer\nRequests 1 Premium (14s)\nTokens up 26.0k down 1.0k\n")

    assert usage["requests"] == "Requests 1 Premium (14s)"
    assert usage["tokens"].startswith("Tokens")


def test_materializable_file_blocks_are_detected() -> None:
    assert _has_materializable_file_blocks(
        """```html filename=index.html
<main>ok</main>
```"""
    )
    assert not _has_materializable_file_blocks("Files created: index.html\nChanges +0 -0")
