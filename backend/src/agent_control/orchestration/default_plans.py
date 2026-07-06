from __future__ import annotations

import logging
import re
from pathlib import Path

from agent_control.config import AppSettings
from agent_control.orchestration.fulfillment import expected_fulfillment
from agent_control.prompts import render_prompt
from agent_control.tools.local_workspace import workspace_dir_for_task
from agent_control.schemas import (
    Capability,
    OrchestrationIntent,
    PlanModel,
    PlanPostcondition,
    PlanStep,
    PostconditionType,
    RiskLevel,
    TaskRecord,
    TaskType,
)


logger = logging.getLogger(__name__)


def build_default_task_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    """Fallback plan factory — only handles explicit system commands.

    The LLM planner is the primary planning path. This function is only called when
    the LLM planner is unavailable or produced no plan. It handles status requests
    and nothing else; all other task routing belongs to the LLM planner.
    """
    # Status requests are handled deterministically without LLM
    status_plan = _build_status_plan(settings, task)
    if status_plan is not None:
        return status_plan

    # Intent-based status routing
    intent = _task_intent(task)
    if intent is not None and intent.route.value == "status":
        return _build_status_plan(settings, task, objective=intent.objective or task.objective)

    # Everything else is handled by the LLM planner
    return None


def build_evaluator_recovery_plan(settings: AppSettings, task: TaskRecord, failure_reason: str) -> PlanModel | None:
    reason = failure_reason.lower()
    if int(task.metadata.get("evaluator_repair_count", 0)) >= 2:
        return None
    if "expected_desktop_observation_missing" in reason:
        if _looks_like_desktop_file_listing(task.objective):
            return _build_filesystem_manage_plan(settings, task)
        return _build_computer_use_plan(settings, task)
    if (
        "outside configured delivery roots" in reason
        or "no deliverable artifact" in reason
        or "expected_artifact_delivered_missing" in reason
    ):
        return _build_file_lookup_delivery_plan(settings, task) or _build_artifact_delivery_plan(settings, task)
    if "tool adapter not registered" in reason or "unregistered tool" in reason or "connector_missing" in reason:
        return (
            _build_mcp_missing_tool_plan(settings, task, failure_reason)
            or _build_code_interpreter_recovery_plan(settings, task, failure_reason)
            or _build_adapter_factory_plan(settings, task)
        )
    if "unsupported operation" in reason or "validation" in reason:
        return _build_code_interpreter_recovery_plan(settings, task, failure_reason)
    return None


def _task_intent(task: TaskRecord) -> OrchestrationIntent | None:
    raw = task.metadata.get("orchestration_intent") or task.metadata.get("intent")
    if not raw:
        return None
    try:
        return OrchestrationIntent.model_validate(raw)
    except Exception:
        logger.debug("failed to deserialize orchestration intent from task metadata", exc_info=True)
        return None


def _task_request_text(task: TaskRecord) -> str:
    original = task.metadata.get("original_message_text")
    if isinstance(original, str) and original.strip():
        return original.strip()
    return task.objective


def _build_status_plan(settings: AppSettings, task: TaskRecord, *, objective: str | None = None) -> PlanModel | None:
    task_type = str(task.metadata.get("task_type") or "")
    if task_type != TaskType.STATUS_REQUEST.value and not _looks_like_status_request(task.objective):
        return None
    policy = settings.capabilities.get(Capability.TELEGRAM_RECEIVE)
    if policy is None or not policy.enabled:
        return None
    return PlanModel(
        objective=objective or task.objective,
        assumptions=["The request asks for current task/workflow status, so the worker reads task and plan state."],
        required_capabilities=[Capability.TELEGRAM_RECEIVE],
        steps=[
            PlanStep(
                title="Report task status",
                description="Summarize recent, active, completed, and blocked task state from the repository.",
                required_capabilities=[Capability.TELEGRAM_RECEIVE],
                risk_level=RiskLevel.LOW,
                requires_approval=policy.requires_approval,
                tool_name="task.status",
                tool_input={"operation": "status", "limit": 20, "timeout_seconds": 30},
                expected_output="Current task status, active work, recent completions, blocked work, and plan context.",
            )
        ],
        success_criteria=["A grounded status summary is returned from repository state."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.TASK_STATUS,
                description="Task and plan status were inspected and summarized.",
                required=True,
            )
        ],
    )


