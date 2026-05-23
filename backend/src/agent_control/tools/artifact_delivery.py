from __future__ import annotations

from mimetypes import guess_type
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from agent_control.schemas import Artifact, ArtifactType, ErrorClass, TaskRecord, ToolCallRequest, ToolCallResult, ToolResultStatus
from agent_control.storage.repositories import ArtifactRepository, TaskRepository


class TelegramFileClient(Protocol):
    async def send_photo_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict[str, Any]:
        ...

    async def send_document_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict[str, Any]:
        ...


class ArtifactDeliveryAdapter:
    def __init__(
        self,
        artifacts: ArtifactRepository,
        tasks: TaskRepository,
        *,
        telegram_client: TelegramFileClient | None = None,
        allowed_roots: list[str] | None = None,
        recent_fallback_enabled: bool = False,
    ) -> None:
        self.artifacts = artifacts
        self.tasks = tasks
        self.telegram_client = telegram_client
        self.allowed_roots = [Path(root).expanduser().resolve() for root in (allowed_roots or [])]
        self.recent_fallback_enabled = recent_fallback_enabled

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        operation = str(request.input.get("operation") or "send_latest")
        try:
            if operation == "list_artifacts":
                output = self._list_artifacts(request)
            elif operation in {"send_file", "send_latest", "send_screenshot"}:
                output = await self._send(request, operation)
            else:
                return _failed(request, f"unsupported artifact delivery operation: {operation}")
        except Exception as exc:
            return _failed(request, str(exc))

        output["operation"] = operation
        output["terminal_output"] = [
            {
                "content": output.get("summary") or f"artifact.deliver {operation} completed.",
                "is_final": True,
                "exit_code": 0,
            }
        ]
        return ToolCallResult(request_id=request.id, status=ToolResultStatus.SUCCEEDED, output=output)

    def _list_artifacts(self, request: ToolCallRequest) -> dict[str, Any]:
        artifacts = self.artifacts.list_for_task(request.task_id)
        return {
            "delivered": False,
            "artifact_ids": [artifact.id for artifact in artifacts],
            "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
            "summary": f"Found {len(artifacts)} artifact(s) for task {request.task_id}.",
        }

    async def _send(self, request: ToolCallRequest, operation: str) -> dict[str, Any]:
        task = self.tasks.get(request.task_id)
        if task is None:
            raise ValueError(f"task not found: {request.task_id}")

        artifact, path = self._resolve_artifact_path(request, task, operation)
        if path is None and operation == "send_latest" and self.recent_fallback_enabled:
            artifact, path = self._resolve_recent_artifact_path(request)
        if path is None and operation == "send_latest":
            artifact, path = self._materialize_latest_text(request, task)
        if path is None:
            raise ValueError("no deliverable artifact or file path was found")
        if not path.exists() or not path.is_file():
            raise ValueError(f"deliverable file does not exist: {path}")

        if artifact is None:
            artifact = self.artifacts.create(
                Artifact(
                    task_id=request.task_id,
                    type=_artifact_type_for_path(path, operation),
                    uri=str(path),
                    content_preview=str(request.input.get("caption") or path.name),
                    metadata={"source": "artifact.deliver", "mime_type": request.input.get("mime_type") or guess_type(path.name)[0]},
                )
            )

        chat_id = str(request.input.get("chat_id") or _task_chat_id(task) or "")
        if not chat_id:
            raise ValueError("telegram chat_id is required and could not be inferred from the task")
        if self.telegram_client is None:
            raise ValueError("Telegram delivery client is not configured")

        caption = _trim(str(request.input.get("caption") or artifact.content_preview or path.name), 900)
        if operation == "send_screenshot" or artifact.type == ArtifactType.SCREENSHOT:
            telegram_result = await self.telegram_client.send_photo_file(chat_id, str(path), caption)
            method = "telegram.sendPhoto"
        else:
            telegram_result = await self.telegram_client.send_document_file(chat_id, str(path), caption)
            method = "telegram.sendDocument"

        return {
            "delivered": True,
            "delivery_method": method,
            "artifact_id": artifact.id,
            "artifact_ids": [artifact.id],
            "path": str(path),
            "chat_id": chat_id,
            "summary": f"Delivered {path.name} to Telegram chat {chat_id}.",
            "telegram_result": telegram_result,
        }

    def _resolve_artifact_path(
        self,
        request: ToolCallRequest,
        task: TaskRecord,
        operation: str,
    ) -> tuple[Artifact | None, Path | None]:
        artifact_id = request.input.get("artifact_id")
        if artifact_id:
            artifact = self.artifacts.get(str(artifact_id))
            if artifact is None:
                raise ValueError(f"artifact not found: {artifact_id}")
            if artifact.task_id not in {None, request.task_id}:
                raise ValueError(f"artifact {artifact_id} is not linked to task {request.task_id}")
            return artifact, _path_from_uri(artifact.uri)

        raw_path = request.input.get("path")
        if raw_path:
            return None, self._safe_path(str(raw_path))

        if operation == "send_screenshot":
            for value in _task_screenshot_values(task):
                path = _path_from_uri(value)
                if path and path.exists() and path.is_file():
                    return _artifact_for_path(self.artifacts.list_for_task(request.task_id), path), path

        artifact_type = str(request.input.get("artifact_type") or "").strip()
        artifacts = self.artifacts.list_for_task(request.task_id)
        if artifact_type:
            artifacts = [item for item in artifacts if item.type.value == artifact_type]
        if operation == "send_screenshot":
            artifacts = [item for item in artifacts if item.type == ArtifactType.SCREENSHOT]
        for artifact in reversed(artifacts):
            path = _path_from_uri(artifact.uri)
            if path and path.exists() and path.is_file():
                return artifact, path
        return None, None

    def _resolve_recent_artifact_path(self, request: ToolCallRequest) -> tuple[Artifact | None, Path | None]:
        list_recent = getattr(self.artifacts, "list_recent", None)
        if not callable(list_recent):
            return None, None
        artifact_type = str(request.input.get("artifact_type") or "").strip() or None
        for artifact in list_recent(limit=25, artifact_type=artifact_type):
            path = _path_from_uri(artifact.uri)
            if path and path.exists() and path.is_file():
                return artifact, path
        if artifact_type:
            for artifact in list_recent(limit=25):
                path = _path_from_uri(artifact.uri)
                if path and path.exists() and path.is_file():
                    return artifact, path
        return None, None

    def _materialize_latest_text(self, request: ToolCallRequest, task: TaskRecord) -> tuple[Artifact | None, Path | None]:
        text = _latest_task_text(task)
        if not text:
            return None, None
        root = self.allowed_roots[0] if self.allowed_roots else Path(".agent_control/artifacts").resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{request.task_id}_latest_output.txt"
        path.write_text(text, encoding="utf-8")
        artifact = self.artifacts.create(
            Artifact(
                task_id=request.task_id,
                type=ArtifactType.TEXT_LOG,
                uri=str(path),
                content_preview=_trim(text, 500),
                metadata={"source": "artifact.deliver", "operation": "send_latest", "materialized": True},
            )
        )
        return artifact, path

    def _safe_path(self, value: str) -> Path:
        path = _path_from_uri(value)
        if path is None:
            raise ValueError(f"invalid file path: {value}")
        path = path.expanduser().resolve()
        if self.allowed_roots and not any(root == path or root in path.parents for root in self.allowed_roots):
            raise ValueError(f"path is outside configured delivery roots: {path}")
        return path


