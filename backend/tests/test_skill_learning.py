"""Skills learned from successful runs are proposed, never self-installed.

An installed skill is injected into every future Operator prompt and followed
as instructions, so a system that writes its own would be changing tomorrow's
behavior with nobody having agreed to it. The queue is where a person decides.
"""

from __future__ import annotations

import json

import pytest

from agent_control.config import SkillsAdapterConfig
from agent_control.schemas import TaskRecord, TaskStatus
from agent_control.skill_learning import (
    SkillSuggestionStore,
    install,
    propose_from_task,
    worth_proposing,
)
from agent_control.tools.skills import _load_skills


class StubProvider:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        return self.reply


def _config(tmp_path, *, learning: bool = True) -> SkillsAdapterConfig:
    return SkillsAdapterConfig(enabled=True, root_dir=str(tmp_path / "skills"), learning_enabled=learning)


def _task(steps: int = 4, status: TaskStatus = TaskStatus.COMPLETED) -> TaskRecord:
    return TaskRecord(
        id="task_1", objective="pull the total out of an invoice pdf", status=status,
        metadata={
            "operator_history": [
                {"tool_name": "filesystem.manage", "input": {"operation": "search"},
                 "reasoning": "find it", "status": "succeeded"}
                for _ in range(steps)
            ]
        },
    )


GOOD_REPLY = json.dumps({
    "name": "Invoice Total Extraction",
    "description": "Pull the total from an invoice PDF.",
    "body": "1. Search for the PDF.\n2. Read it.\n3. Report the total.",
})


@pytest.mark.asyncio
async def test_a_procedure_is_queued_not_installed(tmp_path) -> None:
    config = _config(tmp_path)

    suggestion = await propose_from_task(StubProvider(GOOD_REPLY), config, _task())

    assert suggestion is not None
    assert SkillSuggestionStore.load(config).pending()[0].name == "Invoice Total Extraction"
    # Nothing reaches the catalog until a human accepts.
    assert _load_skills(config.root_dir) == []


@pytest.mark.asyncio
async def test_learning_off_spends_no_llm_call(tmp_path) -> None:
    provider = StubProvider(GOOD_REPLY)

    assert await propose_from_task(provider, _config(tmp_path, learning=False), _task()) is None
    assert provider.calls == 0


def test_trivial_tasks_are_not_procedures(tmp_path) -> None:
    """Two steps is not a runbook, and every skill costs prompt space in
    every future task."""
    assert worth_proposing(_task(steps=2), _config(tmp_path)) is False
    assert worth_proposing(_task(steps=4), _config(tmp_path)) is True


@pytest.mark.asyncio
async def test_a_one_off_produces_nothing(tmp_path) -> None:
    assert await propose_from_task(StubProvider("NONE"), _config(tmp_path), _task()) is None


@pytest.mark.asyncio
async def test_unparseable_model_output_is_dropped(tmp_path) -> None:
    assert await propose_from_task(StubProvider("sure! here you go"), _config(tmp_path), _task()) is None


@pytest.mark.asyncio
async def test_the_same_skill_is_not_queued_twice(tmp_path) -> None:
    config = _config(tmp_path)
    provider = StubProvider(GOOD_REPLY)

    assert await propose_from_task(provider, config, _task()) is not None
    assert await propose_from_task(provider, config, _task()) is None


def test_accepting_installs_a_readable_skill_with_provenance(tmp_path) -> None:
    config = _config(tmp_path)
    store = SkillSuggestionStore.load(config)
    suggestion = store.add("Invoice Total Extraction", "Pull the total.", "1. Search.\n2. Read.", _task())

    installed = install(config, suggestion)

    assert installed["name"] == "Invoice Total Extraction"
    skills = _load_skills(config.root_dir)
    assert len(skills) == 1
    assert "Learned from a completed task" in skills[0]["body"]


def test_rejecting_installs_nothing(tmp_path) -> None:
    config = _config(tmp_path)
    store = SkillSuggestionStore.load(config)
    suggestion = store.add("Bad Idea", "no", "1. do it", _task())

    assert store.decide(suggestion.id, accept=False).status == "rejected"
    assert _load_skills(config.root_dir) == []
    assert SkillSuggestionStore.load(config).pending() == []