def build_default_vscode_development_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if task.metadata.get("task_type") != TaskType.DEVELOPMENT.value:
        return None

    explicit_copilot = _explicitly_requests_copilot(task.objective)
    adapter_plan = _build_adapter_factory_plan(settings, task)
    if adapter_plan is not None:
        return adapter_plan

    workspace_plan = _build_workspace_web_app_plan(settings, task, allow_copilot=explicit_copilot)
    if workspace_plan is not None:
        return workspace_plan

    if not explicit_copilot:
        return None

    policy = settings.capabilities.get(Capability.VSCODE_WRITE_FILES)
    if not settings.adapters.vscode.enabled or policy is None or not policy.enabled:
        return None

    workspace_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    workspace_enabled = bool(settings.adapters.workspace.enabled and workspace_policy and workspace_policy.enabled)
    workspace_dir = str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)) if workspace_enabled else None
    steps: list[PlanStep] = []
    required_capabilities = [Capability.VSCODE_WRITE_FILES]
    assumptions = [
        "The task came from an authorized Telegram message classified as development work.",
        "VS Code bridge write access is enabled for this run.",
    ]
    if workspace_enabled and workspace_policy is not None:
        required_capabilities.insert(0, Capability.FILESYSTEM_WRITE)
        assumptions.append(f"A task workspace will be prepared under {settings.adapters.workspace.root_dir}.")
        steps.append(
            PlanStep(
                title="Prepare local task workspace",
                description="Create a dedicated workspace directory for generated code, notes, and assistant output.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=workspace_policy.requires_approval,
                tool_name="workspace.manage",
                tool_input={
                    "operation": "prepare",
                    "objective": task.objective,
                    "scope_target": settings.adapters.workspace.root_dir,
                    "timeout_seconds": 30,
                },
                expected_output="A local workspace path for this task.",
            )
        )

    workspace_instruction = (
        f"Use this local workspace for files and commands when tool access allows it: {workspace_dir}. "
        if workspace_dir
        else "If code changes are needed, return exact file paths and contents because no local workspace is enabled. "
    )
    prompt = render_prompt(
        "tools/copilot_development.md",
        workspace_instruction=workspace_instruction,
        objective=task.objective,
    )
    steps.append(
        PlanStep(
            title="Send objective to VS Code Copilot terminal",
            description="Queue the task objective through the VS Code bridge and capture the terminal result when available.",
            required_capabilities=[Capability.VSCODE_WRITE_FILES],
            risk_level=RiskLevel.HIGH,
            requires_approval=policy.requires_approval,
            tool_name="vscode.copilot_terminal",
            tool_input={
                "prompt": prompt,
                "terminal_id": "agent-control-copilot",
                "cwd": workspace_dir,
                "capture_output": True,
                "timeout_seconds": 180,
            },
            expected_output="A VS Code bridge terminal output record tied to the queued command.",
        )
    )

    return PlanModel(
        objective=task.objective,
        assumptions=assumptions,
        required_capabilities=required_capabilities,
        steps=steps,
        success_criteria=["The VS Code bridge accepted the command and reported a final terminal output record."],
        postconditions=_workspace_postconditions() if workspace_enabled else [],
    )


def _build_workspace_web_app_plan(settings: AppSettings, task: TaskRecord, *, allow_copilot: bool = False) -> PlanModel | None:
    if not settings.adapters.workspace.enabled:
        return None
    if not _looks_like_launchable_web_app(task.objective):
        return None

    workspace_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if workspace_policy is None or not workspace_policy.enabled:
        return None

    vscode_policy = settings.capabilities.get(Capability.VSCODE_WRITE_FILES)
    vscode_enabled = bool(settings.adapters.vscode.enabled and vscode_policy and vscode_policy.enabled)
    if not vscode_enabled or not allow_copilot:
        return PlanModel(
            objective=task.objective,
            assumptions=[
                "The task asks for a visible local web-app result.",
                (
                    "Copilot was not explicitly requested, so the workspace preview generator will create the app directly."
                    if vscode_enabled
                    else "VS Code/Copilot is not enabled, so the workspace preview generator will create the app directly."
                ),
                f"Generated files are limited to {settings.adapters.workspace.root_dir}.",
            ],
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            steps=[
                PlanStep(
                    title="Create and launch local web app preview",
                    description="Create a task workspace, write a minimal web app, start a localhost static server, and return the URL.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=workspace_policy.requires_approval,
                    tool_name="workspace.manage",
                    tool_input={
                        "operation": "web_app_preview",
                        "objective": task.objective,
                        "scope_target": settings.adapters.workspace.root_dir,
                        "web_port_start": settings.adapters.workspace.web_port_start,
                        "open_browser": settings.adapters.workspace.open_browser,
                        "timeout_seconds": 60,
                    },
                    expected_output="A local workspace path plus an HTTP preview URL.",
                )
            ],
            success_criteria=["The generated web app is available at a localhost URL and the workspace path is reported."],
            postconditions=_preview_postconditions(),
        )

    workspace_dir = str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id))
    copilot_prompt = _web_app_copilot_prompt(task.objective, workspace_dir)
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The task asks for a visible local web-app result.",
            "Copilot is the primary creator for the app files.",
            "If Copilot cannot write files directly, its code-block output will be materialized into the task workspace.",
            f"Generated files are limited to {settings.adapters.workspace.root_dir}.",
        ],
        required_capabilities=[Capability.FILESYSTEM_WRITE, Capability.VSCODE_WRITE_FILES],
        steps=[
            PlanStep(
                title="Prepare local task workspace",
                description="Create the dedicated workspace directory Copilot should use for generated app files.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=workspace_policy.requires_approval,
                tool_name="workspace.manage",
                tool_input={
                    "operation": "prepare",
                    "objective": task.objective,
                    "scope_target": settings.adapters.workspace.root_dir,
                    "timeout_seconds": 30,
                },
                expected_output="A local workspace path for this task.",
            ),
            PlanStep(
                title="Ask Copilot to create the web app",
                description="Send a concrete implementation prompt to Copilot with the task workspace as the working directory.",
                required_capabilities=[Capability.VSCODE_WRITE_FILES],
                risk_level=RiskLevel.HIGH,
                requires_approval=bool(vscode_policy and vscode_policy.requires_approval),
                tool_name="vscode.copilot_terminal",
                tool_input={
                    "prompt": copilot_prompt,
                    "terminal_id": "agent-control-copilot",
                    "cwd": workspace_dir,
                    "capture_output": True,
                    "require_file_blocks": True,
                    "timeout_seconds": 240,
                },
                expected_output="Copilot creates files in the workspace or returns complete file code blocks.",
            ),
            PlanStep(
                title="Materialize Copilot app files",
                description="Ensure the workspace contains a static web app, using Copilot output when direct file writes were unavailable.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=workspace_policy.requires_approval,
                tool_name="workspace.manage",
                tool_input={
                    "operation": "materialize_static_app",
                    "objective": task.objective,
                    "source_text": "{{last_output}}",
                    "allow_fallback_template": True,
                    "require_index": True,
                    "scope_target": settings.adapters.workspace.root_dir,
                    "timeout_seconds": 30,
                },
                expected_output="Static app files are present in the workspace.",
            ),
            PlanStep(
                title="Launch local web app preview",
                description="Serve the workspace on localhost and return the URL.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=workspace_policy.requires_approval,
                tool_name="workspace.manage",
                tool_input={
                    "operation": "launch_static",
                    "objective": task.objective,
                    "scope_target": settings.adapters.workspace.root_dir,
                    "web_port_start": settings.adapters.workspace.web_port_start,
                    "open_browser": settings.adapters.workspace.open_browser,
                    "ensure_index": False,
                    "timeout_seconds": 60,
                },
                expected_output="A local workspace path plus an HTTP preview URL.",
            ),
        ],
        success_criteria=[
            "Copilot has been used as the creator for the requested app.",
            "The generated web app is available at a localhost URL and the workspace path is reported.",
        ],
        postconditions=_preview_postconditions(),
    )


