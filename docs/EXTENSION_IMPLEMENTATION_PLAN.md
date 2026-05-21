# Extension Implementation Plan

This document turns the capability requirements into a minimal, extensible implementation plan. The goal is not to build one-off handlers for each example. The goal is a small set of durable primitives that can compose naturally from Telegram:

```text
Telegram message
-> orchestrator/router
-> typed plan
-> registry capability lookup
-> policy check
-> tool adapter execution
-> typed postcondition validation
-> artifact delivery
-> concise Telegram/admin result
```

## Target Behavior

The system should let the user describe work naturally, while still routing to safe, auditable adapters:

- Desktop work uses `computer.use` only when desktop APIs are needed.
- Browser work uses the Chrome DevTools browser adapter before desktop control.
- Folder and document work uses filesystem/document APIs before File Explorer automation.
- Coding work uses Codex or GitHub Copilot only when the user explicitly says to use that tool.
- Large coding work persists a plan, runs one step at a time, records progress, and resumes after interruption or tool limits.
- Outputs are first-class artifacts. Screenshots, generated files, PDFs, PowerPoints, workspaces, preview URLs, and summaries must be visible in the admin trace and deliverable to Telegram.

## Architecture Principles

1. Prefer APIs over screen driving.
   Use filesystem, PDF, browser DevTools, and PowerPoint libraries when possible. Use `computer.use` as the fallback for visual desktop interaction.

2. Make every capability a registered tool.
   The orchestrator should not know implementation details. It should choose from a registry with operation schemas, output schemas, risk, required capabilities, and postconditions.

3. Keep external coding agents explicit.
   Codex and GitHub Copilot are powerful and may consume usage limits. They should run only when the user names them.

4. Make artifacts durable.
   Every meaningful output should be saved, linked to a task, and optionally sent to Telegram.

5. Keep approvals simple.
   If the admin UI sets a capability to full access, do not create an extra approval loop. If the mode requires approval, the approval action must be available and obvious.

6. Validate outcomes, not just execution.
   A tool call completing is not enough. The validator must check that the requested visible/file/browser/coding outcome happened.

7. Dynamic adapter creation is scaffold-first.
   `adapter.factory` can generate reviewed adapter proposals into a cache/workspace. It should not import and run unreviewed code automatically.

## Implementation Status

Status: complete through the minimal Phase 0-8 implementation.

Implemented:

- Route decisions and fulfillment checks are persisted enough to explain why tools were selected.
- Artifact delivery is a registered `artifact.deliver` tool for sending files and screenshots to Telegram.
- Documents use `document.manage` for PDF text/summary flows and minimal PowerPoint create/update artifacts.
- Desktop, browser, filesystem, document, artifact, coding-agent, schedule, and adapter-factory work all route through the registry.
- `coding.agent` is the preferred explicit Codex/GitHub Copilot abstraction; Codex and Copilot are not used unless named by the user.
- `schedule.manage` plus the `run-scheduler` service provide recurring task creation.
- Tool output schemas are registered for multi-operation tools, and postcondition validation covers previews, workspaces, artifacts, documents, coding agents, browser state, desktop observations, file organization, GitHub PR placeholders, external commands, and schedule creation.
- Admin diagnostics expose service health, scheduler state, schedule rows, database counts, and a registry/tool summary.
- Backend dependencies were cleaned: unused `aiogram`, `alembic`, `sqlalchemy`, and `sqlmodel` were removed; `pypdf` was added because `document.manage` imports it for PDF extraction.

Still intentionally constrained:

- `adapter.factory` scaffolds proposals only; it does not runtime-load generated code automatically.
- `desktop.screenshot`, `vscode.copilot_terminal`, and `coding_assistant` remain compatibility wrappers, but new plans should prefer `computer.use`, `coding.agent`, and typed tools.
- Live Codex/GitHub Copilot limits depend on what their installed CLIs print. The adapter captures known rate/usage-limit patterns and surfaces them, but cannot invent unavailable quota metadata.
- Browser automation targets Chrome DevTools first. Full desktop computer use remains the fallback for UI that has no safer API.

