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


class TTSSynthesizeInput(ToolInputModel):
    operation: Literal["synthesize"] = "synthesize"
    text: str = Field(min_length=1)
    voice: str | None = None
    output_name: str | None = None


class ArtifactDeliverInput(ToolInputModel):
    operation: Literal["send_file", "send_latest", "send_screenshot", "list_artifacts"] = "send_latest"
    artifact_id: str | None = None
    path: str | None = None
    artifact_type: str | None = None
    chat_id: str | None = None
    caption: str | None = None
    mime_type: str | None = None

    @model_validator(mode="after")
    def _require_target_for_send_file(self) -> "ArtifactDeliverInput":
        # send_file needs SOMETHING pointing at the file. Without this, the
        # adapter raises ValueError at execute time and the planner has to
        # replan from scratch. Encoding the constraint here makes the planner
        # see a precise schema error instead.
        if self.operation == "send_file" and not (self.artifact_id or self.path):
            raise ValueError(
                "send_file requires 'path' (file path or basename of a prior step's artifact) "
                "or 'artifact_id'"
            )
        return self


class DocumentManageInput(ToolInputModel):
    operation: Literal["inspect_document", "extract_text", "summarize_pdf", "create_presentation", "update_presentation"] = "inspect_document"
    path: str | None = None
    artifact_id: str | None = None
    title: str | None = None
    content: str | None = None
    instructions: str | None = None
    output_name: str | None = None

    @model_validator(mode="after")
    def _require_inputs_per_operation(self) -> "DocumentManageInput":
        # Operations that read an existing document need to know which one.
        # Operations that create one need at least a title or content to work with.
        op = self.operation
        if op in {"inspect_document", "extract_text", "summarize_pdf", "update_presentation"} and not (
            self.path or self.artifact_id
        ):
            raise ValueError(
                f"{op} requires 'path' or 'artifact_id' to identify the document"
            )
        if op == "create_presentation" and not (self.title or self.content or self.instructions):
            raise ValueError(
                "create_presentation requires at least one of 'title', 'content', or 'instructions'"
            )
        return self


class CodingAgentInput(ToolInputModel):
    operation: Literal["plan", "run_step", "run_goal", "status", "limits", "resume", "stop"] = "run_goal"
    provider: Literal["codex", "github_copilot"]
    prompt: str | None = None
    objective: str | None = None
    workspace_dir: str | None = None
    session_id: str | None = None
    step_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_input_for_run_ops(self) -> "CodingAgentInput":
        if self.operation in {"plan", "run_step", "run_goal"} and not (self.prompt or self.objective):
            raise ValueError(
                f"coding.agent {self.operation} requires 'prompt' or 'objective'"
            )
        return self


class ScheduleManageInput(ToolInputModel):
    operation: Literal["create", "list", "pause", "resume", "delete", "run_now"] = "create"
    schedule_id: str | None = None
    objective: str | None = None
    cadence: str | None = None
    timezone: str | None = None
    source_chat_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_inputs_per_operation(self) -> "ScheduleManageInput":
        if self.operation == "create" and not self.objective:
            raise ValueError("schedule.manage create requires 'objective'")
        if self.operation in {"pause", "resume", "delete", "run_now"} and not self.schedule_id:
            raise ValueError(f"schedule.manage {self.operation} requires 'schedule_id'")
        return self


class TaskStatusInput(ToolInputModel):
    operation: Literal["status"] = "status"
    limit: int = Field(default=10, ge=1, le=50)


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

    @model_validator(mode="after")
    def _require_query_or_objective(self) -> "BrowserSearchInput":
        if not (self.query or self.objective):
            raise ValueError("browser.search requires 'query' or 'objective'")
        return self


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

    @model_validator(mode="after")
    def _require_selector_or_text(self) -> "BrowserClickInput":
        if not (self.selector or self.text):
            raise ValueError("browser.click requires 'selector' (CSS) or 'text' (visible text to match)")
        return self


