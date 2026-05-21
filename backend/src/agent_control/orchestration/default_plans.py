from __future__ import annotations

import re

from agent_control.config import AppSettings
from agent_control.orchestration.fulfillment import expected_fulfillment
from agent_control.prompts import render_prompt
from agent_control.scheduler import cadence_from_text, objective_from_schedule_text
from agent_control.tools.local_workspace import workspace_dir_for_task
from agent_control.schemas import (
    Capability,
    PlanModel,
    PlanPostcondition,
    PlanStep,
    PostconditionType,
    RiskLevel,
    TaskRecord,
    TaskType,
)


def build_default_task_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    filesystem_plan = _build_filesystem_manage_plan(settings, task)
    if filesystem_plan is not None:
        return filesystem_plan
    computer_plan = _build_computer_use_plan(settings, task)
    if computer_plan is not None:
        return computer_plan
    document_plan = _build_document_plan(settings, task)
    if document_plan is not None:
        return document_plan
    schedule_plan = _build_schedule_plan(settings, task)
    if schedule_plan is not None:
        return schedule_plan
    coding_agent_plan = _build_coding_agent_plan(settings, task)
    if coding_agent_plan is not None:
        return coding_agent_plan
    browser_plan = _build_browser_plan(settings, task)
    if browser_plan is not None:
        return browser_plan
    artifact_plan = _build_artifact_delivery_plan(settings, task)
    if artifact_plan is not None:
        return artifact_plan
    return build_default_vscode_development_plan(settings, task)


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


