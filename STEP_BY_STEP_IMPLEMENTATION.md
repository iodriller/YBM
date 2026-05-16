# Configurable Agentic Control System - Step By Step Implementation Plan

## 1. Implementation Rules

Follow these rules throughout implementation:

- Do not execute an action unless a validated structured model represents it.
- Do not call any tool adapter directly from channel code.
- Do not let LLM output reach execution without Pydantic validation.
- Do not use terminal, filesystem write, browser, desktop control, Git push, or outbound messages unless the capability is enabled and policy permits it.
- Persist task state before and after every meaningful action.
- Write an audit event for every state transition, policy decision, approval decision, tool call, and failure.
- Keep assistant-specific behavior inside assistant adapters.
- Keep Telegram-specific behavior inside the Telegram channel adapter.

## 2. Repository Layout

Create this layout:

```text
backend/
  pyproject.toml
  src/
    agent_control/
      __init__.py
      config.py
      schemas.py
      main.py
      admin.py
      policy/
      storage/
      channels/
      llm/
      orchestration/
      tools/
  tests/
vscode-extension/
  package.json
  tsconfig.json
  src/
    extension.ts
config/
  config.example.yaml
docs/
scripts/
PHASED_APPROACH.md
STEP_BY_STEP_IMPLEMENTATION.md
README.md
.env.example
```

## 2.1 Retroactive Quality Gates

Maintain these gates as the implementation evolves:

- Add `.gitignore` entries for bytecode, caches, local databases, artifacts, `.env`, virtualenvs, build output, and node modules.
- Never commit `__pycache__` or `.pyc` files.
- Scope matching must not allow prefix escapes such as `C:/safe_evil` matching `C:/safe`.
- Tool completion audit records must include the task ID.
- A task in `awaiting_approval` must be able to resume after the relevant approval is granted.
- Telegram status/log/screenshot commands are not complete until they produce notification responses.
- VS Code terminal control is not complete until the backend can enqueue terminal commands and the extension can poll and dispatch them.
- VS Code terminal output capture through official APIs is limited; rely on terminal-agent subprocess adapters for full stdout/stderr.
- Admin UI control actions must remain capability-gated; the dashboard may observe by default, but it must not bypass adapter and capability configuration.
- Admin UI should support an optional token for non-default deployments.
- `CREATE TABLE IF NOT EXISTS` is acceptable for the local MVP, but migration versioning is required before serious use.

## 3. Step 1 - Bootstrap The Project

### 3.1 Create folders

Create folders for backend source, tests, VS Code extension source, config, docs, and scripts.

### 3.2 Add backend package metadata

Create `backend/pyproject.toml` with:

- Python requirement: `>=3.12`
- Runtime dependencies:
  - `fastapi`
  - `uvicorn`
  - `pydantic`
  - `pydantic-settings`
  - `pyyaml`
  - `sqlalchemy`
  - `sqlmodel`
  - `alembic`
  - `aiogram`
  - `httpx`
  - `structlog`
- Test dependencies:
  - `pytest`
  - `pytest-asyncio`
  - `respx`

### 3.3 Add backend package entry files

Create:

- `backend/src/agent_control/__init__.py`
- `backend/src/agent_control/main.py`

`main.py` should expose a FastAPI app with a basic `/health` endpoint.

### 3.4 Add VS Code extension placeholders

Create:

- `vscode-extension/package.json`
- `vscode-extension/tsconfig.json`
- `vscode-extension/src/extension.ts`

The extension should compile later into a companion bridge. At bootstrap, it only needs a command placeholder and activation entry point.

### 3.5 Add config examples

Create:

- `.env.example`
- `config/config.example.yaml`

The example config must:

- Set Telegram disabled by default.
- Set desktop screenshots disabled by default.
- Set desktop control disabled by default.
- Set terminal execution disabled by default.
- Set filesystem writes disabled by default.
- Show how to configure an LLM profile without placing real secrets in the file.

### 3.6 Add docs

Create:

- `PHASED_APPROACH.md`
- `STEP_BY_STEP_IMPLEMENTATION.md`
- `README.md`

### 3.7 Validate step 1

Run:

```powershell
python -m compileall backend/src
```

Success means the backend package is syntactically valid.

## 4. Step 2 - Implement Shared Schemas And Configuration

### 4.1 Define shared enums

Create enums for:

- `ChannelType`
- `MessageKind`
- `TaskStatus`
- `SubtaskStatus`
- `RiskLevel`
- `Capability`
- `ApprovalStatus`
- `ToolResultStatus`
- `ArtifactType`
- `AuditEventType`
- `ErrorClass`

These enum values must be stable strings because they will be stored in the database and audit log.

### 4.2 Define strict Pydantic base model

Create a strict base model with:

- `extra="forbid"`
- `str_strip_whitespace=True`
- `validate_assignment=True`
- JSON serialization support for enums

