from __future__ import annotations

from pathlib import Path

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration.default_plans import build_default_task_plan, build_evaluator_recovery_plan
from agent_control.schemas import (
    Capability,
    DeliveryKind,
    IntentRoute,
    MessageClassification,
    OrchestrationIntent,
    RiskLevel,
    TaskRecord,
    TaskType,
)


def _policy(risk: RiskLevel = RiskLevel.LOW) -> CapabilityPolicy:
    return CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=risk)


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        _env_file=None,
        capabilities={
            Capability.TELEGRAM_SEND: _policy(),
            Capability.BROWSER_OPEN: _policy(),
            Capability.BROWSER_CONTROL: _policy(RiskLevel.CRITICAL),
            Capability.DESKTOP_CONTROL: _policy(RiskLevel.CRITICAL),
            Capability.FILESYSTEM_WRITE: _policy(RiskLevel.HIGH),
            Capability.TERMINAL_RUN: _policy(RiskLevel.HIGH),
            Capability.SCHEDULE_MANAGE: _policy(RiskLevel.MEDIUM),
        },
        adapters={
            "browser": {"enabled": True},
            "desktop": {"control_enabled": True},
            "computer_use": {"enabled": True, "allowed_roots": [str(tmp_path)]},
            "workspace": {"enabled": True, "root_dir": str(tmp_path / "workspaces")},
            "coding_agent": {"enabled": True, "workspace_root": str(tmp_path / "workspaces")},
            "code_interpreter": {"enabled": True, "workspace_root": str(tmp_path / "code_interpreter")},
        },
        scheduler={"enabled": True},
    )


def _task(objective: str, intent: OrchestrationIntent, *, original_message_text: str | None = None) -> TaskRecord:
    metadata = {"orchestration_intent": intent.model_dump(mode="json")}
    if original_message_text:
        metadata["original_message_text"] = original_message_text
    return TaskRecord(objective=objective, metadata=metadata)


def test_intent_routes_browser_screenshot_without_text_keywords(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle alpha",
            OrchestrationIntent(
                route=IntentRoute.BROWSER_OPEN,
                operation="screenshot",
                objective="Capture example.com",
                reasoning="The LLM selected browser screenshot.",
                url="https://example.com",
                delivery=DeliveryKind.SCREENSHOT,
            ),
        ),
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["browser.open", "artifact.deliver"]
    assert plan.steps[0].tool_input["operation"] == "screenshot"
    assert plan.steps[0].tool_input["url"] == "https://example.com"
    assert plan.steps[1].tool_input["operation"] == "send_screenshot"


def test_intent_routes_desktop_screenshot_send_operation_to_delivery_step(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Capture and send a screenshot of the desktop.",
            OrchestrationIntent(
                route=IntentRoute.DESKTOP_OBSERVE,
                operation="screenshot",
                objective="Capture a screenshot of the desktop and prepare for delivery.",
                reasoning="The LLM selected desktop screenshot.",
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "computer.use:observe",
        "artifact.deliver:send_screenshot",
    ]


def test_intent_uses_original_message_for_desktop_screenshot_delivery(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Capture a screenshot of the desktop.",
            OrchestrationIntent(
                route=IntentRoute.DESKTOP_OBSERVE,
                operation="screenshot",
                objective="Capture a screenshot of the desktop.",
                reasoning="The LLM normalized away the delivery request.",
                delivery=DeliveryKind.FILE,
            ),
            original_message_text="Send me a screenshot of the desktop.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "computer.use:observe",
        "artifact.deliver:send_screenshot",
    ]


def test_filesystem_route_uses_original_message_for_visual_desktop_observation(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "List files and folders on the user's desktop.",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="list",
                objective="List files and folders on the desktop.",
                reasoning="The LLM converted a visual desktop question into a file listing.",
                folder_path="desktop",
            ),
            original_message_text="Tell me what is on my desktop right now.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "computer.use:observe",
    ]


def test_intent_routes_schedule_without_schedule_phrase(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle beta",
            OrchestrationIntent(
                route=IntentRoute.SCHEDULE_MANAGE,
                operation="create",
                objective="Create recurring website check",
                reasoning="The LLM selected the scheduler.",
                cadence="daily",
                scheduled_objective="Check https://example.com for updates",
            ),
        ),
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["schedule.manage"]
    assert plan.steps[0].tool_input["operation"] == "create"
    assert plan.steps[0].tool_input["cadence"] == "daily"
    assert plan.steps[0].tool_input["objective"] == "Check https://example.com for updates"