## Initial Gap Summary

The repo already had useful foundations:

- Intake, classification, memory, persisted tasks, worker loop, audit log, admin UIs.
- `computer.use` for observe/act/run_goal.
- `browser.open` and `browser.control` for Chrome DevTools tasks.
- `filesystem.manage` for scoped folder inspection/search/organization.
- Workspace tooling for generated apps and localhost previews.
- VS Code/Copilot bridge and a generic `coding_assistant` adapter.
- Typed postconditions for some workspace/browser/desktop outcomes.

The important gaps were:

- Artifact delivery, document management, explicit coding-agent routing, schedule storage/service, broader browser operations, tool output schemas, and postcondition coverage are now implemented in the minimal form.
- Durable long-running coding-agent sessions are represented by workspace/session metadata and task history, not a separate session table yet. That is enough for the current minimal slice; add a dedicated table only when multi-day coding sessions need richer state transitions.
- Compatibility adapters remain in place to avoid breaking existing tasks and tests, but new default routing has moved toward typed tools.

## Implementation Phases

### Phase 0: Stabilize Routing, Evidence, And Smoke Harness

Purpose: make failures observable before adding more adapters.

Implementation steps:

1. Add a smoke-test runner that can create Telegram-style tasks without relying on live Telegram.
   - Proposed path: `backend/tests/smoke/` for Python fixtures and `scripts/run_smoke_suite.ps1` for local manual runs.
   - Store manual smoke logs under `.agent_control/smoke_runs/<timestamp>/`.

2. Standardize smoke log records:
   - Input message.
   - Created task ID.
   - Classification result.
   - Selected plan name and steps.
   - Tool invocations and status.
   - Artifacts created.
   - Telegram notification payload summary.
   - Final task status and validator result.

3. Fix routing rule:
   - If the user does not explicitly say `Codex`, `GitHub Copilot`, or `Copilot`, do not route to external coding agents.
   - Generic app creation can still use local workspace generation or local LLM planning, but not Codex/Copilot.

4. Make admin full-access modes authoritative:
   - Full desktop control means no separate per-task approval.
   - Full filesystem write means scoped writes can proceed inside allowed roots.
   - Full browser control means Chrome actions can proceed.

5. Add a small route decision trace:
   - Persist why a route was selected.
   - Persist why a more powerful route was not selected.
   - Example: `external_agent_skipped: user did not explicitly request Codex/Copilot`.

Exit criteria:

- A non-live smoke test can create a task and produce a trace.
- The trace shows route decision, plan, tools, artifacts, and final notification.
- Requests without explicit Codex/Copilot never use those providers.

### Phase 1: Artifact Delivery

Purpose: make screenshots/files/outputs deliverable, not just stored.

New tool:

```text
artifact.deliver
```

Operations:

- `send_file`
- `send_latest`
- `send_screenshot`
- `list_artifacts`

Implementation steps:

1. Add schemas:
   - `ArtifactDeliverInput`
   - `ArtifactDeliveryOutput`
   - Add or extend artifact metadata with `path`, `mime_type`, `caption`, `telegram_file_id`, `delivered_at`.

2. Add adapter:
   - Proposed path: `backend/src/agent_control/tools/artifact_delivery.py`
   - It should resolve only task-linked artifacts or files inside configured output roots.
   - It should reject path escapes.

3. Extend Telegram notifications:
   - Existing photo sending should remain.
   - Add document sending for PDFs, PowerPoints, ZIPs, text files, and generated outputs.
   - If Telegram delivery fails, include the local path in the final text and mark the artifact as not delivered.

4. Extend postconditions:
   - `artifact_created`
   - `artifact_delivered`

5. Update registry:
   - Add `artifact.deliver` with clear operation descriptions and output schema.

Exit criteria:

- "send me the screenshot" sends an actual Telegram photo or reports a precise delivery error.
- "send me the PDF file" sends the document if it is task-linked and inside allowed roots.

### Phase 2: Document Management

Purpose: cover PDF and PowerPoint flows with file APIs instead of desktop automation.

New tool:

```text
document.manage
```

Operations:

- `summarize_pdf`
- `create_presentation`
- `update_presentation`
- `extract_text`
- `inspect_document`

Implementation steps:

1. Add dependencies:
   - `pypdf` is included for PDF extraction.
   - PowerPoint creation/update uses a minimal stdlib `.pptx` writer in this implementation, so `python-pptx` is not required yet.

2. Add schemas:
   - `DocumentInspectInput`
   - `PdfSummarizeInput`
   - `PresentationCreateInput`
   - `PresentationUpdateInput`
   - `DocumentManageOutput`

3. Add adapter:
   - Proposed path: `backend/src/agent_control/tools/document_manage.py`
   - PDF summarization should extract text, chunk if needed, summarize with local LLM, and save a text summary artifact.
   - PowerPoint creation should produce a `.pptx` artifact under the task workspace.
   - PowerPoint update should use the previous task artifact when the user asks for revisions.

4. Add conversation/task artifact linking:
   - A follow-up like "change the title slide" must find the latest presentation artifact in the same Telegram chat or task thread.

5. Add postconditions:
   - `document_summarized`
   - `presentation_created`
   - `presentation_updated`
   - `file_delivered`

Exit criteria:

- Opening/summarizing a PDF can be done without driving a PDF viewer.
- Creating/updating/sending a PowerPoint works from Telegram.

### Phase 3: Filesystem And Desktop Workflows

Purpose: make file organization and desktop observation reliable.

Enhance existing tools:

```text
filesystem.manage
computer.use
```

Implementation steps:

1. Extend `filesystem.manage` operations:
   - `find_by_description`
   - `resolve_desktop_item`
   - `open_file`
   - `collect_folder_snapshot`

2. Add folder aliases:
   - `desktop`
   - `documents`
   - `downloads`
   - configured named roots.

3. Keep organization as manifest-first:
   - `organize_plan` returns proposed moves/copies.
   - `apply_manifest` applies only approved scoped operations.
   - The final result lists changed paths.

4. Extend `computer.use observe`:
   - Always create a screenshot artifact.
   - Include active window, visible windows, cursor, monitor geometry, and UI Automation summary.
   - If vision LLM is unavailable, return metadata and state that visual summarization is unavailable.

5. Route common desktop/file tasks:
   - "what is on my desktop" -> `computer.use observe` plus local summary.
   - "send me a screenshot" -> `computer.use observe` then `artifact.deliver send_screenshot`.
   - "open folder on desktop, open PDF, tell me what it is about" -> filesystem resolution plus `document.manage summarize_pdf`, using desktop control only if needed.

Exit criteria:

- Desktop screenshot and observation return both a summary and a real deliverable screenshot.
- Folder organization returns a manifest and changed-path summary.
- PDF discovery/summarization/sending works inside configured roots.

### Phase 4: Browser Workflows

Purpose: make browser tasks useful without relying on desktop control.

Enhance existing tool:

```text
browser.control
```

Operations to add or harden:

- `summarize_page`
- `check_page_update`
- `research_pages`
- `fill_form_step`
- `extract_page_state`

Implementation steps:

1. Add output schemas for browser operations:
   - Current URL, title, visible text summary, screenshot path, links visited, form fields found, errors.

2. Add many-page research:
   - Search query.
   - Page limit.
   - Per-page extraction.
   - Deduplicated sources.
   - Final synthesis.
   - Save a `.md` or `.json` artifact with visited pages and summaries.

3. Add page update checks:
   - For show/episode checks, extract date/title markers and compare with saved prior observations when available.

4. Add form fill safety:
   - First pass extracts fields and produces a fill plan.
   - Second pass fills fields.
   - Submission requires explicit user wording or full browser control plus a policy rule that allows submit actions.

5. Screenshot delivery:
   - Browser screenshot operations should save artifacts and route through `artifact.deliver`.

Exit criteria:

- Search/open-first-result/summarize works.
- Browser screenshot delivery works.
- Many-page research logs every visited page.
- Form filling can fill fields and stops before submission unless allowed.

### Phase 5: Explicit Coding Agent Abstraction

Purpose: unify Codex and GitHub Copilot while enforcing explicit user choice.

