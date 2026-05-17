# Getting Started: Admin, Telegram, And LLM Routing

This project uses two local config surfaces:

- `config/config.yaml` for non-secret runtime config. The admin UI can create and update this file.
- `.env` or process environment variables for secrets such as Telegram bot tokens and LLM API keys.

`config/config.yaml` is intentionally ignored by git.

## 1. Start The Backend

From the repo root:

```powershell
.\scripts\init_db.ps1
.\scripts\run_backend.ps1
```

Open:

```text
http://127.0.0.1:8765/admin
```

If you set `AGENT_ADMIN_TOKEN`, open:

```text
http://127.0.0.1:8765/admin?token=<token>
```

## 2. Configure The Orchestrator LLM

The orchestrator LLM is the planning/reasoning model used by this system. It is separate from Codex, GitHub Copilot, or any coding assistant adapter.

The backend currently supports OpenAI-compatible chat-completions endpoints.

### Cloud API

Put the key in `.env`:

```powershell
OPENAI_API_KEY=your_key_here
```

In the admin UI, set:

```text
Profile: default
Default Profile: default
Provider: openai_compatible
Model: your_model_name
Base URL: https://api.openai.com/v1
API Key Env: OPENAI_API_KEY
```

Click `Save`, then `Test`. Telegram text task spawning uses this orchestrator LLM for classification, so this test must pass before text messages can create tasks.

### Local LLM On A 3080

Run any local OpenAI-compatible server. Common local endpoint shapes are:

```text
http://127.0.0.1:1234/v1
http://127.0.0.1:11434/v1
http://127.0.0.1:8000/v1
```

In the admin UI, set:

```text
Profile: local
Default Profile: local
Provider: openai_compatible
Model: the exact model name exposed by your local server
Base URL: your local /v1 endpoint
API Key Env: leave blank unless your local server requires one
```

Click `Save`, restart long-running backend/worker processes if needed, then click `Test`.

Important local requirement: the current planner asks the provider for structured JSON schema output. If your local server does not support compatible structured responses, LLM health may work while planning still fails. In that case, use a server/model with JSON-schema support or add a provider adapter that downgrades to JSON-only prompting.

## 3. Configure Telegram

1. Install Telegram on your phone.
2. Open `BotFather`.
3. Run `/newbot`.
4. Copy the bot token.
5. Add the token to `.env`:

```powershell
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

6. Send `/start` to your bot from Telegram.
7. Find your user ID and chat ID:

```powershell
$env:TELEGRAM_BOT_TOKEN="your_bot_token_here"
Invoke-RestMethod "https://api.telegram.org/bot$env:TELEGRAM_BOT_TOKEN/getUpdates" | ConvertTo-Json -Depth 10
```

Use:

```text
message.from.id
message.chat.id
```

8. In the admin UI Telegram section, set:

```text
Enabled: checked
Token Env: TELEGRAM_BOT_TOKEN
User IDs: your message.from.id
Chat IDs: your message.chat.id
Polling: checked
```

9. Click `Save`.
10. Restart Telegram polling after changing Telegram config:

```powershell
.\scripts\run_telegram_polling.ps1
```

## 4. What Is Monitorable Now

The admin UI currently shows:

- Effective redacted config.
- Telegram enabled state and whether the configured token env var is present.
- Default LLM profile state.
- Recent tasks.
- Recent audit events.
- Audit filters for raw Telegram messages, access decisions, classifications, spawned tasks, failed spawns, policy decisions, config changes, and tool events.
- VS Code bridge heartbeat/state.
- Pending VS Code terminal commands.
- Capability access modes.
- Database path and table counts.

The admin UI can currently control:

- Pause/resume/cancel task status.
- Telegram runtime config.
- Default orchestrator LLM profile config.
- VS Code bridge config.
- Capability access modes such as off, read-only, write access, and full access.
- VS Code terminal command queue only when explicitly enabled and approval-free.

## 5. Current Boundaries

- Telegram text input creates persisted tasks only after authorized messages are classified as tasks by the configured orchestrator LLM.
- Empty Telegram allowlists deny all messages and show an admin warning.
- Telegram command responses work for `/status`, `/tasks`, `/task <id>`, `/logs <id>`, and `/screenshot`.
- The admin LLM `Test` button verifies the configured provider connection.
- Automatic background planning/execution is not yet packaged as a production service.
- Full autonomous coding workflows still require the worker, planner, tool executor, VS Code bridge, and terminal assistant to be started/configured together.