def _task_chat_id(task: TaskRecord) -> str | None:
    value = task.metadata.get("source_chat_id")
    if value:
        return str(value)
    if task.conversation_id and task.conversation_id.startswith("conv_telegram_"):
        return task.conversation_id.removeprefix("conv_telegram_")
    return None


def _task_screenshot_values(task: TaskRecord) -> list[object]:
    result = task.metadata.get("last_tool_result")
    output = result.get("output") if isinstance(result, dict) else {}
    if not isinstance(output, dict):
        output = {}
    return [
        task.metadata.get("screenshot_path"),
        output.get("screenshot_path"),
        task.metadata.get("screenshot_uri"),
        output.get("screenshot_uri"),
    ]


def _path_from_uri(value: object) -> Path | None:
    if not value:
        return None
    text = str(value)
    alias_path = _alias_path(text)
    if alias_path is not None:
        return alias_path.expanduser().resolve()
    if text.startswith("file:///"):
        parsed = urlparse(text)
        raw_path = unquote(parsed.path)
        if raw_path.startswith("/") and len(raw_path) > 3 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        return Path(raw_path).expanduser().resolve()
    return Path(text).expanduser().resolve()


def _alias_path(value: str) -> Path | None:
    normalized = value.strip().strip("\"'").replace("/", "\\")
    lowered = normalized.lower()
    home = Path.home()
    aliases = {
        "desktop": home / "Desktop",
        "%desktop%": home / "Desktop",
        "documents": home / "Documents",
        "my documents": home / "Documents",
        "%documents%": home / "Documents",
        "downloads": home / "Downloads",
        "%downloads%": home / "Downloads",
        "home": home,
        "user": home,
        "my directory": home,
        "my folder": home,
    }
    if lowered in aliases:
        return aliases[lowered]
    for prefix, root in (
        ("desktop\\", home / "Desktop"),
        ("documents\\", home / "Documents"),
        ("my documents\\", home / "Documents"),
        ("downloads\\", home / "Downloads"),
        ("home\\", home),
        ("my directory\\", home),
    ):
        if lowered.startswith(prefix):
            return root / normalized[len(prefix) :]
    return None


def _latest_task_text(task: TaskRecord) -> str | None:
    result = task.metadata.get("last_tool_result")
    output = result.get("output") if isinstance(result, dict) else {}
    if isinstance(output, dict):
        terminal_output = output.get("terminal_output")
        if isinstance(terminal_output, list):
            for item in reversed(terminal_output):
                if isinstance(item, dict) and item.get("content"):
                    return str(item["content"])
        for key in ("summary", "final_summary", "response", "text", "stdout"):
            if output.get(key):
                return str(output[key])
    if task.metadata.get("last_tool_output_text"):
        return str(task.metadata["last_tool_output_text"])
    return None


def _artifact_for_path(artifacts: list[Artifact], path: Path) -> Artifact | None:
    resolved = path.resolve()
    for artifact in artifacts:
        artifact_path = _path_from_uri(artifact.uri)
        if artifact_path and artifact_path.resolve() == resolved:
            return artifact
    return None


def _artifact_type_for_path(path: Path, operation: str) -> ArtifactType:
    if operation == "send_screenshot" or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        return ArtifactType.SCREENSHOT
    return ArtifactType.DOCUMENT


def _trim(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[: limit - 3]}..."


def _failed(request: ToolCallRequest, message: str) -> ToolCallResult:
    return ToolCallResult(
        request_id=request.id,
        status=ToolResultStatus.FAILED,
        error_class=ErrorClass.ADAPTER_FAILED,
        error_message=message,
    )