def test_intent_routes_status_question_to_task_status_tool(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Where are we? Tell me what remains and whether anything is blocked.",
            OrchestrationIntent(
                route=IntentRoute.STATUS,
                operation="status",
                objective="Report current workflow status.",
                reasoning="The user asks for task status.",
            ),
            original_message_text="Where are we? Tell me what remains and whether anything is blocked.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == ["task.status:status"]
    assert plan.steps[0].tool_input["limit"] == 20


def test_intent_routes_explicit_codex_to_coding_agent(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle gamma",
            OrchestrationIntent(
                route=IntentRoute.CODING_AGENT,
                operation="plan",
                objective="Use Codex to plan a mobile LLM deployment app",
                reasoning="The user explicitly asked for Codex.",
                provider="codex",
                needs_plan_first=True,
                use_external_agent=True,
            ),
        ),
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["coding.agent"]
    assert plan.steps[0].tool_input["provider"] == "codex"
    assert plan.steps[0].tool_input["operation"] == "plan"


def test_intent_routes_codex_presentation_request_through_document_adapter(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Use Codex to prepare the content for a 5-slide PowerPoint, then use the presentation-generation adapter to create the PPTX and send it to me.",
            OrchestrationIntent(
                route=IntentRoute.CODING_AGENT,
                operation="run_goal",
                objective="Generate presentation content using Codex and create a PowerPoint artifact.",
                reasoning="The user explicitly asked for Codex and presentation generation.",
                provider="codex",
                use_external_agent=True,
                delivery=DeliveryKind.FILE,
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "coding.agent:run_goal",
        "document.manage:create_presentation",
        "artifact.deliver:send_latest",
    ]
    assert plan.steps[1].tool_input["content"] == "{{last_output}}"


def test_intent_routes_workspace_web_app_without_coding_agent(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle delta",
            OrchestrationIntent(
                route=IntentRoute.WORKSPACE_MANAGE,
                operation="web_app_preview",
                objective="Create and launch a small local web app",
                reasoning="No external coding agent was requested.",
            ),
        ),
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["workspace.manage"]
    assert plan.steps[0].tool_input["operation"] == "web_app_preview"


def test_intent_routes_filesystem_search_without_search_phrase(tmp_path) -> None:
    target = tmp_path / "docs"
    target.mkdir()

    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle epsilon",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="search",
                objective="Find matching local documents",
                reasoning="The LLM selected filesystem search.",
                folder_path=str(target),
                query="resume",
            ),
        ),
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["filesystem.manage"]
    assert plan.steps[0].tool_input["operation"] == "search"
    assert plan.steps[0].tool_input["root"] == str(target)
    assert plan.steps[0].tool_input["query"] == "resume"


def test_intent_routes_desktop_file_listing_to_filesystem_not_computer_use(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle desktop files",
            OrchestrationIntent(
                route=IntentRoute.DESKTOP_OBSERVE,
                operation="observe",
                objective="List all the files at my desktop.",
                reasoning="The user mentions desktop, but asks for file listing.",
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:inspect_folder",
    ]
    assert plan.steps[0].requires_approval is False
    assert plan.steps[0].tool_input["root"] == "desktop"


def test_file_lookup_delivery_searches_desktop_then_sends_resolved_file(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle file delivery",
            OrchestrationIntent(
                route=IntentRoute.ARTIFACT_DELIVERY,
                operation="send_file",
                objective="Find me the file about invoices from my desktop and send it to me.",
                reasoning="The user wants a file delivered but did not provide an exact path.",
                folder_path="desktop",
                query="invoices",
                delivery=DeliveryKind.FILE,
                artifact_type="document",
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:search",
        "artifact.deliver:send_file",
    ]
    assert plan.steps[0].tool_input["root"] == "desktop"
    assert plan.steps[0].tool_input["query"] == "invoices"
    assert plan.steps[1].tool_input["path"] == "{{last_entry_path}}"
    assert [postcondition.type.value for postcondition in plan.postconditions] == ["artifact_delivered"]


def test_artifact_delivery_create_file_request_writes_then_sends(tmp_path) -> None:
    target = tmp_path / "docs" / "e2e-output.txt"
    target.parent.mkdir()

    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Create and deliver e2e-output.txt",
            OrchestrationIntent(
                route=IntentRoute.ARTIFACT_DELIVERY,
                operation="send_file",
                objective="Create and deliver e2e-output.txt",
                reasoning="The LLM selected delivery for a create-and-send request.",
                file_path=str(target),
                delivery=DeliveryKind.FILE,
            ),
            original_message_text=f"Create a small text output in {target.parent} named e2e-output.txt, then send me that file.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:write_text_file",
        "artifact.deliver:send_file",
    ]
    assert plan.steps[0].tool_input["path"] == str(target)
    assert plan.steps[1].tool_input["path"] == str(target)


def test_code_interpreter_route_for_web_note_is_repaired_to_multi_tool_plan(tmp_path) -> None:
    target = tmp_path / "docs"
    target.mkdir()

    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            f"Search Python documentation, create a note in {target}, and deliver the result.",
            OrchestrationIntent(
                route=IntentRoute.CODE_INTERPRETER,
                operation="generate_and_run",
                objective=f"Search Python documentation, create a note in {target}, and deliver the result.",
                reasoning="The LLM incorrectly selected code interpreter for web research plus delivery.",
                file_path=str(target),
                delivery=DeliveryKind.FILE,
            ),
            original_message_text=f"Search the web for Python official documentation, create a short note in {target}, and send me the result.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "browser.open:research",
        "filesystem.manage:write_text_file",
        "artifact.deliver:send_file",
    ]
    assert plan.steps[0].tool_input["query"] == "Python official documentation"
    assert plan.steps[1].tool_input["path"] == str(target / "web-research-note.txt")


