from __future__ import annotations

import os
import signal
import subprocess

import pytest

from agent_control.config import AdapterFactoryConfig, WorkspaceAdapterConfig
from agent_control.schemas import Capability, ToolCallRequest, ToolResultStatus
from agent_control.tools.adapter_factory import AdapterFactoryAdapter
from agent_control.tools.local_workspace import LocalWorkspaceAdapter, LocalWorkspaceWebAppAdapter


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

    _stop_process(int(result.output["server_pid"]))


@pytest.mark.asyncio
async def test_local_workspace_prepare_and_write_files(tmp_path) -> None:
    adapter = LocalWorkspaceAdapter(
        WorkspaceAdapterConfig(root_dir=str(tmp_path / "workspaces"), web_port_start=8890, open_browser=False)
    )
    request = ToolCallRequest(
        task_id="task_files",
        tool_name="workspace.manage",
        capability=Capability.FILESYSTEM_WRITE,
        input={
            "operation": "write_files",
            "objective": "Create project files",
            "files": [{"path": "src/app.py", "content": "print('hello')\n"}],
        },
        timeout_seconds=30,
    )

    result = await adapter.execute(request)

    workspace = tmp_path / "workspaces" / "task_files"
    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["workspace_dir"] == str(workspace)
    assert (workspace / "TASK.md").exists()
    assert (workspace / "src" / "app.py").read_text(encoding="utf-8") == "print('hello')\n"


@pytest.mark.asyncio
async def test_local_workspace_materializes_static_app_from_assistant_output(tmp_path) -> None:
    adapter = LocalWorkspaceAdapter(
        WorkspaceAdapterConfig(root_dir=str(tmp_path / "workspaces"), web_port_start=8890, open_browser=False)
    )
    request = ToolCallRequest(
        task_id="task_materialize",
        tool_name="workspace.manage",
        capability=Capability.FILESYSTEM_WRITE,
        input={
            "operation": "materialize_static_app",
            "objective": "Create a modern app about ferrets",
            "source_text": """```html filename=index.html
<!doctype html><html><body><h1>Ferret Studio</h1></body></html>
```
```css filename=styles.css
body { font-family: sans-serif; }
```""",
        },
        timeout_seconds=30,
    )

    result = await adapter.execute(request)

    workspace = tmp_path / "workspaces" / "task_materialize"
    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["materialized_from"] == "assistant_output"
    assert "Ferret Studio" in (workspace / "index.html").read_text(encoding="utf-8")
    assert (workspace / "styles.css").exists()


@pytest.mark.asyncio
async def test_adapter_factory_scaffolds_cached_adapter(tmp_path) -> None:
    adapter = AdapterFactoryAdapter(AdapterFactoryConfig(root_dir=str(tmp_path / "adapters")))
    request = ToolCallRequest(
        task_id="task_adapter",
        tool_name="adapter.factory",
        capability=Capability.FILESYSTEM_WRITE,
        input={
            "operation": "scaffold",
            "adapter_name": "browser_bookmarks",
            "objective": "Create an adapter for organizing browser bookmarks",
        },
        timeout_seconds=30,
    )

    result = await adapter.execute(request)

    adapter_dir = tmp_path / "adapters" / "browser_bookmarks"
    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["execution_policy"] == "scaffold_only"
    assert (adapter_dir / "manifest.json").exists()
    assert (adapter_dir / "adapter.py").exists()


def _stop_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)