def _build_computer_use_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.computer_use.enabled or not settings.adapters.desktop.control_enabled:
        return None
    if not _looks_like_computer_use_request(task.objective):
        return None
    policy = settings.capabilities.get(Capability.DESKTOP_CONTROL)
    if policy is None or not policy.enabled:
        return None

    objective = task.objective.strip()
    operation = "observe" if _looks_like_observation_only(objective) else "run_goal"
    action = _deterministic_computer_action(objective)
    if action is not None:
        operation = "act"
    tool_input = (
        {
            "operation": "observe",
            "objective": objective,
            "include_screenshot": True,
            "include_ui_tree": True,
            "summarize": True,
            "timeout_seconds": 60,
        }
        if operation == "observe"
        else {
            "operation": "act",
            "objective": objective,
            "action": action,
            "timeout_seconds": 60,
        }
        if operation == "act"
        else {
            "operation": "run_goal",
            "objective": objective,
            "max_steps": settings.adapters.computer_use.max_steps,
            "include_ui_tree": True,
            "require_vision": True,
            "timeout_seconds": 240,
        }
    )
    steps = [
        PlanStep(
            title="Run bounded computer-use session",
            description="Observe the Windows desktop and, when needed, perform bounded local mouse/keyboard actions.",
            required_capabilities=[Capability.DESKTOP_CONTROL],
            risk_level=RiskLevel.CRITICAL,
            requires_approval=policy.requires_approval,
            tool_name="computer.use",
            tool_input=tool_input,
            expected_output="Desktop observation, screenshot path, actions taken, and final summary.",
        )
    ]
    delivery_step = _artifact_delivery_step(settings, task.objective, screenshot=True)
    if delivery_step is not None:
        steps.append(delivery_step)
    postconditions = _desktop_postconditions()
    if delivery_step is not None:
        postconditions.append(
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="A screenshot or task artifact is delivered back to Telegram.",
            )
        )
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The task needs local desktop observation or UI control.",
            "Computer use is bounded by the configured max step count.",
            "Desktop-control access mode determines whether approval is required.",
        ],
        required_capabilities=[
            Capability.DESKTOP_CONTROL,
            *([Capability.TELEGRAM_SEND] if delivery_step is not None else []),
        ],
        steps=steps,
        success_criteria=["The desktop state was observed or the requested bounded UI action was completed."],
        postconditions=postconditions,
    )