def test_filesystem_intent_locate_and_deliver_does_not_organize_desktop(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Find me the file named agent-control-sample from my desktop and send it to me.",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="locate_file",
                objective="Locate and deliver the file 'agent-control-sample' from the user's desktop.",
                reasoning="The LLM selected filesystem work but used a locate operation.",
                file_path="agent-control-sample",
                folder_path="desktop",
                delivery=DeliveryKind.FILE,
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:search",
        "artifact.deliver:send_file",
    ]
    assert plan.steps[0].tool_input["query"] == "agent-control-sample"


def test_filesystem_intent_placeholder_desktop_path_is_treated_as_search_query(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Find me the file named agent-control-sample from my desktop and send it to me.",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="send_file",
                objective="Locate and deliver the file 'agent-control-sample' from the user's desktop.",
                reasoning="The LLM inferred a placeholder path that is not directly usable.",
                file_path=r"C:\Users\<user>\Desktop\agent-control-sample.pdf",
                delivery=DeliveryKind.FILE,
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:search",
        "artifact.deliver:send_file",
    ]
    assert plan.steps[0].tool_input["root"] == "desktop"
    assert plan.steps[0].tool_input["query"] == "agent-control-sample.pdf"


def test_evaluator_recovery_replaces_missing_desktop_observation_with_filesystem_listing(tmp_path) -> None:
    plan = build_evaluator_recovery_plan(
        _settings(tmp_path),
        TaskRecord(objective="List all files at my desktop."),
        "expected_desktop_observation_missing",
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:inspect_folder",
    ]


def test_evaluator_recovery_can_use_code_interpreter_for_validation_failures(tmp_path) -> None:
    plan = build_evaluator_recovery_plan(
        _settings(tmp_path),
        TaskRecord(objective="Normalize these local records into a JSON file."),
        "validation failed for generated tool input",
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "code.interpreter:generate_and_run",
    ]
    assert plan.steps[0].tool_input["workspace_dir"].startswith(str(tmp_path / "code_interpreter"))


def test_intent_routes_filesystem_inspect_alias_as_read_only_operation(tmp_path) -> None:
    target = tmp_path / "docs"
    target.mkdir()

    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle folder inspect alias",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="inspect",
                objective="List files and subfolders within the specified directory.",
                reasoning="The LLM selected filesystem inspection.",
                folder_path=str(target),
            ),
        ),
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["filesystem.manage"]
    assert plan.steps[0].tool_input["operation"] == "inspect_folder"
    assert plan.steps[0].tool_input["root"] == str(target)


def test_intent_routes_filesystem_describe_folder_to_content_extraction(tmp_path) -> None:
    target = tmp_path / "mixed"
    target.mkdir()

    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle folder explanation",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="describe_folder",
                objective="Explain what the files in this folder contain.",
                reasoning="The LLM selected folder description.",
                folder_path=str(target),
            ),
        ),
    )

    assert plan is not None
    assert [step.tool_name for step in plan.steps] == ["filesystem.manage"]
    assert plan.steps[0].tool_input["operation"] == "describe_folder"
    assert plan.steps[0].tool_input["include_ocr"] is True


def test_intent_routes_find_and_read_to_content_search(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Find the resume file on my desktop and read it to me.",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="search",
                objective="Find the resume file on Desktop and read it.",
                reasoning="The LLM selected filesystem search.",
                folder_path="desktop",
                query="resume",
            ),
            original_message_text="Find the resume file on my desktop and read it to me.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:search",
    ]
    assert plan.steps[0].tool_input["root"] == "desktop"
    assert plan.steps[0].tool_input["query"] == "resume"
    assert plan.steps[0].tool_input["include_content"] is True


