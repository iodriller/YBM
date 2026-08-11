<div align="center">

<img src="docs/brand/ybm-mark.svg" alt="YBM logo" width="112" />

# YBM

**A local AI agent you can reach from the web, Telegram, and WhatsApp.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](backend/pyproject.toml)
[![Docker ready](https://img.shields.io/badge/docker-ready-2496ED.svg)](docker-compose.yml)
[![13 model providers](https://img.shields.io/badge/models-13%20providers-6E56CF.svg)](#choose-a-model)

![Asking YBM to organize a Downloads folder: it plans the work, asks for approval before moving any file, then reports exactly what it moved](docs/screenshots/demo.gif)

</div>

YBM runs on your machine and turns a message into either a direct reply or a traceable task. Tasks
can use the tools you enable, including files, terminal commands, Chrome, VS Code, desktop control,
scheduled work, MCP servers, and coding agents. Every tool call passes through policy before it runs.

> YBM is alpha software. Start with a test directory, review the access settings, and keep the admin
> console bound to localhost unless you understand the authentication and network implications.

## Install

Nothing needs to be preinstalled: no Python, no Git, no `uv`. The install bootstraps `uv`, uses it to
provide Python 3.12, creates the project environment, initializes local config and tokens, and starts
YBM.

### Windows

1. Download `YBM-Setup.exe` from the [latest release](https://github.com/iodriller/YBM/releases/latest) and run it, **or** `winget install YBM`.
2. Complete the two-step browser wizard to choose a model and optionally connect Telegram.

The installer is per-user and needs no administrator rights. It installs into `%LOCALAPPDATA%\YBM`,
adds a Start Menu entry, and can be removed from Add or remove programs.

Installing from source instead? Download the repository as a ZIP, extract the whole folder, and
double-click [`YBM.bat`](YBM.bat). The same file handles the first run and every run after it, so
there is nothing else to remember. For a visible, verifiable install:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Verify
```

Node.js 22.22 or newer is only needed to build the admin console **from source**. Release builds and
the Docker image ship it prebuilt, so an installed copy needs no Node.js. The optional WhatsApp
bridge needs Node.js on any install. Without it, the backend still runs, but a source checkout serves
build instructions at `/admin` instead of the console.

### macOS and Linux

From an extracted checkout:

```bash
bash ./scripts/install.sh --verify
```

The script requires Bash and `curl`. It uses Git when available and can download a source archive
when Git is absent. After installation, start YBM from the checkout with:

```bash
./backend/.venv/bin/ybm start --open
```

If the machine has no browser, configure the model and Telegram interactively with:

```bash
./backend/.venv/bin/ybm onboard
```

### Docker

Docker is the headless profile. It includes the admin console and WhatsApp runtime, but it cannot
control the host desktop, attach to the host VS Code session, or use display-dependent browser
automation.

```bash
cp .env.example .env
# Edit .env and set AGENT_ADMIN_TOKEN to a long random value.
docker compose up -d --build
# Open http://127.0.0.1:8765/admin and enter the token when prompted.
```

The compose file stores runtime state in the `ybm-state` volume, writes settings to
`config/config.yaml`, and exposes only `./workspace` to filesystem tools. Put durable cloud API keys
in the host `.env` file. To run an Ollama container too:

```bash
docker compose --profile ollama up -d --build
docker compose exec ollama ollama pull qwen3:8b
```

## First run

The browser wizard has two steps:

1. Choose a local model or configure a cloud provider. YBM verifies access, lists models when the
   provider supports it, and makes one small completion before saving the choice.
2. Use the built-in web chat immediately, or optionally connect Telegram. WhatsApp is configured
   later under Settings.

Both wizard steps can be skipped. A skipped model means chat and task classification will not work
until a model is configured under Settings. See [Local setup](docs/LOCAL_SETUP.md) for manual and
headless configuration.

## Choose a model

| Local, no API key | Cloud API key |
|---|---|
| Ollama, LM Studio, LocalDeploy | Anthropic, OpenAI, OpenRouter, Google Gemini, Groq, DeepSeek, Mistral, xAI, Together AI |

The provider picker also accepts a custom OpenAI-compatible endpoint. Anthropic uses its native
SDK; the remaining cloud providers and local runtimes use their OpenAI-compatible APIs.

Using a local model keeps model prompts and completions on the configured local endpoint. Other
enabled tools, such as web search or HTTP requests, can still contact external services.

## Channels

- **Web chat:** available in the admin console after a model is configured.
- **Telegram:** optional bot integration with user and chat allowlists. Text, commands, approvals,
  voice transcription, and artifact delivery are supported.
- **WhatsApp:** optional, text-only integration through the unofficial Baileys WhatsApp Web client.
  It requires Node.js, QR linking, and an explicit phone-number allowlist.

An empty Telegram or WhatsApp allowlist denies every incoming message.

## What it can do

- Search, read, and organize files inside configured roots.
- Run bounded terminal commands, Python code, and supported coding agents.
- Use Chrome, screenshots, and Windows desktop control when enabled.
- Schedule recurring work and continue long-running tasks.
- Connect to MCP servers and expose its own MCP entry point.
- Work with VS Code through the optional bridge extension.
- Store attributed memory and search an indexed local knowledge base.
- Produce task traces with tool results, approvals, LLM receipts, timing, and cost data.

Capabilities and adapters are separate controls. Enabling an adapter does not bypass its capability
policy. See [Capabilities](docs/CAPABILITIES.md) for the implemented tool catalog.

## Safety model

High-impact capabilities start disabled. Filesystem access is restricted to configured roots.
Terminal, browser, desktop control, dependency installation, and Git pushes are separately gated.
Risky operations require short-lived approvals enforced by the runtime. Secrets are redacted from
logs and task output, and each task retains an audit trail.

Read the [Threat model](docs/THREAT_MODEL.md) before granting access to important files or accounts.
See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## How it works

```text
Telegram  \
WhatsApp  +--> Concierge --> Operator <--> Policy <--> Tools
Web chat  /         |            |
                    +--> reply    +--> Auditor --> result and trace
```

The FastAPI backend owns orchestration, policy, persistence, and adapters. The React console talks
to it through `/admin/api/*`. Telegram and WhatsApp are channel adapters over the same task pipeline,
and the VS Code extension is an editor bridge rather than a second agent runtime. See
[Architecture](docs/ARCHITECTURE.md) and [Roles](docs/ROLES.md) for details.

## Everyday commands

On Windows, use the repository lifecycle wrapper:

```powershell
.\scripts\ybm.ps1 doctor
.\scripts\ybm.ps1 start -Open
.\scripts\ybm.ps1 status
.\scripts\ybm.ps1 logs worker -Follow
.\scripts\ybm.ps1 stop
```

On macOS and Linux, use the installed console script:

```bash
./backend/.venv/bin/ybm doctor
./backend/.venv/bin/ybm start --open
./backend/.venv/bin/ybm status
./backend/.venv/bin/ybm logs worker --follow
./backend/.venv/bin/ybm stop
```

Run `.\scripts\ybm.ps1 help` or `./backend/.venv/bin/ybm --help` for the command available on that
interface. Windows-only conveniences include the tray app, login autostart, test runner, scenario
tools, and extension packaging.

## Development

```powershell
.\scripts\ybm.ps1 setup
.\scripts\ybm.ps1 doctor
.\scripts\ybm.ps1 test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for focused verification commands. Deterministic backend tests
do not need a network connection, GPU, Telegram account, or paid model call. Live E2E and scenario
recording have separate prerequisites and can make external calls.

## Documentation

| Guide | Contents |
|---|---|
| [Local setup](docs/LOCAL_SETUP.md) | Installers, manual setup, runtime commands, and local data |
| [Architecture](docs/ARCHITECTURE.md) | Current components and message flow |
| [Roles](docs/ROLES.md) | Concierge, Operator, and Auditor responsibilities |
| [Capabilities](docs/CAPABILITIES.md) | Implemented tools and their policy gates |
| [Threat model](docs/THREAT_MODEL.md) | Trust boundaries, protections, and residual risk |
| [Database inspection](docs/DATABASE_INSPECTION.md) | Inspect, prune, reset, and trace local state |
| [Known gaps](docs/GAPS.md) | Current limitations and unimplemented behavior |
| [History](docs/HISTORY.md) | Design rationale and completed phases |

## Current limitations

- The project is alpha and is tested most heavily on Windows.
- Desktop observation and control are Windows-only.
- WhatsApp is text-only and uses an unofficial client, which carries account risk.
- Voice transcription is disabled by default and needs the `voice` dependency extra.
- Docker cannot access host desktop or editor sessions, and only mounted paths are visible.

MIT licensed.