def _build_browser_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.browser.enabled:
        return None
    if not _looks_like_browser_request(task.objective):
        return None

    objective = task.objective.strip()
    open_policy = settings.capabilities.get(Capability.BROWSER_OPEN)
    control_policy = settings.capabilities.get(Capability.BROWSER_CONTROL)
    open_enabled = bool(open_policy and open_policy.enabled)
    control_enabled = bool(control_policy and control_policy.enabled)

    if _looks_like_browser_control_request(objective) and control_enabled and control_policy is not None:
        if _looks_like_form_fill_request(objective):
            url = _first_url_from_text(objective)
            steps: list[PlanStep] = []
            required_capabilities = [Capability.BROWSER_CONTROL]
            if url and open_enabled and open_policy is not None:
                required_capabilities.insert(0, Capability.BROWSER_OPEN)
                steps.append(
                    PlanStep(
                        title="Open form page in Chrome",
                        description="Open the requested form page before extracting fields.",
                        required_capabilities=[Capability.BROWSER_OPEN],
                        risk_level=RiskLevel.LOW,
                        requires_approval=open_policy.requires_approval,
                        tool_name="browser.open",
                        tool_input={"operation": "open", "url": url, "new_tab": True, "timeout_seconds": 60},
                        expected_output="Requested page is open in Chrome.",
                    )
                )
            steps.extend(
                [
                    PlanStep(
                        title="Extract form fields",
                        description="Inspect the page form state before filling fields.",
                        required_capabilities=[Capability.BROWSER_CONTROL],
                        risk_level=RiskLevel.CRITICAL,
                        requires_approval=control_policy.requires_approval,
                        tool_name="browser.control",
                        tool_input={"operation": "extract_page_state", "timeout_seconds": 60},
                        expected_output="Detected form fields.",
                    ),
                    PlanStep(
                        title="Fill form fields",
                        description="Fill detected form fields using provided user information without submitting unless requested.",
                        required_capabilities=[Capability.BROWSER_CONTROL],
                        risk_level=RiskLevel.CRITICAL,
                        requires_approval=control_policy.requires_approval,
                        tool_name="browser.control",
                        tool_input={
                            "operation": "fill_form_step",
                            "fields": _form_fields_from_objective(objective),
                            "submit": _objective_wants_submit(objective),
                            "timeout_seconds": 60,
                        },
                        expected_output="Filled form field state.",
                    ),
                ]
            )
            return PlanModel(
                objective=task.objective,
                assumptions=["The request asks to fill a browser form through Chrome DevTools."],
                required_capabilities=required_capabilities,
                steps=steps,
                success_criteria=["The form state is inspected and requested fields are filled."],
                postconditions=_browser_postconditions(),
            )
        operation, tool_input = _browser_control_input(objective)
        tool_input = {"operation": operation, **tool_input}
        url = _first_url_from_text(objective)
        steps: list[PlanStep] = []
        required_capabilities = [Capability.BROWSER_CONTROL]
        if url and open_enabled and open_policy is not None:
            required_capabilities.insert(0, Capability.BROWSER_OPEN)
            steps.append(
                PlanStep(
                    title="Open requested page in Chrome",
                    description="Open the requested URL before applying the browser control action.",
                    required_capabilities=[Capability.BROWSER_OPEN],
                    risk_level=RiskLevel.LOW,
                    requires_approval=open_policy.requires_approval,
                    tool_name="browser.open",
                    tool_input={
                        "operation": "open",
                        "url": url,
                        "new_tab": True,
                        "timeout_seconds": 60,
                    },
                    expected_output="Requested page is open in a DevTools-controlled Chrome tab.",
                )
            )
            if "tab_id" not in tool_input and "url_contains" not in tool_input and "title_contains" not in tool_input:
                tool_input["url_contains"] = url
        steps.append(
            PlanStep(
                title="Control Chrome through browser adapter",
                description="Use the Chrome DevTools browser adapter for the requested browser control action.",
                required_capabilities=[Capability.BROWSER_CONTROL],
                risk_level=RiskLevel.CRITICAL,
                requires_approval=control_policy.requires_approval,
                tool_name="browser.control",
                tool_input={
                    **tool_input,
                    "objective": objective,
                    "timeout_seconds": 60,
                },
                expected_output="Updated browser state and a concise result summary.",
            )
        )
        return PlanModel(
            objective=task.objective,
            assumptions=[
                "The request asks for an explicit browser control action.",
                "Only Chrome tabs exposed through the configured DevTools remote debugging port can be controlled.",
            ],
            required_capabilities=required_capabilities,
            steps=steps,
            success_criteria=["The requested Chrome control action is performed or a clear adapter error is reported."],
            postconditions=_browser_postconditions(),
        )

    if not open_enabled or open_policy is None:
        return None

    operation, tool_input = _browser_open_input(objective)
    steps = [
        PlanStep(
            title="Use Chrome browser adapter",
            description="Open, search, inspect, or screenshot Chrome through the registered browser adapter.",
            required_capabilities=[Capability.BROWSER_OPEN],
            risk_level=RiskLevel.LOW,
            requires_approval=open_policy.requires_approval,
            tool_name="browser.open",
            tool_input={
                "operation": operation,
                **tool_input,
                "timeout_seconds": 90,
            },
            expected_output="Browser URL, page title, visible-page summary, tab state, or screenshot path.",
        )
    ]
    delivery_step = _artifact_delivery_step(settings, task.objective, screenshot=operation == "screenshot")
    if delivery_step is not None:
        steps.append(delivery_step)
    postconditions = _browser_postconditions()
    if delivery_step is not None:
        postconditions.append(
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="A browser screenshot or task artifact is delivered back to Telegram.",
            )
        )
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The request asks to use the browser or inspect browser-visible information.",
            "Chrome will be launched with remote debugging if it is not already available.",
            "Normal Chrome windows not launched with remote debugging are not visible to this adapter.",
        ],
        required_capabilities=[
            Capability.BROWSER_OPEN,
            *([Capability.TELEGRAM_SEND] if delivery_step is not None else []),
        ],
        steps=steps,
        success_criteria=["The browser adapter reports the requested browser state or page summary."],
        postconditions=postconditions,
    )


