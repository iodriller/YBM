from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from agent_control.schemas import PlanModel, PlanPostcondition, PostconditionType, TaskRecord


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


def expected_fulfillment(objective: str) -> dict[str, bool]:
    expected = _postconditions_from_objective(objective)
    return {
        "workspace_dir": any(item.type == PostconditionType.WORKSPACE_DIR for item in expected),
        "preview_url": any(item.type == PostconditionType.PREVIEW_URL for item in expected),
        "adapter_proposal": any(item.type == PostconditionType.ADAPTER_PROPOSAL for item in expected),
    }


def expected_postconditions(task: TaskRecord, plan: PlanModel | None = None) -> tuple[PlanPostcondition, ...]:
    if plan and plan.postconditions:
        return tuple(plan.postconditions)

    inferred: list[PlanPostcondition] = []
    if plan is not None:
        inferred.extend(_postconditions_from_plan(plan))
    inferred.extend(_postconditions_from_objective(task.objective))
    return _dedupe(inferred)


def validate_fulfillment(task: TaskRecord, plan: PlanModel | None = None) -> FulfillmentValidation:
    expected = expected_postconditions(task, plan)
    missing = tuple(
        item.type
        for item in expected
        if item.required and not _postcondition_satisfied(task, item.type)
    )
    return FulfillmentValidation(expected=expected, missing=missing)


def fulfillment_gap(task: TaskRecord, plan: PlanModel | None = None) -> str | None:
    return validate_fulfillment(task, plan).first_gap


def _postconditions_from_plan(plan: PlanModel) -> list[PlanPostcondition]:
    expected: list[PlanPostcondition] = []
    for step in plan.steps:
        operation = str(step.tool_input.get("operation") or "")
        if step.tool_name == "adapter.factory" and operation in {"", "scaffold"}:
            expected.append(
                PlanPostcondition(
                    type=PostconditionType.ADAPTER_PROPOSAL,
                    description="A generated adapter proposal directory is reported.",
                )
            )
        if step.tool_name in {"workspace.manage", "workspace.web_app"}:
            if operation in {"prepare", "write_files", "materialize_static_app", "web_app_preview", "launch_static"}:
                expected.append(
                    PlanPostcondition(
                        type=PostconditionType.WORKSPACE_DIR,
                        description="A task workspace directory is reported.",
                    )
                )
            if operation in {"web_app_preview", "launch_static"}:
                expected.append(
                    PlanPostcondition(
                        type=PostconditionType.PREVIEW_URL,
                        description="A local preview URL is reported.",
                    )
                )
    return _dedupe(expected)


def _postconditions_from_objective(objective: str) -> list[PlanPostcondition]:
    lowered = objective.lower()
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
    return _dedupe(expected)


def _postcondition_satisfied(task: TaskRecord, expected: PostconditionType) -> bool:
    if expected == PostconditionType.WORKSPACE_DIR:
        return bool(_value(task, "workspace_dir", "workspace_dir"))
    if expected == PostconditionType.PREVIEW_URL:
        value = _value(task, "preview_url", "url")
        return isinstance(value, str) and value.startswith(("http://", "https://"))
    if expected == PostconditionType.ADAPTER_PROPOSAL:
        return bool(_value(task, "adapter_dir", "adapter_dir"))
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
    return f"expected_{value.value}_missing"
