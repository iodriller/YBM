from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from agent_control.config import AppSettings
from agent_control.schemas import Capability, PlanModel
from agent_control.tools.adapter_factory import AdapterFactoryAdapter
from agent_control.tools.artifact_delivery import ArtifactDeliveryAdapter
from agent_control.tools.browser import BrowserAdapter
from agent_control.tools.coding_assistant import GenericTerminalAgentAdapter
from agent_control.tools.coding_agent import CodingAgentAdapter
from agent_control.tools.computer_use import ComputerUseAdapter
from agent_control.tools.contracts import (
    AdapterFactoryAssessInput,
    AdapterFactoryAssessOutput,
    AdapterFactoryScaffoldInput,
    AdapterFactoryScaffoldOutput,
    ArtifactDeliverInput,
    ArtifactDeliveryOutput,
    BrowserClickInput,
    BrowserCloseTabInput,
    BrowserCheckPageUpdateInput,
    BrowserExtractPageStateInput,
    BrowserFillFormInput,
    BrowserFillFormStepInput,
    BrowserInspectTabsInput,
    BrowserNavigateInput,
    BrowserOpenInput,
    BrowserResearchInput,
    BrowserResearchPagesInput,
    BrowserScreenshotInput,
    BrowserSearchInput,
    BrowserSummarizePageInput,
    BrowserToolOutput,
    CodeInterpreterGenerateAndRunInput,
    CodeInterpreterOutput,
    CodeInterpreterRunPythonInput,
    CodingAgentInput,
    CodingAgentOutput,
    CodingAssistantInput,
    CodingAssistantOutput,
    ComputerActInput,
    ComputerObserveInput,
    ComputerRunGoalInput,
    ComputerUseOutput,
    DocumentManageInput,
    DocumentManageOutput,
    FilesystemCollectFolderSnapshotInput,
    FilesystemDescribeFolderInput,
    FilesystemFindByDescriptionInput,
    FilesystemOpenFileInput,
    FilesystemReadFileInput,
    FilesystemWriteTextFileInput,
    FilesystemResolveDesktopItemInput,
    FilesystemApplyManifestInput,
    FilesystemInspectInput,
    FilesystemManageOutput,
    FilesystemOrganizePlanInput,
    FilesystemRenamePlanInput,
    FilesystemSearchInput,
    ScheduleManageInput,
    ScheduleManageOutput,
    TaskStatusInput,
    TaskStatusOutput,
    TTSSynthesizeInput,
    TTSSynthesizeOutput,
    VSCodeCopilotTerminalInput,
    VSCodeTerminalCommandInput,
    VSCodeTerminalToolOutput,
    WorkspaceLaunchStaticInput,
    WorkspaceLaunchStaticOutput,
    WorkspaceMaterializeStaticAppInput,
    WorkspaceMaterializeStaticAppOutput,
    WorkspacePrepareInput,
    WorkspacePrepareOutput,
    WorkspaceWebAppPreviewInput,
    WorkspaceWebAppPreviewOutput,
    WorkspaceWriteFilesInput,
    WorkspaceWriteFilesOutput,
)
from agent_control.tools.code_interpreter import CodeInterpreterAdapter
from agent_control.tools.document_manage import DocumentManageAdapter
from agent_control.tools.filesystem_manage import FilesystemManageAdapter
from agent_control.tools.local_workspace import LocalWorkspaceAdapter
from agent_control.tools.schedule_manage import ScheduleManageAdapter
from agent_control.tools.task_status import TaskStatusAdapter
from agent_control.tools.tts import build_tts_adapter
from agent_control.tools.vscode_bridge import VSCodeBridgeTerminalAdapter


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    capability: Capability
    enabled: bool
    description: str
    operations: tuple[str, ...] = ()
    lifecycle: str = "runtime"
    input_schema: type[BaseModel] | None = None
    operation_schemas: dict[str, type[BaseModel]] | None = None
    output_schema: type[BaseModel] | None = None
    operation_output_schemas: dict[str, type[BaseModel]] | None = None
    default_operation: str | None = None
    # Worked usage examples shown to the planner. Each entry is a `tool_input`
    # dict the planner can imitate. The 8B model imitates concrete examples
    # much more reliably than it follows abstract descriptions.
    examples: tuple[dict, ...] = ()

    def validate_input(self, value: dict) -> dict:
        return self._validate_schema(value, self.input_schema, self.operation_schemas, "input")

    def validate_output(self, value: dict) -> dict:
        return self._validate_schema(value, self.output_schema, self.operation_output_schemas, "output")

    def _validate_schema(
        self,
        value: dict,
        base_schema: type[BaseModel] | None,
        operation_schemas: dict[str, type[BaseModel]] | None,
        kind: str,
    ) -> dict:
        schema = base_schema
        payload = dict(value or {})
        if operation_schemas:
            operation = str(payload.get("operation") or self.default_operation or "")
            schema = operation_schemas.get(operation)
            if schema is None:
                expected = ", ".join(sorted(operation_schemas))
                raise ValueError(
                    f"unsupported operation for {self.name}: {operation or '<missing>'}; "
                    f"expected one of: {expected}"
                )
            payload["operation"] = operation
        if schema is None:
            return payload
        try:
            return schema.model_validate(payload).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            raise ValueError(f"invalid {kind} for {self.name}: {exc}") from exc