New tool:

```text
coding.agent
```

Providers:

- `codex`
- `github_copilot`

Operations:

- `plan`
- `run_step`
- `run_goal`
- `status`
- `limits`
- `resume`
- `stop`

Implementation steps:

1. Add schemas:
   - `CodingAgentInput`
   - `CodingAgentPlanInput`
   - `CodingAgentRunStepInput`
   - `CodingAgentStatusInput`
   - `CodingAgentOutput`
   - `CodingAgentLimitState`

2. Add persistent session fields:
   - `provider`
   - `workspace_dir`
   - `session_id`
   - `plan_artifact_id`
   - `current_step_index`
   - `last_prompt`
   - `last_response`
   - `limit_state`
   - `next_retry_at`

3. Add provider commands:
   - Codex: use installed `codex exec` in the selected workspace.
   - Copilot: use installed Copilot CLI where available.
   - Both providers must support timeout, captured stdout/stderr, exit code, and usage/limit parsing.

4. Add prompt files:
   - No prompt text in Python.
   - Put provider prompts under `backend/src/agent_control/prompts/tools/coding_agent/`.

5. Replace historical routes:
   - Deprecate direct use of `vscode.copilot_terminal` for new plans.
   - Keep compatibility for existing tasks until `coding.agent` covers it.

6. Add deterministic routing:
   - "use Codex ..." -> `coding.agent provider=codex`.
   - "use GitHub Copilot ..." -> `coding.agent provider=github_copilot`.
   - No explicit provider -> do not use either provider.

7. Add stepwise large-task loop:
   - Large requirement -> create plan artifact.
   - Run step 1.
   - Inspect result.
   - Run step 2.
   - Continue until complete, stopped, failed, or limited.

8. Add limit handling:
   - Parse known usage/limit text.
   - If limit reached and renewal time is known, set `next_retry_at` and pause.
   - If renewal is unknown, stop and notify user.
   - Do not burn retries on hard limit errors.

Exit criteria:

- Codex and Copilot are never used unless named.
- Large Codex work persists a plan and can resume.
- Usage/limit information is visible in admin and Telegram summaries when available.

### Phase 6: Scheduler

Purpose: support recurring tasks.

Implementation status: complete for the minimal local scheduler.

New service:

```text
run-scheduler
```

New tool:

```text
schedule.manage
```

Operations:

- `create`
- `list`
- `pause`
- `resume`
- `delete`
- `run_now`

Implementation steps:

1. Add schedule storage:
   - `schedule_id`
   - `source_channel`
   - `source_chat_id`
   - `objective`
   - `cadence`
   - `timezone`
   - `next_run_at`
   - `last_run_at`
   - `enabled`
   - `metadata`

2. Add scheduler loop:
   - Poll due schedules.
   - Create normal tasks from schedule objectives.
   - Record generated task IDs.

3. Add routing:
   - "set up a scheduled job every day to..." -> `schedule.manage create`.
   - "pause that scheduled job" -> `schedule.manage pause`.

4. Add admin UI:
   - Schedules table.
   - Last run, next run, status.
   - Pause/resume/delete buttons.

Exit criteria:

- A daily web check can be scheduled, creates due tasks, and sends results to Telegram.

Implemented notes:

- `schedule.manage` is registered under the dedicated `schedule.manage` capability.
- `run-scheduler` is a supervised service launched by `scripts/start_stack.ps1`.
- Schedules are stored in SQLite with status, cadence, next run, last run, and last generated task.
- Due schedules create normal tasks, preserving `source_schedule_id`, `source_chat_id`, and schedule metadata.
- Default routing supports create/list and pause/resume/delete/run-now when a `schedule_<id>` is named.
- Admin summary and `/admin/api/schedules` expose current schedules.

### Phase 7: Registry, Schemas, And Postconditions

Purpose: make the orchestration layer scalable.

Implementation status: complete for the typed minimal registry.

Implementation steps:

1. Add output schemas per operation, not just input schemas.

