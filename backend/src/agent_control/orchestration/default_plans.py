from __future__ import annotations

from agent_control.config import AppSettings
from agent_control.schemas import Capability, PlanModel, PlanStep, RiskLevel, TaskRecord, TaskType


def build_default_vscode_development_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if task.metadata.get("task_type") != TaskType.DEVELOPMENT.value:
        return None

    workspace_plan = _build_workspace_web_app_plan(settings, task)
    if workspace_plan is not None:
        return workspace_plan

    if not settings.adapters.vscode.enabled:
        return None

    policy = settings.capabilities.get(Capability.VSCODE_WRITE_FILES)
    if policy is None or not policy.enabled:
        return None

    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The task came from an authorized Telegram message classified as development work.",
            "VS Code bridge write access is enabled for this run.",
        ],
        required_capabilities=[Capability.VSCODE_WRITE_FILES],
        steps=[
            PlanStep(
                title="Send objective to VS Code Copilot terminal",
                description="Queue the task objective through the VS Code bridge and capture the terminal result when available.",
                required_capabilities=[Capability.VSCODE_WRITE_FILES],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="vscode.copilot_terminal",
                tool_input={
                    "prompt": (
                        "Answer this development request concisely. If code is requested, include the code "
                        "and any minimal run instructions. Do not modify files or ask for permissions; return "
                        f"the answer as terminal text.\n\nRequest: {task.objective}"
                    ),
                    "terminal_id": "agent-control-copilot",
                    "capture_output": True,
                    "timeout_seconds": 180,
                },
                expected_output="A VS Code bridge terminal output record tied to the queued command.",
            )
        ],
        success_criteria=["The VS Code bridge accepted the command and reported a final terminal output record."],
    )


def _build_workspace_web_app_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.workspace.enabled:
        return None
    if not _looks_like_launchable_web_app(task.objective):
        return None

    policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if policy is None or not policy.enabled:
        return None

    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The task asks for a visible local web-app result.",
            f"Generated files are limited to {settings.adapters.workspace.root_dir}.",
        ],
        required_capabilities=[Capability.FILESYSTEM_WRITE],
        steps=[
            PlanStep(
                title="Create and launch local web app preview",
                description="Create a task workspace, write a minimal web app, start a localhost static server, and return the URL.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="workspace.web_app",
                tool_input={
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
    )


def _looks_like_launchable_web_app(objective: str) -> bool:
    lowered = objective.lower()
    web_markers = ("web app", "website", "webpage", "web page", "html", "browser app")
    launch_markers = ("launch", "start", "serve", "run it", "open it", "show me", "preview", "url")
    creation_markers = ("create", "build", "make", "write")
    return any(marker in lowered for marker in web_markers) and (
        any(marker in lowered for marker in launch_markers) or any(marker in lowered for marker in creation_markers)
    )
