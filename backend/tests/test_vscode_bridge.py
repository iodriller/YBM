from __future__ import annotations

from fastapi.testclient import TestClient

from agent_control.main import app, vscode_store


def test_vscode_bridge_updates_and_reads_state(monkeypatch) -> None:
    monkeypatch.delenv("VSCODE_BRIDGE_TOKEN", raising=False)
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
    monkeypatch.delenv("VSCODE_BRIDGE_TOKEN", raising=False)
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
    monkeypatch.delenv("VSCODE_BRIDGE_TOKEN", raising=False)
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