def test_intent_routes_desktop_subfolder_listing_to_that_folder(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Open the resumes folder at my desktop and tell me all the files inside.",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="inspect_folder",
                objective="List all files in the resumes folder on Desktop.",
                reasoning="The LLM selected filesystem inspection.",
                delivery=DeliveryKind.FILE,
                folder_path=r"C:\Users\<user>\Desktop\resumes",
            ),
            original_message_text="Open the resumes folder at my desktop and tell me all the files inside.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:inspect_folder",
    ]
    assert plan.steps[0].tool_input["root"] == "desktop\\resumes"


def test_coding_agent_misroute_without_provider_repairs_to_workspace_preview(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Build a simple web app with login and a dashboard.",
            OrchestrationIntent(
                route=IntentRoute.CODING_AGENT,
                operation="create",
                objective="Build a simple web app with login and a dashboard.",
                reasoning="The LLM incorrectly selected coding agent without an explicit provider.",
                provider=None,
                use_external_agent=False,
            ),
            original_message_text="Build a simple web app with login and a dashboard.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "workspace.manage:web_app_preview",
    ]


def test_browser_chatgpt_prompt_routes_to_open_then_fill(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "Open chatgpt.com and ask how is the weather and give me the answer.",
            OrchestrationIntent(
                route=IntentRoute.BROWSER_OPEN,
                operation="open",
                objective="Open ChatGPT and ask how is the weather.",
                reasoning="The LLM selected browser open.",
            ),
            original_message_text="Open chatgpt.com and ask how is the weather and give me the answer.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "browser.open:open",
        "browser.control:fill_form_step",
    ]
    assert plan.steps[0].tool_input["url"] == "https://chatgpt.com"
    assert "how is the weather" in plan.steps[1].tool_input["fields"]["message"]
    assert plan.steps[1].tool_input["submit"] is True


def test_intent_routes_pdf_summary_from_folder_to_search_then_document(tmp_path) -> None:
    target = tmp_path / "desktop_folder"
    target.mkdir()

    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle pdf summary folder",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="summarize_pdf",
                objective="Find the PDF in this folder and summarize it.",
                reasoning="The LLM selected PDF summarization but supplied a folder.",
                folder_path=str(target),
                artifact_type="document",
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:search",
        "document.manage:summarize_pdf",
    ]
    assert plan.steps[0].tool_input["query"] == ".pdf"
    assert plan.steps[1].tool_input["path"] == "{{last_entry_path}}"


def test_filesystem_inspect_file_pdf_request_does_not_become_organization(tmp_path) -> None:
    target = tmp_path / "desktop_folder"
    target.mkdir()

    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            f"Inspect the contents of the PDF file located in {target} and describe its content.",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="inspect_file",
                objective="Describe the content of the PDF file.",
                reasoning="The LLM selected a generic file inspection operation for a PDF summary request.",
                file_path=str(target / "*.pdf"),
                folder_path=str(target),
                delivery=DeliveryKind.FILE,
            ),
            original_message_text=f"Open the desktop folder {target}, find the PDF inside it, and tell me what the PDF is about.",
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:search",
        "document.manage:summarize_pdf",
    ]
    assert plan.steps[0].tool_input["root"] == str(target)
    assert plan.steps[0].tool_input["query"] == ".pdf"
    assert plan.steps[1].tool_input["path"] == "{{last_entry_path}}"


def test_intent_routes_filesystem_rename_alias_to_rename_manifest(tmp_path) -> None:
    target = tmp_path / "docs"
    target.mkdir()

    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle rename",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="rename",
                objective="Rename files based on their contents and report before and after names.",
                reasoning="The LLM selected filesystem rename.",
                folder_path=str(target),
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:rename_plan",
        "filesystem.manage:apply_manifest",
    ]
    assert plan.steps[1].tool_input["manifest"] == "{{last_manifest}}"


def test_filesystem_rename_request_overrides_llm_organize_operation(tmp_path) -> None:
    target = tmp_path / "docs"
    target.mkdir()

    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            f"Inspect, rename, and document files in {target}.",
            OrchestrationIntent(
                route=IntentRoute.FILESYSTEM_MANAGE,
                operation="organize",
                objective=f"Inspect, rename, and document files in {target}.",
                reasoning="The LLM selected organization even though the original request is a rename.",
                folder_path=str(target),
            ),
            original_message_text=(
                f"Inspect every file in {target}, create clearer filenames based on what each file is about, "
                "rename them consistently, and give me a before-and-after table."
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "filesystem.manage:rename_plan",
        "filesystem.manage:apply_manifest",
    ]


