from __future__ import annotations

import re
from pathlib import Path

from agent_control.config import AppSettings
from agent_control.orchestration.fulfillment import expected_fulfillment
from agent_control.prompts import render_prompt
from agent_control.scheduler import cadence_from_text, objective_from_schedule_text
from agent_control.tools.local_workspace import workspace_dir_for_task
from agent_control.schemas import (
    Capability,
    DeliveryKind,
    IntentRoute,
    OrchestrationIntent,
    PlanModel,
    PlanPostcondition,
    PlanStep,
    PostconditionType,
    RiskLevel,
    TaskRecord,
    TaskType,
)


def build_default_task_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    intent_plan = _build_intent_plan(settings, task)
    if intent_plan is not None:
        return intent_plan

    status_plan = _build_status_plan(settings, task)
    if status_plan is not None:
        return status_plan

    file_lookup_delivery_plan = _build_file_lookup_delivery_plan(settings, task)
    if file_lookup_delivery_plan is not None:
        return file_lookup_delivery_plan
    filesystem_plan = _build_filesystem_manage_plan(settings, task)
    if filesystem_plan is not None:
        return filesystem_plan
    if _looks_like_latest_output_delivery_request(task.objective):
        artifact_plan = _build_artifact_delivery_plan(settings, task)
        if artifact_plan is not None:
            return artifact_plan
    document_plan = _build_document_plan(settings, task)
    if document_plan is not None:
        return document_plan
    schedule_plan = _build_schedule_plan(settings, task)
    if schedule_plan is not None:
        return schedule_plan
    adapter_plan = _build_adapter_factory_plan(settings, task)
    if adapter_plan is not None:
        return adapter_plan
    coding_agent_plan = _build_coding_agent_plan(settings, task)
    if coding_agent_plan is not None:
        return coding_agent_plan
    browser_plan = _build_browser_plan(settings, task)
    if browser_plan is not None:
        return browser_plan
    computer_plan = _build_computer_use_plan(settings, task)
    if computer_plan is not None:
        return computer_plan
    artifact_plan = _build_artifact_delivery_plan(settings, task)
    if artifact_plan is not None:
        return artifact_plan
    return build_default_vscode_development_plan(settings, task)


def build_evaluator_recovery_plan(settings: AppSettings, task: TaskRecord, failure_reason: str) -> PlanModel | None:
    reason = failure_reason.lower()
    if int(task.metadata.get("evaluator_repair_count", 0)) >= 2:
        return None
    if "expected_desktop_observation_missing" in reason:
        if _looks_like_desktop_file_listing(task.objective):
            return _build_filesystem_manage_plan(settings, task)
        return _build_computer_use_plan(settings, task)
    if (
        "outside configured delivery roots" in reason
        or "no deliverable artifact" in reason
        or "expected_artifact_delivered_missing" in reason
    ):
        return _build_file_lookup_delivery_plan(settings, task) or _build_artifact_delivery_plan(settings, task)
    if "tool adapter not registered" in reason or "unregistered tool" in reason:
        return _build_adapter_factory_plan(settings, task)
    if "unsupported operation" in reason or "validation" in reason:
        return _build_code_interpreter_recovery_plan(settings, task, failure_reason)
    return None


