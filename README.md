# Configurable Agentic Control System

Local-first control plane for sending Telegram text or voice commands to an agentic orchestration layer that can safely coordinate VS Code, coding assistants, terminal tools, desktop observations, and future automation adapters.

## Current Status

Implemented:

- Project scaffold
- Phased project plan
- Detailed step-by-step implementation plan
- Pydantic schemas for commands, tasks, plans, tools, approvals, artifacts, and audit events
- Strict configuration models with safe default capability policies
- SQLite persistence, repositories, audit logging, and redaction
- Minimal Telegram Bot API polling wrapper, update normalizer, allowlist checks, command parsing, and task intake
- Telegram gateway responses for direct questions, plain `status`, task lists, task details, logs, and screenshot capability state
- Telegram voice download/transcription service with transcript artifacts
- LLM provider abstraction and structured planner with plan persistence
- Capability policy engine with approval requests
- Gated tool executor and minimal durable task worker
- Minimal VS Code bridge endpoints, extension state sync, and terminal command queue
- Generic terminal coding-assistant adapter
- Local generated-workspace adapter that writes files under `.agent_control/workspaces` and launches localhost web previews
- File-backed artifacts, optional screenshot capture, and Telegram screenshot artifact delivery
- Retry policy with durable retry metadata
- Windows setup scripts and local setup documentation
- Basic FastAPI health endpoint
- Built-in FastAPI admin UI for safe config summary, task monitoring, audit logs, VS Code bridge state, and gated task controls
- Admin configuration writes for Telegram and the default OpenAI-compatible orchestrator LLM profile
- LLM-based Telegram task classification with readable audit events
- Admin audit filters, capability access modes, and database summary
- One-command local stack launcher for LocalDeploy, backend, Telegram polling, and worker
- Worker completion notifications back to Telegram with tool output summaries
- Concise per-Telegram-chat memory for durable facts such as the user's name

Not implemented yet:

- Direct GitHub Copilot Chat panel response capture through VS Code APIs
- Persistent editable configuration for every advanced capability and adapter field

## Development

Start the whole local stack:

```powershell
.\scripts\start_stack.ps1
```

This initializes SQLite, starts LocalDeploy if needed, starts the backend, starts Telegram polling, and starts the worker. Open:

```text
http://127.0.0.1:8765/admin
```

Stop the YBM background processes started by the stack script:

```powershell
.\scripts\stop_stack.ps1
```

Default local LLM and gateway behavior:

- Keep `OPENAI_API_KEY` saved in `.env` for fallback.
- The active default profile is `localdeploy_gemma3_4b`, which calls LocalDeploy at `http://127.0.0.1:8000/v1` with model `gemma3_4b_ollama_safe`.
- Non-task Telegram messages get a direct local LLM answer with concise runtime context.
- The gateway keeps a small per-chat memory summary, not the full conversation.
- Plain `status` and `/status` return deterministic task state.
- Requests like `create a hello world web app and launch it` create files under `.agent_control/workspaces/task_<id>`, start a localhost preview, and return the URL.
- Development tasks route to the VS Code/GitHub Copilot terminal handoff when VS Code write access is enabled, with a local Copilot CLI fallback when the bridge is not connected.
- Worker results are sent back to the source Telegram chat.

Compile backend source:

```powershell
python -m compileall backend/src
```

Run tests:

```powershell
$env:PYTHONPATH='backend/src'
pytest backend/tests
```

If `AGENT_ADMIN_TOKEN` is set, open:

```text
http://127.0.0.1:8765/admin?token=<token>
```

Getting started docs:

- [Admin, Telegram, and LLM setup](docs/GETTING_STARTED_ADMIN_TELEGRAM_LLM.md)
- [Minimal end-to-end test](docs/MINIMAL_END_TO_END_TEST.md)
- [Database inspection](docs/DATABASE_INSPECTION.md)

Lower-level scripts still exist for debugging: `init_db.ps1`, `run_backend.ps1`, `run_telegram_polling.ps1`, and `run_worker.ps1`.

Flow docs:

- [Telegram to task worker flow](docs/TASK_FLOW.md)

## Safety Defaults

The example config disables terminal execution, filesystem access, VS Code access, desktop screenshots, desktop control, browser automation, dependency installation, and Git pushes.
