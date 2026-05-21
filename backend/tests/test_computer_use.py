from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_control.config import AppSettings, CapabilityPolicy, ComputerUseAdapterConfig
from agent_control.orchestration.default_plans import build_default_task_plan
from agent_control.policy import PolicyEngine
from agent_control.schemas import Capability, RiskLevel, TaskRecord, ToolCallRequest
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.computer_use import ComputerUseAdapter
from agent_control.tools.computer_use import _clean_summary_text
from agent_control.tools.registry import build_tool_registry


class FakeComputerBackend:
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []
        self.observations = 0

    def observe(self, screenshot_path: Path, *, include_ui_tree: bool, max_ui_elements: int) -> dict[str, Any]:
        self.observations += 1
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.write_bytes(b"fake image")
        return {
            "platform": "windows",
            "screenshot_path": str(screenshot_path),
            "screenshot_uri": screenshot_path.resolve().as_uri(),
            "monitors": [{"index": 0, "left": 0, "top": 0, "width": 100, "height": 100}],
            "cursor_position": {"x": 10, "y": 20},
            "active_window": {"title": "Desktop"},
            "visible_windows": [{"title": "Desktop"}],
            "ui_tree": [{"name": "Start", "control_type": "Button"}] if include_ui_tree else [],
        }

    def execute_action(self, action: dict[str, Any], config: ComputerUseAdapterConfig) -> dict[str, Any]:
        self.actions.append(action)
        return {"ok": True, "summary": f"executed {action.get('type')}"}


class QueueVisionProvider:
    def __init__(self, responses: list[dict[str, Any] | str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, list[str]]] = []

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        self.calls.append((system_prompt, user_prompt, image_paths))
        response = self.responses.pop(0)
        return response if isinstance(response, str) else json.dumps(response)


