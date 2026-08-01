from __future__ import annotations

import pytest

from agent_control.schemas import Capability, MemoryFact, MemorySource, ToolCallRequest, ToolResultStatus
from agent_control.tools.memory_manage import MemoryManageAdapter
from helpers import make_repos


@pytest.mark.asyncio
async def test_memory_manage_remember_creates_a_task_derived_fact(tmp_path) -> None:
    """The tool cannot claim any other source (docs/UI_UX_AUDIT.md Phase 4's
    provenance requirement) - there is no source field on the input at all,
    every fact this adapter creates is stamped TASK_DERIVED."""
    repos, _audit = make_repos(tmp_path)
    task = repos.tasks.create("remember my preference")
    adapter = MemoryManageAdapter(repos)

    result = await adapter.execute(
        ToolCallRequest(
            task_id=task.id, tool_name="memory.manage", capability=Capability.MEMORY_MANAGE,
            input={"operation": "remember", "category": "preference", "content": "Prefers metric units"},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    fact_id = result.output["fact_id"]
    stored = repos.memory_facts.get(fact_id)
    assert stored is not None
    assert stored.source == MemorySource.TASK_DERIVED
    assert stored.task_id == task.id
    assert stored.category == "preference"
    assert stored.content == "Prefers metric units"


@pytest.mark.asyncio
async def test_memory_manage_list_returns_matching_facts(tmp_path) -> None:
    repos, _audit = make_repos(tmp_path)
    repos.memory_facts.create(MemoryFact(category="preference", content="Likes dark mode"))
    repos.memory_facts.create(MemoryFact(category="project", content="Working on a CLI tool"))
    adapter = MemoryManageAdapter(repos)

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_1", tool_name="memory.manage", capability=Capability.MEMORY_MANAGE,
            input={"operation": "list", "query": "dark"},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert [f["content"] for f in result.output["facts"]] == ["Likes dark mode"]


@pytest.mark.asyncio
async def test_memory_manage_forget_deletes_the_fact(tmp_path) -> None:
    repos, _audit = make_repos(tmp_path)
    fact = repos.memory_facts.create(MemoryFact(category="preference", content="Likes dark mode"))
    adapter = MemoryManageAdapter(repos)

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_1", tool_name="memory.manage", capability=Capability.MEMORY_MANAGE,
            input={"operation": "forget", "fact_id": fact.id},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert repos.memory_facts.get(fact.id) is None


@pytest.mark.asyncio
async def test_memory_manage_forget_fails_clearly_for_an_unknown_fact(tmp_path) -> None:
    repos, _audit = make_repos(tmp_path)
    adapter = MemoryManageAdapter(repos)

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_1", tool_name="memory.manage", capability=Capability.MEMORY_MANAGE,
            input={"operation": "forget", "fact_id": "mem_missing"},
        )
    )

    assert result.status == ToolResultStatus.FAILED
    assert "mem_missing" in (result.error_message or "")
