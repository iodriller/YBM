# Local setup

## Before you start

YBM source installs use `uv` to provide Python 3.12 and create an isolated environment. You do not
need to install Python or Git first.

You do need:

- An internet connection for the first dependency install.
- Bash and `curl` for the macOS/Linux installer.
- **Node.js 22.22 or newer**, to build the admin console. Every install today is a source install,
  and a source install builds the console itself.
- Docker Desktop or Docker Engine with Compose only if you choose the container path.

Without Node.js, setup still completes and the backend runs, but `/admin` shows build instructions
instead of the console until you install Node.js and run the `ui-build` command for your platform.
The Docker image builds the console inside the image, so the container path needs nothing on the
host. The unbuilt release artifacts would also carry it prebuilt, which is what will make Node.js
optional for everyone else.

## Recommended install

> **No release has been published yet**, so there is no installer, no `winget` package, and no
> release archive to download. `packaging/` and `.github/workflows/release.yml` contain the
> packaging for those, but it has never been run against a tag. Everything below installs from
> source and is tested.

### Windows

Two routes, neither needing administrator rights.

| Route | Command or file | Downloads a file? |
|---|---|---|
| PowerShell | `irm https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.ps1 \| iex` | No |
| Plain folder | Repository ZIP, then double-click `YBM.bat` | Yes |

Finish the browser wizard that opens afterwards.

The PowerShell route writes nothing to disk before running, so there is no downloaded file for
Windows to flag. Pipe it to `more` instead of `iex` to read it first.

From a source checkout, double-click `YBM.bat` in the extracted folder. It installs anything missing,
including `uv` itself, then starts YBM and opens the console. The same file serves the first run and
every run after it; there is no separate setup step.

For visible PowerShell output and the complete installer option set:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Verify
```

Available PowerShell parameters are `-DryRun`, `-Verify`, `-NoPrompt`, and
`-InstallDir C:\path\to\ybm`. `YBM_INSTALL_DIR`, `YBM_DRY_RUN`, and `YBM_NO_PROMPT` provide the
corresponding environment settings.

After setup, double-click `YBM.bat`. It runs the idempotent Windows lifecycle command, syncs runtime
dependencies when the lock files change, starts YBM, and opens the console.

### macOS and Linux

From a checkout, `./ybm.sh` is the counterpart to `YBM.bat`: it installs whatever is missing,
including `uv` and Python 3.12, then starts YBM and opens the console. It is idempotent, so it is
also the command for every launch afterwards. Pass `--no-desktop` to skip the desktop-control extras
on a headless box.

Or bootstrap from source in one line:

```bash
curl -fsSL https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.sh | bash
```

From a checkout you already have:

```bash
bash ./scripts/install.sh --verify
```

`install.sh` only fetches the code and then runs `./ybm.sh`. Available options:

```text
--dry-run
--verify
--no-prompt
--install-dir DIR
```

The script uses Git when it is available. If Git is absent and the script needs to fetch the code,
it downloads a source archive instead. `YBM_INSTALL_DIR` changes the default install path, and
`YBM_DRY_RUN=1` enables the dry run.

After setup, `./ybm.sh` starts YBM and opens the console. The equivalent CLI call, if you want the
individual command rather than the launcher, is:

```bash
./backend/.venv/bin/ybm start --open
```

The installer does not add that virtual-environment directory to your shell `PATH`. The full path
above works without activating the environment. If you do activate `backend/.venv`, the shorter
`ybm start --open` form is available.

### Docker

Docker runs the headless profile. It bundles the admin console and WhatsApp dependencies, but it
cannot attach to the host desktop, Chrome display, or VS Code session.

```bash
cp .env.example .env
# Edit .env and set AGENT_ADMIN_TOKEN to a long random value.
docker compose up -d --build
```

Open `http://127.0.0.1:8765/admin` and enter the admin token. The first-run wizard writes settings
to the host's ignored `config/config.yaml`. Store cloud API keys in the host `.env` so they survive
container recreation.

Compose publishes the console only on host loopback, keeps runtime state in the `ybm-state` volume,
and exposes `./workspace` as the allowed host workspace. To add the optional Ollama service:

```bash
docker compose --profile ollama up -d --build
docker compose exec ollama ollama pull qwen3:8b
```

## What setup creates

The source installers:

1. Locate or install the pinned `uv` release.
2. Ask `uv` to provide Python 3.12.
3. Create `backend/.venv` and install YBM dependencies.
4. Copy `config/config.example.yaml` to ignored `config/config.yaml` if it is missing.
5. Generate `AGENT_ADMIN_TOKEN` and `AGENT_SECRET_VAULT_KEY` in ignored `.env` if they are missing.
6. Initialize `.agent_control/agent_control.db`.
7. Build the admin console and install WhatsApp dependencies when Node.js is available.
8. Start the stack and open the local console.

Existing config, tokens, and local state are retained. On Windows, `YBM.bat` also checks whether
Python dependency inputs changed before syncing again. It checks for updates but does not apply
source updates automatically.

## First-run configuration

The browser wizard is shown when no reachable model has been configured. It lets you:

1. Select Ollama, LM Studio, LocalDeploy, a cloud provider, or another OpenAI-compatible endpoint.
2. Verify the key or endpoint, select a model, and run one small completion before saving.
3. Use web chat immediately or optionally verify and connect a Telegram bot.

The wizard can be skipped. Without a working model, chat replies and task classification remain
unavailable until you configure one under Settings.

