# Configurable Agentic Control System

Local-first control plane for sending Telegram text or voice commands to an agentic orchestration layer that can safely coordinate VS Code, coding assistants, browser automation, scoped filesystem work, desktop observation/control, and future automation adapters.

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
- Chrome browser adapter for web search, page summaries, tab inspection, screenshots, navigation, tab close, click, and simple form-fill operations through DevTools
- Windows-first `computer.use` adapter for policy-gated desktop observation and bounded local mouse/keyboard action loops
- Scoped `filesystem.manage` adapter for folder inspection, file search, organization manifests, and approved move/copy application inside configured roots
- File-backed artifacts, optional screenshot capture, and Telegram screenshot artifact delivery
- Retry policy with durable retry metadata
- Windows setup scripts and local setup documentation
- Basic FastAPI health endpoint
- Streamlit admin UI backed by FastAPI admin APIs for task monitoring, traces, config, audit, VS Code bridge state, and gated controls
- Legacy FastAPI admin page retained as a fallback
- Admin configuration writes for Telegram and the default OpenAI-compatible orchestrator LLM profile
- LLM-based Telegram task classification with readable audit events
- Admin audit filters, capability access modes, and database summary
- One-command local stack launcher for LocalDeploy, backend, Telegram polling, and worker
- Worker completion notifications back to Telegram with tool output summaries
- LLM-backed per-Telegram-chat rolling memory with a concise summary and recent-turn window
- Copilot-first launchable web-app flow with workspace materialization and localhost preview URLs
- Capability registry/vault plus generated adapter proposal cache under `.agent_control/adapters`

Not implemented yet:

- Direct GitHub Copilot Chat panel response capture through VS Code APIs
- Persistent editable configuration for every advanced capability and adapter field

## Development

Start the whole local stack:

```powershell
.\scripts\start_stack.ps1
```

This initializes SQLite, starts LocalDeploy if needed, starts the backend, starts Telegram polling, starts the worker, and launches the Streamlit admin UI. Open:

```text
http://127.0.0.1:8501
```

The legacy FastAPI admin page remains available at `http://127.0.0.1:8765/admin`.

Stop the YBM background processes started by the stack script:

```powershell
.\scripts\stop_stack.ps1
```

Default local LLM and gateway behavior:

- Keep `OPENAI_API_KEY` saved in `.env` for fallback.
- The active default profile is `localdeploy_gemma3_4b`, which calls LocalDeploy at `http://127.0.0.1:8000/v1` with model `gemma3_4b_ollama_safe`.
- Non-task Telegram messages get a direct local LLM answer with concise runtime context.
- The gateway keeps an LLM-updated per-chat memory summary plus a small recent-turn window, not the full conversation.
- Plain `status` and `/status` return deterministic task state.
- Requests like `create a hello world web app and launch it` use Copilot as the creator when VS Code write access is enabled, materialize files under `.agent_control/workspaces/task_<id>`, start a localhost preview, and return the URL.
- Browser requests like `search the web for Python packaging docs and summarize the first result` use the `browser.open` tool. Chrome is launched with remote debugging when needed, screenshots are saved under `.agent_control/browser/screenshots`, and results are returned to Telegram.
- Computer-use requests like `take a screenshot and tell me what is open` or `use the computer to open this folder` route to `computer.use` when desktop control is enabled. Screenshots are saved under `.agent_control/computer_use/screenshots`; action loops require the local multimodal LLM and are capped by `adapters.computer_use.max_steps`.
- Folder organization/search requests use `filesystem.manage` when an explicit path is present. It creates a manifest first, then applies only approved moves/copies inside `adapters.computer_use.allowed_roots`.
- Development tasks route to the VS Code/GitHub Copilot terminal handoff when VS Code write access is enabled, with a local Copilot CLI fallback when the bridge is not connected.
- Missing-tool work can be routed to `adapter.factory`, which creates a reviewable cached adapter proposal under `.agent_control/adapters` without loading it into runtime automatically.
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
http://127.0.0.1:8501
```

The Streamlit UI reads `AGENT_ADMIN_TOKEN` from `.env` and also has a sidebar token field.

Getting started docs:

- [Admin, Telegram, and LLM setup](docs/GETTING_STARTED_ADMIN_TELEGRAM_LLM.md)
- [Minimal end-to-end test](docs/MINIMAL_END_TO_END_TEST.md)
- [Database inspection](docs/DATABASE_INSPECTION.md)

Lower-level scripts still exist for debugging: `init_db.ps1`, `run_backend.ps1`, `run_admin_ui.ps1`, `run_telegram_polling.ps1`, and `run_worker.ps1`.

Flow docs:

- [Telegram to task worker flow](docs/TASK_FLOW.md)

## Safety Defaults

The example config disables terminal execution, filesystem access, VS Code access, desktop screenshots, desktop control, computer use, browser automation, dependency installation, and Git pushes.
