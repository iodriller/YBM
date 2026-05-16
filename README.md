# Configurable Agentic Control System

Local-first control plane for sending Telegram text or voice commands to an agentic orchestration layer that can safely coordinate VS Code, coding assistants, terminal tools, desktop observations, and future automation adapters.

## Current Status

Implemented:

- Project scaffold
- Phased project plan
- Detailed step-by-step implementation plan
- Pydantic schemas for commands, tasks, plans, tools, approvals, artifacts, and audit events
- Strict configuration models with safe default capability policies
- Basic FastAPI health endpoint

Not implemented yet:

- Persistence
- Telegram runtime adapter
- LLM calls
- VS Code bridge runtime
- Task worker loop

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

