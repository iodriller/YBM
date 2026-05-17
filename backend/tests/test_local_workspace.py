from __future__ import annotations

import os
import signal

import pytest

from agent_control.config import WorkspaceAdapterConfig
from agent_control.schemas import Capability, ToolCallRequest, ToolResultStatus
from agent_control.tools.local_workspace import LocalWorkspaceWebAppAdapter


@pytest.mark.asyncio
async def test_local_workspace_web_app_creates_files_and_url(tmp_path) -> None:
    adapter = LocalWorkspaceWebAppAdapter(
        WorkspaceAdapterConfig(root_dir=str(tmp_path / "workspaces"), web_port_start=8890, open_browser=False)
    )
    request = ToolCallRequest(
        task_id="task_test",
        tool_name="workspace.web_app",
        capability=Capability.FILESYSTEM_WRITE,
        input={"objective": "Create a hello world web app and launch it"},
        timeout_seconds=30,
    )

    result = await adapter.execute(request)

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["url"].startswith("http://127.0.0.1:")
    workspace = tmp_path / "workspaces" / "task_test"
    assert (workspace / "index.html").exists()
    assert (workspace / "README.md").exists()

    os.kill(int(result.output["server_pid"]), signal.SIGTERM)