2. Register postcondition templates per operation:
   - Desktop observed.
   - Screenshot delivered.
   - Browser page summarized.
   - Browser screenshot delivered.
   - Folder organized.
   - PDF summarized.
   - File delivered.
   - Presentation created/updated.
   - Coding agent step completed.
   - Coding agent limit captured.
   - Schedule created.

3. Validate LLM-generated plans before persisting:
   - Tool exists.
   - Operation exists.
   - Input schema validates.
   - Required capability is enabled or the plan is blocked with a clear reason.
   - Postconditions are known.

4. Add plan repair:
   - If an LLM plan references a missing tool or invalid operation, repair it using the registry.
   - If it cannot be repaired, create a blocked task with a useful message.

5. Add registry grouping:
   - `desktop`
   - `browser`
   - `filesystem`
   - `documents`
   - `coding_agents`
   - `schedules`
   - `artifacts`
   - `adapter_factory`

Exit criteria:

- Invalid plans fail before entering the worker loop.
- Admin trace shows selected tool group, operation, schema result, and postcondition result.

Implemented notes:

- Registry validation checks registered tool names, enabled state, operation/input schemas, and adds required capabilities before execution.
- Planner retries once with a structured repair prompt when registry validation rejects an LLM plan.
- Multi-operation tools now expose output schemas per operation, using shared output contracts where the operation family returns the same shape.
- Fulfillment validation covers schedule creation and the previously added desktop/browser/filesystem/document/artifact/coding outputs.
- Admin exposes registry groups and schemas through `tool_registry`.

### Phase 8: Admin UI And Diagnostics

Purpose: make operations debuggable.

Implementation status: complete for the minimal diagnostic surface.

Implementation steps:

1. Add service indicators:
   - Backend.
   - Worker.
   - Telegram polling.
   - Local LLM.
   - Browser DevTools.
   - Computer-use screenshot/control.
   - Codex CLI.
   - Copilot CLI.
   - Scheduler.

2. Add task trace grouping:
   - Intake.
   - Routing.
   - Plan.
   - Policy.
   - Tools.
   - Validation.
   - Notification.

3. Add artifact panel:
   - Screenshot preview.
   - File list.
   - Send/resend buttons.
   - Local path and preview URL.

4. Add coding-agent panel:
   - Active provider.
   - Workspace.
   - Current step.
   - Last prompt/response.
   - Usage/limit state.

5. Add schedule panel:
   - Due jobs.
   - Last result.
   - Pause/resume.

Exit criteria:

- A failed task can be diagnosed from one page without checking raw logs first.

Implemented notes:

- Service status now includes the scheduler supervisor.
- Streamlit diagnostics show schedules and the tool registry summary.
- FastAPI admin summary includes schedule rows, database schedule counts, and registry metadata.
- Existing task trace continues to group plan steps, timeline, tool requests/results, artifacts, signals, and raw context.

## Cleanup And Removal Plan

Cleanup should happen after replacement tests are passing.

### Remove unused dependencies

Completed dependency cleanup:

- Removed `aiogram`.
- Removed `alembic`.
- Removed `sqlalchemy`.
- Removed `sqlmodel`.
- Added `pypdf`.

Justification:

- The codebase uses custom Telegram handling and direct repository/storage abstractions.
- Keeping unused dependencies increases installation time and confusion.
- `rg` confirmed no source, tests, or scripts rely on the removed packages.

### Deprecate overlapping adapters

Candidate:

- `coding_assistant`

Replacement:

- `coding.agent`

Justification:

- Coding providers need provider-specific session state, usage limit parsing, workspace state, and explicit user selection.
- A generic terminal wrapper is useful internally, but the orchestrator should see one typed coding-agent tool.

### Keep compatibility wrappers

Keep, but route new plans elsewhere:

- `desktop.screenshot`
  - Keep as compatibility wrapper.
  - Prefer `computer.use observe` plus `artifact.deliver`.

- `vscode.copilot_terminal`
  - Keep while existing tests/tasks depend on it.
  - Prefer `coding.agent provider=github_copilot` for new plans.

### Restrict adapter.factory

Keep scaffold generation, but do not runtime-load generated code automatically.

Justification:

- It is useful to draft new adapters.
- Running newly generated code without review is not a minimal safe architecture.

## Restructuring Plan

### Tool modules

Target structure:

```text
backend/src/agent_control/tools/
  registry.py
  contracts.py
  artifact_delivery.py
  browser.py
  coding_agent.py
  computer_use.py
  document_manage.py
  filesystem_manage.py
  schedule_manage.py
  local_workspace.py
  adapter_factory.py
```

### Prompts

Target structure:

```text
backend/src/agent_control/prompts/
  base/
  routing/
  tasks/
  tools/
    browser/
    coding_agent/
    computer_use/
    documents/
    filesystem/
```

Rules:

- No static prompts in `.py` files.
- Python loads prompt markdown by name.
- Prompts should state compact context, allowed tools, expected JSON shape, and validation criteria.

### Storage

Add tables or repository models for:

- Tool output schema version if needed.
- Artifact delivery state.
- Coding agent sessions.
- Schedules.

### Config

Consolidate around YAML plus `.env` for secrets.

Rules:

- `.env` stores secrets only.
- YAML stores modes, adapter settings, roots, ports, and defaults.
- Admin UI writes YAML for non-secret config.

## Testing Plan

Testing has three layers:

1. Unit tests with fake adapters and no external processes.
2. Worker integration tests using local repositories and fake Telegram notifications.
3. Manual smoke tests against this Windows machine, Chrome, LocalDeploy, Codex, and Copilot.

Every smoke test should write a run log under:

```text
.agent_control/smoke_runs/<timestamp>/<test_name>.json
```

Each log should include:

- `input_message`
- `task_id`
- `route_decision`
- `plan_name`
- `plan_steps`
- `tool_invocations`
- `artifacts`
- `notification_summary`
- `final_status`
- `validator_result`
- `failure_reason`
- `local_paths`
- `urls`

## Requirement Test Matrix

### Desktop Observation

Requirement:

```text
Ask what is on my desktop right now.
```

Expected route:

```text
computer.use observe
```

Assertions:

- Task completes.
- Screenshot artifact exists.
- Observation includes active window and visible windows when available.
- Telegram response includes a concise desktop summary.
- Admin trace includes screenshot path and observation metadata.

### Send Desktop Screenshot

Requirement:

```text
Ask it to send me a screenshot of the desktop.
```

Expected route:

```text
computer.use observe -> artifact.deliver send_screenshot
```

Assertions:

- Screenshot file exists.
- Telegram photo/document send is attempted.
- Delivery result is recorded.
- If delivery fails, Telegram text includes local screenshot path and error.

### Open Desktop Folder And Summarize PDF

Requirement:

```text
Open one of the folders on my desktop, open a PDF file inside it, and tell me what the PDF is about.
```

Expected route:

```text
filesystem.manage resolve_desktop_item
-> filesystem.manage search/find_by_description
-> document.manage summarize_pdf
```

Fallback:

```text
computer.use run_goal
```

Assertions:

- PDF file path is resolved inside allowed roots.
- PDF text is extracted.
- Summary artifact is created.
- Telegram response includes summary and file path.
- Desktop control is only used if filesystem route cannot satisfy the request.

### Send Found Or Opened PDF

Requirement:

```text
Ask it to send me the PDF file it found or opened.
```

Expected route:

```text
artifact.deliver send_latest
```

Assertions:

- The latest PDF artifact/path is found from task/chat context.
- Telegram document send is attempted.
- Delivery result is recorded.

### Organize Documents Folder

Requirement:

```text
Organize my documents in a particular folder based on criteria I give.
```

Expected route:

```text
filesystem.manage inspect_folder
-> filesystem.manage organize_plan
-> filesystem.manage apply_manifest
```

Assertions:

- Plan is non-mutating.
- Manifest contains only paths inside allowed roots.
- Apply step changes only approved paths.
- Final Telegram response lists changed paths and unchanged/skipped files.

### Browser Open Website Screenshot

Requirement:

```text
Open the browser, go to a website, and send me a screenshot.
```

Expected route:

```text
browser.open or browser.control navigate
-> browser.control screenshot
-> artifact.deliver send_screenshot
```

