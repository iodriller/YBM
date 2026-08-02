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
reachability, Telegram token, Node.js availability, WhatsApp link status, admin token, secret
vault key, and port availability - so a missing piece surfaces before you try to start the stack,
not as a silent crash loop after.

## 4. Start The Stack

```powershell
.\scripts\ybm.ps1 start
```

This runs `doctor` first (skip with `-SkipDoctor`), then initializes the database and starts
LocalDeploy, backend, Telegram polling, WhatsApp polling, worker, scheduler, and the
coding-session watcher. The admin console is served by the backend itself, not a separate
service. Skip individual services with `-NoTelegram`, `-NoWhatsApp`, `-NoWorker`, `-NoScheduler`,
or `-NoLocalDeploy`. Unlike Telegram, WhatsApp is off by default (`channels.whatsapp.enabled:
false`) and only shows a non-blocking `[FAIL]` line here until you configure and link it - see
below. Generated task workspaces default to `.agent_control/workspaces/task_<id>`.

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

## 5. Link WhatsApp (optional)

WhatsApp uses [Baileys](https://github.com/WhiskeySockets/Baileys), an unofficial WhatsApp Web
client - no Meta developer account or public webhook needed, just a phone with WhatsApp installed
that you link as a linked device (the same mechanism as WhatsApp Web/Desktop). It runs as a small
Node.js sidecar (`whatsapp-bridge/`) that the backend spawns and owns; `ybm setup` installs its
dependencies automatically if Node.js 20+ is on `PATH` (`node_path` in config to point at a
specific binary otherwise).

1. In `config/config.yaml`, set `channels.whatsapp.enabled: true`.
2. Start (or restart) the stack: `.\scripts\ybm.ps1 start`.
3. Watch the `whatsapp` service's log for the QR code: `.\scripts\ybm.ps1 logs whatsapp -Follow`.
4. Scan it from WhatsApp on your phone (Settings -> Linked Devices -> Link a Device). The linked
   session is saved under `.agent_control/whatsapp_auth/` and persists across restarts.
5. Add the linked number to `channels.whatsapp.allowed_numbers` in `config/config.yaml`, E.164
   digits only, no leading `+` (e.g. `"15551234567"`) - like Telegram's allowlists, an empty list
   denies every message. Restart the stack for the change to take effect.

Consider linking a secondary number rather than your primary one - Baileys is unofficial, and
while it mirrors how popular self-hosted WhatsApp gateways already run in production, there is a
small account-flagging risk inherent to any unofficial client. Never commit a real phone number to
`config/config.yaml` if you intend to share this checkout.

v1 is plain text only: no slash commands, inline buttons, voice transcription, or screenshot/file
delivery over WhatsApp (all of those already work over Telegram). Plain-text `approve` / `status`
/ `remember that ...` work the same way they do on Telegram.

## 6. Check Status, Logs, And Stop The Stack

```powershell
.\scripts\ybm.ps1 status
.\scripts\ybm.ps1 logs worker -Follow
.\scripts\ybm.ps1 stop
```

Backend health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

## 7. Lower-Level Commands

The per-service launchers under `scripts/services/` are what `ybm start` actually runs; use
them directly only when debugging a single process in isolation (they read `YBM_LOCALDEPLOY_ROOT`
and other `.env` values the same way `ybm start` does): `run_backend.ps1`,
`run_telegram_polling.ps1`, `run_whatsapp.ps1`, `run_worker.ps1`, `run_coding_session_watcher.ps1`,
`run_localdeploy.ps1`.

## 8. Run Tests

```powershell
.\scripts\ybm.ps1 test
```

## 9. Package VS Code Extension

```powershell
.\scripts\package_vscode_extension.ps1
```

The extension sends workspace state to the local backend and polls for queued terminal commands.

## 10. Current Limits

- **Desktop screenshot/control (`computer.use`) is Windows-only.** Every other capability -
  filesystem, browser, code interpreter, MCP, scheduling, coding-agent sessions, the web chat -
  is cross-platform and runs in CI on Linux, Windows, and macOS.
- `scripts\ybm.ps1` is Windows-only (uses `Win32_Process` for process tracking); Linux/macOS use
  the cross-platform `ybm start`/`stop`/`status`/`logs` commands instead, which are functionally
  equivalent but not (yet) a single unified implementation - see docs/HISTORY.md for the current
  state of that consolidation.
- Desktop screenshot/control and computer use are disabled by default and should be enabled per task/access mode from the admin UI.
- WhatsApp uses Baileys, an unofficial client, not Meta's Business API - it is disabled by
  default, requires linking a number via QR (see step 5), and is plain-text only for now: no
  slash commands, inline buttons, voice transcription, or screenshot/file delivery.
- WhatsApp's privacy-preserving "LID" addressing sends an opaque id instead of the sender's real
  phone number for some contacts - there is no way to resolve that id back to a number, so those
  senders can never match `channels.whatsapp.allowed_numbers` no matter how it's configured. The
  audit trail labels this denial reason distinctly (`lid_jid_no_resolvable_number`) from an
  ordinary not-on-the-allowlist denial so it doesn't read as a config mistake.
- `computer.use run_goal` needs the configured local model endpoint to accept OpenAI-compatible image payloads. If LocalDeploy/Gemma vision is unavailable, observation still returns screenshot/UI metadata but action planning fails clearly.
- `filesystem.manage` only operates inside configured allowed roots and rejects path escapes.
- Browser inspection/control can see only Chrome tabs exposed through the configured remote debugging port. Normal Chrome windows launched without remote debugging are not visible to this adapter.
- VS Code terminal stdout capture depends on VS Code shell integration. Without it, the bridge records dispatch completion only.
- Direct Copilot Chat panel scraping is not implemented; Copilot routing uses VS Code terminal command dispatch or the local Copilot CLI fallback.
