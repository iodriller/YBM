from __future__ import annotations

import json

from agent_control.testing.smoke import SMOKE_LOG_KEYS, write_smoke_log


REQUIREMENT_SPECS = [
    {
        "id": "desktop_observation",
        "requirement": "ask what is on my desktop right now",
        "phase": 3,
        "route": ["computer.use:observe"],
        "evidence": ["screenshot_path", "desktop_observation", "telegram_summary"],
        "test_name": "test_desktop_observation_returns_summary_and_screenshot",
    },
    {
        "id": "desktop_folder_pdf_summary",
        "requirement": "open a desktop folder, open a PDF, and tell me what it is about",
        "phase": 2,
        "route": ["filesystem.manage:resolve_desktop_item", "document.manage:summarize_pdf"],
        "evidence": ["pdf_path", "summary_artifact", "telegram_summary"],
        "test_name": "test_desktop_folder_pdf_summary_flow",
    },
    {
        "id": "send_found_pdf",
        "requirement": "send me the PDF file it found or opened",
        "phase": 1,
        "route": ["artifact.deliver:send_latest"],
        "evidence": ["artifact_id", "telegram_sendDocument"],
        "test_name": "test_artifact_delivery_sends_latest_document_artifact",
    },
    {
        "id": "organize_folder",
        "requirement": "organize documents in a folder based on criteria and report changes",
        "phase": 3,
        "route": ["filesystem.manage:organize_plan", "filesystem.manage:apply_manifest"],
        "evidence": ["manifest", "changed_paths"],
        "test_name": "test_filesystem_manage_organizes_folder_with_manifest",
    },
    {
        "id": "send_desktop_screenshot",
        "requirement": "send me a screenshot of the desktop",
        "phase": 1,
        "route": ["computer.use:observe", "artifact.deliver:send_screenshot"],
        "evidence": ["screenshot_path", "telegram_sendPhoto"],
        "test_name": "test_artifact_delivery_sends_screenshot_from_task_metadata",
    },
    {
        "id": "browser_website_screenshot",
        "requirement": "open browser, go to a website, and send a screenshot",
        "phase": 1,
        "route": ["browser.open:screenshot", "artifact.deliver:send_screenshot"],
        "evidence": ["browser_url", "screenshot_path", "telegram_sendPhoto"],
        "test_name": "test_default_browser_screenshot_plan_delivers_when_user_asks_to_send",
    },
    {
        "id": "browser_search_first_summary",
        "requirement": "search, open the first web page, and summarize it",
        "phase": 4,
        "route": ["browser.control:search", "browser.control:summarize_page"],
        "evidence": ["visited_url", "page_title", "summary"],
        "test_name": "test_browser_search_first_result_summary",
    },
    {
        "id": "browser_check_episode_update",
        "requirement": "go to a web page and check whether a new show or episode came out",
        "phase": 4,
        "route": ["browser.control:check_page_update"],
        "evidence": ["url", "extracted_markers", "update_result"],
        "test_name": "test_browser_check_page_update",
    },
    {
        "id": "browser_fill_form",
        "requirement": "go to a web page and start filling out a form",
        "phase": 4,
        "route": ["browser.control:extract_page_state", "browser.control:fill_form_step"],
        "evidence": ["fields_detected", "fields_filled", "post_fill_screenshot"],
        "test_name": "test_browser_fill_form_without_implicit_submit",
    },
    {
        "id": "use_codex_explicit",
        "requirement": "say use Codex and have it use Codex",
        "phase": 5,
        "route": ["coding.agent:run_goal provider=codex"],
        "evidence": ["provider", "workspace_dir", "stdout", "exit_code"],
        "test_name": "test_coding_agent_uses_codex_when_explicit",
    },
    {
        "id": "use_copilot_explicit",
        "requirement": "say use GitHub Copilot and have it use GitHub Copilot",
        "phase": 0,
        "route": ["vscode.copilot_terminal", "future:coding.agent provider=github_copilot"],
        "evidence": ["route_decision", "tool_invocation"],
        "test_name": "test_worker_launchable_app_request_uses_copilot_then_workspace_preview_when_explicit",
    },
    {
        "id": "no_external_agent_without_name",
        "requirement": "do not use Codex or GitHub Copilot unless specifically named",
        "phase": 0,
        "route": ["workspace.manage", "no coding agent"],
        "evidence": ["route_decision.external_agent_skipped"],
        "test_name": "test_worker_launchable_app_request_does_not_use_copilot_unless_explicit",
    },
    {
        "id": "large_codex_mobile_llm_app",
        "requirement": "use Codex to start creating an app for mobile deployment of an LLM",
        "phase": 5,
        "route": ["coding.agent:plan provider=codex", "coding.agent:run_step provider=codex"],
        "evidence": ["plan_artifact", "current_step", "workspace_dir"],
        "test_name": "test_large_codex_task_creates_plan_before_steps",
    },
    {
        "id": "weird_codex_app",
        "requirement": "use Codex for a weird app idea like hamsters and mice",
        "phase": 5,
        "route": ["coding.agent:run_goal provider=codex"],
        "evidence": ["workspace_dir", "generated_files", "preview_url"],
        "test_name": "test_codex_weird_app_generates_workspace",
    },
    {
        "id": "coding_status_screenshot",
        "requirement": "tell coding process status and give screenshot of VS Code and file directory",
        "phase": 5,
        "route": ["coding.agent:status", "computer.use:observe", "artifact.deliver:send_screenshot"],
        "evidence": ["provider_status", "screenshot_path", "telegram_sendPhoto"],
        "test_name": "test_coding_status_can_include_desktop_screenshot",
    },
    {
        "id": "codex_powerpoint_create",
        "requirement": "use Codex and create a PowerPoint presentation and send it",
        "phase": 2,
        "route": ["document.manage:create_presentation", "artifact.deliver:send_file"],
        "evidence": ["pptx_path", "telegram_sendDocument"],
        "test_name": "test_create_powerpoint_and_deliver",
    },
    {
        "id": "powerpoint_update_followup",
        "requirement": "send a follow-up asking for presentation changes and get revised output",
        "phase": 2,
        "route": ["document.manage:update_presentation", "artifact.deliver:send_file"],
        "evidence": ["previous_artifact_id", "revision_artifact_id", "telegram_sendDocument"],
        "test_name": "test_update_latest_powerpoint_revision",
    },
    {
        "id": "large_task_plan_first",
        "requirement": "large website or app requirement first prepares a plan",
        "phase": 5,
        "route": ["planner", "coding.agent:plan"],
        "evidence": ["plan_artifact", "no_run_step_before_plan"],
        "test_name": "test_large_task_creates_plan_before_execution",
    },
    {
        "id": "explicit_tool_combo",
        "requirement": "use Codex and search, or use GitHub Copilot and web search",
        "phase": 5,
        "route": ["browser.control:research_pages", "coding.agent:run_step explicit provider"],
        "evidence": ["visited_urls", "provider", "handoff_context"],
        "test_name": "test_explicit_tool_combination_routes_only_named_tools",
    },
    {
        "id": "many_page_search",
        "requirement": "web search through many pages, even 50 pages",
        "phase": 4,
        "route": ["browser.control:research_pages"],
        "evidence": ["page_limit", "visited_urls", "source_summaries"],
        "test_name": "test_browser_research_pages_respects_limit_and_logs_urls",
    },
    {
        "id": "create_adapter",
        "requirement": "ask it to create an adapter to access something",
        "phase": 0,
        "route": ["adapter.factory:scaffold"],
        "evidence": ["adapter_dir", "manifest", "not_runtime_loaded"],
        "test_name": "test_adapter_factory_scaffolds_cached_adapter",
    },
    {
        "id": "scheduled_job_create",
        "requirement": "set up a scheduled job like every day",
        "phase": 6,
        "route": ["schedule.manage:create"],
        "evidence": ["schedule_id", "next_run_at"],
        "test_name": "test_schedule_manage_creates_daily_job",
    },
    {
        "id": "scheduled_job_runs",
        "requirement": "scheduled job searches internet or checks a website daily",
        "phase": 6,
        "route": ["scheduler", "task creation"],
        "evidence": ["generated_task_id", "last_run_at", "telegram_summary"],
        "test_name": "test_due_schedule_creates_task_and_reports_result",
    },
    {
        "id": "scheduled_job_with_coding_provider",
        "requirement": "use Codex or Copilot in workspace to prepare scheduled job code",
        "phase": 6,
        "route": ["coding.agent explicit provider", "schedule.manage:create"],
        "evidence": ["workspace_dir", "schedule_id"],
        "test_name": "test_schedule_job_can_reference_explicit_coding_workspace",
    },
    {
        "id": "codex_availability",
        "requirement": "ask current availability of Codex and whether almost at limit",
        "phase": 5,
        "route": ["coding.agent:limits provider=codex"],
        "evidence": ["limit_state", "latest_known_limit_event"],
        "test_name": "test_codex_limits_reports_availability_or_unknown",
    },
    {
        "id": "codex_limit_notify",
        "requirement": "tell me when Codex reaches a limit while developing",
        "phase": 5,
        "route": ["coding.agent:run_step", "limit parser", "notification"],
        "evidence": ["limit_state", "telegram_summary"],
        "test_name": "test_codex_limit_event_is_recorded_and_notified",
    },
    {
        "id": "codex_limit_resume",
        "requirement": "stop on Codex limit, check renewal, continue after it renews",
        "phase": 5,
        "route": ["coding.agent:run_step", "scheduler/worker resume"],
        "evidence": ["next_retry_at", "current_step_index", "resumed_task_id"],
        "test_name": "test_codex_limit_pauses_and_resumes_from_saved_step",
    },
    {
        "id": "large_codex_stepwise",
        "requirement": "implement large plan one piece at a time using Codex",
        "phase": 5,
        "route": ["coding.agent:plan", "coding.agent:run_step loop"],
        "evidence": ["step_invocations", "step_outputs", "current_step_index"],
        "test_name": "test_codex_large_plan_runs_one_step_at_a_time",
    },
    {
        "id": "wait_read_next_step",
        "requirement": "wait for Codex to finish one step, read result, then send next piece",
        "phase": 5,
        "route": ["coding.agent:run_step", "workspace inspect", "coding.agent:run_step"],
        "evidence": ["previous_output_in_next_prompt"],
        "test_name": "test_coding_agent_reads_step_result_before_next_prompt",
    },
    {
        "id": "continuous_until_done_or_limited",
        "requirement": "continue slowly until complete unless tool limit is reached",
        "phase": 5,
        "route": ["coding.agent loop"],
        "evidence": ["completion_state", "limit_state", "progress_updates"],
        "test_name": "test_coding_agent_loop_stops_only_on_completion_or_limit",
    },
    {
        "id": "preserve_plan_resume",
        "requirement": "preserve plan and continue from where it stopped",
        "phase": 5,
        "route": ["coding.agent:resume"],
        "evidence": ["plan_artifact_id", "current_step_index"],
        "test_name": "test_coding_agent_resume_preserves_plan_position",
    },
    {
        "id": "outputs_along_way",
        "requirement": "send outputs along the way such as screenshots, files, summaries, code, PowerPoints",
        "phase": 1,
        "route": ["artifact.deliver"],
        "evidence": ["artifact_ids", "delivery_results"],
        "test_name": "test_artifact_delivery_sends_latest_document_artifact",
    },
    {
        "id": "natural_workflow",
        "requirement": "natural task descriptions across desktop, browser, files, coding, schedules, and delivery",
        "phase": 7,
        "route": ["router", "registry", "typed plan validation"],
        "evidence": ["route_decision", "validated_plan", "postcondition_result"],
        "test_name": "test_router_composes_natural_multi_capability_workflow",
    },
]