def _build_document_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not _looks_like_document_request(task.objective):
        return None
    policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if policy is None or not policy.enabled:
        return None
    lowered = task.objective.lower()
    path = _path_from_objective(task.objective)
    steps: list[PlanStep] = []
    required = [Capability.FILESYSTEM_WRITE]
    provider = _explicit_coding_provider(task.objective)
    terminal_policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if provider and terminal_policy and terminal_policy.enabled and settings.adapters.coding_agent.enabled:
        required.append(Capability.TERMINAL_RUN)
        steps.append(
            PlanStep(
                title=f"Ask {provider} for document guidance",
                description="Use the explicitly requested coding agent for planning/content guidance before creating the document artifact.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.HIGH,
                requires_approval=terminal_policy.requires_approval,
                tool_name="coding.agent",
                tool_input={
                    "operation": "run_goal",
                    "provider": provider,
                    "objective": task.objective,
                    "prompt": task.objective,
                    "workspace_dir": str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)),
                    "timeout_seconds": settings.adapters.coding_agent.timeout_seconds,
                },
                expected_output="Coding-agent guidance for the requested document.",
            )
        )
    if "pdf" in lowered and ("summarize" in lowered or "about" in lowered or "tell me" in lowered):
        if not path:
            return None
        steps.append(
            PlanStep(
                title="Summarize PDF",
                description="Extract text from the requested PDF and return a concise summary.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="document.manage",
                tool_input={"operation": "summarize_pdf", "path": path, "timeout_seconds": 90},
                expected_output="A PDF summary and summary artifact.",
            )
        )
        postconditions = [
            PlanPostcondition(
                type=PostconditionType.DOCUMENT_SUMMARY,
                description="A PDF summary is reported.",
            )
        ]
    elif "powerpoint" in lowered or "presentation" in lowered or "pptx" in lowered:
        operation = "update_presentation" if any(word in lowered for word in ("update", "revise", "change", "edit")) and path else "create_presentation"
        tool_input: dict[str, object] = {
            "operation": operation,
            "title": _presentation_title(task.objective),
            "content": task.objective,
            "instructions": task.objective,
            "timeout_seconds": 90,
        }
        if path:
            tool_input["path"] = path
        steps.append(
            PlanStep(
                title="Create presentation artifact" if operation == "create_presentation" else "Create revised presentation artifact",
                description="Create a PowerPoint file as a task artifact.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="document.manage",
                tool_input=tool_input,
                expected_output="A PowerPoint file path and artifact ID.",
            )
        )
        postconditions = [
            PlanPostcondition(
                type=PostconditionType.PRESENTATION_FILE,
                description="A PowerPoint file artifact is reported.",
            )
        ]
    else:
        return None

    delivery_step = _artifact_delivery_step(settings, task.objective, screenshot=False)
    if delivery_step is not None:
        steps.append(delivery_step)
        required.append(Capability.TELEGRAM_SEND)
        postconditions.append(
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="The document artifact is delivered to Telegram.",
            )
        )
    return PlanModel(
        objective=task.objective,
        assumptions=["Document work is handled through file APIs before desktop UI automation."],
        required_capabilities=required,
        steps=steps,
        success_criteria=["The requested document output is created or summarized."],
        postconditions=postconditions,
    )