@dataclass(frozen=True)
class ToolRegistry:
    adapters: dict[str, object]
    definitions: tuple[ToolDefinition, ...]

    def context(self) -> str:
        lines = ["Available worker tools:"]
        for definition in self.definitions:
            status = "enabled" if definition.enabled else "disabled"
            operations = f" operations={','.join(definition.operations)}" if definition.operations else ""
            lines.append(
                f"- {definition.name}: {status}; capability={definition.capability.value}; "
                f"lifecycle={definition.lifecycle}; {definition.description}{operations}"
            )
            if definition.enabled and definition.examples:
                # Show worked examples inline — the planner imitates these
                # better than abstract descriptions of input shape.
                for ex in definition.examples:
                    lines.append(f"    example tool_input: {json.dumps(ex, ensure_ascii=False)}")
        return "\n".join(lines)

    def vault_summary(self) -> str:
        lines = ["Capability vault:"]
        for definition in self.definitions:
            state = "available" if definition.enabled else "known_gap"
            lines.append(f"- {definition.name}: {state}; {definition.description}")
        return "\n".join(lines)

    def validate_plan(self, plan: PlanModel) -> PlanModel:
        definitions = {definition.name: definition for definition in self.definitions}
        errors: list[str] = []
        steps = []
        required_capabilities = list(plan.required_capabilities)
        for index, step in enumerate(plan.steps, start=1):
            if not step.tool_name:
                steps.append(step)
                continue
            definition = definitions.get(step.tool_name)
            if definition is None:
                errors.append(f"step {index} uses unregistered tool {step.tool_name!r}")
                steps.append(step)
                continue
            if not definition.enabled:
                errors.append(f"step {index} uses disabled tool {step.tool_name!r}")
            try:
                validated_input = definition.validate_input(step.tool_input)
            except ValueError as exc:
                errors.append(f"step {index} {exc}")
                validated_input = step.tool_input
            step_capabilities = list(step.required_capabilities)
            if definition.capability not in step_capabilities:
                step_capabilities.insert(0, definition.capability)
            if definition.capability not in required_capabilities:
                required_capabilities.append(definition.capability)
            steps.append(
                step.model_copy(
                    update={
                        "tool_input": validated_input,
                        "required_capabilities": step_capabilities,
                    }
                )
            )

        if errors:
            raise ValueError("plan failed registry validation:\n" + "\n".join(f"- {error}" for error in errors))
        return plan.model_copy(update={"steps": steps, "required_capabilities": required_capabilities})