def _build_intent_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    intent = _task_intent(task)
    if intent is None or intent.route in {IntentRoute.CONVERSATION, IntentRoute.UNKNOWN}:
        return None

    objective = intent.objective or task.objective
    request_text = _task_request_text(task)
    policy_send = settings.capabilities.get(Capability.TELEGRAM_SEND)

    if intent.route == IntentRoute.STATUS:
        return _build_status_plan(settings, task, objective=objective)

    if intent.route == IntentRoute.DESKTOP_OBSERVE:
        if _looks_like_desktop_file_listing(request_text):
            return _build_filesystem_manage_plan(settings, task.model_copy(update={"objective": objective}))
        desktop_policy = settings.capabilities.get(Capability.DESKTOP_CONTROL)
        if (
            not settings.adapters.computer_use.enabled
            or not settings.adapters.desktop.control_enabled
            or desktop_policy is None
            or not desktop_policy.enabled
        ):
            return None
        steps = [
            PlanStep(
                title="Observe desktop",
                description="Capture the local desktop state and summarize visible windows/UI.",
                required_capabilities=[Capability.DESKTOP_CONTROL],
                risk_level=RiskLevel.CRITICAL,
                requires_approval=desktop_policy.requires_approval,
                tool_name="computer.use",
                tool_input={
                    "operation": "observe",
                    "objective": objective,
                    "include_screenshot": True,
                    "include_ui_tree": True,
                    "summarize": True,
                    "timeout_seconds": 60,
                },
                expected_output="Desktop observation, screenshot path, and summary.",
            )
        ]
        wants_screenshot_delivery = intent.delivery == DeliveryKind.SCREENSHOT or (
            intent.operation in {"screenshot", "send_screenshot"} and _looks_like_delivery_request(request_text)
        )
        if wants_screenshot_delivery and policy_send and policy_send.enabled:
            steps.append(_intent_delivery_step(settings, request_text, "send_screenshot", screenshot=True))
        return PlanModel(
            objective=objective,
            assumptions=["The LLM router selected desktop observation."],
            required_capabilities=[Capability.DESKTOP_CONTROL, *([Capability.TELEGRAM_SEND] if len(steps) > 1 else [])],
            steps=steps,
            success_criteria=["The desktop state is observed and summarized."],
            postconditions=[
                *_desktop_postconditions(),
                *(
                    [
                        PlanPostcondition(
                            type=PostconditionType.ARTIFACT_DELIVERED,
                            description="The requested desktop screenshot is delivered to Telegram.",
                        )
                    ]
                    if wants_screenshot_delivery and len(steps) > 1
                    else []
                ),
            ],
        )

    if intent.route == IntentRoute.COMPUTER_USE:
        if _looks_like_desktop_file_listing(request_text):
            return _build_filesystem_manage_plan(settings, task.model_copy(update={"objective": objective}))
        desktop_policy = settings.capabilities.get(Capability.DESKTOP_CONTROL)
        if (
            not settings.adapters.computer_use.enabled
            or not settings.adapters.desktop.control_enabled
            or desktop_policy is None
            or not desktop_policy.enabled
        ):
            return None
        operation = intent.operation if intent.operation in {"observe", "act", "run_goal"} else "run_goal"
        if operation == "observe":
            tool_input = {
                "operation": "observe",
                "objective": objective,
                "include_screenshot": True,
                "include_ui_tree": True,
                "summarize": True,
                "timeout_seconds": 60,
            }
        elif operation == "act":
            tool_input = {
                "operation": "act",
                "objective": objective,
                "action": {"type": "open_path", "path": intent.path or intent.folder_path or intent.file_path}
                if (intent.path or intent.folder_path or intent.file_path)
                else {"type": "wait", "seconds": settings.adapters.computer_use.step_delay_seconds},
                "timeout_seconds": 60,
            }
        else:
            tool_input = {
                "operation": "run_goal",
                "objective": objective,
                "max_steps": settings.adapters.computer_use.max_steps,
                "include_ui_tree": True,
                "require_vision": True,
                "timeout_seconds": 240,
            }
        steps = [
            PlanStep(
                title="Run computer-use action",
                description="Use the bounded computer-use adapter for the requested desktop action.",
                required_capabilities=[Capability.DESKTOP_CONTROL],
                risk_level=RiskLevel.CRITICAL,
                requires_approval=desktop_policy.requires_approval,
                tool_name="computer.use",
                tool_input=tool_input,
                expected_output="Desktop observation, actions taken, and final summary.",
            )
        ]
        wants_screenshot_delivery = _objective_wants_screenshot(request_text) and _looks_like_delivery_request(request_text)
        if wants_screenshot_delivery and policy_send and policy_send.enabled:
            steps.append(_intent_delivery_step(settings, request_text, "send_screenshot", screenshot=True))
        return PlanModel(
            objective=objective,
            assumptions=["The LLM router selected bounded local computer use."],
            required_capabilities=[Capability.DESKTOP_CONTROL, *([Capability.TELEGRAM_SEND] if len(steps) > 1 else [])],
            steps=steps,
            success_criteria=["The requested bounded desktop action is completed or a clear adapter error is reported."],
            postconditions=[
                *_desktop_postconditions(),
                *(
                    [
                        PlanPostcondition(
                            type=PostconditionType.ARTIFACT_DELIVERED,
                            description="The requested desktop screenshot is delivered to Telegram.",
                        )
                    ]
                    if wants_screenshot_delivery and len(steps) > 1
                    else []
                ),
            ],
        )

    if _intent_requests_pdf_summary(intent, request_text, objective):
        pdf_plan = _build_pdf_summary_intent_plan(settings, task, intent, objective)
        if pdf_plan is not None:
            return pdf_plan

    if intent.route == IntentRoute.FILESYSTEM_MANAGE:
        if _looks_like_visual_desktop_observation_request(request_text):
            return _build_computer_use_plan(settings, task.model_copy(update={"objective": request_text}))
        if not settings.adapters.computer_use.enabled:
            return None
        write_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
        if write_policy is None or not write_policy.enabled:
            return None
        root = (
            _usable_intent_path(intent.folder_path)
            or _folder_root_from_request(request_text)
            or _usable_intent_path(intent.path)
            or _usable_intent_path(intent.file_path)
            or _root_from_objective(objective)
        )
        if not root:
            return None
        operation = _filesystem_intent_operation(intent.operation)
        if _looks_like_rename_request(request_text):
            operation = "rename_plan"
        listing_or_description = operation in {"inspect_folder", "describe_folder"} and not _looks_like_delivery_request(request_text)
        if not listing_or_description and (
            intent.delivery in {DeliveryKind.FILE, DeliveryKind.LATEST} or _looks_like_delivery_request(_task_request_text(task))
        ):
            file_lookup_plan = _build_file_lookup_delivery_plan(settings, task, intent=intent)
            if file_lookup_plan is not None:
                return file_lookup_plan
        if _looks_like_find_and_read_request(request_text, objective) and operation == "search":
            operation = "search"
        if operation == "organize_plan" and _looks_like_file_search(objective):
            operation = "search"
        if operation == "read_file" and not (intent.file_path or intent.path):
            operation = "search"
        if operation == "search":
            steps = [
                PlanStep(
                    title="Search scoped folder",
                    description="Search inside the requested folder and include readable file previews when possible.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "search",
                        "root": root,
                        "query": intent.query or _query_from_file_reference(intent.file_path or intent.path) or "*",
                        "include_content": True,
                        "timeout_seconds": 60,
                    },
                    expected_output="Matching file paths and readable content previews under the configured allowed root.",
                )
            ]
        elif operation == "read_file":
            steps = [
                PlanStep(
                    title="Read scoped file",
                    description="Read the requested file content through filesystem APIs.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "read_file",
                        "path": intent.file_path or intent.path,
                        "max_chars": 16000,
                        "timeout_seconds": 60,
                    },
                    expected_output="Readable file content and a concise summary.",
                )
            ]
        elif operation == "inspect_folder":
            steps = [
                PlanStep(
                    title="Inspect scoped folder",
                    description="List and summarize the requested folder using filesystem APIs.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={"operation": "inspect_folder", "root": root, "timeout_seconds": 60},
                    expected_output="Folder entries and summary.",
                )
            ]
        elif operation == "describe_folder":
            steps = [
                PlanStep(
                    title="Describe folder contents",
                    description="Inspect supported files, extract readable content, and summarize what the folder contains.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "describe_folder",
                        "root": root,
                        "recursive": False,
                        "include_ocr": True,
                        "timeout_seconds": 180,
                    },
                    expected_output="Per-file descriptions, extracted text previews, OCR status, and folder summary.",
                )
            ]
        elif operation == "rename_plan":
            steps = [
                PlanStep(
                    title="Plan file renames",
                    description="Create a before/after rename manifest before changing files.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "rename_plan",
                        "root": root,
                        "strategy": "by_content",
                        "recursive": False,
                        "timeout_seconds": 60,
                    },
                    expected_output="A proposed before/after rename manifest.",
                ),
                PlanStep(
                    title="Apply file rename manifest",
                    description="Apply the generated rename manifest inside the same allowed folder.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "apply_manifest",
                        "root": root,
                        "manifest": "{{last_manifest}}",
                        "dry_run": False,
                        "timeout_seconds": 120,
                    },
                    expected_output="Before/after rename table and changed paths.",
                ),
            ]
        else:
            steps = [
                PlanStep(
                    title="Plan folder organization",
                    description="Create a move/copy manifest before changing files.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "organize_plan",
                        "root": root,
                        "strategy": "by_type",
                        "recursive": False,
                        "timeout_seconds": 60,
                    },
                    expected_output="A proposed file organization manifest.",
                ),
                PlanStep(
                    title="Apply folder organization manifest",
                    description="Apply the generated manifest inside the same allowed folder.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "apply_manifest",
                        "root": root,
                        "manifest": "{{last_manifest}}",
                        "dry_run": False,
                        "timeout_seconds": 120,
                    },
                    expected_output="Changed paths from the applied manifest.",
                ),
            ]
        return PlanModel(
            objective=objective,
            assumptions=["The LLM router selected scoped filesystem APIs instead of desktop UI automation."],
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            steps=steps,
            success_criteria=["The filesystem operation reports changed paths or readable results."],
            postconditions=_file_organization_postconditions(required=operation not in {"inspect_folder", "search", "describe_folder"}),
        )

    if intent.route == IntentRoute.BROWSER_OPEN:
        prompt_plan = _build_browser_prompt_interaction_plan(settings, task, intent, request_text, objective)
        if prompt_plan is not None:
            return prompt_plan
        open_policy = settings.capabilities.get(Capability.BROWSER_OPEN)
        if not settings.adapters.browser.enabled or open_policy is None or not open_policy.enabled:
            return None
        if intent.url and _objective_wants_page_update_check(objective):
            control_policy = settings.capabilities.get(Capability.BROWSER_CONTROL)
            if control_policy is not None and control_policy.enabled:
                return PlanModel(
                    objective=objective,
                    assumptions=["The LLM router selected browser open, but the objective is a page update/content check."],
                    required_capabilities=[Capability.BROWSER_CONTROL],
                    steps=[
                        PlanStep(
                            title="Check browser page for updates",
                            description="Inspect the requested page for new release/episode/content evidence.",
                            required_capabilities=[Capability.BROWSER_CONTROL],
                            risk_level=RiskLevel.CRITICAL,
                            requires_approval=control_policy.requires_approval,
                            tool_name="browser.control",
                            tool_input={
                                "operation": "check_page_update",
                                "url": intent.url,
                                "objective": objective,
                                "timeout_seconds": 90,
                            },
                            expected_output="Current page evidence and update markers.",
                        )
                    ],
                    success_criteria=["The requested page is inspected for current content/update evidence."],
                    postconditions=_browser_postconditions(),
                )
        operation = intent.operation if intent.operation in {"open", "search", "research", "inspect_tabs", "screenshot", "summarize_page", "research_pages"} else None
        if operation is None:
            operation = "screenshot" if intent.delivery == DeliveryKind.SCREENSHOT else "research_pages" if intent.page_limit else "research"
        tool_input: dict[str, object] = {"operation": operation, "objective": objective, "timeout_seconds": 180 if operation == "research_pages" else 90}
        if intent.url:
            tool_input["url"] = intent.url
        if intent.query:
            tool_input["query"] = intent.query
        if intent.page_limit:
            tool_input["page_limit"] = intent.page_limit
        if intent.open_first_result:
            tool_input["open_first_result"] = True
        if operation == "screenshot":
            tool_input.setdefault("full_page", True)
        steps = [
            PlanStep(
                title="Use Chrome browser adapter",
                description="Use the browser adapter for the LLM-selected browser task.",
                required_capabilities=[Capability.BROWSER_OPEN],
                risk_level=RiskLevel.LOW,
                requires_approval=open_policy.requires_approval,
                tool_name="browser.open",
                tool_input=tool_input,
                expected_output="Browser state, page summary, research results, or screenshot path.",
            )
        ]
        if intent.delivery == DeliveryKind.SCREENSHOT and policy_send and policy_send.enabled:
            steps.append(_intent_delivery_step(settings, objective, "send_screenshot", screenshot=True))
        return PlanModel(
            objective=objective,
            assumptions=["The LLM router selected the Chrome browser adapter."],
            required_capabilities=[Capability.BROWSER_OPEN, *([Capability.TELEGRAM_SEND] if len(steps) > 1 else [])],
            steps=steps,
            success_criteria=["The browser adapter reports the requested result."],
            postconditions=[
                *_browser_postconditions(),
                *(
                    [
                        PlanPostcondition(
                            type=PostconditionType.ARTIFACT_DELIVERED,
                            description="The requested browser artifact is delivered to Telegram.",
                        )
                    ]
                    if len(steps) > 1
                    else []
                ),
            ],
        )

    if intent.route == IntentRoute.BROWSER_CONTROL:
        prompt_plan = _build_browser_prompt_interaction_plan(settings, task, intent, request_text, objective)
        if prompt_plan is not None:
            return prompt_plan
        control_policy = settings.capabilities.get(Capability.BROWSER_CONTROL)
        if not settings.adapters.browser.enabled or control_policy is None or not control_policy.enabled:
            return None
        open_policy = settings.capabilities.get(Capability.BROWSER_OPEN)
        if intent.operation in {"search", "research", "research_pages", "summarize_page", "inspect_tabs"} and not (
            intent.url and _objective_wants_page_update_check(objective)
        ):
            if open_policy is None or not open_policy.enabled:
                return None
            operation = intent.operation
            if operation == "search" and intent.open_first_result:
                operation = "research"
            tool_input: dict[str, object] = {
                "operation": operation,
                "objective": objective,
                "timeout_seconds": 180 if operation == "research_pages" else 90,
            }
            if intent.query:
                tool_input["query"] = intent.query
            if intent.url:
                tool_input["url"] = intent.url
            if intent.page_limit:
                tool_input["page_limit"] = intent.page_limit
            if intent.open_first_result:
                tool_input["open_first_result"] = True
            return PlanModel(
                objective=objective,
                assumptions=["The LLM router selected browser control, but the operation is a browser research/read operation."],
                required_capabilities=[Capability.BROWSER_OPEN],
                steps=[
                    PlanStep(
                        title="Use browser research adapter",
                        description="Search/open/read through the browser adapter.",
                        required_capabilities=[Capability.BROWSER_OPEN],
                        risk_level=RiskLevel.LOW,
                        requires_approval=open_policy.requires_approval,
                        tool_name="browser.open",
                        tool_input=tool_input,
                        expected_output="Visited URL, page title, and page summary.",
                    )
                ],
                success_criteria=["The browser adapter reports source/page evidence for the requested research."],
                postconditions=_browser_postconditions(),
            )
        steps: list[PlanStep] = []
        required = [Capability.BROWSER_CONTROL]
        if intent.url and open_policy and open_policy.enabled:
            required.insert(0, Capability.BROWSER_OPEN)
            steps.append(
                PlanStep(
                    title="Open requested browser page",
                    description="Open the target URL before applying browser control.",
                    required_capabilities=[Capability.BROWSER_OPEN],
                    risk_level=RiskLevel.LOW,
                    requires_approval=open_policy.requires_approval,
                    tool_name="browser.open",
                    tool_input={"operation": "open", "url": intent.url, "new_tab": True, "timeout_seconds": 60},
                    expected_output="Requested page is open in Chrome.",
                )
            )
        if intent.operation == "screenshot" or (
            intent.operation in {None, "navigate"}
            and intent.delivery == DeliveryKind.SCREENSHOT
            and _objective_wants_screenshot(objective)
        ):
            if open_policy is None or not open_policy.enabled:
                return None
            steps.append(
                PlanStep(
                    title="Capture browser screenshot",
                    description="Capture the requested browser page after opening or navigating to it.",
                    required_capabilities=[Capability.BROWSER_OPEN],
                    risk_level=RiskLevel.LOW,
                    requires_approval=open_policy.requires_approval,
                    tool_name="browser.open",
                    tool_input={
                        "operation": "screenshot",
                        **({"url": intent.url} if intent.url else {}),
                        "full_page": True,
                        "timeout_seconds": 60,
                    },
                    expected_output="Browser screenshot path and page summary.",
                )
            )
            if policy_send and policy_send.enabled:
                steps.append(_intent_delivery_step(settings, objective, "send_screenshot", screenshot=True))
                required.append(Capability.TELEGRAM_SEND)
            return PlanModel(
                objective=objective,
                assumptions=["The LLM router selected browser control, and the requested outcome is a browser screenshot."],
                required_capabilities=required,
                steps=steps,
                success_criteria=["The requested browser page is captured and delivered when requested."],
                postconditions=[
                    *_browser_postconditions(),
                    *(
                        [
                            PlanPostcondition(
                                type=PostconditionType.ARTIFACT_DELIVERED,
                                description="The requested browser screenshot is delivered to Telegram.",
                            )
                        ]
                        if intent.delivery == DeliveryKind.SCREENSHOT and policy_send and policy_send.enabled
                        else []
                    ),
                ],
            )
        control_operation = _browser_control_operation(intent.operation, objective)
        operation = control_operation if control_operation in {"navigate", "close_tab", "click", "fill_form", "check_page_update", "extract_page_state", "fill_form_step"} else None
        operation = operation or ("check_page_update" if intent.query else "fill_form_step" if intent.form_fields else "navigate")
        if operation in {"fill_form", "fill_form_step"}:
            if not any(step.tool_name == "browser.control" and step.tool_input.get("operation") == "extract_page_state" for step in steps):
                steps.append(
                    PlanStep(
                        title="Extract form fields",
                        description="Inspect the current page form state before filling fields.",
                        required_capabilities=[Capability.BROWSER_CONTROL],
                        risk_level=RiskLevel.CRITICAL,
                        requires_approval=control_policy.requires_approval,
                        tool_name="browser.control",
                        tool_input={
                            "operation": "extract_page_state",
                            **({"url_contains": intent.url} if intent.url else {}),
                            "timeout_seconds": 60,
                        },
                        expected_output="Detected form fields.",
                    )
                )
            control_input = {
                "operation": "fill_form_step",
                "fields": intent.form_fields,
                "submit": intent.submit,
                **({"url_contains": intent.url} if intent.url else {}),
                "timeout_seconds": 60,
            }
        elif operation == "check_page_update":
            control_input = {"operation": "check_page_update", "url": intent.url, "objective": objective, "timeout_seconds": 90}
        else:
            if operation == "navigate" and not intent.url:
                return None
            control_input = {"operation": operation, "objective": objective, "timeout_seconds": 60}
            if intent.url:
                control_input["url"] = intent.url
        steps.append(
            PlanStep(
                title="Control Chrome through browser adapter",
                description="Apply the LLM-selected browser control action.",
                required_capabilities=[Capability.BROWSER_CONTROL],
                risk_level=RiskLevel.CRITICAL,
                requires_approval=control_policy.requires_approval,
                tool_name="browser.control",
                tool_input=control_input,
                expected_output="Updated browser state and concise result summary.",
            )
        )
        if operation in {"fill_form", "fill_form_step"} and intent.delivery == DeliveryKind.SCREENSHOT and intent.url:
            steps.append(
                PlanStep(
                    title="Capture filled form screenshot",
                    description="Capture the browser state after filling the form, without submitting it.",
                    required_capabilities=[Capability.BROWSER_OPEN],
                    risk_level=RiskLevel.LOW,
                    requires_approval=bool(open_policy and open_policy.requires_approval),
                    tool_name="browser.open",
                    tool_input={
                        "operation": "screenshot",
                        "url_contains": intent.url,
                        "full_page": True,
                        "timeout_seconds": 60,
                    },
                    expected_output="A screenshot of the filled form for review.",
                )
            )
            if policy_send and policy_send.enabled:
                steps.append(_intent_delivery_step(settings, objective, "send_screenshot", screenshot=True))
                required.append(Capability.TELEGRAM_SEND)
        return PlanModel(
            objective=objective,
            assumptions=["The LLM router selected browser control."],
            required_capabilities=required,
            steps=steps,
            success_criteria=["The requested browser control action is performed or a clear adapter error is reported."],
            postconditions=_browser_postconditions(),
        )

    if intent.route == IntentRoute.DOCUMENT_MANAGE:
        policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
        if policy is None or not policy.enabled:
            return None
        operation = intent.operation if intent.operation in {"inspect_document", "extract_text", "summarize_pdf", "create_presentation", "update_presentation"} else None
        if operation is None:
            operation = "summarize_pdf" if intent.file_path else "create_presentation"
        tool_input: dict[str, object] = {"operation": operation, "timeout_seconds": 90}
        if intent.file_path or intent.path:
            tool_input["path"] = intent.file_path or intent.path
        if operation in {"create_presentation", "update_presentation"}:
            content_source = "{{last_output}}" if intent.provider in {"codex", "github_copilot"} else objective
            tool_input.update({"title": _presentation_title(objective), "content": content_source, "instructions": content_source})
        steps = [
            PlanStep(
                title="Run document operation",
                description="Use the document adapter for the LLM-selected document task.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="document.manage",
                tool_input=tool_input,
                expected_output="Document summary or generated file artifact.",
            )
        ]
        if intent.delivery in {DeliveryKind.FILE, DeliveryKind.LATEST} and policy_send and policy_send.enabled:
            steps.append(_intent_delivery_step(settings, objective, "send_latest", artifact_type=intent.artifact_type or "document"))
        return PlanModel(
            objective=objective,
            assumptions=["The LLM router selected document management."],
            required_capabilities=[Capability.FILESYSTEM_WRITE, *([Capability.TELEGRAM_SEND] if len(steps) > 1 else [])],
            steps=steps,
            success_criteria=["The document adapter reports the requested result."],
            postconditions=[
                PlanPostcondition(
                    type=PostconditionType.PRESENTATION_FILE if "presentation" in operation else PostconditionType.DOCUMENT_SUMMARY,
                    description="The requested document output is reported.",
                    required=True,
                )
            ],
        )

    if intent.route == IntentRoute.ARTIFACT_DELIVERY:
        policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
        if policy is None or not policy.enabled:
            return None
        intent_path = intent.file_path or intent.path
        if intent_path and _looks_like_file_creation_request(request_text):
            create_plan = _build_create_and_deliver_file_plan(settings, task, intent_path, request_text)
            if create_plan is not None:
                return create_plan
        if (not intent_path or not _usable_explicit_path(intent_path)) and intent.delivery != DeliveryKind.SCREENSHOT:
            file_lookup_plan = _build_file_lookup_delivery_plan(settings, task, intent=intent)
            if file_lookup_plan is not None:
                return file_lookup_plan
        operation = intent.operation if intent.operation in {"send_file", "send_latest", "send_screenshot", "list_artifacts"} else None
        if operation is None:
            operation = "send_file" if (intent.file_path or intent.path) else "send_screenshot" if intent.delivery == DeliveryKind.SCREENSHOT else "send_latest"
        tool_input: dict[str, object] = {
            "operation": operation,
            "caption": f"Result for: {objective[:180]}",
            "timeout_seconds": 60,
        }
        if intent.file_path or intent.path:
            tool_input["path"] = intent.file_path or intent.path
        if intent.artifact_type:
            tool_input["artifact_type"] = intent.artifact_type
        return PlanModel(
            objective=objective,
            assumptions=["The LLM router selected Telegram artifact delivery for current task artifacts or explicit paths."],
            required_capabilities=[Capability.TELEGRAM_SEND],
            steps=[
                PlanStep(
                    title="Deliver artifact to Telegram",
                    description="Send the requested current-task artifact, screenshot, or explicit file.",
                    required_capabilities=[Capability.TELEGRAM_SEND],
                    risk_level=RiskLevel.LOW,
                    requires_approval=policy.requires_approval,
                    tool_name="artifact.deliver",
                    tool_input=tool_input,
                    expected_output="Telegram delivery result.",
                )
            ],
            success_criteria=["The requested artifact is sent to Telegram or a clear delivery error is reported."],
            postconditions=[
                PlanPostcondition(
                    type=PostconditionType.ARTIFACT_DELIVERED,
                    description="The requested artifact is delivered to Telegram.",
                )
            ],
        )

    if intent.route == IntentRoute.CODE_INTERPRETER:
        if _looks_like_web_note_delivery_request(request_text):
            web_note_plan = _build_web_note_delivery_plan(settings, task, intent, request_text, objective)
            if web_note_plan is not None:
                return web_note_plan
        policy = settings.capabilities.get(Capability.TERMINAL_RUN)
        if policy is None or not policy.enabled or not settings.adapters.code_interpreter.enabled:
            return None
        operation = intent.operation if intent.operation in {"run_python", "generate_and_run"} else "generate_and_run"
        request_text = _task_request_text(task)
        interpreter_objective = request_text if request_text.strip() else objective
        tool_input: dict[str, object] = {
            "operation": operation,
            "objective": interpreter_objective,
            "context": f"Router objective: {objective}",
            "workspace_dir": str(workspace_dir_for_task(settings.adapters.code_interpreter.workspace_root, task.id)),
            "timeout_seconds": settings.adapters.code_interpreter.timeout_seconds,
        }
        if operation == "run_python":
            tool_input["code"] = intent.query or intent.objective or objective
        return PlanModel(
            objective=objective,
            assumptions=["The LLM router selected bounded local Python code interpretation."],
            required_capabilities=[Capability.TERMINAL_RUN],
            steps=[
                PlanStep(
                    title="Run local code interpreter",
                    description="Generate and execute a small Python script inside a managed task workspace.",
                    required_capabilities=[Capability.TERMINAL_RUN],
                    risk_level=RiskLevel.MEDIUM,
                    requires_approval=policy.requires_approval,
                    tool_name="code.interpreter",
                    tool_input=tool_input,
                    expected_output="Interpreter stdout, stderr, changed files, and workspace path.",
                )
            ],
            success_criteria=["The code interpreter produces a clear result or reports execution errors."],
            postconditions=[
                PlanPostcondition(
                    type=PostconditionType.EXTERNAL_COMMAND,
                    description="A bounded local Python script executed and returned output.",
                )
            ],
        )

    if intent.route == IntentRoute.CODING_AGENT:
        if _looks_like_document_request(task.objective) or _looks_like_document_request(objective):
            document_plan = _build_document_plan(settings, task)
            if document_plan is not None:
                return document_plan
        if intent.provider not in {"codex", "github_copilot"} and _looks_like_local_web_app_build(request_text):
            workspace_plan = _build_workspace_intent_plan(settings, task, intent, request_text)
            if workspace_plan is not None:
                return workspace_plan
        policy = settings.capabilities.get(Capability.TERMINAL_RUN)
        if policy is None or not policy.enabled or not settings.adapters.coding_agent.enabled:
            return None
        provider = intent.provider
        if provider not in {"codex", "github_copilot"}:
            return None
        operation = intent.operation if intent.operation in {"plan", "run_step", "run_goal", "status", "limits", "resume", "stop"} else None
        operation = operation or ("plan" if intent.needs_plan_first else "run_goal")
        steps = [
            PlanStep(
                title=f"Run {provider} coding agent",
                description="Run the explicitly requested coding agent in a task workspace.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="coding.agent",
                tool_input={
                    "operation": operation,
                    "provider": provider,
                    "objective": objective,
                    "prompt": objective,
                    "workspace_dir": str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)),
                    "timeout_seconds": settings.adapters.coding_agent.timeout_seconds,
                },
                expected_output="Coding-agent output, workspace state, status, or limit state.",
            )
        ]
        return PlanModel(
            objective=objective,
            assumptions=[f"The LLM router selected explicit {provider} use."],
            required_capabilities=[Capability.TERMINAL_RUN],
            steps=steps,
            success_criteria=["The requested coding-agent operation reports status, output, or limits clearly."],
            postconditions=[
                PlanPostcondition(
                    type=PostconditionType.CODING_AGENT_STEP,
                    description="The coding agent completed a step or reported a status/limit state.",
                )
            ],
        )

    if intent.route == IntentRoute.SCHEDULE_MANAGE:
        policy = settings.capabilities.get(Capability.SCHEDULE_MANAGE)
        if policy is None or not policy.enabled or not settings.scheduler.enabled:
            return None
        operation = intent.operation if intent.operation in {"create", "list", "pause", "resume", "delete", "run_now"} else "create"
        tool_input: dict[str, object] = {"operation": operation, "timeout_seconds": 30}
        if operation == "create":
            tool_input.update(
                {
                    "objective": intent.scheduled_objective or objective,
                    "cadence": intent.cadence or "daily",
                    "timezone": settings.scheduler.default_timezone,
                    "source_chat_id": task.metadata.get("source_chat_id"),
                    "metadata": {
                        "created_from_task_id": task.id,
                        "created_from_objective": task.objective,
                        **({"coding_provider": intent.provider} if intent.provider else {}),
                    },
                }
            )
        elif intent.schedule_id:
            tool_input["schedule_id"] = intent.schedule_id
        elif operation != "list":
            return None
        return PlanModel(
            objective=objective,
            assumptions=["The LLM router selected schedule management."],
            required_capabilities=[Capability.SCHEDULE_MANAGE],
            steps=[
                PlanStep(
                    title=f"{operation.replace('_', ' ').title()} schedule",
                    description="Run the selected scheduler operation.",
                    required_capabilities=[Capability.SCHEDULE_MANAGE],
                    risk_level=RiskLevel.MEDIUM,
                    requires_approval=policy.requires_approval,
                    tool_name="schedule.manage",
                    tool_input=tool_input,
                    expected_output="Schedule operation result.",
                )
            ],
            success_criteria=["The schedule operation returns a clear state."],
            postconditions=[
                PlanPostcondition(
                    type=PostconditionType.SCHEDULE_CREATED,
                    description="A schedule ID and next run timestamp are reported.",
                    required=operation == "create",
                )
            ],
        )

    if intent.route == IntentRoute.ADAPTER_FACTORY:
        return _build_adapter_factory_intent_plan(settings, task, intent, objective)

    if intent.route == IntentRoute.WORKSPACE_MANAGE:
        return _build_workspace_intent_plan(settings, task, intent, objective)

    return None


