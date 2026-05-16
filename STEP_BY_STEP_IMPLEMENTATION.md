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

## 12. Step 10 - Implement Coding Assistant Adapter

Implement the generic terminal-agent adapter with command templates, output streaming, state detection, and limit detection.

## 13. Step 11 - Implement Observation And Artifacts

Implement screenshot adapter, artifact service, retention policies, and Telegram artifact delivery.

## 14. Step 12 - Implement Recovery And Resume

Implement error classification, retries, backoff, usage-limit handling, and human intervention summaries.

## 15. Step 13 - Package For Local Use

Add startup scripts, local setup docs, extension packaging, and Windows-specific instructions.

## 16. MVP Done Definition

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

