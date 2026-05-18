from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolInputModel(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    scope_target: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)


class ToolOutputModel(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    operation: str | None = None
    terminal_output: list[dict[str, Any]] = Field(default_factory=list)


class WorkspaceFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(min_length=1)
    content: str = ""


class WorkspacePrepareInput(ToolInputModel):
    operation: Literal["prepare"] = "prepare"
    objective: str | None = None
    refresh_task_file: bool = True


class WorkspaceWriteFilesInput(ToolInputModel):
    operation: Literal["write_files"] = "write_files"
    objective: str | None = None
    files: list[WorkspaceFileInput] = Field(min_length=1)


class WorkspaceMaterializeStaticAppInput(ToolInputModel):
    operation: Literal["materialize_static_app"] = "materialize_static_app"
    objective: str | None = None
    prompt: str | None = None
    source_text: str | None = None
    assistant_output: str | None = None
    allow_fallback_template: bool = True
    require_index: bool = False
    overwrite: bool = False


class WorkspaceLaunchStaticInput(ToolInputModel):
    operation: Literal["launch_static"] = "launch_static"
    objective: str | None = None
    prompt: str | None = None
    web_port_start: int | None = Field(default=None, ge=1, le=65535)
    open_browser: bool | None = None
    ensure_index: bool = True


class WorkspaceWebAppPreviewInput(ToolInputModel):
    operation: Literal["web_app_preview"] = "web_app_preview"
    objective: str | None = None
    prompt: str | None = None
    web_port_start: int | None = Field(default=None, ge=1, le=65535)
    open_browser: bool | None = None


class AdapterFactoryAssessInput(ToolInputModel):
    operation: Literal["assess"] = "assess"
    objective: str | None = None
    prompt: str | None = None
    adapter_name: str | None = None


class AdapterFactoryScaffoldInput(ToolInputModel):
    operation: Literal["scaffold"] = "scaffold"
    objective: str | None = None
    prompt: str | None = None
    adapter_name: str | None = None
    tool_name: str | None = None
    capability: str | None = None


class VSCodeCopilotTerminalInput(ToolInputModel):
    prompt: str | None = None
    command: str | None = None
    terminal_id: str = "agent-control-copilot"
    instance_id: str | None = None
    cwd: str | None = None
    capture_output: bool = True
    allow_local_fallback: bool = True

    @model_validator(mode="after")
    def prompt_or_command_required(self) -> "VSCodeCopilotTerminalInput":
        if not (self.prompt or self.command):
            raise ValueError("prompt or command is required")
        return self


class VSCodeTerminalCommandInput(ToolInputModel):
    command: str = Field(min_length=1)
    terminal_id: str = "agent-control"
    instance_id: str | None = None
    cwd: str | None = None
    capture_output: bool = True


class CodingAssistantInput(ToolInputModel):
    prompt: str = Field(min_length=1)


class WorkspacePrepareOutput(ToolOutputModel):
    workspace_dir: str = Field(min_length=1)
    files: list[str] = Field(default_factory=list)


class WorkspaceWriteFilesOutput(WorkspacePrepareOutput):
    files: list[str] = Field(min_length=1)


class WorkspaceMaterializeStaticAppOutput(WorkspacePrepareOutput):
    materialized_from: Literal["assistant_output", "fallback_template", "existing_files"]


class WorkspaceLaunchStaticOutput(WorkspacePrepareOutput):
    url: str = Field(pattern=r"^https?://")
    server_pid: int = Field(ge=1)


class WorkspaceWebAppPreviewOutput(WorkspaceLaunchStaticOutput):
    pass


class AdapterFactoryAssessOutput(ToolOutputModel):
    adapter_name: str = Field(min_length=1)
    assessment: str = Field(min_length=1)
    cacheable: bool = True
    execution_policy: str = Field(min_length=1)


class AdapterFactoryScaffoldOutput(ToolOutputModel):
    adapter_dir: str = Field(min_length=1)
    adapter_name: str = Field(min_length=1)
    files: list[str] = Field(min_length=1)
    cacheable: bool = True
    execution_policy: str = Field(min_length=1)


class VSCodeTerminalToolOutput(ToolOutputModel):
    command_id: str | None = None
    queued: dict[str, Any] | None = None
    usage: dict[str, str] = Field(default_factory=dict)
    retried: bool | None = None
    terminal_output: list[dict[str, Any]] = Field(default_factory=list)


class CodingAssistantOutput(ToolOutputModel):
    stdout: str = ""
    stderr: str = ""
    returncode: int
