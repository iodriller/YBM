from __future__ import annotations

import sys

import pytest

from agent_control.config import CodingAssistantAdapterConfig
from agent_control.schemas import Capability, ErrorClass, ToolCallRequest, ToolResultStatus
from agent_control.tools import GenericTerminalAgentAdapter


@pytest.mark.asyncio
async def test_generic_terminal_agent_runs_command() -> None:
    adapter = GenericTerminalAgentAdapter(
        CodingAssistantAdapterConfig(
            enabled=True,
            command_template=[sys.executable, "-c", "print('hello {prompt}')"],
        )
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_1",
            tool_name="coding_assistant",
            capability=Capability.TERMINAL_RUN,
            input={"prompt": "world"},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert "hello world" in result.output["stdout"]


@pytest.mark.asyncio
async def test_generic_terminal_agent_detects_usage_limit() -> None:
    adapter = GenericTerminalAgentAdapter(
        CodingAssistantAdapterConfig(
            enabled=True,
            command_template=[sys.executable, "-c", "print('usage limit reached')"],
        )
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_1",
            tool_name="coding_assistant",
            capability=Capability.TERMINAL_RUN,
        )
    )

    assert result.status == ToolResultStatus.RATE_LIMITED
    assert result.error_class == ErrorClass.USAGE_LIMITED
