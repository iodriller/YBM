from __future__ import annotations

from pathlib import Path

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration.default_plans import build_default_task_plan
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
        },
        scheduler={"enabled": True},
    )


def _task(objective: str, intent: OrchestrationIntent) -> TaskRecord:
    return TaskRecord(objective=objective, metadata={"orchestration_intent": intent.model_dump(mode="json")})


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