def _task_intent(task: TaskRecord) -> OrchestrationIntent | None:
    raw = task.metadata.get("orchestration_intent") or task.metadata.get("intent")
    if not raw:
        return None
    try:
        return OrchestrationIntent.model_validate(raw)
    except Exception:
        return None


def _task_request_text(task: TaskRecord) -> str:
    original = task.metadata.get("original_message_text")
    if isinstance(original, str) and original.strip():
        return original.strip()
    return task.objective


def _build_status_plan(settings: AppSettings, task: TaskRecord, *, objective: str | None = None) -> PlanModel | None:
    task_type = str(task.metadata.get("task_type") or "")
    if task_type != TaskType.STATUS_REQUEST.value and not _looks_like_status_request(task.objective):
        return None
    policy = settings.capabilities.get(Capability.TELEGRAM_RECEIVE)
    if policy is None or not policy.enabled:
        return None
    return PlanModel(
        objective=objective or task.objective,
        assumptions=["The request asks for current task/workflow status, so the worker reads task and plan state."],
        required_capabilities=[Capability.TELEGRAM_RECEIVE],
        steps=[
            PlanStep(
                title="Report task status",
                description="Summarize recent, active, completed, and blocked task state from the repository.",
                required_capabilities=[Capability.TELEGRAM_RECEIVE],
                risk_level=RiskLevel.LOW,
                requires_approval=policy.requires_approval,
                tool_name="task.status",
                tool_input={"operation": "status", "limit": 20, "timeout_seconds": 30},
                expected_output="Current task status, active work, recent completions, blocked work, and plan context.",
            )
        ],
        success_criteria=["A grounded status summary is returned from repository state."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.TASK_STATUS,
                description="Task and plan status were inspected and summarized.",
                required=True,
            )
        ],
    )


def _filesystem_intent_operation(operation: str | None) -> str:
    normalized = (operation or "").strip().lower().replace("-", "_")
    aliases = {
        "inspect": "inspect_folder",
        "list": "inspect_folder",
        "ls": "inspect_folder",
        "read_folder": "inspect_folder",
        "read_file": "read_file",
        "read": "read_file",
        "file_read": "read_file",
        "folder_inspection": "inspect_folder",
        "describe": "describe_folder",
        "explain": "describe_folder",
        "summarize": "describe_folder",
        "summarize_folder": "describe_folder",
        "folder_description": "describe_folder",
        "find": "search",
        "find_file": "search",
        "find_files": "search",
        "locate": "search",
        "locate_file": "search",
        "lookup_file": "search",
        "lookup": "search",
        "get_file": "search",
        "send_file": "search",
        "deliver_file": "search",
        "retrieve_file": "search",
        "organize": "organize_plan",
        "plan_organization": "organize_plan",
        "rename": "rename_plan",
        "rename_files": "rename_plan",
        "file_rename": "rename_plan",
        "apply": "apply_manifest",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"inspect_folder", "search", "read_file", "describe_folder", "organize_plan", "rename_plan", "apply_manifest"}:
        return normalized
    return "organize_plan"


def _looks_like_visual_desktop_observation_request(request_text: str) -> bool:
    if not _looks_like_observation_only(request_text):
        return False
    return not _looks_like_desktop_file_listing(request_text)


def _intent_requests_pdf_summary(intent: OrchestrationIntent, request_text: str, objective: str) -> bool:
    if not (intent.file_path or intent.path or intent.folder_path):
        return False
    combined = " ".join(part for part in (request_text, objective, intent.objective or "") if part).lower()
    has_pdf_reference = "pdf" in combined or any(
        str(value).lower().endswith(".pdf") or "*.pdf" in str(value).lower()
        for value in (intent.file_path, intent.path, intent.folder_path)
        if value
    )
    if not has_pdf_reference:
        return False
    if intent.operation == "summarize_pdf":
        return True
    if intent.operation in {"inspect_file", "inspect_document", "extract_text", "open_file", "read_file", "read", "summarize"}:
        return True
    return any(marker in combined for marker in ("what the pdf is about", "tell me what", "summarize", "explain", "describe", "read it"))


def _browser_control_operation(operation: str | None, objective: str) -> str | None:
    raw = (operation or "").strip().lower()
    aliases = {
        "check": "check_page_update",
        "check_update": "check_page_update",
        "check_page": "check_page_update",
        "monitor": "check_page_update",
        "page_update": "check_page_update",
        "new_content": "check_page_update",
        "new_episode": "check_page_update",
        "form": "fill_form_step",
        "fill_form": "fill_form_step",
        "fill": "fill_form_step",
        "navigate_to": "navigate",
        "open": "navigate",
    }
    if raw in aliases:
        return aliases[raw]
    if _objective_wants_page_update_check(objective):
        return "check_page_update"
    if raw:
        return raw
    lowered = objective.lower()
    if any(marker in lowered for marker in ("new episode", "new content", "new release", "came out", "published")):
        return "check_page_update"
    return None


def _objective_wants_page_update_check(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("new episode", "new content", "new release", "came out", "published"))


def _looks_like_browser_prompt_interaction(objective: str) -> bool:
    lowered = objective.lower()
    has_browser_app = any(marker in lowered for marker in ("chatgpt", "claude", "gemini", "web form", "form website"))
    asks_to_enter = any(marker in lowered for marker in ("ask ", "type ", "enter ", "fill ", "prompt ", "send this", "with this information"))
    return has_browser_app and asks_to_enter


def _prompt_from_browser_prompt_interaction(objective: str) -> str | None:
    patterns = [
        r"\bask\s+(?:it\s+)?(?:to\s+)?(.+?)(?:\s+and\s+give\s+me\s+the\s+answer|[.!?]?$)",
        r"\bprompt\s*[:\-]\s*(.+)$",
        r"\btype\s+(.+?)(?:\s+into|\s+in|\s+on|[.!?]?$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, objective, flags=re.IGNORECASE)
        if match:
            prompt = re.sub(r"\s+", " ", match.group(1)).strip(" .,:;\"'")
            if prompt:
                return prompt
    return None


def _looks_like_submit_prompt(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("ask ", "send the prompt", "submit", "give me the answer"))


def _build_pdf_summary_intent_plan(
    settings: AppSettings,
    task: TaskRecord,
    intent: OrchestrationIntent,
    objective: str,
) -> PlanModel | None:
    policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if policy is None or not policy.enabled:
        return None
    source = intent.file_path or intent.path
    steps: list[PlanStep] = []
    required = [Capability.FILESYSTEM_WRITE]
    document_path = source
    search_root = intent.folder_path
    if source and any(marker in source for marker in ("*", "?", "[")):
        try:
            search_root = intent.folder_path or str(Path(source).expanduser().parent)
        except OSError:
            search_root = intent.folder_path
        document_path = None
    elif source:
        try:
            candidate = Path(source).expanduser()
            if candidate.is_dir():
                search_root = str(candidate)
                document_path = None
        except OSError:
            pass
    if not document_path and search_root:
        steps.append(
            PlanStep(
                title="Find PDF in folder",
                description="Locate a PDF under the requested folder before summarizing it.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="filesystem.manage",
                tool_input={
                    "operation": "search",
                    "root": search_root,
                    "query": ".pdf",
                    "include_content": False,
                    "max_results": 5,
                    "timeout_seconds": 60,
                },
                expected_output="Matching PDF paths under the requested folder.",
            )
        )
        document_path = "{{last_entry_path}}"
    if not document_path:
        return None
    steps.append(
        PlanStep(
            title="Summarize PDF",
            description="Extract text from the resolved PDF and summarize the actual document content.",
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            risk_level=RiskLevel.HIGH,
            requires_approval=policy.requires_approval,
            tool_name="document.manage",
            tool_input={"operation": "summarize_pdf", "path": document_path, "timeout_seconds": 90},
            expected_output="PDF content summary and text artifact.",
        )
    )
    return PlanModel(
        objective=objective,
        assumptions=["The LLM selected PDF summarization; any folder input is resolved to a PDF before document processing."],
        required_capabilities=required,
        steps=steps,
        success_criteria=["The PDF content is extracted and summarized."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.DOCUMENT_SUMMARY,
                description="The PDF content is summarized from the actual file.",
                required=True,
            )
        ],
    )


