from __future__ import annotations

from agent_control.config import AppSettings
from agent_control.schemas import Capability, PlanModel, PlanStep, RiskLevel, TaskRecord, TaskType


def build_default_vscode_development_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if task.metadata.get("task_type") != TaskType.DEVELOPMENT.value:
        return None
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
                    "prompt": task.objective,
                    "terminal_id": "agent-control-copilot",
                    "capture_output": True,
                    "timeout_seconds": 180,
                },
                expected_output="A VS Code bridge terminal output record tied to the queued command.",
            )
        ],
        success_criteria=["The VS Code bridge accepted the command and reported a final terminal output record."],
    )