def _build_file_lookup_delivery_plan(
    settings: AppSettings,
    task: TaskRecord,
    *,
    intent: OrchestrationIntent | None = None,
) -> PlanModel | None:
    request_text = _task_request_text(task)
    if _objective_wants_screenshot(request_text):
        return None
    intent_path = (intent.file_path or intent.path) if intent else None
    if (intent_path and _usable_explicit_path(intent_path)) or _path_from_objective(request_text):
        return None
    if not _looks_like_delivery_request(request_text) and intent is None:
        return None
    if not _looks_like_file_reference(request_text) and not (intent and intent.artifact_type):
        return None
    root = (intent.folder_path if intent else None) or _root_from_objective(request_text)
    if not root:
        return None

    write_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    send_policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
    if write_policy is None or not write_policy.enabled or send_policy is None or not send_policy.enabled:
        return None

    query = (
        (intent.query if intent else None)
        or (_query_from_file_reference(intent_path) if intent_path else None)
        or _search_query_from_objective(request_text)
    )
    if query == "*" and _looks_like_file_reference(request_text):
        query = _file_query_from_reference(request_text)
    steps = [
        PlanStep(
            title="Find requested file",
            description="Search the requested safe folder before delivering a file whose exact path was not provided.",
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            risk_level=RiskLevel.HIGH,
            requires_approval=write_policy.requires_approval,
            tool_name="filesystem.manage",
            tool_input={
                "operation": "search",
                "root": root,
                "query": query or "*",
                "include_content": True,
                "max_results": 20,
                "timeout_seconds": 90,
            },
            expected_output="Matching file paths under the configured allowed root.",
        ),
        PlanStep(
            title="Deliver resolved file",
            description="Send the first resolved matching file back to the source Telegram chat.",
            required_capabilities=[Capability.TELEGRAM_SEND],
            risk_level=RiskLevel.LOW,
            requires_approval=send_policy.requires_approval,
            tool_name="artifact.deliver",
            tool_input={
                "operation": "send_file",
                "path": "{{last_entry_path}}",
                "artifact_type": (intent.artifact_type if intent else None) or "document",
                "caption": f"Result for: {request_text[:180]}",
                "timeout_seconds": 60,
            },
            expected_output="Telegram delivery result for the resolved file.",
        ),
    ]
    return PlanModel(
        objective=request_text,
        assumptions=[
            "The user asked for a file without an exact path.",
            "The file is resolved through scoped filesystem search before Telegram delivery.",
        ],
        required_capabilities=[Capability.FILESYSTEM_WRITE, Capability.TELEGRAM_SEND],
        steps=steps,
        success_criteria=["A matching file is found and delivered, or a clear no-match error is reported."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="The resolved file is delivered to Telegram.",
                required=True,
            ),
        ],
    )


def _build_code_interpreter_recovery_plan(settings: AppSettings, task: TaskRecord, failure_reason: str) -> PlanModel | None:
    policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if policy is None or not policy.enabled or not settings.adapters.code_interpreter.enabled:
        return None
    objective = (
        "Recover this failed task with a small bounded Python script only if a script is appropriate. "
        f"Original task: {task.objective}. Failure: {failure_reason}. "
        "Write outputs inside the managed workspace and print a concise summary."
    )
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The evaluator selected the local code interpreter for a bounded repair attempt.",
            "The script must operate inside the managed interpreter workspace.",
        ],
        required_capabilities=[Capability.TERMINAL_RUN],
        steps=[
            PlanStep(
                title="Run evaluator repair script",
                description="Use the local code interpreter to generate and run a small repair or diagnostic script.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.MEDIUM,
                requires_approval=policy.requires_approval,
                tool_name="code.interpreter",
                tool_input={
                    "operation": "generate_and_run",
                    "objective": objective,
                    "workspace_dir": str(workspace_dir_for_task(settings.adapters.code_interpreter.workspace_root, task.id)),
                    "timeout_seconds": settings.adapters.code_interpreter.timeout_seconds,
                },
                expected_output="Interpreter stdout, stderr, changed files, and workspace path.",
            )
        ],
        success_criteria=["The evaluator repair script reports concrete output or a clear reason it cannot repair the task."],
        postconditions=_workspace_postconditions(),
    )


def _build_mcp_missing_tool_plan(settings: AppSettings, task: TaskRecord, failure_reason: str) -> PlanModel | None:
    if not settings.mcp.enabled or not settings.mcp.servers:
        return None
    policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if policy is None or not policy.enabled:
        return None
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "A native tool or connector was missing.",
            "Configured MCP servers are checked before generating a permanent adapter proposal.",
        ],
        required_capabilities=[Capability.TERMINAL_RUN],
        steps=[
            PlanStep(
                title="Discover MCP tools for missing capability",
                description="List configured MCP server tools that might satisfy the missing capability.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="mcp.client",
                tool_input={
                    "operation": "list_tools",
                    "timeout_seconds": 60,
                },
                expected_output="Configured MCP servers and their available tools, or a clear MCP health error.",
            )
        ],
        success_criteria=[
            "MCP discovery reports whether an external tool can cover the missing capability.",
            f"Original failure is preserved for follow-up planning: {failure_reason[:200]}",
        ],
    )