All internal command and event models should inherit from this base.

### 4.3 Define communication models

Add:

- `Attachment`
- `VoiceAttachment`
- `InboundMessage`
- `OutboundMessage`
- `CommandEnvelope`

Each inbound message should include:

- message ID
- channel
- sender ID
- chat ID or thread ID
- text
- attachments
- timestamp
- correlation ID

### 4.4 Define task and planning models

Add:

- `PlanStep`
- `PlanModel`
- `TaskRecord`
- `SubtaskRecord`
- `TaskSignal`

The plan model should include:

- objective
- assumptions
- required capabilities
- approval gates
- ordered steps
- success criteria

### 4.5 Define tool models

Add:

- `ToolCallRequest`
- `ToolCallResult`
- `ToolObservation`

Every tool request should include:

- tool name
- capability
- risk level
- scope target
- structured input
- timeout
- idempotency key
- approval requirement flag

### 4.6 Define approval models

Add:

- `ApprovalRequest`
- `ApprovalDecision`

Approval requests should include:

- task ID
- capability
- risk level
- human-readable summary
- structured action payload
- expiration timestamp

### 4.7 Define artifact and audit models

Add:

- `Artifact`
- `AuditEvent`

Artifacts should support:

- text logs
- JSON
- screenshots
- voice files
- transcripts
- generated files
- external links

Audit events should include:

- event type
- actor
- task ID
- correlation ID
- redacted payload
- timestamp

### 4.8 Define configuration models

Create configuration sections:

- `ServerConfig`
- `IdentityConfig`
- `TelegramConfig`
- `ChannelsConfig`
- `LLMProfileConfig`
- `LLMConfig`
- `CapabilityPolicy`
- `ApprovalPolicyConfig`
- `StorageConfig`
- `LoggingConfig`
- `LimitsConfig`
- `VSCodeAdapterConfig`
- `TerminalAdapterConfig`
- `DesktopAdapterConfig`
- `STTAdapterConfig`
- `AdaptersConfig`
- `AppSettings`

### 4.9 Define default capability policies

Default all capabilities to disabled unless they are harmless internal capabilities.

These must be disabled by default:

- `terminal.run`
- `filesystem.read`
- `filesystem.write`
- `vscode.read_state`
- `vscode.write_files`
- `desktop.screenshot`
- `desktop.control`
- `browser.open`
- `browser.control`
- `github.push`
- `dependencies.install`

### 4.10 Implement config loading

Config loading should support:

1. Defaults
2. YAML config file
3. Environment variables
4. Runtime overrides for tests

Environment variables should use:

```text
AGENT_
```

Nested values should use:

```text
__
```

Example:

```text
AGENT_CHANNELS__TELEGRAM__ENABLED=true
```

### 4.11 Implement safe config summary

Add a helper that returns a redacted summary of active configuration.

The summary must not reveal:

- Telegram bot token
- LLM API key
- raw environment variable values
- secrets

### 4.12 Validate step 2

Add tests that verify:

- Default invasive capabilities are disabled.
- Unknown config keys fail validation.
- Invalid capability names fail validation.
- A minimal LLM profile validates.
- Safe summary redacts secret-like values.
- A structured plan validates.
- A tool call requires a valid capability.

Run:

```powershell
$env:PYTHONPATH='backend/src'
pytest backend/tests
```

## 5. Step 3 - Implement Persistence And Audit Log

Create SQL models, repositories, migrations, and audit append helpers.

Do this after step 2 is fully passing.

## 6. Step 4 - Implement Telegram Adapter

Implement Telegram polling, allowlists, text normalization, command handling, and approval callbacks.

Do this after persistence exists.

## 7. Step 5 - Implement Voice Transcription

Implement voice file download, STT adapter interface, transcript artifacts, and transcript-to-task flow.

Do this after Telegram text flow is working.

## 8. Step 6 - Implement LLM Planning

Implement provider abstraction, structured output validation, retry on invalid structured output, and clarification detection.

Do this before execution tools are enabled.

## 9. Step 7 - Implement Policy And Approval Enforcement

Implement policy checks as the single gate before tool execution.

No adapter should be callable without passing through this layer.

## 10. Step 8 - Implement Orchestrator Worker

Implement durable task execution, state transitions, task signals, pause/resume/cancel, and progress notifications.

## 11. Step 9 - Implement VS Code Bridge

Implement the TypeScript extension and backend bridge for workspace state, diagnostics, terminal creation, terminal output, and heartbeat.

Minimum implementation:

- Add backend bridge state models for heartbeat, workspace state, and terminal output observations.
- Add FastAPI endpoints for:
  - `POST /vscode/heartbeat`
  - `POST /vscode/state`
  - `GET /vscode/state`
  - `POST /vscode/terminal-output`