def _intent_delivery_step(
    settings: AppSettings,
    objective: str,
    operation: str,
    *,
    screenshot: bool = False,
    artifact_type: str | None = None,
) -> PlanStep:
    policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
    return PlanStep(
        title="Deliver task artifact to Telegram",
        description="Send the current-task artifact requested by the LLM route back to the source Telegram chat.",
        required_capabilities=[Capability.TELEGRAM_SEND],
        risk_level=RiskLevel.LOW,
        requires_approval=bool(policy and policy.requires_approval),
        tool_name="artifact.deliver",
        tool_input={
            "operation": "send_screenshot" if screenshot else operation,
            "caption": f"Result for: {objective[:180]}",
            **({"artifact_type": artifact_type} if artifact_type else {}),
            "timeout_seconds": 60,
        },
        expected_output="Telegram delivery result for the requested artifact.",
    )


def _build_adapter_factory_intent_plan(
    settings: AppSettings,
    task: TaskRecord,
    intent: OrchestrationIntent,
    objective: str,
) -> PlanModel | None:
    if not settings.adapters.adapter_factory.enabled:
        return None
    policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if policy is None or not policy.enabled:
        return None
    return PlanModel(
        objective=objective,
        assumptions=["The LLM router selected adapter factory work."],
        required_capabilities=[Capability.FILESYSTEM_WRITE],
        steps=[
            PlanStep(
                title="Scaffold generated adapter proposal",
                description="Create a reviewable adapter/tool proposal.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="adapter.factory",
                tool_input={
                    "operation": intent.operation if intent.operation in {"assess", "scaffold"} else "scaffold",
                    "objective": objective,
                    "scope_target": settings.adapters.adapter_factory.root_dir,
                    "timeout_seconds": 30,
                },
                expected_output="A generated adapter proposal directory or assessment.",
            )
        ],
        success_criteria=["A reviewable adapter proposal or assessment exists."],
        postconditions=_adapter_postconditions(),
    )


def _build_workspace_intent_plan(
    settings: AppSettings,
    task: TaskRecord,
    intent: OrchestrationIntent,
    objective: str,
) -> PlanModel | None:
    if not settings.adapters.workspace.enabled:
        return None
    policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if policy is None or not policy.enabled:
        return None
    operation = intent.operation if intent.operation in {"prepare", "write_files", "materialize_static_app", "launch_static", "web_app_preview"} else "web_app_preview"
    return PlanModel(
        objective=objective,
        assumptions=["The LLM router selected local workspace management."],
        required_capabilities=[Capability.FILESYSTEM_WRITE],
        steps=[
            PlanStep(
                title="Run workspace operation",
                description="Prepare, create, or launch local workspace artifacts.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="workspace.manage",
                tool_input={
                    "operation": operation,
                    "objective": objective,
                    "scope_target": settings.adapters.workspace.root_dir,
                    "web_port_start": settings.adapters.workspace.web_port_start,
                    "open_browser": settings.adapters.workspace.open_browser,
                    "timeout_seconds": 60,
                },
                expected_output="Workspace path, generated files, or preview URL.",
            )
        ],
        success_criteria=["Workspace operation reports local paths and any preview URL."],
        postconditions=_preview_postconditions() if operation in {"web_app_preview", "launch_static"} else _workspace_postconditions(),
    )


def _build_browser_prompt_interaction_plan(
    settings: AppSettings,
    task: TaskRecord,
    intent: OrchestrationIntent,
    request_text: str,
    objective: str,
) -> PlanModel | None:
    if not _looks_like_browser_prompt_interaction(request_text):
        return None
    open_policy = settings.capabilities.get(Capability.BROWSER_OPEN)
    control_policy = settings.capabilities.get(Capability.BROWSER_CONTROL)
    if (
        not settings.adapters.browser.enabled
        or open_policy is None
        or not open_policy.enabled
        or control_policy is None
        or not control_policy.enabled
    ):
        return None
    url = intent.url or _first_url_from_text(request_text) or ("https://chatgpt.com" if "chatgpt" in request_text.lower() else None)
    if not url:
        return None
    url = _normalize_url_for_plan(url)
    prompt = intent.query or _prompt_from_browser_prompt_interaction(request_text) or objective
    fields = intent.form_fields or {
        "message": prompt,
        "prompt": prompt,
        "textarea": prompt,
        "chat": prompt,
    }
    return PlanModel(
        objective=objective,
        assumptions=["The user asked to interact with a browser-based prompt or form, so the browser adapter opens the page and tries to enter the prompt."],
        required_capabilities=[Capability.BROWSER_OPEN, Capability.BROWSER_CONTROL],
        steps=[
            PlanStep(
                title="Open browser app",
                description="Open the requested browser application before entering the prompt.",
                required_capabilities=[Capability.BROWSER_OPEN],
                risk_level=RiskLevel.LOW,
                requires_approval=open_policy.requires_approval,
                tool_name="browser.open",
                tool_input={"operation": "open", "url": url, "new_tab": True, "timeout_seconds": 60},
                expected_output="Requested browser app is open.",
            ),
            PlanStep(
                title="Enter browser prompt",
                description="Fill the visible prompt field if one is available; report login or blocked states clearly.",
                required_capabilities=[Capability.BROWSER_CONTROL],
                risk_level=RiskLevel.CRITICAL,
                requires_approval=control_policy.requires_approval,
                tool_name="browser.control",
                tool_input={
                    "operation": "fill_form_step",
                    "url_contains": url,
                    "fields": fields,
                    "submit": bool(intent.submit or _looks_like_submit_prompt(request_text)),
                    "timeout_seconds": 90,
                },
                expected_output="Updated page state or a clear blocked/login-required summary.",
            ),
        ],
        success_criteria=["The prompt is entered when possible, or the browser state explains why it could not be entered."],
        postconditions=_browser_postconditions(),
    )


def build_default_vscode_development_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if task.metadata.get("task_type") != TaskType.DEVELOPMENT.value:
        return None

    explicit_copilot = _explicitly_requests_copilot(task.objective)
    adapter_plan = _build_adapter_factory_plan(settings, task)
    if adapter_plan is not None:
        return adapter_plan

    workspace_plan = _build_workspace_web_app_plan(settings, task, allow_copilot=explicit_copilot)
    if workspace_plan is not None:
        return workspace_plan

    if not explicit_copilot:
        return None

    policy = settings.capabilities.get(Capability.VSCODE_WRITE_FILES)
    if not settings.adapters.vscode.enabled or policy is None or not policy.enabled:
        return None

    workspace_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    workspace_enabled = bool(settings.adapters.workspace.enabled and workspace_policy and workspace_policy.enabled)
    workspace_dir = str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)) if workspace_enabled else None
    steps: list[PlanStep] = []
    required_capabilities = [Capability.VSCODE_WRITE_FILES]
    assumptions = [
        "The task came from an authorized Telegram message classified as development work.",
        "VS Code bridge write access is enabled for this run.",
    ]
    if workspace_enabled and workspace_policy is not None:
        required_capabilities.insert(0, Capability.FILESYSTEM_WRITE)
        assumptions.append(f"A task workspace will be prepared under {settings.adapters.workspace.root_dir}.")
        steps.append(
            PlanStep(
                title="Prepare local task workspace",
                description="Create a dedicated workspace directory for generated code, notes, and assistant output.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=workspace_policy.requires_approval,
                tool_name="workspace.manage",
                tool_input={
                    "operation": "prepare",
                    "objective": task.objective,
                    "scope_target": settings.adapters.workspace.root_dir,
                    "timeout_seconds": 30,
                },
                expected_output="A local workspace path for this task.",
            )
        )

    workspace_instruction = (
        f"Use this local workspace for files and commands when tool access allows it: {workspace_dir}. "
        if workspace_dir
        else "If code changes are needed, return exact file paths and contents because no local workspace is enabled. "
    )
    prompt = render_prompt(
        "tools/copilot_development.md",
        workspace_instruction=workspace_instruction,
        objective=task.objective,
    )
    steps.append(
        PlanStep(
            title="Send objective to VS Code Copilot terminal",
            description="Queue the task objective through the VS Code bridge and capture the terminal result when available.",
            required_capabilities=[Capability.VSCODE_WRITE_FILES],
            risk_level=RiskLevel.HIGH,
            requires_approval=policy.requires_approval,
            tool_name="vscode.copilot_terminal",
            tool_input={
                "prompt": prompt,
                "terminal_id": "agent-control-copilot",
                "cwd": workspace_dir,
                "capture_output": True,
                "timeout_seconds": 180,
            },
            expected_output="A VS Code bridge terminal output record tied to the queued command.",
        )
    )

    return PlanModel(
        objective=task.objective,
        assumptions=assumptions,
        required_capabilities=required_capabilities,
        steps=steps,
        success_criteria=["The VS Code bridge accepted the command and reported a final terminal output record."],
        postconditions=_workspace_postconditions() if workspace_enabled else [],
    )


def _build_workspace_web_app_plan(settings: AppSettings, task: TaskRecord, *, allow_copilot: bool = False) -> PlanModel | None:
    if not settings.adapters.workspace.enabled:
        return None
    if not _looks_like_launchable_web_app(task.objective):
        return None

    workspace_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if workspace_policy is None or not workspace_policy.enabled:
        return None

    vscode_policy = settings.capabilities.get(Capability.VSCODE_WRITE_FILES)
    vscode_enabled = bool(settings.adapters.vscode.enabled and vscode_policy and vscode_policy.enabled)
    if not vscode_enabled or not allow_copilot:
        return PlanModel(
            objective=task.objective,
            assumptions=[
                "The task asks for a visible local web-app result.",
                (
                    "Copilot was not explicitly requested, so the workspace preview generator will create the app directly."
                    if vscode_enabled
                    else "VS Code/Copilot is not enabled, so the workspace preview generator will create the app directly."
                ),
                f"Generated files are limited to {settings.adapters.workspace.root_dir}.",
            ],
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            steps=[
                PlanStep(
                    title="Create and launch local web app preview",
                    description="Create a task workspace, write a minimal web app, start a localhost static server, and return the URL.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=workspace_policy.requires_approval,
                    tool_name="workspace.manage",
                    tool_input={
                        "operation": "web_app_preview",
                        "objective": task.objective,
                        "scope_target": settings.adapters.workspace.root_dir,
                        "web_port_start": settings.adapters.workspace.web_port_start,
                        "open_browser": settings.adapters.workspace.open_browser,
                        "timeout_seconds": 60,
                    },
                    expected_output="A local workspace path plus an HTTP preview URL.",
                )
            ],
            success_criteria=["The generated web app is available at a localhost URL and the workspace path is reported."],
            postconditions=_preview_postconditions(),
        )

    workspace_dir = str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id))
    copilot_prompt = _web_app_copilot_prompt(task.objective, workspace_dir)
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The task asks for a visible local web-app result.",
            "Copilot is the primary creator for the app files.",
            "If Copilot cannot write files directly, its code-block output will be materialized into the task workspace.",
            f"Generated files are limited to {settings.adapters.workspace.root_dir}.",
        ],
        required_capabilities=[Capability.FILESYSTEM_WRITE, Capability.VSCODE_WRITE_FILES],
        steps=[
            PlanStep(
                title="Prepare local task workspace",
                description="Create the dedicated workspace directory Copilot should use for generated app files.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=workspace_policy.requires_approval,
                tool_name="workspace.manage",
                tool_input={
                    "operation": "prepare",
                    "objective": task.objective,
                    "scope_target": settings.adapters.workspace.root_dir,
                    "timeout_seconds": 30,
                },
                expected_output="A local workspace path for this task.",
            ),
            PlanStep(
                title="Ask Copilot to create the web app",
                description="Send a concrete implementation prompt to Copilot with the task workspace as the working directory.",
                required_capabilities=[Capability.VSCODE_WRITE_FILES],
                risk_level=RiskLevel.HIGH,
                requires_approval=bool(vscode_policy and vscode_policy.requires_approval),
                tool_name="vscode.copilot_terminal",
                tool_input={
                    "prompt": copilot_prompt,
                    "terminal_id": "agent-control-copilot",
                    "cwd": workspace_dir,
                    "capture_output": True,
                    "require_file_blocks": True,
                    "timeout_seconds": 240,
                },
                expected_output="Copilot creates files in the workspace or returns complete file code blocks.",
            ),
            PlanStep(
                title="Materialize Copilot app files",
                description="Ensure the workspace contains a static web app, using Copilot output when direct file writes were unavailable.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=workspace_policy.requires_approval,
                tool_name="workspace.manage",
                tool_input={
                    "operation": "materialize_static_app",
                    "objective": task.objective,
                    "source_text": "{{last_output}}",
                    "allow_fallback_template": True,
                    "require_index": True,
                    "scope_target": settings.adapters.workspace.root_dir,
                    "timeout_seconds": 30,
                },
                expected_output="Static app files are present in the workspace.",
            ),
            PlanStep(
                title="Launch local web app preview",
                description="Serve the workspace on localhost and return the URL.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=workspace_policy.requires_approval,
                tool_name="workspace.manage",
                tool_input={
                    "operation": "launch_static",
                    "objective": task.objective,
                    "scope_target": settings.adapters.workspace.root_dir,
                    "web_port_start": settings.adapters.workspace.web_port_start,
                    "open_browser": settings.adapters.workspace.open_browser,
                    "ensure_index": False,
                    "timeout_seconds": 60,
                },
                expected_output="A local workspace path plus an HTTP preview URL.",
            ),
        ],
        success_criteria=[
            "Copilot has been used as the creator for the requested app.",
            "The generated web app is available at a localhost URL and the workspace path is reported.",
        ],
        postconditions=_preview_postconditions(),
    )