def _build_coding_agent_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    provider = _explicit_coding_provider(task.objective)
    if provider is None:
        return None
    policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if policy is None or not policy.enabled or not settings.adapters.coding_agent.enabled:
        return None
    steps: list[PlanStep] = []
    required = [Capability.TERMINAL_RUN]
    browser_policy = settings.capabilities.get(Capability.BROWSER_OPEN)
    if _looks_like_web_research_request(task.objective) and browser_policy and browser_policy.enabled:
        required.append(Capability.BROWSER_OPEN)
        steps.append(
            PlanStep(
                title="Collect web research context",
                description="Use the browser adapter to collect source summaries before handing context to the coding agent.",
                required_capabilities=[Capability.BROWSER_OPEN],
                risk_level=RiskLevel.LOW,
                requires_approval=browser_policy.requires_approval,
                tool_name="browser.open",
                tool_input={
                    "operation": "research_pages",
                    "query": _query_for_browser(task.objective),
                    "objective": task.objective,
                    "page_limit": _page_limit_from_objective(task.objective),
                    "timeout_seconds": 180,
                },
                expected_output="Visited URLs and page summaries for coding-agent context.",
            )
        )
    operation = "plan" if _looks_like_large_coding_request(task.objective) else "run_goal"
    steps.append(
        PlanStep(
            title=f"Run {provider} coding agent",
            description="Run the explicitly requested coding agent in a task workspace.",
            required_capabilities=[Capability.TERMINAL_RUN],
            risk_level=RiskLevel.HIGH,
            requires_approval=policy.requires_approval,
            tool_name="coding.agent",
            tool_input={
                "operation": operation,
                "provider": provider,
                "objective": task.objective,
                "prompt": task.objective,
                "workspace_dir": str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)),
                "timeout_seconds": settings.adapters.coding_agent.timeout_seconds,
            },
            expected_output="Coding-agent stdout, stderr, workspace, status, and limit state.",
        )
    )
    if operation == "plan":
        steps.append(
            PlanStep(
                title=f"Run first {provider} implementation step",
                description="Begin implementation after the planning response is captured.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="coding.agent",
                tool_input={
                    "operation": "run_step",
                    "provider": provider,
                    "objective": task.objective,
                    "prompt": "Use the previous plan output and implement the first safe step.\n\n{{last_output}}",
                    "workspace_dir": str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)),
                    "step_index": 0,
                    "timeout_seconds": settings.adapters.coding_agent.timeout_seconds,
                },
                expected_output="The first implementation step result.",
            )
        )
    return PlanModel(
        objective=task.objective,
        assumptions=[
            f"The user explicitly requested {provider}.",
            "Coding-agent work is run in a task workspace and captures stdout, stderr, status, and limits.",
        ],
        required_capabilities=required,
        steps=steps,
        success_criteria=["The explicit coding agent runs or reports availability/limit failure clearly."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.CODING_AGENT_STEP,
                description="The coding agent completed a step or reported a limit state.",
            )
        ],
    )


def _build_schedule_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not _looks_like_schedule_request(task.objective):
        return None
    policy = settings.capabilities.get(Capability.SCHEDULE_MANAGE)
    if policy is None or not policy.enabled or not settings.scheduler.enabled:
        return None

    steps: list[PlanStep] = []
    required = [Capability.SCHEDULE_MANAGE]
    operation = _schedule_operation(task.objective)
    schedule_id = _schedule_id_from_text(task.objective)
    if operation != "create":
        if operation != "list" and schedule_id is None:
            return None
        steps.append(
            PlanStep(
                title=f"{operation.replace('_', ' ').title()} schedule",
                description="Manage an existing recurring schedule.",
                required_capabilities=[Capability.SCHEDULE_MANAGE],
                risk_level=RiskLevel.MEDIUM,
                requires_approval=policy.requires_approval,
                tool_name="schedule.manage",
                tool_input={
                    "operation": operation,
                    **({"schedule_id": schedule_id} if schedule_id else {}),
                    "timeout_seconds": 30,
                },
                expected_output="Updated schedule state.",
            )
        )
        return PlanModel(
            objective=task.objective,
            assumptions=["The request asks to manage an existing schedule."],
            required_capabilities=required,
            steps=steps,
            success_criteria=["The schedule management operation returns the updated state."],
            postconditions=[],
        )

    assumptions = [
        "The request asks for recurring work.",
        "The scheduler creates normal tasks from the saved objective when the job is due.",
    ]
    provider = _explicit_coding_provider(task.objective)
    terminal_policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if provider and terminal_policy and terminal_policy.enabled and settings.adapters.coding_agent.enabled:
        required.insert(0, Capability.TERMINAL_RUN)
        assumptions.append(f"The user explicitly asked {provider} to prepare work for the schedule.")
        steps.append(
            PlanStep(
                title=f"Prepare scheduled-job workspace with {provider}",
                description="Use the explicitly requested coding agent to prepare any code or notes needed by the scheduled job.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.HIGH,
                requires_approval=terminal_policy.requires_approval,
                tool_name="coding.agent",
                tool_input={
                    "operation": "run_step",
                    "provider": provider,
                    "objective": task.objective,
                    "prompt": task.objective,
                    "workspace_dir": str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)),
                    "timeout_seconds": settings.adapters.coding_agent.timeout_seconds,
                },
                expected_output="Workspace, stdout/stderr, and any usage-limit state from the requested coding provider.",
            )
        )

    cadence = cadence_from_text(task.objective)
    scheduled_objective = objective_from_schedule_text(task.objective)
    metadata = {
        "created_from_task_id": task.id,
        "created_from_objective": task.objective,
    }
    if provider:
        metadata["coding_provider"] = provider
    steps.append(
        PlanStep(
            title="Create recurring schedule",
            description="Save the recurring objective so the scheduler can create due tasks later.",
            required_capabilities=[Capability.SCHEDULE_MANAGE],
            risk_level=RiskLevel.MEDIUM,
            requires_approval=policy.requires_approval,
            tool_name="schedule.manage",
            tool_input={
                "operation": "create",
                "objective": scheduled_objective,
                "cadence": cadence,
                "timezone": settings.scheduler.default_timezone,
                "source_chat_id": task.metadata.get("source_chat_id"),
                "metadata": metadata,
                "timeout_seconds": 30,
            },
            expected_output="A schedule ID and next run timestamp.",
        )
    )
    return PlanModel(
        objective=task.objective,
        assumptions=assumptions,
        required_capabilities=required,
        steps=steps,
        success_criteria=["The schedule is saved and reports its next due run."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.SCHEDULE_CREATED,
                description="A schedule ID and next run timestamp are reported.",
            )
        ],
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


