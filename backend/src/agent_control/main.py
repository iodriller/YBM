from fastapi import Depends, FastAPI, Header

from agent_control.config import load_settings
from agent_control.tools.vscode_bridge import (
    VSCodeBridgeStore,
    VSCodeHeartbeat,
    VSCodeTerminalCommand,
    VSCodeTerminalOutput,
    VSCodeWorkspaceState,
    require_vscode_bridge_token,
)

app = FastAPI(title="Agent Control Backend")
vscode_store = VSCodeBridgeStore()


@app.get("/health")
def health() -> dict[str, str]:
    settings = load_settings()
    return {"status": "ok", "instance": settings.identity.instance_name}


def _vscode_auth(token: str | None = Header(default=None, alias="X-Agent-Control-Token")) -> None:
    require_vscode_bridge_token(load_settings().adapters.vscode, token)


@app.post("/vscode/heartbeat", dependencies=[Depends(_vscode_auth)])
def vscode_heartbeat(heartbeat: VSCodeHeartbeat) -> VSCodeHeartbeat:
    return vscode_store.update_heartbeat(heartbeat)


@app.post("/vscode/state", dependencies=[Depends(_vscode_auth)])
def vscode_state(state: VSCodeWorkspaceState) -> VSCodeWorkspaceState:
    return vscode_store.update_state(state)


@app.get("/vscode/state", dependencies=[Depends(_vscode_auth)])
def get_vscode_state() -> VSCodeWorkspaceState | None:
    return vscode_store.state


@app.post("/vscode/terminal-output", dependencies=[Depends(_vscode_auth)])
def vscode_terminal_output(output: VSCodeTerminalOutput) -> VSCodeTerminalOutput:
    return vscode_store.add_terminal_output(output)


@app.post("/vscode/terminal-commands", dependencies=[Depends(_vscode_auth)])
def enqueue_vscode_terminal_command(command: VSCodeTerminalCommand) -> VSCodeTerminalCommand:
    return vscode_store.enqueue_terminal_command(command)


@app.get("/vscode/terminal-commands", dependencies=[Depends(_vscode_auth)])
def take_vscode_terminal_commands(instance_id: str | None = None) -> list[VSCodeTerminalCommand]:
    return vscode_store.take_terminal_commands(instance_id)
