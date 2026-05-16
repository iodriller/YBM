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
- Basic FastAPI health endpoint

Not implemented yet:

- Real tool adapters beyond test/static adapters
- Real screenshot capture and artifact delivery
- Full VS Code terminal output capture; current extension records dispatch observations only

## Development

Compile backend source:

```powershell
python -m compileall backend/src
```

Run tests:

```powershell
$env:PYTHONPATH='backend/src'
pytest backend/tests
```

## Safety Defaults

The example config disables terminal execution, filesystem access, VS Code access, desktop screenshots, desktop control, browser automation, dependency installation, and Git pushes.
