# Configurable Agentic Control System

Local-first, Telegram-first task runner with a small orchestration core and a growing tool layer. YBM coordinates VS Code, external coding CLIs, browser automation, scoped filesystem work, desktop observation/control, code interpreter helpers, scheduled tasks, artifacts, and MCP-connected tools without turning the app into a Codex/Copilot-only cockpit.

## Current Status

Implemented:

- Project scaffold
- Pydantic schemas for commands, tasks, tools, approvals, artifacts, and audit events
- Strict configuration models with safe default capability policies
- SQLite persistence, repositories, audit logging, and redaction
- Minimal Telegram Bot API polling wrapper, update normalizer, allowlist checks, command parsing, and task intake
- Telegram gateway responses for direct questions, plain `status`, task lists, task details, logs, and screenshot capability state
- Telegram voice download/transcription service with transcript artifacts
- LLM provider abstraction; three-agent Concierge/Operator/Auditor pipeline (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md))
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
- One-command local stack launcher for LocalDeploy, backend, Telegram polling, worker, scheduler, and Streamlit admin UI
- Worker completion notifications back to Telegram with tool output summaries
- LLM-backed per-Telegram-chat rolling memory with a concise summary and recent-turn window
- Launchable web-app flow with workspace materialization and localhost preview URLs; Codex/Copilot are only used when explicitly requested
- Capability registry/vault plus generated adapter proposal cache under `.agent_control/adapters`
- Sandbox-tested, approval-gated hot promotion for generated adapter proposals into the live tool registry
- Registered `schedule.manage` tool plus supervised scheduler service for recurring tasks
- Registered document and artifact delivery tools for PDF summaries, PowerPoint artifacts, and Telegram file delivery
- Pluggable `code.interpreter` execution backends with normalized metadata, safer import defaults, health reporting, and an optional Docker Python sandbox
- Session-backed `coding.agent` support for Codex, Claude Code, and GitHub Copilot CLI runs, with durable session files, event logs, and a watcher service for restart-safe completion handling
- Structured attempt history and failure diagnosis metadata for bounded recovery instead of unbounded retries
- YBM MCP server for external clients plus `mcp.client` for calling configured external MCP servers from YBM tasks
- `task.status` includes active tasks, background coding sessions, waiting clarification/approval/external work, LocalDeploy fallback state, and MCP config state

Not implemented yet:

- Direct GitHub Copilot Chat panel response capture through VS Code APIs
- Persistent editable configuration for every advanced capability and adapter field

## Development

`scripts/ybm.ps1` is the single entry point for everything below - setup, health checks,
starting/stopping the stack, tests, and database maintenance. See
[docs/HISTORY.md](docs/HISTORY.md) for the rationale.

First-time setup (creates `backend/.venv` via `uv`, installs dependencies, bootstraps
`config/config.yaml` and the `.env` secrets used for the admin token and secret vault):

```powershell
.\scripts\ybm.ps1 setup
```

Check the environment before starting anything - missing dependencies, missing config,
unreachable LocalDeploy, and similar problems are reported as one line each instead of
surfacing as a silent crash loop:

```powershell
.\scripts\ybm.ps1 doctor
```

Start the whole local stack (this runs `doctor` first and refuses to start if it fails):

```powershell
.\scripts\ybm.ps1 start
```

This initializes SQLite, starts LocalDeploy if needed, starts the backend, starts Telegram polling, starts the worker, starts the scheduler, and launches the Streamlit admin UI. Open:
It also starts the coding-session watcher, which finalizes background Codex/Claude/Copilot sessions after worker restarts and reports completion back to Telegram. Selectively skip services with `-NoTelegram`, `-NoWorker`, `-NoScheduler`, `-NoAdminUi`, `-NoLocalDeploy`, or skip the preflight with `-SkipDoctor`.

```text
http://127.0.0.1:8501
```

The legacy FastAPI admin page remains available at `http://127.0.0.1:8765/admin`.

Check what's running, tail a service's logs, or stop everything:

```powershell
.\scripts\ybm.ps1 status
.\scripts\ybm.ps1 logs worker -Follow
.\scripts\ybm.ps1 stop
```

`.\scripts\start_stack.ps1` and `.\scripts\stop_stack.ps1` still work as thin aliases for
`ybm start` / `ybm stop`.

Default local LLM and gateway behavior:

- Keep `OPENAI_API_KEY` saved in `.env` for fallback.
- The active default profile is `localdeploy_gemma3_12b`, which calls LocalDeploy at `http://127.0.0.1:8000/v1` with model `gemma3_12b_ollama_safe`.
- Non-task Telegram messages get a direct local LLM answer with concise runtime context.
- The gateway keeps an LLM-updated per-chat memory summary plus a small recent-turn window, not the full conversation.
- Plain `status` and `/status` return deterministic task state.
- Requests like `create a hello world web app and launch it` materialize files under `.agent_control/workspaces/task_<id>`, start a localhost preview, and return the URL. Codex or GitHub Copilot are used only when the message explicitly says to use them.
- Requests like `use Codex to build the first step of this app` or `use GitHub Copilot for this project` route through `coding.agent`, record workspace/output/limit state, and report failures or usage-limit text when the CLI exposes it.
- Long-running Codex, Claude Code, and Copilot CLI sessions move the task to `awaiting_external` until the durable watcher sees a terminal result. A task no longer looks complete simply because a background CLI started.
- Browser requests like `search the web for Python packaging docs and summarize the first result` use the `browser.open` tool. Chrome is launched with remote debugging when needed, screenshots are saved under `.agent_control/browser/screenshots`, and results are returned to Telegram.
- Computer-use requests like `take a screenshot and tell me what is open` or `use the computer to open this folder` route to `computer.use` when desktop control is enabled. Screenshots are saved under `.agent_control/computer_use/screenshots`; action loops require the local multimodal LLM and are capped by `adapters.computer_use.max_steps`.
- Folder organization/search requests use `filesystem.manage` when an explicit path is present. It creates a manifest first, then applies only approved moves/copies inside `adapters.computer_use.allowed_roots`.
- Development tasks route to the VS Code/GitHub Copilot terminal handoff when VS Code write access is enabled, with a local Copilot CLI fallback when the bridge is not connected.
- Direct API work can use `http.request` when `network.http` is enabled and the target host or URL prefix is allowlisted. It can inject secrets from the encrypted vault at call time without logging the values.
- Missing-tool work can be routed to `adapter.factory`, which creates a cached adapter proposal under `.agent_control/adapters`; proposals can be hot-registered after their sandbox import and tests pass and approval is granted.
- If a native tool is missing, recovery checks configured MCP tools first, then tries bounded `code.interpreter` helpers when appropriate, then scaffolds a reviewable adapter proposal. `mcp.client install_server` can persist a new stdio MCP server config when an installable server is known.
- `code.interpreter` supports `run_python`, `generate_and_run`, `solve_once`, `inspect_state`, helper-building/repair operations, and `health`. By default it uses local Python for trusted runs; Docker can be enabled as `docker_python` for untrusted/generated code with network off, memory/CPU/pids limits, and artifact extraction from the managed workspace.
- External MCP servers are configured under `mcp.servers`; MCP is disabled by default in the example config, while local runtime configs can enable specific stdio servers.
- Scheduled-job requests like `set up a scheduled job every day to check this site` create a `schedule.manage` record. The supervised scheduler creates normal tasks from due schedules.
- Worker results are sent back to the source Telegram chat.

Run unit tests:

```powershell
.\scripts\ybm.ps1 test
```

Browser-drive the Streamlit admin UI (port 8501) for diagnosis:

```powershell
python -m playwright install chromium   # one-time browser download
```

Run live Telegram E2E checks:

```powershell
.\scripts\ybm.ps1 e2e --only desktop_inspection
.\scripts\ybm.ps1 e2e                              # full suite
```

See [e2e/README.md](e2e/README.md) for the required Telethon user-session setup and log format. Live results are written under `.agent_control/e2e_results/run_<timestamp>/`.

Inspect, prune, or wipe the local database:

```powershell
.\scripts\ybm.ps1 db inspect
.\scripts\ybm.ps1 db clean --days 30
.\scripts\ybm.ps1 db reset --yes
```

If `AGENT_ADMIN_TOKEN` is set, open:

```text
http://127.0.0.1:8501
```

The Streamlit UI reads `AGENT_ADMIN_TOKEN` from `.env` and also has a sidebar token field.

Docs:

- [Architecture and message flow](docs/ARCHITECTURE.md) — how the system works now, plus known gaps
- [History](docs/HISTORY.md) — why it's built this way, and the full phase-by-phase record
- [Local setup](docs/LOCAL_SETUP.md)
- [Minimal end-to-end test](docs/MINIMAL_END_TO_END_TEST.md)
- [Database inspection](docs/DATABASE_INSPECTION.md)

Per-service launchers used internally by `ybm.ps1` live under `scripts/services/` if you need to run one directly for debugging. Everything else is available through `ybm` too: `ybm clean` wipes generated caches/workspaces/adapter proposals, `ybm e2e-login` bootstraps the Telethon user session needed for live E2E checks, `ybm send "<message>"` traces one ad-hoc message through the full pipeline, `ybm trace <task_id>` prints a full post-mortem for a task straight from the DB, `ybm scenario record <name>` re-records one deterministic scenario fixture against a live LLM (see below), and `ybm package-extension` builds the VS Code bridge `.vsix`. Run `.\scripts\ybm.ps1 help` for the full list.

### Scenario tests

`backend/tests/scenario/` is the fast deterministic tier: the real
worker/registry/policy/executor stack against a temp filesystem, with recorded LLM responses
replayed from `backend/tests/scenario/fixtures/`. The whole tier runs in seconds with no
network, no GPU, and no API spend, and is included in `ybm test`.

When a change alters a prompt, a tool's advertised schema, or the workspace layout, the
affected fixtures stop matching (they are keyed on exact prompt text) and their tests fail
loudly rather than replaying stale data. Re-record just those:

```powershell
.\scripts\ybm.ps1 scenario record folder_open_inspection
.\scripts\ybm.ps1 scenario record folder_open_inspection --profile openai_saved
```

This makes **real LLM calls**. It defaults to `llm.default_profile`, so with LocalDeploy
running it costs nothing; `--profile` overrides that. Review the regenerated fixture before
committing it.

## Safety Defaults

The example config disables terminal execution, filesystem access, VS Code access, desktop screenshots, desktop control, computer use, browser automation, dependency installation, and Git pushes.