def _build_filesystem_manage_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.computer_use.enabled:
        return None
    if not _looks_like_filesystem_manage_request(task.objective):
        return None
    root = _path_from_objective(task.objective)
    if root is None:
        return None
    write_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if write_policy is None or not write_policy.enabled:
        return None

    if _looks_like_file_search(task.objective):
        query = _search_query_from_objective(task.objective)
        return PlanModel(
            objective=task.objective,
            assumptions=["The request is safer through scoped filesystem APIs than desktop UI control."],
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            steps=[
                PlanStep(
                    title="Search scoped folder",
                    description="Search file names and optional text content inside the requested allowed folder.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "search",
                        "root": root,
                        "query": query,
                        "include_content": False,
                        "timeout_seconds": 60,
                    },
                    expected_output="Matching file paths under the configured allowed root.",
                )
            ],
            success_criteria=["Search results are reported."],
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
    operation = "send_screenshot" if "screenshot" in lowered or "screen shot" in lowered else "send_latest"
    tool_input: dict[str, object] = {
        "operation": operation,
        "caption": f"Result for: {task.objective[:180]}",
        "timeout_seconds": 60,
    }
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


def _browser_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.BROWSER_STATE,
            description="A browser URL, tab state, page title, or screenshot path is reported.",
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
    return PlanStep(
        title="Deliver task artifact to Telegram",
        description="Send the screenshot or latest task artifact back to the source Telegram chat.",
        required_capabilities=[Capability.TELEGRAM_SEND],
        risk_level=RiskLevel.LOW,
        requires_approval=policy.requires_approval,
        tool_name="artifact.deliver",
        tool_input={
            "operation": "send_screenshot" if screenshot or "screenshot" in objective.lower() else "send_latest",
            "caption": f"Result for: {objective[:180]}",
            "timeout_seconds": 60,
        },
        expected_output="Telegram delivery result for the requested screenshot or file artifact.",
    )


def _looks_like_delivery_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(phrase in lowered for phrase in ("send it", "send me", "send the", "send a", "send screenshot", "send file"))


def _looks_like_adapter_request(objective: str) -> bool:
    lowered = objective.lower()
    has_adapter_word = any(word in lowered for word in ("adapter", "tool", "capability", "connector"))
    has_create_word = any(word in lowered for word in ("create", "build", "write", "make", "add", "implement"))
    return has_adapter_word and has_create_word


