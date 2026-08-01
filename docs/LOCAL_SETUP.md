# Local Setup

## Quickstart (any OS)

```bash
# Linux/macOS
curl -fsSL https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.sh | sh
```
```powershell
# Windows
iwr https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.ps1 -UseBasicParsing | iex
```

Either clones the repo, installs dependencies via `uv`, and runs `ybm onboard` - an interactive
wizard that detects a local LLM (Ollama, or an existing LocalDeploy checkout) or asks for a cloud
API key, optionally sets up Telegram (the local web chat needs no setup and is on by default),
writes `config/config.yaml` and `.env`, runs `ybm doctor`, and offers to start the stack. Already
have the repo cloned? Run the same script from inside it and it skips straight to setup.

Once installed, the cross-platform `ybm` command (from `backend/.venv`) covers day-to-day use:

```bash
ybm start           # start the stack
ybm status           # what's running
ybm logs worker -f   # follow one service's log
ybm stop             # stop everything
```

The sections below cover the same setup manually, plus Windows-specific tooling
(`scripts\ybm.ps1`) that predates the cross-platform `ybm` command and remains fully supported.

## 1. Run Setup (Windows, manual)

```powershell
.\scripts\ybm.ps1 setup
```

This creates `backend\.venv` via `uv`, installs dependencies, copies `config/config.example.yaml`
to `config/config.yaml` if missing (every capability starts disabled), and generates
`AGENT_ADMIN_TOKEN` / `AGENT_SECRET_VAULT_KEY` into `.env`. Pass `--telegram-token <token>` to
save `TELEGRAM_BOT_TOKEN` at the same time, or add it to `.env` yourself.

If you run a local LocalDeploy checkout, add its path to `.env`:

```powershell
YBM_LOCALDEPLOY_ROOT=C:\path\to\LocalDeploy
```

Otherwise point `llm.profiles` in `config/config.yaml` at whatever OpenAI-compatible endpoint
you use.

Optional, for the VS Code bridge:

```powershell
VSCODE_BRIDGE_TOKEN=...
```

## 2. Configure The App

`config/config.yaml` was created from `config/config.example.yaml` in step 1. Safe defaults keep
terminal execution, filesystem access, VS Code access, desktop screenshots, desktop control/computer
use, browser automation, dependency installation, and Git pushes disabled. The workspace adapter
itself is available by default, but it only executes when `filesystem.write` is enabled for task
workspaces and generated files. Toggle capabilities from the admin UI's Access panel, or with
`.\scripts\ybm.ps1 config set <dotted.path> <value>`.

## 3. Check The Environment

```powershell
.\scripts\ybm.ps1 doctor
```

Reports one line per check - Python version, dependencies, config validity, database, LocalDeploy
reachability, Telegram token, admin token, secret vault key, and port availability - so a missing
piece surfaces before you try to start the stack, not as a silent crash loop after.

## 4. Start The Stack

```powershell
.\scripts\ybm.ps1 start
```

This runs `doctor` first (skip with `-SkipDoctor`), then initializes the database and starts
LocalDeploy, backend, Telegram polling, worker, scheduler, and the coding-session watcher. The
admin console is served by the backend itself, not a separate service. Skip individual services
with `-NoTelegram`, `-NoWorker`, `-NoScheduler`, or `-NoLocalDeploy`. Generated task workspaces
default to `.agent_control/workspaces/task_<id>`.

Browser tasks use Chrome through the DevTools remote debugging port configured at `adapters.browser.remote_debugging_port` (default `9222`). If Chrome is not already available there, the adapter launches a separate Chrome profile under `.agent_control/browser/chrome-profile`. Screenshots are saved under `.agent_control/browser/screenshots`.

Computer-use tasks use the `computer.use` adapter when desktop control is enabled. Desktop observations save screenshots under `.agent_control/computer_use/screenshots`; bounded action loops use the active local multimodal provider when available, stop at `adapters.computer_use.max_steps`, and check task cancellation before each action. Folder organization/search should use `filesystem.manage` instead of File Explorer automation when an explicit path is provided. Its allowed roots are configured through `adapters.computer_use.allowed_roots`.

