from __future__ import annotations

import pytest

from agent_control.schemas import Capability, ChannelType, TaskStatus, ToolCallRequest
from agent_control.storage import Database, Repositories
from agent_control.tools.registry import build_tool_registry
from agent_control.tools.task_status import TaskStatusAdapter
from agent_control.config import AppSettings, CapabilityPolicy


@pytest.mark.asyncio
async def test_task_status_adapter_reports_recent_active_and_blocked_tasks(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    conversation_id = repos.conversations.get_or_create(ChannelType.TELEGRAM, "100")
    active = repos.tasks.create("Run active task", conversation_id=conversation_id)
    repos.tasks.update_status(active.id, TaskStatus.RUNNING)
    failed = repos.tasks.create("Failed task", conversation_id=conversation_id)
    repos.tasks.update_status(failed.id, TaskStatus.FAILED)

    result = await TaskStatusAdapter(repos).execute(
        ToolCallRequest(
            task_id="task_status",
            tool_name="task.status",
            capability=Capability.TELEGRAM_RECEIVE,
            input={"operation": "status", "limit": 10},
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["task_status"]["active_count"] == 1
    assert "Blocked or failed recently: 1" in result.output["summary"]
    assert result.output["terminal_output"]


@pytest.mark.asyncio
async def test_task_status_reports_awaiting_external_and_coding_sessions(tmp_path) -> None:
    import json

    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    task = repos.tasks.create("Use Codex", metadata={"awaiting_external": {"session_id": "codex_live"}})
    repos.tasks.update_status(task.id, TaskStatus.AWAITING_EXTERNAL)
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    (session_root / "codex_live.json").write_text(
        json.dumps(
            {
                "session_id": "codex_live",
                "provider": "codex",
                "status": "running",
                "task_id": task.id,
                "workspace_dir": str(tmp_path),
                "started_at": "2026-07-05T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    settings = AppSettings(
        _env_file=None,
        adapters={"coding_agent": {"session_root": str(session_root)}},
        mcp={"enabled": True, "servers": {}},
    )

    result = await TaskStatusAdapter(repos, settings).execute(
        ToolCallRequest(
            task_id="task_status",
            tool_name="task.status",
            capability=Capability.TELEGRAM_RECEIVE,
            input={"operation": "status", "limit": 10},
        )
    )

    assert result.output["task_status"]["awaiting_external_count"] == 1
    assert result.output["task_status"]["active_coding_sessions"][0]["session_id"] == "codex_live"
    assert result.output["task_status"]["mcp"]["enabled"] is True


def test_registry_exposes_task_status_when_repositories_are_available(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    settings = AppSettings(
        _env_file=None,
        capabilities={Capability.TELEGRAM_RECEIVE: CapabilityPolicy(enabled=True, requires_approval=False)},
    )

    registry = build_tool_registry(settings, "http://127.0.0.1:8765", repositories=repos)
    definitions = {definition.name: definition for definition in registry.definitions}

    assert definitions["task.status"].enabled is True
    assert "task.status" in registry.adapters
