from __future__ import annotations

from datetime import timedelta

import pytest

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration.default_plans import build_default_task_plan
from agent_control.orchestration.fulfillment import validate_fulfillment
from agent_control.scheduler import objective_from_schedule_text, run_scheduler_once
from agent_control.schemas import Capability, PlanModel, PlanStep, RiskLevel, ScheduleRecord, ToolCallRequest, ToolResultStatus, utc_now
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.registry import build_tool_registry
from agent_control.tools.schedule_manage import ScheduleManageAdapter


def _repos(tmp_path) -> tuple[Repositories, AuditLogger]:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    return repos, AuditLogger(repos.audit)


def _settings(tmp_path, *, terminal: bool = False) -> AppSettings:
    capabilities = {
        Capability.SCHEDULE_MANAGE: CapabilityPolicy(
            enabled=True,
            requires_approval=False,
            max_risk_level=RiskLevel.MEDIUM,
        )
    }
    if terminal:
        capabilities[Capability.TERMINAL_RUN] = CapabilityPolicy(
            enabled=True,
            requires_approval=False,
            max_risk_level=RiskLevel.HIGH,
        )
    return AppSettings(
        _env_file=None,
        storage={"database_url": f"sqlite:///{tmp_path / 'agent.db'}"},
        adapters={"coding_agent": {"enabled": True, "workspace_root": str(tmp_path / "workspaces")}},
        capabilities=capabilities,
    )


def test_objective_from_schedule_text_removes_scheduling_wrapper() -> None:
    objective = objective_from_schedule_text(
        "Set up a scheduled job every day to check https://example.com and tell me if a new episode came out."
    )

    assert objective == "check https://example.com and tell me if a new episode came out"


@pytest.mark.asyncio
async def test_schedule_manage_creates_lists_pauses_resumes_and_runs_now(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    task = repos.tasks.create("set up scheduled job every day to check example.com", metadata={"source_chat_id": "100"})
    adapter = ScheduleManageAdapter(repos, audit)

    created = await adapter.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="schedule.manage",
            capability=Capability.SCHEDULE_MANAGE,
            risk_level=RiskLevel.MEDIUM,
            input={
                "operation": "create",
                "objective": task.objective,
                "source_chat_id": "100",
            },
        )
    )

    assert created.status == ToolResultStatus.SUCCEEDED
    assert created.output["schedule_id"]
    assert created.output["next_run_at"]

    listed = await adapter.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="schedule.manage",
            capability=Capability.SCHEDULE_MANAGE,
            risk_level=RiskLevel.MEDIUM,
            input={"operation": "list"},
        )
    )
    assert len(listed.output["schedules"]) == 1

    schedule_id = created.output["schedule_id"]
    for operation, status in (("pause", "paused"), ("resume", "enabled")):
        result = await adapter.execute(
            ToolCallRequest(
                task_id=task.id,
                tool_name="schedule.manage",
                capability=Capability.SCHEDULE_MANAGE,
                risk_level=RiskLevel.MEDIUM,
                input={"operation": operation, "schedule_id": schedule_id},
            )
        )
        assert result.output["schedules"][0]["status"] == status

    run_now = await adapter.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="schedule.manage",
            capability=Capability.SCHEDULE_MANAGE,
            risk_level=RiskLevel.MEDIUM,
            input={"operation": "run_now", "schedule_id": schedule_id},
        )
    )

    assert run_now.status == ToolResultStatus.SUCCEEDED
    assert run_now.output["task_id"]
    assert repos.tasks.get(run_now.output["task_id"]).metadata["source_schedule_id"] == schedule_id


@pytest.mark.asyncio
async def test_due_schedule_creates_task_and_advances_next_run(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    due = utc_now() - timedelta(minutes=1)
    schedule = repos.schedules.create(
        ScheduleRecord(
            source_chat_id="100",
            objective="check https://example.com and tell me if it changed",
            cadence="every 5 minutes",
            next_run_at=due,
        )
    )

    tasks = await run_scheduler_once(repos, audit, now=utc_now())

    updated = repos.schedules.get(schedule.id)
    assert len(tasks) == 1
    assert tasks[0].metadata["source_schedule_id"] == schedule.id
    assert updated.last_task_id == tasks[0].id
    assert updated.next_run_at > due


def test_schedule_manage_is_registered_under_schedule_capability(tmp_path) -> None:
    settings = _settings(tmp_path)
    repos, audit = _repos(tmp_path)
    registry = build_tool_registry(settings, "http://127.0.0.1:8765", repositories=repos, audit_logger=audit)
    definition = next(item for item in registry.definitions if item.name == "schedule.manage")

    assert definition.enabled is True
    assert definition.capability == Capability.SCHEDULE_MANAGE
    assert "schedule.manage" in registry.adapters


def test_registry_rejects_invalid_schedule_operation(tmp_path) -> None:
    settings = _settings(tmp_path)
    registry = build_tool_registry(settings, "http://127.0.0.1:8765")
    plan = PlanModel(
        objective="schedule something",
        required_capabilities=[Capability.SCHEDULE_MANAGE],
        steps=[
            PlanStep(
                title="Bad schedule step",
                description="Use unsupported operation",
                required_capabilities=[Capability.SCHEDULE_MANAGE],
                risk_level=RiskLevel.MEDIUM,
                tool_name="schedule.manage",
                tool_input={"operation": "bogus", "objective": "check example.com"},
            )
        ],
    )

    with pytest.raises(ValueError, match="invalid input for schedule.manage"):
        registry.validate_plan(plan)


def test_default_plan_routes_scheduled_job_to_schedule_manage(tmp_path) -> None:
    settings = _settings(tmp_path)
    repos, _ = _repos(tmp_path)
    record = repos.tasks.create(
        "set up a scheduled job every day to check https://example.com and tell me if a new episode came out",
        metadata={"source_chat_id": "100"},
    )

    plan = build_default_task_plan(settings, record)

    assert plan is not None
    assert plan.steps[-1].tool_name == "schedule.manage"
    assert plan.steps[-1].tool_input["operation"] == "create"
    assert Capability.SCHEDULE_MANAGE in plan.required_capabilities
    assert validate_fulfillment(
        record.model_copy(update={"metadata": {"last_tool_result": {"output": {"schedule_id": "schedule_1"}}}}),
        plan,
    ).ok


def test_schedule_job_can_reference_explicit_coding_workspace(tmp_path) -> None:
    settings = _settings(tmp_path, terminal=True)
    repos, _ = _repos(tmp_path)
    record = repos.tasks.create(
        "use Codex to prepare the script and set up a scheduled job every day to search the web for LLM deployment news",
        metadata={"source_chat_id": "100"},
    )

    plan = build_default_task_plan(settings, record)

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["coding.agent", "schedule.manage"]
    assert plan.steps[0].tool_input["provider"] == "codex"
    assert plan.steps[1].tool_input["metadata"]["coding_provider"] == "codex"


def test_default_plan_routes_schedule_pause_when_schedule_id_is_named(tmp_path) -> None:
    settings = _settings(tmp_path)
    repos, _ = _repos(tmp_path)
    record = repos.tasks.create("pause schedule schedule_abc123")

    plan = build_default_task_plan(settings, record)

    assert plan is not None
    assert plan.steps[0].tool_name == "schedule.manage"
    assert plan.steps[0].tool_input == {
        "operation": "pause",
        "schedule_id": "schedule_abc123",
        "timeout_seconds": 30,
    }
    assert plan.postconditions == []