Open the admin UI:

```text
http://127.0.0.1:8765/admin
```

If `frontend/`'s React console hasn't been built yet at this checkout (`ybm ui-build`, or
`npm run build` in `frontend/`), this serves a small pointer page instead, explaining how to build
it. Either way, the JSON API underneath it (`/admin/api/*`) is unchanged and works regardless of
whether a build exists.

Launchable app requests use Copilot first when VS Code write access is enabled, then the workspace adapter serves the result locally. Generated adapter proposals, when requested, are cached under `.agent_control/adapters` and are not loaded into runtime automatically.

Codex, Claude Code, and GitHub Copilot CLI sessions are stored under `.agent_control/coding_sessions`. Long runs put the task in `awaiting_external`; the watcher finalizes session files and sends the completion report after a worker restart.

External MCP tools are configured under `mcp.servers` in `config/config.yaml`. MCP is disabled by default; when enabled, YBM exposes a single `mcp.client` tool for discovery, health checks, and configured tool calls.

`code.interpreter` is local-first and writes under `.agent_control/code_interpreter`. The default backend is `local_subprocess`; enable `adapters.code_interpreter.docker.enabled` and add `docker_python` to `adapters.code_interpreter.backends` to run untrusted/generated Python in a short-lived Docker container. Docker runs with network off unless requested and allowed by policy, plus configured memory/CPU/pids limits. Use `code.interpreter` operation `health` to inspect Docker availability, configured remote backends, and recent backend failures.

## 5. Check Status, Logs, And Stop The Stack

```powershell
.\scripts\ybm.ps1 status
.\scripts\ybm.ps1 logs worker -Follow
.\scripts\ybm.ps1 stop
```

Backend health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

## 6. Lower-Level Commands

The per-service launchers under `scripts/services/` are what `ybm start` actually runs; use
them directly only when debugging a single process in isolation (they read `YBM_LOCALDEPLOY_ROOT`
and other `.env` values the same way `ybm start` does): `run_backend.ps1`,
`run_telegram_polling.ps1`, `run_worker.ps1`, `run_coding_session_watcher.ps1`,
`run_localdeploy.ps1`.

## 7. Run Tests

```powershell
.\scripts\ybm.ps1 test
```

## 8. Package VS Code Extension

```powershell
.\scripts\package_vscode_extension.ps1
```

The extension sends workspace state to the local backend and polls for queued terminal commands.

## 9. Current Limits

- **Desktop screenshot/control (`computer.use`) is Windows-only.** Every other capability -
  filesystem, browser, code interpreter, MCP, scheduling, coding-agent sessions, the web chat -
  is cross-platform and runs in CI on Linux, Windows, and macOS.
- `scripts\ybm.ps1` is Windows-only (uses `Win32_Process` for process tracking); Linux/macOS use
  the cross-platform `ybm start`/`stop`/`status`/`logs` commands instead, which are functionally
  equivalent but not (yet) a single unified implementation - see docs/HISTORY.md for the current
  state of that consolidation.
- Desktop screenshot/control and computer use are disabled by default and should be enabled per task/access mode from the admin UI.
- `computer.use run_goal` needs the configured local model endpoint to accept OpenAI-compatible image payloads. If LocalDeploy/Gemma vision is unavailable, observation still returns screenshot/UI metadata but action planning fails clearly.
- `filesystem.manage` only operates inside configured allowed roots and rejects path escapes.
- Browser inspection/control can see only Chrome tabs exposed through the configured remote debugging port. Normal Chrome windows launched without remote debugging are not visible to this adapter.
- VS Code terminal stdout capture depends on VS Code shell integration. Without it, the bridge records dispatch completion only.
- Direct Copilot Chat panel scraping is not implemented; Copilot routing uses VS Code terminal command dispatch or the local Copilot CLI fallback.