def _build_browser_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.browser.enabled:
        return None
    if not _looks_like_browser_request(task.objective):
        return None

    objective = task.objective.strip()
    open_policy = settings.capabilities.get(Capability.BROWSER_OPEN)
    control_policy = settings.capabilities.get(Capability.BROWSER_CONTROL)
    open_enabled = bool(open_policy and open_policy.enabled)
    control_enabled = bool(control_policy and control_policy.enabled)

    if _looks_like_browser_control_request(objective) and control_enabled and control_policy is not None:
        if _looks_like_form_fill_request(objective):
            url = _first_url_from_text(objective)
            steps: list[PlanStep] = []
            required_capabilities = [Capability.BROWSER_CONTROL]
            if url and open_enabled and open_policy is not None:
                required_capabilities.insert(0, Capability.BROWSER_OPEN)
                steps.append(
                    PlanStep(
                        title="Open form page in Chrome",
                        description="Open the requested form page before extracting fields.",
                        required_capabilities=[Capability.BROWSER_OPEN],
                        risk_level=RiskLevel.LOW,
                        requires_approval=open_policy.requires_approval,
                        tool_name="browser.open",
                        tool_input={"operation": "open", "url": url, "new_tab": True, "timeout_seconds": 60},
                        expected_output="Requested page is open in Chrome.",
                    )
                )
            steps.extend(
                [
                    PlanStep(
                        title="Extract form fields",
                        description="Inspect the page form state before filling fields.",
                        required_capabilities=[Capability.BROWSER_CONTROL],
                        risk_level=RiskLevel.CRITICAL,
                        requires_approval=control_policy.requires_approval,
                        tool_name="browser.control",
                        tool_input={
                            "operation": "extract_page_state",
                            **({"url_contains": url} if url else {}),
                            "timeout_seconds": 60,
                        },
                        expected_output="Detected form fields.",
                    ),
                    PlanStep(
                        title="Fill form fields",
                        description="Fill detected form fields using provided user information without submitting unless requested.",
                        required_capabilities=[Capability.BROWSER_CONTROL],
                        risk_level=RiskLevel.CRITICAL,
                        requires_approval=control_policy.requires_approval,
                        tool_name="browser.control",
                        tool_input={
                            "operation": "fill_form_step",
                            "fields": _form_fields_from_objective(objective),
                            "submit": _objective_wants_submit(objective),
                            **({"url_contains": url} if url else {}),
                            "timeout_seconds": 60,
                        },
                        expected_output="Filled form field state.",
                    ),
                ]
            )
            if _objective_wants_screenshot(objective) and url and open_enabled and open_policy is not None:
                steps.append(
                    PlanStep(
                        title="Capture filled form screenshot",
                        description="Capture the browser state after filling the form, without submitting it.",
                        required_capabilities=[Capability.BROWSER_OPEN],
                        risk_level=RiskLevel.LOW,
                        requires_approval=open_policy.requires_approval,
                        tool_name="browser.open",
                        tool_input={
                            "operation": "screenshot",
                            "url_contains": url,
                            "full_page": True,
                            "timeout_seconds": 60,
                        },
                        expected_output="A screenshot of the filled form for review.",
                    )
                )
                delivery_step = _artifact_delivery_step(settings, task.objective, screenshot=True)
                if delivery_step is not None:
                    steps.append(delivery_step)
                    required_capabilities.append(Capability.TELEGRAM_SEND)
            return PlanModel(
                objective=task.objective,
                assumptions=["The request asks to fill a browser form through Chrome DevTools."],
                required_capabilities=required_capabilities,
                steps=steps,
                success_criteria=["The form state is inspected and requested fields are filled."],
                postconditions=_browser_postconditions(),
            )
        operation, tool_input = _browser_control_input(objective)
        tool_input = {"operation": operation, **tool_input}
        url = _first_url_from_text(objective)
        steps: list[PlanStep] = []
        required_capabilities = [Capability.BROWSER_CONTROL]
        if url and open_enabled and open_policy is not None:
            required_capabilities.insert(0, Capability.BROWSER_OPEN)
            steps.append(
                PlanStep(
                    title="Open requested page in Chrome",
                    description="Open the requested URL before applying the browser control action.",
                    required_capabilities=[Capability.BROWSER_OPEN],
                    risk_level=RiskLevel.LOW,
                    requires_approval=open_policy.requires_approval,
                    tool_name="browser.open",
                    tool_input={
                        "operation": "open",
                        "url": url,
                        "new_tab": True,
                        "timeout_seconds": 60,
                    },
                    expected_output="Requested page is open in a DevTools-controlled Chrome tab.",
                )
            )
            if "tab_id" not in tool_input and "url_contains" not in tool_input and "title_contains" not in tool_input:
                tool_input["url_contains"] = url
        steps.append(
            PlanStep(
                title="Control Chrome through browser adapter",
                description="Use the Chrome DevTools browser adapter for the requested browser control action.",
                required_capabilities=[Capability.BROWSER_CONTROL],
                risk_level=RiskLevel.CRITICAL,
                requires_approval=control_policy.requires_approval,
                tool_name="browser.control",
                tool_input={
                    **tool_input,
                    "objective": objective,
                    "timeout_seconds": 60,
                },
                expected_output="Updated browser state and a concise result summary.",
            )
        )
        return PlanModel(
            objective=task.objective,
            assumptions=[
                "The request asks for an explicit browser control action.",
                "Only Chrome tabs exposed through the configured DevTools remote debugging port can be controlled.",
            ],
            required_capabilities=required_capabilities,
            steps=steps,
            success_criteria=["The requested Chrome control action is performed or a clear adapter error is reported."],
            postconditions=_browser_postconditions(),
        )

    if not open_enabled or open_policy is None:
        return None

    operation, tool_input = _browser_open_input(objective)
    steps = [
        PlanStep(
            title="Use Chrome browser adapter",
            description="Open, search, inspect, or screenshot Chrome through the registered browser adapter.",
            required_capabilities=[Capability.BROWSER_OPEN],
            risk_level=RiskLevel.LOW,
            requires_approval=open_policy.requires_approval,
            tool_name="browser.open",
            tool_input={
                "operation": operation,
                **tool_input,
                "timeout_seconds": 90,
            },
            expected_output="Browser URL, page title, visible-page summary, tab state, or screenshot path.",
        )
    ]
    delivery_step = _artifact_delivery_step(settings, task.objective, screenshot=operation == "screenshot")
    if delivery_step is not None:
        steps.append(delivery_step)
    postconditions = _browser_postconditions()
    if delivery_step is not None:
        postconditions.append(
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="A browser screenshot or task artifact is delivered back to Telegram.",
            )
        )
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The request asks to use the browser or inspect browser-visible information.",
            "Chrome will be launched with remote debugging if it is not already available.",
            "Normal Chrome windows not launched with remote debugging are not visible to this adapter.",
        ],
        required_capabilities=[
            Capability.BROWSER_OPEN,
            *([Capability.TELEGRAM_SEND] if delivery_step is not None else []),
        ],
        steps=steps,
        success_criteria=["The browser adapter reports the requested browser state or page summary."],
        postconditions=postconditions,
    )


def _build_document_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    request_text = _task_request_text(task)
    if not _looks_like_document_request(request_text) and not _looks_like_document_request(task.objective):
        return None
    if _looks_like_latest_output_delivery_request(request_text) and not any(
        marker in request_text.lower() for marker in ("powerpoint", "presentation", "pptx")
    ):
        return None
    policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if policy is None or not policy.enabled:
        return None
    lowered = request_text.lower()
    path = _document_path_from_objective(request_text) or _document_path_from_objective(task.objective)
    steps: list[PlanStep] = []
    required = [Capability.FILESYSTEM_WRITE]
    provider = _explicit_coding_provider(request_text) or _explicit_coding_provider(task.objective)
    terminal_policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if provider and terminal_policy and terminal_policy.enabled and settings.adapters.coding_agent.enabled:
        required.append(Capability.TERMINAL_RUN)
        steps.append(
            PlanStep(
                title=f"Ask {provider} for document guidance",
                description="Use the explicitly requested coding agent for planning/content guidance before creating the document artifact.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.HIGH,
                requires_approval=terminal_policy.requires_approval,
                tool_name="coding.agent",
                tool_input={
                    "operation": "run_goal",
                    "provider": provider,
                    "objective": request_text,
                    "prompt": request_text,
                    "workspace_dir": str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)),
                    "timeout_seconds": settings.adapters.coding_agent.timeout_seconds,
                },
                expected_output="Coding-agent guidance for the requested document.",
            )
        )
    if "pdf" in lowered and ("summarize" in lowered or "about" in lowered or "tell me" in lowered):
        if not path:
            return None
        steps.append(
            PlanStep(
                title="Summarize PDF",
                description="Extract text from the requested PDF and return a concise summary.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="document.manage",
                tool_input={"operation": "summarize_pdf", "path": path, "timeout_seconds": 90},
                expected_output="A PDF summary and summary artifact.",
            )
        )
        postconditions = [
            PlanPostcondition(
                type=PostconditionType.DOCUMENT_SUMMARY,
                description="A PDF summary is reported.",
            )
        ]
    elif "powerpoint" in lowered or "presentation" in lowered or "pptx" in lowered:
        operation = "update_presentation" if any(word in lowered for word in ("update", "revise", "change", "edit")) and path else "create_presentation"
        content_source = "{{last_output}}" if provider else task.objective
        tool_input: dict[str, object] = {
            "operation": operation,
            "title": _presentation_title(request_text),
            "content": content_source,
            "instructions": content_source,
            "timeout_seconds": 90,
        }
        if path:
            tool_input["path"] = path
        steps.append(
            PlanStep(
                title="Create presentation artifact" if operation == "create_presentation" else "Create revised presentation artifact",
                description="Create a PowerPoint file as a task artifact.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="document.manage",
                tool_input=tool_input,
                expected_output="A PowerPoint file path and artifact ID.",
            )
        )
        postconditions = [
            PlanPostcondition(
                type=PostconditionType.PRESENTATION_FILE,
                description="A PowerPoint file artifact is reported.",
            )
        ]
    else:
        return None

    delivery_step = _artifact_delivery_step(settings, request_text, screenshot=False)
    if delivery_step is not None:
        steps.append(delivery_step)
        required.append(Capability.TELEGRAM_SEND)
        postconditions.append(
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="The document artifact is delivered to Telegram.",
            )
        )
    return PlanModel(
        objective=task.objective,
        assumptions=["Document work is handled through file APIs before desktop UI automation."],
        required_capabilities=required,
        steps=steps,
        success_criteria=["The requested document output is created or summarized."],
        postconditions=postconditions,
    )


def _build_coding_agent_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    provider = _explicit_coding_provider(task.objective)
    if provider is None:
        return None
    policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if policy is None or not policy.enabled or not settings.adapters.coding_agent.enabled:
        return None
    steps: list[PlanStep] = []
    required = [Capability.TERMINAL_RUN]
    browser_policy = settings.capabilities.get(Capability.BROWSER_OPEN)
    if _looks_like_web_research_request(task.objective) and browser_policy and browser_policy.enabled:
        required.append(Capability.BROWSER_OPEN)
        steps.append(
            PlanStep(
                title="Collect web research context",
                description="Use the browser adapter to collect source summaries before handing context to the coding agent.",
                required_capabilities=[Capability.BROWSER_OPEN],
                risk_level=RiskLevel.LOW,
                requires_approval=browser_policy.requires_approval,
                tool_name="browser.open",
                tool_input={
                    "operation": "research_pages",
                    "query": _query_for_browser(task.objective),
                    "objective": task.objective,
                    "page_limit": _page_limit_from_objective(task.objective),
                    "timeout_seconds": 180,
                },
                expected_output="Visited URLs and page summaries for coding-agent context.",
            )
        )
    operation = "plan" if _looks_like_large_coding_request(task.objective) else "run_goal"
    steps.append(
        PlanStep(
            title=f"Run {provider} coding agent",
            description="Run the explicitly requested coding agent in a task workspace.",
            required_capabilities=[Capability.TERMINAL_RUN],
            risk_level=RiskLevel.HIGH,
            requires_approval=policy.requires_approval,
            tool_name="coding.agent",
            tool_input={
                "operation": operation,
                "provider": provider,
                "objective": task.objective,
                "prompt": task.objective,
                "workspace_dir": str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)),
                "timeout_seconds": settings.adapters.coding_agent.timeout_seconds,
            },
            expected_output="Coding-agent stdout, stderr, workspace, status, and limit state.",
        )
    )
    if operation == "plan":
        steps.append(
            PlanStep(
                title=f"Run first {provider} implementation step",
                description="Begin implementation after the planning response is captured.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.HIGH,
                requires_approval=policy.requires_approval,
                tool_name="coding.agent",
                tool_input={
                    "operation": "run_step",
                    "provider": provider,
                    "objective": task.objective,
                    "prompt": "Use the previous plan output and implement the first safe step.\n\n{{last_output}}",
                    "workspace_dir": str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)),
                    "step_index": 0,
                    "timeout_seconds": settings.adapters.coding_agent.timeout_seconds,
                },
                expected_output="The first implementation step result.",
            )
        )
    return PlanModel(
        objective=task.objective,
        assumptions=[
            f"The user explicitly requested {provider}.",
            "Coding-agent work is run in a task workspace and captures stdout, stderr, status, and limits.",
        ],
        required_capabilities=required,
        steps=steps,
        success_criteria=["The explicit coding agent runs or reports availability/limit failure clearly."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.CODING_AGENT_STEP,
                description="The coding agent completed a step or reported a limit state.",
            )
        ],
    )


