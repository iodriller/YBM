from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from agent_control.config import AppSettings, CapabilityPolicy, CodeInterpreterAdapterConfig
from agent_control.schemas import Capability, RiskLevel, ToolCallRequest
from agent_control.tools import code_interpreter as code_interpreter_module
from agent_control.tools.code_interpreter import (
    CodeExecutionPlan,
    CodeExecutionResult,
    CodeInterpreterAdapter,
    DockerPythonBackend,
    ProcessExecutionResult,
)
from agent_control.tools.registry import build_tool_registry

T = TypeVar("T", bound=BaseModel)

class FakeScriptProvider:
    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        return ""

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        return ""

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T], **_ignored_kwargs) -> T:
        return output_model.model_validate(
            {
                "summary": "Write a small result file.",
                "code": "from pathlib import Path\nPath('result.txt').write_text('ok', encoding='utf-8')\nprint('created result')\n",
                "expected_files": ["result.txt"],
            }
        )

class MalformedMarkdownProvider(FakeScriptProvider):
    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T], **_ignored_kwargs) -> T:
        return output_model.model_validate(
            {
                "summary": "Malformed Markdown writer.",
                "code": 'Path("meeting-report.md").write_text("# Meeting\n\n- broken", encoding="utf-8")',
                "expected_files": ["meeting-report.md"],
            }
        )

class BrokenStructuredProvider(FakeScriptProvider):
    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T], **_ignored_kwargs) -> T:
        raise ValueError("LLM structured output failed validation")

class FakeArtifactRepository:
    def __init__(self) -> None:
        self.created = []

    def create(self, artifact):
        self.created.append(artifact)
        return artifact

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

    # approved=True: Docker isn't available in this test environment, so the
    # run would otherwise fall back to unsandboxed execution and need
    # approval first - see test_code_interpreter_generated_run_needs_approval_*
    # below for that gate itself.
    result = await adapter.execute(
        _request(tmp_path, "generate_and_run", objective="Create a result file.", approved=True)
    )

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
            approved=True,
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
            approved=True,
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["generation_repaired"] is True
    assert "route-checklist.md" in result.output["files_created"]

@pytest.mark.asyncio
async def test_code_interpreter_blocks_dangerous_imports_by_default(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(CodeInterpreterAdapterConfig(workspace_root=str(tmp_path / "code")))

    result = await adapter.execute(_request(tmp_path, "run_python", code="import subprocess\nprint('bad')\n"))

    assert result.status.value == "failed"
    assert "blocked import" in (result.error_message or "")

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "import importlib\nimportlib.import_module('os')\n",
        "from importlib import import_module\nimport_module('subprocess')\n",
        "import multiprocessing\n",
        "import winreg\n",
    ],
)
async def test_code_interpreter_blocks_import_bypass_modules_by_default(tmp_path, code) -> None:
    # importlib.import_module dynamically imports a module without a static
    # Import/ImportFrom AST node - _validate_python's blocked_imports check
    # wouldn't see "os"/"subprocess" being reached this way unless importlib
    # itself is blocked. multiprocessing (process spawning) and winreg
    # (Windows registry) are the same risk class as subprocess/os but were
    # missing from the original list (docs/HISTORY.md P5).
    adapter = CodeInterpreterAdapter(CodeInterpreterAdapterConfig(workspace_root=str(tmp_path / "code")))

    result = await adapter.execute(_request(tmp_path, "run_python", code=code))

    assert result.status.value == "failed"
    assert "blocked import" in (result.error_message or "")

@pytest.mark.asyncio
async def test_code_interpreter_output_includes_backend_metadata(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter)

    result = await adapter.execute(_request(tmp_path, "run_python", code="print('metadata ok')\n"))

    assert result.status.value == "succeeded"
    assert result.output["backend"] == "local_subprocess"
    assert result.output["execution_profile"] == "trusted"
    assert result.output["sandboxed"] is False
    assert result.output["resource_limits"]["timeout_seconds"] == 60
    assert "duration_seconds" in result.output["resource_usage"]

@pytest.mark.asyncio
async def test_code_interpreter_can_block_configured_imports(tmp_path) -> None:
    config = _settings(tmp_path).adapters.code_interpreter.model_copy(update={"blocked_imports": ["subprocess"]})
    adapter = CodeInterpreterAdapter(config)

    result = await adapter.execute(_request(tmp_path, "run_python", code="import subprocess\nprint('bad')\n"))

    assert result.status.value == "failed"
    assert "blocked import" in (result.error_message or "")