@pytest.mark.asyncio
async def test_computer_use_observe_uses_fake_backend_and_vision_summary(tmp_path) -> None:
    backend = FakeComputerBackend()
    provider = QueueVisionProvider(["Desktop shows one active window."])
    adapter = ComputerUseAdapter(
        ComputerUseAdapterConfig(enabled=True, screenshot_dir=str(tmp_path / "screens")),
        provider=provider,
        backend=backend,
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_observe",
            tool_name="computer.use",
            capability=Capability.DESKTOP_CONTROL,
            risk_level=RiskLevel.CRITICAL,
            input={"operation": "observe", "objective": "take a screenshot"},
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["completed"] is True
    assert result.output["final_summary"] == "Desktop shows one active window."
    assert Path(result.output["screenshot_path"]).exists()
    assert backend.observations == 1
    assert provider.calls[0][2] == [result.output["screenshot_path"]]


@pytest.mark.asyncio
async def test_computer_use_run_goal_executes_bounded_actions(tmp_path) -> None:
    backend = FakeComputerBackend()
    provider = QueueVisionProvider(
        [
            {"completed": False, "summary": "Need to wait.", "action": {"type": "wait", "seconds": 0}},
            {"completed": True, "summary": "Goal is visible."},
        ]
    )
    adapter = ComputerUseAdapter(
        ComputerUseAdapterConfig(enabled=True, screenshot_dir=str(tmp_path / "screens"), step_delay_seconds=0),
        provider=provider,
        backend=backend,
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_run",
            tool_name="computer.use",
            capability=Capability.DESKTOP_CONTROL,
            risk_level=RiskLevel.CRITICAL,
            input={"operation": "run_goal", "objective": "wait until the desktop is visible", "max_steps": 3},
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["completed"] is True
    assert result.output["final_summary"] == "Goal is visible."
    assert [action["type"] for action in result.output["actions_taken"]] == ["wait"]
    assert len(result.output["screenshots"]) == 2


@pytest.mark.asyncio
async def test_computer_use_run_goal_stops_before_next_action_when_cancelled(tmp_path) -> None:
    backend = FakeComputerBackend()
    provider = QueueVisionProvider(
        [{"completed": False, "summary": "Click next.", "action": {"type": "click", "x": 5, "y": 5}}]
    )
    allowed = True

    def should_continue(task_id: str) -> bool:
        nonlocal allowed
        if allowed:
            allowed = False
            return True
        return False

    adapter = ComputerUseAdapter(
        ComputerUseAdapterConfig(enabled=True, screenshot_dir=str(tmp_path / "screens"), step_delay_seconds=0),
        provider=provider,
        backend=backend,
        should_continue=should_continue,
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_cancel",
            tool_name="computer.use",
            capability=Capability.DESKTOP_CONTROL,
            risk_level=RiskLevel.CRITICAL,
            input={"operation": "run_goal", "objective": "click next", "max_steps": 3},
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["completed"] is False
    assert result.output["final_summary"] == "Stopped before the next computer-use action."
    assert backend.actions == []


def test_registry_exposes_computer_use_and_filesystem_manage_when_enabled(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        adapters={
            "computer_use": {"enabled": True, "allowed_roots": [str(tmp_path)]},
            "desktop": {"control_enabled": True},
        },
        capabilities={
            Capability.DESKTOP_CONTROL: CapabilityPolicy(
                enabled=True,
                requires_approval=True,
                max_risk_level=RiskLevel.CRITICAL,
            ),
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=True,
                max_risk_level=RiskLevel.HIGH,
            ),
        },
    )

    registry = build_tool_registry(settings, "http://127.0.0.1:8765", provider=QueueVisionProvider([]))
    definitions = {definition.name: definition for definition in registry.definitions}

    assert definitions["computer.use"].enabled is True
    assert definitions["computer.use"].operations == ("observe", "act", "run_goal")
    assert definitions["filesystem.manage"].enabled is True
    assert "computer.use" in registry.adapters
    assert "filesystem.manage" in registry.adapters


def test_policy_blocks_disabled_desktop_control_and_requires_approval(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    task = repos.tasks.create("use the computer")
    request = ToolCallRequest(
        task_id=task.id,
        tool_name="computer.use",
        capability=Capability.DESKTOP_CONTROL,
        risk_level=RiskLevel.CRITICAL,
        input={"operation": "run_goal", "objective": "open notepad"},
    )

    denied = PolicyEngine(AppSettings(_env_file=None), audit).evaluate(request)
    assert denied.allowed is False
    assert denied.reason == "capability_disabled"

    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.DESKTOP_CONTROL: CapabilityPolicy(
                enabled=True,
                requires_approval=True,
                max_risk_level=RiskLevel.CRITICAL,
            )
        },
    )
    needs_approval = PolicyEngine(settings, audit).evaluate(request)
    approved = PolicyEngine(settings, audit).evaluate(request, approved=True)

    assert needs_approval.needs_approval is True
    assert approved.allowed is True


def test_default_plans_route_desktop_and_folder_requests(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        adapters={
            "computer_use": {"enabled": True, "allowed_roots": [str(tmp_path)]},
            "desktop": {"control_enabled": True},
        },
        capabilities={
            Capability.DESKTOP_CONTROL: CapabilityPolicy(
                enabled=True,
                requires_approval=True,
                max_risk_level=RiskLevel.CRITICAL,
            ),
            Capability.FILESYSTEM_WRITE: CapabilityPolicy(
                enabled=True,
                requires_approval=True,
                max_risk_level=RiskLevel.HIGH,
            ),
        },
    )

    screenshot_plan = build_default_task_plan(settings, TaskRecord(objective="Take a screenshot and tell me what you see"))
    wait_plan = build_default_task_plan(settings, TaskRecord(objective="Use computer to wait 1 second"))
    organize_plan = build_default_task_plan(settings, TaskRecord(objective=f'Organize folder "{tmp_path}" by type'))

    assert screenshot_plan is not None
    assert screenshot_plan.steps[0].tool_name == "computer.use"
    assert screenshot_plan.steps[0].tool_input["operation"] == "observe"
    assert wait_plan is not None
    assert wait_plan.steps[0].tool_name == "computer.use"
    assert wait_plan.steps[0].tool_input["operation"] == "act"
    assert wait_plan.steps[0].tool_input["action"] == {"type": "wait", "seconds": 1.0}
    assert organize_plan is not None
    assert [step.tool_name for step in organize_plan.steps] == ["filesystem.manage", "filesystem.manage"]
    assert organize_plan.steps[0].tool_input["operation"] == "organize_plan"
    assert organize_plan.steps[1].tool_input["manifest"] == "{{last_manifest}}"


def test_default_computer_use_plan_honors_full_access_policy_over_adapter_session_flag(tmp_path) -> None:
    settings = AppSettings(
        _env_file=None,
        adapters={
            "computer_use": {
                "enabled": True,
                "allowed_roots": [str(tmp_path)],
                "require_session_approval": True,
            },
            "desktop": {"control_enabled": True},
        },
        capabilities={
            Capability.DESKTOP_CONTROL: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.CRITICAL,
            ),
        },
    )

    plan = build_default_task_plan(settings, TaskRecord(objective="Take a screenshot of my desktop"))

    assert plan is not None
    assert plan.steps[0].tool_name == "computer.use"
    assert plan.steps[0].requires_approval is False


def test_computer_use_summary_cleanup_handles_fenced_json() -> None:
    assert (
        _clean_summary_text('```json\n{"status":"complete","description":"Desktop is visible."}\n```')
        == "Desktop is visible."
    )