def _build_schedule_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not _looks_like_schedule_request(task.objective):
        return None
    policy = settings.capabilities.get(Capability.SCHEDULE_MANAGE)
    if policy is None or not policy.enabled or not settings.scheduler.enabled:
        return None

    steps: list[PlanStep] = []
    required = [Capability.SCHEDULE_MANAGE]
    operation = _schedule_operation(task.objective)
    schedule_id = _schedule_id_from_text(task.objective)
    if operation != "create":
        if operation != "list" and schedule_id is None:
            return None
        steps.append(
            PlanStep(
                title=f"{operation.replace('_', ' ').title()} schedule",
                description="Manage an existing recurring schedule.",
                required_capabilities=[Capability.SCHEDULE_MANAGE],
                risk_level=RiskLevel.MEDIUM,
                requires_approval=policy.requires_approval,
                tool_name="schedule.manage",
                tool_input={
                    "operation": operation,
                    **({"schedule_id": schedule_id} if schedule_id else {}),
                    "timeout_seconds": 30,
                },
                expected_output="Updated schedule state.",
            )
        )
        return PlanModel(
            objective=task.objective,
            assumptions=["The request asks to manage an existing schedule."],
            required_capabilities=required,
            steps=steps,
            success_criteria=["The schedule management operation returns the updated state."],
            postconditions=[],
        )

    assumptions = [
        "The request asks for recurring work.",
        "The scheduler creates normal tasks from the saved objective when the job is due.",
    ]
    provider = _explicit_coding_provider(task.objective)
    terminal_policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if provider and terminal_policy and terminal_policy.enabled and settings.adapters.coding_agent.enabled:
        required.insert(0, Capability.TERMINAL_RUN)
        assumptions.append(f"The user explicitly asked {provider} to prepare work for the schedule.")
        steps.append(
            PlanStep(
                title=f"Prepare scheduled-job workspace with {provider}",
                description="Use the explicitly requested coding agent to prepare any code or notes needed by the scheduled job.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.HIGH,
                requires_approval=terminal_policy.requires_approval,
                tool_name="coding.agent",
                tool_input={
                    "operation": "run_step",
                    "provider": provider,
                    "objective": task.objective,
                    "prompt": task.objective,
                    "workspace_dir": str(workspace_dir_for_task(settings.adapters.workspace.root_dir, task.id)),
                    "timeout_seconds": settings.adapters.coding_agent.timeout_seconds,
                },
                expected_output="Workspace, stdout/stderr, and any usage-limit state from the requested coding provider.",
            )
        )

    cadence = cadence_from_text(task.objective)
    scheduled_objective = objective_from_schedule_text(task.objective)
    metadata = {
        "created_from_task_id": task.id,
        "created_from_objective": task.objective,
    }
    if provider:
        metadata["coding_provider"] = provider
    steps.append(
        PlanStep(
            title="Create recurring schedule",
            description="Save the recurring objective so the scheduler can create due tasks later.",
            required_capabilities=[Capability.SCHEDULE_MANAGE],
            risk_level=RiskLevel.MEDIUM,
            requires_approval=policy.requires_approval,
            tool_name="schedule.manage",
            tool_input={
                "operation": "create",
                "objective": scheduled_objective,
                "cadence": cadence,
                "timezone": settings.scheduler.default_timezone,
                "source_chat_id": task.metadata.get("source_chat_id"),
                "metadata": metadata,
                "timeout_seconds": 30,
            },
            expected_output="A schedule ID and next run timestamp.",
        )
    )
    return PlanModel(
        objective=task.objective,
        assumptions=assumptions,
        required_capabilities=required,
        steps=steps,
        success_criteria=["The schedule is saved and reports its next due run."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.SCHEDULE_CREATED,
                description="A schedule ID and next run timestamp are reported.",
            )
        ],
    )


def _build_computer_use_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.computer_use.enabled or not settings.adapters.desktop.control_enabled:
        return None
    if not _looks_like_computer_use_request(task.objective):
        return None
    policy = settings.capabilities.get(Capability.DESKTOP_CONTROL)
    if policy is None or not policy.enabled:
        return None

    objective = task.objective.strip()
    operation = "observe" if _looks_like_observation_only(objective) else "run_goal"
    action = _deterministic_computer_action(objective)
    if action is not None:
        operation = "act"
    tool_input = (
        {
            "operation": "observe",
            "objective": objective,
            "include_screenshot": True,
            "include_ui_tree": True,
            "summarize": True,
            "timeout_seconds": 60,
        }
        if operation == "observe"
        else {
            "operation": "act",
            "objective": objective,
            "action": action,
            "timeout_seconds": 60,
        }
        if operation == "act"
        else {
            "operation": "run_goal",
            "objective": objective,
            "max_steps": settings.adapters.computer_use.max_steps,
            "include_ui_tree": True,
            "require_vision": True,
            "timeout_seconds": 240,
        }
    )
    steps = [
        PlanStep(
            title="Run bounded computer-use session",
            description="Observe the Windows desktop and, when needed, perform bounded local mouse/keyboard actions.",
            required_capabilities=[Capability.DESKTOP_CONTROL],
            risk_level=RiskLevel.CRITICAL,
            requires_approval=policy.requires_approval,
            tool_name="computer.use",
            tool_input=tool_input,
            expected_output="Desktop observation, screenshot path, actions taken, and final summary.",
        )
    ]
    delivery_step = _artifact_delivery_step(settings, task.objective, screenshot=True)
    if delivery_step is not None:
        steps.append(delivery_step)
    postconditions = _desktop_postconditions()
    if delivery_step is not None:
        postconditions.append(
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="A screenshot or task artifact is delivered back to Telegram.",
            )
        )
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The task needs local desktop observation or UI control.",
            "Computer use is bounded by the configured max step count.",
            "Desktop-control access mode determines whether approval is required.",
        ],
        required_capabilities=[
            Capability.DESKTOP_CONTROL,
            *([Capability.TELEGRAM_SEND] if delivery_step is not None else []),
        ],
        steps=steps,
        success_criteria=["The desktop state was observed or the requested bounded UI action was completed."],
        postconditions=postconditions,
    )


def _build_file_lookup_delivery_plan(
    settings: AppSettings,
    task: TaskRecord,
    *,
    intent: OrchestrationIntent | None = None,
) -> PlanModel | None:
    request_text = _task_request_text(task)
    if _objective_wants_screenshot(request_text):
        return None
    intent_path = (intent.file_path or intent.path) if intent else None
    if (intent_path and _usable_explicit_path(intent_path)) or _path_from_objective(request_text):
        return None
    if not _looks_like_delivery_request(request_text) and intent is None:
        return None
    if not _looks_like_file_reference(request_text) and not (intent and intent.artifact_type):
        return None
    root = (intent.folder_path if intent else None) or _root_from_objective(request_text)
    if not root:
        return None

    write_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    send_policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
    if write_policy is None or not write_policy.enabled or send_policy is None or not send_policy.enabled:
        return None

    query = (
        (intent.query if intent else None)
        or (_query_from_file_reference(intent_path) if intent_path else None)
        or _search_query_from_objective(request_text)
    )
    if query == "*" and _looks_like_file_reference(request_text):
        query = _file_query_from_reference(request_text)
    steps = [
        PlanStep(
            title="Find requested file",
            description="Search the requested safe folder before delivering a file whose exact path was not provided.",
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            risk_level=RiskLevel.HIGH,
            requires_approval=write_policy.requires_approval,
            tool_name="filesystem.manage",
            tool_input={
                "operation": "search",
                "root": root,
                "query": query or "*",
                "include_content": True,
                "max_results": 20,
                "timeout_seconds": 90,
            },
            expected_output="Matching file paths under the configured allowed root.",
        ),
        PlanStep(
            title="Deliver resolved file",
            description="Send the first resolved matching file back to the source Telegram chat.",
            required_capabilities=[Capability.TELEGRAM_SEND],
            risk_level=RiskLevel.LOW,
            requires_approval=send_policy.requires_approval,
            tool_name="artifact.deliver",
            tool_input={
                "operation": "send_file",
                "path": "{{last_entry_path}}",
                "artifact_type": (intent.artifact_type if intent else None) or "document",
                "caption": f"Result for: {request_text[:180]}",
                "timeout_seconds": 60,
            },
            expected_output="Telegram delivery result for the resolved file.",
        ),
    ]
    return PlanModel(
        objective=request_text,
        assumptions=[
            "The user asked for a file without an exact path.",
            "The file is resolved through scoped filesystem search before Telegram delivery.",
        ],
        required_capabilities=[Capability.FILESYSTEM_WRITE, Capability.TELEGRAM_SEND],
        steps=steps,
        success_criteria=["A matching file is found and delivered, or a clear no-match error is reported."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="The resolved file is delivered to Telegram.",
                required=True,
            ),
        ],
    )


def _build_create_and_deliver_file_plan(
    settings: AppSettings,
    task: TaskRecord,
    path: str,
    request_text: str,
) -> PlanModel | None:
    write_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    send_policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
    if write_policy is None or not write_policy.enabled or send_policy is None or not send_policy.enabled:
        return None
    content = _text_file_content_from_request(request_text)
    return PlanModel(
        objective=request_text,
        assumptions=[
            "The user asked to create a local text artifact and send it.",
            "The file is written through scoped filesystem APIs before delivery.",
        ],
        required_capabilities=[Capability.FILESYSTEM_WRITE, Capability.TELEGRAM_SEND],
        steps=[
            PlanStep(
                title="Create requested text file",
                description="Write the requested text output at the explicit path before delivery.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=write_policy.requires_approval,
                tool_name="filesystem.manage",
                tool_input={
                    "operation": "write_text_file",
                    "path": path,
                    "content": content,
                    "overwrite": False,
                    "timeout_seconds": 60,
                },
                expected_output="Created file path and changed path.",
            ),
            PlanStep(
                title="Deliver created file",
                description="Send the newly created file back to the source Telegram chat.",
                required_capabilities=[Capability.TELEGRAM_SEND],
                risk_level=RiskLevel.LOW,
                requires_approval=send_policy.requires_approval,
                tool_name="artifact.deliver",
                tool_input={
                    "operation": "send_file",
                    "path": path,
                    "artifact_type": "document",
                    "caption": f"Result for: {request_text[:180]}",
                    "timeout_seconds": 60,
                },
                expected_output="Telegram delivery result for the created file.",
            ),
        ],
        success_criteria=["The text file is created under an allowed root and delivered to Telegram."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.FILE_ORGANIZATION,
                description="The created file path is reported.",
            ),
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="The created file is delivered to Telegram.",
                required=True,
            ),
        ],
    )


def _build_web_note_delivery_plan(
    settings: AppSettings,
    task: TaskRecord,
    intent: OrchestrationIntent,
    request_text: str,
    objective: str,
) -> PlanModel | None:
    open_policy = settings.capabilities.get(Capability.BROWSER_OPEN)
    write_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    send_policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
    if (
        open_policy is None
        or not open_policy.enabled
        or write_policy is None
        or not write_policy.enabled
        or send_policy is None
        or not send_policy.enabled
    ):
        return None
    folder = intent.folder_path or intent.file_path or intent.path or _path_from_objective(request_text)
    if not folder:
        return None
    try:
        output_path = Path(folder).expanduser()
        if output_path.suffix:
            note_path = str(output_path)
        else:
            note_path = str(output_path / "web-research-note.txt")
    except OSError:
        return None
    query = intent.query or _web_query_from_request(request_text) or objective
    return PlanModel(
        objective=objective,
        assumptions=[
            "The request combines web research, local note creation, and delivery.",
            "The browser adapter gathers the source summary; filesystem APIs write the note; delivery sends the file.",
        ],
        required_capabilities=[Capability.BROWSER_OPEN, Capability.FILESYSTEM_WRITE, Capability.TELEGRAM_SEND],
        steps=[
            PlanStep(
                title="Research requested topic",
                description="Search/read the requested source through the browser adapter.",
                required_capabilities=[Capability.BROWSER_OPEN],
                risk_level=RiskLevel.LOW,
                requires_approval=open_policy.requires_approval,
                tool_name="browser.open",
                tool_input={
                    "operation": "research",
                    "query": query,
                    "objective": request_text,
                    "open_first_result": True,
                    "timeout_seconds": 120,
                },
                expected_output="Browser page title, URL, and summary.",
            ),
            PlanStep(
                title="Write research note",
                description="Persist a short note from the browser result under the requested folder.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=write_policy.requires_approval,
                tool_name="filesystem.manage",
                tool_input={
                    "operation": "write_text_file",
                    "path": note_path,
                    "content": "{{last_output}}",
                    "overwrite": False,
                    "timeout_seconds": 60,
                },
                expected_output="Created note path and changed path.",
            ),
            PlanStep(
                title="Deliver research note",
                description="Send the created note back to Telegram.",
                required_capabilities=[Capability.TELEGRAM_SEND],
                risk_level=RiskLevel.LOW,
                requires_approval=send_policy.requires_approval,
                tool_name="artifact.deliver",
                tool_input={
                    "operation": "send_file",
                    "path": note_path,
                    "artifact_type": "document",
                    "caption": f"Result for: {request_text[:180]}",
                    "timeout_seconds": 60,
                },
                expected_output="Telegram delivery result for the research note.",
            ),
        ],
        success_criteria=["The web result is summarized, written to a local note, and delivered."],
        postconditions=[
            PlanPostcondition(type=PostconditionType.BROWSER_STATE, description="Browser source evidence is reported."),
            PlanPostcondition(type=PostconditionType.FILE_ORGANIZATION, description="The note file path is reported."),
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="The generated note is delivered to Telegram.",
                required=True,
            ),
        ],
    )


def _build_code_interpreter_recovery_plan(settings: AppSettings, task: TaskRecord, failure_reason: str) -> PlanModel | None:
    policy = settings.capabilities.get(Capability.TERMINAL_RUN)
    if policy is None or not policy.enabled or not settings.adapters.code_interpreter.enabled:
        return None
    objective = (
        "Recover this failed task with a small bounded Python script only if a script is appropriate. "
        f"Original task: {task.objective}. Failure: {failure_reason}. "
        "Write outputs inside the managed workspace and print a concise summary."
    )
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The evaluator selected the local code interpreter for a bounded repair attempt.",
            "The script must operate inside the managed interpreter workspace.",
        ],
        required_capabilities=[Capability.TERMINAL_RUN],
        steps=[
            PlanStep(
                title="Run evaluator repair script",
                description="Use the local code interpreter to generate and run a small repair or diagnostic script.",
                required_capabilities=[Capability.TERMINAL_RUN],
                risk_level=RiskLevel.MEDIUM,
                requires_approval=policy.requires_approval,
                tool_name="code.interpreter",
                tool_input={
                    "operation": "generate_and_run",
                    "objective": objective,
                    "workspace_dir": str(workspace_dir_for_task(settings.adapters.code_interpreter.workspace_root, task.id)),
                    "timeout_seconds": settings.adapters.code_interpreter.timeout_seconds,
                },
                expected_output="Interpreter stdout, stderr, changed files, and workspace path.",
            )
        ],
        success_criteria=["The evaluator repair script reports concrete output or a clear reason it cannot repair the task."],
        postconditions=_workspace_postconditions(),
    )