def build_tool_registry(
    settings: AppSettings,
    backend_base_url: str,
    provider: object | None = None,
    should_continue: Callable[[str], bool] | None = None,
    artifact_repository: object | None = None,
    task_repository: object | None = None,
    repositories: object | None = None,
    audit_logger: object | None = None,
    telegram_client: object | None = None,
) -> ToolRegistry:
    adapters: dict[str, object] = {}
    definitions: list[ToolDefinition] = []

    workspace_enabled = _capability_enabled(settings, Capability.FILESYSTEM_WRITE) and settings.adapters.workspace.enabled
    definitions.append(
        ToolDefinition(
            name="workspace.manage",
            capability=Capability.FILESYSTEM_WRITE,
            enabled=workspace_enabled,
            description=f"manage task workspaces under {settings.adapters.workspace.root_dir}",
            operations=("prepare", "write_files", "materialize_static_app", "launch_static", "web_app_preview"),
            operation_schemas={
                "prepare": WorkspacePrepareInput,
                "write_files": WorkspaceWriteFilesInput,
                "materialize_static_app": WorkspaceMaterializeStaticAppInput,
                "launch_static": WorkspaceLaunchStaticInput,
                "web_app_preview": WorkspaceWebAppPreviewInput,
            },
            operation_output_schemas={
                "prepare": WorkspacePrepareOutput,
                "write_files": WorkspaceWriteFilesOutput,
                "materialize_static_app": WorkspaceMaterializeStaticAppOutput,
                "launch_static": WorkspaceLaunchStaticOutput,
                "web_app_preview": WorkspaceWebAppPreviewOutput,
            },
            default_operation="prepare",
        )
    )
    if settings.adapters.workspace.enabled:
        workspace = LocalWorkspaceAdapter(settings.adapters.workspace)
        adapters["workspace.manage"] = workspace
        adapters["workspace.web_app"] = workspace

    filesystem_enabled = (
        settings.adapters.computer_use.enabled
        and _capability_enabled(settings, Capability.FILESYSTEM_WRITE)
    )
    definitions.append(
        ToolDefinition(
            name="filesystem.manage",
            capability=Capability.FILESYSTEM_WRITE,
            enabled=filesystem_enabled,
            description=(
                "inspect, search, plan organization, and apply move/copy manifests inside configured "
                f"roots: {', '.join(settings.adapters.computer_use.allowed_roots) or '<none>'}"
            ),
            operations=(
                "inspect_folder",
                "search",
                "resolve_desktop_item",
                "find_by_description",
                "open_file",
                "read_file",
                "write_text_file",
                "collect_folder_snapshot",
                "describe_folder",
                "organize_plan",
                "rename_plan",
                "apply_manifest",
            ),
            operation_schemas={
                "inspect_folder": FilesystemInspectInput,
                "search": FilesystemSearchInput,
                "resolve_desktop_item": FilesystemResolveDesktopItemInput,
                "find_by_description": FilesystemFindByDescriptionInput,
                "open_file": FilesystemOpenFileInput,
                "read_file": FilesystemReadFileInput,
                "write_text_file": FilesystemWriteTextFileInput,
                "collect_folder_snapshot": FilesystemCollectFolderSnapshotInput,
                "describe_folder": FilesystemDescribeFolderInput,
                "organize_plan": FilesystemOrganizePlanInput,
                "rename_plan": FilesystemRenamePlanInput,
                "apply_manifest": FilesystemApplyManifestInput,
            },
            output_schema=FilesystemManageOutput,
            operation_output_schemas=_same_output_schema(
                (
                    "inspect_folder",
                    "search",
                    "resolve_desktop_item",
                    "find_by_description",
                    "open_file",
                    "read_file",
                    "write_text_file",
                    "collect_folder_snapshot",
                    "describe_folder",
                    "organize_plan",
                    "rename_plan",
                    "apply_manifest",
                ),
                FilesystemManageOutput,
            ),
            default_operation="inspect_folder",
            examples=(
                {"operation": "inspect_folder", "root": "desktop"},
                {"operation": "search", "root": "desktop", "query": "resume"},
                {"operation": "read_file", "path": "{{last_entry_path}}", "max_chars": 8000},
            ),
        )
    )
    if settings.adapters.computer_use.enabled:
        adapters["filesystem.manage"] = FilesystemManageAdapter(settings.adapters.computer_use.allowed_roots, provider=provider)  # type: ignore[arg-type]

    factory_enabled = _capability_enabled(settings, Capability.FILESYSTEM_WRITE) and settings.adapters.adapter_factory.enabled
    definitions.append(
        ToolDefinition(
            name="adapter.factory",
            capability=Capability.FILESYSTEM_WRITE,
            enabled=factory_enabled,
            description=f"scaffold generated adapter proposals under {settings.adapters.adapter_factory.root_dir}",
            operations=("assess", "scaffold"),
            lifecycle="scaffold",
            operation_schemas={
                "assess": AdapterFactoryAssessInput,
                "scaffold": AdapterFactoryScaffoldInput,
            },
            operation_output_schemas={
                "assess": AdapterFactoryAssessOutput,
                "scaffold": AdapterFactoryScaffoldOutput,
            },
            default_operation="scaffold",
        )
    )
    if settings.adapters.adapter_factory.enabled:
        adapters["adapter.factory"] = AdapterFactoryAdapter(settings.adapters.adapter_factory)

    code_interpreter_enabled = _capability_enabled(settings, Capability.TERMINAL_RUN) and settings.adapters.code_interpreter.enabled
    definitions.append(
        ToolDefinition(
            name="code.interpreter",
            capability=Capability.TERMINAL_RUN,
            enabled=code_interpreter_enabled,
            description=(
                "generate and run bounded local Python scripts inside managed workspaces under "
                f"{settings.adapters.code_interpreter.workspace_root}"
            ),
            operations=("run_python", "generate_and_run"),
            operation_schemas={
                "run_python": CodeInterpreterRunPythonInput,
                "generate_and_run": CodeInterpreterGenerateAndRunInput,
            },
            operation_output_schemas={
                "run_python": CodeInterpreterOutput,
                "generate_and_run": CodeInterpreterOutput,
            },
            default_operation="run_python",
            examples=(
                {"operation": "generate_and_run",
                 "objective": "compute the 20th Fibonacci number and print it"},
                {"operation": "generate_and_run",
                 "objective": "write a Python script using openpyxl that creates sales_data.xlsx with sample sales rows"},
            ),
        )
    )
    if settings.adapters.code_interpreter.enabled:
        adapters["code.interpreter"] = CodeInterpreterAdapter(
            settings.adapters.code_interpreter,
            provider=provider,  # type: ignore[arg-type]
            artifacts=artifact_repository,
        )

    vscode_enabled = _capability_enabled(settings, Capability.VSCODE_WRITE_FILES) and settings.adapters.vscode.enabled
    definitions.append(
        ToolDefinition(
            name="vscode.copilot_terminal",
            capability=Capability.VSCODE_WRITE_FILES,
            enabled=vscode_enabled,
            description="send a prompt to VS Code/Copilot terminal or local Copilot CLI fallback",
            input_schema=VSCodeCopilotTerminalInput,
            output_schema=VSCodeTerminalToolOutput,
        )
    )
    definitions.append(
        ToolDefinition(
            name="vscode.terminal_command",
            capability=Capability.VSCODE_WRITE_FILES,
            enabled=vscode_enabled,
            description="queue an explicit terminal command through the VS Code bridge",
            input_schema=VSCodeTerminalCommandInput,
            output_schema=VSCodeTerminalToolOutput,
        )
    )
    if settings.adapters.vscode.enabled:
        vscode = VSCodeBridgeTerminalAdapter(settings.adapters.vscode, backend_base_url)
        adapters["vscode.terminal_command"] = vscode
        adapters["vscode.copilot_terminal"] = vscode

    coding_enabled = _capability_enabled(settings, Capability.TERMINAL_RUN) and settings.adapters.coding_assistant.enabled
    definitions.append(
        ToolDefinition(
            name="coding_assistant",
            capability=Capability.TERMINAL_RUN,
            enabled=coding_enabled,
            description="run the configured local coding assistant command template",
            input_schema=CodingAssistantInput,
            output_schema=CodingAssistantOutput,
        )
    )
    if settings.adapters.coding_assistant.enabled:
        adapters["coding_assistant"] = GenericTerminalAgentAdapter(settings.adapters.coding_assistant)

    tts_enabled = _capability_enabled(settings, Capability.TTS_SYNTHESIZE) and settings.adapters.tts.enabled
    definitions.append(
        ToolDefinition(
            name="tts.synthesize",
            capability=Capability.TTS_SYNTHESIZE,
            enabled=tts_enabled,
            description="synthesize local speech audio with the configured Kokoro ONNX runtime",
            operations=("synthesize",),
            input_schema=TTSSynthesizeInput,
            output_schema=TTSSynthesizeOutput,
            default_operation="synthesize",
        )
    )
    if settings.adapters.tts.enabled:
        adapters["tts.synthesize"] = build_tts_adapter(settings.adapters.tts)

    coding_agent_enabled = _capability_enabled(settings, Capability.TERMINAL_RUN) and settings.adapters.coding_agent.enabled
    definitions.append(
        ToolDefinition(
            name="coding.agent",
            capability=Capability.TERMINAL_RUN,
            enabled=coding_agent_enabled,
            description="run explicitly requested Codex or GitHub Copilot CLI work inside a task workspace",
            operations=("plan", "run_step", "run_goal", "status", "limits", "resume", "stop"),
            input_schema=CodingAgentInput,
            output_schema=CodingAgentOutput,
            operation_output_schemas=_same_output_schema(
                ("plan", "run_step", "run_goal", "status", "limits", "resume", "stop"),
                CodingAgentOutput,
            ),
            default_operation="run_goal",
        )
    )
    if settings.adapters.coding_agent.enabled:
        adapters["coding.agent"] = CodingAgentAdapter(settings.adapters.coding_agent)

    schedule_enabled = _capability_enabled(settings, Capability.SCHEDULE_MANAGE) and settings.scheduler.enabled
    definitions.append(
        ToolDefinition(
            name="schedule.manage",
            capability=Capability.SCHEDULE_MANAGE,
            enabled=schedule_enabled,
            description="create, list, pause, resume, delete, or run recurring task schedules",
            operations=("create", "list", "pause", "resume", "delete", "run_now"),
            input_schema=ScheduleManageInput,
            output_schema=ScheduleManageOutput,
            operation_output_schemas=_same_output_schema(
                ("create", "list", "pause", "resume", "delete", "run_now"),
                ScheduleManageOutput,
            ),
            default_operation="create",
        )
    )
    if repositories is not None and audit_logger is not None:
        adapters["schedule.manage"] = ScheduleManageAdapter(
            repositories,  # type: ignore[arg-type]
            audit_logger,  # type: ignore[arg-type]
            default_timezone=settings.scheduler.default_timezone,
        )

    task_status_enabled = repositories is not None and _capability_enabled(settings, Capability.TELEGRAM_RECEIVE)
    definitions.append(
        ToolDefinition(
            name="task.status",
            capability=Capability.TELEGRAM_RECEIVE,
            enabled=task_status_enabled,
            description="report current task, plan, active, completed, and blocked state for status questions",
            operations=("status",),
            input_schema=TaskStatusInput,
            output_schema=TaskStatusOutput,
            default_operation="status",
            examples=(
                {"operation": "status", "limit": 10},
            ),
        )
    )
    if repositories is not None:
        adapters["task.status"] = TaskStatusAdapter(repositories)  # type: ignore[arg-type]

    artifact_delivery_enabled = _capability_enabled(settings, Capability.TELEGRAM_SEND)
    definitions.append(
        ToolDefinition(
            name="artifact.deliver",
            capability=Capability.TELEGRAM_SEND,
            enabled=artifact_delivery_enabled,
            description="list task artifacts and deliver screenshots or files to the source Telegram chat",
            operations=("send_file", "send_latest", "send_screenshot", "list_artifacts"),
            input_schema=ArtifactDeliverInput,
            output_schema=ArtifactDeliveryOutput,
            operation_output_schemas=_same_output_schema(
                ("send_file", "send_latest", "send_screenshot", "list_artifacts"),
                ArtifactDeliveryOutput,
            ),
            default_operation="send_latest",
            examples=(
                # Deliver a file by basename — finds files produced by a prior
                # code.interpreter step automatically (registered as artifacts).
                {"operation": "send_file", "path": "sales_data.xlsx"},
                {"operation": "send_screenshot"},
                {"operation": "send_latest"},
            ),
        )
    )
    if artifact_repository is not None and task_repository is not None:
        adapters["artifact.deliver"] = ArtifactDeliveryAdapter(
            artifact_repository,  # type: ignore[arg-type]
            task_repository,  # type: ignore[arg-type]
            telegram_client=telegram_client,  # type: ignore[arg-type]
            allowed_roots=_artifact_delivery_roots(settings),
            recent_fallback_enabled=settings.adapters.artifact_delivery.recent_artifact_fallback_enabled,
        )

    document_enabled = _capability_enabled(settings, Capability.FILESYSTEM_WRITE)
    definitions.append(
        ToolDefinition(
            name="document.manage",
            capability=Capability.FILESYSTEM_WRITE,
            enabled=document_enabled,
            description="inspect documents, summarize PDFs, and create or revise PowerPoint files as task artifacts",
            operations=("inspect_document", "extract_text", "summarize_pdf", "create_presentation", "update_presentation"),
            input_schema=DocumentManageInput,
            output_schema=DocumentManageOutput,
            operation_output_schemas=_same_output_schema(
                ("inspect_document", "extract_text", "summarize_pdf", "create_presentation", "update_presentation"),
                DocumentManageOutput,
            ),
            default_operation="inspect_document",
            examples=(
                {"operation": "summarize_pdf", "path": "{{last_entry_path}}"},
                {"operation": "create_presentation",
                 "title": "Weekly Update",
                 "content": "Status: green. Blockers: none."},
            ),
        )
    )
    if artifact_repository is not None:
        adapters["document.manage"] = DocumentManageAdapter(
            artifact_repository,  # type: ignore[arg-type]
            provider=provider,
            allowed_roots=_document_roots(settings),
        )

    computer_use_enabled = (
        settings.adapters.computer_use.enabled
        and settings.adapters.desktop.control_enabled
        and _capability_enabled(settings, Capability.DESKTOP_CONTROL)
    )
    definitions.append(
        ToolDefinition(
            name="computer.use",
            capability=Capability.DESKTOP_CONTROL,
            enabled=computer_use_enabled,
            description="observe and control the local Windows desktop with bounded screenshot/action loops",
            operations=("observe", "act", "run_goal"),
            operation_schemas={
                "observe": ComputerObserveInput,
                "act": ComputerActInput,
                "run_goal": ComputerRunGoalInput,
            },
            output_schema=ComputerUseOutput,
            operation_output_schemas=_same_output_schema(("observe", "act", "run_goal"), ComputerUseOutput),
            default_operation="observe",
        )
    )
    if settings.adapters.computer_use.enabled:
        adapters["computer.use"] = ComputerUseAdapter(
            settings.adapters.computer_use,
            provider=provider,
            should_continue=should_continue,
        )

    # NOTE: There used to be a `desktop.screenshot` ToolDefinition here, but no
    # adapter was ever registered for it — the planner happily picked it from
    # the catalog and execution then failed with "tool adapter not registered".
    # All real screenshot work is done by `computer.use observe` (captures +
    # returns the image) and `artifact.deliver send_screenshot` (delivers it).
    # The legacy `/screenshot` command in telegram.py is a separate code path
    # that uses Capability.DESKTOP_SCREENSHOT directly, unaffected by removing
    # this tool advertisement. See docs/FIX_PLAN.md Priority 1 for details.

    browser_open_enabled = settings.adapters.browser.enabled and _capability_enabled(settings, Capability.BROWSER_OPEN)
    browser_control_enabled = settings.adapters.browser.enabled and _capability_enabled(settings, Capability.BROWSER_CONTROL)
    definitions.append(
        ToolDefinition(
            name="browser.open",
            capability=Capability.BROWSER_OPEN,
            enabled=browser_open_enabled,
            description=(
                "open Chrome, search the web, summarize exposed tabs/pages, and capture browser screenshots "
                f"through DevTools at {settings.adapters.browser.host}:{settings.adapters.browser.remote_debugging_port}"
            ),
            operations=("open", "search", "research", "inspect_tabs", "screenshot", "summarize_page", "research_pages"),
            operation_schemas={
                "open": BrowserOpenInput,
                "search": BrowserSearchInput,
                "research": BrowserResearchInput,
                "inspect_tabs": BrowserInspectTabsInput,
                "screenshot": BrowserScreenshotInput,
                "summarize_page": BrowserSummarizePageInput,
                "research_pages": BrowserResearchPagesInput,
            },
            output_schema=BrowserToolOutput,
            operation_output_schemas=_same_output_schema(
                ("open", "search", "research", "inspect_tabs", "screenshot", "summarize_page", "research_pages"),
                BrowserToolOutput,
            ),
            default_operation="open",
            examples=(
                {"operation": "open", "url": "https://dizibox.com"},
                {"operation": "summarize_page", "objective": "list the first 5 new episodes"},
                {"operation": "search", "query": "python official docs"},
            ),
        )
    )
    definitions.append(
        ToolDefinition(
            name="browser.control",
            capability=Capability.BROWSER_CONTROL,
            enabled=browser_control_enabled,
            description="navigate, close tabs, click elements, and fill simple forms in Chrome through DevTools",
            operations=(
                "navigate",
                "close_tab",
                "click",
                "fill_form",
                "check_page_update",
                "extract_page_state",
                "fill_form_step",
            ),
            operation_schemas={
                "navigate": BrowserNavigateInput,
                "close_tab": BrowserCloseTabInput,
                "click": BrowserClickInput,
                "fill_form": BrowserFillFormInput,
                "check_page_update": BrowserCheckPageUpdateInput,
                "extract_page_state": BrowserExtractPageStateInput,
                "fill_form_step": BrowserFillFormStepInput,
            },
            output_schema=BrowserToolOutput,
            operation_output_schemas=_same_output_schema(
                (
                    "navigate",
                    "close_tab",
                    "click",
                    "fill_form",
                    "check_page_update",
                    "extract_page_state",
                    "fill_form_step",
                ),
                BrowserToolOutput,
            ),
            default_operation="navigate",
            examples=(
                {"operation": "navigate", "url": "https://example.com/contact"},
                {"operation": "fill_form",
                 "fields": {"name": "Oney", "email": "oney@example.com", "message": "Hello"},
                 "submit": True},
                {"operation": "click", "selector": "button.submit"},
            ),
        )
    )
    if settings.adapters.browser.enabled:
        browser = BrowserAdapter(settings.adapters.browser)
        adapters["browser.open"] = browser
        adapters["browser.control"] = browser

    return ToolRegistry(adapters=adapters, definitions=tuple(definitions))


def _capability_enabled(settings: AppSettings, capability: Capability) -> bool:
    policy = settings.capabilities.get(capability)
    return bool(policy and policy.enabled)


def _same_output_schema(operations: tuple[str, ...], schema: type[BaseModel]) -> dict[str, type[BaseModel]]:
    return {operation: schema for operation in operations}


def _artifact_delivery_roots(settings: AppSettings) -> list[str]:
    return [
        settings.storage.artifact_dir,
        settings.adapters.workspace.root_dir,
        settings.adapters.browser.screenshot_dir,
        settings.adapters.computer_use.screenshot_dir,
        # Files produced by code.interpreter live here — without this entry,
        # "generate a file and send it" requests can't deliver the result.
        settings.adapters.code_interpreter.workspace_root,
        *settings.adapters.computer_use.allowed_roots,
    ]


def _document_roots(settings: AppSettings) -> list[str]:
    return [
        settings.storage.artifact_dir,
        settings.adapters.workspace.root_dir,
        *settings.adapters.computer_use.allowed_roots,
    ]