@pytest.mark.asyncio
async def test_code_interpreter_untrusted_run_python_needs_approval(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter)

    result = await adapter.execute(
        _request(tmp_path, "run_python", code="print('blocked until approved')\n", execution_profile="untrusted")
    )

    assert result.status.value == "needs_approval"
    assert "requires approval" in (result.error_message or "")

@pytest.mark.asyncio
async def test_code_interpreter_health_reports_backends(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter)

    result = await adapter.execute(_request(tmp_path, "health"))

    assert result.status.value == "succeeded"
    assert result.output["health"]["default_backend"] == "local_subprocess"
    names = {item["name"] for item in result.output["health"]["backends"]}
    assert "local_subprocess" in names
    assert "docker_python" in names

@pytest.mark.asyncio
async def test_code_interpreter_health_warns_when_import_blocklist_empty(tmp_path) -> None:
    config = _settings(tmp_path).adapters.code_interpreter.model_copy(update={"blocked_imports": []})
    adapter = CodeInterpreterAdapter(config)

    result = await adapter.execute(_request(tmp_path, "health"))

    assert result.status.value == "succeeded"
    assert result.output["health"]["safety_warnings"]
    assert "blocklist is empty" in result.output["stdout"]

@pytest.mark.asyncio
async def test_code_interpreter_health_warns_when_untrusted_default_is_unavailable_docker(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter)

    result = await adapter.execute(_request(tmp_path, "health"))

    assert result.status.value == "succeeded"
    warnings = result.output["health"]["safety_warnings"]
    assert any("untrusted_default_backend is docker_python" in item for item in warnings)
    assert "runs UNSANDBOXED on the host" in result.output["stdout"]

@pytest.mark.asyncio
async def test_code_interpreter_generated_run_needs_approval_on_silent_docker_fallback(tmp_path) -> None:
    # Generated code is normally exempt from approval (it's meant to be a
    # self-contained, automatic operation) - but not when it would silently
    # fall back from the configured sandboxed backend to unsandboxed
    # local_subprocess (Docker unavailable, the state in this test
    # environment): full process-privilege execution of LLM-authored code
    # with no human review is exactly the gap this gate closes.
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter, provider=FakeScriptProvider())

    result = await adapter.execute(_request(tmp_path, "generate_and_run", objective="Create a result file."))

    assert result.status.value == "needs_approval"
    assert "unsandboxed" in (result.error_message or "")
    assert "docker_python backend is unavailable" in (result.error_message or "")

@pytest.mark.asyncio
async def test_code_interpreter_generated_run_warns_on_silent_docker_fallback_once_approved(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter, provider=FakeScriptProvider())

    result = await adapter.execute(
        _request(tmp_path, "generate_and_run", objective="Create a result file.", approved=True)
    )

    assert result.status.value == "succeeded"
    assert result.output["backend"] == "local_subprocess"
    assert result.output["backend_fallback_warning"] is not None
    assert "docker_python backend is unavailable" in result.output["backend_fallback_warning"]
    assert "Warning: docker_python backend is unavailable" in result.output["summary"]

@pytest.mark.asyncio
async def test_code_interpreter_inspect_state_lists_workspace_files(tmp_path) -> None:
    workspace = tmp_path / "code" / "task_code"
    workspace.mkdir(parents=True)
    (workspace / "existing.txt").write_text("ok", encoding="utf-8")
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter)

    result = await adapter.execute(_request(tmp_path, "inspect_state"))

    assert result.status.value == "succeeded"
    assert "existing.txt" in result.output["files_after"]

@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["solve_once", "build_temp_helper", "repair_script"])
async def test_code_interpreter_advertised_generation_operations_execute(tmp_path, operation) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter, provider=FakeScriptProvider())
    payload = {"objective": "Create a result file.", "approved": True}
    if operation == "repair_script":
        payload.update({"failing_code": "print(unknown)", "error_text": "NameError"})

    result = await adapter.execute(_request(tmp_path, operation, **payload))

    assert result.status.value == "succeeded"
    assert result.output["operation"] == operation
    assert result.output["generated"] is True
    assert "result.txt" in result.output["files_created"]