def _looks_like_schedule_request(objective: str) -> bool:
    lowered = objective.lower()
    explicit_schedule = any(marker in lowered for marker in ("schedule", "scheduled job", "recurring", "every day", "daily", "weekly"))
    has_recurring_phrase = bool(re.search(r"\bevery\s+\d+\s+(?:minute|minutes|hour|hours|day|days|week|weeks)\b", lowered))
    has_action = any(marker in lowered for marker in ("set up", "create", "add", "run", "check", "search", "do ", "pause", "resume", "delete", "list"))
    return (explicit_schedule or has_recurring_phrase) and has_action


def _schedule_operation(objective: str) -> str:
    lowered = objective.lower()
    if "pause" in lowered:
        return "pause"
    if "resume" in lowered or "enable" in lowered:
        return "resume"
    if "delete" in lowered or "remove" in lowered:
        return "delete"
    if "run now" in lowered or "run this schedule" in lowered:
        return "run_now"
    if "list" in lowered or "show schedules" in lowered:
        return "list"
    return "create"


def _schedule_id_from_text(objective: str) -> str | None:
    match = re.search(r"\bschedule_[a-f0-9]+\b", objective)
    return match.group(0) if match else None


def _explicitly_requests_copilot(objective: str) -> bool:
    lowered = objective.lower()
    return "copilot" in lowered or "github copilot" in lowered


def _looks_like_computer_use_request(objective: str) -> bool:
    lowered = objective.lower()
    if _first_url_from_text(objective):
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
    return any(marker in lowered for marker in ("what do you see", "take a screenshot", "screenshot", "observe", "look at my screen"))


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
    return any(marker in lowered for marker in ("organize", "sort files", "clean folder", "search folder", "find file", "search files"))


def _looks_like_file_search(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("search folder", "find file", "search files", "find files"))


def _path_from_objective(objective: str) -> str | None:
    quoted = _quoted_text(objective)
    if quoted and _looks_like_path(quoted):
        return quoted
    match = re.search(r"[A-Za-z]:\\[^\n\r\"']+", objective)
    if match:
        return match.group(0).strip().rstrip(".,")
    return None


