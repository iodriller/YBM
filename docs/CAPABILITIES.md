# Capabilities

The full, unabridged build log of what's implemented. See the [README](../README.md) for
positioning and quickstart, and [docs/ARCHITECTURE.md](ARCHITECTURE.md) for how it fits together.

## Implemented

- Project scaffold
- Pydantic schemas for commands, tasks, tools, approvals, artifacts, and audit events
- Strict configuration models with safe default capability policies
- SQLite persistence, repositories, audit logging, and redaction
- Minimal Telegram Bot API polling wrapper, update normalizer, allowlist checks, command parsing, and task intake
- Telegram gateway responses for direct questions, plain `status`, task lists, task details, logs, and screenshot capability state
- Telegram voice download/transcription service with transcript artifacts
- LLM provider abstraction; three-agent Concierge/Operator/Auditor pipeline (see [docs/ARCHITECTURE.md](ARCHITECTURE.md))
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
- Cross-platform setup: `scripts/install.sh` / `scripts/install.ps1`, `ybm onboard`, and Windows-specific `scripts/ybm.ps1`
- Basic FastAPI health endpoint
- React admin console (served by the backend at `/admin`) for chat, task monitoring, trace graphs, access/capability controls, and settings, backed by the same FastAPI admin APIs
- A small pointer page served at `/admin` if the console hasn't been built yet at this checkout
- Admin configuration writes for Telegram and the default OpenAI-compatible orchestrator LLM profile
- LLM-based Telegram task classification with readable audit events
- Admin audit filters, capability access modes, and database summary
- One-command local stack launcher for LocalDeploy, backend, Telegram polling, worker, and scheduler
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
- Operator loop can run independent tool calls concurrently (`call_tools_parallel`) or hand a bounded sub-task to an isolated inner loop with its own history (`delegate`)
- Per-task LLM token/cost tracking, surfaced in `ybm trace` and the admin trace view
- User-droppable skills (`skills.use`), a global persona/preferences document (`persona.manage`), and local keyword-search over a personal document folder (`knowledge.search`)
- Local web chat channel in the admin console — no Telegram required for basic use

## Not implemented yet

- Direct GitHub Copilot Chat panel response capture through VS Code APIs
- Persistent editable configuration for every advanced capability and adapter field
- A single unified process supervisor (Windows `scripts/ybm.ps1` and the cross-platform `ybm`
  console command are both fully functional but are two separate implementations today - see
  docs/HISTORY.md)

## Detailed runtime behavior

- The active default profile targets a local OpenAI-compatible endpoint (LocalDeploy or Ollama);
  keep a cloud API key saved in `.env` as a fallback if you want one.
- Non-task Telegram/web-chat messages get a direct LLM answer with concise runtime context.
- The gateway keeps an LLM-updated per-chat memory summary plus a small recent-turn window, not the full conversation.
- Plain `status` and `/status` return deterministic task state.
- Requests like `create a hello world web app and launch it` materialize files under `.agent_control/workspaces/task_<id>`, start a localhost preview, and return the URL. Codex or GitHub Copilot are used only when the message explicitly says to use them.
- Requests like `use Codex to build the first step of this app` or `use GitHub Copilot for this project` route through `coding.agent`, record workspace/output/limit state, and report failures or usage-limit text when the CLI exposes it.
- Long-running Codex, Claude Code, and Copilot CLI sessions move the task to `awaiting_external` until the durable watcher sees a terminal result. A task no longer looks complete simply because a background CLI started.
- Browser requests like `search the web for Python packaging docs and summarize the first result` use the `browser.open` tool. Chrome is launched with remote debugging when needed, screenshots are saved under `.agent_control/browser/screenshots`, and results are returned to Telegram.
- Computer-use requests like `take a screenshot and tell me what is open` or `use the computer to open this folder` route to `computer.use` when desktop control is enabled (Windows only). Screenshots are saved under `.agent_control/computer_use/screenshots`; action loops require the local multimodal LLM and are capped by `adapters.computer_use.max_steps`.
- Folder organization/search requests use `filesystem.manage` when an explicit path is present. It creates a manifest first, then applies only approved moves/copies inside configured allowed roots.
- Development tasks route to the VS Code/GitHub Copilot terminal handoff when VS Code write access is enabled, with a local Copilot CLI fallback when the bridge is not connected.
- Direct API work can use `http.request` when `network.http` is enabled and the target host or URL prefix is allowlisted. It can inject secrets from the encrypted vault at call time without logging the values.
- Missing-tool work can be routed to `adapter.factory`, which creates a cached adapter proposal under `.agent_control/adapters`; proposals can be hot-registered after their sandbox import and tests pass and approval is granted.
- If a native tool is missing, recovery checks configured MCP tools first, then tries bounded `code.interpreter` helpers when appropriate, then scaffolds a reviewable adapter proposal. `mcp.client install_server` can persist a new stdio MCP server config when an installable server is known.
- `code.interpreter` supports `run_python`, `generate_and_run`, `solve_once`, `inspect_state`, helper-building/repair operations, and `health`. By default it uses local Python for trusted runs; Docker can be enabled as `docker_python` for untrusted/generated code with network off, memory/CPU/pids limits, and artifact extraction from the managed workspace.
- External MCP servers are configured under `mcp.servers`; MCP is disabled by default in the example config, while local runtime configs can enable specific stdio servers.
- Scheduled-job requests like `set up a scheduled job every day to check this site` create a `schedule.manage` record. The supervised scheduler creates normal tasks from due schedules.
- Worker results are sent back to the source Telegram chat or the web chat, whichever the task came from.