def _build_filesystem_manage_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.computer_use.enabled:
        return None
    if _looks_like_delivery_request(task.objective) and _path_from_objective(task.objective):
        return None
    if not _looks_like_filesystem_manage_request(task.objective):
        return None
    root = _path_from_objective(task.objective) or _folder_root_from_request(_task_request_text(task)) or _root_from_objective(task.objective)
    if root is None:
        return None
    write_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if write_policy is None or not write_policy.enabled:
        return None

    explicit_path = _path_from_objective(task.objective)
    if explicit_path and _looks_like_find_and_read_request(_task_request_text(task), task.objective):
        return PlanModel(
            objective=task.objective,
            assumptions=["The request asks to read an explicit file path through scoped filesystem APIs."],
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            steps=[
                PlanStep(
                    title="Read scoped file",
                    description="Read the requested file content through filesystem APIs.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "read_file",
                        "path": explicit_path,
                        "max_chars": 16000,
                        "timeout_seconds": 60,
                    },
                    expected_output="Readable file content and a concise summary.",
                )
            ],
            success_criteria=["The file content is returned or a clear extraction error is reported."],
            postconditions=_file_organization_postconditions(required=False),
        )

    if _looks_like_file_search(task.objective) or _looks_like_desktop_file_listing(task.objective):
        operation = "inspect_folder" if _looks_like_desktop_file_listing(task.objective) else "search"
        query = _search_query_from_objective(task.objective)
        return PlanModel(
            objective=task.objective,
            assumptions=["The request is safer through scoped filesystem APIs than desktop UI control."],
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            steps=[
                PlanStep(
                    title="Inspect scoped folder" if operation == "inspect_folder" else "Search scoped folder",
                    description=(
                        "List files and folders inside the requested allowed folder."
                        if operation == "inspect_folder"
                        else "Search file names and optional text content inside the requested allowed folder."
                    ),
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": operation,
                        "root": root,
                        **({"query": query, "include_content": True} if operation == "search" else {}),
                        "timeout_seconds": 60,
                    },
                    expected_output="Folder entries or matching file paths under the configured allowed root.",
                )
            ],
            success_criteria=["Folder entries or search results are reported."],
            postconditions=_file_organization_postconditions(required=False),
        )

    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The request asks to organize files in a folder.",
            "The adapter will first create a manifest, then apply exactly that manifest.",
        ],
        required_capabilities=[Capability.FILESYSTEM_WRITE],
        steps=[
            PlanStep(
                title="Plan folder organization",
                description="Create a deterministic move manifest without changing files.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=write_policy.requires_approval,
                tool_name="filesystem.manage",
                tool_input={
                    "operation": "organize_plan",
                    "root": root,
                    "strategy": "by_type",
                    "recursive": False,
                    "timeout_seconds": 60,
                },
                expected_output="A proposed file move manifest.",
            ),
            PlanStep(
                title="Apply folder organization manifest",
                description="Apply the previously generated manifest inside the same allowed folder.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=write_policy.requires_approval,
                tool_name="filesystem.manage",
                tool_input={
                    "operation": "apply_manifest",
                    "root": root,
                    "manifest": "{{last_manifest}}",
                    "dry_run": False,
                    "timeout_seconds": 120,
                },
                expected_output="Moved or copied file paths.",
            ),
        ],
        success_criteria=["The folder organization manifest was applied and changed paths were reported."],
        postconditions=_file_organization_postconditions(),
    )


def _build_artifact_delivery_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not _looks_like_delivery_request(task.objective):
        return None
    policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
    if policy is None or not policy.enabled:
        return None
    lowered = task.objective.lower()
    path = _path_from_objective(task.objective)
    operation = (
        "send_file"
        if path
        else "send_latest"
        if _looks_like_latest_output_delivery_request(task.objective)
        else "send_screenshot"
        if "screenshot" in lowered or "screen shot" in lowered
        else "send_latest"
    )
    tool_input: dict[str, object] = {
        "operation": operation,
        "caption": f"Result for: {task.objective[:180]}",
        "timeout_seconds": 60,
    }
    if path:
        tool_input["path"] = path
    if any(marker in lowered for marker in ("pdf", "document", "powerpoint", "presentation", "pptx", "file")):
        tool_input["artifact_type"] = "document"
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The request asks to send an artifact already associated with this task or conversation.",
            "Artifact delivery uses the source Telegram chat when chat_id is not explicitly provided.",
        ],
        required_capabilities=[Capability.TELEGRAM_SEND],
        steps=[
            PlanStep(
                title="Deliver latest matching task artifact",
                description="Send the latest matching task artifact or screenshot back to Telegram.",
                required_capabilities=[Capability.TELEGRAM_SEND],
                risk_level=RiskLevel.LOW,
                requires_approval=policy.requires_approval,
                tool_name="artifact.deliver",
                tool_input=tool_input,
                expected_output="Telegram delivery result for the requested artifact.",
            )
        ],
        success_criteria=["The requested artifact is sent to Telegram or a clear delivery error is reported."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="The requested artifact is delivered to Telegram.",
            )
        ],
    )


