from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from agent_control.schemas import PlanPostcondition, PostconditionType, TaskRecord


@dataclass(frozen=True)
class FulfillmentValidation:
    expected: tuple[PlanPostcondition, ...]
    missing: tuple[PostconditionType, ...]

    @property
    def ok(self) -> bool:
        return not self.missing

    @property
    def first_gap(self) -> str | None:
        if not self.missing:
            return None
        return _gap_reason(self.missing[0])


def expected_postconditions(task: TaskRecord) -> tuple[PlanPostcondition, ...]:
    """Objective-text inference is the only source of expected postconditions.

    There used to be a plan-derived path here that took priority (the LLM's own
    declared `plan.postconditions`, then tool-name-derived rules). Both are gone
    with the plan-once execution path (docs/HISTORY.md P3) - nothing creates a
    PlanModel anymore, so `plan` was always None and those branches were
    unreachable. See docs/HISTORY.md §1.1.
    """
    return tuple(_dedupe(_postconditions_from_objective(task.objective)))


def validate_fulfillment(task: TaskRecord) -> FulfillmentValidation:
    expected = expected_postconditions(task)
    missing = tuple(
        item.type
        for item in expected
        if item.required and not _postcondition_satisfied(task, item.type)
    )
    return FulfillmentValidation(expected=expected, missing=missing)


_EMBEDDED_PATH = re.compile(r"[a-z]:\\\S+|\\\\\S+", re.IGNORECASE)


def _strip_embedded_paths(text: str) -> str:
    """Objectives routinely embed a literal filesystem path ("look in the
    folder C:\\Users\\...\\search_results"). Left in, a path segment that
    happens to contain a trigger word (a folder named "...search", "...app",
    "...schedule") produces a false-positive expected postcondition that
    nothing in the actual task run ever satisfies - this is the sole
    fulfillment signal the Operator loop has, so a false positive means a task
    that genuinely finished loops on a gap it can never close. Strip anything
    path-shaped before keyword matching, not just one trigger word - the
    failure mode is the pattern (words embedded in a path getting matched as
    intent), not any single keyword.
    """
    return _EMBEDDED_PATH.sub(" ", text)


def _postconditions_from_objective(objective: str) -> list[PlanPostcondition]:
    lowered = _strip_embedded_paths(objective).lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    visible_action = bool(words & {"launch", "start", "serve", "open", "preview"}) or "show me" in lowered or "url" in words
    app_request = bool(words & {"app", "application", "website", "webpage", "html"}) or "web page" in lowered
    create_action = bool(words & {"create", "build", "write", "make", "add", "implement", "generate", "update", "edit"})
    workspace_subject = bool(words & {"code", "script", "app", "application", "project", "file", "files", "website", "webpage", "html"})
    expected: list[PlanPostcondition] = []
    if visible_action and app_request:
        expected.extend(
            [
                PlanPostcondition(
                    type=PostconditionType.PREVIEW_URL,
                    description="A local preview URL is reported.",
                ),
                PlanPostcondition(
                    type=PostconditionType.WORKSPACE_DIR,
                    description="A task workspace directory is reported.",
                ),
            ]
        )
    elif create_action and workspace_subject:
        expected.append(
            PlanPostcondition(
                type=PostconditionType.WORKSPACE_DIR,
                description="A task workspace directory is reported.",
            )
        )

    has_adapter_word = bool(words & {"adapter", "tool", "capability", "connector"})
    if has_adapter_word and create_action:
        expected.append(
            PlanPostcondition(
                type=PostconditionType.ADAPTER_PROPOSAL,
                description="A generated adapter proposal directory is reported.",
            )
        )
    if bool(words & {"browser", "browse", "search", "website", "page"}) and bool(
        words & {"open", "search", "visit", "look", "find"}
    ):
        expected.append(
            PlanPostcondition(
                type=PostconditionType.BROWSER_STATE,
                description="Browser state or page observation is reported.",
            )
        )
    if (bool(words & {"screenshot", "screen", "desktop"}) or "what do you see" in lowered) and not _desktop_file_listing_request(
        lowered
    ):
        expected.append(
            PlanPostcondition(
                type=PostconditionType.DESKTOP_OBSERVATION,
                description="A desktop observation or screenshot is reported.",
            )
        )
    if bool(words & {"organize", "move", "rename", "sort"}) and bool(
        words & {"file", "files", "folder", "folders", "directory", "directories"}
    ):
        expected.append(
            PlanPostcondition(
                type=PostconditionType.FILE_ORGANIZATION,
                description="Changed, moved, or organized file paths are reported.",
            )
        )
    if ("pull request" in lowered or " pr " in f" {lowered} " or "github" in words) and bool(
        words & {"create", "open", "make", "submit"}
    ):
        expected.append(
            PlanPostcondition(
                type=PostconditionType.GITHUB_PR,
                description="A GitHub pull request URL or number is reported.",
            )
        )
    if bool(words & {"run", "execute", "launch"}) and bool(words & {"command", "terminal", "script"}):
        expected.append(
            PlanPostcondition(
                type=PostconditionType.EXTERNAL_COMMAND,
                description="External command completion is reported.",
            )
        )
    if (
        bool(words & {"schedule", "scheduled", "recurring", "daily", "weekly"})
        or bool(re.search(r"\bevery\s+\d+\s+(?:minute|minutes|hour|hours|day|days|week|weeks)\b", lowered))
    ) and bool(words & {"create", "add", "set", "run", "check", "search"}):
        expected.append(
            PlanPostcondition(
                type=PostconditionType.SCHEDULE_CREATED,
                description="A schedule ID and next run timestamp are reported.",
            )
        )
    return _dedupe(expected)


