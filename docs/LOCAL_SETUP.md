# Local Setup

## Fastest path

Nothing needs to be installed first - not git, not Python. `uv` is a standalone binary and it
downloads the interpreter YBM runs on.

From inside the folder:

```bash
# Linux/macOS
./scripts/install.sh
```
```powershell
# Windows - or just double-click YBM-Setup.cmd, no terminal needed
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Both accept `--dry-run` (show the plan, change nothing), `--verify` (prove the install works
before returning), `--no-prompt`, and `--install-dir DIR`.

The installer installs dependencies via `uv`, writes `config/config.yaml` and `.env`, generates
admin and vault tokens, and starts the stack. The LLM and Telegram choices happen in the browser
wizard that opens (the local web chat needs no setup and is on by default). The interactive
`ybm onboard` CLI wizard still exists for headless/SSH-only installs with no browser to open.
Already inside a checkout? The script detects that and skips straight to setup.

```mermaid
flowchart LR
    I["install script"] --> S["ybm setup<br/>venv · config.yaml · .env tokens"]
    S --> O["ybm onboard<br/>pick an LLM, optional Telegram"]
    O --> D["ybm doctor<br/>one line per check"]
    D --> R["ybm start"]
    R --> A["http://127.0.0.1:8765/admin"]
```

After that, `YBM.bat` (double-click) or `ybm start` is all you need.

## Two interfaces, both supported

| | Cross-platform | Windows |
|---|---|---|
| Command | `ybm` (from `backend/.venv`) | `scripts\ybm.ps1` |
| Style | hyphenated: `ybm trace-task <id>` | two-word: `.\scripts\ybm.ps1 trace <id>` |

Day to day:

```bash
ybm start            # start the stack
ybm status           # what's running
ybm logs worker -f   # follow one service
ybm doctor           # check the environment
ybm stop             # stop everything
```

## Manual setup

### 1. Install

```powershell
.\scripts\ybm.ps1 setup
```

This creates `backend\.venv` via `uv`, installs dependencies, copies `config/config.example.yaml`
to `config/config.yaml` if missing (high-impact capabilities start disabled), and generates
`AGENT_ADMIN_TOKEN` / `AGENT_SECRET_VAULT_KEY` into `.env`. Pass `--telegram-token <token>` to
save `TELEGRAM_BOT_TOKEN` at the same time, or add it to `.env` yourself.

### 2. Point at an LLM

Either add a local [LocalDeploy](https://github.com/iodriller/LocalDeploy) checkout to `.env`:

```powershell
YBM_LOCALDEPLOY_ROOT=C:\path\to\LocalDeploy
```

Otherwise choose any provider in the browser wizard, or configure a native Anthropic or
OpenAI-compatible profile under `llm.profiles` in `config/config.yaml`.

Optional, for the VS Code bridge: `VSCODE_BRIDGE_TOKEN=...`

### 3. Enable what you need

Everything invasive starts **off**. Turn capabilities on from the admin console's Access page, or:

```powershell
.\scripts\ybm.ps1 config set <dotted.path> <value>
```

See [CAPABILITIES.md](CAPABILITIES.md) for what each capability unlocks.

### 4. Check and start

```powershell
.\scripts\ybm.ps1 doctor    # Python, deps, config, DB, LLM, tokens, ports
.\scripts\ybm.ps1 start     # runs doctor first; -SkipDoctor to skip
```

`start` launches LocalDeploy, backend, Telegram polling, WhatsApp polling, worker, scheduler, and
the coding-session watcher. Skip any with `-NoTelegram`, `-NoWhatsApp`, `-NoWorker`,
`-NoScheduler`, `-NoLocalDeploy`. The admin console is served by the backend, not a separate
process.

Open **http://127.0.0.1:8765/admin**. If the React console hasn't been built at this checkout
(`ybm ui-build`), you get a pointer page instead - the JSON API under `/admin/api/*` works either way.

### 5. Link WhatsApp (optional)

WhatsApp is off by default and uses [Baileys](https://github.com/WhiskeySockets/Baileys), an
unofficial WhatsApp Web client - no Meta developer account or public webhook, just a phone you
link as a device. `ybm setup` installs the sidecar's dependencies if Node.js 20+ is on `PATH`.

1. Set `channels.whatsapp.enabled: true` in `config/config.yaml`.
2. Restart: `.\scripts\ybm.ps1 start`
3. Watch for the QR code: `.\scripts\ybm.ps1 logs whatsapp -Follow`
4. Scan it (phone → Settings → Linked Devices). The session persists in `.agent_control/whatsapp_auth/`.
5. Add the number to `channels.whatsapp.allowed_numbers` - E.164 digits, no `+`
   (e.g. `"15551234567"`). **An empty list denies everything.** Restart to apply.

> Consider linking a secondary number. Baileys is unofficial, so there's a small
> account-flagging risk. Never commit a real number to a shared checkout.

## Daily commands

```powershell
.\scripts\ybm.ps1 status
.\scripts\ybm.ps1 logs worker -Follow
.\scripts\ybm.ps1 stop
.\scripts\ybm.ps1 test
Invoke-RestMethod http://127.0.0.1:8765/health
```

Package the VS Code extension: `.\scripts\package_vscode_extension.ps1`

The per-service launchers under `scripts/services/` are what `start` actually runs - use them
directly only to debug one process in isolation.

## Where things land

| Path | Contents |
|---|---|
| `.agent_control/workspaces/task_<id>` | Per-task generated files |
| `.agent_control/logs/<service>.jsonl` | Structured logs, secrets redacted |
| `.agent_control/browser/screenshots` | Browser screenshots |
| `.agent_control/computer_use/screenshots` | Desktop screenshots |
| `.agent_control/coding_sessions` | Codex / Claude Code / Copilot session state |
| `.agent_control/adapters` | Generated adapter proposals (never auto-loaded) |
| `agent_control.db` | SQLite database - see [DATABASE_INSPECTION.md](DATABASE_INSPECTION.md) |

Keep all of it private - see [THREAT_MODEL.md](THREAT_MODEL.md).