- Require `X-Agent-Control-Token` when the configured VS Code bridge token env var is set.
- Update the VS Code extension to collect workspace folders, active file, diagnostics count, and send state to the backend.
- Add a local extension command to create a terminal as the future hook for backend-requested terminal work.
- Add backend tests for state update, state retrieval, and auth denial.
- Add a terminal-command queue:
  - `POST /vscode/terminal-commands`
  - `GET /vscode/terminal-commands?instance_id=...`
- Update the extension to poll queued commands, create/reuse terminals, send text, and post a dispatch observation.

## 12. Step 10 - Implement Coding Assistant Adapter

Implement the generic terminal-agent adapter with command templates, output streaming, state detection, and limit detection.

Minimum implementation:

- Add a coding-assistant adapter config section with `enabled`, `command_template`, `working_dir`, `timeout_seconds`, `output_limit_chars`, and limit-detection patterns.
- Implement `GenericTerminalAgentAdapter` using `asyncio.create_subprocess_exec` with `shell=False`.
- Fill `{prompt}` placeholders from structured tool input.
- Classify successful completion, generic failure, rate limit, and usage limit.
- Return a `ToolCallResult` with stdout, stderr, return code, and detected state.
- Keep execution behind `ToolExecutor` and policy checks; Telegram and planner code must not call the adapter directly.
- Add tests using a local Python subprocess, not a real coding assistant.

## 13. Step 11 - Implement Observation And Artifacts

Implement screenshot adapter, artifact service, retention policies, and Telegram artifact delivery.

Also implement Telegram command responses for `/status`, `/tasks`, `/task <id>`, `/logs <id>`, and `/screenshot`. Until the screenshot adapter exists, `/screenshot` must explicitly report whether `desktop.screenshot` is disabled or enabled-but-not-yet-implemented.

Minimum implementation:

- Add a file-backed artifact service that stores artifact bytes under `storage.artifact_dir`.
- Add a screenshot service behind `desktop.screenshot`, disabled by default.
- Use Pillow for local screenshot capture when enabled.
- Store screenshots as `ArtifactType.SCREENSHOT`.
- Deliver screenshot artifacts through Telegram polling when an outbound message references screenshot artifact IDs.
- Add tests with a fake screenshot adapter.

## 14. Step 12 - Implement Recovery And Resume

Implement error classification, retries, backoff, usage-limit handling, and human intervention summaries.

Minimum implementation:

- Add a retry policy using `limits.max_retries` and `limits.retry_backoff_seconds`.
- Retry only transient, rate-limit, usage-limit, and timeout results.
- Persist retry count, next retry time, and last retry reason in task metadata.
- Move exhausted retries to `blocked` with an intervention summary.
- Add tests for retrying on transient failures.

## 15. Step 13 - Package For Local Use

Add startup scripts, local setup docs, extension packaging, and Windows-specific instructions.

Minimum implementation:

- Add PowerShell scripts for backend startup, database initialization, test execution, and VS Code extension packaging.
- Add a PowerShell script for Telegram polling.
- Add `docs/LOCAL_SETUP.md` with safe local setup instructions.
- Keep scripts non-destructive and local-first.

## 16. Step 14 - Implement Admin Control Web UI

Add a built-in FastAPI admin UI for local monitoring and configuration visibility.

Minimum implementation:

- Add `/admin` as a simple HTML page served by the backend.
- Add `/admin/api/summary` for redacted configuration, recent tasks, recent audit events, and VS Code bridge status.
- Add `/admin/api/tasks` for recent task inspection.
- Add `/admin/api/audit` with optional task filtering.
- Add `/admin/api/vscode` for heartbeat, workspace state, pending terminal commands, and recent terminal observations.
- Add task control endpoints for pause, resume, and cancel by recording structured task signals and updating task status.
- Add config endpoints for Telegram and the default orchestrator LLM profile.
- Write non-secret config to `config/config.yaml`, and keep API keys and bot tokens in `.env` or process environment variables.
- Add a one-click LLM health test that uses the configured default LLM profile.
- Add optional admin token enforcement through `server.admin_token_env`.
- Do not allow terminal command dispatch unless:
  - `adapters.vscode.enabled` is true.
  - `terminal.run` is enabled.
  - `terminal.run` does not require approval for direct admin dispatch.
- Document that persistent config editing is intentionally deferred until a config persistence model exists.
- Document the minimal Telegram-to-task end-to-end test.
- Add tests for the page, summary endpoint, task signals, default terminal-command rejection, and enabled terminal-command queueing.

## 17. MVP Done Definition

The MVP is done when:

- Telegram text creates a task.
- Telegram voice creates a transcribed task.
- A structured plan is generated.
- Approval flow works through Telegram.
- Pause, resume, cancel, and status work.
- VS Code bridge reports workspace state.
- A configured terminal agent can run when terminal access is enabled.
- Screenshots work only when enabled.
- All important actions are audited.
- The admin UI shows redacted config, tasks, audit logs, and VS Code bridge status without enabling unsafe actions by default.