def test_intent_routes_form_fill_to_same_tab_and_review_screenshot(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle zeta",
            OrchestrationIntent(
                route=IntentRoute.BROWSER_CONTROL,
                operation="fill_form_step",
                objective="Fill the contact form and show a screenshot before submitting",
                reasoning="The LLM selected browser form filling.",
                url="https://form.test",
                form_fields={"name": "Oney", "email": "oney@example.com"},
                submit=False,
                delivery=DeliveryKind.SCREENSHOT,
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "browser.open:open",
        "browser.control:extract_page_state",
        "browser.control:fill_form_step",
        "browser.open:screenshot",
        "artifact.deliver:send_screenshot",
    ]
    assert plan.steps[1].tool_input["url_contains"] == "https://form.test"
    assert plan.steps[2].tool_input["url_contains"] == "https://form.test"
    assert plan.steps[2].tool_input["submit"] is False
    assert plan.steps[3].tool_input["url_contains"] == "https://form.test"


def test_intent_routes_browser_control_screenshot_to_capture_and_delivery(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle browser screenshot",
            OrchestrationIntent(
                route=IntentRoute.BROWSER_CONTROL,
                operation="screenshot",
                objective="Open example.com, verify it loaded, and send me a screenshot.",
                reasoning="The LLM selected browser control for a screenshot request.",
                url="https://example.com",
                delivery=DeliveryKind.SCREENSHOT,
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "browser.open:open",
        "browser.open:screenshot",
        "artifact.deliver:send_screenshot",
    ]
    assert plan.steps[1].tool_input["url"] == "https://example.com"


def test_intent_repairs_browser_control_research_to_browser_open(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle browser research",
            OrchestrationIntent(
                route=IntentRoute.BROWSER_CONTROL,
                operation="research_pages",
                objective="Search for Python documentation, open the first result, and summarize it.",
                reasoning="The LLM selected browser control but supplied a research operation.",
                query="Python official documentation",
                page_limit=1,
                open_first_result=True,
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "browser.open:research_pages",
    ]
    assert plan.steps[0].tool_input["query"] == "Python official documentation"
    assert plan.steps[0].tool_input["open_first_result"] is True


def test_intent_routes_browser_control_check_alias_to_page_update(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle episode check",
            OrchestrationIntent(
                route=IntentRoute.BROWSER_CONTROL,
                operation="check",
                objective="Determine whether a new episode is available and provide evidence.",
                reasoning="The LLM selected a page check alias.",
                url="https://example.com/episode",
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "browser.open:open",
        "browser.control:check_page_update",
    ]
    assert plan.steps[1].tool_input["url"] == "https://example.com/episode"


def test_intent_repairs_browser_open_episode_research_to_page_update(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle episode research",
            OrchestrationIntent(
                route=IntentRoute.BROWSER_OPEN,
                operation="research",
                objective="Check for a new episode and provide evidence.",
                reasoning="The LLM selected browser research for an update check.",
                url="https://example.com/episode",
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "browser.control:check_page_update",
    ]


def test_intent_repairs_browser_control_episode_research_to_page_update(tmp_path) -> None:
    plan = build_default_task_plan(
        _settings(tmp_path),
        _task(
            "handle episode control research",
            OrchestrationIntent(
                route=IntentRoute.BROWSER_CONTROL,
                operation="research",
                objective="Check for new episode and provide evidence.",
                reasoning="The LLM selected browser research for a page update check.",
                url="https://example.com/episode",
            ),
        ),
    )

    assert plan is not None
    assert [f"{step.tool_name}:{step.tool_input.get('operation')}" for step in plan.steps] == [
        "browser.open:open",
        "browser.control:check_page_update",
    ]


def test_llm_route_aliases_are_normalized_before_validation() -> None:
    classification = MessageClassification.model_validate(
        {
            "is_task": True,
            "task_type": "browser.research",
            "normalized_objective": "Research Python documentation.",
            "confidence": 0.8,
            "reason": "Browser research requested.",
            "intent": {
                "route": "browser.research",
                "operation": "research",
                "objective": "Research Python documentation.",
                "reasoning": "Search and summarize a page.",
                "query": "Python official documentation",
            },
        }
    )

    assert classification.task_type == TaskType.OTHER
    assert classification.intent is not None
    assert classification.intent.route == IntentRoute.BROWSER_OPEN
