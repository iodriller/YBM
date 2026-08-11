<div align="center">

# YBM Control

**Your own AI agent, running on your machine — reachable from the apps you already use.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![Docker ready](https://img.shields.io/badge/docker-ready-2496ED.svg)](docker-compose.yml)
[![Works with Claude, GPT, Gemini, Ollama](https://img.shields.io/badge/models-13%20providers-6E56CF.svg)](#bring-your-own-model)

![YBM Control — setup, channels, tools, and policy](docs/screenshots/demo.gif)

</div>

---

Text it from your phone. It works on your actual computer — your files, your browser, your
editor, your terminal — and reports back.

```
You:  organize my Downloads folder by file type, then tell me what you moved
YBM:  Sorted 41 files into 6 folders (Images, Documents, Archives, Installers,
      Code, Other). Nothing was deleted. Full list and every file path touched
      are in this task's trace.
```

That is not a chat window pretending to do things. Every request becomes a **task** with a full
trace you can open: which tool ran, what it returned, what was approved, what was refused.

## Why you might want this

- **It reaches your real machine.** Filesystem, terminal, Chrome, VS Code, desktop control, a
  scheduler, MCP tools. Not a sandbox in someone else's cloud.
- **It comes to you.** Telegram, WhatsApp, or the built-in web console. With optional local voice
  transcription enabled, you can send a voice note when typing is inconvenient.
- **Nothing dangerous happens by default.** High-impact capabilities start **off**. Anything risky needs
  a one-shot, expiring approval the *runtime* enforces — not the model, not a config flag, and not
  bypassable by an "allow everything" mode.
- **It's yours.** Runs local models for free with nothing leaving the machine, or your own API
  key. MIT licensed.

## Try it in five minutes

**Windows, no terminal:** download the folder, double-click **`YBM-Setup.cmd`**. It installs
whatever is missing — Python included — and opens the console.

**macOS / Linux:**

```bash
./scripts/install.sh
```

**Docker:**

```bash
docker compose up -d
# then open http://127.0.0.1:8765/admin
```

A browser wizard asks two questions — which model, and where you want to reach it — and both are
skippable. After that, **`YBM.bat`** (or `ybm run`) starts everything and opens the console.

## Bring your own model

| Free, on your hardware | With an API key |
|---|---|
| Ollama · LM Studio · LocalDeploy | Anthropic (Claude) · OpenAI · Google Gemini · OpenRouter · Groq · DeepSeek · Mistral · xAI · Together |

Paste a key and YBM checks it, lists the models that key can actually reach, and **makes one real
call before saving** — so a model that cannot answer never silently becomes your default. Local
models cost nothing and nothing you type leaves the machine.

Anthropic gets a native provider rather than an OpenAI-compatible shim, because current Claude
models reject `temperature` outright and a shim would fail every request while looking like an
auth problem.

## What it can actually do

Search and organize files inside allowed roots · run coding agents (Codex, Claude Code, Copilot
CLI) or a bounded Python interpreter · drive Chrome, including multi-source research · bridge to
VS Code · take and act on screenshots (Windows) · schedule recurring work · speak MCP as both
client and server · generate PDFs and documents · track cost per task · run tools in parallel and
delegate to sub-agents · remember facts you tell it, with provenance · search your own documents.

All of it policy-gated, on every channel. Full list in [docs/CAPABILITIES.md](docs/CAPABILITIES.md).

## How it fits together

A message arrives on any channel. The **Concierge** decides whether it is chat or work. Work goes
to the **Operator**, a tool-calling loop where every call passes the policy engine first — allowed,
needs your approval, or refused with a reason. The **Auditor** checks the result against what you
asked before it is reported as done.

```
Telegram ─┐
WhatsApp ─┼─▶ Concierge ─▶ Operator ⇄ Policy ⇄ Tools
Web chat ─┘       │            │
                  └── reply    └──▶ Auditor ─▶ result + full trace
```

Diagrams and the detail: [docs/ROLES.md](docs/ROLES.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Is this safe to run?

It is designed to be, and the design is written down rather than asserted.

Capabilities are off until you turn them on. Filesystem access is confined to roots you choose.
Terminal, browser, desktop control, dependency installation, and git push are each separately
gated. Approvals expire and are single-use. Secrets are redacted from logs and task output.
Everything is auditable after the fact.

Read [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) before pointing it at anything you care about,
and [SECURITY.md](SECURITY.md) to report a problem.

## Everyday commands

```bash
ybm doctor            # is this machine set up correctly?
ybm start             # start everything
ybm status            # what is running
ybm logs worker -f    # follow a service
ybm stop              # stop everything
```

`ybm autostart enable` adds a tray icon and starts YBM at login. Windows also has the fuller
`scripts\ybm.ps1` interface — `.\scripts\ybm.ps1 help` lists it.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and
[docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md) for manual setup.

```bash
cd backend && uv run --frozen pytest
```

`backend/tests/scenario/` replays recorded LLM responses, so the suite runs with no network, no
GPU, and no API spend.

## Docs

| | |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | How the system works today |
| [Roles](docs/ROLES.md) | Concierge, Operator, Auditor, with diagrams |
| [Capabilities](docs/CAPABILITIES.md) | Everything it can be allowed to do |
| [Threat model](docs/THREAT_MODEL.md) | What it protects against, and what it does not |
| [Local setup](docs/LOCAL_SETUP.md) | Manual setup and every runtime option |
| [Gaps](docs/GAPS.md) | Known bugs and missing pieces, kept honest |
| [Public release](docs/PUBLIC_RELEASE.md) | External checks to perform when visibility changes |
| [History](docs/HISTORY.md) | Why things are the way they are |

## Honest limitations

- WhatsApp is plain text only — no buttons, voice, or file delivery yet, unlike Telegram.
- Desktop control is Windows-only.
- Voice transcription is off by default and needs the `voice` extra installed.
- More in [docs/GAPS.md](docs/GAPS.md), which is kept current rather than flattering.

---

MIT licensed. Built to run on your own hardware, on your own terms.