def _postcondition_satisfied(task: TaskRecord, expected: PostconditionType) -> bool:
    if expected == PostconditionType.WORKSPACE_DIR:
        return bool(_value(task, "workspace_dir", "workspace_dir"))
    if expected == PostconditionType.PREVIEW_URL:
        value = _value(task, "preview_url", "url")
        return isinstance(value, str) and value.startswith(("http://", "https://"))
    if expected == PostconditionType.ADAPTER_PROPOSAL:
        return bool(_value(task, "adapter_dir", "adapter_dir"))
    if expected == PostconditionType.ARTIFACT_DELIVERED:
        delivery = task.metadata.get("artifact_delivery")
        if isinstance(delivery, dict) and delivery.get("delivered") is True:
            return True
        output = _last_output_dict(task)
        return output.get("delivered") is True
    if expected == PostconditionType.DOCUMENT_SUMMARY:
        return bool(_any_value(task, metadata_keys=("document_summary",), output_keys=("summary", "text")))
    if expected == PostconditionType.PRESENTATION_FILE:
        value = _any_value(task, metadata_keys=("document_path",), output_keys=("path",))
        return isinstance(value, str) and value.lower().endswith(".pptx")
    if expected == PostconditionType.CODING_AGENT_STEP:
        output = _last_output_dict(task)
        if output.get("provider") not in {"codex", "github_copilot", "claude_code"}:
            return False
        session = task.metadata.get("coding_agent_session")
        session_status = session.get("status") if isinstance(session, dict) else None
        return (
            output.get("returncode") == 0
            or output.get("status") == "completed"
            or session_status in {"completed", "failed", "stopped"}
            or bool((output.get("limit_state") or {}).get("limited"))
        )
    if expected == PostconditionType.SCHEDULE_CREATED:
        return bool(_any_value(task, metadata_keys=("schedule_id",), output_keys=("schedule_id", "task_id")))
    if expected == PostconditionType.BROWSER_STATE:
        return bool(
            _any_value(
                task,
                metadata_keys=("browser_state", "browser_url", "page_title", "screenshot_uri", "screenshot_path"),
                output_keys=("browser_state", "browser_url", "url", "page_title", "screenshot_uri", "screenshot_path"),
            )
        )
    if expected == PostconditionType.DESKTOP_OBSERVATION:
        output = _last_output_dict(task)
        if output.get("operation") == "run_goal":
            # A run_goal step is a multi-step action loop with an actual objective;
            # a screenshot existing is not evidence the goal was reached. Only an
            # explicit completed=True counts (max_steps exhaustion reports False).
            return output.get("completed") is True
        return bool(
            _any_value(
                task,
                metadata_keys=("desktop_observation", "screenshot_uri", "screenshot_path", "computer_use_actions"),
                output_keys=("desktop_observation", "observation", "screenshot_uri", "screenshot_path", "artifact_uri", "final_summary"),
            )
        )
    if expected == PostconditionType.FILE_ORGANIZATION:
        return bool(
            _any_value(
                task,
                metadata_keys=("organized_paths", "moved_files", "changed_files", "files", "file_manifest"),
                output_keys=("organized_paths", "changed_paths", "moved_files", "changed_files", "files", "manifest", "entries"),
            )
        )
    if expected == PostconditionType.TASK_STATUS:
        return bool(_any_value(task, metadata_keys=("task_status",), output_keys=("task_status", "summary")))
    if expected == PostconditionType.GITHUB_PR:
        return bool(
            _any_value(
                task,
                metadata_keys=("pull_request_url", "pr_url", "pr_number"),
                output_keys=("pull_request_url", "pr_url", "html_url", "url", "pr_number", "number"),
            )
        )
    if expected == PostconditionType.EXTERNAL_COMMAND:
        return _external_command_succeeded(task)
    return False


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


