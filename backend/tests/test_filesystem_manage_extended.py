from __future__ import annotations

import pytest

from agent_control.schemas import Capability, ToolCallRequest
from agent_control.tools.filesystem_manage import FilesystemManageAdapter


@pytest.mark.asyncio
async def test_filesystem_find_by_description_prefers_matching_names(tmp_path) -> None:
    (tmp_path / "resume_final.pdf").write_text("resume", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes", encoding="utf-8")
    adapter = FilesystemManageAdapter([str(tmp_path)])

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_files",
            tool_name="filesystem.manage",
            capability=Capability.FILESYSTEM_WRITE,
            input={"operation": "find_by_description", "root": str(tmp_path), "description": "resume pdf"},
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["entries"][0]["relative_path"] == "resume_final.pdf"


@pytest.mark.asyncio
async def test_filesystem_collect_folder_snapshot_matches_inspection(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    adapter = FilesystemManageAdapter([str(tmp_path)])

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_files",
            tool_name="filesystem.manage",
            capability=Capability.FILESYSTEM_WRITE,
            input={"operation": "collect_folder_snapshot", "root": str(tmp_path)},
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["entries"][0]["relative_path"] == "a.txt"
