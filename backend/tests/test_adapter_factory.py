from __future__ import annotations

import json

import pytest

from agent_control.config import AdapterFactoryConfig
from agent_control.schemas import Capability, ToolCallRequest, ToolResultStatus
from agent_control.tools.adapter_factory import AdapterFactoryAdapter


@pytest.mark.asyncio
async def test_adapter_factory_promotes_passing_adapter(tmp_path) -> None:
    root = tmp_path / "adapters"
    adapter_dir = root / "echo_tool"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "echo.tool",
                "capability": "llm.generate",
                "status": "proposal",
                "objective": "Echo text for tests.",
                "adapter_class": "EchoToolAdapter",
                "operations": ["echo"],
                "default_operation": "echo",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (adapter_dir / "adapter.py").write_text(
        """
from __future__ import annotations

from agent_control.schemas import ToolCallRequest, ToolCallResult, ToolResultStatus


class EchoToolAdapter:
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(
            request_id=request.id,
            status=ToolResultStatus.SUCCEEDED,
            output={"text": request.input.get("text", "ok")},
        )
""".lstrip(),
        encoding="utf-8",
    )
    (adapter_dir / "test_adapter.py").write_text(
        """
from __future__ import annotations

import asyncio

from adapter import EchoToolAdapter
from agent_control.schemas import Capability, ToolCallRequest, ToolResultStatus


def test_echo_tool_adapter_succeeds() -> None:
    result = asyncio.run(
        EchoToolAdapter().execute(
            ToolCallRequest(
                task_id="task",
                tool_name="echo.tool",
                capability=Capability.LLM_GENERATE,
                input={"text": "hello"},
            )
        )
    )
    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["text"] == "hello"
""".lstrip(),
        encoding="utf-8",
    )

    promoted = {}
    factory = AdapterFactoryAdapter(AdapterFactoryConfig(root_dir=str(root)))
    factory.set_promotion_callback(lambda definition, adapter: promoted.update(definition=definition, adapter=adapter))

    result = await factory.execute(
        ToolCallRequest(
            task_id="task_adapter",
            tool_name="adapter.factory",
            capability=Capability.FILESYSTEM_WRITE,
            input={"operation": "promote_after_approval", "adapter_dir": str(adapter_dir), "approved": True},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["promoted"] is True
    assert promoted["definition"].name == "echo.tool"
    dynamic_result = await promoted["adapter"].execute(
        ToolCallRequest(
            task_id="task_adapter",
            tool_name="echo.tool",
            capability=Capability.LLM_GENERATE,
            input={"operation": "echo", "text": "live"},
        )
    )
    assert dynamic_result.status == ToolResultStatus.SUCCEEDED
    assert dynamic_result.output["text"] == "live"
