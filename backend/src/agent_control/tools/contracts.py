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


class BrowserOpenInput(ToolInputModel):
    operation: Literal["open"] = "open"
    url: str | None = None
    query: str | None = None
    objective: str | None = None
    new_tab: bool = True
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class BrowserSearchInput(ToolInputModel):
    operation: Literal["search"] = "search"
    query: str | None = None
    objective: str | None = None
    open_first_result: bool = False
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class BrowserResearchInput(ToolInputModel):
    operation: Literal["research"] = "research"
    objective: str = Field(min_length=1)
    url: str | None = None
    query: str | None = None
    open_first_result: bool | None = None
    screenshot: bool = False
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class BrowserInspectTabsInput(ToolInputModel):
    operation: Literal["inspect_tabs"] = "inspect_tabs"
    include_text: bool = False
    max_tabs: int = Field(default=8, ge=1, le=30)


class BrowserScreenshotInput(ToolInputModel):
    operation: Literal["screenshot"] = "screenshot"
    url: str | None = None
    tab_id: str | None = None
    filename: str | None = None
    full_page: bool = True
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class BrowserNavigateInput(ToolInputModel):
    operation: Literal["navigate"] = "navigate"
    url: str = Field(min_length=1)
    tab_id: str | None = None
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class BrowserCloseTabInput(ToolInputModel):
    operation: Literal["close_tab"] = "close_tab"
    tab_id: str | None = None
    url_contains: str | None = None
    title_contains: str | None = None


class BrowserClickInput(ToolInputModel):
    operation: Literal["click"] = "click"
    selector: str | None = None
    text: str | None = None
    tab_id: str | None = None
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class BrowserFillFormInput(ToolInputModel):
    operation: Literal["fill_form"] = "fill_form"
    fields: dict[str, str] = Field(default_factory=dict)
    submit: bool = False
    submit_selector: str | None = None
    tab_id: str | None = None
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class ComputerObserveInput(ToolInputModel):
    operation: Literal["observe"] = "observe"
    objective: str | None = None
    include_screenshot: bool = True
    include_ui_tree: bool = True
    summarize: bool = True


class ComputerActInput(ToolInputModel):
    operation: Literal["act"] = "act"
    objective: str | None = None
    action: dict[str, Any] = Field(default_factory=dict)


class ComputerRunGoalInput(ToolInputModel):
    operation: Literal["run_goal"] = "run_goal"
    objective: str = Field(min_length=1)
    max_steps: int | None = Field(default=None, ge=1, le=50)
    include_ui_tree: bool = True
    require_vision: bool = True


class FilesystemInspectInput(ToolInputModel):
    operation: Literal["inspect_folder"] = "inspect_folder"
    root: str = Field(min_length=1)
    max_depth: int = Field(default=2, ge=0, le=10)
    max_entries: int = Field(default=200, ge=1, le=5000)


class FilesystemSearchInput(ToolInputModel):
    operation: Literal["search"] = "search"
    root: str = Field(min_length=1)
    query: str = Field(min_length=1)
    include_content: bool = False
    max_results: int = Field(default=100, ge=1, le=1000)


class FilesystemOrganizePlanInput(ToolInputModel):
    operation: Literal["organize_plan"] = "organize_plan"
    root: str = Field(min_length=1)
    strategy: Literal["by_type", "by_extension"] = "by_type"
    recursive: bool = False
    max_files: int = Field(default=1000, ge=1, le=10000)


class FilesystemManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation: Literal["move", "copy"] = "move"
    source: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    reason: str | None = None


class FilesystemApplyManifestInput(ToolInputModel):
    operation: Literal["apply_manifest"] = "apply_manifest"
    root: str = Field(min_length=1)
    manifest: list[FilesystemManifestItem] = Field(min_length=1)
    dry_run: bool = False
    overwrite: bool = False


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


class BrowserToolOutput(ToolOutputModel):
    operation: str = Field(min_length=1)
    browser_state: dict[str, Any] | None = None
    browser_url: str | None = None
    url: str | None = None
    page_title: str | None = None
    summary: str | None = None
    tabs: list[dict[str, Any]] = Field(default_factory=list)
    links: list[dict[str, str]] = Field(default_factory=list)
    screenshot_path: str | None = None
    screenshot_uri: str | None = None


class ComputerUseOutput(ToolOutputModel):
    operation: str = Field(min_length=1)
    observation: dict[str, Any] | None = None
    actions_taken: list[dict[str, Any]] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    screenshot_path: str | None = None
    screenshot_uri: str | None = None
    final_summary: str | None = None
    completed: bool = False


class FilesystemManageOutput(ToolOutputModel):
    operation: str = Field(min_length=1)
    root: str | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)
    manifest: list[dict[str, Any]] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    dry_run: bool = False
    summary: str | None = None
