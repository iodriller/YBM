from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from agent_control.config import CodeInterpreterAdapterConfig
from agent_control.llm.providers import LLMProvider
from agent_control.prompts import prompt_text, render_prompt
from agent_control.schemas import Artifact, ArtifactType, ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus


logger = logging.getLogger(__name__)


class GeneratedPythonScript(BaseModel):
    summary: str = Field(min_length=1)
    code: str = Field(min_length=1)
    expected_files: list[str] = Field(default_factory=list)


class CodeInterpreterAdapter:
    """Bounded local Python execution in a managed task workspace."""

    def __init__(
        self,
        config: CodeInterpreterAdapterConfig,
        provider: LLMProvider | None = None,
        artifacts: object | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        # Optional artifact repository — when present, files produced by a script
        # are registered as task artifacts so later steps (e.g. artifact.deliver)
        # can find them by id without any path guessing.
        self.artifacts = artifacts

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        if not self.config.enabled:
            return _failed(request, "code interpreter adapter is disabled")
        operation = str(request.input.get("operation") or "run_python")
        try:
            if operation == "run_python":
                output = await self._run_python(request, generated=False)
            elif operation == "generate_and_run":
                output = await self._generate_and_run(request)
            else:
                return _failed(request, f"unsupported code interpreter operation: {operation}")
        except TimeoutError:
            return ToolCallResult(
                request_id=request.id,
                status=ToolResultStatus.TIMEOUT,
                error_class=ErrorClass.TRANSIENT,
                error_message="code interpreter command timed out",
            )
        except Exception as exc:
            return _failed(request, f"code interpreter operation failed: {exc}")

        output["operation"] = operation
        output["terminal_output"] = [_terminal_output(operation, output)]
        status = ToolResultStatus.SUCCEEDED if int(output.get("returncode") or 0) == 0 else ToolResultStatus.FAILED
        return ToolCallResult(
            request_id=request.id,
            status=status,
            output=output,
            error_class=ErrorClass.ADAPTER_FAILED if status == ToolResultStatus.FAILED else None,
            error_message=(output.get("summary") if status == ToolResultStatus.FAILED else None),
        )

    async def _generate_and_run(self, request: ToolCallRequest) -> dict[str, Any]:
        if self.provider is None:
            raise ValueError("LLM provider is required for generate_and_run")
        objective = str(request.input["objective"]).strip()
        workspace = self._workspace(request)
        context_text = str(request.input.get("context") or "No extra context.")
        generation_repaired = False
        regenerated_from_failure = False

        async def _generate(previous_attempt: str) -> "GeneratedPythonScript":
            return await self.provider.generate_structured(
                prompt_text("base/code_interpreter_system.md"),
                render_prompt(
                    "tasks/code_interpreter_user.md",
                    objective=objective,
                    context=context_text,
                    workspace_dir=str(workspace),
                    previous_attempt=previous_attempt,
                ),
                GeneratedPythonScript,
            )

        try:
            generated = await _generate("")
            generated = generated.model_copy(update={"code": _clean_generated_code(generated.code)})
        except Exception:
            fallback = _fallback_generated_script(objective)
            if fallback is None:
                raise
            generated = fallback
            generation_repaired = True

        updated = request.model_copy(update={"input": {**request.input, "code": generated.code}})
        try:
            output = await self._run_python(updated, generated=True)
        except (SyntaxError, ValueError) as exc:
            # Syntax errors mean the LLM produced invalid Python. Regenerate
            # with the offending code + the parser error. If the retry still
            # raises (e.g. the LLM produced the same broken code, or
            # regeneration itself failed), fall back to the static template.
            regenerated_from_failure = True
            output = None
            try:
                retry_gen = await _generate(_previous_attempt_block(generated.code, str(exc), kind="parse_error"))
                retry_generated = retry_gen.model_copy(update={"code": _clean_generated_code(retry_gen.code)})
                retry_updated = request.model_copy(update={"input": {**request.input, "code": retry_generated.code}})
                try:
                    output = await self._run_python(retry_updated, generated=True)
                    generated = retry_generated
                    updated = retry_updated
                except (SyntaxError, ValueError):
                    output = None
            except Exception:
                output = None
            if output is None:
                fallback = _fallback_generated_script(objective)
                if fallback is None:
                    raise exc
                generation_repaired = True
                generated = fallback
                updated = request.model_copy(update={"input": {**request.input, "code": generated.code}})
                output = await self._run_python(updated, generated=True)

        if int(output.get("returncode") or 0) != 0:
            # Runtime crash. Show the LLM what its code produced and ask it to
            # take a different approach (different libraries, different
            # algorithm, defensive defaults). This is the key fix for the
            # "LLM regenerates the same broken script" failure class.
            regenerated_from_failure = True
            previous_block = _previous_attempt_block(
                generated.code,
                f"{output.get('stderr','')}\nstdout: {output.get('stdout','')}",
                kind="runtime_error",
            )
            try:
                retry_gen = await _generate(previous_block)
                generated = retry_gen.model_copy(update={"code": _clean_generated_code(retry_gen.code)})
                updated = request.model_copy(update={"input": {**request.input, "code": generated.code}})
                output = await self._run_python(updated, generated=True)
            except Exception:
                # If the LLM regen call itself fails (provider error, schema
                # validation, etc.), fall back to the static template as a
                # final attempt — same as before.
                fallback = _fallback_generated_script(objective)
                if fallback is not None and fallback.code != generated.code:
                    generation_repaired = True
                    generated = fallback
                    updated = request.model_copy(update={"input": {**request.input, "code": generated.code}})
                    output = await self._run_python(updated, generated=True)

        output["generation_summary"] = generated.summary
        output["expected_files"] = generated.expected_files
        if generation_repaired:
            output["generation_repaired"] = True
        if regenerated_from_failure:
            output["regenerated_from_failure"] = True
        return output

    async def _run_python(self, request: ToolCallRequest, *, generated: bool) -> dict[str, Any]:
        code = str(request.input["code"])
        if len(code) > self.config.max_code_chars:
            raise ValueError(f"code exceeds configured limit of {self.config.max_code_chars} characters")
        _validate_python(code, allowed_imports=set(self.config.allowed_imports), blocked_imports=set(self.config.blocked_imports))
        workspace = self._workspace(request)
        workspace.mkdir(parents=True, exist_ok=True)
        before = _relative_files(workspace, max_files=self.config.max_files_listed)
        script_path = _safe_child_path(workspace, str(request.input.get("script_name") or "script.py"))
        if script_path.suffix.lower() != ".py":
            script_path = script_path.with_suffix(".py")
        script_path.write_text(code, encoding="utf-8")
        timeout = int(request.input.get("timeout_seconds") or self.config.timeout_seconds)
        returncode, stdout, stderr = await _run_python_script(
            self.config.python_executable or sys.executable,
            script_path,
            workspace,
            timeout=timeout,
        )
        stdout = stdout[: self.config.max_output_chars]
        stderr = stderr[: self.config.max_output_chars]
        after = _relative_files(workspace, max_files=self.config.max_files_listed)
        created = sorted(set(after) - set(before))
        summary = _summary(returncode, stdout, stderr, created)
        artifact_ids = self._register_created_artifacts(request.task_id, workspace, created)
        return {
            "workspace_dir": str(workspace),
            "script_path": str(script_path),
            "files_before": before,
            "files_after": after,
            "files_created": created,
            "artifact_ids": artifact_ids,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
            "summary": summary,
            "generated": generated,
        }

    def _register_created_artifacts(self, task_id: str, workspace: Path, created: list[str]) -> list[str]:
        """Register each newly-created file as a task artifact.

        Skip the generator's own ``script.py`` (an implementation detail, not a
        user-facing output). The returned ids let downstream tools like
        ``artifact.deliver`` resolve the file without path guessing.
        """
        if self.artifacts is None or not created:
            return []
        ids: list[str] = []
        for relative in created:
            if relative == "script.py":
                continue
            full = (workspace / relative).resolve()
            try:
                if not full.is_file():
                    continue
            except OSError:
                continue
            try:
                artifact = self.artifacts.create(   # type: ignore[attr-defined]
                    Artifact(
                        task_id=task_id,
                        type=ArtifactType.GENERATED_FILE,
                        uri=str(full),
                        content_preview=f"Generated by code.interpreter: {relative}",
                        metadata={"source": "code.interpreter", "workspace_dir": str(workspace)},
                    )
                )
                ids.append(artifact.id)
            except Exception:
                # Registration is best-effort — a missing repo or DB hiccup must
                # not break the actual code execution result.
                continue
        return ids

    def _workspace(self, request: ToolCallRequest) -> Path:
        root = Path(self.config.workspace_root).expanduser().resolve()
        if request.input.get("workspace_dir"):
            workspace = Path(str(request.input["workspace_dir"])).expanduser().resolve()
        else:
            workspace = root / f"task_{_safe_segment(request.task_id)}_{uuid4().hex[:8]}"
        if root != workspace and root not in workspace.parents:
            raise ValueError(f"workspace is outside configured code interpreter root: {workspace}")
        return workspace


def _validate_python(code: str, *, allowed_imports: set[str], blocked_imports: set[str]) -> None:
    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _validate_import(alias.name, allowed_imports=allowed_imports, blocked_imports=blocked_imports)
        elif isinstance(node, ast.ImportFrom):
            _validate_import(node.module or "", allowed_imports=allowed_imports, blocked_imports=blocked_imports)
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in {"eval", "exec", "compile", "__import__", "input"}:
                raise ValueError(f"blocked unsafe builtin call: {name}")


def _validate_import(module: str, *, allowed_imports: set[str], blocked_imports: set[str]) -> None:
    root = module.split(".", 1)[0]
    if root in blocked_imports:
        raise ValueError(f"blocked import: {root}")
    # When an allowlist is configured (non-empty), any import outside it is
    # rejected. Empty allowlist = no whitelist constraint (default behavior).
    if allowed_imports and root not in allowed_imports:
        raise ValueError(f"import not in allowed_imports allowlist: {root}")


def _call_name(value: ast.AST) -> str | None:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


def _previous_attempt_block(prev_code: str, error_text: str, *, kind: str) -> str:
    """Format the failed prior attempt so the LLM can fix its own mistake.

    ``kind`` is ``"parse_error"`` or ``"runtime_error"``. Truncated aggressively
    to keep the regeneration prompt under control.
    """
    error_snippet = (error_text or "").strip()[-1500:]
    code_snippet = (prev_code or "").strip()
    if len(code_snippet) > 1800:
        code_snippet = code_snippet[:1800] + "\n# ... (truncated)"
    label = "parse error" if kind == "parse_error" else "runtime error"
    return (
        f"Previous attempt to write code for this objective FAILED with a {label}.\n"
        f"DO NOT regenerate the same script. Take a different approach: use a different\n"
        f"library, a different algorithm, or simpler defensive defaults. If the previous\n"
        f"attempt depended on a non-standard package that wasn't installed, switch to the\n"
        f"Python standard library only.\n"
        f"\n"
        f"Previous code:\n"
        f"```python\n{code_snippet}\n```\n"
        f"\n"
        f"Error/stderr from the failed run:\n"
        f"```\n{error_snippet}\n```"
    )


def _clean_generated_code(code: str) -> str:
    text = str(code).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if "\\n" in text and "\n" not in text:
        try:
            text = text.encode("utf-8").decode("unicode_escape")
        except Exception:
            logger.debug("unicode_escape decode failed; leaving text unchanged", exc_info=True)
    return text


def _fallback_generated_script(objective: str) -> GeneratedPythonScript | None:
    output_name = _output_name_from_objective(objective)
    if output_name is None:
        return None
    suffix = Path(output_name).suffix.lower()
    if suffix == ".xlsx":
        return _fallback_excel_script(output_name, objective)
    if suffix not in {".md", ".txt", ".json", ".csv"}:
        return None
    content = _fallback_content(objective, output_name)
    code = (
        "from pathlib import Path\n"
        f"output = Path({output_name!r})\n"
        f"output.write_text({content!r}, encoding='utf-8')\n"
        "print(f'created {output}')\n"
    )
    return GeneratedPythonScript(
        summary=f"Create {output_name} with a deterministic fallback script.",
        code=code,
        expected_files=[output_name],
    )


def _output_name_from_objective(objective: str) -> str | None:
    match = re.search(r"\b(?:named|called|file)\s+([A-Za-z0-9_.-]+\.(?:md|txt|json|csv|xlsx))\b", objective, flags=re.IGNORECASE)
    if match:
        return _safe_output_name(match.group(1))
    match = re.search(r"\b([A-Za-z0-9_.-]+\.(?:md|txt|json|csv|xlsx))\b", objective, flags=re.IGNORECASE)
    if match:
        return _safe_output_name(match.group(1))
    if re.search(r"\b(excel|xlsx|spreadsheet|workbook)\b", objective, flags=re.IGNORECASE):
        return "workbook.xlsx"
    return None


def _safe_output_name(value: str) -> str | None:
    name = Path(value.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        return None
    if ".." in Path(name).parts:
        return None
    return name


def _fallback_content(objective: str, output_name: str) -> str:
    suffix = Path(output_name).suffix.lower()
    notes = _notes_from_objective(objective)
    if suffix == ".md":
        title = Path(output_name).stem.replace("-", " ").replace("_", " ").title()
        bullets = "\n".join(f"- {item}" for item in notes) if notes else f"- Generated from request: {objective}"
        return f"# {title}\n\n{bullets}\n"
    if suffix == ".json":
        import json

        return json.dumps({"source": objective, "items": notes}, indent=2) + "\n"
    if suffix == ".csv":
        rows = ["item"] + [item.replace(",", " ") for item in notes]
        return "\n".join(rows) + "\n"
    return "\n".join(notes or [objective]) + "\n"


def _fallback_excel_script(output_name: str, objective: str) -> GeneratedPythonScript:
    rows = [["Item", "Value"], ["alpha", "2"], ["beta", "4"], ["gamma", "6"]]
    if "hello" in objective.lower():
        rows = [["Message"], ["Hello from the generated workbook"]]
    code = f"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape
import re

output = Path({output_name!r})
rows = {rows!r}

def sheet_xml(rows):
    xml_rows = []
    for row_index, row in enumerate(rows, 1):
        cells = []
        for col_index, value in enumerate(row, 1):
            col = chr(64 + col_index)
            text = escape(str(value))
            cells.append(f'<c r="{{col}}{{row_index}}" t="inlineStr"><is><t>{{text}}</t></is></c>')
        xml_rows.append(f'<row r="{{row_index}}">' + ''.join(cells) + '</row>')
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(xml_rows) + '</sheetData></worksheet>'

with ZipFile(output, "w", ZIP_DEFLATED) as workbook:
    workbook.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    workbook.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    workbook.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
    workbook.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml(rows))

with ZipFile(output) as workbook:
    xml = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
loaded_values = re.findall(r"<t>(.*?)</t>", xml)
print(f"created and loaded {{output}}")
print("values: " + ", ".join(loaded_values))
"""
    return GeneratedPythonScript(
        summary=f"Create and load {output_name} using only the Python standard library.",
        code=_clean_generated_code(code),
        expected_files=[output_name],
    )


def _notes_from_objective(objective: str) -> list[str]:
    text = objective.split(":", 1)[1] if ":" in objective else objective
    parts = re.split(r"[,;\n]+|\s+-\s+", text)
    notes = [re.sub(r"\s+", " ", item).strip(" .") for item in parts]
    return [item for item in notes if item][:20]


async def _run_python_script(executable: str, script_path: Path, workspace: Path, *, timeout: int) -> tuple[int, str, str]:
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    process = await asyncio.create_subprocess_exec(
        executable,
        str(script_path),
        cwd=str(workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise TimeoutError from exc
    return process.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def _safe_child_path(workspace: Path, relative_path: str) -> Path:
    # Block obviously-malicious traversal even when an absolute path is given.
    if ".." in Path(relative_path).parts:
        raise ValueError(f"script path escaped workspace: {relative_path}")
    # Accept either a relative path or an absolute path that already points
    # INTO the workspace (e.g. the LLM emitted "{{workspace_dir}}/script.py"
    # which the executor substituted into an absolute path).
    raw = Path(relative_path)
    if raw.is_absolute():
        target = raw.resolve()
    else:
        cleaned = relative_path.replace("\\", "/").strip().lstrip("/")
        if not cleaned:
            raise ValueError(f"script path escaped workspace: {relative_path}")
        target = (workspace / cleaned).resolve()
    if workspace != target and workspace not in target.parents:
        raise ValueError(f"script path escaped workspace: {relative_path}")
    return target


def _relative_files(workspace: Path, *, max_files: int) -> list[str]:
    if not workspace.exists():
        return []
    files = []
    for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
        files.append(str(path.resolve().relative_to(workspace)))
        if len(files) >= max_files:
            break
    return files


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "task"


def _summary(returncode: int, stdout: str, stderr: str, created: list[str]) -> str:
    if returncode == 0:
        prefix = f"Python completed successfully and created {len(created)} file(s)."
        text = stdout.strip()
        return f"{prefix} {text[:500]}" if text else prefix
    text = (stderr or stdout).strip()
    return f"Python failed with exit code {returncode}." + (f" {text[:500]}" if text else "")


def _terminal_output(operation: str, output: dict[str, Any]) -> dict[str, Any]:
    lines = [f"Code interpreter operation completed: {operation}"]
    lines.append(f"Workspace: {output.get('workspace_dir')}")
    if output.get("script_path"):
        lines.append(f"Script: {output['script_path']}")
    lines.append(f"Return code: {output.get('returncode')}")
    if output.get("files_created"):
        lines.append("Created files:")
        lines.extend(f"- {path}" for path in output["files_created"])
    if output.get("stdout"):
        lines.append("Stdout:")
        lines.append(str(output["stdout"])[:2000])
    if output.get("stderr"):
        lines.append("Stderr:")
        lines.append(str(output["stderr"])[:2000])
    return {
        "instance_id": "local-worker",
        "terminal_id": "code-interpreter",
        "content": "\n".join(line for line in lines if line is not None),
        "is_final": True,
        "exit_code": output.get("returncode") or 0,
        "source": "code_interpreter",
    }


def _failed(request: ToolCallRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        request_id=request.id,
        status=ToolResultStatus.FAILED,
        error_class=ErrorClass.ADAPTER_FAILED,
        error_message=message,
    )
