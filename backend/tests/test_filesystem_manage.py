from __future__ import annotations

from pathlib import Path

import pytest

from agent_control.schemas import Capability, ToolCallRequest
from agent_control.tools.filesystem_manage import FilesystemManageAdapter


def _request(root: Path, operation: str, **payload):
    return ToolCallRequest(
        task_id="task_fs",
        tool_name="filesystem.manage",
        capability=Capability.FILESYSTEM_WRITE,
        input={"operation": operation, "root": str(root), **payload},
    )


@pytest.mark.asyncio
async def test_filesystem_manage_inspect_search_plan_and_apply(tmp_path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    note = root / "notes.txt"
    image = root / "photo.png"
    note.write_text("resume draft", encoding="utf-8")
    image.write_bytes(b"fake")
    adapter = FilesystemManageAdapter([str(tmp_path)])

    inspect_result = await adapter.execute(_request(root, "inspect_folder"))
    search_result = await adapter.execute(_request(root, "search", query="resume", include_content=True))
    plan_result = await adapter.execute(_request(root, "organize_plan", strategy="by_type"))
    apply_result = await adapter.execute(
        _request(root, "apply_manifest", manifest=plan_result.output["manifest"], dry_run=False)
    )

    assert inspect_result.status.value == "succeeded"
    assert len(inspect_result.output["entries"]) == 2
    assert search_result.output["entries"][0]["path"] == str(note.resolve())
    assert len(plan_result.output["manifest"]) == 2
    assert apply_result.status.value == "succeeded"
    assert "Moved 2 file(s)" in apply_result.output["summary"]
    assert "changed 2 path(s)" in apply_result.output["summary"]
    assert sorted(Path(path).parent.name for path in apply_result.output["changed_paths"]) == ["documents", "images"]
    assert not note.exists()
    assert (root / "documents" / "notes.txt").exists()


@pytest.mark.asyncio
async def test_filesystem_manage_rename_plan_and_apply(tmp_path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    source = root / "untitled.txt"
    source.write_text("Quarterly invoice notes for Alpha project", encoding="utf-8")
    adapter = FilesystemManageAdapter([str(tmp_path)])

    plan_result = await adapter.execute(_request(root, "rename_plan"))
    apply_result = await adapter.execute(
        _request(root, "apply_manifest", manifest=plan_result.output["manifest"], dry_run=False)
    )

    assert plan_result.status.value == "succeeded"
    assert plan_result.output["rename_manifest"][0]["before"] == "untitled.txt"
    assert plan_result.output["rename_manifest"][0]["after"].endswith(".txt")
    assert apply_result.status.value == "succeeded"
    assert "Renamed 1 file(s)" in apply_result.output["summary"]
    assert apply_result.output["rename_manifest"][0]["before"] == "untitled.txt"
    assert not source.exists()


@pytest.mark.asyncio
async def test_filesystem_manage_rejects_path_escape(tmp_path) -> None:
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    adapter = FilesystemManageAdapter([str(root)])

    result = await adapter.execute(_request(outside, "inspect_folder"))

    assert result.status.value == "failed"
    assert "outside allowed roots" in (result.error_message or "")