class BrowserFillFormInput(ToolInputModel):
    operation: Literal["fill_form"] = "fill_form"
    # fields is logically required for fill_form — encoding that here instead
    # of in the browser adapter's runtime check eliminates a class of replans.
    fields: dict[str, str] = Field(..., min_length=1)
    submit: bool = False
    submit_selector: str | None = None
    tab_id: str | None = None
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class BrowserSummarizePageInput(ToolInputModel):
    operation: Literal["summarize_page"] = "summarize_page"
    tab_id: str | None = None
    url: str | None = None
    url_contains: str | None = None
    title_contains: str | None = None
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class BrowserCheckPageUpdateInput(ToolInputModel):
    operation: Literal["check_page_update"] = "check_page_update"
    url: str | None = None
    objective: str | None = None
    previous_observation: str | None = None
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)

    @model_validator(mode="after")
    def _require_target(self) -> "BrowserCheckPageUpdateInput":
        if not (self.url or self.objective):
            raise ValueError("browser.check_page_update requires 'url' or 'objective' to identify the page")
        return self


class BrowserResearchPagesInput(ToolInputModel):
    operation: Literal["research_pages"] = "research_pages"
    query: str | None = None
    objective: str | None = None
    page_limit: int = Field(default=10, ge=1, le=50)
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)

    @model_validator(mode="after")
    def _require_query_or_objective(self) -> "BrowserResearchPagesInput":
        if not (self.query or self.objective):
            raise ValueError("browser.research_pages requires 'query' or 'objective'")
        return self


class BrowserExtractPageStateInput(ToolInputModel):
    operation: Literal["extract_page_state"] = "extract_page_state"
    tab_id: str | None = None
    url: str | None = None
    url_contains: str | None = None
    title_contains: str | None = None
    wait_seconds: float | None = Field(default=None, ge=0.0, le=30.0)


class BrowserFillFormStepInput(ToolInputModel):
    operation: Literal["fill_form_step"] = "fill_form_step"
    fields: dict[str, str] = Field(default_factory=dict)
    submit: bool = False
    submit_selector: str | None = None
    tab_id: str | None = None
    url_contains: str | None = None
    title_contains: str | None = None
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

    @model_validator(mode="after")
    def _require_action(self) -> "ComputerActInput":
        if not self.action:
            raise ValueError("computer.use act requires an 'action' dict describing the UI action")
        return self


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


class FilesystemResolveDesktopItemInput(ToolInputModel):
    operation: Literal["resolve_desktop_item"] = "resolve_desktop_item"
    name: str | None = None
    query: str | None = None
    item_type: Literal["file", "folder", "any"] = "any"


class FilesystemFindByDescriptionInput(ToolInputModel):
    operation: Literal["find_by_description"] = "find_by_description"
    root: str | None = None
    description: str = Field(min_length=1)
    max_results: int = Field(default=20, ge=1, le=200)


class FilesystemOpenFileInput(ToolInputModel):
    operation: Literal["open_file"] = "open_file"
    path: str = Field(min_length=1)


class FilesystemReadFileInput(ToolInputModel):
    operation: Literal["read_file"] = "read_file"
    path: str = Field(min_length=1)
    max_chars: int = Field(default=12000, ge=100, le=100000)


class FilesystemWriteTextFileInput(ToolInputModel):
    operation: Literal["write_text_file"] = "write_text_file"
    path: str = Field(min_length=1)
    content: str = ""
    overwrite: bool = False


class FilesystemCollectFolderSnapshotInput(ToolInputModel):
    operation: Literal["collect_folder_snapshot"] = "collect_folder_snapshot"
    root: str = Field(min_length=1)
    max_depth: int = Field(default=2, ge=0, le=10)
    max_entries: int = Field(default=200, ge=1, le=5000)