For a headless source install with no browser:

```bash
./backend/.venv/bin/ybm onboard
```

The Windows equivalent is:

```powershell
& .\backend\.venv\Scripts\ybm.exe onboard
```

## Runtime interfaces

The Windows wrapper and installed Python CLI share the main runtime operations, but they are not
identical.

| Operation | Windows wrapper | Installed CLI on macOS/Linux |
|---|---|---|
| Start and open | `YBM.bat` or `.\scripts\ybm.ps1 run` | `./ybm.sh` or `./backend/.venv/bin/ybm start --open` |
| Diagnose | `.\scripts\ybm.ps1 doctor` | `./backend/.venv/bin/ybm doctor` |
| Status | `.\scripts\ybm.ps1 status` | `./backend/.venv/bin/ybm status` |
| Follow worker log | `.\scripts\ybm.ps1 logs worker -Follow` | `./backend/.venv/bin/ybm logs worker --follow` |
| Stop | `.\scripts\ybm.ps1 stop` | `./backend/.venv/bin/ybm stop` |
| Trace a task | `.\scripts\ybm.ps1 trace <task_id>` | `./backend/.venv/bin/ybm trace-task <task_id>` |
| Change config | `.\scripts\ybm.ps1 config set <path> <value>` | `./backend/.venv/bin/ybm config-set <path> <value>` |
| Build UI | `.\scripts\ybm.ps1 ui-build` | `./backend/.venv/bin/ybm ui-build` |

Run `.\scripts\ybm.ps1 help` or `./backend/.venv/bin/ybm --help` for the authoritative command list.
The PowerShell wrapper also includes development tests, live E2E helpers, scenarios, cleanup,
extension packaging, tray, autostart, and restart workflows.

## Manual Windows setup

For development or recovery after the bootstrap installer has provided `uv`:

```powershell
.\scripts\ybm.ps1 setup
.\scripts\ybm.ps1 doctor
.\scripts\ybm.ps1 start -Open
```

The developer setup installs the test, E2E, lint, voice, tray, and desktop extras. The normal
`YBM.bat` path installs runtime extras only.

To save a Telegram token during setup:

```powershell
.\scripts\ybm.ps1 setup --telegram-token <token>
```

To point YBM at a local [LocalDeploy](https://github.com/iodriller/LocalDeploy) checkout, add this to
`.env` before starting:

```text
YBM_LOCALDEPLOY_ROOT=C:\path\to\LocalDeploy
```

You can instead configure any catalog provider or custom OpenAI-compatible endpoint in the browser.

## Access and approvals

High-impact capabilities start disabled. Enable only the access needed for the current workflow
from the console's Access page, or change a specific value on Windows:

```powershell
.\scripts\ybm.ps1 config set <dotted.path> <value>
```

An enabled adapter does not override capability policy. Approval gates, risk ceilings, allowlists,
and allowed roots still apply. See [CAPABILITIES.md](CAPABILITIES.md) and
[THREAT_MODEL.md](THREAT_MODEL.md).

## Link WhatsApp

WhatsApp is disabled by default. It uses [Baileys](https://github.com/WhiskeySockets/Baileys), an
unofficial WhatsApp Web client. It does not need a Meta developer account or public webhook, but it
does carry a small account-flagging risk.

1. Set `channels.whatsapp.enabled: true` in `config/config.yaml` or use Settings.
2. Start or restart YBM.
3. Follow the bridge log: `.\scripts\ybm.ps1 logs whatsapp -Follow` on Windows, or
   `./backend/.venv/bin/ybm logs whatsapp --follow` on macOS/Linux.
4. Scan the QR code from WhatsApp under Settings, Linked Devices.
5. Add your number to `channels.whatsapp.allowed_numbers` as E.164 digits without `+`, such as
   `"15551234567"`, then restart.

The linked session persists under `.agent_control/whatsapp_auth/`. An empty allowed-number list
denies every message. Consider testing with a secondary number, and never commit a real number or
session data.

## Common Windows operations

```powershell
.\scripts\ybm.ps1 status
.\scripts\ybm.ps1 logs backend -Follow
.\scripts\ybm.ps1 logs worker -Follow
.\scripts\ybm.ps1 backup
.\scripts\ybm.ps1 check-updates
.\scripts\ybm.ps1 package-extension
.\scripts\ybm.ps1 stop
Invoke-RestMethod http://127.0.0.1:8765/health
```

Use service scripts under `scripts/services/` directly only when debugging one process in isolation.

## Local data

| Path | Contents |
|---|---|
| `.env` | Local tokens and provider keys |
| `config/config.yaml` | Local settings and access policy |
| `.agent_control/agent_control.db` | SQLite task, message, approval, and audit state |
| `.agent_control/workspaces/task_<id>` | Per-task generated files |
| `.agent_control/logs/<service>.jsonl` | Structured logs with secret redaction |
| `.agent_control/browser/screenshots` | Browser screenshots |
| `.agent_control/computer_use/screenshots` | Desktop screenshots |
| `.agent_control/coding_sessions` | Coding-agent session state |
| `.agent_control/adapters` | Generated adapter proposals, never auto-loaded |
| `.agent_control/whatsapp_auth` | WhatsApp linked-device credentials |

All of these paths are private and ignored by Git. Use
[DATABASE_INSPECTION.md](DATABASE_INSPECTION.md) to inspect or prune database state.
