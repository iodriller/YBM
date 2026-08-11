from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

from agent_control.schemas import Capability, ToolCallRequest
from agent_control.tools.filesystem_manage import FilesystemManageAdapter

T = TypeVar("T", bound=BaseModel)


class FakeVisionProvider:
    async def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        return ""

    async def generate_multimodal_text(self, system_prompt: str, user_prompt: str, image_paths: list[str]) -> str:
        return "Visible text: OCR SAMPLE. The image looks like a small document screenshot."

    async def generate_structured(self, system_prompt: str, user_prompt: str, output_model: type[T], **_ignored_kwargs) -> T:
        raise AssertionError("structured generation is not used")


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
async def test_filesystem_manage_inspection_order_is_deterministic(tmp_path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    for name in ("resume.txt", "invoice_2026.txt", "notes.txt"):
        (root / name).write_text(name, encoding="utf-8")
    adapter = FilesystemManageAdapter([str(tmp_path)])

    result = await adapter.execute(_request(root, "inspect_folder"))

    assert [
        entry["relative_path"] for entry in result.output["entries"]
    ] == ["invoice_2026.txt", "notes.txt", "resume.txt"]


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
async def test_filesystem_manage_write_text_file_inside_allowed_root(tmp_path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    adapter = FilesystemManageAdapter([str(tmp_path)])

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_fs",
            tool_name="filesystem.manage",
            capability=Capability.FILESYSTEM_WRITE,
            input={
                "operation": "write_text_file",
                "path": str(root / "e2e-output.txt"),
                "content": "hello\n",
            },
        )
    )

    assert result.status.value == "succeeded"
    assert (root / "e2e-output.txt").read_text(encoding="utf-8") == "hello\n"
    assert result.output["path"] == str((root / "e2e-output.txt").resolve())
    assert result.output["changed_paths"] == [str((root / "e2e-output.txt").resolve())]


@pytest.mark.asyncio
async def test_filesystem_manage_search_by_name_includes_readable_content_preview(tmp_path) -> None:
    root = tmp_path / "desktop"
    root.mkdir()
    report = root / "resume-notes.txt"
    report.write_text("Oney resume notes include Python, orchestration, and local automation.", encoding="utf-8")
    adapter = FilesystemManageAdapter([str(tmp_path)])

    result = await adapter.execute(_request(root, "search", query="resume", include_content=True))

    assert result.status.value == "succeeded"
    assert result.output["entries"][0]["relative_path"] == "resume-notes.txt"
    assert "Python, orchestration" in result.output["entries"][0]["content_preview"]
    assert "resume-notes.txt" in result.output["terminal_output"][0]["content"]


@pytest.mark.asyncio
async def test_filesystem_manage_search_supports_natural_wildcard_filename_queries(tmp_path) -> None:
    root = tmp_path / "notes"
    root.mkdir()
    renamed = root / "career-master-current.txt"
    renamed.write_text("marker: RECOVERED", encoding="utf-8")
    adapter = FilesystemManageAdapter([str(tmp_path)])

    result = await adapter.execute(_request(root, "search", query="career*"))
    all_result = await adapter.execute(_request(root, "search", query="*"))

    assert [entry["path"] for entry in result.output["entries"]] == [str(renamed.resolve())]
    assert [entry["path"] for entry in all_result.output["entries"]] == [str(renamed.resolve())]


@pytest.mark.asyncio
async def test_filesystem_manage_read_file_returns_contents(tmp_path) -> None:
    root = tmp_path / "desktop"
    root.mkdir()
    report = root / "readme.txt"
    report.write_text("This file explains the desktop automation test fixture.", encoding="utf-8")
    adapter = FilesystemManageAdapter([str(tmp_path)])

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_fs",
            tool_name="filesystem.manage",
            capability=Capability.FILESYSTEM_WRITE,
            input={"operation": "read_file", "path": str(report), "max_chars": 1000},
        )
    )

    assert result.status.value == "succeeded"
    assert "desktop automation test fixture" in result.output["text"]
    assert "Content:" in result.output["terminal_output"][0]["content"]


@pytest.mark.asyncio
async def test_filesystem_manage_describe_folder_extracts_file_content_and_image_ocr(tmp_path) -> None:
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "project_notes.txt").write_text("Alpha project notes about desktop automation.", encoding="utf-8")
    (root / "budget.csv").write_text("name,amount\nhosting,25\n", encoding="utf-8")
    image = root / "ocr-note.png"
    try:
        from PIL import Image, ImageDraw

        canvas = Image.new("RGB", (240, 80), color="white")
        draw = ImageDraw.Draw(canvas)
        draw.text((12, 28), "OCR SAMPLE", fill="black")
        canvas.save(image)
    except Exception:
        image.write_bytes(b"not a real image")
    adapter = FilesystemManageAdapter([str(tmp_path)], provider=FakeVisionProvider())

    result = await adapter.execute(_request(root, "describe_folder", include_ocr=True))

    assert result.status.value == "succeeded"
    descriptions = {Path(item["path"]).name: item for item in result.output["file_descriptions"]}
    assert "desktop automation" in descriptions["project_notes.txt"]["content_preview"]
    assert "hosting" in descriptions["budget.csv"]["content_preview"]
    assert descriptions["ocr-note.png"]["ocr_status"] == "completed"
    assert "OCR SAMPLE" in descriptions["ocr-note.png"]["ocr_text"]
    assert "Described 3 file(s)" in result.output["summary"]


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


@pytest.mark.asyncio
async def test_filesystem_manage_resolves_desktop_alias_prefix(monkeypatch, tmp_path) -> None:
    fake_home = tmp_path / "home"
    desktop = fake_home / "Desktop"
    desktop.mkdir(parents=True)
    (desktop / "invoice.txt").write_text("invoice content", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    adapter = FilesystemManageAdapter([str(fake_home)])

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_fs",
            tool_name="filesystem.manage",
            capability=Capability.FILESYSTEM_WRITE,
            input={"operation": "search", "root": "desktop", "query": "invoice", "include_content": True},
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["entries"][0]["path"] == str((desktop / "invoice.txt").resolve())