class FilesystemDescribeFolderInput(ToolInputModel):
    operation: Literal["describe_folder"] = "describe_folder"
    root: str = Field(min_length=1)
    recursive: bool = False
    include_ocr: bool = True
    max_files: int = Field(default=50, ge=1, le=500)
    max_chars_per_file: int = Field(default=4000, ge=100, le=50000)


class FilesystemOrganizePlanInput(ToolInputModel):
    operation: Literal["organize_plan"] = "organize_plan"
    root: str = Field(min_length=1)
    strategy: Literal["by_type", "by_extension"] = "by_type"
    recursive: bool = False
    max_files: int = Field(default=1000, ge=1, le=10000)


class FilesystemRenamePlanInput(ToolInputModel):
    operation: Literal["rename_plan"] = "rename_plan"
    root: str = Field(min_length=1)
    strategy: Literal["by_content", "by_name"] = "by_content"
    recursive: bool = False
    max_files: int = Field(default=1000, ge=1, le=10000)


class FilesystemManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation: Literal["move", "copy", "rename"] = "move"
    source: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    reason: str | None = None
    before_name: str | None = None
    after_name: str | None = None


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


class CodeInterpreterRunPythonInput(ToolInputModel):
    operation: Literal["run_python"] = "run_python"
    code: str = Field(min_length=1)
    objective: str | None = None
    workspace_dir: str | None = None
    script_name: str = "script.py"


class CodeInterpreterGenerateAndRunInput(ToolInputModel):
    operation: Literal["generate_and_run"] = "generate_and_run"
    objective: str = Field(min_length=1)
    context: str | None = None
    workspace_dir: str | None = None
    script_name: str = "script.py"


class CodeInterpreterOutput(ToolOutputModel):
    operation: str = Field(min_length=1)
    workspace_dir: str = Field(min_length=1)
    script_path: str | None = None
    files_before: list[str] = Field(default_factory=list)
    files_after: list[str] = Field(default_factory=list)
    files_created: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    summary: str | None = None
    generated: bool = False


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


class TTSSynthesizeOutput(ToolOutputModel):
    operation: Literal["synthesize"] = "synthesize"
    path: str = Field(min_length=1)
    voice: str | None = None
    provider: str = Field(min_length=1)
    sample_rate: int | None = Field(default=None, ge=1)
    summary: str | None = None


class ArtifactDeliveryOutput(ToolOutputModel):
    operation: str = Field(min_length=1)
    delivered: bool = False
    delivery_method: str | None = None
    artifact_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    path: str | None = None
    chat_id: str | None = None
    summary: str | None = None
    telegram_result: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class DocumentManageOutput(ToolOutputModel):
    operation: str = Field(min_length=1)
    path: str | None = None
    artifact_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    text: str | None = None
    summary: str | None = None
    slide_count: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodingAgentOutput(ToolOutputModel):
    operation: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    workspace_dir: str | None = None
    session_id: str | None = None
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    limit_state: dict[str, Any] = Field(default_factory=dict)
    files_before: list[str] = Field(default_factory=list)
    files_after: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    summary: str | None = None


class ScheduleManageOutput(ToolOutputModel):
    operation: str = Field(min_length=1)
    schedule_id: str | None = None
    schedules: list[dict[str, Any]] = Field(default_factory=list)
    task_id: str | None = None
    next_run_at: str | None = None
    summary: str | None = None


class TaskStatusOutput(ToolOutputModel):
    operation: Literal["status"] = "status"
    summary: str = Field(min_length=1)
    task_status: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] | None = None


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
    visited_urls: list[str] = Field(default_factory=list)
    page_summaries: list[dict[str, Any]] = Field(default_factory=list)
    forms: list[dict[str, Any]] = Field(default_factory=list)


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
    path: str | None = None
    entries: list[dict[str, Any]] = Field(default_factory=list)
    text: str | None = None
    content_preview: str | None = None
    content_summary: str | None = None
    manifest: list[dict[str, Any]] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    dry_run: bool = False
    summary: str | None = None