def _build_filesystem_manage_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.computer_use.enabled:
        return None
    if _looks_like_delivery_request(task.objective) and _path_from_objective(task.objective):
        return None
    if not _looks_like_filesystem_manage_request(task.objective):
        return None
    root = _path_from_objective(task.objective) or _folder_root_from_request(_task_request_text(task)) or _root_from_objective(task.objective)
    if root is None:
        return None
    write_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if write_policy is None or not write_policy.enabled:
        return None

    explicit_path = _path_from_objective(task.objective)
    if explicit_path and _looks_like_find_and_read_request(_task_request_text(task), task.objective):
        return PlanModel(
            objective=task.objective,
            assumptions=["The request asks to read an explicit file path through scoped filesystem APIs."],
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            steps=[
                PlanStep(
                    title="Read scoped file",
                    description="Read the requested file content through filesystem APIs.",
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": "read_file",
                        "path": explicit_path,
                        "max_chars": 16000,
                        "timeout_seconds": 60,
                    },
                    expected_output="Readable file content and a concise summary.",
                )
            ],
            success_criteria=["The file content is returned or a clear extraction error is reported."],
            postconditions=_file_organization_postconditions(required=False),
        )

    if _looks_like_file_search(task.objective) or _looks_like_desktop_file_listing(task.objective):
        operation = "inspect_folder" if _looks_like_desktop_file_listing(task.objective) else "search"
        query = _search_query_from_objective(task.objective)
        return PlanModel(
            objective=task.objective,
            assumptions=["The request is safer through scoped filesystem APIs than desktop UI control."],
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            steps=[
                PlanStep(
                    title="Inspect scoped folder" if operation == "inspect_folder" else "Search scoped folder",
                    description=(
                        "List files and folders inside the requested allowed folder."
                        if operation == "inspect_folder"
                        else "Search file names and optional text content inside the requested allowed folder."
                    ),
                    required_capabilities=[Capability.FILESYSTEM_WRITE],
                    risk_level=RiskLevel.HIGH,
                    requires_approval=write_policy.requires_approval,
                    tool_name="filesystem.manage",
                    tool_input={
                        "operation": operation,
                        "root": root,
                        **({"query": query, "include_content": True} if operation == "search" else {}),
                        "timeout_seconds": 60,
                    },
                    expected_output="Folder entries or matching file paths under the configured allowed root.",
                )
            ],
            success_criteria=["Folder entries or search results are reported."],
            postconditions=_file_organization_postconditions(required=False),
        )

    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The request asks to organize files in a folder.",
            "The adapter will first create a manifest, then apply exactly that manifest.",
        ],
        required_capabilities=[Capability.FILESYSTEM_WRITE],
        steps=[
            PlanStep(
                title="Plan folder organization",
                description="Create a deterministic move manifest without changing files.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=write_policy.requires_approval,
                tool_name="filesystem.manage",
                tool_input={
                    "operation": "organize_plan",
                    "root": root,
                    "strategy": "by_type",
                    "recursive": False,
                    "timeout_seconds": 60,
                },
                expected_output="A proposed file move manifest.",
            ),
            PlanStep(
                title="Apply folder organization manifest",
                description="Apply the previously generated manifest inside the same allowed folder.",
                required_capabilities=[Capability.FILESYSTEM_WRITE],
                risk_level=RiskLevel.HIGH,
                requires_approval=write_policy.requires_approval,
                tool_name="filesystem.manage",
                tool_input={
                    "operation": "apply_manifest",
                    "root": root,
                    "manifest": "{{last_manifest}}",
                    "dry_run": False,
                    "timeout_seconds": 120,
                },
                expected_output="Moved or copied file paths.",
            ),
        ],
        success_criteria=["The folder organization manifest was applied and changed paths were reported."],
        postconditions=_file_organization_postconditions(),
    )


def _build_artifact_delivery_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not _looks_like_delivery_request(task.objective):
        return None
    policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
    if policy is None or not policy.enabled:
        return None
    lowered = task.objective.lower()
    path = _path_from_objective(task.objective)
    operation = (
        "send_file"
        if path
        else "send_latest"
        if _looks_like_latest_output_delivery_request(task.objective)
        else "send_screenshot"
        if "screenshot" in lowered or "screen shot" in lowered
        else "send_latest"
    )
    tool_input: dict[str, object] = {
        "operation": operation,
        "caption": f"Result for: {task.objective[:180]}",
        "timeout_seconds": 60,
    }
    if path:
        tool_input["path"] = path
    if any(marker in lowered for marker in ("pdf", "document", "powerpoint", "presentation", "pptx", "file")):
        tool_input["artifact_type"] = "document"
    return PlanModel(
        objective=task.objective,
        assumptions=[
            "The request asks to send an artifact already associated with this task or conversation.",
            "Artifact delivery uses the source Telegram chat when chat_id is not explicitly provided.",
        ],
        required_capabilities=[Capability.TELEGRAM_SEND],
        steps=[
            PlanStep(
                title="Deliver latest matching task artifact",
                description="Send the latest matching task artifact or screenshot back to Telegram.",
                required_capabilities=[Capability.TELEGRAM_SEND],
                risk_level=RiskLevel.LOW,
                requires_approval=policy.requires_approval,
                tool_name="artifact.deliver",
                tool_input=tool_input,
                expected_output="Telegram delivery result for the requested artifact.",
            )
        ],
        success_criteria=["The requested artifact is sent to Telegram or a clear delivery error is reported."],
        postconditions=[
            PlanPostcondition(
                type=PostconditionType.ARTIFACT_DELIVERED,
                description="The requested artifact is delivered to Telegram.",
            )
        ],
    )


def _build_adapter_factory_plan(settings: AppSettings, task: TaskRecord) -> PlanModel | None:
    if not settings.adapters.adapter_factory.enabled:
        return None
    if not _looks_like_adapter_request(task.objective):
        return None

    factory_policy = settings.capabilities.get(Capability.FILESYSTEM_WRITE)
    if factory_policy is None or not factory_policy.enabled:
        return None

    vscode_policy = settings.capabilities.get(Capability.VSCODE_WRITE_FILES)
    vscode_enabled = bool(settings.adapters.vscode.enabled and vscode_policy and vscode_policy.enabled)
    steps = [
        PlanStep(
            title="Scaffold generated adapter proposal",
            description="Create a cached adapter proposal that can be reviewed and promoted later.",
            required_capabilities=[Capability.FILESYSTEM_WRITE],
            risk_level=RiskLevel.HIGH,
            requires_approval=factory_policy.requires_approval,
            tool_name="adapter.factory",
            tool_input={
                "operation": "scaffold",
                "objective": task.objective,
                "scope_target": settings.adapters.adapter_factory.root_dir,
                "timeout_seconds": 30,
            },
            expected_output="A generated adapter proposal directory with manifest, adapter stub, README, and test placeholder.",
        )
    ]
    required_capabilities = [Capability.FILESYSTEM_WRITE]
    assumptions = [
        "The request asks for a new or missing adapter/tool capability.",
        "Generated adapter work is cached as a proposal and is not auto-imported or executed.",
    ]
    success_criteria = ["A reviewable adapter proposal exists in the generated adapter cache."]
    postconditions = _adapter_postconditions()
    if vscode_enabled and _explicitly_requests_copilot(task.objective):
        required_capabilities.append(Capability.VSCODE_WRITE_FILES)
        assumptions.append("Copilot may refine the generated proposal inside the adapter cache.")
        success_criteria.append("Copilot has been asked to improve the adapter proposal without registering it automatically.")
        steps.append(
            PlanStep(
                title="Ask Copilot to implement the adapter proposal",
                description="Use Copilot to fill in the cached adapter proposal while keeping it isolated from runtime registration.",
                required_capabilities=[Capability.VSCODE_WRITE_FILES],
                risk_level=RiskLevel.HIGH,
                requires_approval=bool(vscode_policy and vscode_policy.requires_approval),
                tool_name="vscode.copilot_terminal",
                tool_input={
                    "prompt": _adapter_copilot_prompt(task.objective),
                    "terminal_id": "agent-control-copilot",
                    "cwd": "{{adapter_dir}}",
                    "capture_output": True,
                    "timeout_seconds": 240,
                },
                expected_output="The adapter proposal files are improved in the cache or Copilot returns implementation guidance.",
            )
        )

    return PlanModel(
        objective=task.objective,
        assumptions=assumptions,
        required_capabilities=required_capabilities,
        steps=steps,
        success_criteria=success_criteria,
        postconditions=postconditions,
    )


def _looks_like_launchable_web_app(objective: str) -> bool:
    return bool(expected_fulfillment(objective).get("preview_url"))


def _looks_like_local_web_app_build(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("web app", "website", "site", "dashboard", "frontend", "html app")) and any(
        marker in lowered for marker in ("build", "create", "make", "generate", "launch")
    )


def _normalize_url_for_plan(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith(("http://", "https://", "about:")):
        return stripped
    return f"https://{stripped}"


def _workspace_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.WORKSPACE_DIR,
            description="A task workspace directory is reported.",
        )
    ]


def _preview_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.PREVIEW_URL,
            description="A local preview URL is reported.",
        ),
        *_workspace_postconditions(),
    ]


def _adapter_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.ADAPTER_PROPOSAL,
            description="A generated adapter proposal directory is reported.",
        )
    ]


def _browser_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.BROWSER_STATE,
            description="A browser URL, tab state, page title, or screenshot path is reported.",
        )
    ]


def _desktop_postconditions() -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.DESKTOP_OBSERVATION,
            description="A desktop observation, screenshot, or computer-use summary is reported.",
        )
    ]


def _file_organization_postconditions(required: bool = True) -> list[PlanPostcondition]:
    return [
        PlanPostcondition(
            type=PostconditionType.FILE_ORGANIZATION,
            description="A file organization manifest, changed paths, or search results are reported.",
            required=required,
        )
    ]


def _artifact_delivery_step(settings: AppSettings, objective: str, *, screenshot: bool = False) -> PlanStep | None:
    if not _looks_like_delivery_request(objective):
        return None
    policy = settings.capabilities.get(Capability.TELEGRAM_SEND)
    if policy is None or not policy.enabled:
        return None
    lowered = objective.lower()
    tool_input: dict[str, object] = {
        "operation": "send_screenshot" if screenshot or "screenshot" in lowered else "send_latest",
        "caption": f"Result for: {objective[:180]}",
        "timeout_seconds": 60,
    }
    if not screenshot and any(marker in lowered for marker in ("pdf", "document", "powerpoint", "presentation", "pptx", "file")):
        tool_input["artifact_type"] = "document"
    return PlanStep(
        title="Deliver task artifact to Telegram",
        description="Send the screenshot or latest task artifact back to the source Telegram chat.",
        required_capabilities=[Capability.TELEGRAM_SEND],
        risk_level=RiskLevel.LOW,
        requires_approval=policy.requires_approval,
        tool_name="artifact.deliver",
        tool_input=tool_input,
        expected_output="Telegram delivery result for the requested screenshot or file artifact.",
    )


def _looks_like_delivery_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(phrase in lowered for phrase in ("send it", "send me", "send the", "send a", "send screenshot", "send file"))


def _objective_wants_screenshot(objective: str) -> bool:
    lowered = objective.lower()
    return "screenshot" in lowered or "screen shot" in lowered


def _looks_like_latest_output_delivery_request(objective: str) -> bool:
    lowered = objective.lower()
    return _looks_like_delivery_request(objective) and any(
        marker in lowered for marker in ("latest output", "current task", "latest artifact", "any screenshot", "powerpoint artifact")
    )


def _looks_like_adapter_request(objective: str) -> bool:
    lowered = objective.lower()
    has_adapter_word = any(word in lowered for word in ("adapter", "tool", "capability", "connector"))
    has_create_word = any(word in lowered for word in ("create", "build", "write", "make", "add", "implement"))
    return has_adapter_word and has_create_word


def _looks_like_schedule_request(objective: str) -> bool:
    lowered = objective.lower()
    explicit_schedule = any(marker in lowered for marker in ("schedule", "scheduled job", "recurring", "every day", "daily", "weekly"))
    has_recurring_phrase = bool(re.search(r"\bevery\s+\d+\s+(?:minute|minutes|hour|hours|day|days|week|weeks)\b", lowered))
    has_action = any(marker in lowered for marker in ("set up", "create", "add", "run", "check", "search", "do ", "pause", "resume", "delete", "list"))
    return (explicit_schedule or has_recurring_phrase) and has_action


def _looks_like_status_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(
        marker in lowered
        for marker in (
            "where are we",
            "what is happening",
            "what's happening",
            "what remains",
            "anything blocked",
            "task status",
            "status update",
            "current status",
        )
    )


def _schedule_operation(objective: str) -> str:
    lowered = objective.lower()
    if "pause" in lowered:
        return "pause"
    if "resume" in lowered or "enable" in lowered:
        return "resume"
    if "delete" in lowered or "remove" in lowered:
        return "delete"
    if "run now" in lowered or "run this schedule" in lowered:
        return "run_now"
    if "list" in lowered or "show schedules" in lowered:
        return "list"
    return "create"


def _schedule_id_from_text(objective: str) -> str | None:
    match = re.search(r"\bschedule_[a-f0-9]+\b", objective)
    return match.group(0) if match else None


def _explicitly_requests_copilot(objective: str) -> bool:
    lowered = objective.lower()
    return "copilot" in lowered or "github copilot" in lowered


def _looks_like_computer_use_request(objective: str) -> bool:
    lowered = objective.lower()
    if _first_url_from_text(objective):
        return False
    if _looks_like_desktop_file_listing(objective):
        return False
    if _path_from_objective(objective) and ("pdf" in lowered or _looks_like_delivery_request(objective)):
        return False
    if _looks_like_delivery_request(objective) and any(
        marker in lowered for marker in ("latest output", "current task", "artifact", "powerpoint artifact")
    ):
        return False
    markers = (
        "use computer",
        "computer use",
        "control my computer",
        "desktop",
        "screen",
        "what do you see",
        "take a screenshot",
        "open this folder",
        "open folder",
        "click",
        "type ",
        "launch app",
        "open app",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_observation_only(objective: str) -> bool:
    lowered = objective.lower()
    if any(
        marker in lowered
        for marker in (
            "what do you see",
            "what is on my desktop",
            "what's on my desktop",
            "what is on my screen",
            "what's on my screen",
            "take a screenshot",
            "screenshot",
            "observe",
            "inspect the desktop",
            "inspect my desktop",
            "look at my screen",
            "tell me what you see",
            "what windows are open",
            "what apps are open",
        )
    ):
        return True
    return bool(
        re.search(r"\b(?:what|tell me|describe|inspect)\b", lowered)
        and re.search(r"\b(?:desktop|screen|windows?|open apps?)\b", lowered)
        and not re.search(r"\b(?:click|type|fill|move|drag|open|launch|close|organize|delete|rename)\b", lowered)
    )


def _deterministic_computer_action(objective: str) -> dict[str, object] | None:
    lowered = objective.lower()
    path = _path_from_objective(objective)
    if path and any(marker in lowered for marker in ("open this folder", "open folder", "open path", "open file")):
        return {"type": "open_path", "path": path}
    if "wait" in lowered:
        seconds_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:second|seconds|sec|secs)\b", lowered)
        seconds = float(seconds_match.group(1)) if seconds_match else 1.0
        return {"type": "wait", "seconds": min(max(seconds, 0.0), 30.0)}
    return None


