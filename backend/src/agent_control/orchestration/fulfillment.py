from __future__ import annotations

from typing import Any

from agent_control.schemas import TaskRecord


def expected_fulfillment(objective: str) -> dict[str, bool]:
    lowered = objective.lower()
    visible_action = any(marker in lowered for marker in ("launch", "start", "serve", "open", "preview", "show me", "url"))
    app_request = any(marker in lowered for marker in ("app", "application", "website", "webpage", "web page", "html"))
    if visible_action and app_request:
        return {"preview_url": True, "workspace_dir": True}
    if any(marker in lowered for marker in ("code", "script", "app", "project", "file")):
        return {"workspace_dir": True}
    return {}


def fulfillment_gap(task: TaskRecord) -> str | None:
    expected = expected_fulfillment(task.objective)
    if expected.get("preview_url") and not _value(task, "preview_url", "url"):
        return "expected_preview_url_missing"
    return None


def _value(task: TaskRecord, metadata_key: str, output_key: str) -> Any:
    if task.metadata.get(metadata_key):
        return task.metadata[metadata_key]
    result = task.metadata.get("last_tool_result")
    if not isinstance(result, dict):
        return None
    output = result.get("output")
    if not isinstance(output, dict):
        return None
    return output.get(output_key)
