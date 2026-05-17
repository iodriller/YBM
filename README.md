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
- Minimal Telegram command responses for status, task lists, task details, logs, and screenshot capability state
- Telegram voice download/transcription service with transcript artifacts
- LLM provider abstraction and structured planner with plan persistence
- Capability policy engine with approval requests
- Gated tool executor and minimal durable task worker
- Minimal VS Code bridge endpoints, extension state sync, and terminal command queue
- Generic terminal coding-assistant adapter
- File-backed artifacts, optional screenshot capture, and Telegram screenshot artifact delivery
- Retry policy with durable retry metadata
- Windows setup scripts and local setup documentation
- Basic FastAPI health endpoint
- Built-in FastAPI admin UI for safe config summary, task monitoring, audit logs, VS Code bridge state, and gated task controls
- Admin configuration writes for Telegram and the default OpenAI-compatible orchestrator LLM profile
- LLM-based Telegram task classification with readable audit events
- Admin audit filters, capability access modes, and database summary

Not implemented yet:

- Real tool adapters beyond test/static adapters
- Full VS Code terminal output capture; current extension records dispatch observations only
- Production process wrapper for Telegram polling and task workers
- Persistent editable configuration for every capability and adapter

## Development

Default local LLM:

- Keep `OPENAI_API_KEY` saved in `.env` for fallback.
- The active default profile is `localdeploy_gemma3_4b`, which calls LocalDeploy at `http://127.0.0.1:8000/v1` with model `gemma3_4b_ollama_safe`.
- `.\scripts\run_backend.ps1`, `.\scripts\run_telegram_polling.ps1`, and `.\scripts\run_worker.ps1` start `C:\for fun\LocalDeploy\api_server.py` automatically if it is not already running.

Compile backend source:

```powershell
python -m compileall backend/src
```

Run tests:

```powershell
$env:PYTHONPATH='backend/src'
pytest backend/tests
```

Initialize the local database:

```powershell
.\scripts\init_db.ps1
```

Run the backend:

```powershell
.\scripts\run_backend.ps1
```

Open the admin UI:

```text
http://127.0.0.1:8765/admin
```

If `AGENT_ADMIN_TOKEN` is set, open:

```text
http://127.0.0.1:8765/admin?token=<token>
```

Getting started docs:

- [Admin, Telegram, and LLM setup](docs/GETTING_STARTED_ADMIN_TELEGRAM_LLM.md)
- [Minimal end-to-end test](docs/MINIMAL_END_TO_END_TEST.md)
- [Database inspection](docs/DATABASE_INSPECTION.md)

Run Telegram polling:

```powershell
.\scripts\run_telegram_polling.ps1
```

Run the task worker that picks up persisted tasks:

```powershell
.\scripts\run_worker.ps1
```

Flow docs:

- [Telegram to task worker flow](docs/TASK_FLOW.md)

## Safety Defaults

The example config disables terminal execution, filesystem access, VS Code access, desktop screenshots, desktop control, browser automation, dependency installation, and Git pushes.