def test_every_user_requirement_has_a_smoke_spec() -> None:
    ids = [item["id"] for item in REQUIREMENT_SPECS]
    assert len(ids) == 33
    assert len(ids) == len(set(ids))
    for item in REQUIREMENT_SPECS:
        assert item["requirement"]
        assert item["phase"] >= 0
        assert item["route"]
        assert item["evidence"]
        assert item["test_name"].startswith("test_")


def test_phase0_and_phase1_requirements_have_executable_test_names() -> None:
    implemented_names = {
        "test_artifact_delivery_sends_latest_document_artifact",
        "test_artifact_delivery_sends_screenshot_from_task_metadata",
        "test_default_browser_screenshot_plan_delivers_when_user_asks_to_send",
        "test_worker_launchable_app_request_uses_copilot_then_workspace_preview_when_explicit",
        "test_worker_launchable_app_request_does_not_use_copilot_unless_explicit",
        "test_adapter_factory_scaffolds_cached_adapter",
    }
    missing = [
        item["test_name"]
        for item in REQUIREMENT_SPECS
        if item["phase"] in {0, 1} and item["test_name"] not in implemented_names
    ]
    assert missing == []


def test_smoke_log_writer_uses_required_debug_fields(tmp_path) -> None:
    path = write_smoke_log(
        tmp_path,
        "desktop screenshot",
        {
            "input_message": "send me a screenshot",
            "task_id": "task_1",
            "route_decision": {"selected_tools": ["computer.use", "artifact.deliver"]},
            "final_status": "completed",
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "desktop_screenshot.json"
    for key in SMOKE_LOG_KEYS:
        assert key in payload
    assert payload["input_message"] == "send me a screenshot"
    assert payload["route_decision"]["selected_tools"] == ["computer.use", "artifact.deliver"]