def _build_adapter_factory_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.adapter_factory.enabled:
        return None
    if not _looks_like_adapter_request(task.objective):
        return None

    factory_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if factory_policy is None or not factory_policy.enabled:
        return None

    vscode_policy = settings.capabilities.get(Capability.VSCODE_WRITE_FILES)
    vscode_enabled = bool(settings.adapters.vscode.enabled and vscode_policy and vscode_policy.enabled)
    steps = [
        PlanStep(
            title="Scaffold generated adapter proposal",
            description="Create a cached adapter proposal that can be reviewed and promoted later.",
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            risk_level=RiskLevel.HIGH,
            requires_approval=factory_policy.requires_approval,
            tool_name="adapter.factory",
            tool_input={
                "operation": "scaffold",
                "objective": task.objective,
                "scope_target": settings.adapters.adapter_factory.root_dir,
                "timeout_seconds": 30,
            },
            expected_output="A generated adapter proposal directory with manifest, adapter stub, README, and test placeholder.",
        )
    ]
    required_capabilities = [Capability.FILESYSTEM_WRITE]
    assumptions = [
        "The request asks for a new or missing adapter/tool capability.",
        "Generated adapter work is cached as a proposal and is not auto-imported or executed.",
    ]
    success_criteria = ["A reviewable adapter proposal exists in the generated adapter cache."]
    postconditions = _adapter_postconditions()
    if vscode_enabled and _explicitly_requests_copilot(task.objective):
        required_capabilities.append(Capability.VSCODE_WRITE_FILES)
        assumptions.append("Copilot may refine the generated proposal inside the adapter cache.")
        success_criteria.append("Copilot has been asked to improve the adapter proposal without registering it automatically.")
        steps.append(
            PlanStep(
                title="Ask Copilot to implement the adapter proposal",
                description="Use Copilot to fill in the cached adapter proposal while keeping it isolated from runtime registration.",
                required_capabilities=[Capability.VSCODE_WRITE_FILES],
                risk_level=RiskLevel.HIGH,
                requires_approval=bool(vscode_policy and vscode_policy.requires_approval),
                tool_name="vscode.copilot_terminal",
                tool_input={
                    "prompt": _adapter_copilot_prompt(task.objective),
                    "terminal_id": "agent-control-copilot",
                    "cwd": "{{adapter_dir}}",
                    "capture_output": True,
                    "timeout_seconds": 240,
                },
                expected_output="The adapter proposal files are improved in the cache or Copilot returns implementation guidance.",
            )
        )

    return PlanModel(
        objective=task.objective,
        assumptions=assumptions,
        required_capabilities=required_capabilities,
        steps=steps,
        success_criteria=success_criteria,
        postconditions=postconditions,
    )


def _looks_like_launchable_web_app(objective: str) -> bool:
    return bool(expected_fulfillment(objective).get("preview_url"))


def _workspace_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.WORKSPACE_DIR,
            description="A task workspace directory is reported.",
        )
    ]


def _preview_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.PREVIEW_URL,
            description="A local preview URL is reported.",
        ),
        *_workspace_postconditions(),
    ]


def _adapter_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.ADAPTER_PROPOSAL,
            description="A generated adapter proposal directory is reported.",
        )
    ]


def _desktop_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.DESKTOP_OBSERVATION,
            description="A desktop observation, screenshot, or computer-use summary is reported.",
        )
    ]


def _file_organization_postconditions(required: bool = True) -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.FILE_ORGANIZATION,
            description="A file organization manifest, changed paths, or search results are reported.",
            required=required,
        )
    ]


def _artifact_delivery_step(settings: AppSettings, objective: str, *, screenshot: bool = False) -> PlanStep | None:
    if not _looks_like_delivery_request(objective):
        return None
    policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
    if policy is None or not policy.enabled:
        return None
    lowered = objective.lower()
    tool_input: dict[str, object] = {
        "operation": "send_screenshot" if screenshot or "screenshot" in lowered else "send_latest",
        "caption": f"Result for: {objective[:180]}",
        "timeout_seconds": 60,
    }
    if not screenshot and any(marker in lowered for marker in ("pdf", "document", "powerpoint", "presentation", "pptx", "file")):
        tool_input["artifact_type"] = "document"
    return PlanStep(
        title="Deliver task artifact to Telegram",
        description="Send the screenshot or latest task artifact back to the source Telegram chat.",
        required_capabilities=[Capability.TELEGRAM_SEND],
        risk_level=RiskLevel.LOW,
        requires_approval=policy.requires_approval,
        tool_name="artifact.deliver",
        tool_input=tool_input,
        expected_output="Telegram delivery result for the requested screenshot or file artifact.",
    )


def _looks_like_delivery_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(phrase in lowered for phrase in ("send it", "send me", "send the", "send a", "send screenshot", "send file"))


