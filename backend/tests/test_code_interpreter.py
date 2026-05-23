from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration.default_plans import build_default_task_plan
from agent_control.schemas import Capability, IntentRoute, OrchestrationIntent, RiskLevel, TaskRecord, ToolCallRequest
from agent_control.tools.code_interpreter import CodeInterpreterAdapter
from agent_control.tools.registry import build_tool_registry

T = TypeVar("T", bound=BaseModel)


class FakeScriptProvider:
    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        return ""

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        return ""

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T]) -> T:
        return output_model.model_validate(
            {
                "summary": "Write a small result file.",
                "code": "from pathlib import Path\nPath('result.txt').write_text('ok', encoding='utf-8')\nprint('created result')\n",
                "expected_files": ["result.txt"],
            }
        )


class MalformedMarkdownProvider(FakeScriptProvider):
    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T]) -> T:
        return output_model.model_validate(
            {
                "summary": "Malformed Markdown writer.",
                "code": 'Path("meeting-report.md").write_text("# Meeting\n\n- broken", encoding="utf-8")',
                "expected_files": ["meeting-report.md"],
            }
        )


class BrokenStructuredProvider(FakeScriptProvider):
    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T]) -> T:
        raise ValueError("LLM structured output failed validation")


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        _env_file=None,
        capabilities={Capability.TERMINAL_RUN: CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.HIGH)},
        adapters={
            "code_interpreter": {"enabled": True, "workspace_root": str(tmp_path / "code")},
            "workspace": {"root_dir": str(tmp_path / "workspaces")},
        },
    )


def _request(tmp_path: Path, operation: str, **payload) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task_code",
        tool_name="code.interpreter",
        capability=Capability.TERMINAL_RUN,
        input={"operation": operation, "workspace_dir": str(tmp_path / "code" / "task_code"), **payload},
    )


def test_registry_exposes_code_interpreter_when_terminal_run_is_enabled(tmp_path) -> None:
    registry = build_tool_registry(_settings(tmp_path), backend_base_url="http://127.0.0.1:8765", provider=FakeScriptProvider())

    definitions = {definition.name: definition for definition in registry.definitions}
    assert definitions["code.interpreter"].enabled is True
    assert "generate_and_run" in definitions["code.interpreter"].operations
    assert "code.interpreter" in registry.adapters


@pytest.mark.asyncio
async def test_code_interpreter_runs_python_in_managed_workspace(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter)

    result = await adapter.execute(
        _request(
            tmp_path,
            "run_python",
            code="from pathlib import Path\nPath('answer.txt').write_text('42', encoding='utf-8')\nprint('done')\n",
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["returncode"] == 0
    assert "answer.txt" in result.output["files_created"]
    assert (tmp_path / "code" / "task_code" / "answer.txt").read_text(encoding="utf-8") == "42"


@pytest.mark.asyncio
async def test_code_interpreter_generate_and_run_uses_local_llm_provider(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter, provider=FakeScriptProvider())

    result = await adapter.execute(_request(tmp_path, "generate_and_run", objective="Create a result file."))

    assert result.status.value == "succeeded"
    assert result.output["generated"] is True
    assert result.output["generation_summary"] == "Write a small result file."
    assert "result.txt" in result.output["files_created"]


@pytest.mark.asyncio
async def test_code_interpreter_repairs_malformed_generated_markdown_script(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter, provider=MalformedMarkdownProvider())

    result = await adapter.execute(
        _request(
            tmp_path,
            "generate_and_run",
            objective="Turn these notes into a Markdown report named meeting-report.md: desktop inspection passed, browser screenshot pending.",
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["generation_repaired"] is True
    assert "meeting-report.md" in result.output["files_created"]
    assert "desktop inspection passed" in (tmp_path / "code" / "task_code" / "meeting-report.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_code_interpreter_falls_back_when_structured_generation_fails_for_simple_file(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter, provider=BrokenStructuredProvider())

    result = await adapter.execute(
        _request(
            tmp_path,
            "generate_and_run",
            objective="Run a small local script that creates route-checklist.md: inspect desktop, search files, deliver artifact.",
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["generation_repaired"] is True
    assert "route-checklist.md" in result.output["files_created"]


@pytest.mark.asyncio
async def test_code_interpreter_blocks_unsafe_imports(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter)

    result = await adapter.execute(_request(tmp_path, "run_python", code="import subprocess\nprint('bad')\n"))

    assert result.status.value == "failed"
    assert "blocked import" in (result.error_message or "")


def test_intent_routes_code_interpreter_to_bounded_python(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        TaskRecord(
            objective="Use the local code interpreter to create a small CSV summary.",
            metadata={
                "orchestration_intent": OrchestrationIntent(
                    route=IntentRoute.CODE_INTERPRETER,
                    operation="generate_and_run",
                    objective="Create a small CSV summary.",
                    reasoning="The LLM selected bounded Python execution.",
                ).model_dump(mode="json")
            },
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "code.interpreter:generate_and_run"
    ]
    assert plan.steps[0].tool_input["workspace_dir"].startswith(str(tmp_path / "code"))


def test_code_interpreter_plan_preserves_original_inline_data(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        TaskRecord(
            objective="Normalize task list to JSON",
            metadata={
                "original_message_text": (
                    "Use the local code interpreter to normalize this task list into tasks-normalized.json: "
                    "task A priority high owner Oney; task B priority low owner Agent."
                ),
                "orchestration_intent": OrchestrationIntent(
                    route=IntentRoute.CODE_INTERPRETER,
                    operation="generate_and_run",
                    objective="Normalize task list to JSON",
                    reasoning="The LLM selected bounded Python execution.",
                    file_path="tasks-normalized.json",
                ).model_dump(mode="json"),
            },
        ),
    )

    assert plan is not None
    assert "task A priority high owner Oney" in plan.steps[0].tool_input["objective"]
    assert plan.steps[0].tool_input["context"] == "Router objective: Normalize task list to JSON"