def _looks_like_filesystem_manage_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(
        marker in lowered
        for marker in (
            "organize",
            "sort files",
            "clean folder",
            "search folder",
            "find file",
            "find files",
            "find me the file",
            "locate file",
            "locate the file",
            "locate and deliver",
            "search files",
            "search for",
            "look for",
            "get me",
            "send me",
        )
    ) or _looks_like_desktop_file_listing(objective)


def _looks_like_file_search(objective: str) -> bool:
    lowered = objective.lower()
    return any(
        marker in lowered
        for marker in (
            "search folder",
            "find file",
            "find files",
            "find me the file",
            "locate file",
            "locate the file",
            "locate and deliver",
            "search files",
            "look for",
            "search for",
            "get me",
            "send me",
        )
    ) and _looks_like_file_reference(objective)


def _looks_like_file_reference(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("file", "files", "pdf", "document", "documents", "folder", "directory"))


def _looks_like_file_creation_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("create", "write", "generate", "make")) and any(
        marker in lowered for marker in ("file", ".txt", ".md", ".json", ".csv", "output")
    )


def _looks_like_web_note_delivery_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("search the web", "web search", "research")) and any(
        marker in lowered for marker in ("create a short note", "create a note", "write a note", "note in")
    ) and _looks_like_delivery_request(objective)


def _web_query_from_request(objective: str) -> str | None:
    match = re.search(r"\b(?:search the web for|web search for|research)\s+(.+?)(?:,\s*(?:create|write|then)|\s+and\s+(?:create|write)|$)", objective, flags=re.IGNORECASE)
    if not match:
        return None
    query = re.sub(r"\s+", " ", match.group(1)).strip(" .")
    return query or None


def _text_file_content_from_request(objective: str) -> str:
    match = re.search(r"\b(?:with|containing|content)\s+(.+?)(?:,\s*then|\s+then\s+send|\s+and\s+send|$)", objective, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip().strip(".") + "\n"
    return f"Generated output for request:\n{objective.strip()}\n"


def _looks_like_rename_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(
        marker in lowered
        for marker in (
            "rename",
            "clearer filename",
            "clearer filenames",
            "meaningful filename",
            "meaningful filenames",
            "before-and-after",
            "before and after",
        )
    )


def _looks_like_desktop_file_listing(objective: str) -> bool:
    lowered = objective.lower()
    return "desktop" in lowered and any(
        marker in lowered
        for marker in (
            "list all",
            "list the",
            "show all",
            "show me all",
            "what files",
            "which files",
            "files on",
            "files at",
            "files in",
            "folders on",
            "folders at",
            "folders in",
            "desktop files",
        )
    )


def _root_from_objective(objective: str) -> str | None:
    lowered = objective.lower()
    if "desktop" in lowered:
        return "desktop"
    if "download" in lowered:
        return "downloads"
    if "document" in lowered:
        return "documents"
    if any(marker in lowered for marker in ("my directory", "my folder", "home directory", "user directory")):
        return str(Path.home())
    return None


def _folder_root_from_request(objective: str) -> str | None:
    lowered = objective.lower()
    root_alias = _root_from_objective(objective)
    if not root_alias:
        return None

    folder_name: str | None = None
    patterns = [
        r"\b([A-Za-z0-9_. -]{2,80}?)\s+folder\s+(?:on|at|in)\s+(?:my\s+)?(?:desktop|documents|downloads)\b",
        r"\bfolder\s+(?:named|called)\s+([A-Za-z0-9_. -]{2,80}?)(?:\s+(?:on|at|in)\s+(?:my\s+)?(?:desktop|documents|downloads)|[.!?]|$)",
        r"\bopen\s+(?:the\s+)?([A-Za-z0-9_. -]{2,80}?)\s+folder\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, objective, flags=re.IGNORECASE)
        if match:
            folder_name = _clean_folder_name(match.group(1))
            break
    if not folder_name:
        return None
    if folder_name.lower() in {"desktop", "documents", "downloads"}:
        return folder_name.lower()
    if folder_name.lower() in {"my", "the", "a", "an", "this", "that"}:
        return None
    if root_alias == "desktop" and folder_name.lower() in {"download", "downloads"}:
        return "downloads"
    if root_alias == "documents" and folder_name.lower() in {"desktop", "downloads"}:
        return folder_name.lower()
    return f"{root_alias}\\{folder_name}"


def _clean_folder_name(value: str) -> str:
    cleaned = re.sub(
        r"\b(open|the|a|an|my|folder|directory|named|called|at|on|in|and|tell|me|all|files|inside|inside it)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;\"'")
    return cleaned


def _path_from_objective(objective: str) -> str | None:
    quoted = _quoted_text(objective)
    if quoted and _looks_like_path(quoted):
        return quoted
    match = re.search(r"[A-Za-z]:\\[^\n\r\"']+", objective)
    if match:
        candidate = match.group(0).strip().rstrip(".,")
        existing = _longest_existing_path_prefix(candidate)
        return existing or candidate
    return None


def _document_path_from_objective(objective: str) -> str | None:
    path = _path_from_objective(objective)
    if not path:
        return None
    try:
        candidate = Path(path).expanduser()
        if candidate.is_dir() and "pdf" in objective.lower():
            pdf = next((item for item in sorted(candidate.iterdir()) if item.is_file() and item.suffix.lower() == ".pdf"), None)
            if pdf is not None:
                return str(pdf)
    except OSError:
        return path
    return path


def _longest_existing_path_prefix(value: str) -> str | None:
    candidate = value.strip().rstrip(".,")
    while candidate:
        try:
            path = Path(candidate).expanduser()
            if path.exists():
                return str(path)
        except OSError:
            pass
        if " " not in candidate:
            return None
        candidate = candidate.rsplit(" ", 1)[0].rstrip(".,")
    return None


def _looks_like_path(value: str) -> bool:
    return bool(
        re.match(r"^[A-Za-z]:\\", value)
        or value.startswith((".", "~", "/"))
        or value.strip().lower() in {"desktop", "documents", "my documents", "downloads", "home", "my directory"}
    )


def _usable_explicit_path(value: str) -> bool:
    return _looks_like_path(value) and not re.search(r"<[^>]+>|\{[^}]+\}", value)


def _usable_intent_path(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text or re.search(r"<[^>]+>|\{[^}]+\}", text):
        return None
    return text


def _query_from_file_reference(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().strip("\"'")
    if not text:
        return None
    if _usable_explicit_path(text):
        return None
    name = Path(text.replace("/", "\\")).name
    return name or text


def _looks_like_find_and_read_request(*values: str) -> bool:
    combined = " ".join(value for value in values if value).lower()
    return any(marker in combined for marker in ("read it", "read me", "read the file", "what inside", "what is inside", "contents", "content of"))


def _search_query_from_objective(objective: str) -> str:
    quoted = _quoted_text(objective)
    if quoted and not _looks_like_path(quoted):
        return quoted
    named = re.search(r"\b(?:named|called)\s+([A-Za-z0-9_.-]+)", objective, flags=re.IGNORECASE)
    if named:
        return named.group(1)
    cleaned = re.sub(r"[A-Za-z]:\\[^\n\r\"']+", " ", objective)
    cleaned = re.sub(
        r"\b(search|look|looking|locate|deliver|named|called|folder|directory|desktop|documents|downloads|find|file|files|send|get|me|the|a|an|and|it|to|about|under|in|at|on|for|from|my|user|users|user's|all|list|show|please)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "*"


def _file_query_from_reference(objective: str) -> str:
    lowered = objective.lower()
    if "pdf" in lowered:
        return ".pdf"
    if "powerpoint" in lowered or "pptx" in lowered:
        return ".pptx"
    if "document" in lowered:
        return ".pdf"
    return "*"


def _looks_like_browser_request(objective: str) -> bool:
    lowered = objective.lower()
    has_url = bool(_first_url_from_text(objective))
    explicit_browser = any(marker in lowered for marker in ("browser", "chrome", "tab", "website", "web page", "webpage", "url", "go to", "search", "google", "bing"))
    if expected_fulfillment(objective).get("preview_url") and not (has_url or explicit_browser):
        return False
    markers = (
        "browser",
        "chrome",
        "tab",
        "website",
        "web page",
        "webpage",
        "url",
        "http://",
        "https://",
        "www.",
        "search",
        "google",
        "bing",
        "open this page",
        "go to",
        "fill the form",
        "click",
    )
    return any(marker in lowered for marker in markers) or (has_url and any(marker in lowered for marker in ("screenshot", "screen shot", "summarize", "check", "open")))


def _looks_like_browser_control_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(
        marker in lowered
        for marker in (
            "close tab",
            "close the tab",
            "click",
            "fill the form",
            "start filling",
            "submit the form",
            "new episode",
            "new show",
            "came out",
            "check whether",
        )
    )


def _looks_like_form_fill_request(objective: str) -> bool:
    lowered = objective.lower()
    return "form" in lowered and any(marker in lowered for marker in ("fill", "filling", "start"))


def _looks_like_page_update_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("new episode", "new show", "came out", "check whether"))


def _objective_wants_submit(objective: str) -> bool:
    lowered = objective.lower()
    if any(
        marker in lowered
        for marker in (
            "do not submit",
            "don't submit",
            "dont submit",
            "without submitting",
            "stop before submitting",
            "pause before submitting",
        )
    ):
        return False
    return "submit" in lowered or "send the form" in lowered


def _form_fields_from_objective(objective: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in re.findall(r"\b([A-Za-z][A-Za-z0-9 _-]{1,30})\s*=\s*([^,;\n]+)", objective):
        normalized_key = key.strip().split()[-1]
        cleaned_value = re.split(
            r"\b(?:do not submit|don't submit|dont submit|without submitting|stop before submitting|pause before submitting)\b",
            value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip().rstrip(" ")
        if normalized_key.lower() in {"email", "e-mail"}:
            cleaned_value = cleaned_value.rstrip(".")
        fields[normalized_key] = cleaned_value
    return fields


def _browser_open_input(objective: str) -> tuple[str, dict[str, object]]:
    lowered = objective.lower()
    if any(phrase in lowered for phrase in ("open tabs", "current tabs", "what tabs", "what websites")):
        return "inspect_tabs", {"include_text": "summary" in lowered or "about" in lowered}
    if "screenshot" in lowered or "screen shot" in lowered:
        url = _first_url_from_text(objective)
        if url:
            return "screenshot", {"url": url, "full_page": True}
        return "screenshot", {"full_page": True}
    if "search" in lowered or "google" in lowered or "bing" in lowered:
        if "50" in lowered or "many page" in lowered or "many pages" in lowered:
            return "research_pages", {
                "objective": objective,
                "query": _query_for_browser(objective),
                "page_limit": _page_limit_from_objective(objective),
            }
        return "research", {
            "objective": objective,
            "query": _query_for_browser(objective),
            "open_first_result": _objective_wants_first_result(objective),
        }
    url = _first_url_from_text(objective)
    if url:
        return "research", {"objective": objective, "url": url}
    return "research", {"objective": objective}


def _browser_control_input(objective: str) -> tuple[str, dict[str, object]]:
    lowered = objective.lower()
    if _looks_like_page_update_request(objective):
        return "check_page_update", {"url": _first_url_from_text(objective), "objective": objective}
    if "close" in lowered:
        quoted = _quoted_text(objective)
        return "close_tab", {"title_contains": quoted} if quoted else {}
    if "click" in lowered:
        quoted = _quoted_text(objective)
        return "click", {"text": quoted or _text_after_word(objective, "click")}
    return "fill_form_step", {"fields": _form_fields_from_objective(objective), "submit": _objective_wants_submit(objective)}


def _first_url_from_text(value: str) -> str | None:
    import re

    match = re.search(r"https?://[^\s<>()]+|www\.[^\s<>()]+|\b[A-Za-z0-9.-]+\.(?:com|org|net|io|ai|dev|edu|gov|co)\b", value)
    return match.group(0).rstrip(".,") if match else None


def _quoted_text(value: str) -> str | None:
    import re

    match = re.search(r"['\"]([^'\"]+)['\"]", value)
    return match.group(1).strip() if match else None


def _text_after_word(value: str, word: str) -> str:
    lowered = value.lower()
    index = lowered.find(word)
    if index < 0:
        return value.strip()
    return value[index + len(word) :].strip(" .:")


def _query_for_browser(objective: str) -> str:
    import re

    cleaned = re.sub(r"\b(search|google|bing|look up|find|open|browser|chrome|summarize|summary)\b", " ", objective, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or objective


def _objective_wants_first_result(objective: str) -> bool:
    lowered = objective.lower()
    return any(phrase in lowered for phrase in ("first result", "first website", "first site", "go to the first", "open the first"))


def _page_limit_from_objective(objective: str) -> int:
    match = re.search(r"\b(\d{1,2})\s+pages?\b", objective.lower())
    if not match:
        return 50 if "50" in objective else 10
    return max(1, min(50, int(match.group(1))))


def _looks_like_document_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("pdf", "powerpoint", "presentation", "pptx"))


def _presentation_title(objective: str) -> str:
    quoted = _quoted_text(objective)
    if quoted:
        return quoted
    cleaned = re.sub(r"\b(use codex|create|make|build|powerpoint|presentation|pptx|send me|send it)\b", " ", objective, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:")
    return cleaned[:80] or "Presentation"


def _explicit_coding_provider(objective: str) -> str | None:
    lowered = objective.lower()
    if "codex" in lowered:
        return "codex"
    if "github copilot" in lowered or "copilot" in lowered:
        return "github_copilot"
    return None


def _looks_like_web_research_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("web search", "search for", "search this", "search the web", "research"))


def _looks_like_large_coding_request(objective: str) -> bool:
    lowered = objective.lower()
    return any(marker in lowered for marker in ("large", "step by step", "plan first", "start creating", "mobile deployment"))


def _adapter_copilot_prompt(objective: str) -> str:
    return render_prompt("tools/adapter_factory_copilot.md", objective=objective)


def _web_app_copilot_prompt(objective: str, workspace_dir: str) -> str:
    return render_prompt("tools/copilot_web_app.md", objective=objective, workspace_dir=workspace_dir)