Assertions:

- Chrome DevTools connection works or launches Chrome profile.
- Page URL matches request.
- Screenshot artifact exists.
- Telegram delivery is attempted and logged.

### Browser Search First Result Summary

Requirement:

```text
Open browser, search for something, go into the first web page, and tell me what that page is about.
```

Expected route:

```text
browser.control search
-> browser.control navigate first result
-> browser.control summarize_page
```

Assertions:

- Search page opens.
- First result URL is captured.
- Page title and summary are returned.
- Visited URL is logged.

### Check Show Or Episode Update

Requirement:

```text
Go to a web page and check whether a new show or new episode came out.
```

Expected route:

```text
browser.control navigate
-> browser.control check_page_update
```

Assertions:

- Page content is extracted.
- Relevant dates/titles are identified when possible.
- Result says whether an update was found or not enough evidence was available.
- If repeated, previous observation is used for comparison.

### Fill Web Form

Requirement:

```text
Go to a web page and start filling out a form based on information I give.
```

Expected route:

```text
browser.control extract_page_state
-> browser.control fill_form_step
```

Assertions:

- Form fields are detected.
- Fill plan maps user data to fields.
- Fields are filled.
- Submission does not happen unless explicitly requested and allowed.
- Screenshot after fill is saved.

### Use Codex Explicitly

Requirement:

```text
Say "use Codex" and have it use Codex for the task.
```

Expected route:

```text
coding.agent provider=codex
```

Assertions:

- Route decision records explicit provider request.
- Codex command is invoked in configured workspace.
- Output, exit code, and artifacts are logged.
- If Codex is unavailable, the task fails clearly.

### Use GitHub Copilot Explicitly

Requirement:

```text
Say "use GitHub Copilot" and have it use GitHub Copilot for the task.
```

Expected route:

```text
coding.agent provider=github_copilot
```

Assertions:

- Route decision records explicit provider request.
- Copilot CLI command is invoked in configured workspace.
- Output, exit code, and artifacts are logged.

### Do Not Use Codex Or Copilot Unless Named

Requirement:

```text
Do not use Codex or GitHub Copilot unless I specifically say to use them.
```

Expected route:

```text
No coding.agent provider when provider name is absent.
```

Assertions:

- Generic app request does not invoke Codex or Copilot.
- Trace records why external provider was skipped.

### Large App With Codex

Requirement:

```text
Use Codex and start creating an app for mobile deployment of an LLM.
```

Expected route:

```text
coding.agent plan -> coding.agent run_step loop
```

Assertions:

- Plan artifact is created first.
- Current step is persisted.
- Workspace path is recorded.
- Telegram status includes current step and next step.

### Weird Specific App With Codex

Requirement:

```text
Use Codex for a weird app idea, like hamsters and mice.
```

Expected route:

```text
coding.agent provider=codex
```

Assertions:

- Workspace is created.
- Codex receives the actual idea and constraints.
- Generated files are detected.
- Preview is launched if the project type supports it.

### Coding Status With Screenshots

Requirement:

```text
Tell me where it is in the coding process, and give me a screenshot of VS Code and the file directory when I ask.
```

Expected route:

```text
coding.agent status
-> computer.use observe or desktop screenshot
-> artifact.deliver send_screenshot
```

Assertions:

- Status includes provider, workspace, step, and latest output.
- Screenshot artifact is created and delivered or local path is reported.

### Create PowerPoint With Codex

Requirement:

```text
Use Codex and create me a PowerPoint presentation.
```

Preferred route:

```text
document.manage create_presentation
```

Optional route when explicitly requested:

```text
coding.agent provider=codex -> document.manage inspect/send
```

Assertions:

- `.pptx` artifact exists.
- Telegram document delivery is attempted.
- Slide count and title are logged.

### Update PowerPoint Follow-Up

Requirement:

```text
Send another message asking for changes, update PowerPoint, send revised output.
```

Expected route:

```text
document.manage update_presentation
-> artifact.deliver send_file
```

Assertions:

