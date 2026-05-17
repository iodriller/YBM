from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Header, HTTPException
from pydantic import Field

from agent_control.config import VSCodeAdapterConfig
from agent_control.config_sync import read_env_value
from agent_control.schemas import StrictBaseModel, new_id, utc_now


class VSCodeHeartbeat(StrictBaseModel):
    instance_id: str
    workspace_folders: list[str] = Field(default_factory=list)
    active_file: str | None = None
    diagnostics_count: int = 0
    observed_at: datetime = Field(default_factory=utc_now)


class VSCodeWorkspaceState(StrictBaseModel):
    instance_id: str
    workspace_folders: list[str] = Field(default_factory=list)
    active_file: str | None = None
    open_files: list[str] = Field(default_factory=list)
    diagnostics_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utc_now)


class VSCodeTerminalOutput(StrictBaseModel):
    instance_id: str
    terminal_id: str
    content: str
    observed_at: datetime = Field(default_factory=utc_now)


class VSCodeTerminalCommand(StrictBaseModel):
    id: str = Field(default_factory=lambda: new_id("vscode_terminal_command"))
    command: str
    terminal_id: str = "agent-control"
    instance_id: str | None = None
    cwd: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class VSCodeBridgeStore:
    def __init__(self) -> None:
        self.heartbeat: VSCodeHeartbeat | None = None
        self.state: VSCodeWorkspaceState | None = None
        self.terminal_outputs: list[VSCodeTerminalOutput] = []
        self.terminal_commands: list[VSCodeTerminalCommand] = []

    def update_heartbeat(self, heartbeat: VSCodeHeartbeat) -> VSCodeHeartbeat:
        self.heartbeat = heartbeat
        return heartbeat

    def update_state(self, state: VSCodeWorkspaceState) -> VSCodeWorkspaceState:
        self.state = state
        return state

    def add_terminal_output(self, output: VSCodeTerminalOutput) -> VSCodeTerminalOutput:
        self.terminal_outputs.append(output)
        return output

    def enqueue_terminal_command(self, command: VSCodeTerminalCommand) -> VSCodeTerminalCommand:
        self.terminal_commands.append(command)
        return command

    def take_terminal_commands(self, instance_id: str | None = None) -> list[VSCodeTerminalCommand]:
        ready: list[VSCodeTerminalCommand] = []
        remaining: list[VSCodeTerminalCommand] = []
        for command in self.terminal_commands:
            if command.instance_id is None or command.instance_id == instance_id:
                ready.append(command)
            else:
                remaining.append(command)
        self.terminal_commands = remaining
        return ready


def require_vscode_bridge_token(config: VSCodeAdapterConfig, token: str | None = Header(default=None, alias="X-Agent-Control-Token")) -> None:
    expected = read_env_value(config.auth_token_env)
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="invalid VS Code bridge token")