def _looks_like_path(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:\\", value) or value.startswith((".", "~", "/")))


def _search_query_from_objective(objective: str) -> str:
    cleaned = re.sub(r"[A-Za-z]:\\[^\n\r\"']+", " ", objective)
    cleaned = re.sub(r"\b(search|folder|find|file|files|under|in|for)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "*"


def _looks_like_browser_request(objective: str) -> bool:
    if expected_fulfillment(objective).get("preview_url"):
        return False
    lowered = objective.lower()
    markers = (
        "browser",
        "chrome",
        "tab",
        "website",
        "web page",
        "webpage",
        "url",
        "http://",
        "https://",
        "www.",
        "search",
        "google",
        "bing",
        "open this page",
        "go to",
        "screenshot",
        "screen shot",
        "fill the form",
        "click",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_browser_control_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(
        marker in lowered
        for marker in (
            "close tab",
            "close the tab",
            "click",
            "fill the form",
            "start filling",
            "submit the form",
            "new episode",
            "new show",
            "came out",
            "check whether",
        )
    )


def _looks_like_form_fill_request(objective: str) -> bool:
    lowered = objective.lower()
    return "form" in lowered and any(marker in lowered for marker in ("fill", "filling", "start"))


def _looks_like_page_update_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("new episode", "new show", "came out", "check whether"))


def _objective_wants_submit(objective: str) -> bool:
    lowered = objective.lower()
    return "submit" in lowered or "send the form" in lowered


def _form_fields_from_objective(objective: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in re.findall(r"\b([A-Za-z][A-Za-z0-9 _-]{1,30})\s*=\s*([^,;\n]+)", objective):
        normalized_key = key.strip().split()[-1]
        fields[normalized_key] = value.strip()
    return fields


def _browser_open_input(objective: str) -> tuple[str, dict[str, object]]:
    lowered = objective.lower()
    if any(phrase in lowered for phrase in ("open tabs", "current tabs", "what tabs", "what websites")):
        return "inspect_tabs", {"include_text": "summary" in lowered or "about" in lowered}
    if "screenshot" in lowered or "screen shot" in lowered:
        url = _first_url_from_text(objective)
        if url:
            return "screenshot", {"url": url, "full_page": True}
        return "screenshot", {"full_page": True}
    if "search" in lowered or "google" in lowered or "bing" in lowered:
        if "50" in lowered or "many page" in lowered or "many pages" in lowered:
            return "research_pages", {
                "objective": objective,
                "query": _query_for_browser(objective),
                "page_limit": _page_limit_from_objective(objective),
            }
        return "research", {
            "objective": objective,
            "query": _query_for_browser(objective),
            "open_first_result": _objective_wants_first_result(objective),
        }
    url = _first_url_from_text(objective)
    if url:
        return "research", {"objective": objective, "url": url}
    return "research", {"objective": objective}


def _browser_control_input(objective: str) -> tuple[str, dict[str, object]]:
    lowered = objective.lower()
    if _looks_like_page_update_request(objective):
        return "check_page_update", {"url": _first_url_from_text(objective), "objective": objective}
    if "close" in lowered:
        quoted = _quoted_text(objective)
        return "close_tab", {"title_contains": quoted} if quoted else {}
    if "click" in lowered:
        quoted = _quoted_text(objective)
        return "click", {"text": quoted or _text_after_word(objective, "click")}
    return "fill_form_step", {"fields": _form_fields_from_objective(objective), "submit": _objective_wants_submit(objective)}


def _first_url_from_text(value: str) -> str | None:
    import re

    match = re.search(r"https?://[^\s<>()]+|www\.[^\s<>()]+", value)
    return match.group(0).rstrip(".,") if match else None


def _quoted_text(value: str) -> str | None:
    import re

    match = re.search(r"['\"]([^'\"]+)['\"]", value)
    return match.group(1).strip() if match else None


def _text_after_word(value: str, word: str) -> str:
    lowered = value.lower()
    index = lowered.find(word)
    if index < 0:
        return value.strip()
    return value[index + len(word) :].strip(" .:")


def _query_for_browser(objective: str) -> str:
    import re

    cleaned = re.sub(r"\b(search|google|bing|look up|find|open|browser|chrome|summarize|summary)\b", " ", objective, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or objective


def _objective_wants_first_result(objective: str) -> bool:
    lowered = objective.lower()
    return any(phrase in lowered for phrase in ("first result", "first website", "first site", "go to the first", "open the first"))


def _page_limit_from_objective(objective: str) -> int:
    match = re.search(r"\b(\d{1,2})\s+pages?\b", objective.lower())
    if not match:
        return 50 if "50" in objective else 10
    return max(1, min(50, int(match.group(1))))


def _looks_like_document_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("pdf", "powerpoint", "presentation", "pptx"))


def _presentation_title(objective: str) -> str:
    quoted = _quoted_text(objective)
    if quoted:
        return quoted
    cleaned = re.sub(r"\b(use codex|create|make|build|powerpoint|presentation|pptx|send me|send it)\b", " ", objective, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:")
    return cleaned[:80] or "Presentation"


def _explicit_coding_provider(objective: str) -> str | None:
    lowered = objective.lower()
    if "codex" in lowered:
        return "codex"
    if "github copilot" in lowered or "copilot" in lowered:
        return "github_copilot"
    return None


def _looks_like_web_research_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("web search", "search for", "search this", "search the web", "research"))


def _looks_like_large_coding_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("large", "step by step", "plan first", "start creating", "mobile deployment"))


def _adapter_copilot_prompt(objective: str) -> str:
    return render_prompt("tools/adapter_factory_copilot.md", objective=objective)


def _web_app_copilot_prompt(objective: str, workspace_dir: str) -> str:
    return render_prompt("tools/copilot_web_app.md", objective=objective, workspace_dir=workspace_dir)