def _objective_wants_screenshot(objective: str) -> bool:
    lowered = objective.lower()
    return "screenshot" in lowered or "screen shot" in lowered


def _looks_like_latest_output_delivery_request(objective: str) -> bool:
    lowered = objective.lower()
    return _looks_like_delivery_request(objective) and any(
        marker in lowered for marker in ("latest output", "current task", "latest artifact", "any screenshot", "powerpoint artifact")
    )


def _looks_like_adapter_request(objective: str) -> bool:
    lowered = objective.lower()
    has_adapter_word = any(word in lowered for word in ("adapter", "tool", "capability", "connector"))
    has_create_word = any(word in lowered for word in ("create", "build", "write", "make", "add", "implement"))
    return has_adapter_word and has_create_word


def _looks_like_status_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(
        marker in lowered
        for marker in (
            "where are we",
            "what is happening",
            "what's happening",
            "what remains",
            "anything blocked",
            "task status",
            "status update",
            "current status",
        )
    )


def _explicitly_requests_copilot(objective: str) -> bool:
    lowered = objective.lower()
    return "copilot" in lowered or "github copilot" in lowered


def _looks_like_computer_use_request(objective: str) -> bool:
    lowered = objective.lower()
    if _first_url_from_text(objective):
        return False
    if _looks_like_desktop_file_listing(objective):
        return False
    if _path_from_objective(objective) and ("pdf" in lowered or _looks_like_delivery_request(objective)):
        return False
    if _looks_like_delivery_request(objective) and any(
        marker in lowered for marker in ("latest output", "current task", "artifact", "powerpoint artifact")
    ):
        return False
    markers = (
        "use computer",
        "computer use",
        "control my computer",
        "desktop",
        "screen",
        "what do you see",
        "take a screenshot",
        "open this folder",
        "open folder",
        "click",
        "type ",
        "launch app",
        "open app",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_observation_only(objective: str) -> bool:
    lowered = objective.lower()
    if any(
        marker in lowered
        for marker in (
            "what do you see",
            "what is on my desktop",
            "what's on my desktop",
            "what is on my screen",
            "what's on my screen",
            "take a screenshot",
            "screenshot",
            "observe",
            "inspect the desktop",
            "inspect my desktop",
            "look at my screen",
            "tell me what you see",
            "what windows are open",
            "what apps are open",
        )
    ):
        return True
    return bool(
        re.search(r"\b(?:what|tell me|describe|inspect)\b", lowered)
        and re.search(r"\b(?:desktop|screen|windows?|open apps?)\b", lowered)
        and not re.search(r"\b(?:click|type|fill|move|drag|open|launch|close|organize|delete|rename)\b", lowered)
    )


def _deterministic_computer_action(objective: str) -> dict[str, object] | None:
    lowered = objective.lower()
    path = _path_from_objective(objective)
    if path and any(marker in lowered for marker in ("open this folder", "open folder", "open path", "open file")):
        return {"type": "open_path", "path": path}
    if "wait" in lowered:
        seconds_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:second|seconds|sec|secs)\b", lowered)
        seconds = float(seconds_match.group(1)) if seconds_match else 1.0
        return {"type": "wait", "seconds": min(max(seconds, 0.0), 30.0)}
    return None


def _looks_like_filesystem_manage_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(
        marker in lowered
        for marker in (
            "organize",
            "sort files",
            "clean folder",
            "search folder",
            "find file",
            "find files",
            "find me the file",
            "locate file",
            "locate the file",
            "locate and deliver",
            "search files",
            "search for",
            "look for",
            "get me",
            "send me",
        )
    ) or _looks_like_desktop_file_listing(objective)


def _looks_like_file_search(objective: str) -> bool:
    lowered = objective.lower()
    return any(
        marker in lowered
        for marker in (
            "search folder",
            "find file",
            "find files",
            "find me the file",
            "locate file",
            "locate the file",
            "locate and deliver",
            "search files",
            "look for",
            "search for",
            "get me",
            "send me",
        )
    ) and _looks_like_file_reference(objective)


def _looks_like_file_reference(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("file", "files", "pdf", "document", "documents", "folder", "directory"))


def _looks_like_desktop_file_listing(objective: str) -> bool:
    lowered = objective.lower()
    return "desktop" in lowered and any(
        marker in lowered
        for marker in (
            "list all",
            "list the",
            "show all",
            "show me all",
            "what files",
            "which files",
            "files on",
            "files at",
            "files in",
            "folders on",
            "folders at",
            "folders in",
            "desktop files",
        )
    )


def _root_from_objective(objective: str) -> str | None:
    lowered = objective.lower()
    if "desktop" in lowered:
        return "desktop"
    if "download" in lowered:
        return "downloads"
    if "document" in lowered:
        return "documents"
    if any(marker in lowered for marker in ("my directory", "my folder", "home directory", "user directory")):
        return str(Path.home())
    return None