- Previous presentation artifact is resolved from chat/task context.
- New revision artifact is created.
- Original file is not overwritten unless explicitly requested.
- Revised file is delivered.

### Large Requirement Plan First

Requirement:

```text
Give a large website/app requirement, have it first prepare a plan.
```

Expected route:

```text
planner creates plan artifact before coding.agent run_step
```

Assertions:

- No coding step starts before plan exists.
- Plan is visible in admin.
- Telegram summary gives the first few steps and current status.

### Explicit Tool Combination

Requirement:

```text
Use Codex and search for this, or use GitHub Copilot and web search.
```

Expected route:

```text
browser.control/research_pages
-> coding.agent provider=<explicit provider>
```

Assertions:

- Only explicitly named tools are used.
- Search artifacts are passed to coding agent as context.
- Trace shows handoff from browser to coding agent.

### Many-Page Web Search

Requirement:

```text
Search many pages, even 50 pages, and give me results.
```

Expected route:

```text
browser.control research_pages page_limit=50
```

Assertions:

- Page limit is respected.
- Each visited URL is logged.
- Failures per page do not fail the whole task unless all pages fail.
- Final summary includes sources and confidence.

### Create Adapter

Requirement:

```text
Ask it to create an adapter to access something.
```

Expected route:

```text
adapter.factory create proposal
```

Assertions:

- Adapter proposal is saved under configured adapter cache/workspace.
- Generated code is not auto-loaded into runtime.
- Response explains review/activation step.

### Scheduled Job

Requirement:

```text
Set up a scheduled job that runs every day.
```

Expected route:

```text
schedule.manage create
```

Assertions:

- Schedule row exists.
- Next run time is calculated.
- Admin UI shows schedule.
- Due scheduler creates a normal task.

### Scheduled Job With Coding Provider

Requirement:

```text
Use Codex or GitHub Copilot in the workspace to prepare code for the scheduled job.
```

Expected route:

```text
coding.agent provider=<explicit provider>
-> schedule.manage create
```

Assertions:

- Provider is explicit.
- Workspace artifact is created.
- Schedule references the workspace or script artifact.

### Codex Availability And Limits

Requirement:

```text
Ask current availability of Codex and whether almost at limit.
```

Expected route:

```text
coding.agent limits provider=codex
```

Assertions:

- If CLI exposes limit info, parse and return it.
- If not exposed, say unavailable and include latest known task-level limit event.

### Stop And Continue After Limit

Requirement:

```text
Stop when Codex reaches a limit, check renewal, continue after renew.
```

Expected route:

```text
coding.agent run_step -> limit_state -> scheduler/worker resume
```

Assertions:

- Limit event pauses the task.
- `next_retry_at` is set when known.
- Task resumes from saved step.
- It does not restart from step 1.

### Continuous Stepwise Coding

Requirement:

```text
Implement a large plan one piece at a time using Codex, wait for each result, then send the next piece.
```

Expected route:

```text
coding.agent plan -> run_step -> inspect -> run_step loop
```

Assertions:

- Each step has a separate invocation.
- Result of prior step is read before next step prompt.
- Current step survives worker restart.
- Telegram sends progress updates at configured intervals.

### Output Delivery Along The Way

Requirement:

```text
Send outputs along the way: screenshots, files, summaries, code, PowerPoints, final result.
```

Expected route:

```text
artifact.deliver
```

Assertions:

- Every output is task-linked.
- Deliveries are logged.
- Failed deliveries include actionable local paths.

## Minimal Build Order

Recommended order:

1. Phase 0: smoke harness, route trace, explicit-provider guard.
2. Phase 1: artifact delivery.
3. Phase 2: document management.
4. Phase 3: filesystem/desktop reliability.
5. Phase 4: browser reliability.
6. Phase 5: explicit coding agent.
7. Phase 6: scheduler.
8. Phase 7: registry schemas and postconditions.
9. Phase 8: admin diagnostics.
10. Cleanup unused dependencies and deprecated wrappers after replacement tests pass.

This order gets quick value first: screenshots/files start returning to Telegram, PDF/PPTX work becomes possible, and routing becomes safer before the larger coding-agent and scheduler work.
