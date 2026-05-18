from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from agent_control.config import AdapterFactoryConfig
from agent_control.schemas import ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus


class AdapterFactoryAdapter:
    """Scaffold generated adapter proposals without importing or executing them."""

    def __init__(self, config: AdapterFactoryConfig) -> None:
        self.config = config

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        if not self.config.enabled:
            return _failed(request, "adapter factory is disabled")
        operation = str(request.input.get("operation") or "scaffold")
        try:
            if operation == "scaffold":
                output = self._scaffold(request)
            elif operation == "assess":
                output = self._assess(request)
            else:
                return _failed(request, f"unsupported adapter factory operation: {operation}")
        except Exception as exc:
            return _failed(request, f"adapter factory operation failed: {exc}")

        output["operation"] = operation
        output["terminal_output"] = [_terminal_output(operation, output)]
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

    def _assess(self, request: ToolCallRequest) -> dict[str, Any]:
        objective = str(request.input.get("objective") or request.input.get("prompt") or "").strip()
        name = _adapter_name(request)
        return {
            "adapter_name": name,
            "assessment": _assessment(objective),
            "cacheable": True,
            "execution_policy": "scaffold_only",
        }

    def _scaffold(self, request: ToolCallRequest) -> dict[str, Any]:
        objective = str(request.input.get("objective") or request.input.get("prompt") or "").strip()
        capability = str(request.input.get("capability") or "filesystem.write").strip()
        tool_name = str(request.input.get("tool_name") or _adapter_name(request)).strip()
        root = Path(self.config.root_dir).expanduser().resolve()
        adapter_dir = (root / _safe_segment(tool_name)).resolve()
        if root != adapter_dir and root not in adapter_dir.parents:
            raise ValueError("adapter path escaped configured root")
        adapter_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "name": tool_name,
            "capability": capability,
            "status": "proposal",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "objective": objective,
            "execution_policy": "scaffold_only",
            "promotion_steps": [
                "Review adapter.py and add tests.",
                "Move reviewed code into backend/src/agent_control/tools.",
                "Register the tool in backend/src/agent_control/tools/registry.py.",
            ],
        }
        files = {
            "manifest.json": json.dumps(manifest, indent=2) + "\n",
            "README.md": _readme(tool_name, objective, capability),
            "adapter.py": _adapter_py(tool_name),
            "test_adapter.py": _test_py(tool_name),
        }
        written: list[str] = []
        for relative_path, content in files.items():
            target = adapter_dir / relative_path
            target.write_text(content, encoding="utf-8")
            written.append(str(target))
        return {
            "adapter_dir": str(adapter_dir),
            "adapter_name": tool_name,
            "files": written,
            "cacheable": True,
            "execution_policy": "scaffold_only",
        }


def _adapter_name(request: ToolCallRequest) -> str:
    explicit = str(request.input.get("adapter_name") or "").strip()
    if explicit:
        return explicit
    objective = str(request.input.get("objective") or request.input.get("prompt") or request.task_id)
    words = re.findall(r"[A-Za-z0-9]+", objective.lower())
    useful = [word for word in words if word not in {"create", "build", "make", "adapter", "tool", "for", "the", "and"}]
    return "_".join(useful[:5]) or f"generated_{request.task_id}"


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "generated_adapter"


def _assessment(objective: str) -> str:
    lowered = objective.lower()
    if any(word in lowered for word in ("browser", "desktop", "system", "delete", "move files")):
        return "high_risk_requires_review"
    if any(word in lowered for word in ("api", "cli", "file", "workspace", "telegram")):
        return "moderate_complexity_cacheable"
    return "simple_scaffold_cacheable"


def _readme(name: str, objective: str, capability: str) -> str:
    return f"""# {name}

Generated adapter proposal.

Capability: `{capability}`

Objective:

```text
{objective or "No objective supplied."}
```

This directory is a cache for generated adapter work. The orchestrator does not import
or execute this code automatically. Promote it into `backend/src/agent_control/tools`
and `backend/tests` after review.
"""


def _adapter_py(name: str) -> str:
    class_name = "".join(part.capitalize() for part in re.findall(r"[A-Za-z0-9]+", name)) or "Generated"
    if class_name[0].isdigit():
        class_name = f"Generated{class_name}"
    return f'''from __future__ import annotations

from agent_control.schemas import ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus


class {class_name}Adapter:
    """Generated adapter proposal. Review before registering."""

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.FAILED,
            error_class=ErrorClass.ADAPTER_FAILED,
            error_message="generated adapter has not been implemented or reviewed",
        )
'''


def _test_py(name: str) -> str:
    return f'''from __future__ import annotations


def test_{_safe_segment(name).lower()}_adapter_needs_review() -> None:
    assert True
'''


def _terminal_output(operation: str, output: dict[str, Any]) -> dict[str, Any]:
    lines = [f"Adapter factory operation completed: {operation}"]
    if output.get("adapter_dir"):
        lines.append(f"Adapter cache: {output['adapter_dir']}")
    if output.get("adapter_name"):
        lines.append(f"Adapter: {output['adapter_name']}")
    if output.get("assessment"):
        lines.append(f"Assessment: {output['assessment']}")
    return {
        "instance_id": "local-worker",
        "terminal_id": "adapter-factory",
        "content": "\n".join(lines),
        "command_id": None,
        "is_final": True,
        "exit_code": 0,
        "source": "adapter_factory",
    }


def _failed(request: ToolCallRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        request_id=request.id,
        status=ToolResultStatus.FAILED,
        error_class=ErrorClass.ADAPTER_FAILED,
        error_message=message,
    )