@pytest.mark.asyncio
async def test_code_interpreter_registers_generated_artifacts_but_not_script(tmp_path) -> None:
    artifacts = FakeArtifactRepository()
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter, artifacts=artifacts)

    result = await adapter.execute(
        _request(
            tmp_path,
            "run_python",
            code=(
                "from pathlib import Path\n"
                "Path('report.md').write_text('# Report', encoding='utf-8')\n"
                "Path('data.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n"
                "Path('workbook.xlsx').write_bytes(b'PK\\x03\\x04')\n"
                "print('created artifacts')\n"
            ),
        )
    )

    assert result.status.value == "succeeded"
    registered = {Path(item.uri).name for item in artifacts.created}
    assert registered == {"report.md", "data.csv", "workbook.xlsx"}
    assert "script.py" not in registered
    assert len(result.output["artifact_ids"]) == 3

@pytest.mark.asyncio
async def test_code_interpreter_can_select_docker_backend_when_configured(tmp_path, monkeypatch) -> None:
    base_config = _settings(tmp_path).adapters.code_interpreter
    config = base_config.model_copy(
        update={
            "backends": ["local_subprocess", "docker_python"],
            "docker": base_config.docker.model_copy(update={"enabled": True, "image": "python:3.12-slim", "pull_policy": "never"}),
        }
    )
    adapter = CodeInterpreterAdapter(config)

    monkeypatch.setattr(DockerPythonBackend, "available", lambda self: True)

    async def fake_execute(self, plan):
        plan.script_path.write_text(plan.code + "\nprint('docker fake')\n", encoding="utf-8")
        return CodeExecutionResult(
            returncode=0,
            stdout="docker fake\n",
            stderr="",
            backend="docker_python",
            sandboxed=True,
            resource_usage={"duration_seconds": 0.01},
            resource_limits={"memory": "512m"},
            network_enabled=plan.allow_network,
        )

    monkeypatch.setattr(DockerPythonBackend, "execute", fake_execute)

    result = await adapter.execute(
        _request(
            tmp_path,
            "run_python",
            code="print('hello')\n",
            backend="docker_python",
            approved=True,
            execution_profile="untrusted",
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["backend"] == "docker_python"
    assert result.output["sandboxed"] is True

@pytest.mark.asyncio
async def test_docker_backend_runs_with_network_disabled_and_security_flags(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "code" / "task_code"
    workspace.mkdir(parents=True)
    script = workspace / "script.py"
    script.write_text("print('hello')\n", encoding="utf-8")
    base_config = CodeInterpreterAdapterConfig(workspace_root=str(tmp_path / "code"))
    config = base_config.model_copy(
        update={
            "docker": base_config.docker.model_copy(update={"enabled": True, "pull_policy": "never"}),
        }
    )
    backend = DockerPythonBackend(config)
    captured: list[list[str]] = []

    monkeypatch.setattr(code_interpreter_module.shutil, "which", lambda _name: "docker")

    async def fake_run_command(args, *, cwd, timeout):
        captured.append(args)
        return ProcessExecutionResult(returncode=0, stdout="hello\n", stderr="", duration_seconds=0.01, pid=123)

    monkeypatch.setattr(code_interpreter_module, "_run_command", fake_run_command)

    result = await backend.execute(
        CodeExecutionPlan(
            request_id="req",
            task_id="task_code",
            code="print('hello')\n",
            workspace=workspace,
            script_path=script,
            timeout_seconds=30,
            generated=False,
            backend="docker_python",
            execution_profile="untrusted",
            allow_network=False,
        )
    )

    assert result.backend == "docker_python"
    args = captured[0]
    assert "--network" in args
    assert "none" in args
    assert "--security-opt" in args
    assert "no-new-privileges" in args
    assert "--mount" in args
    assert any(str(workspace) in item for item in args)

@pytest.mark.asyncio
async def test_code_interpreter_fallback_can_create_and_load_excel_workbook(tmp_path) -> None:
    adapter = CodeInterpreterAdapter(_settings(tmp_path).adapters.code_interpreter, provider=BrokenStructuredProvider())

    result = await adapter.execute(
        _request(
            tmp_path,
            "generate_and_run",
            objective="Create a simple Python script to generate and load an Excel workbook.",
            approved=True,
        )
    )

    assert result.status.value == "succeeded"
    assert "workbook.xlsx" in result.output["files_created"]
    assert "created and loaded" in result.output["stdout"]

def test_import_validation_allows_openpyxl_and_third_party() -> None:
    from agent_control.tools.code_interpreter import _validate_python

    code = "import openpyxl\nimport pandas\nimport requests\nprint('ok')\n"
    # Should not raise — third-party imports are permitted by default
    _validate_python(code, allowed_imports=set(), blocked_imports=set())

