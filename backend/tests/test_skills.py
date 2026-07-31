"""docs/HISTORY.md Part 4 T1.3: user-droppable capability packs. A skill is
one markdown file (YAML frontmatter + instructions body) under
adapters.skills.root_dir, discovered fresh on every list/read call - no
code change, no restart.
"""

from __future__ import annotations

import pytest

from agent_control.config import AppSettings, Capability, CapabilityPolicy, RiskLevel, SkillsAdapterConfig
from agent_control.schemas import ToolCallRequest, ToolResultStatus
from agent_control.tools.registry import build_tool_registry
from agent_control.tools.skills import SkillsAdapter


def _write_skill(tmp_path, filename: str, *, name: str | None = "invoice-extraction", description: str = "How to pull the total from an invoice PDF.", body: str = "1. Read the PDF.\n2. Find the total line.") -> None:
    frontmatter_lines = ["---"]
    if name is not None:
        frontmatter_lines.append(f"name: {name}")
    frontmatter_lines.append(f"description: {description}")
    frontmatter_lines.append("---")
    text = "\n".join(frontmatter_lines) + "\n" + body
    (tmp_path / filename).write_text(text, encoding="utf-8")


def _request(operation: str, **payload) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task_skills",
        tool_name="skills.use",
        capability=Capability.TELEGRAM_RECEIVE,
        input={"operation": operation, **payload},
    )


@pytest.mark.asyncio
async def test_list_returns_name_and_description_but_never_the_body(tmp_path) -> None:
    """Progressive disclosure is the whole point: list must be cheap and
    must never leak full skill content into every single prompt."""
    _write_skill(tmp_path, "invoice.md")
    adapter = SkillsAdapter(SkillsAdapterConfig(root_dir=str(tmp_path)))

    result = await adapter.execute(_request("list"))

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["skills"] == [
        {"name": "invoice-extraction", "description": "How to pull the total from an invoice PDF."}
    ]
    assert "body" not in result.output["skills"][0]
    assert "Find the total line" not in str(result.output)


@pytest.mark.asyncio
async def test_read_returns_full_body_for_the_named_skill(tmp_path) -> None:
    _write_skill(tmp_path, "invoice.md")
    adapter = SkillsAdapter(SkillsAdapterConfig(root_dir=str(tmp_path)))

    result = await adapter.execute(_request("read", name="invoice-extraction"))

    assert result.status == ToolResultStatus.SUCCEEDED
    assert len(result.output["skills"]) == 1
    assert result.output["skills"][0]["body"] == "1. Read the PDF.\n2. Find the total line."
    assert "Find the total line" in result.output["terminal_output"][0]["content"]


@pytest.mark.asyncio
async def test_read_unknown_skill_fails_with_available_names_listed(tmp_path) -> None:
    _write_skill(tmp_path, "invoice.md", name="invoice-extraction")
    _write_skill(tmp_path, "resume.md", name="resume-formatting", description="How resumes should look.")
    adapter = SkillsAdapter(SkillsAdapterConfig(root_dir=str(tmp_path)))

    result = await adapter.execute(_request("read", name="nonexistent-skill"))

    assert result.status == ToolResultStatus.FAILED
    assert "nonexistent-skill" in result.error_message
    assert "invoice-extraction" in result.error_message
    assert "resume-formatting" in result.error_message


@pytest.mark.asyncio
async def test_read_requires_name_field() -> None:
    from agent_control.tools.contracts import SkillsInput
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SkillsInput(operation="read")


@pytest.mark.asyncio
async def test_list_on_empty_or_missing_directory_returns_no_skills_not_an_error(tmp_path) -> None:
    adapter = SkillsAdapter(SkillsAdapterConfig(root_dir=str(tmp_path / "does_not_exist")))

    result = await adapter.execute(_request("list"))

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["skills"] == []


@pytest.mark.asyncio
async def test_malformed_skill_file_is_skipped_without_hiding_valid_ones(tmp_path) -> None:
    """A broken skill file (bad YAML, missing name, no frontmatter at all)
    must not crash listing or hide every OTHER valid skill in the same
    directory - just itself."""
    _write_skill(tmp_path, "good.md", name="good-skill")
    (tmp_path / "no_frontmatter.md").write_text("just plain text, no frontmatter", encoding="utf-8")
    (tmp_path / "bad_yaml.md").write_text("---\nname: [unterminated\n---\nbody", encoding="utf-8")
    (tmp_path / "no_name.md").write_text("---\ndescription: has a description but no name\n---\nbody", encoding="utf-8")
    adapter = SkillsAdapter(SkillsAdapterConfig(root_dir=str(tmp_path)))

    result = await adapter.execute(_request("list"))

    assert result.status == ToolResultStatus.SUCCEEDED
    assert [s["name"] for s in result.output["skills"]] == ["good-skill"]


@pytest.mark.asyncio
async def test_list_is_sorted_and_capped_at_max_skills_listed(tmp_path) -> None:
    for i in range(5):
        _write_skill(tmp_path, f"skill_{i}.md", name=f"skill-{i:02d}", description=f"desc {i}")
    adapter = SkillsAdapter(SkillsAdapterConfig(root_dir=str(tmp_path), max_skills_listed=3))

    result = await adapter.execute(_request("list"))

    names = [s["name"] for s in result.output["skills"]]
    assert names == sorted(names)
    assert len(names) == 3


@pytest.mark.asyncio
async def test_unsupported_operation_fails_cleanly(tmp_path) -> None:
    adapter = SkillsAdapter(SkillsAdapterConfig(root_dir=str(tmp_path)))

    result = await adapter.execute(_request("delete"))

    assert result.status == ToolResultStatus.FAILED
    assert "unsupported" in result.error_message


def test_registry_exposes_skills_use_when_telegram_receive_is_enabled() -> None:
    settings = AppSettings(_env_file=None)  # TELEGRAM_RECEIVE is enabled by default

    registry = build_tool_registry(settings, "http://127.0.0.1:8765")

    definitions = {d.name: d for d in registry.definitions}
    assert definitions["skills.use"].enabled is True
    assert "skills.use" in registry.adapters
    assert "read" in definitions["skills.use"].operations


def test_registry_disables_skills_use_when_adapter_disabled_in_config() -> None:
    settings = AppSettings(_env_file=None, adapters={"skills": {"enabled": False}})

    registry = build_tool_registry(settings, "http://127.0.0.1:8765")

    definitions = {d.name: d for d in registry.definitions}
    assert definitions["skills.use"].enabled is False
    assert "skills.use" not in registry.adapters


def test_registry_disables_skills_use_when_telegram_receive_capability_is_off() -> None:
    settings = AppSettings(
        _env_file=None,
        capabilities={Capability.TELEGRAM_RECEIVE: CapabilityPolicy(enabled=False, max_risk_level=RiskLevel.LOW)},
    )

    registry = build_tool_registry(settings, "http://127.0.0.1:8765")

    definitions = {d.name: d for d in registry.definitions}
    assert definitions["skills.use"].enabled is False