def _any_value(task: TaskRecord, metadata_keys: tuple[str, ...], output_keys: tuple[str, ...]) -> Any:
    for key in metadata_keys:
        if task.metadata.get(key):
            return task.metadata[key]
    output = _last_output_dict(task)
    for key in output_keys:
        if output.get(key):
            return output[key]
    return None


def _external_command_succeeded(task: TaskRecord) -> bool:
    output = _last_output_dict(task)
    for key in ("returncode", "exit_code"):
        if output.get(key) == 0:
            return True
    terminal_output = output.get("terminal_output")
    if isinstance(terminal_output, list):
        for item in terminal_output:
            if isinstance(item, dict) and item.get("is_final") and item.get("exit_code") == 0:
                return True
    return False


def _last_output_dict(task: TaskRecord) -> dict[str, Any]:
    result = task.metadata.get("last_tool_result")
    if not isinstance(result, dict):
        return {}
    output = result.get("output")
    return output if isinstance(output, dict) else {}


def _dedupe(values: list[PlanPostcondition]) -> tuple[PlanPostcondition, ...]:
    result: list[PlanPostcondition] = []
    seen: set[PostconditionType] = set()
    for item in values:
        if item.type in seen:
            continue
        seen.add(item.type)
        result.append(item)
    return tuple(result)


def _gap_reason(value: PostconditionType) -> str:
    if value == PostconditionType.WORKSPACE_DIR:
        return "expected_workspace_dir_missing"
    if value == PostconditionType.PREVIEW_URL:
        return "expected_preview_url_missing"
    if value == PostconditionType.ADAPTER_PROPOSAL:
        return "expected_adapter_proposal_missing"
    if value == PostconditionType.SCHEDULE_CREATED:
        return "expected_schedule_created_missing"
    return f"expected_{value.value}_missing"


def _desktop_file_listing_request(lowered: str) -> bool:
    """True when the message uses 'desktop' as a folder path, not as a screen surface.

    This exempts the objective from requiring a DESKTOP_OBSERVATION postcondition.
    Covers two cases:
    - Listing the contents of the desktop folder ("list files on desktop")
    - Acting on a file located on the desktop ("find/read/open X on my desktop")
    """
    if "desktop" not in lowered:
        return False
    listing_markers = (
        "list all", "list the", "show all", "show me all",
        "what files", "which files",
        "files on", "files at", "files in",
        "folders on", "folders at", "folders in",
        "desktop files",
    )
    file_action_markers = (
        "find ", "search ", "look for ", "locate ",
        "read ", "open the ", "open my ", "open a ",
        "delete ", "remove ", "rename ", "move ", "copy ",
        "send me ", "share ", "email ", "upload ", "get ",
        ".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".xlsx", ".csv",
    )
    return any(marker in lowered for marker in listing_markers + file_action_markers)
