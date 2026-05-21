from __future__ import annotations

from pathlib import Path
import shutil
import os
from typing import Any

from agent_control.schemas import ErrorClass, ToolCallRequest, ToolCallResult, ToolResultStatus


class FilesystemManageAdapter:
    """Scoped filesystem inspection, search, and organization tool."""

    def __init__(self, allowed_roots: list[str]) -> None:
        self.allowed_roots = [_resolve_root(root) for root in allowed_roots]

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        operation = str(request.input.get("operation") or "inspect_folder")
        try:
            if operation == "inspect_folder":
                output = self._inspect_folder(request)
            elif operation == "search":
                output = self._search(request)
            elif operation == "resolve_desktop_item":
                output = self._resolve_desktop_item(request)
            elif operation == "find_by_description":
                output = self._find_by_description(request)
            elif operation == "open_file":
                output = self._open_file(request)
            elif operation == "collect_folder_snapshot":
                output = self._collect_folder_snapshot(request)
            elif operation == "organize_plan":
                output = self._organize_plan(request)
            elif operation == "apply_manifest":
                output = self._apply_manifest(request)
            else:
                return _failed(request, f"unsupported filesystem operation: {operation}")
        except Exception as exc:
            return _failed(request, f"filesystem operation failed: {exc}")

        output["operation"] = operation
        output["terminal_output"] = [_terminal_output(operation, output)]
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

    def _inspect_folder(self, request: ToolCallRequest) -> dict[str, Any]:
        root = self._safe_path(str(request.input["root"]))
        if not root.exists() or not root.is_dir():
            raise ValueError(f"folder does not exist: {root}")
        max_depth = int(request.input.get("max_depth") or 2)
        max_entries = int(request.input.get("max_entries") or 200)
        entries = []
        for path in _walk_limited(root, max_depth=max_depth):
            if path == root:
                continue
            entries.append(_entry(root, path))
            if len(entries) >= max_entries:
                break
        return {
            "root": str(root),
            "entries": entries,
            "summary": f"Found {len(entries)} item(s) under {root}.",
        }

    def _search(self, request: ToolCallRequest) -> dict[str, Any]:
        root = self._safe_path(str(request.input["root"]))
        query = str(request.input["query"]).lower()
        include_content = bool(request.input.get("include_content", False))
        max_results = int(request.input.get("max_results") or 100)
        results = []
        for path in _walk_limited(root, max_depth=20):
            if path == root:
                continue
            matched = query in path.name.lower()
            content_preview = None
            if include_content and path.is_file() and _is_text_file(path):
                text = path.read_text(encoding="utf-8", errors="ignore")
                index = text.lower().find(query)
                matched = matched or index >= 0
                if index >= 0:
                    start = max(0, index - 120)
                    content_preview = text[start : index + 240].strip()
            if matched:
                item = _entry(root, path)
                if content_preview:
                    item["content_preview"] = content_preview
                results.append(item)
                if len(results) >= max_results:
                    break
        return {
            "root": str(root),
            "entries": results,
            "summary": f"Found {len(results)} result(s) for {request.input['query']!r} under {root}.",
        }

    def _resolve_desktop_item(self, request: ToolCallRequest) -> dict[str, Any]:
        query = str(request.input.get("query") or request.input.get("name") or "").lower().strip()
        item_type = str(request.input.get("item_type") or "any")
        roots = [root for root in self._candidate_roots("desktop") if root.exists()]
        entries: list[dict[str, Any]] = []
        for root in roots:
            for path in root.iterdir():
                if item_type == "file" and not path.is_file():
                    continue
                if item_type == "folder" and not path.is_dir():
                    continue
                if query and query not in path.name.lower():
                    continue
                entries.append(_entry(root, path))
        return {
            "root": str(roots[0]) if roots else None,
            "entries": entries,
            "summary": f"Resolved {len(entries)} desktop item(s) matching {query or '*'}."
        }

    def _find_by_description(self, request: ToolCallRequest) -> dict[str, Any]:
        root_value = request.input.get("root")
        root = self._safe_path(str(root_value)) if root_value else self._first_existing_root()
        description = str(request.input["description"]).lower()
        max_results = int(request.input.get("max_results") or 20)
        terms = [term for term in description.replace(".", " ").replace("_", " ").split() if len(term) >= 3]
        entries = []
        for path in _walk_limited(root, max_depth=20):
            if path == root:
                continue
            name = path.name.lower()
            score = sum(1 for term in terms if term in name)
            if score <= 0 and any(term in description for term in (path.suffix.lower().lstrip("."), _type_bucket(path))):
                score = 1
            if score > 0:
                item = _entry(root, path)
                item["score"] = score
                entries.append(item)
        entries.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("relative_path") or "")))
        entries = entries[:max_results]
        return {
            "root": str(root),
            "entries": entries,
            "summary": f"Found {len(entries)} item(s) matching the description under {root}.",
        }

    def _open_file(self, request: ToolCallRequest) -> dict[str, Any]:
        path = self._safe_path(str(request.input["path"]))
        if not path.exists():
            raise ValueError(f"path does not exist: {path}")
        os.startfile(str(path))  # type: ignore[attr-defined]
        return {
            "root": str(path.parent),
            "entries": [_entry(path.parent, path)],
            "changed_paths": [str(path)],
            "summary": f"Opened {path}.",
        }

    def _collect_folder_snapshot(self, request: ToolCallRequest) -> dict[str, Any]:
        return self._inspect_folder(request)

    def _organize_plan(self, request: ToolCallRequest) -> dict[str, Any]:
        root = self._safe_path(str(request.input["root"]))
        if not root.exists() or not root.is_dir():
            raise ValueError(f"folder does not exist: {root}")
        strategy = str(request.input.get("strategy") or "by_type")
        recursive = bool(request.input.get("recursive", False))
        max_files = int(request.input.get("max_files") or 1000)
        manifest = []
        iterator = root.rglob("*") if recursive else root.iterdir()
        for path in iterator:
            if len(manifest) >= max_files:
                break
            if not path.is_file():
                continue
            bucket = _extension_bucket(path) if strategy == "by_extension" else _type_bucket(path)
            destination = _dedupe_destination(root / bucket / path.name)
            if destination.resolve() == path.resolve():
                continue
            manifest.append(
                {
                    "operation": "move",
                    "source": str(path.resolve()),
                    "destination": str(destination.resolve()),
                    "reason": f"Group by {strategy}: {bucket}",
                }
            )
        return {
            "root": str(root),
            "manifest": manifest,
            "dry_run": True,
            "summary": f"Prepared {len(manifest)} file organization action(s) for {root}.",
        }

    def _apply_manifest(self, request: ToolCallRequest) -> dict[str, Any]:
        root = self._safe_path(str(request.input["root"]))
        manifest = request.input.get("manifest") or []
        dry_run = bool(request.input.get("dry_run", False))
        overwrite = bool(request.input.get("overwrite", False))
        changed_paths = []
        normalized_manifest = []
        for item in manifest:
            source = self._safe_path(str(item["source"]))
            destination = self._safe_path(str(item["destination"]))
            if root not in source.parents and root != source:
                raise ValueError(f"source is outside requested root: {source}")
            if root not in destination.parents and root != destination:
                raise ValueError(f"destination is outside requested root: {destination}")
            operation = str(item.get("operation") or "move")
            normalized_manifest.append({**item, "source": str(source), "destination": str(destination)})
            if dry_run:
                continue
            if not source.exists() or not source.is_file():
                raise ValueError(f"source file does not exist: {source}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and not overwrite:
                destination = _dedupe_destination(destination)
            if operation == "copy":
                shutil.copy2(source, destination)
            elif operation == "move":
                shutil.move(str(source), str(destination))
            else:
                raise ValueError(f"unsupported manifest operation: {operation}")
            changed_paths.append(str(destination))
        return {
            "root": str(root),
            "manifest": normalized_manifest,
            "changed_paths": changed_paths,
            "dry_run": dry_run,
            "summary": (
                f"Validated {len(normalized_manifest)} file organization action(s)."
                if dry_run
                else f"Applied {len(changed_paths)} file organization action(s)."
            ),
        }

    def _safe_path(self, value: str) -> Path:
        if not self.allowed_roots:
            raise ValueError("no allowed filesystem roots are configured")
        path = self._alias_path(value).expanduser().resolve()
        if not any(root == path or root in path.parents for root in self.allowed_roots):
            allowed = ", ".join(str(root) for root in self.allowed_roots)
            raise ValueError(f"path is outside allowed roots: {path}; allowed roots: {allowed}")
        return path

    def _candidate_roots(self, alias: str) -> list[Path]:
        alias_path = self._alias_path(alias).resolve()
        return [root for root in [alias_path, *self.allowed_roots] if root == alias_path or alias_path in root.parents or root in alias_path.parents]

    def _first_existing_root(self) -> Path:
        for root in self.allowed_roots:
            if root.exists():
                return root
        raise ValueError("none of the allowed roots exists")

    @staticmethod
    def _alias_path(value: str) -> Path:
        lowered = value.strip().lower()
        home = Path.home()
        if lowered in {"desktop", "%desktop%"}:
            return home / "Desktop"
        if lowered in {"documents", "my documents", "%documents%"}:
            return home / "Documents"
        if lowered in {"downloads", "%downloads%"}:
            return home / "Downloads"
        return Path(value)


def _resolve_root(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _walk_limited(root: Path, *, max_depth: int):
    root_depth = len(root.parts)
    yield root
    for path in root.rglob("*"):
        if len(path.parts) - root_depth <= max_depth:
            yield path


def _entry(root: Path, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "relative_path": str(path.resolve().relative_to(root.resolve())),
        "is_dir": path.is_dir(),
        "size_bytes": stat.st_size if path.is_file() else None,
        "modified_at": stat.st_mtime,
    }


def _type_bucket(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}:
        return "images"
    if ext in {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf"}:
        return "documents"
    if ext in {".xls", ".xlsx", ".csv"}:
        return "spreadsheets"
    if ext in {".ppt", ".pptx"}:
        return "presentations"
    if ext in {".zip", ".rar", ".7z", ".tar", ".gz"}:
        return "archives"
    if ext in {".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml"}:
        return "code"
    if ext in {".mp3", ".wav", ".flac", ".m4a"}:
        return "audio"
    if ext in {".mp4", ".mov", ".avi", ".mkv"}:
        return "video"
    return "other"


def _extension_bucket(path: Path) -> str:
    return path.suffix.lower().lstrip(".") or "no_extension"


def _dedupe_destination(path: Path) -> Path:
    candidate = path
    index = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        index += 1
    return candidate


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in {".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml", ".csv"}


def _terminal_output(operation: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": output.get("summary") or f"filesystem.manage {operation} completed.",
        "is_final": True,
        "exit_code": 0,
    }


def _failed(request: ToolCallRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        request_id=request.id,
        status=ToolResultStatus.FAILED,
        error_class=ErrorClass.ADAPTER_FAILED,
        error_message=message,
    )