def _folder_root_from_request(objective: str) -> str | None:
    lowered = objective.lower()
    root_alias = _root_from_objective(objective)
    if not root_alias:
        return None

    folder_name: str | None = None
    patterns = [
        r"\b([A-Za-z0-9_. -]{2,80}?)\s+folder\s+(?:on|at|in)\s+(?:my\s+)?(?:desktop|documents|downloads)\b",
        r"\bfolder\s+(?:named|called)\s+([A-Za-z0-9_. -]{2,80}?)(?:\s+(?:on|at|in)\s+(?:my\s+)?(?:desktop|documents|downloads)|[.!?]|$)",
        r"\bopen\s+(?:the\s+)?([A-Za-z0-9_. -]{2,80}?)\s+folder\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, objective, flags=re.IGNORECASE)
        if match:
            folder_name = _clean_folder_name(match.group(1))
            break
    if not folder_name:
        return None
    if folder_name.lower() in {"desktop", "documents", "downloads"}:
        return folder_name.lower()
    if folder_name.lower() in {"my", "the", "a", "an", "this", "that"}:
        return None
    if root_alias == "desktop" and folder_name.lower() in {"download", "downloads"}:
        return "downloads"
    if root_alias == "documents" and folder_name.lower() in {"desktop", "downloads"}:
        return folder_name.lower()
    return f"{root_alias}\\{folder_name}"


def _clean_folder_name(value: str) -> str:
    cleaned = re.sub(
        r"\b(open|the|a|an|my|folder|directory|named|called|at|on|in|and|tell|me|all|files|inside|inside it)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;\"'")
    return cleaned


def _path_from_objective(objective: str) -> str | None:
    quoted = _quoted_text(objective)
    if quoted and _looks_like_path(quoted):
        return quoted
    match = re.search(r"[A-Za-z]:\\[^\n\r\"']+", objective)
    if match:
        candidate = match.group(0).strip().rstrip(".,")
        existing = _longest_existing_path_prefix(candidate)
        return existing or candidate
    return None


def _longest_existing_path_prefix(value: str) -> str | None:
    candidate = value.strip().rstrip(".,")
    while candidate:
        try:
            path = Path(candidate).expanduser()
            if path.exists():
                return str(path)
        except OSError:
            pass
        if " " not in candidate:
            return None
        candidate = candidate.rsplit(" ", 1)[0].rstrip(".,")
    return None


def _looks_like_path(value: str) -> bool:
    return bool(
        re.match(r"^[A-Za-z]:\\", value)
        or value.startswith((".", "~", "/"))
        or value.strip().lower() in {"desktop", "documents", "my documents", "downloads", "home", "my directory"}
    )


def _usable_explicit_path(value: str) -> bool:
    return _looks_like_path(value) and not re.search(r"<[^>]+>|\{[^}]+\}", value)


def _query_from_file_reference(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().strip("\"'")
    if not text:
        return None
    if _usable_explicit_path(text):
        return None
    name = Path(text.replace("/", "\\")).name
    return name or text


def _looks_like_find_and_read_request(*values: str) -> bool:
    combined = " ".join(value for value in values if value).lower()
    return any(marker in combined for marker in ("read it", "read me", "read the file", "what inside", "what is inside", "contents", "content of"))


def _search_query_from_objective(objective: str) -> str:
    quoted = _quoted_text(objective)
    if quoted and not _looks_like_path(quoted):
        return quoted
    named = re.search(r"\b(?:named|called)\s+([A-Za-z0-9_.-]+)", objective, flags=re.IGNORECASE)
    if named:
        return named.group(1)
    cleaned = re.sub(r"[A-Za-z]:\\[^\n\r\"']+", " ", objective)
    cleaned = re.sub(
        r"\b(search|look|looking|locate|deliver|named|called|folder|directory|desktop|documents|downloads|find|file|files|send|get|me|the|a|an|and|it|to|about|under|in|at|on|for|from|my|user|users|user's|all|list|show|please)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "*"


def _file_query_from_reference(objective: str) -> str:
    lowered = objective.lower()
    if "pdf" in lowered:
        return ".pdf"
    if "powerpoint" in lowered or "pptx" in lowered:
        return ".pptx"
    if "document" in lowered:
        return ".pdf"
    return "*"


def _first_url_from_text(value: str) -> str | None:
    import re

    match = re.search(r"https?://[^\s<>()]+|www\.[^\s<>()]+|\b[A-Za-z0-9.-]+\.(?:com|org|net|io|ai|dev|edu|gov|co)\b", value)
    return match.group(0).rstrip(".,") if match else None


def _quoted_text(value: str) -> str | None:
    import re

    match = re.search(r"['\"]([^'\"]+)['\"]", value)
    return match.group(1).strip() if match else None


def _adapter_copilot_prompt(objective: str) -> str:
    return render_prompt("tools/adapter_factory_copilot.md", objective=objective)


def _web_app_copilot_prompt(objective: str, workspace_dir: str) -> str:
    return render_prompt("tools/copilot_web_app.md", objective=objective, workspace_dir=workspace_dir)
