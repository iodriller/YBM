from __future__ import annotations

import pytest

from agent_control.orchestration.worker import TaskWorker
from agent_control.schemas import ChannelType, TaskStatus
from agent_control.storage import AuditLogger, Database, Repositories


@pytest.mark.asyncio
async def test_worker_records_compact_task_output_in_conversation_memory(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    audit = AuditLogger(repos.audit)
    conversation_id = repos.conversations.get_or_create(ChannelType.TELEGRAM, "100")
    task = repos.tasks.create(
        "Inspect the folder",
        conversation_id=conversation_id,
        metadata={
            "last_tool_name": "filesystem.manage",
            "last_tool_output_text": "Found notes.txt, budget.csv, and sample.pdf.",
            "file_manifest": [{"name": "notes.txt"}, {"name": "budget.csv"}, {"name": "sample.pdf"}],
        },
    )
    completed = repos.tasks.update_status(task.id, TaskStatus.COMPLETED)
    worker = TaskWorker(repos, audit)

    await worker._notify_if_needed(completed)

    memory = repos.conversation_memory.get(conversation_id)

    assert memory is not None
    assert "notes.txt" in memory["summary"]
    assert memory["facts"]["recent_turns"][-1]["role"] == "task"
